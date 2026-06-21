"""``agents-shipgate attest`` — a durable, local release attestation.

The Attestation Contract (Pattern 9): an agent-capability release should leave a
record, not a memory — *which* capability shipped, under *which* verdict,
acknowledged by *whom*. This command derives a deterministic, local, JSON-first
attestation from ``verifier.json`` (enriched from the sibling ``report.json``
when present).

It introduces no new gate: every field is copied or hashed from the verify run.
It is content-addressed by git SHAs and artifact hashes and carries **no
wall-clock timestamp**, so re-deriving from the same inputs is byte-identical —
consistent with the project's determinism discipline.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import typer

from agents_shipgate import __version__
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.attestation import (
    ATTESTATION_SCHEMA_VERSION,
    ReleaseAttestationV1,
)


def _attest_command(
    source: Path = typer.Option(
        Path("agents-shipgate-reports/verifier.json"),
        "--from",
        help="Path to verifier.json.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Write the attestation to this path.",
    ),
    redact: bool = typer.Option(
        True,
        "--redact/--no-redact",
        help=(
            "Reduce local artifact paths to filenames. Does not remove "
            "explicit org/CI identity fields."
        ),
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Optional shipgate.yaml to copy organization metadata from.",
    ),
    org_id: str | None = typer.Option(None, "--org-id"),
    repo: str | None = typer.Option(None, "--repo"),
    service: str | None = typer.Option(None, "--service"),
    tier: str | None = typer.Option(None, "--tier"),
    pr_number: str | None = typer.Option(None, "--pr-number"),
    workflow_run_id: str | None = typer.Option(None, "--workflow-run-id"),
    actor: str | None = typer.Option(None, "--actor"),
    merge_sha: str | None = typer.Option(None, "--merge-sha"),
    verify_run: Path | None = typer.Option(
        None,
        "--verify-run",
        help=(
            "Optional verify-run.json. Defaults to the sibling verify-run.json "
            "or the verifier artifact reference when present."
        ),
    ),
    event_time: str | None = typer.Option(
        None,
        "--event-time",
        help="Optional declared event time copied into the attestation.",
    ),
    source_url: str | None = typer.Option(
        None,
        "--source-url",
        help="Optional declared PR/workflow URL copied into the attestation.",
    ),
    branch: str | None = typer.Option(
        None,
        "--branch",
        help="Optional declared branch name copied into the attestation.",
    ),
    base_sha: str | None = typer.Option(
        None,
        "--base-sha",
        help="Optional declared base commit SHA copied into the attestation.",
    ),
    head_sha: str | None = typer.Option(
        None,
        "--head-sha",
        help="Optional declared head commit SHA copied into the attestation.",
    ),
    ci_context: str | None = typer.Option(
        None,
        "--ci-context",
        help="Optional CI context provider. Supported: github-actions.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the attestation JSON to stdout.",
    ),
) -> None:
    """Derive a local release attestation from verifier.json.

    Deterministic and local-only: no network, no wall-clock timestamp. Records
    the verdict, the capability delta, the declared human-acknowledgement state,
    a policy-snapshot hash, and content hashes of the verify artifacts. It does
    not gate — ``report.json.release_decision.decision`` remains the only gate.
    """
    try:
        verifier = _load_json_object(source, "verifier.json")
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    report = _load_sibling_report(source, verifier)
    verify_run_path = _resolve_verify_run_path(source, verifier, explicit=verify_run)
    verify_run_payload = _load_optional_json_object(verify_run_path)
    verify_run_sha256 = (
        hashlib.sha256(verify_run_path.read_bytes()).hexdigest()
        if verify_run_path is not None and verify_run_path.is_file()
        else None
    )
    org_context, run_context = _context_from_inputs(
        config=config,
        explicit={
            "org_id": org_id,
            "repo": repo,
            "service": service,
            "tier": tier,
            "pr_number": pr_number,
            "workflow_run_id": workflow_run_id,
            "actor": actor,
            "merge_sha": merge_sha,
        },
        run_explicit={
            "event_time": event_time,
            "source_url": source_url,
            "branch": branch,
            "base_sha": base_sha,
            "head_sha": head_sha,
        },
        ci_context=ci_context,
    )
    payload = build_attestation_payload(
        verifier,
        source=source,
        redacted=redact,
        report=report,
        verify_run=verify_run_payload,
        verify_run_sha256=verify_run_sha256,
        org_context=org_context,
        run_context=run_context,
    )
    rendered_payload = ReleaseAttestationV1.model_validate(payload).model_dump(
        mode="json"
    )
    rendered = json.dumps(rendered_payload, indent=2, sort_keys=True) + "\n"

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    if json_output or out is None:
        typer.echo(rendered.rstrip())
    else:
        typer.echo(f"Wrote attestation to {out}")


def build_attestation_payload(
    verifier: dict[str, Any],
    *,
    source: Path,
    redacted: bool,
    report: dict[str, Any] | None = None,
    verify_run: dict[str, Any] | None = None,
    verify_run_sha256: str | None = None,
    org_context: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a verify run onto a deterministic attestation dict.

    ``report`` (the sibling ``report.json``) is optional: when present it adds
    the declared ``human_ack`` state and a policy-snapshot hash; when absent the
    attestation degrades gracefully (``human_ack.satisfied`` is ``null`` and
    ``policy_snapshot_sha256`` is ``null``).
    """
    artifacts = _obj(verifier.get("artifacts"))
    release_decision = _obj(verifier.get("release_decision"))
    capability_review = _obj(verifier.get("capability_review"))
    human_review = _obj(verifier.get("human_review"))
    report = report or {}
    verify_run = verify_run or {}
    human_ack = _obj(report.get("human_ack"))
    effective_policy = report.get("effective_policy")
    org_context = _clean_org_context(org_context or {})
    run_context = _clean_run_context(run_context or {})

    return {
        "attestation_schema_version": ATTESTATION_SCHEMA_VERSION,
        "cli_version": __version__,
        "org": org_context,
        "source_verifier": source.name if redacted else str(source),
        "redacted": redacted,
        "run_id": _clean_str(verify_run.get("run_id")),
        "verify_run_sha256": _clean_str(verify_run_sha256),
        "event_time": run_context.get("event_time"),
        "source_url": run_context.get("source_url"),
        "branch": run_context.get("branch"),
        "base_sha": run_context.get("base_sha"),
        "head_sha": run_context.get("head_sha"),
        "base_ref": verifier.get("base_ref"),
        "head_ref": verifier.get("head_ref"),
        "base_tree_sha": verifier.get("base_tree_sha"),
        "head_tree_sha": verifier.get("head_tree_sha"),
        "mode": verifier.get("mode"),
        "verdict": {
            "merge_verdict": verifier.get("merge_verdict"),
            "decision": verifier.get("decision") or release_decision.get("decision"),
            "applicability": verifier.get("applicability"),
            "can_merge_without_human": bool(verifier.get("can_merge_without_human")),
        },
        "capability": {
            "added": int(capability_review.get("added", 0) or 0),
            "modified": int(capability_review.get("modified", 0) or 0),
            "removed": int(capability_review.get("removed", 0) or 0),
            "trust_root_touched": bool(capability_review.get("trust_root_touched")),
            "policy_weakened": bool(capability_review.get("policy_weakened")),
            "change_ids": _change_ids(capability_review.get("top_changes")),
        },
        "capability_lock": _capability_lock_binding(
            artifacts,
            base_dir=source.parent,
            redacted=redacted,
        ),
        "capability_diff": _capability_diff_binding(
            artifacts,
            base_dir=source.parent,
            redacted=redacted,
        ),
        "human_ack": {
            "required": bool(
                human_ack.get("required", human_review.get("required", False))
            ),
            # ``None`` when no report.json was available to confirm it.
            "satisfied": human_ack.get("satisfied"),
            "outstanding": _str_list(human_ack.get("outstanding")),
            "acks": _human_ack_entries(human_ack.get("acks")),
        },
        "policy_snapshot_sha256": (
            _canonical_sha256(effective_policy) if effective_policy is not None else None
        ),
        "policy_packs": _verify_run_policy_packs(verify_run),
        "artifact_sha256": _artifact_hashes(artifacts, base_dir=source.parent),
    }


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputParseError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputParseError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise InputParseError(f"{label} must contain an object: {path}")
    return data


