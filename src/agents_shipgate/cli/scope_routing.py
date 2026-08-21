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

# A monorepo can hold hundreds of agent projects. A refusal, and the routing
# beside it, list enough of them to choose from and point at the JSON payload
# for the rest.
MAX_LISTED_SCOPE_CANDIDATES = 10


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


def scope_candidate_actions(
    workspace: Path,
    candidates: Sequence[AgentProjectCandidate],
    *,
    setup_flags: Sequence[str] = (),
    kit: Path | None = None,
) -> list[NextAction]:
    """One ``init`` command per candidate project, in the caller's order.

    These are ranked *below* the decision that selects among them — promoting
    one would make the same arbitrary pick the refusal exists to prevent — so
    this returns only the commands and leaves rank 1 to the caller.

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
    for candidate in routable[:MAX_LISTED_SCOPE_CANDIDATES]:
        target = workspace / candidate.path
        defines = ", ".join(candidate.agent_names)
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
    "MAX_LISTED_SCOPE_CANDIDATES",
    "rebased_kit_flags",
    "scope_candidate_actions",
]
