from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import typer

from agents_shipgate import __version__
from agents_shipgate.cli.attest import build_attestation_payload
from agents_shipgate.cli.registry import _row_from_attestation
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.host_grants import (
    DEFAULT_BASELINE_FILE,
    host_audit_inventory,
)
from agents_shipgate.core.org_governance import (
    build_org_governance_status,
    build_org_policy_packs_status,
    host_grant_drift_payload,
    render_org_status_markdown,
)
from agents_shipgate.core.verification_identity import validate_receipt_artifacts
from agents_shipgate.schemas.attestation import ReleaseAttestationV1
from agents_shipgate.schemas.org_evidence_bundle import (
    OrgEvidenceBundleArtifactRef,
    OrgEvidenceBundleV1,
)
from agents_shipgate.schemas.verification_identity import VerificationReceipt

ORG_GOVERNANCE_EXIT_CODE = 20

org_app = typer.Typer(
    name="org",
    help="Organization governance status over local Shipgate artifacts.",
    no_args_is_help=True,
)


@org_app.command("status")
def org_status(
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Manifest path.",
    ),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root.",
    ),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Accepted-debt baseline path. Defaults to .agents-shipgate/baseline.json.",
    ),
    host_baseline: Path | None = typer.Option(
        None,
        "--host-baseline",
        help="Host-grants baseline path. Defaults to .agents-shipgate/host-grants.json.",
    ),
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="Reference date for expiry/age checks (YYYY-MM-DD; default today UTC).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Evaluate opt-in org governance gates without changing release verdicts."""

    try:
        as_of_date = date.fromisoformat(as_of) if as_of else datetime.now(UTC).date()
    except ValueError:
        typer.echo(f"--as-of must be an ISO date (YYYY-MM-DD), got {as_of!r}.", err=True)
        raise typer.Exit(2) from None

    root = workspace.resolve()
    config_path = config if config.is_absolute() else root / config
    try:
        manifest = load_manifest(config_path)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc

    baseline_path = None
    if baseline is not None:
        baseline_path = baseline if baseline.is_absolute() else root / baseline
    host_baseline_path = None
    if host_baseline is not None:
        host_baseline_path = host_baseline if host_baseline.is_absolute() else root / host_baseline

    payload = build_org_governance_status(
        manifest=manifest,
        config_path=config_path,
        workspace=root,
        as_of=as_of_date,
        baseline_path=baseline_path,
        host_baseline_path=host_baseline_path,
    )
    if json_output:
        typer.echo(payload.model_dump_json(indent=2, exclude_none=False))
    else:
        typer.echo(render_org_status_markdown(payload), nl=False)
    if payload.violations:
        raise typer.Exit(ORG_GOVERNANCE_EXIT_CODE)


