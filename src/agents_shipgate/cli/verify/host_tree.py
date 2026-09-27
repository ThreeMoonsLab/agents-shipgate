"""Materialize host declarations and their bounded direct script inputs (#702)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agents_shipgate.core.boundary_registry import is_boundary_surface_path
from agents_shipgate.core.host_grants import HostStaticParseCache, build_host_boundary_snapshot


def materialize_host_tree(
    workspace: Path, commit: str, destination: Path, *, archive: Callable,
) -> Path:
    """Select from verified declarations first, then materialize exact dependencies.

    The initial inventory is discovery only: missing script bytes there are
    expected, and its result is never returned as comparison evidence. No
    script content selects another file. Both passes use the same immutable
    commit and the existing archive's object/containment checks.
    """
    archive(workspace, commit, destination, scope=is_boundary_surface_path)
    snapshot = build_host_boundary_snapshot(
        destination, cache=HostStaticParseCache(reference_workspace=workspace),
    )
    dependencies = {
        item["path"]
        for grant in snapshot.inventory["grants"] if grant.get("kind") == "hook"
        for item in grant.get("script_inputs") or []
        if item.get("path") is not None and item.get("basis") is not None
        # The first archive already preserves links. Selecting one again
        # would expand the generic archive's link-target closure, although
        # this reader deliberately never consumes the target's bytes.
        and item.get("limit") not in {"redacted_dependency_path", "symlink_input"}
    }
    if not dependencies:
        return destination
    complete = destination.with_name(destination.name + "-with-hook-scripts")
    archive(
        workspace, commit, complete,
        scope=lambda path: is_boundary_surface_path(path) or path in dependencies,
    )
    return complete
