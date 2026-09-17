"""Manifest-free host evidence from exact Git trees, never the caller's other HEAD."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from agents_shipgate.cli.current_workspace import default_reports_dir
from agents_shipgate.cli.verify.git import (
    archive_tree,
    blob_path_identities,
    blob_path_unchanged,
    commit_sha,
    detect_default_base,
    require_merge_base_sha,
    shallow_merge_base_is_proven,
    tree_sha,
)
from agents_shipgate.core.boundary_registry import is_boundary_surface_path
from agents_shipgate.core.host_comparison import compare_host_inventories
from agents_shipgate.core.host_grants import (
    HostBoundarySnapshot,
    build_host_boundary_snapshot,
    without_host_issues,
)
from agents_shipgate.schemas.host_comparison import HostComparison


def compare_host_refs(
    *,
    workspace: Path,
    base: str | None,
    head: str | None,
    auto_base: bool,
    config_relative: Path,
    require_unconfigured: bool = True,
    out_dir: Path | None = None,
    redact_permission_arguments: bool = False,
    exclude_plugin_reference_limits: bool = False,
    coverage: bool = True,
) -> HostComparison | None:
    """None means no host route, or an application manifest must still be gated.

    Exceptions remain input failures; callers must never turn one into an empty
    comparable result. Explicit heads are archived even when they equal HEAD:
    dirty files in the checkout do not belong to that requested commit.

    ``exclude_plugin_reference_limits`` is for `check` only (#714). Its result
    cannot name a limit, so an unchanged one would refuse the whole
    comparison, and `check` does not route plugin manifests or plugin hook
    files. A plugin-reference limit both sides share on an untouched source
    is dropped there; one only one side has, or one the change touched, still
    refuses the comparison. `diff` and `verify` keep every limit: they name a
    shared parse or shape limit on an unchanged source, and refuse otherwise.

    ``coverage=False`` is for `check` too: its result carries no coverage, so
    it asks no identity question it would discard (#812).
    """
    from agents_shipgate.cli.verify.orchestrator import (
        _safe_repository_identity,
        _safe_worktree_overlay,
    )
    from agents_shipgate.schemas.current_control import CurrentControlWorkspaceIdentity

    if require_unconfigured and config_relative != Path("shipgate.yaml"):
        return None  # An explicitly selected application config must be supplied.
    head_commit = commit_sha(workspace, head or "HEAD")
    if head_commit is None:
        raise ValueError("The requested head commit is not available locally")
    base_ref = base
    if base_ref is None and auto_base:
        base_ref = detect_default_base(
            workspace, head_commit, allow_local_when_no_remote=True, allow_equal_head=True
        )
        if base_ref is None:
            raise ValueError("No comparison base is available; pass --base explicitly")
    base_tip = commit_sha(workspace, base_ref) if base_ref else None
    base_commit = (
        require_merge_base_sha(workspace, base_tip, head_commit) if base_tip else head_commit
    )
    if base_ref and base_tip is None:
        raise ValueError("The requested base commit is not available locally")
    if base_tip and not shallow_merge_base_is_proven(workspace, base_tip, head_commit, base_commit):
        # Existing callers route a failed shallow comparison to fetch recovery;
        # never publish rows relative to a potentially older common ancestor.
        raise ValueError("Shallow history cannot establish the comparison merge base")

    def identity():
        bound, overlay = _safe_worktree_overlay(
            workspace, exclude=out_dir or default_reports_dir(workspace)
        )
        if not bound:
            raise ValueError("The working tree could not be captured for comparison")
        return CurrentControlWorkspaceIdentity(
            repository=_safe_repository_identity(workspace),
            head_ref=head or "HEAD",
            head_commit_sha=commit_sha(workspace, head or "HEAD"),
            head_tree_sha=tree_sha(workspace, head or "HEAD"),
            base_ref=base_ref,
            base_commit_sha=commit_sha(workspace, base_ref) if base_ref else None,
            merge_base_sha=base_commit if base_ref else None,
            snapshot_kind="committed_tree" if head is not None else "worktree_overlay",
            worktree_overlay_sha256=None if head is not None else overlay,
        )

    captured_identity = identity()
    if captured_identity.head_commit_sha != head_commit or captured_identity.base_commit_sha != base_tip:
        raise ValueError("Host comparison refs moved while their identity was captured")
    with tempfile.TemporaryDirectory(prefix="shipgate-host-comparison-") as scratch:
        before = Path(scratch) / "base"
        before.mkdir()
        # The host comparison reads host surface only, so it archives host
        # surface only: the same scope the live reader uses (#686, #688).
        archive_tree(
            workspace, base_commit, before, scope=is_boundary_surface_path
        )
        after = workspace
        if head is not None:
            after = Path(scratch) / "head"
            after.mkdir()
            archive_tree(
                workspace, head_commit, after, scope=is_boundary_surface_path
            )
        # Removing a configured gate, or selecting a historical head containing
        # one, is not first adoption. Leave the existing verifier route intact.
        if require_unconfigured and (
            (before / config_relative).exists() or (after / config_relative).exists()
        ):
            return None
        compared_head = head_commit if head is not None else None

        def unchanged(source: str) -> bool:
            return blob_path_unchanged(workspace, base_commit, compared_head, source)

        def identities(paths):
            return blob_path_identities(workspace, base_commit, compared_head, paths)

        base_snapshot = build_host_boundary_snapshot(before)
        head_snapshot = build_host_boundary_snapshot(after)
        base_inventory = base_snapshot.inventory
        head_inventory = head_snapshot.inventory
        if exclude_plugin_reference_limits:
            base_inventory, head_inventory = _without_shared_plugin_reference_limits(
                base_snapshot, head_snapshot, unchanged=unchanged
            )
        result = compare_host_inventories(
            base_inventory,
            head_inventory,
            head_kind="commit" if head is not None else "worktree",
            base_commit=base_commit,
            head_commit=head_commit,
            redact_permission_arguments=redact_permission_arguments,
            unchanged=unchanged,
            identities=identities,
            coverage=coverage,
        )
        if identity() != captured_identity:
            raise ValueError("Host comparison inputs moved during the run")
        result.input_identity = captured_identity
        if not result.paths and result.comparison_status == "comparable":
            return None
        return result


def _without_shared_plugin_reference_limits(
    base: HostBoundarySnapshot,
    head: HostBoundarySnapshot,
    *,
    unchanged: Callable[[str], bool],
) -> tuple[dict, dict]:
    """Both inventories without the plugin-reference limits `check` may leave out (#714).

    `check` cannot name a limit and routes no plugin file, so a blocking
    plugin-reference limit both sides carry, on a source the change did not
    touch, is dropped: `1.0.0` never read plugin files, and nothing about that
    plugin changed. A blocking one only one side carries, or one on a source
    the change touched, is kept. The comparison then refuses with
    `base_inventory_incomplete` or `head_inventory_incomplete`, instead of
    publishing an added or removed row built from evidence it could not read.
    A non-blocking plugin-reference issue never affects completeness and is
    dropped.
    """

    def key(issue: dict) -> tuple[str, str, str]:
        return (str(issue.get("kind")), str(issue.get("host")), str(issue.get("source")))

    def blocking(snapshot: HostBoundarySnapshot) -> set[tuple[str, str, str]]:
        return {
            key(issue)
            for issue in snapshot.inventory.get("issues", [])
            if issue.get("blocking") and issue.get("issue_id") in snapshot.plugin_reference_issue_ids
        }

    shared = {
        item for item in blocking(base) & blocking(head) if unchanged(item[2])
    }

    def dropped(snapshot: HostBoundarySnapshot) -> set[str]:
        return {
            str(issue["issue_id"])
            for issue in snapshot.inventory.get("issues", [])
            if issue.get("issue_id") in snapshot.plugin_reference_issue_ids
            and (not issue.get("blocking") or key(issue) in shared)
        }

    return (
        without_host_issues(base.inventory, dropped(base)),
        without_host_issues(head.inventory, dropped(head)),
    )


def host_comparison_failure(
    workspace: Path, head: str | None, error: Exception
) -> HostComparison | None:
    """Keep a Git comparison failure out of the setup/init fallback."""
    from agents_shipgate.cli.verify.git import _history_is_truncated, ensure_git_workspace
    from agents_shipgate.core.errors import ConfigError

    try:
        ensure_git_workspace(workspace)
    except ConfigError:
        return None  # Non-Git setup still has its original discovery route.
    if not build_host_boundary_snapshot(workspace).inventory["artifacts"]:
        return None  # Preserve missing-config precedence for application-only repositories.
    shallow = _history_is_truncated(workspace) is True
    return HostComparison(
        comparison_status="incomparable",
        incomparable_reasons=[
            "shallow_history" if shallow else f"comparison_input_unavailable:{type(error).__name__}"
        ],
        head_kind="commit" if head is not None else "worktree",
    )