def _load_sibling_report(
    source: Path, verifier: dict[str, Any]
) -> dict[str, Any] | None:
    """Best-effort read of the report.json beside verifier.json (or named in its
    artifacts map). Absence or a parse error is fine — enrichment is optional."""
    artifacts = _obj(verifier.get("artifacts"))
    name = Path(str(artifacts.get("report_json", "report.json"))).name or "report.json"
    candidate = source.parent / name
    if not candidate.is_file():
        return None
    try:
        return _load_json_object(candidate, "report.json")
    except InputParseError:
        return None


def _resolve_verify_run_path(
    source: Path,
    verifier: dict[str, Any],
    *,
    explicit: Path | None,
) -> Path | None:
    if explicit is not None:
        return explicit
    artifacts = _obj(verifier.get("artifacts"))
    name = Path(str(artifacts.get("verify_run_json", "verify-run.json"))).name
    candidate = source.parent / (name or "verify-run.json")
    return candidate if candidate.is_file() else None


def _load_optional_json_object(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        return _load_json_object(path, path.name)
    except InputParseError:
        return None


def _context_from_inputs(
    *,
    config: Path | None,
    explicit: dict[str, str | None],
    run_explicit: dict[str, str | None],
    ci_context: str | None,
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    context: dict[str, str | None] = {}
    run_context: dict[str, str | None] = {}
    if config is not None and config.is_file():
        try:
            manifest = load_manifest(config)
        except Exception:
            manifest = None
        if manifest is not None and manifest.organization is not None:
            context.update(
                {
                    "org_id": manifest.organization.id,
                    "repo": manifest.organization.repo,
                    "service": manifest.organization.service,
                    "tier": manifest.organization.tier,
                }
            )
    if ci_context:
        if ci_context != "github-actions":
            raise typer.BadParameter(
                "unsupported ci context; supported value: github-actions",
                param_hint="--ci-context",
            )
        repo = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")
        actor = os.environ.get("GITHUB_ACTOR")
        event_name = os.environ.get("GITHUB_EVENT_NAME")
        ref = os.environ.get("GITHUB_SHA")
        event = _github_event_payload()
        pr = _github_event_pr(event)
        context.update(
            {
                "repo": repo,
                "workflow_run_id": run_id,
                "actor": actor,
                "merge_sha": ref,
                "pr_number": pr if event_name == "pull_request" else explicit.get("pr_number"),
            }
        )
        run_context.update(_github_run_context(event))
    context.update({key: value for key, value in explicit.items() if value is not None})
    run_context.update(
        {key: value for key, value in run_explicit.items() if value is not None}
    )
    return _clean_org_context(context), _clean_run_context(run_context)


def _github_event_payload() -> dict[str, Any]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _github_event_pr(payload: dict[str, Any]) -> str | None:
    pr = payload.get("pull_request")
    number = pr.get("number") if isinstance(pr, dict) else None
    return str(number) if number is not None else None


def _github_run_context(payload: dict[str, Any]) -> dict[str, str | None]:
    pr = payload.get("pull_request")
    if isinstance(pr, dict):
        base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        return {
            "event_time": _clean_str(pr.get("updated_at") or pr.get("created_at")),
            "source_url": _clean_str(pr.get("html_url")),
            "branch": _clean_str(head.get("ref")),
            "base_sha": _clean_str(base.get("sha")),
            "head_sha": _clean_str(head.get("sha")),
        }
    return {
        "event_time": _clean_str(payload.get("head_commit", {}).get("timestamp"))
        if isinstance(payload.get("head_commit"), dict)
        else None,
        "source_url": _clean_str(payload.get("compare")),
        "branch": _clean_str(os.environ.get("GITHUB_REF_NAME")),
        "base_sha": _clean_str(payload.get("before")),
        "head_sha": _clean_str(payload.get("after") or os.environ.get("GITHUB_SHA")),
    }


def _clean_org_context(context: dict[str, Any]) -> dict[str, str | None]:
    keys = (
        "org_id",
        "repo",
        "service",
        "tier",
        "pr_number",
        "workflow_run_id",
        "actor",
        "merge_sha",
    )
    return {key: _clean_str(context.get(key)) for key in keys}


def _clean_run_context(context: dict[str, Any]) -> dict[str, str | None]:
    keys = ("event_time", "source_url", "branch", "base_sha", "head_sha")
    return {key: _clean_str(context.get(key)) for key in keys}


def _human_ack_entries(value: Any) -> list[dict[str, str | None]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str | None]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "owner": _clean_str(item.get("owner")),
                "reason": _clean_str(item.get("reason")),
                "affected_surface": _clean_str(item.get("affected_surface")),
                "expires": _clean_str(item.get("expires")),
                "source": _clean_str(item.get("source")),
            }
        )
    return sorted(
        out,
        key=lambda item: (
            item.get("affected_surface") or "",
            item.get("owner") or "",
            item.get("reason") or "",
        ),
    )


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _artifact_hashes(artifacts: Any, *, base_dir: Path) -> dict[str, str]:
    """sha256 of each verify artifact that sits beside verifier.json.

    Artifact paths inside verifier.json may be absolute or workspace-relative,
    so resolve by basename against the verifier.json directory — CWD-independent.
    """
    out: dict[str, str] = {}
    if not isinstance(artifacts, dict):
        return out
    for key, value in artifacts.items():
        candidate = base_dir / Path(str(value)).name
        if candidate.is_file():
            out[str(key)] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return dict(sorted(out.items()))


