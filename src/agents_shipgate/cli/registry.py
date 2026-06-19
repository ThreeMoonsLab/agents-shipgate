"""``agents-shipgate registry`` — a minimal local capability-release ledger.

v0 substrate for the cross-repo attestation registry (ROADMAP §4): a
local, append-only JSONL file of attestation rows that answers "which
capability was released, under which verdict, acknowledged by whom,
across which repos?" without any service. Rows are normalized
projections of ``agents-shipgate attest`` output; ingest is idempotent
(content-addressed by the attestation's own hashes), queries are
deterministic. No network, nothing executed.

This is intentionally tiny: a directory of repos can ingest into one
shared file (vendored, or synced like a lockfile) and `registry query`
becomes the org's capability-change audit trail. A hosted aggregation
plane, if it ever exists, consumes the same rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.schemas.registry import (
    REGISTRY_SCHEMA_VERSION,
    RegistryBypassReportV1,
    RegistryQueryResultV1,
    RegistryRowV1,
    RegistrySkippedRowV1,
)

DEFAULT_REGISTRY_PATH = Path(".agents-shipgate/registry.jsonl")

registry_app = typer.Typer(
    name="registry",
    help="Local capability-release ledger built from attestations.",
    no_args_is_help=True,
)


@dataclass(frozen=True)
class LoadedRegistry:
    rows: list[RegistryRowV1]
    skipped: list[RegistrySkippedRowV1]


def _row_from_attestation(
    attestation: dict[str, Any], *, repo: str | None
) -> RegistryRowV1:
    verdict = attestation.get("verdict") or {}
    capability = attestation.get("capability") or {}
    human_ack = attestation.get("human_ack") or {}
    org = attestation.get("org") or {}
    human_ack_required = human_ack.get("required")
    human_ack_satisfied = human_ack.get("satisfied")
    row = {
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "repo": repo or org.get("repo") or "",
        "org_id": org.get("org_id"),
        "service": org.get("service"),
        "tier": org.get("tier"),
        "pr_number": org.get("pr_number"),
        "workflow_run_id": org.get("workflow_run_id"),
        "actor": org.get("actor"),
        "merge_sha": org.get("merge_sha"),
        "attestation_schema_version": attestation.get("attestation_schema_version"),
        "cli_version": attestation.get("cli_version"),
        "base_ref": attestation.get("base_ref"),
        "head_ref": attestation.get("head_ref"),
        "base_tree_sha": attestation.get("base_tree_sha"),
        "head_tree_sha": attestation.get("head_tree_sha"),
        "merge_verdict": verdict.get("merge_verdict"),
        "decision": verdict.get("decision"),
        "can_merge_without_human": verdict.get("can_merge_without_human"),
        "capability_added": capability.get("added"),
        "capability_modified": capability.get("modified"),
        "capability_removed": capability.get("removed"),
        "capability_change_ids": sorted(capability.get("change_ids") or []),
        "trust_root_touched": capability.get("trust_root_touched"),
        "policy_weakened": capability.get("policy_weakened"),
        "human_ack_required": (
            human_ack_required if isinstance(human_ack_required, bool) else None
        ),
        "human_ack_satisfied": (
            human_ack_satisfied if isinstance(human_ack_satisfied, bool) else None
        ),
        "human_ack_outstanding": [
            str(item) for item in human_ack.get("outstanding") or []
        ],
        "human_ack": human_ack,
        "policy_snapshot_sha256": attestation.get("policy_snapshot_sha256"),
        "artifact_sha256": attestation.get("artifact_sha256"),
    }
    row["row_id"] = _row_id(row)
    return RegistryRowV1.model_validate(row)


def _row_id(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "row_id"}
    return "att_" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _load_registry(registry_path: Path) -> LoadedRegistry:
    if not registry_path.is_file():
        return LoadedRegistry(rows=[], skipped=[])
    rows: list[RegistryRowV1] = []
    skipped: list[RegistrySkippedRowV1] = []
    for line_number, line in enumerate(
        registry_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            skipped.append(
                RegistrySkippedRowV1(
                    line=line_number,
                    reason=f"invalid_json: {exc.msg}",
                )
            )
            continue
        if not isinstance(loaded, dict):
            skipped.append(
                RegistrySkippedRowV1(
                    line=line_number,
                    reason="row must be a JSON object",
                )
            )
            continue
        try:
            rows.append(_coerce_row(loaded))
        except ValidationError as exc:
            skipped.append(
                RegistrySkippedRowV1(
                    line=line_number,
                    reason=f"schema_validation_error: {exc.errors()[0]['msg']}",
                )
            )
    return LoadedRegistry(rows=rows, skipped=skipped)


def _load_rows(registry_path: Path) -> list[RegistryRowV1]:
    """Back-compat helper for internal callers that only need valid rows."""
    return _load_registry(registry_path).rows


def _coerce_row(value: dict[str, Any]) -> RegistryRowV1:
    if "row_id" not in value:
        value = {**value, "row_id": _row_id(value)}
    human_ack = value.get("human_ack") or {}
    if "human_ack_required" not in value and isinstance(human_ack, dict):
        required = human_ack.get("required")
        value["human_ack_required"] = required if isinstance(required, bool) else None
    if "human_ack_satisfied" not in value and isinstance(human_ack, dict):
        satisfied = human_ack.get("satisfied")
        value["human_ack_satisfied"] = satisfied if isinstance(satisfied, bool) else None
    if "human_ack_outstanding" not in value and isinstance(human_ack, dict):
        value["human_ack_outstanding"] = [
            str(item) for item in human_ack.get("outstanding") or []
        ]
    return RegistryRowV1.model_validate(value)


@registry_app.command("ingest")
def registry_ingest(
    attestation: Path = typer.Option(
        ...,
        "--attestation",
        help="Path to an attestation JSON produced by `agents-shipgate attest`.",
    ),
    registry: Path = typer.Option(
        DEFAULT_REGISTRY_PATH,
        "--registry",
        help="JSONL ledger to append to (created if missing).",
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        help="Repository label for cross-repo ledgers (e.g. org/service-a).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Append one attestation to the ledger. Idempotent by content."""
    try:
        data = json.loads(attestation.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"Could not read attestation {attestation}: {exc}", err=True)
        raise typer.Exit(3) from exc
    if not isinstance(data, dict) or "attestation_schema_version" not in data:
        typer.echo(
            f"{attestation} is not an Agents Shipgate attestation "
            "(missing attestation_schema_version).",
            err=True,
        )
        raise typer.Exit(3)
    row = _row_from_attestation(data, repo=repo)
    existing = _load_rows(registry)
    status = "exists"
    if all(item.row_id != row.row_id for item in existing):
        registry.parent.mkdir(parents=True, exist_ok=True)
        with registry.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n")
        status = "ingested"
    if json_output:
        typer.echo(json.dumps({"status": status, "row_id": row.row_id}))
    else:
        typer.echo(f"{status}: {row.row_id} → {registry}")


