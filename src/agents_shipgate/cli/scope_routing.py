"""The commands that carry out a scope decision, for every command that reports one.

``init --write`` refuses when a workspace holds several self-contained projects
that define agents, and ``detect`` reports the same fact one step earlier. Both
then owe the caller the same thing: for every candidate, the step that advances
*that* project, so the decision "which project is this change about?" is the
only work left to do.

Which step that is depends on the candidate, and this module is where that is
decided once for both commands:

* no manifest — ``init --write``, carrying the setup flags the caller asked for;
* a manifest already — ``doctor``, because ``init --write`` refuses a file it
  will not overwrite; or an ``init`` *without* ``--write`` when the caller
  asked for setup that manifest does not supply;
* the workspace root — a human route and no command, because ``init`` there is
  the run that just refused and ``--allow-unresolved-scope`` adopts the whole
  workspace rather than that one agent.

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

from agents_shipgate.cli.control_pack_routing import (
    control_pack_route,
    manifest_control_pack_at,
    unapplied_control_pack,
)
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


def rebased_kit_flags(kit: Path | None, *, source: Path, target: Path) -> list[str] | None:
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


def is_adopted(directory: Path, *, manifest_name: str = MANIFEST_NAME) -> Path | None:
    """The manifest already in ``directory``, when one is there to route to.

    A symlinked manifest is treated as absent: the loader refuses a manifest
    path with symlink components, so routing ``doctor`` at one would publish a
    command that exits 2 (#363 review). The ``init`` route is emitted instead,
    and reports the real problem.
    """

    candidate = directory / manifest_name
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
    except OSError:  # pragma: no cover - unreadable directory
        return None
    return candidate


def describe_candidate(candidate: AgentProjectCandidate, *, workspace: Path) -> str:
    """One candidate as a line a human can choose from.

    Not every project names its agent in a string literal — a config-driven
    ``LlmAgent(name=CONFIG.agent_name)`` has none to parse — so the marker
    that made the directory a project stands in for it rather than leaving
    an empty pair of brackets.

    Two candidates say more than their name, because the routing published
    beside this list sends them somewhere other than the ``init --write`` the
    surrounding prose recommends: one that already carries a manifest goes to
    ``doctor``, and the workspace root goes to a human, since ``init`` there is
    the run that just refused. Both would otherwise read as ordinary entries in
    a list captioned "re-run init on the one you are changing" (#397 review).

    Shared rather than written once per command — ``detect`` and ``init`` each
    print this list, and a second copy is how one of them kept saying ``init``
    after the other stopped. ``workspace`` is required rather than defaulted
    for the same reason: an optional one would let a third surface print this
    list unmarked, and be right back to a human summary that contradicts the
    routes beside it.
    """

    detail = ", ".join(candidate.agent_names) or (candidate.marker or "project root")
    if candidate.path == ".":
        return f"{candidate.path} ({detail}) — the workspace itself, not a project"
    adopted = is_adopted(workspace / candidate.path) is not None
    return f"{candidate.path} ({detail})" + (" — already adopted" if adopted else "")


def candidate_caveats(workspace: Path, candidates: Sequence[AgentProjectCandidate]) -> list[str]:
    """What the "re-run init on one of these" line does not cover.

    Emitted only for the candidates actually present, so neither line becomes
    boilerplate. `detect` prints the same pair from the same helper — a printed
    list that reads uniformly while the routing beside it splits three ways is
    the divergence this is here to prevent (#397 review).

    Pass the candidates that are *printed*, not every candidate. These lines
    refer to the marking on the list they sit under ("a project marked already
    adopted"), so one raised by a candidate the display cap cut leaves a reader
    hunting a mark that is not on screen.
    """

    lines: list[str] = []
    if any(
        candidate.path != "." and is_adopted(workspace / candidate.path) is not None
        for candidate in candidates
    ):
        lines.append(
            "A project marked already adopted has a manifest init will not "
            "overwrite; ask doctor --config <that manifest> what it still "
            "owes instead."
        )
    if any(candidate.path == "." for candidate in candidates):
        lines.append(
            "The workspace itself is listed because agent files there belong "
            "to no project — which is why this scope is unresolved. Give them "
            "a project directory, or accept the whole workspace as one scope "
            "with --allow-unresolved-scope; re-running init at the root "
            "returns this refusal."
        )
    return lines


def scope_candidate_actions(
    workspace: Path,
    candidates: Sequence[AgentProjectCandidate],
    *,
    setup_flags: Sequence[str] = (),
    adopted_setup_flags: Sequence[str] = (),
    kit: Path | None = None,
    init_refreshes_existing: bool = False,
    requested_control_pack: str | None = None,
    manifest_name: str = MANIFEST_NAME,
    alternate_manifest_name: str | None = None,
    write_flag: str = "--write",
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

    ``adopted_setup_flags`` keeps the flag-preservation contract on that route.
    Setup the caller asked for does not stop being owed because the manifest
    already exists — a refused ``init --write --ci`` writes no workflow, and
    handing back a bare ``doctor`` dropped the request silently. These are the
    requested flags that still do their work with ``--write`` omitted, so the
    manifest is left alone and the setup is installed in one command that exits
    0; the caller decides which qualify, because only it knows which of its
    flags need a write (#397 review).

    ``.`` is a candidate the caller can pick and Shipgate cannot route: agent
    files that belong to no project are why the scope is unresolved in the
    first place, and neither ``init`` at the root (which returns this refusal)
    nor ``--allow-unresolved-scope`` (which accepts the *whole workspace* as one
    scope, a different decision) is a route for that one project. It gets an
    explicit human route saying so, rather than being listed as selectable and
    silently left out.

    ``setup_flags`` is repeated into each command because a recovery that
    silently drops ``--ci`` or an agent-instruction selection completes with
    less than the caller asked for. ``detect`` passes none: it asked for no
    setup, so promising any here would be inventing it.

    ``requested_control_pack`` is the same obligation one level down. An
    adopted candidate's manifest selects a pack, and this run may have asked
    for a different one — so handing back a bare ``doctor`` sends the caller
    to a command that reads that manifest, finds nothing wrong, and advances
    to the gate under the pack that is *there*. The request is gone and
    nothing said so. When the two differ, the route for that candidate is the
    reconciliation instead, owned by whoever the *direction* says owns it (see
    :mod:`agents_shipgate.cli.control_pack_routing`). ``detect`` passes none,
    for the same reason it passes no setup flags.
    """

    actions: list[NextAction] = []
    root = next((c for c in candidates if c.path == "."), None)
    if root is not None:
        defines = ", ".join(root.agent_names)
        actions.append(
            NextAction(
                kind="review",
                why=(
                    "The workspace root is a candidate"
                    + (f" because it defines {defines}" if defines else "")
                    + ", and it is the one candidate with no command: agent "
                    "files that belong to no project are why this scope is "
                    "unresolved. Give them a project directory of their own, "
                    "or accept the workspace as a single scope with "
                    "--allow-unresolved-scope — which adopts every project "
                    "under it, not this agent alone."
                ),
                expects=(
                    "Either the root agent files moved into a project "
                    "directory, or a deliberate whole-workspace adoption."
                ),
            )
        )
    # The workspace root is never offered as a *command*: `init` there is the
    # run that just refused, so it returns straight here.
    routable = [candidate for candidate in candidates if candidate.path != "."]
    for candidate in routable:
        target = workspace / candidate.path
        defines = ", ".join(candidate.agent_names)
        manifest = is_adopted(target, manifest_name=manifest_name)
        if manifest is None and alternate_manifest_name is not None:
            manifest = is_adopted(target, manifest_name=alternate_manifest_name)
        if manifest is not None and not init_refreshes_existing:
            candidate_pack = manifest_control_pack_at(manifest)
            if (
                requested_control_pack is not None
                and candidate_pack is not None
                and unapplied_control_pack(requested=requested_control_pack, on_disk=candidate_pack)
            ):
                # Reconcile before handing off, exactly as the workspace's own
                # `init` route does. Ranked here, among the candidate commands,
                # because it belongs to this candidate rather than to the
                # decision above them.
                #
                # Both operands are re-tested rather than asserted: `assert` is
                # stripped under `-O`, and what it would be narrowing here is
                # the difference between naming a pack and printing "None".
                actions.append(
                    control_pack_route(
                        manifest=manifest,
                        requested=requested_control_pack,
                        on_disk=candidate_pack,
                        display_path=candidate.path,
                    )
                )
                continue
            if adopted_setup_flags:
                # The manifest is not the outstanding work here; the setup the
                # caller asked for is. Without `--write` these flags do their
                # own work and the existing manifest is never touched, so this
                # exits 0 where `init --write` would refuse it.
                actions.append(
                    NextAction(
                        kind="command",
                        command=render_command(
                            [
                                "init",
                                "--workspace",
                                str(target),
                                *adopted_setup_flags,
                                "--json",
                            ]
                        ),
                        why=(
                            f"{candidate.path} already carries a manifest"
                            + (f" and defines {defines}," if defines else ",")
                            + " so this installs the setup you asked for and "
                            "leaves the manifest alone. Ask doctor what that "
                            "manifest still owes afterwards."
                        ),
                        expects=(
                            f"The requested setup present in {candidate.path} "
                            "with its manifest unchanged."
                        ),
                    )
                )
                continue
            actions.append(
                NextAction(
                    kind="command",
                    command=render_command(["doctor", "--config", str(manifest), "--json"]),
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
                        write_flag,
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
    "candidate_caveats",
    "describe_candidate",
    "MAX_LISTED_SCOPE_CANDIDATES",
    "is_adopted",
    "rebased_kit_flags",
    "scope_candidate_actions",
]
