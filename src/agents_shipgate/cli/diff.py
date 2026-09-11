"""`shipgate diff` — what this change does to the agent's authority.

The engine already computes this. `audit --host --drift` compares two host
inventories and names every typed grant change; it just required a baseline
someone had committed in advance and reachable from the branch, which is
why the answer was two checkouts and a recorded file away.

This command supplies the other side from Git instead: materialise the base
tree, read it with the same host readers, and hand both inventories to the
same comparator. No manifest, no committed baseline, no verdict — the rows
are the answer, and the release decision stays where it lives (#651).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import typer

from agents_shipgate.cli.workspace_guard import require_workspace
from agents_shipgate.core.capability_diff_rows import (
    ABSENT,
    CapabilityDiffRow,
    capability_diff_rows,
)
from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
)

DIFF_SCHEMA_VERSION = "0.1"


def _resolve_base(workspace: Path, base: str | None) -> tuple[str, str]:
    """The base ref and the merge-base commit this diff compares against."""

    from agents_shipgate.cli.verify.git import (
        commit_sha,
        detect_default_base,
        merge_base_sha,
    )

    # Same resolver `check` uses (#649), including its narrow local
    # fallback: a local `main` is refused while a remote exists, because the
    # remote is the authority it might be stale against, and used only where
    # the repository has no remote at all.
    requested = base or detect_default_base(
        workspace, "HEAD", allow_local_when_no_remote=True
    )
    if requested is None:
        # Nothing else to compare against — commonly because HEAD *is* the
        # default branch, or a branch has not diverged yet. The working tree
        # against HEAD is then the only question left, and it is a real one:
        # uncommitted edits still change what the agent may do. The header
        # names what was compared, so a narrower answer is never a silent
        # one. `check` refuses here instead, because it publishes a verdict
        # and a verdict against an unstated base is not reviewable (#649).
        if commit_sha(workspace, "HEAD") is None:
            raise typer.BadParameter(
                "This repository has no commits, so there is nothing to "
                "compare the working tree against.",
                param_hint="--base",
            )
        return "HEAD", str(commit_sha(workspace, "HEAD"))
    if commit_sha(workspace, requested) is None:
        raise typer.BadParameter(
            f"Base ref {requested!r} is not available locally. Fetch it first; "
            "this command never fetches.",
            param_hint="--base",
        )
    resolved = merge_base_sha(workspace, requested, "HEAD")
    if resolved is None:
        raise typer.BadParameter(
            f"No merge base between {requested!r} and HEAD, so there is no "
            "common point to compare from.",
            param_hint="--base",
        )
    return requested, resolved


def _render_table(rows: list[CapabilityDiffRow]) -> list[str]:
    """Two lines per change: the fact, then why it matters.

    One line per row put `why` in a seventh column and ran to about 190
    characters, so the column a reviewer most needs was the one their
    terminal wrapped or truncated.
    """

    severity_width = max(len(row.severity) for row in rows)
    direction_width = max(len(row.direction) for row in rows)
    lines: list[str] = []
    for row in rows:
        marker = "⚠" if row.expands else " "
        transition = (
            f"{row.before} → {row.after}"
            if row.before != ABSENT and row.after != ABSENT
            else (row.after if row.before == ABSENT else f"{row.before} → gone")
        )
        lines.append(
            f"{marker} {row.severity.ljust(severity_width)}  "
            f"{row.direction.ljust(direction_width)}  {row.subject}"
        )
        lines.append(f"{' ' * (severity_width + direction_width + 5)}{transition}")
        lines.append(f"{' ' * (severity_width + direction_width + 5)}{row.why}")
        lines.append("")
    return lines[:-1]


def run_capability_diff(
    *,
    workspace: Path,
    base: str | None,
    json_output: bool,
) -> int:
    require_workspace(workspace)
    base_ref, base_commit = _resolve_base(workspace, base)

    head = build_host_boundary_snapshot(workspace, cache=HostStaticParseCache())
    with tempfile.TemporaryDirectory(prefix="shipgate-diff-base-") as scratch:
        from agents_shipgate.cli.verify.git import archive_tree

        base_tree = Path(scratch) / "base"
        base_tree.mkdir()
        archive_tree(workspace, base_commit, base_tree)
        base_inventory = build_host_boundary_snapshot(
            base_tree, cache=HostStaticParseCache()
        ).inventory

    payload = build_host_drift_payload(
        baseline=build_host_grants_baseline(base_inventory),
        inventory=head.inventory,
        baseline_file=f"{base_ref}@{base_commit[:12]}",
    )
    rows = capability_diff_rows(payload)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "capability_diff_schema_version": DIFF_SCHEMA_VERSION,
                    "workspace": str(workspace.resolve()),
                    "base_ref": base_ref,
                    "base_commit": base_commit,
                    "comparison_status": payload.get("comparison_status"),
                    "incomparable_reasons": payload.get("incomparable_reasons") or [],
                    "rows": [row.as_dict() for row in rows],
                    "static_analysis_only": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if payload.get("comparison_status") != "comparable":
        typer.echo(
            f"Cannot compare against {base_ref}: "
            + "; ".join(str(reason) for reason in payload.get("incomparable_reasons") or [])
        )
        typer.echo(
            "This is an input limit, not a finding about the change. Nothing "
            "below is a claim that the change is safe."
        )
        return 0

    typer.echo(f"Agent capability diff  {base_ref} ({base_commit[:8]}) -> working tree")
    typer.echo("")
    if not rows:
        typer.echo("No change to what the agent may do.")
        return 0
    for line in _render_table(rows):
        typer.echo(line)
    typer.echo("")
    widened = sum(1 for row in rows if row.expands)
    typer.echo(
        f"{len(rows)} change(s)"
        + (f", {widened} widening what the agent may do (⚠)" if widened else "")
        + "."
    )
    typer.echo(
        "Static configuration only: this is what the files permit, not what "
        "the agent did. No verdict is implied."
    )
    return 0


def diff(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Repository to compare.",
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help=(
            "Base ref. Defaults to the detected default branch; the "
            "comparison runs from its merge base with HEAD."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the rows as JSON."),
) -> None:
    """Show what this change does to the agent's authority."""

    raise typer.Exit(
        run_capability_diff(workspace=workspace, base=base, json_output=json_output)
    )


__all__ = ["diff", "run_capability_diff", "DIFF_SCHEMA_VERSION", "ABSENT"]
