"""Read-only host applicability using the existing bounded boundary census."""

from __future__ import annotations

import stat
from pathlib import Path

from agents_shipgate.core.boundary_registry import host_config_adapters_for_path
from agents_shipgate.core.errors import DiscoveryError
from agents_shipgate.core.host_grants import HostStaticParseCache, _repository_paths
from agents_shipgate.core.host_input_failure import HostInventoryReadError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.detect import DetectResult, HostBoundaryCandidate
from agents_shipgate.schemas.diagnostics import NextAction


def discover_host_boundary(
    workspace: Path,
) -> tuple[list[HostBoundaryCandidate], list[str]]:
    """Enumerate names, including ignored settings, without opening configs.

    The host audit owns parsing. In particular, a malformed JSON file remains
    a candidate, and an unreadable census is never a successful empty result.
    """
    cache = HostStaticParseCache()
    try:
        rows, _visited = _repository_paths(
            workspace, reader=cache.reader_for(workspace),
            limits=cache.configured_limits, include_directory_candidates=True,
        )
        candidates: list[HostBoundaryCandidate] = []
        incomplete: set[str] = set()
        for relative in sorted({row[1] for row in rows}):
            path = workspace / relative
            adapters = host_config_adapters_for_path(relative)
            link_parent = next((
                parent for parent in reversed(path.parents)
                if parent != workspace and workspace in parent.parents and parent.is_symlink()
            ), None)
            # The audit deliberately retains arbitrary symlinks that could
            # hide a recursive match. Their names are not host evidence.
            if link_parent is not None:
                incomplete.add(link_parent.relative_to(workspace).as_posix())
            elif path.is_symlink():
                incomplete.add(relative)
            if not adapters:
                continue
            try:
                if link_parent is not None:
                    raise FileNotFoundError
                mode = path.lstat().st_mode
            except FileNotFoundError:
                # Expected child beneath an unfollowed host-directory link.
                file_type = "unresolved"
            else:
                file_type = (
                    "symlink" if stat.S_ISLNK(mode) else
                    "directory" if stat.S_ISDIR(mode) else
                    "file" if stat.S_ISREG(mode) else "other"
                )
            candidates.append(HostBoundaryCandidate(
                path=relative,
                hosts=sorted({host for adapter in adapters for host in adapter.hosts}),
                file_type=file_type,
            ))
        cache.finish()
    except (HostInventoryReadError, OSError, NotImplementedError, ValueError) as exc:
        raise DiscoveryError(
            "Host configuration discovery could not read a stable, bounded "
            "repository inventory. Inspect unreadable paths or reduce the "
            "workspace, then rerun detect."
        ) from exc
    return candidates, sorted(incomplete)


def needs_host_route(result: DetectResult) -> bool:
    """Host-only applicability; never select a manifest's scope from it."""
    return bool(
        not result.is_agent_project and not result.suggested_sources
        and not result.codex_plugin_candidates and not result.python_parse_truncated
        and result.agent_scope == "single"
        and (result.host_boundary_candidates or result.host_discovery_incomplete_paths)
    )


def host_discovery_action(result: DetectResult, workspace: Path) -> NextAction:
    directories = [c.path for c in result.host_boundary_candidates if c.file_type == "directory"]
    if directories:
        return NextAction(
            kind="review",
            why="Host configuration paths are directories: " + ", ".join(directories[:5]) + ".",
            expects="Inspect these paths and supply the intended configuration files, then rerun detect.",
        )
    if not result.host_boundary_candidates:
        return NextAction(
            kind="review",
            why=("Unfollowed links may conceal host configuration: "
                 + ", ".join(result.host_discovery_incomplete_paths[:5]) + "."),
            expects="Inspect those links or select the actual project directory, then rerun detect.",
        )
    return NextAction(
        kind="command",
        command=render_command(["audit", "--host", "--workspace", str(workspace), "--json"]),
        why=("Recognized host configuration paths were found. Their contents and "
             "grants have not been verified; audit them without creating shipgate.yaml."),
        expects="A repository host inventory with explicit parse, coverage and grant evidence.",
    )