def _capability_lock_binding(
    artifacts: dict[str, Any],
    *,
    base_dir: Path,
    redacted: bool,
) -> dict[str, Any]:
    key = "capability_lock"
    candidate = _artifact_candidate(artifacts, key, base_dir=base_dir)
    if candidate is None:
        return {
            "path": None,
            "sha256": None,
            "capability_lock_schema_version": None,
            "semantic_capability_set_hash": None,
            "evidence_set_hash": None,
            "source_set_hash": None,
            "capability_count": None,
        }
    payload = _load_optional_json(candidate)
    hashes = _obj(payload.get("hashes")) if isinstance(payload, dict) else {}
    summary = _obj(payload.get("summary")) if isinstance(payload, dict) else {}
    return {
        "path": _artifact_display_path(artifacts.get(key), redacted=redacted),
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "capability_lock_schema_version": (
            payload.get("capability_lock_schema_version")
            if isinstance(payload, dict)
            else None
        ),
        "semantic_capability_set_hash": hashes.get("semantic_capability_set_hash"),
        "evidence_set_hash": hashes.get("evidence_set_hash"),
        "source_set_hash": hashes.get("source_set_hash"),
        "capability_count": summary.get("capability_count"),
    }


def _capability_diff_binding(
    artifacts: dict[str, Any],
    *,
    base_dir: Path,
    redacted: bool,
) -> dict[str, Any] | None:
    key = "capability_lock_diff_json"
    candidate = _artifact_candidate(artifacts, key, base_dir=base_dir)
    if candidate is None:
        return None
    payload = _load_optional_json(candidate)
    if not isinstance(payload, dict):
        return {
            "path": _artifact_display_path(artifacts.get(key), redacted=redacted),
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "capability_lock_diff_schema_version": None,
            "base_semantic_capability_set_hash": None,
            "head_semantic_capability_set_hash": None,
            "summary": None,
        }
    base = _obj(payload.get("base"))
    head = _obj(payload.get("head"))
    return {
        "path": _artifact_display_path(artifacts.get(key), redacted=redacted),
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "capability_lock_diff_schema_version": payload.get(
            "capability_lock_diff_schema_version"
        ),
        "base_semantic_capability_set_hash": base.get(
            "semantic_capability_set_hash"
        ),
        "head_semantic_capability_set_hash": head.get(
            "semantic_capability_set_hash"
        ),
        "summary": _capability_diff_summary(payload.get("summary")),
    }


