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
    RegistrySummaryV1,
    RegistryVerificationIssueV1,
    RegistryVerificationResultV1,
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
    attestation: dict[str, Any],
    *,
    repo: str | None,
    source_attestation_sha256: str | None = None,
    previous_row: RegistryRowV1 | None = None,
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
        "event_time": attestation.get("event_time"),
        "source_url": attestation.get("source_url"),
        "branch": attestation.get("branch"),
        "base_sha": attestation.get("base_sha"),
        "head_sha": attestation.get("head_sha"),
        "attestation_schema_version": attestation.get("attestation_schema_version"),
        "cli_version": attestation.get("cli_version"),
        "run_id": attestation.get("run_id"),
        "request_id": attestation.get("request_id"),
        "receipt_id": attestation.get("receipt_id"),
        "decision_id": attestation.get("decision_id"),
        "artifact_set_id": attestation.get("artifact_set_id"),
        "source_verification_receipt_sha256": attestation.get("verification_receipt_sha256"),
        "source_attestation_sha256": source_attestation_sha256,
        "source_verify_run_sha256": attestation.get("verify_run_sha256"),
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
        "human_ack_outstanding": [str(item) for item in human_ack.get("outstanding") or []],
        "human_ack": human_ack,
        "policy_snapshot_sha256": attestation.get("policy_snapshot_sha256"),
        "artifact_sha256": attestation.get("artifact_sha256"),
        "capability_lock": attestation.get("capability_lock") or {},
        "capability_diff": attestation.get("capability_diff"),
        "policy_packs": sorted(
            [item for item in attestation.get("policy_packs") or [] if isinstance(item, dict)],
            key=lambda item: (
                str(item.get("path") or ""),
                str(item.get("id") or ""),
                str(item.get("sha256") or ""),
            ),
        ),
        "previous_row_id": previous_row.row_id if previous_row is not None else None,
        "previous_row_sha256": (
            _row_content_sha256(previous_row) if previous_row is not None else None
        ),
    }
    row["row_id"] = _row_id(row)
    return RegistryRowV1.model_validate(row)


def _row_id(row: dict[str, Any] | RegistryRowV1) -> str:
    payload = _row_identity_payload(row)
    return (
        "att_"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
    )


def _row_identity_payload(row: dict[str, Any] | RegistryRowV1) -> dict[str, Any]:
    if isinstance(row, RegistryRowV1):
        payload = row.model_dump(mode="json")
    else:
        pending = {**row, "row_id": str(row.get("row_id") or "att_pending")}
        payload = RegistryRowV1.model_validate(pending).model_dump(mode="json")
    payload.pop("row_id", None)
    return payload


