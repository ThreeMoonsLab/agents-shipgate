"""Materialize host declarations and their bounded direct script inputs (#702)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agents_shipgate.core.boundary_registry import is_boundary_surface_path
from agents_shipgate.core.host_grants import (
    HostBoundarySnapshot,
    HostStaticParseCache,
    build_host_boundary_snapshot,
)


def materialize_host_tree(
    workspace: Path, commit: str, destination: Path, *, archive: Callable,
) -> tuple[Path, HostBoundarySnapshot | None]:
    """Select from verified declarations first, then materialize exact dependencies.

    Returns the tree to read and, when it selects no script, the snapshot
    already read from it, so the caller does not read it twice. Otherwise
    the initial inventory is discovery only: missing script bytes there are
    expected, and its result is never returned as comparison evidence. No
    script content selects another file. Both trees come from one copy of
    the immutable commit's verified object graph (``archive``'s ``rescope``),
    with the archive's object and containment checks on each.
    """

    complete = destination.with_name(destination.name + "-with-hook-scripts")
    read: dict[str, HostBoundarySnapshot] = {}

    def dependencies(tree: Path) -> tuple[Path, Callable[[str], bool]] | None:
        snapshot = build_host_boundary_snapshot(
            tree, cache=HostStaticParseCache(reference_workspace=workspace),
        )
        found = {
            item["path"]
            for grant in snapshot.inventory["grants"] if grant.get("kind") == "hook"
            for item in grant.get("script_inputs") or []
            if item.get("path") is not None and item.get("basis") is not None
            # The first archive already preserves links. Selecting one again
            # would expand the generic archive's link-target closure, although
            # this reader deliberately never consumes the target's bytes.
            and item.get("limit") not in {"redacted_dependency_path", "symlink_input"}
        }
        if not found:
            read["snapshot"] = snapshot
            return None
        return complete, lambda path: is_boundary_surface_path(path) or path in found

    archive(workspace, commit, destination, scope=is_boundary_surface_path, rescope=dependencies)
    if "snapshot" in read:
        return destination, read["snapshot"]
    return complete, None
