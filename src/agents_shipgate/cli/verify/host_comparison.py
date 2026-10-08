"""Manifest-free host evidence from exact Git trees, never the caller's other HEAD."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from agents_shipgate.cli.current_workspace import default_reports_dir, worktree_exclusion
from agents_shipgate.cli.verify.changed_inputs import comparison_changed_inputs
from agents_shipgate.cli.verify.git import (
    archive_tree,
    blob_path_identities,
    blob_path_unchanged,
    commit_sha,
    detect_default_base,
    merge_base_sha,
    require_merge_base_sha,
    shallow_merge_base_is_proven,
    tree_path_absent,
    tree_sha,
)
from agents_shipgate.core.boundary_diff import BoundaryInputIssue
from agents_shipgate.core.boundary_registry import (
    is_boundary_surface_path,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.host_comparison import (
    compare_host_inventories,
    without_untouched_script_limits,
)
from agents_shipgate.core.host_grants import (
    EnabledPluginHookFiles,
    HostBoundarySnapshot,
    HostStaticParseCache,
    build_host_boundary_snapshot,
    hook_dependency_limits,
    without_host_issues,
)
from agents_shipgate.core.unread_inputs import ChangedInputs
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
    comparison, and `check` routes a plugin manifest, marketplace or hook file
    only when the change touches one that declares an enabled plugin's hooks
    (#809). A plugin-reference limit both sides share on an untouched source
    is dropped there; one only one side has, or one the change touched, still
    refuses the comparison. `diff` and `verify` keep every limit: they name a
    shared parse or shape limit on an unchanged source, leave a plugin
    directory a plugin-reference limit is bounded by uncompared when nothing
    they compare depends on it (a ``partial`` comparison, #808), and refuse
    otherwise. For the same reason `check` leaves out the limit of a
    selected hook script the change does not touch (#702): it routes a
    changed script itself.

    ``coverage=False`` is for `check` too: its result carries no coverage, so
    it asks no identity question it would discard (#812), and lists the
    changed files for unread inputs, which it does not name (#821), only
    when a removed MCP server's row is worded from them (#929).

    A comparison that read no host artifact on either side is ``None`` as
    before, unless its coverage names a changed input this entry does not
    read (#821): that change is the one this result exists to name, so it is
    not handed to the setup route, which would say nothing about it. A
    changed candidate input it counts as not examined keeps it too (#821
    review cycle 2): the count is the only place that change is mentioned.
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
        from agents_shipgate.cli.verify.host_tree import materialize_host_tree

        before, base_read = materialize_host_tree(workspace, base_commit, before, archive=archive_tree)
        after = workspace
        head_read = None
        if head is not None:
            after = Path(scratch) / "head"
            after.mkdir()
            after, head_read = materialize_host_tree(workspace, head_commit, after, archive=archive_tree)
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

        def absent(path: str) -> bool:
            return tree_path_absent(workspace, base_commit, compared_head, path)

        def changed_inputs() -> ChangedInputs:
            """The change's own paths, without this run's output directory (#821)."""

            try:
                exclude = worktree_exclusion(workspace, out_dir or default_reports_dir(workspace))
            except (OSError, RuntimeError, ValueError):
                # Never guessed past: the change set is then not examined.
                return ChangedInputs(paths=None)
            return comparison_changed_inputs(workspace, base_commit, compared_head, exclude=exclude)

        base_snapshot = base_read or build_host_boundary_snapshot(
            before, cache=HostStaticParseCache(reference_workspace=workspace)
        )
        head_snapshot = head_read or build_host_boundary_snapshot(
            after, cache=HostStaticParseCache(reference_workspace=workspace)
        )
        base_inventory = base_snapshot.inventory
        head_inventory = head_snapshot.inventory
        if exclude_plugin_reference_limits:
            base_inventory, head_inventory = _without_shared_plugin_reference_limits(
                base_snapshot, head_snapshot, unchanged=unchanged
            )
            if hook_dependency_limits(base_inventory) or hook_dependency_limits(head_inventory):
                listed = changed_inputs().paths
                if listed is not None:
                    touched = set(listed)
                    base_inventory, head_inventory = without_untouched_script_limits(
                        base_inventory, head_inventory, touched.__contains__
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
            absent=absent,
            coverage=coverage,
            # Without coverage (`check`) the set is listed only if a removed
            # MCP server's row needs it (#929).
            changed_inputs=changed_inputs() if coverage else changed_inputs,
            # A plugin directory a limit is bounded by is left uncompared and
            # named, and the rest compared (#808). Only where coverage names
            # it: `check` records none, so it refuses as before.
            plugin_scopes=(base_snapshot.plugin_scopes, head_snapshot.plugin_scopes),
        )
        if identity() != captured_identity:
            raise ValueError("Host comparison inputs moved during the run")
        result.input_identity = captured_identity
        if head is None:
            from agents_shipgate.schemas.verification_identity import VerificationBlob

            reads = {**head_snapshot.cache.hook_script_reads, **head_snapshot.cache.openshell_input_reads}
            result.input_script_blobs = [
                VerificationBlob(
                    path=path, sha256="sha256:" + facts["sha256"],
                    size_bytes=facts["size_bytes"], source=facts.get("source", "worktree"),
                )
                for path, facts in sorted(reads.items())
                if facts.get("sha256") is not None
            ]
            result.input_script_absent_paths = sorted(
                head_snapshot.cache.hook_script_absences | head_snapshot.cache.openshell_input_absences
            )
            result.input_script_unconfirmable_paths = sorted(
                {path for captured in (
                    head_snapshot.cache.hook_script_reads,
                    head_snapshot.cache.openshell_input_reads,
                ) for path, facts in captured.items()
                if facts.get("limit") not in {None, "missing_input"}
                }
            )
        result_coverage = result.coverage
        mentions_unread = result_coverage is not None and (
            not result_coverage.read_sources_only
            or result_coverage.unread_candidates_not_examined > 0
        )
        if not result.paths and result.comparison_status == "comparable" and not mentions_unread:
            return None
        return result


def _without_shared_plugin_reference_limits(
    base: HostBoundarySnapshot,
    head: HostBoundarySnapshot,
    *,
    unchanged: Callable[[str], bool],
) -> tuple[dict, dict]:
    """Both inventories without the plugin-reference limits `check` may leave out (#714).

    `check` cannot name a limit, and routes a plugin file only where the
    change touches an enabled plugin's hooks (#809). So a blocking
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


def enabled_plugin_hook_evidence(
    *,
    workspace: Path,
    changed_files: list[str],
    head_is_worktree: bool,
    base: str | None,
    head: str | None = None,
) -> tuple[HostBoundarySnapshot | None, EnabledPluginHookFiles | None, list[BoundaryInputIssue]]:
    """Selected plugin declarations and executable dependencies on both sides.

    `check` and `verify` both call this, so the boundary decision each
    publishes for one change is made from the same evidence. Returns
    ``(working tree snapshot, enabled plugin hook files, issues)``; ``None``
    for either leaves the assessment to read the tree it evaluates.

    The route needs the side on which the plugin was enabled. A deleted hook
    file, or one the head stops selecting, is an enabled plugin's hook only in
    the base tree, so the merge base's plugin configuration is read exactly
    as the host comparison reads it: an archive of the boundary surface
    (:func:`is_boundary_surface_path`) and the same snapshot builder. A head
    commit is read the same way, since the working tree may hold something
    else; with ``head_is_worktree`` the working tree is the head. A caller
    with no commit to compare (a provided diff) passes no ``base``, and only
    the tree it evaluates is read.

    A script can have any filename, so the head's declarations are read for
    every nonempty change. Only boundary declarations are archived here:
    lexical references establish selection, while the comparison separately
    captures executable bytes. The base matters even when the head already
    selects a path, since another host may have selected it only at the
    base, so it is read too whenever the change touches a boundary surface.
    A change that touches none leaves every declaration byte-identical, so
    the base selects exactly what the head does and is not archived again
    (#702 review). An unreadable side leaves selection unestablished rather
    than guessed.
    """

    # Executables may have any repository filename. Read the declarations
    # before ruling out a changed file as a selected dependency.
    candidates = sorted(set(changed_files))
    if not candidates or not base:
        return None, None, []
    declarations_changed = any(is_boundary_surface_path(path) for path in candidates)
    from dataclasses import replace

    from agents_shipgate.cli.verify.host_tree import materialize_host_tree
    from agents_shipgate.core.openshell_boundary import OpenShellBoundaryEvidence

    openshell_sides = []

    snapshot: HostBoundarySnapshot | None = None
    files = EnabledPluginHookFiles()

    try:
        if head_is_worktree:
            commits = [
                commit_sha(workspace, "HEAD")
                if base == "HEAD"
                else merge_base_sha(workspace, base, "HEAD")
            ] if declarations_changed else []
            snapshot = build_host_boundary_snapshot(workspace, scope="repository")
            files = files.union(EnabledPluginHookFiles.of(snapshot))
            openshell_sides.append(snapshot)
            if snapshot.cache.openshell_selected_paths:
                if not commits:
                    commits.append(commit_sha(workspace, "HEAD") if base == "HEAD" else merge_base_sha(workspace, base, "HEAD"))
        else:
            head_ref = head or "HEAD"
            # Preserve each host's selection from both declaration trees.
            commits = [commit_sha(workspace, head_ref)]
            if declarations_changed:
                commits.append(merge_base_sha(workspace, base, head_ref))
        for commit in commits:
            if commit is None:
                raise ValueError("a compared commit is not available locally")
            with tempfile.TemporaryDirectory(prefix="shipgate-plugin-hooks-") as scratch:
                tree = Path(scratch) / "tree"
                tree, archived = materialize_host_tree(workspace, commit, tree, archive=archive_tree)
                archived = archived or build_host_boundary_snapshot(
                    tree, cache=HostStaticParseCache(reference_workspace=workspace))
                openshell_sides.append(archived)
                if not head_is_worktree and len(commits) == 1 and archived.cache.openshell_selected_paths:
                    commits.append(merge_base_sha(workspace, base, head or "HEAD"))
                files = files.union(EnabledPluginHookFiles.of(archived))
    except (OSError, RuntimeError, ValueError, ConfigError):
        return snapshot, None, [
            BoundaryInputIssue(
                code="host_inventory_unreadable",
                path=path,
                message=(
                    "The selected host inputs of a compared commit could not be read, so "
                    "whether this file declares selected plugin hooks or is a selected "
                    "hook executable is not established."
                ),
            )
            for path in candidates
        ]
    if openshell_sides:
        # Head is read first; the immutable merge base is the final side.
        head_side, base_side = openshell_sides[0], openshell_sides[-1]
        paths = frozenset(base_side.cache.openshell_selected_paths | head_side.cache.openshell_selected_paths)
        before_reads, after_reads = base_side.cache.openshell_input_reads, head_side.cache.openshell_input_reads
        links = frozenset(path for path in before_reads.keys() | after_reads.keys()
            if any(reads.get(path, {}).get("source") == "generated" for reads in (before_reads, after_reads))
            and before_reads.get(path) != after_reads.get(path))
        files = replace(files, openshell=OpenShellBoundaryEvidence(paths=paths, changed_links=links,
            before=base_side.inventory if len(openshell_sides) > 1 else None,
            after=head_side.inventory))
    return snapshot, files, []


def host_comparison_failure(
    workspace: Path, head: str | None, error: Exception
) -> HostComparison | None:
    """Keep a Git comparison failure out of the setup/init fallback."""
    from agents_shipgate.cli.verify.git import _history_is_truncated, ensure_git_workspace

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
