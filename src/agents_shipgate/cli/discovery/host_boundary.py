"""Read-only host applicability using the existing bounded boundary census."""

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_shipgate.core.boundary_registry import host_config_adapters_for_path
from agents_shipgate.core.host_grants import HostStaticParseCache, _repository_paths
from agents_shipgate.core.host_input_failure import HostInventoryReadError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.detect import DetectResult, HostBoundaryCandidate
from agents_shipgate.schemas.diagnostics import NextAction

#: What a census failure names when the failure itself carries no path.
#: ``_repository_paths`` already spells the workspace root ``"."``.
UNSEEN_WORKSPACE_SUBJECT = "."


def _link_may_conceal_a_tree(link: Path) -> bool:
    """Whether an unfollowed link could hide nested configuration.

    The link is never enumerated; only the *type* of what it points at is
    read, and that type is the whole question. A link that resolves to a
    regular file has no descendants to conceal, and counting one as
    concealment withheld the product-wide negative on every repository that
    keeps an ordinary ``docs/README.md -> ../README.md`` — turning a settled
    "not a Shipgate target" into a human stop about a file. Anything that
    cannot be typed at all — broken, cyclic, unreadable — stays concealment.
    """

    try:
        return stat.S_ISDIR(link.stat().st_mode)
    except OSError:
        return True


def discover_host_boundary(
    workspace: Path,
) -> tuple[list[HostBoundaryCandidate], list[str]]:
    """Enumerate names, including ignored settings, without opening configs.

    The host audit owns parsing. In particular, a malformed JSON file remains
    a candidate, and an unreadable census is never a successful empty result:
    the subject that stopped it comes back in the second element, which is
    what withholds the complete negative.

    A census that could not finish is *not* a reason to refuse the rest of the
    classification. ``audit --host`` owns this walk and records exactly these
    failures as an ``inventory_failures`` row before carrying on
    (``host_grants._host_repository``); discovery, which only wants an
    applicability hint, must not be stricter than the audit it routes to. It
    used to raise, so one ``chmod 000`` directory anywhere made ``detect``,
    ``init`` and ``bootstrap`` exit 4 on a repository the audit still audits,
    with a published recovery — "rerun detect" — that could never succeed.
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
                if not _link_may_conceal_a_tree(link_parent):
                    # The ancestor resolves to something with no descendants,
                    # so this synthesized path cannot exist and hides nothing.
                    continue
                incomplete.add(link_parent.relative_to(workspace).as_posix())
            elif path.is_symlink() and _link_may_conceal_a_tree(path):
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
    except HostInventoryReadError as exc:
        # No candidate is published from a census that did not finish; the
        # subject that stopped it is what a reader has to inspect.
        return [], [exc.failure.source or UNSEEN_WORKSPACE_SUBJECT]
    except (OSError, NotImplementedError, ValueError):
        # Including `IdentityReadBudgetExceeded`, which `finish()` raises
        # directly rather than through `HostInventoryReadError`.
        return [], [UNSEEN_WORKSPACE_SUBJECT]
    return candidates, sorted(incomplete)


def needs_host_route_payload(payload: Mapping[str, Any]) -> bool:
    """Host-only applicability; never select a manifest's scope from it.

    Written over the ``detect --json`` payload rather than the model because
    ``bootstrap`` reads exactly that — it shells out to the same binary and
    parses stdout — and a second spelling of this rule is how the two answers
    drift (#322). ``bootstrap`` adds its own ``shipgate.yaml`` check on top;
    the applicability question itself has one implementation.
    """
    return bool(
        not payload.get("is_agent_project") and not payload.get("suggested_sources")
        and not payload.get("codex_plugin_candidates")
        and not payload.get("python_parse_truncated")
        and payload.get("agent_scope") == "single"
        and (
            payload.get("host_boundary_candidates")
            or payload.get("host_discovery_incomplete_paths")
        )
    )


def needs_host_route(result: DetectResult) -> bool:
    """:func:`needs_host_route_payload` over an in-process classification."""
    return needs_host_route_payload(result.model_dump(mode="json"))


def listed_subjects(subjects: list[str], *, limit: int) -> str:
    """The first ``limit`` subjects, saying so whenever there are more.

    A display cap that reads as the whole set is the defect #397 fixed one
    field over: five names and a full stop assert there are five.
    """

    shown = ", ".join(subjects[:limit])
    remaining = len(subjects) - limit
    return f"{shown} (and {remaining} more)" if remaining > 0 else shown


def host_discovery_action(result: DetectResult, workspace: Path) -> NextAction:
    directories = [c.path for c in result.host_boundary_candidates if c.file_type == "directory"]
    if directories:
        return NextAction(
            kind="review",
            why="Host configuration paths are directories: "
                + listed_subjects(directories, limit=5) + ".",
            expects="Inspect these paths and supply the intended configuration files, then rerun detect.",
        )
    if not result.host_boundary_candidates:
        return NextAction(
            kind="review",
            # One sentence for both ways the census stops short — an
            # unfollowed link and a path it could not read are the same fact
            # to a caller: this workspace's host configuration was not
            # enumerated, so its absence is not established.
            why=("Host discovery could not see through these paths, so an absence of "
                 "host configuration is not established: "
                 + listed_subjects(result.host_discovery_incomplete_paths, limit=5) + "."),
            expects=(
                "Each named path inspected — an unfollowed link resolved, an unreadable "
                "directory made readable, or the actual project directory selected — "
                "then rerun detect."
            ),
        )
    return NextAction(
        kind="command",
        command=render_command(["audit", "--host", "--workspace", str(workspace), "--json"]),
        why=("Recognized host configuration paths were found. Their contents and "
             "grants have not been verified; audit them without creating shipgate.yaml."),
        expects="A repository host inventory with explicit parse, coverage and grant evidence.",
    )
