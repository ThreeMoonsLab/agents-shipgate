"""``agents-shipgate registry`` — a minimal local capability-release ledger.

v0 substrate for the cross-repo attestation registry (ROADMAP §4): a
local, append-only JSONL file of attestation rows that answers "which
capability was released, under which verdict, acknowledged by whom,
across which repos?" without any service. Rows are normalized
projections of ``agents-shipgate attest`` output; ingest is idempotent
(content-addressed by the attestation's own hashes), queries are
deterministic. No network, nothing executed.

Registry 0.6 adds a second row kind, ``drift_observation``: one scheduled
``audit --host --drift`` (and optionally ``capability diff``) comparison of
the repository with its approved state, chained in the same per-repo ledger
(#1119). ``registry report --drift`` reads them back.

This is intentionally tiny: a directory of repos can ingest into one
shared file (vendored, or synced like a lockfile) and `registry query`
becomes the org's capability-change audit trail. A hosted aggregation
plane, if it ever exists, consumes the same rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.core.control_evidence import (
    ControlMapError,
    control_evidence,
    extract_ref,
    load_control_map,
    render_control_evidence_markdown,
)
from agents_shipgate.core.drift_observation import (
    CapabilityDiffError,
    ObservationError,
    drift_observation,
    drift_report,
    drift_report_needs_review,
    parse_event_time,
)
from agents_shipgate.schemas.control_evidence import (
    CONTROL_EVIDENCE_FILENAME,
    CONTROL_EVIDENCE_MARKDOWN_FILENAME,
    LEDGER_EXTRACT_FILENAME,
)
from agents_shipgate.schemas.registry import (
    REGISTRY_LATE_DEFAULTED_FIELDS,
    REGISTRY_LATE_OPTIONAL_FIELDS,
    REGISTRY_SCHEMA_VERSION,
    RegistryBypassReportV1,
    RegistryDriftObservationV1,
    RegistryDriftReportV1,
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
    #: Each row's line as the ledger stores it, paired with ``rows``.
    lines: list[str] = field(default_factory=list)


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
    # Copied as attested; RegistryRowV1 validates it as the attestation model
    # does, so a block the attestation could not carry is refused, not dropped.
    approval = attestation.get("approval")
    if approval == {}:
        approval = None
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
        "approval_status": (
            approval.get("status", "not_supplied") if isinstance(approval, dict) else None
        ),
        "approval": approval,
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


_ROW_ID_PREFIX = {"attestation": "att_", "drift_observation": "obs_"}


def _row_id(row: dict[str, Any] | RegistryRowV1) -> str:
    payload = _row_identity_payload(row)
    return (
        _ROW_ID_PREFIX.get(str(payload.get("row_kind", "attestation")), "att_")
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
    )


def _row_identity_payload(row: dict[str, Any] | RegistryRowV1) -> dict[str, Any]:
    if isinstance(row, RegistryRowV1):
        payload = _hashed_row(row)
    else:
        pending = {**row, "row_id": str(row.get("row_id") or "att_pending")}
        payload = _hashed_row(RegistryRowV1.model_validate(pending))
    payload.pop("row_id", None)
    return payload


def _row_content_sha256(row: RegistryRowV1) -> str:
    return hashlib.sha256(
        json.dumps(
            _hashed_row(row),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _hashed_row(row: RegistryRowV1) -> dict[str, Any]:
    """The dumped row both hashes are taken over.

    A field added to the row after ledgers existed is left out while it is
    ``None`` (or, for a field with a default, while it holds that default), so
    an old row's id and the chain hash a later row stored for it are
    unchanged.
    """

    payload = row.model_dump(mode="json")
    for name in REGISTRY_LATE_OPTIONAL_FIELDS:
        if payload.get(name) is None:
            payload.pop(name, None)
    for name, default in REGISTRY_LATE_DEFAULTED_FIELDS.items():
        if payload.get(name) == default:
            payload.pop(name, None)
    return payload


def _load_registry(registry_path: Path) -> LoadedRegistry:
    if not registry_path.is_file():
        return LoadedRegistry(rows=[], skipped=[])
    rows: list[RegistryRowV1] = []
    skipped: list[RegistrySkippedRowV1] = []
    lines: list[str] = []
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
            lines.append(line)
        except ValidationError as exc:
            skipped.append(
                RegistrySkippedRowV1(
                    line=line_number,
                    reason=f"schema_validation_error: {exc.errors()[0]['msg']}",
                )
            )
    return LoadedRegistry(rows=rows, skipped=skipped, lines=lines)


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


def _row_from_observation(
    observation: RegistryDriftObservationV1,
    *,
    repo: str,
    event_time: str,
    workflow_run_id: str | None,
    head_sha: str | None,
    branch: str | None,
    previous_row: RegistryRowV1 | None,
) -> RegistryRowV1:
    row = {
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "row_kind": "drift_observation",
        "repo": repo,
        "workflow_run_id": workflow_run_id,
        "event_time": event_time,
        "branch": branch,
        "head_sha": head_sha,
        "observation": observation.model_dump(mode="json"),
        "previous_row_id": previous_row.row_id if previous_row is not None else None,
        "previous_row_sha256": (
            _row_content_sha256(previous_row) if previous_row is not None else None
        ),
    }
    row["row_id"] = _row_id(row)
    return RegistryRowV1.model_validate(row)


def _observation_key(row: RegistryRowV1) -> tuple[Any, ...] | None:
    """What makes two observation rows the same run.

    Not the payload alone: two daily runs that found nothing print the same
    bytes, and each is a run to record. The declared time, run and commit
    tell them apart; ingesting one run's files twice gives the same key.
    """

    if row.row_kind != "drift_observation" or row.observation is None:
        return None
    capability = row.observation.capability
    return (
        row.repo,
        row.observation.source_sha256,
        capability.source_sha256 if capability is not None else None,
        row.event_time,
        row.workflow_run_id,
        row.head_sha,
    )


def _observation_context(
    *,
    ci_context: str | None,
    repo: str | None,
    workflow_run_id: str | None,
    head_sha: str | None,
) -> dict[str, str | None]:
    context: dict[str, str | None] = {
        "repo": None,
        "workflow_run_id": None,
        "head_sha": None,
        "branch": None,
        "trigger": None,
    }
    if ci_context is not None:
        if ci_context != "github-actions":
            raise typer.BadParameter(
                "unsupported ci context; supported value: github-actions",
                param_hint="--ci-context",
            )
        context.update(
            {
                "repo": os.environ.get("GITHUB_REPOSITORY"),
                "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
                "head_sha": os.environ.get("GITHUB_SHA"),
                "branch": os.environ.get("GITHUB_REF_NAME"),
                "trigger": os.environ.get("GITHUB_EVENT_NAME"),
            }
        )
    explicit = {"repo": repo, "workflow_run_id": workflow_run_id, "head_sha": head_sha}
    context.update({key: value for key, value in explicit.items() if value is not None})
    return {
        key: (value.strip() or None) if isinstance(value, str) else None
        for key, value in context.items()
    }


def _read_json_input(path: Path, label: str) -> tuple[bytes, Any]:
    try:
        raw = path.read_bytes()
        return raw, json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        typer.echo(f"Could not read {label} {path}: {exc}", err=True)
        raise typer.Exit(3) from exc


def _append_row(registry: Path, row: RegistryRowV1) -> None:
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n")


def _ingest_observation(
    *,
    observation_path: Path,
    capability_diff_path: Path | None,
    registry: Path,
    repo: str | None,
    event_time: str | None,
    workflow_run_id: str | None,
    head_sha: str | None,
    ci_context: str | None,
) -> tuple[str, RegistryRowV1]:
    if event_time is None:
        typer.echo(
            "--observation needs --event-time: the time the comparison ran, which "
            "the engine never reads from a clock. In a workflow pass "
            '--event-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" from the step that ran it.',
            err=True,
        )
        raise typer.Exit(2)
    try:
        declared_time = parse_event_time(event_time)
    except ObservationError as exc:
        raise typer.BadParameter(str(exc), param_hint="--event-time") from exc
    context = _observation_context(
        ci_context=ci_context,
        repo=repo,
        workflow_run_id=workflow_run_id,
        head_sha=head_sha,
    )
    raw, payload = _read_json_input(observation_path, "observation")
    diff_raw, diff_payload = (
        _read_json_input(capability_diff_path, "capability diff")
        if capability_diff_path is not None
        else (None, None)
    )
    try:
        observation = drift_observation(
            payload,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            capability_diff=diff_payload,
            capability_diff_sha256=(
                hashlib.sha256(diff_raw).hexdigest() if diff_raw is not None else None
            ),
            trigger=context["trigger"],
        )
    except ObservationError as exc:
        source = capability_diff_path if isinstance(exc, CapabilityDiffError) else observation_path
        typer.echo(f"Observation error in {source}: {exc}", err=True)
        raise typer.Exit(3) from exc
    existing = _load_rows(registry)
    label = context["repo"] or ""
    row = _row_from_observation(
        observation,
        repo=label,
        event_time=declared_time,
        workflow_run_id=context["workflow_run_id"],
        head_sha=context["head_sha"],
        branch=context["branch"],
        previous_row=_last_row_for_repo(existing, label),
    )
    key = _observation_key(row)
    prior = next((item for item in existing if _observation_key(item) == key), None)
    if prior is not None:
        return "exists", prior
    _append_row(registry, row)
    return "ingested", row


@registry_app.command("ingest")
def registry_ingest(
    attestation: Path | None = typer.Option(
        None,
        "--attestation",
        help="Path to an attestation JSON produced by `agents-shipgate attest`.",
    ),
    observation: Path | None = typer.Option(
        None,
        "--observation",
        help=(
            "Path to the JSON `shipgate audit --host --drift --json` printed: one "
            "scheduled comparison with the acknowledged host-grants baseline, "
            "recorded as a drift_observation row. Copies ids, kinds and counts, "
            "never a grant's values."
        ),
    ),
    capability_diff: Path | None = typer.Option(
        None,
        "--capability-diff",
        help=(
            "With --observation: the same run's `agents-shipgate capability diff` "
            "JSON, the reviewed lock against a fresh export."
        ),
    ),
    event_time: str | None = typer.Option(
        None,
        "--event-time",
        help=(
            "With --observation (required): when the comparison ran, ISO 8601 with "
            "an offset. Stored in UTC."
        ),
    ),
    workflow_run_id: str | None = typer.Option(
        None, "--workflow-run-id", help="With --observation: the run that compared."
    ),
    head_sha: str | None = typer.Option(
        None, "--head-sha", help="With --observation: the commit that was compared."
    ),
    ci_context: str | None = typer.Option(
        None,
        "--ci-context",
        help=(
            "With --observation: read the repository, run id, commit, branch and "
            "trigger from the CI environment. Supported: github-actions. Explicit "
            "flags win."
        ),
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
    """Append one attestation or one drift observation to the ledger.

    Idempotent: an attestation by its exact bytes, an observation by its files
    and declared time, run and commit.
    """
    if (attestation is None) == (observation is None):
        typer.echo(
            "Pass exactly one of --attestation (a release attestation) or "
            "--observation (a scheduled `audit --host --drift --json` comparison).",
            err=True,
        )
        raise typer.Exit(2)
    if observation is not None:
        status, row = _ingest_observation(
            observation_path=observation,
            capability_diff_path=capability_diff,
            registry=registry,
            repo=repo,
            event_time=event_time,
            workflow_run_id=workflow_run_id,
            head_sha=head_sha,
            ci_context=ci_context,
        )
        _echo_ingest(status, row, registry=registry, json_output=json_output)
        return
    observation_only = {
        "--capability-diff": capability_diff,
        "--event-time": event_time,
        "--workflow-run-id": workflow_run_id,
        "--head-sha": head_sha,
        "--ci-context": ci_context,
    }
    misplaced = [flag for flag, value in observation_only.items() if value is not None]
    if misplaced:
        typer.echo(
            f"{', '.join(misplaced)} only apply to --observation; an attestation "
            "carries its own run context (set it on `agents-shipgate attest`).",
            err=True,
        )
        raise typer.Exit(2)
    assert attestation is not None
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
        try:
            row = _row_from_attestation(
                data,
                repo=repo,
                source_attestation_sha256=source_attestation_sha256,
                previous_row=_last_row_for_repo(
                    existing, repo or (data.get("org") or {}).get("repo") or ""
                ),
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ()))
            reason = str(first.get("msg", "")).removeprefix("Value error, ")
            typer.echo(
                f"{attestation} cannot be recorded: "
                + (f"{where}: " if where else "")
                + reason,
                err=True,
            )
            raise typer.Exit(3) from exc
        status = "ingested"
        _append_row(registry, row)
    _echo_ingest(status, row, registry=registry, json_output=json_output)


def _echo_ingest(status: str, row: RegistryRowV1, *, registry: Path, json_output: bool) -> None:
    if json_output:
        payload: dict[str, Any] = {"status": status, "row_id": row.row_id}
        if row.row_kind != "attestation":
            payload["row_kind"] = row.row_kind
            if row.observation is not None:
                payload["observation_status"] = row.observation.status
        typer.echo(json.dumps(payload))
    else:
        suffix = (
            f" ({row.observation.status})"
            if row.row_kind == "drift_observation" and row.observation is not None
            else ""
        )
        typer.echo(f"{status}: {row.row_id}{suffix} → {registry}")


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
    typer.echo(f"{len(selected)} row(s) in {registry}")
    if loaded.skipped:
        typer.echo(
            f"Skipped {len(loaded.skipped)} malformed registry row(s).",
            err=True,
        )
    for row in selected:
        if row.row_kind == "drift_observation":
            status = row.observation.status if row.observation is not None else "unknown"
            typer.echo(
                f"- {row.row_id} repo={row.repo or '—'} observation={status} "
                f"at={row.event_time or '—'}"
            )
            continue
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


def _is_bypass_candidate(row: RegistryRowV1) -> bool:
    return (
        row.row_kind == "attestation"
        and row.merge_verdict != "mergeable"
        and row.human_ack_satisfied is not True
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
    drift: bool = typer.Option(
        False,
        "--drift",
        help=(
            "Report drift observations: runs that found drift, runs that could "
            "not compare, and (with --expected-interval-days) stretches with no run."
        ),
    ),
    repo: str | None = typer.Option(
        None, "--repo", help="With --drift: only this repository label."
    ),
    since: str | None = typer.Option(
        None, "--since", help="With --drift: first UTC day of the window (YYYY-MM-DD)."
    ),
    until: str | None = typer.Option(
        None,
        "--until",
        help=(
            "With --drift: last UTC day of the window (YYYY-MM-DD), inclusive. "
            "Not later than the day the report runs, or the stretch after the "
            "last run is measured into the future."
        ),
    ),
    expected_interval_days: int | None = typer.Option(
        None,
        "--expected-interval-days",
        min=1,
        help=(
            "With --drift: report a gap where a repository went longer than this "
            "many days without an observation. Set it above the schedule's "
            "period, since scheduled runs can start late."
        ),
    ),
    json_output: bool = typer.Option(False, "--json"),
    fail_on_bypass: bool = typer.Option(
        False,
        "--fail-on-bypass",
        help="Exit 20 when --bypass finds at least one row.",
    ),
    fail_on_drift: bool = typer.Option(
        False,
        "--fail-on-drift",
        help=(
            "Exit 20 when --drift finds a run with drift, a run that could not "
            "compare, or a gap."
        ),
    ),
    control_map: Path | None = typer.Option(
        None,
        "--control-map",
        help=(
            "Report the rows, counts and gaps of one audit window (--since, "
            "--until) arranged by a control map, such as "
            "docs/control-maps/aiuc-1.yaml. States evidence; never says a control "
            "is met."
        ),
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help=(
            "With --control-map: write control-evidence.json, control-evidence.md "
            "and ledger-extract.jsonl (the cited ledger lines) to this directory."
        ),
    ),
) -> None:
    """Emit derived registry reports: --bypass, --drift or --control-map."""
    modes = sum((bypass, drift, control_map is not None))
    if modes != 1:
        typer.echo(
            "Pass exactly one of --bypass, --drift or --control-map."
            if modes
            else "Nothing to report: pass --bypass, --drift or --control-map.",
            err=True,
        )
        raise typer.Exit(2)
    if out is not None and control_map is None:
        typer.echo("--out only applies to --control-map.", err=True)
        raise typer.Exit(2)
    if control_map is not None:
        _control_map_command(
            registry=registry,
            control_map=control_map,
            repo=repo,
            since=since,
            until=until,
            expected_interval_days=expected_interval_days,
            out=out,
            json_output=json_output,
            fail_flags={"--fail-on-bypass": fail_on_bypass, "--fail-on-drift": fail_on_drift},
        )
        return
    if drift:
        _drift_report_command(
            registry=registry,
            repo=repo,
            since=since,
            until=until,
            expected_interval_days=expected_interval_days,
            json_output=json_output,
            fail_on_drift=fail_on_drift,
            fail_on_bypass=fail_on_bypass,
        )
        return
    drift_only = {
        "--repo": repo,
        "--since": since,
        "--until": until,
        "--expected-interval-days": expected_interval_days,
        "--fail-on-drift": fail_on_drift or None,
    }
    misplaced = [flag for flag, value in drift_only.items() if value is not None]
    if misplaced:
        typer.echo(f"{', '.join(misplaced)} only apply to --drift.", err=True)
        raise typer.Exit(2)
    loaded = _load_registry(registry)
    rows = [row for row in loaded.rows if _is_bypass_candidate(row)]
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


def _control_map_command(
    *,
    registry: Path,
    control_map: Path,
    repo: str | None,
    since: str | None,
    until: str | None,
    expected_interval_days: int | None,
    out: Path | None,
    json_output: bool,
    fail_flags: dict[str, bool],
) -> None:
    misplaced = [flag for flag, value in fail_flags.items() if value]
    if misplaced:
        typer.echo(
            f"{', '.join(misplaced)} do not apply to --control-map: it states "
            "evidence and never fails a run.",
            err=True,
        )
        raise typer.Exit(2)
    if since is None or until is None:
        typer.echo(
            "--control-map needs --since and --until: the audit window, in UTC "
            "days (YYYY-MM-DD).",
            err=True,
        )
        raise typer.Exit(2)
    since_day = _report_day(since, "--since")
    until_day = _report_day(until, "--until")
    assert since_day is not None and until_day is not None
    if out is not None and out.exists() and not out.is_dir():
        typer.echo(f"--out {out} exists and is not a directory.", err=True)
        raise typer.Exit(2)
    try:
        loaded_map, map_sha256 = load_control_map(control_map)
    except ControlMapError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    loaded = _load_registry(registry)
    # Spelled out rather than rendered from how this process was started, so
    # two runs of one review print the same command on any machine.
    recompute = shlex.join(
        [
            "agents-shipgate",
            "registry",
            "report",
            "--control-map",
            str(control_map),
            "--registry",
            LEDGER_EXTRACT_FILENAME if out is not None else str(registry),
            "--since",
            since_day.isoformat(),
            "--until",
            until_day.isoformat(),
            *(["--repo", repo] if repo is not None else []),
            *(
                ["--expected-interval-days", str(expected_interval_days)]
                if expected_interval_days is not None
                else []
            ),
            "--json",
        ]
    )
    try:
        view, extract_lines = control_evidence(
            control_map=loaded_map,
            map_path=str(control_map),
            map_sha256=map_sha256,
            registry=str(registry),
            rows=loaded.rows,
            lines=loaded.lines,
            skipped=loaded.skipped,
            since=since_day,
            until=until_day,
            repo=repo,
            expected_interval_days=expected_interval_days,
            recompute=recompute,
        )
    except ControlMapError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if out is not None:
        view = view.model_copy(
            update={"extract": extract_ref(LEDGER_EXTRACT_FILENAME, extract_lines)}
        )
        out.mkdir(parents=True, exist_ok=True)
        (out / LEDGER_EXTRACT_FILENAME).write_text(
            "".join(f"{line}\n" for line in extract_lines), encoding="utf-8"
        )
        (out / CONTROL_EVIDENCE_FILENAME).write_text(
            view.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (out / CONTROL_EVIDENCE_MARKDOWN_FILENAME).write_text(
            render_control_evidence_markdown(view), encoding="utf-8"
        )
    if json_output:
        typer.echo(view.model_dump_json(indent=2))
        return
    found = sum(control.gap_count for control in view.controls)
    typer.echo(
        f"{len(view.controls)} control(s) from {view.map.framework_label}, "
        f"{view.since} to {view.until}: {view.cited_row_count} row(s) cited, "
        f"{found} gap(s) found"
    )
    for control in view.controls:
        rows = sum(item.row_count for item in control.evidence)
        label = "not evidenced by this ledger" if not control.evidence else f"{rows} row(s)"
        typer.echo(f"- {control.id} {control.title}: {label}, {control.gap_count} gap(s)")
    if out is not None:
        typer.echo(
            f"Wrote {CONTROL_EVIDENCE_FILENAME}, {CONTROL_EVIDENCE_MARKDOWN_FILENAME} "
            f"and {LEDGER_EXTRACT_FILENAME} to {out}"
        )
    if loaded.skipped:
        typer.echo(f"Skipped {len(loaded.skipped)} malformed registry row(s).", err=True)


def _report_day(value: str | None, flag: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise typer.BadParameter(f"{value!r} is not a YYYY-MM-DD day", param_hint=flag) from exc


def _drift_report_command(
    *,
    registry: Path,
    repo: str | None,
    since: str | None,
    until: str | None,
    expected_interval_days: int | None,
    json_output: bool,
    fail_on_drift: bool,
    fail_on_bypass: bool,
) -> None:
    if fail_on_bypass:
        typer.echo("--fail-on-bypass only applies to --bypass; use --fail-on-drift.", err=True)
        raise typer.Exit(2)
    loaded = _load_registry(registry)
    try:
        payload = drift_report(
            registry=str(registry),
            rows=loaded.rows,
            skipped=loaded.skipped,
            repo=repo,
            since=_report_day(since, "--since"),
            until=_report_day(until, "--until"),
            expected_interval_days=expected_interval_days,
        )
    except ObservationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if json_output:
        typer.echo(payload.model_dump_json(indent=2))
    else:
        _echo_drift_report(payload, registry=registry, skipped=len(loaded.skipped))
    if fail_on_drift and drift_report_needs_review(payload):
        raise typer.Exit(20)


def _echo_drift_report(payload: RegistryDriftReportV1, *, registry: Path, skipped: int) -> None:
    window = ""
    if payload.since or payload.until:
        window = f" from {payload.since or 'the first row'} to {payload.until or 'the last row'}"
    typer.echo(
        f"{payload.observation_count} drift observation(s) in {registry}{window}: "
        f"{payload.drift_count} with drift, {payload.incomparable_count} incomparable, "
        f"{payload.clean_count} clean"
    )
    if payload.expected_interval_days is not None:
        typer.echo(
            f"{payload.gap_count} gap(s) longer than {payload.expected_interval_days} day(s)"
        )
    if payload.undated_count:
        typer.echo(f"{payload.undated_count} observation(s) without a readable event_time.")
    if skipped:
        typer.echo(f"Skipped {skipped} malformed registry row(s).", err=True)
    for row in payload.rows:
        observation = row.observation
        if observation is None or observation.status == "clean":
            continue
        host = observation.host
        detail = (
            f"host {len(host.grant_changes)} grant/{len(host.artifact_changes)} artifact "
            f"change(s)"
            if host.comparison_status == "comparable"
            else "host comparison incomparable"
        )
        if observation.capability is not None and observation.capability.has_drift:
            capability = observation.capability
            moved = (
                len(capability.added_ids)
                + len(capability.removed_ids)
                + len(capability.reidentified_ids)
                + len(capability.changed_ids)
                + len(capability.definition_changed_ids)
            )
            detail += f", {moved} capability change(s)"
        typer.echo(
            f"- {row.row_id} repo={row.repo or '—'} at={row.event_time or '—'} "
            f"{observation.status}: {detail}"
        )
    for gap in payload.gaps:
        days = gap.duration_seconds / 86400
        typer.echo(f"- gap repo={gap.repo or '—'} {gap.start} → {gap.end} ({days:.1f} days)")


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
    typer.echo(f"{payload.count} row(s) in {registry}")
    if payload.by_row_kind.get("drift_observation"):
        typer.echo(
            f"Attestations: {payload.by_row_kind.get('attestation', 0)}; "
            f"drift observations: {payload.by_row_kind['drift_observation']}"
        )
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
    every_row = loaded.rows
    # The release counts below describe attestations; an observation row has
    # no verdict, and would otherwise read as an unacknowledged bypass.
    rows = [row for row in every_row if row.row_kind == "attestation"]
    return RegistrySummaryV1(
        registry=str(registry),
        count=len(every_row),
        skipped_count=len(loaded.skipped),
        skipped_rows=loaded.skipped,
        by_row_kind=_counts(row.row_kind for row in every_row),
        by_observation_status=_counts(
            row.observation.status for row in every_row if row.observation is not None
        ),
        by_merge_verdict=_counts(row.merge_verdict for row in rows),
        by_decision=_counts(row.decision for row in rows),
        by_repo=_counts(row.repo for row in rows if row.repo),
        by_service=_counts(row.service for row in rows),
        by_tier=_counts(row.tier for row in rows),
        by_approval_status=_counts(row.approval_status for row in rows),
        bypass_count=sum(1 for row in rows if _is_bypass_candidate(row)),
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