def _capability_diff_summary(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {
        "added": int(value.get("added", 0) or 0),
        "removed": int(value.get("removed", 0) or 0),
        "reidentified": int(value.get("reidentified", 0) or 0),
        "changed": int(value.get("changed", 0) or 0),
        "evidence_changed": int(value.get("evidence_changed", 0) or 0),
        "unchanged": int(value.get("unchanged", 0) or 0),
    }


def _artifact_candidate(
    artifacts: dict[str, Any],
    key: str,
    *,
    base_dir: Path,
) -> Path | None:
    value = artifacts.get(key)
    if value is None:
        return None
    candidate = base_dir / Path(str(value)).name
    if candidate.is_file():
        return candidate
    return None


def _artifact_display_path(value: Any, *, redacted: bool) -> str | None:
    if value is None:
        return None
    text = str(value)
    return Path(text).name if redacted else text


def _load_optional_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verify_run_policy_packs(verify_run: dict[str, Any]) -> list[dict[str, str | int | None]]:
    inputs = _obj(verify_run.get("inputs"))
    rows: list[dict[str, str | int | None]] = []
    for item in inputs.get("policy_packs") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": _clean_str(item.get("id")),
                "name": _clean_str(item.get("name")),
                "version": _clean_str(item.get("version")),
                "path": _clean_str(item.get("path")),
                "sha256": _clean_str(item.get("sha256")),
                "rule_count": (
                    int(item["rule_count"]) if isinstance(item.get("rule_count"), int) else None
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row.get("path") or "",
            row.get("id") or "",
            row.get("sha256") or "",
        ),
    )


def _change_ids(top_changes: Any) -> list[str]:
    if not isinstance(top_changes, list):
        return []
    ids = {
        str(change.get("id"))
        for change in top_changes
        if isinstance(change, dict) and change.get("id")
    }
    return sorted(ids)


def _str_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = ["ATTESTATION_SCHEMA_VERSION", "build_attestation_payload"]
