"""The commands that carry out a scope decision, for every command that reports one.

``init --write`` refuses when a workspace holds several self-contained projects
that define agents, and ``detect`` reports the same fact one step earlier. Both
then owe the caller the same thing: the exact ``init`` invocation for each
candidate, so the decision "which project is this change about?" is the only
work left to do.

``init`` published those commands and ``detect`` published a JSON selector
inside prose — ``init --workspace <agent_project_candidates[].path> --write`` —
and no runnable command at all, so the loop that ``allowed_next_commands``
promises ended at the first ``detect`` (#397). One builder here rather than a
copy in each: two commands an adopter runs in sequence must not publish
different recoveries for one workspace.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.detect import AgentProjectCandidate
from agents_shipgate.schemas.diagnostics import NextAction

# A monorepo can hold hundreds of agent projects. A *human* refusal lists
# enough of them to recognize the shape and points at the JSON payload for the
# rest. This cap belongs to that summary alone: applying it to the machine
# routes below silently left candidate 11 onward with no command, which is the
# dead end this module exists to close — `detect --workspace samples --json`
# found 22 candidates and emitted 10 commands, and issue #397's own
# reproduction has 25 (#397 review).
MAX_LISTED_SCOPE_CANDIDATES = 10

# The manifest `init` writes and `doctor` reads. Fixed rather than threaded
# through: `init --write` writes this name and nothing else, so a candidate
# carrying it is a candidate `init` would refuse.
MANIFEST_NAME = "shipgate.yaml"


def rebased_kit_flags(
    kit: Path | None, *, source: Path, target: Path
) -> list[str] | None:
    """``--agent-instructions-kit`` for a command run in ``target``.

    Returns ``None`` when the kit cannot be named from there, which is a
    refusal rather than a fallback: a kit path is resolved *under the
    workspace*, so copying a root-relative ``.agents-shipgate/kit.yaml``
    into a command that runs in ``apps/a`` points at a file that does not
    exist, and the emitted command exits 2 (#363 review).
    """

    if kit is None:
        return []
    resolved = kit if kit.is_absolute() else (source / kit)
    try:
        relative = resolved.resolve().relative_to(target.resolve())
    except (OSError, ValueError):
        return None
    return ["--agent-instructions-kit", relative.as_posix()]


def _adopted(directory: Path) -> Path | None:
    """The manifest already in ``directory``, when one is there to route to.

    A symlinked manifest is treated as absent: the loader refuses a manifest
    path with symlink components, so routing ``doctor`` at one would publish a
    command that exits 2 (#363 review). The ``init`` route is emitted instead,
    and reports the real problem.
    """

    candidate = directory / MANIFEST_NAME
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
    except OSError:  # pragma: no cover - unreadable directory
        return None
    return candidate


def scope_candidate_actions(
    workspace: Path,
    candidates: Sequence[AgentProjectCandidate],
    *,
    setup_flags: Sequence[str] = (),
    kit: Path | None = None,
    init_refreshes_existing: bool = False,
) -> list[NextAction]:
    """One exact command per candidate project, in the caller's order.

    These are ranked *below* the decision that selects among them — promoting
    one would make the same arbitrary pick the refusal exists to prevent — so
    this returns only the commands and leaves rank 1 to the caller.

    Every candidate gets one, with no cap. The list is exactly as long as the
    ``agent_project_candidates[]`` the same payload already publishes in full,
    and a candidate the reader can select but not run is the dead end this is
    here to close.

    **A candidate that already carries a manifest routes to ``doctor``, not to
    ``init``.** A nested ``shipgate.yaml`` is itself evidence of a project, so
    adopted directories are candidates too — on this repository's own
    ``samples/``, 21 of 22 are — and ``init --write`` there exits 2 on a
    manifest it will not overwrite while ``expects`` promises a file that
    already exists. ``doctor`` is the command that answers what is actually
    outstanding for an adopted project, and it is the same handoff ``detect``
    already makes for an adopted workspace root (#397 review).

    ``init_refreshes_existing`` is the one exception, and the caller owns it
    because only the caller knows what it asked for: with
    ``--agent-instructions`` selected, ``init --write`` deliberately leaves an
    existing manifest alone and exits 0, which makes it the advertised refresh
    command rather than a refusal.

    ``setup_flags`` is repeated into each command because a recovery that
    silently drops ``--ci`` or an agent-instruction selection completes with
    less than the caller asked for. ``detect`` passes none: it asked for no
    setup, so promising any here would be inventing it.
    """

    actions: list[NextAction] = []
    # The workspace root is never offered as a command: it is the scope this
    # run just refused, so running it again returns here. `.` stays in the
    # reported candidate list because agent files that belong to no
    # sub-project are real evidence of why the answer is unresolved, and
    # `--allow-unresolved-scope` is the route that accepts them.
    routable = [candidate for candidate in candidates if candidate.path != "."]
    for candidate in routable:
        target = workspace / candidate.path
        defines = ", ".join(candidate.agent_names)
        manifest = _adopted(target)
        if manifest is not None and not init_refreshes_existing:
            actions.append(
                NextAction(
                    kind="command",
                    command=render_command(
                        ["doctor", "--config", str(manifest), "--json"]
                    ),
                    why=(
                        f"{candidate.path} already carries a manifest"
                        + (f" and defines {defines}." if defines else ".")
                        + " Ask doctor what it still owes rather than "
                        "initializing over it."
                    ),
                    expects=(
                        "A doctor payload whose control state names the "
                        f"outstanding setup obligation for {candidate.path}, "
                        "or the release gate when there is none."
                    ),
                )
            )
            continue
        kit_flags = rebased_kit_flags(kit, source=workspace, target=target)
        if kit_flags is None:
            actions.append(
                NextAction(
                    kind="review",
                    why=(
                        f"{candidate.path} is a candidate, but the adoption kit "
                        f"at {kit} sits outside it and a kit path is resolved "
                        "under the workspace. Relocate the kit into the project "
                        "or drop --agent-instructions-kit before initializing "
                        "there."
                    ),
                    expects=f"An adoption kit reachable from {candidate.path}.",
                )
            )
            continue
        actions.append(
            NextAction(
                kind="command",
                command=render_command(
                    [
                        "init",
                        "--workspace",
                        str(target),
                        "--write",
                        *setup_flags,
                        *kit_flags,
                        "--json",
                    ]
                ),
                why=(
                    f"Initialize only {candidate.path}"
                    + (f", which defines {defines}." if defines else ".")
                ),
                expects=f"shipgate.yaml is created in {candidate.path}.",
            )
        )
    return actions


__all__ = [
    "MANIFEST_NAME",
    "MAX_LISTED_SCOPE_CANDIDATES",
    "rebased_kit_flags",
    "scope_candidate_actions",
]
