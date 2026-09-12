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
from agents_shipgate.core.boundary_registry import is_boundary_surface_path
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
    inventory_is_complete,
)

DIFF_SCHEMA_VERSION = "0.1"


def _resolve_base(workspace: Path, base: str | None) -> tuple[str, str]:
    """The base ref and the merge-base commit this diff compares against."""

    from agents_shipgate.cli.verify.git import (
        _history_is_truncated,
        commit_sha,
        detect_default_base,
        merge_base_sha,
    )

    if base is not None and (not base.strip() or base.startswith("-")):
        raise typer.BadParameter("Base ref must be non-empty and cannot start with a dash.", param_hint="--base")

    def refuse_shallow() -> None:
        from agents_shipgate.cli.agent_mode import emit_agent_mode_error_action
        from agents_shipgate.invocation import join_argv
        from agents_shipgate.schemas.diagnostics import NextAction

        command = join_argv(["git", "-C", str(workspace), "fetch", "--unshallow"])
        message = (
            f"This checkout is shallow; run {command} "
            "(or use fetch-depth: 0 in CI), then rerun diff."
        )
        # A shallow boundary is expected missing history, not a corrupt graph.
        # Keep archive validation intact and recover before scanning either side.
        # Plain stderr keeps the command copyable; Rich parameter panels can
        # split paths/flags across lines in a narrow or color-enabled terminal.
        typer.echo(message, err=True)
        emit_agent_mode_error_action(
            "config_error",
            message=message,
            exit_code=2,
            action=NextAction(kind="command", command=command, why=message),
        )
        raise typer.Exit(2)

    truncated = _history_is_truncated(workspace) is True


    # Same resolver `check` uses (#649), including its narrow local
    # fallback: a local `main` is refused while a remote exists, because the
    # remote is the authority it might be stale against, and used only where
    # the repository has no remote at all.
    requested = base or detect_default_base(
        workspace, "HEAD", allow_local_when_no_remote=True, allow_equal_head=True
    )
    if requested is None:
        raise typer.BadParameter(
            "No base ref could be detected. Pass --base <ref> explicitly "
            "(use --base HEAD for uncommitted changes only).",
            param_hint="--base",
        )
    if commit_sha(workspace, requested) is None:
        if truncated:
            # The ref is missing and history is cut: fetching is the repair,
            # and it is a more useful answer than "fetch it first".
            refuse_shallow()
        raise typer.BadParameter(
            f"Base ref {requested!r} is not available locally. Fetch it first; "
            "this command never fetches.",
            param_hint="--base",
        )
    resolved = merge_base_sha(workspace, requested, "HEAD")
    if resolved is None:
        if truncated:
            refuse_shallow()
        raise typer.BadParameter(
            f"No merge base between {requested!r} and HEAD, so there is no "
            "common point to compare from.",
            param_hint="--base",
        )
    # Shallow is refused for the comparisons it actually breaks, not for
    # being shallow. The base tree is read through a scoped archive that
    # packs the tree and walks no ancestry, so a base this clone already
    # holds is readable at `--depth 1`. What shallowness can still break is
    # the *choice* of base: `git merge-base` cannot see past a graft, so a
    # merge base that is itself a graft may not be the real one, and
    # comparing against the wrong base silently is worse than refusing.
    # Refusing unconditionally sent `actions/checkout`'s default
    # (`fetch-depth: 1`) to `git fetch --unshallow` for a comparison the
    # tool could already answer (#686).
    # No graft check here on purpose. `git merge-base` does not guess past a
    # shallow boundary — it reports nothing, which the branch above turns
    # into the `--unshallow` recovery. Verified on two diverged shallow
    # clones whose true base lay past the graft: both returned empty, never
    # a wrong commit. So a merge base that *is* returned was proven through
    # commits this clone holds, and no absent commit can be a more recent
    # common ancestor. A graft check could not be made to fire on a wrong
    # answer, and a guard that cannot fire is not a guard.
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
    from agents_shipgate.cli.verify.git import ensure_git_workspace
    from agents_shipgate.core.errors import ConfigError

    try:
        workspace = ensure_git_workspace(workspace)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc), param_hint="--workspace") from exc
    base_ref, base_commit = _resolve_base(workspace, base)

    head = build_host_boundary_snapshot(workspace, cache=HostStaticParseCache())
    with tempfile.TemporaryDirectory(prefix="shipgate-diff-base-") as scratch:
        from agents_shipgate.cli.verify.git import archive_tree

        base_tree = Path(scratch) / "base"
        base_tree.mkdir()
        archive_tree(
            workspace, base_commit, base_tree, scope=is_boundary_surface_path
        )
        base_inventory = build_host_boundary_snapshot(
            base_tree, cache=HostStaticParseCache()
        ).inventory

    if not inventory_is_complete(base_inventory):
        payload = {
            "comparison_status": "incomparable",
            "incomparable_reasons": ["base_inventory_incomplete"],
            "changes": [],
            "expansion_signals": [],
        }
    else:
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
        typer.echo("No static host-grant changes detected. No verdict is implied.")
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
        help="Checkout to compare; paths inside a checkout resolve to its repository root.",
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
