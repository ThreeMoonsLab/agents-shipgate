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
from pathlib import Path
from typing import Any

import typer

REGISTRY_SCHEMA_VERSION = "0.1"
DEFAULT_REGISTRY_PATH = Path(".agents-shipgate/registry.jsonl")

registry_app = typer.Typer(
    name="registry",
    help="Local capability-release ledger built from attestations.",
    no_args_is_help=True,
)


def _row_from_attestation(
    attestation: dict[str, Any], *, repo: str | None
) -> dict[str, Any]:
    verdict = attestation.get("verdict") or {}
    capability = attestation.get("capability") or {}
    human_ack = attestation.get("human_ack") or {}
    row = {
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "repo": repo or "",
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
        "human_ack": human_ack,
        "policy_snapshot_sha256": attestation.get("policy_snapshot_sha256"),
        "artifact_sha256": attestation.get("artifact_sha256"),
    }
    row["row_id"] = "att_" + hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return row


def _load_rows(registry_path: Path) -> list[dict[str, Any]]:
    if not registry_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in registry_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows


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
    if all(item.get("row_id") != row["row_id"] for item in existing):
        registry.parent.mkdir(parents=True, exist_ok=True)
        with registry.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        status = "ingested"
    if json_output:
        typer.echo(json.dumps({"status": status, "row_id": row["row_id"]}))
    else:
        typer.echo(f"{status}: {row['row_id']} → {registry}")


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
    rows = _load_rows(registry)
    selected = []
    for row in rows:
        if repo and row.get("repo") != repo:
            continue
        if verdict and row.get("merge_verdict") != verdict:
            continue
        if capability_id and capability_id not in (
            row.get("capability_change_ids") or []
        ):
            continue
        if trust_root_touched and row.get("trust_root_touched") is not True:
            continue
        selected.append(row)
    selected.sort(key=lambda row: str(row.get("row_id")))
    if json_output:
        typer.echo(
            json.dumps(
                {"registry": str(registry), "count": len(selected), "rows": selected},
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"{len(selected)} attestation row(s) in {registry}")
    for row in selected:
        flags = []
        if row.get("trust_root_touched"):
            flags.append("trust-root")
        if row.get("policy_weakened"):
            flags.append("policy-weakened")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        typer.echo(
            f"- {row.get('row_id')} repo={row.get('repo') or '—'} "
            f"verdict={row.get('merge_verdict')} "
            f"+{row.get('capability_added', 0)}/~{row.get('capability_modified', 0)}"
            f"/-{row.get('capability_removed', 0)}{suffix}"
        )