def _row_content_sha256(row: RegistryRowV1) -> str:
    return hashlib.sha256(
        json.dumps(
            row.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


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
    value = {**value}
    human_ack = value.get("human_ack") or {}
    if "human_ack_required" not in value and isinstance(human_ack, dict):
        required = human_ack.get("required")
        value["human_ack_required"] = required if isinstance(required, bool) else None
    if "human_ack_satisfied" not in value and isinstance(human_ack, dict):
        satisfied = human_ack.get("satisfied")
        value["human_ack_satisfied"] = satisfied if isinstance(satisfied, bool) else None
    if "human_ack_outstanding" not in value and isinstance(human_ack, dict):
        value["human_ack_outstanding"] = [str(item) for item in human_ack.get("outstanding") or []]
    if "row_id" not in value:
        value["row_id"] = _row_id(value)
    row = RegistryRowV1.model_validate(value)
    # Preserve malformed historical rows for query output; registry verify
    # reports any mismatch without hiding the row from readers.
    return row


def _last_row_for_repo(rows: list[RegistryRowV1], repo: str) -> RegistryRowV1 | None:
    for row in reversed(rows):
        if row.repo == repo:
            return row
    return None


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
    """Append one attestation to the ledger. Idempotent by exact attestation bytes."""
    try:
        raw = attestation.read_bytes()
        data = json.loads(raw)
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
    existing = _load_rows(registry)
    source_attestation_sha256 = hashlib.sha256(raw).hexdigest()
    prior = next(
        (item for item in existing if item.source_attestation_sha256 == source_attestation_sha256),
        None,
    )
    if prior is not None:
        status = "exists"
        row = prior
    else:
        row = _row_from_attestation(
            data,
            repo=repo,
            source_attestation_sha256=source_attestation_sha256,
            previous_row=_last_row_for_repo(
                existing, repo or (data.get("org") or {}).get("repo") or ""
            ),
        )
        status = "ingested"
        registry.parent.mkdir(parents=True, exist_ok=True)
        with registry.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n")
    if json_output:
        typer.echo(json.dumps({"status": status, "row_id": row.row_id}))
    else:
        typer.echo(f"{status}: {row.row_id} → {registry}")


@registry_app.command("query")
def registry_query(
    registry: Path = typer.Option(DEFAULT_REGISTRY_PATH, "--registry"),
    repo: str | None = typer.Option(None, "--repo", help="Filter by repo label."),
    org_id: str | None = typer.Option(None, "--org-id", help="Filter by organization id."),
    service: str | None = typer.Option(None, "--service", help="Filter by service."),
    tier: str | None = typer.Option(None, "--tier", help="Filter by tier."),
    actor: str | None = typer.Option(None, "--actor", help="Filter by CI/merge actor."),
    verdict: str | None = typer.Option(None, "--verdict", help="Filter by merge_verdict."),
    capability_id: str | None = typer.Option(
        None, "--capability-id", help="Filter rows whose change_ids contain this id."
    ),
    trust_root_touched: bool = typer.Option(
        False,
        "--trust-root-touched",
        help="Only rows where a trust root was touched.",
    ),
    policy_weakened: bool = typer.Option(
        False,
        "--policy-weakened",
        help="Only rows where policy was weakened.",
    ),
    human_ack_required: bool | None = typer.Option(
        None,
        "--human-ack-required/--human-ack-not-required",
        help="Filter by whether human acknowledgement was required.",
    ),
    human_ack_satisfied: bool | None = typer.Option(
        None,
        "--human-ack-satisfied/--human-ack-not-satisfied",
        help="Filter by whether human acknowledgement was satisfied.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Query the ledger: which capability shipped, under which verdict."""
    loaded = _load_registry(registry)
    selected = []
    for row in loaded.rows:
        if repo and row.repo != repo:
            continue
        if org_id and row.org_id != org_id:
            continue
        if service and row.service != service:
            continue
        if tier and row.tier != tier:
            continue
        if actor and row.actor != actor:
            continue
        if verdict and row.merge_verdict != verdict:
            continue
        if capability_id and capability_id not in row.capability_change_ids:
            continue
        if trust_root_touched and row.trust_root_touched is not True:
            continue
        if policy_weakened and row.policy_weakened is not True:
            continue
        if human_ack_required is not None and row.human_ack_required is not human_ack_required:
            continue
        if human_ack_satisfied is not None and row.human_ack_satisfied is not human_ack_satisfied:
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


@registry_app.command("summary")
def registry_summary(
    registry: Path = typer.Option(DEFAULT_REGISTRY_PATH, "--registry"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Summarize the ledger for cross-repo governance reporting."""
    loaded = _load_registry(registry)
    payload = _registry_summary_payload(registry, loaded)
    if json_output:
        typer.echo(payload.model_dump_json(indent=2))
        return
    typer.echo(f"{payload.count} attestation row(s) in {registry}")
    typer.echo(f"Bypass candidates: {payload.bypass_count}")
    typer.echo(f"Trust-root touched: {payload.trust_root_touched_count}")
    typer.echo(f"Policy weakened: {payload.policy_weakened_count}")
    typer.echo(f"Human acknowledgement unsatisfied: {payload.human_ack_unsatisfied_count}")


@registry_app.command("verify")
def registry_verify(
    registry: Path = typer.Option(DEFAULT_REGISTRY_PATH, "--registry"),
    json_output: bool = typer.Option(False, "--json"),
    fail_on_issue: bool = typer.Option(
        False,
        "--fail-on-issue",
        help="Exit 20 when registry integrity issues are found.",
    ),
) -> None:
    """Verify row ids and optional per-repo hash-chain links."""
    loaded = _load_registry(registry)
    payload = _registry_verify_payload(registry, loaded)
    if json_output:
        typer.echo(payload.model_dump_json(indent=2))
        if fail_on_issue and not payload.ok:
            raise typer.Exit(20)
        return
    if payload.ok:
        typer.echo(f"Registry verified: {payload.row_count} row(s) in {registry}")
    else:
        typer.echo(f"Registry has {payload.issue_count} issue(s) in {registry}")
        for issue in payload.issues:
            row = issue.row_id or "unknown"
            typer.echo(f"- {issue.kind}: {row} {issue.message}")
    if payload.skipped_count:
        typer.echo(f"Skipped {payload.skipped_count} malformed row(s).", err=True)
    if fail_on_issue and not payload.ok:
        raise typer.Exit(20)


def _registry_summary_payload(
    registry: Path,
    loaded: LoadedRegistry,
) -> RegistrySummaryV1:
    rows = loaded.rows
    return RegistrySummaryV1(
        registry=str(registry),
        count=len(rows),
        skipped_count=len(loaded.skipped),
        skipped_rows=loaded.skipped,
        by_merge_verdict=_counts(row.merge_verdict for row in rows),
        by_decision=_counts(row.decision for row in rows),
        by_repo=_counts(row.repo for row in rows if row.repo),
        by_service=_counts(row.service for row in rows),
        by_tier=_counts(row.tier for row in rows),
        bypass_count=sum(
            1
            for row in rows
            if row.merge_verdict != "mergeable" and row.human_ack_satisfied is not True
        ),
        trust_root_touched_count=sum(1 for row in rows if row.trust_root_touched is True),
        policy_weakened_count=sum(1 for row in rows if row.policy_weakened is True),
        human_ack_required_count=sum(1 for row in rows if row.human_ack_required is True),
        human_ack_unsatisfied_count=sum(
            1
            for row in rows
            if row.human_ack_required is True and row.human_ack_satisfied is not True
        ),
        policy_pack_unverified_count=sum(
            1
            for row in rows
            for pack in row.policy_packs
            if isinstance(pack, dict) and pack.get("status") not in {None, "verified"}
        ),
    )


def _counts(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        if value is None:
            continue
        key = str(value)
        if not key:
            continue
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _registry_verify_payload(
    registry: Path,
    loaded: LoadedRegistry,
) -> RegistryVerificationResultV1:
    issues: list[RegistryVerificationIssueV1] = []
    seen_ids: set[str] = set()
    last_by_repo: dict[str, RegistryRowV1] = {}
    for row in loaded.rows:
        expected = _row_id(row)
        if row.row_id != expected:
            issues.append(
                RegistryVerificationIssueV1(
                    row_id=row.row_id,
                    repo=row.repo,
                    kind="row_id_mismatch",
                    message=f"expected {expected}",
                )
            )
        if row.row_id in seen_ids:
            issues.append(
                RegistryVerificationIssueV1(
                    row_id=row.row_id,
                    repo=row.repo,
                    kind="duplicate_row_id",
                    message="row_id appears more than once",
                )
            )
        seen_ids.add(row.row_id)
        previous = last_by_repo.get(row.repo)
        if row.previous_row_id or row.previous_row_sha256:
            if previous is None:
                issues.append(
                    RegistryVerificationIssueV1(
                        row_id=row.row_id,
                        repo=row.repo,
                        kind="missing_previous_row",
                        message="row carries a previous link but no prior row exists",
                    )
                )
            elif row.previous_row_id != previous.row_id:
                issues.append(
                    RegistryVerificationIssueV1(
                        row_id=row.row_id,
                        repo=row.repo,
                        kind="previous_row_id_mismatch",
                        message=f"expected {previous.row_id}",
                    )
                )
            elif row.previous_row_sha256 != _row_content_sha256(previous):
                issues.append(
                    RegistryVerificationIssueV1(
                        row_id=row.row_id,
                        repo=row.repo,
                        kind="previous_row_sha256_mismatch",
                        message="previous row hash does not match row content",
                    )
                )
        last_by_repo[row.repo] = row
    issue_count = len(issues) + len(loaded.skipped)
    return RegistryVerificationResultV1(
        registry=str(registry),
        row_count=len(loaded.rows),
        skipped_count=len(loaded.skipped),
        skipped_rows=loaded.skipped,
        issue_count=issue_count,
        ok=issue_count == 0,
        issues=issues,
    )