@org_app.command("policy-packs")
def org_policy_packs(
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Manifest path.",
    ),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Report local organization policy-pack pin status."""

    root = workspace.resolve()
    config_path = config if config.is_absolute() else root / config
    try:
        manifest = load_manifest(config_path)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc
    payload = build_org_policy_packs_status(
        manifest=manifest,
        config_path=config_path,
        workspace=root,
    )
    if json_output:
        typer.echo(payload.model_dump_json(indent=2, exclude_none=False))
        return
    if payload.violations:
        typer.echo(f"Policy-pack violations: {payload.policy_pack_violation_count}")
        for item in payload.violations:
            typer.echo(f"- `{item['kind']}` in `{item['source']}`")
    else:
        typer.echo("No policy-pack pin violations.")
    typer.echo(f"Policy packs: {payload.policy_pack_count}")


@org_app.command("bundle")
def org_bundle(
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Manifest path.",
    ),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root.",
    ),
    source: Path = typer.Option(
        Path("agents-shipgate-reports/verifier.json"),
        "--from",
        help="Path to verifier.json.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Write the org evidence bundle to this path.",
    ),
    attestation: Path | None = typer.Option(
        None,
        "--attestation",
        help="Optional attestation.json to include. Defaults to sibling attestation.json if present.",
    ),
    registry: str | None = typer.Option(
        None,
        "--registry",
        help="Optional registry path label to include as metadata only; never mutated.",
    ),
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="Reference date for org status (YYYY-MM-DD; default today UTC).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON to stdout."),
) -> None:
    """Build a compact organization evidence bundle from local artifacts."""

    try:
        as_of_date = date.fromisoformat(as_of) if as_of else datetime.now(UTC).date()
    except ValueError:
        typer.echo(f"--as-of must be an ISO date (YYYY-MM-DD), got {as_of!r}.", err=True)
        raise typer.Exit(2) from None

    root = workspace.resolve()
    config_path = config if config.is_absolute() else root / config
    verifier_path = source if source.is_absolute() else root / source
    try:
        manifest = load_manifest(config_path)
        verifier = _load_json_object(verifier_path, "verifier.json")
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(3) from exc

    report_path = _artifact_path(verifier_path, verifier, "report_json", "report.json")
    verify_run_path = _artifact_path(
        verifier_path,
        verifier,
        "verify_run_json",
        "verify-run.json",
    )
    report = _load_optional_json_object(report_path)
    verify_run = _load_optional_json_object(verify_run_path)
    receipt_path = _artifact_path(
        verifier_path,
        verifier,
        "verification_receipt_json",
        "verification-receipt.json",
    )
    receipt = _load_optional_json_object(receipt_path)
    verified_receipt: VerificationReceipt | None = None
    if verifier.get("verifier_schema_version") in {"0.5", "0.6"}:
        try:
            if receipt is None:
                raise ValueError("current verifier evidence is missing verification-receipt.json")
            verified_receipt = VerificationReceipt.model_validate(receipt)
            validate_receipt_artifacts(verified_receipt, root=verifier_path.parent)
            receipt = verified_receipt.model_dump(mode="json")
        except (ValueError, OSError) as exc:
            typer.echo(f"Verification receipt error: {exc}", err=True)
            raise typer.Exit(3) from exc
    attestation_path = _resolve_attestation_path(verifier_path, attestation)
    attestation_payload = _load_optional_json_object(attestation_path)
    if attestation_payload is None:
        attestation_payload = build_attestation_payload(
            verifier,
            source=verifier_path,
            redacted=True,
            report=report,
            verify_run=verify_run,
            verify_run_sha256=_file_sha256(verify_run_path),
            verification_receipt=receipt,
            verification_receipt_sha256=_file_sha256(receipt_path),
            org_context=_org_context_from_manifest(manifest),
        )
    attestation_model = ReleaseAttestationV1.model_validate(
        _normalize_attestation_payload(attestation_payload)
    )
    if verified_receipt is not None:
        expected_receipt_identity = {
            "request_id": verified_receipt.request_id,
            "receipt_id": verified_receipt.receipt_id,
            "decision_id": verified_receipt.decision_id,
            "artifact_set_id": verified_receipt.artifact_set_id,
        }
        for field, expected in expected_receipt_identity.items():
            if getattr(attestation_model, field) != expected:
                typer.echo(
                    f"Attestation {field} does not match the terminal verification receipt.",
                    err=True,
                )
                raise typer.Exit(3)
    attestation_json = attestation_model.model_dump(mode="json")
    registry_row = _row_from_attestation(
        attestation_json,
        repo=(
            manifest.organization.repo
            if manifest.organization is not None and manifest.organization.repo
            else None
        ),
        source_attestation_sha256=_attestation_source_sha256(
            attestation_path,
            attestation_json,
        ),
    )
    org_status_payload = build_org_governance_status(
        manifest=manifest,
        config_path=config_path,
        workspace=root,
        as_of=as_of_date,
    )
    inventory = host_audit_inventory(root)
    drift = host_grant_drift_payload(
        workspace=root,
        baseline_path=root / DEFAULT_BASELINE_FILE,
    )
    artifacts = _bundle_artifacts(
        {
            "verifier": verifier_path,
            "report": report_path,
            "verify_run": verify_run_path,
            "attestation": attestation_path,
            "verification_receipt": receipt_path,
        }
    )
    if out is not None:
        artifacts["org_evidence_bundle"] = OrgEvidenceBundleArtifactRef(
            path=str(out),
            sha256=None,
        )
    bundle = OrgEvidenceBundleV1(
        cli_version=__version__,
        organization=(
            manifest.organization.model_dump(mode="json", exclude_none=True)
            if manifest.organization is not None
            else None
        ),
        workspace=str(root),
        config=_display_path(config_path, root),
        source_verifier=_display_path(verifier_path, root),
        source_report=(
            _display_path(report_path, root)
            if report_path is not None and report_path.is_file()
            else None
        ),
        source_verify_run=(
            _display_path(verify_run_path, root)
            if verify_run_path is not None and verify_run_path.is_file()
            else None
        ),
        source_attestation=(
            _display_path(attestation_path, root)
            if attestation_path is not None and attestation_path.is_file()
            else None
        ),
        source_verification_receipt=(
            _display_path(receipt_path, root) if receipt_path.is_file() else None
        ),
        request_id=attestation_json.get("request_id"),
        receipt_id=attestation_json.get("receipt_id"),
        decision_id=attestation_json.get("decision_id"),
        artifact_set_id=attestation_json.get("artifact_set_id"),
        attestation=attestation_json,
        registry_row=registry_row.model_dump(mode="json"),
        org_status=org_status_payload.model_dump(mode="json", exclude_none=False),
        policy_packs=[item.model_dump(mode="json") for item in org_status_payload.policy_packs],
        host_grants=_host_grant_summary(inventory, registry_path=registry),
        host_grant_drift=drift,
        privacy=_obj(report.get("privacy_audit")) if report is not None else {},
        artifacts=artifacts,
    )
    rendered = bundle.model_dump_json(indent=2, exclude_none=False) + "\n"
    if out is not None:
        out_path = out if out.is_absolute() else root / out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    if json_output or out is None:
        typer.echo(rendered.rstrip())
    else:
        typer.echo(f"Wrote org evidence bundle to {out}")


def _artifact_path(
    verifier_path: Path,
    verifier: dict[str, Any],
    key: str,
    default_name: str,
) -> Path:
    artifacts = _obj(verifier.get("artifacts"))
    name = Path(str(artifacts.get(key, default_name))).name or default_name
    return verifier_path.parent / name


def _resolve_attestation_path(verifier_path: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit if explicit.is_absolute() else verifier_path.parent / explicit
    return verifier_path.parent / "attestation.json"


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain an object: {path}")
    return payload


def _load_optional_json_object(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        return _load_json_object(path, path.name)
    except ValueError:
        return None


def _normalize_attestation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("attestation_schema_version")
    if version not in {"0.3", "0.4"}:
        return payload
    upgraded = {
        **payload,
        "attestation_schema_version": "0.5",
    }
    for field in (
        "run_id",
        "verify_run_sha256",
        "event_time",
        "source_url",
        "branch",
        "base_sha",
        "head_sha",
        "request_id",
        "receipt_id",
        "decision_id",
        "artifact_set_id",
        "verification_receipt_sha256",
    ):
        upgraded.setdefault(field, None)
    upgraded.setdefault("policy_packs", [])
    return upgraded


def _org_context_from_manifest(manifest) -> dict[str, str | None]:
    org = manifest.organization
    if org is None:
        return {}
    return {
        "org_id": org.id,
        "repo": org.repo,
        "service": org.service,
        "tier": org.tier,
    }


def _bundle_artifacts(paths: dict[str, Path | None]) -> dict[str, OrgEvidenceBundleArtifactRef]:
    artifacts: dict[str, OrgEvidenceBundleArtifactRef] = {}
    for key, path in paths.items():
        if path is None or not path.is_file():
            continue
        artifacts[key] = OrgEvidenceBundleArtifactRef(
            path=str(path),
            sha256=_file_sha256(path),
        )
    return artifacts


def _host_grant_summary(
    inventory: dict[str, Any],
    *,
    registry_path: str | None,
) -> dict[str, Any]:
    grants = [item for item in inventory.get("grants") or [] if isinstance(item, dict)]
    rules = [item for item in grants if item.get("kind") == "permission_rule"]
    workflows = [item for item in grants if item.get("kind") == "workflow"]
    issues = [item for item in inventory.get("issues") or [] if isinstance(item, dict)]
    return {
        "host_grants_inventory_schema_version": inventory.get(
            "host_grants_inventory_schema_version"
        ),
        "registry_path": registry_path,
        "mcp_server_count": sum(1 for item in grants if item.get("kind") == "mcp_server"),
        "permission_rule_count": len(rules),
        "wildcard_permission_rule_count": sum(
            1 for item in rules if isinstance(item, dict) and item.get("wildcard")
        ),
        "hook_count": sum(1 for item in grants if item.get("kind") == "hook"),
        "workflow_count": len(workflows),
        "workflow_with_write_scope_count": sum(
            1
            for item in workflows
            if isinstance(item, dict)
            and (item.get("write_scopes") or item.get("pull_request_target"))
        ),
        "parse_warning_count": sum(1 for item in issues if item.get("blocking")),
    }


def _file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _attestation_source_sha256(path: Path | None, payload: dict[str, Any]) -> str:
    file_hash = _file_sha256(path)
    if file_hash is not None:
        return file_hash
    return _canonical_sha256(payload)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "ORG_GOVERNANCE_EXIT_CODE",
    "org_app",
    "org_bundle",
    "org_policy_packs",
    "org_status",
]
