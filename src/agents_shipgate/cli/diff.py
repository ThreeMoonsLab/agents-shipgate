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
from typing import NoReturn

import typer

from agents_shipgate.cli.workspace_guard import require_workspace
from agents_shipgate.core.agent_control_envelope import single_line_text
from agents_shipgate.core.boundary_registry import is_boundary_surface_path
from agents_shipgate.core.capability_diff_rows import (
    ABSENT,
    CapabilityDiffRow,
)
from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
)

# 0.2 adds `unchanged_limits` (#721).
DIFF_SCHEMA_VERSION = "0.2"


def _resolve_base(workspace: Path, base: str | None) -> tuple[str, str]:
    """The base ref and the merge-base commit this diff compares against."""

    from agents_shipgate.cli.verify.git import (
        _history_is_truncated,
        commit_sha,
        detect_default_base,
        merge_base_sha,
        shallow_merge_base_is_proven,
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
    if truncated:
        # A merge can expose an older common ancestor along one parent while
        # a shallow graft hides a newer one along another. A nonempty answer
        # alone is therefore insufficient. After excluding the candidate and
        # its ancestry, every visible path from both tips must terminate: any
        # remaining root may be a graft hiding a better common ancestor.
        # HEAD/self and fully visible paths to the candidate remain usable.
        base_commit = commit_sha(workspace, requested)
        head_commit = commit_sha(workspace, "HEAD")
        if base_commit is None or head_commit is None:
            refuse_shallow()
        if not shallow_merge_base_is_proven(workspace, base_commit, head_commit, resolved):
            refuse_shallow()
    return requested, resolved


def _refuse_objects_missing(workspace: Path, base_ref: str, base_commit: str) -> NoReturn:
    """Name the hydration a partial clone needs instead of a traceback (#817).

    In a `--filter=blob:none` clone only what was checked out has blobs, and in
    a `--filter=tree:0` clone only its trees too; the base tree's objects do
    not arrive until something fetches them, and Shipgate never does. The
    repair is the one `verify` names for the same `objects_missing` reason,
    worded by the same function, bound here to this workspace and to the
    remote the clone fetches from. `--refetch` alone would keep the clone's
    filter and fetch no blob, which is why `--no-filter` is part of it.
    """

    from agents_shipgate.cli.agent_mode import emit_agent_mode_error_action
    from agents_shipgate.cli.verify.git import (
        objects_missing_remediation,
        promisor_remote,
    )
    from agents_shipgate.invocation import join_argv
    from agents_shipgate.schemas.diagnostics import NextAction

    remote = promisor_remote(workspace) or "origin"
    command = join_argv(
        ["git", "-C", str(workspace), "fetch", "--refetch", "--no-filter", remote]
    )
    message = (
        f"The base side of this diff, {_one_line(base_ref)} ({base_commit[:8]}), "
        "could not be read (objects_missing). "
        + objects_missing_remediation(command)
    )
    typer.echo(message, err=True)
    emit_agent_mode_error_action(
        "objects_missing",
        message=message,
        exit_code=2,
        action=NextAction(kind="command", command=command, why=message),
    )
    raise typer.Exit(2)


def _one_line(value: object) -> str:
    return single_line_text(str(value))


def _render_table(rows: list[CapabilityDiffRow]) -> list[str]:
    """Two lines per change: the fact, then why it matters.

    One line per row put `why` in a seventh column and ran to about 190
    characters, so the column a reviewer most needs was the one their
    terminal wrapped or truncated.

    Every field carries repository text — a step name, a `uses:` value, a
    permission rule — so each is rendered on one line: a newline, escape or
    bidi control inside one is shown, and cannot print a row of its own.
    """

    severity_width = max(len(_one_line(row.severity)) for row in rows)
    direction_width = max(len(_one_line(row.direction)) for row in rows)
    indent = " " * (severity_width + direction_width + 5)
    lines: list[str] = []
    for row in rows:
        marker = "⚠" if row.expands else " "
        before, after = _one_line(row.before), _one_line(row.after)
        transition = (
            f"{before} → {after}"
            if row.before != ABSENT and row.after != ABSENT
            else (after if row.before == ABSENT else f"{before} → gone")
        )
        lines.append(
            f"{marker} {_one_line(row.severity).ljust(severity_width)}  "
            f"{_one_line(row.direction).ljust(direction_width)}  {_one_line(row.subject)}"
        )
        lines.append(f"{indent}{transition}")
        lines.append(f"{indent}{_one_line(row.why)}")
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
        from agents_shipgate.cli.verify.git import (
            PromisedObjectsMissingError,
            archive_fetched_tree,
        )

        base_tree = Path(scratch) / "base"
        base_tree.mkdir()
        try:
            archive_fetched_tree(
                workspace, base_commit, base_tree, scope=is_boundary_surface_path
            )
        except PromisedObjectsMissingError:
            # The head is the working tree, so the base is the only side that
            # reads objects. Any other archive failure is raised as it was.
            _refuse_objects_missing(workspace, base_ref, base_commit)
        base_inventory = build_host_boundary_snapshot(
            base_tree, cache=HostStaticParseCache()
        ).inventory

    from agents_shipgate.cli.verify.git import blob_path_unchanged
    from agents_shipgate.core.host_comparison import compare_host_inventories

    # One comparison for diff, verify and check (#721). An unchanged partial or
    # experimental surface is named as a limit instead of refusing every row.
    comparison = compare_host_inventories(
        base_inventory,
        head.inventory,
        head_kind="worktree",
        base_commit=base_commit,
        unchanged=lambda source: blob_path_unchanged(workspace, base_commit, None, source),
    )
    rows = list(comparison.rows)
    limits = [limit.model_dump(mode="json") for limit in comparison.unchanged_limits]
    payload = {
        "comparison_status": comparison.comparison_status,
        "incomparable_reasons": comparison.incomparable_reasons,
    }

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
                    "unchanged_limits": limits,
                    "static_analysis_only": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if payload.get("comparison_status") != "comparable":
        typer.echo(
            f"Cannot compare against {_one_line(base_ref)}: "
            + "; ".join(_one_line(reason) for reason in payload.get("incomparable_reasons") or [])
        )
        typer.echo(
            "This is an input limit, not a finding about the change. Nothing "
            "below is a claim that the change is safe."
        )
        return 0

    typer.echo(
        f"Agent capability diff  {_one_line(base_ref)} ({base_commit[:8]}) -> working tree"
    )
    typer.echo("")
    if limits:
        typer.echo(
            "Not compared: unchanged in this change and not read, so no claim is made about them:"
        )
        for limit in limits:
            typer.echo(
                f"  {_one_line(limit['host'])} {_one_line(limit['source'])} — "
                f"{_one_line(limit['limit'])}"
            )
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