@registry_app.command("query")
def registry_query(
    registry: Path = typer.Option(DEFAULT_REGISTRY_PATH, "--registry"),
    repo: str | None = typer.Option(None, "--repo", help="Filter by repo label."),
    verdict: str | None = typer.Option(
        None, "--verdict", help="Filter by merge_verdict."
    ),
    capability_id: str | None = typer.Option(
        None, "--capability-id", help="Filter rows whose change_ids contain this id."
    ),
    trust_root_touched: bool = typer.Option(
        False,
        "--trust-root-touched",
        help="Only rows where a trust root was touched.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Query the ledger: which capability shipped, under which verdict."""
    loaded = _load_registry(registry)
    selected = []
    for row in loaded.rows:
        if repo and row.repo != repo:
            continue
        if verdict and row.merge_verdict != verdict:
            continue
        if capability_id and capability_id not in row.capability_change_ids:
            continue
        if trust_root_touched and row.trust_root_touched is not True:
            continue
        selected.append(row)
    selected.sort(key=lambda row: row.row_id)
    if json_output:
        payload = RegistryQueryResultV1(
            registry=str(registry),
            count=len(selected),
            skipped_count=len(loaded.skipped),
            skipped_rows=loaded.skipped,
            rows=selected,
        )
        typer.echo(payload.model_dump_json(indent=2))
        return
    typer.echo(f"{len(selected)} attestation row(s) in {registry}")
    if loaded.skipped:
        typer.echo(
            f"Skipped {len(loaded.skipped)} malformed registry row(s).",
            err=True,
        )
    for row in selected:
        flags = []
        if row.trust_root_touched:
            flags.append("trust-root")
        if row.policy_weakened:
            flags.append("policy-weakened")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        typer.echo(
            f"- {row.row_id} repo={row.repo or '—'} "
            f"verdict={row.merge_verdict} "
            f"+{row.capability_added or 0}/~{row.capability_modified or 0}"
            f"/-{row.capability_removed or 0}{suffix}"
        )


@registry_app.command("report")
def registry_report(
    registry: Path = typer.Option(DEFAULT_REGISTRY_PATH, "--registry"),
    bypass: bool = typer.Option(
        False,
        "--bypass",
        help=(
            "Report merges whose merge_verdict was not mergeable and whose "
            "attestation does not carry satisfied human acknowledgement."
        ),
    ),
    json_output: bool = typer.Option(False, "--json"),
    fail_on_bypass: bool = typer.Option(
        False,
        "--fail-on-bypass",
        help="Exit 20 when --bypass finds at least one row.",
    ),
) -> None:
    """Emit derived registry reports. Currently supports --bypass."""
    if not bypass:
        typer.echo("Nothing to report: pass --bypass.", err=True)
        raise typer.Exit(2)
    loaded = _load_registry(registry)
    rows = [
        row
        for row in loaded.rows
        if row.merge_verdict != "mergeable" and row.human_ack_satisfied is not True
    ]
    rows.sort(key=lambda row: row.row_id)
    payload = RegistryBypassReportV1(
        registry=str(registry),
        bypass_count=len(rows),
        skipped_count=len(loaded.skipped),
        skipped_rows=loaded.skipped,
        rows=rows,
    )
    if json_output:
        typer.echo(payload.model_dump_json(indent=2))
        if fail_on_bypass and payload.bypass_count > 0:
            raise typer.Exit(20)
        return
    typer.echo(f"{payload.bypass_count} possible bypass row(s) in {registry}")
    if loaded.skipped:
        typer.echo(
            f"Skipped {len(loaded.skipped)} malformed registry row(s).",
            err=True,
        )
    for row in payload.rows:
        typer.echo(
            f"- {row.row_id} repo={row.repo or '—'} verdict={row.merge_verdict} "
            f"human_ack_satisfied={row.human_ack_satisfied}"
        )
    if fail_on_bypass and payload.bypass_count > 0:
        raise typer.Exit(20)
