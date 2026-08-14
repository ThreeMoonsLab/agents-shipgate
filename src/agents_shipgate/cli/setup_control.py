"""The single projection from *setup facts* onto ``shipgate.agent_control/v1``.

``detect``, ``init``, and ``doctor`` run before a release decision can exist, so
their control state cannot be a projection of one. It is derived instead from
the facts those commands already publish — detection confidence, manifest
validity, unresolved tool sources, unresolved placeholders — and it says so:
every envelope built here carries ``decision_source: "setup"``.

Three properties make that legitimate rather than a second gate:

* **It routes; it never decides a release.** Whenever a diagnostic fired, the
  rank-1 control action *is* that diagnostic's own rank-1 ``NextAction``, so
  ``control.next_action`` and ``next_actions[0]`` cannot describe different
  work. Nothing new is computed about the repository here, so there is no second
  verdict to disagree with ``release_decision.decision`` — see
  :mod:`agents_shipgate.core.agent_control_envelope`. The only route this module
  does not take from a diagnostic is the onward stage a *clean* run hands off
  to, which the caller supplies as ``advance``; ``doctor`` has no diagnostic to
  carry it because a manifest with nothing wrong produces none.
* **It authorizes nothing.** Setup reads no diff, so no setup envelope can grant
  edit, commit, push, update_pr, merge, or report_complete. ``complete`` is
  unreachable for these operations in the published schema, which is why "run
  `init` and report done" is not a shape a producer can emit.
* **It cannot be mistaken for the gate.** ``decision`` uses the closed
  ``SETUP_DECISIONS`` vocabulary and ``decision_source`` names the engine, in
  both directions, enforced by the envelope schema.

One thing *is* decided here, and deliberately: **who owns the next step.** A
manifest the loader rejected is coding-agent work; an unresolved
``agent.declared_purpose`` is not, and publishing it as an agent-executable edit
would invite exactly the invented semantic declaration ``do_not_auto_assert``
exists to prevent (#325).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_shipgate.cli.diagnostics import ranked_diagnostics
from agents_shipgate.cli.discovery.placeholders import human_owned_placeholders
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_control_envelope import envelope_from_setup
from agents_shipgate.invocation import split_invocation
from agents_shipgate.schemas.agent_control import (
    AgentActionKind,
    AgentControl,
    AgentControlAction,
    CodingAgentCommandAction,
    HumanControlAction,
)
from agents_shipgate.schemas.agent_control_envelope import (
    MAX_ENVELOPE_PROSE_BYTES,
    AgentControlEnvelope,
    AgentControlExecution,
    SetupEditAction,
)
from agents_shipgate.schemas.diagnostics import (
    DIAG_CHANGE_ME_PLACEHOLDERS,
    DIAG_CODEX_PLUGIN_PACKAGE_DETECTED,
    DIAG_DYNAMIC_TOOLSETS_ONLY,
    DIAG_INVALID_MANIFEST,
    DIAG_MCP_OPENAPI_ARTIFACT_ONLY,
    DIAG_MISSING_MANIFEST,
    DIAG_MISSING_SOURCE_FILE,
    DIAG_NO_AGENT_SURFACE,
    DIAG_NO_PRODUCTION_PERMISSIONS,
    DIAG_NON_AGENT_LIBRARY,
    DIAG_PURE_PROMPT_EXPERIMENT,
    DIAG_UNKNOWN_ADAPTER_SOURCE_TYPE,
    DIAG_ZERO_TOOLS,
    Diagnostic,
    NextAction,
)

SetupOperation = Literal["detect", "init", "doctor"]


@dataclass(frozen=True)
class SetupRouting:
    """One selected route, projected onto both surfaces a setup command publishes.

    ``actions`` is the ranked list for ``next_actions[]``, and ``actions[0]`` is
    the same route ``envelope.next_action`` carries. They are returned together
    because a caller that could take one without the other is a caller that can
    publish two rank-one answers.
    """

    envelope: AgentControlEnvelope
    actions: list[NextAction]

    @property
    def legacy_next_action(self) -> str:
        """The back-compat single-string field, from the same selected route."""

        return self.actions[0].to_legacy_string()

    def json_actions(self) -> list[dict[str, object]]:
        return [action.model_dump(mode="json") for action in self.actions]

# Which coding-agent action kind each diagnostic's route is, when that route is
# an executable command. Kept as one table rather than derived from the command
# string: parsing `agents-shipgate verify --preview` back into "verify" is a
# second grammar for something the diagnostic author already knows, and it
# breaks the moment a diagnostic emits `pip install`.
#
# `tests/test_setup_control.py` pins this against `ALL_DIAGNOSTIC_IDS`, so a new
# diagnostic cannot be added without stating what kind of step it asks for.
SETUP_ACTION_KINDS: dict[str, AgentActionKind] = {
    DIAG_MISSING_MANIFEST: "verify",
    DIAG_INVALID_MANIFEST: "configure",
    DIAG_NO_AGENT_SURFACE: "discover",
    DIAG_NON_AGENT_LIBRARY: "discover",
    DIAG_PURE_PROMPT_EXPERIMENT: "discover",
    DIAG_MCP_OPENAPI_ARTIFACT_ONLY: "initialize",
    DIAG_CODEX_PLUGIN_PACKAGE_DETECTED: "initialize",
    DIAG_ZERO_TOOLS: "configure",
    DIAG_DYNAMIC_TOOLSETS_ONLY: "configure",
    DIAG_MISSING_SOURCE_FILE: "configure",
    DIAG_CHANGE_ME_PLACEHOLDERS: "configure",
    DIAG_NO_PRODUCTION_PERMISSIONS: "configure",
    DIAG_UNKNOWN_ADAPTER_SOURCE_TYPE: "install",
}

# Diagnostics whose remediation is a human decision even though the underlying
# change is a file edit. Declaring what an agent may do, and the policy that
# governs it, is not something the agent under governance gets to write.
HUMAN_OWNED_SETUP_DIAGNOSTICS = frozenset({DIAG_NO_PRODUCTION_PERMISSIONS})

# Setup verdicts, re-exported so callers do not have to reach into the schema
# module for the two they use.
SETUP_COMPLETE = "setup_complete"
SETUP_INCOMPLETE = "setup_incomplete"
SETUP_NOT_APPLICABLE = "setup_not_applicable"


def setup_input_id(
    *,
    operation: SetupOperation,
    workspace: Path,
    manifest_path: Path | None = None,
    manifest_bytes: bytes | None = None,
    routing_facts: object = None,
) -> str:
    """Content-address the subject this setup answer was computed against.

    ``check`` binds its authority to an ``audit_id`` and ``verify`` to a
    ``request_id`` for the same reason: an answer that cannot name its own
    subject is one a reader has no way to check.

    ``routing_facts`` is the load-bearing argument. Hashing only the operation,
    the workspace path, and the manifest bytes covered none of what actually
    selects the route: adding a real agent moved ``detect`` from
    ``setup_not_applicable`` to ``setup_incomplete`` under an *identical*
    ``input_id``, and deleting a declared source moved ``doctor`` from a verify
    handoff to an edit under another. An identity that does not change when the
    answer changes is worse than no identity, because it invites exactly the
    cached reuse the field exists to prevent. Callers pass the facts they routed
    on — the detection result, the inspection payload, the placeholder set.

    ``manifest_bytes`` lets a caller fold in the *same* bytes it inspected. The
    alternative — reopening the file here — is a second read of something the
    caller may have already acted on, so an edit landing between the two would
    produce an id for a manifest state no answer was ever computed from.
    """

    digest = hashlib.sha256()

    def absorb(value: object) -> None:
        digest.update(
            json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
        )
        digest.update(b"\0")

    absorb(operation)
    absorb(str(workspace))
    absorb(str(manifest_path) if manifest_path is not None else None)
    if manifest_bytes is not None:
        digest.update(hashlib.sha256(manifest_bytes).digest())
    elif manifest_path is not None:
        try:
            digest.update(hashlib.sha256(manifest_path.read_bytes()).digest())
        except OSError:
            # An unreadable manifest is a real state (`doctor` reports it), and
            # it is *not* the same state as an absent one. Mark it rather than
            # silently hashing to the no-manifest identity.
            digest.update(b"<unreadable>")
    digest.update(b"\0")
    absorb(routing_facts)
    return f"sha256:{digest.hexdigest()}"


def setup_control_envelope(
    *,
    operation: SetupOperation,
    input_id: str,
    reason: str,
    diagnostics: Sequence[Diagnostic] = (),
    advance: NextAction | None = None,
    advance_kind: AgentActionKind = "verify",
    advance_decision: str = SETUP_COMPLETE,
    advance_blocking: bool = False,
    advance_alternatives: Sequence[NextAction] = (),
    recheck_command: str | None = None,
    placeholders: Sequence[Mapping[str, object]] | None = None,
    manifest_display_path: str | None = None,
    execution: AgentControlExecution = "succeeded",
    exit_code: int | None = None,
) -> SetupRouting:
    """Select one route for a setup command, and project it onto both surfaces.

    Returns the envelope *and* the ranked ``NextAction`` list the command
    publishes, both derived from one selected route. That is the point of
    returning a pair rather than an envelope: adding ``control`` beside an
    independently-computed ``next_action`` left ``init --minimal --write --json``
    publishing an executable ``scan`` command at the top level while its control
    envelope said ``human_review_required`` and offered no command at all. Two
    rank-one answers in one document is worse than the single ambiguous one it
    replaced, because the unsafe route is the one a pre-#323 consumer reads.

    ``diagnostics`` is exactly what the command puts in its ``diagnostics[]``
    field, and ``advance`` is the step it already names when nothing is wrong.

    Precedence, most urgent first:

    1. a ``block``-severity diagnostic — something is wrong *now*;
    2. ``advance`` when ``advance_blocking`` — an obligation this run produced,
       such as a target ``init`` refused to overwrite;
    3. an unresolved human-owned placeholder — the manifest is loadable but a
       person still owes it a declaration, and no command may be offered that
       would carry that unfilled value into a release decision (#325);
    4. any remaining diagnostic, in severity order;
    5. ``advance`` — the next stage of the adoption walk.

    Blocking diagnostics outrank the placeholder obligation on purpose: a
    manifest the loader rejects has to be repaired before anyone can usefully
    review what it declares, and the obligation is not lost — the next run
    surfaces it, because it is derived from the manifest rather than remembered.

    ``recheck_command`` is the command that confirms a file edit this command
    routes to — ``doctor`` for a manifest, and required whenever a diagnostic's
    rank-1 action is an ``edit``.

    ``advance_decision`` is the caller's own statement about whether that onward
    step *finishes* setup or merely continues it. ``detect`` pointing at ``init``
    has not configured anything yet; ``doctor`` pointing at ``verify`` has. It is
    not inferred from the command string, because that would be a second reading
    of a fact the caller already holds.
    """

    ordered = ranked_diagnostics(list(diagnostics))
    pending_human = human_owned_placeholders(placeholders)
    alternatives = [diag.next_actions[0] for diag in ordered]

    blocking = next((diag for diag in ordered if diag.severity == "block"), None)
    if blocking is not None:
        selected, kind, decision = _route_for(blocking)
    elif advance_blocking and advance is not None:
        # An obligation this run itself produced — a target `init` refused to
        # overwrite — rather than a standing one. Same tier as a blocking
        # diagnostic, and for the same reason: it is agent-owned, it does not
        # depend on the human declaration, and routing past it would report a
        # non-zero exit with no explanation of what failed. The declaration is
        # not skipped, only deferred: it is derived from the manifest on every
        # run, so the next one surfaces it.
        selected, kind, decision = advance, advance_kind, advance_decision
    elif pending_human:
        selected = NextAction(
            kind="review",
            why=_placeholder_review_why(pending_human, manifest_display_path),
            expects="Every human-owned field above holds a value a person supplied.",
        )
        kind, decision = "configure", SETUP_INCOMPLETE
    elif ordered:
        selected, kind, decision = _route_for(ordered[0])
    elif advance is not None:
        selected, kind, decision = advance, advance_kind, advance_decision
    else:
        # No obligation and no onward step is not "done": setup never completes
        # a task, so the honest answer is that a person decides what happens
        # next. Synthesizing a plausible command here is how a routing surface
        # starts inventing work.
        selected = NextAction(kind="review", why=reason)
        kind, decision = "configure", advance_decision

    setup_edit: SetupEditAction | None = None
    unrunnable = _unrunnable_command(selected)
    if unrunnable is not None:
        # The ranked list keeps the diagnostic's own action. `NextAction`
        # already handles a string with no faithful argv the honest way — it
        # withholds the computed `executable`/`args` pair and lets the rendered
        # string carry the instruction (#322) — so nothing there is claiming
        # more than it can deliver. The envelope has no such escape hatch:
        # `next_action.command` *is* the step, and there is no way to publish a
        # command while withholding the fact that it runs. So the control route
        # becomes the human one, carrying the same string as prose.
        actions = [selected, *(item for item in alternatives if item is not selected)][:3]
        control: AgentControl = _human_route(
            f"{selected.why} The remediation is not a runnable command as "
            f"written: {unrunnable}"
        )
    elif selected.kind in {"review", "stop"}:
        # A human route drops the *diagnostic* alternatives: re-offering the
        # very obligation being routed to a person as an agent-executable edit,
        # one position down the list, is the bypass this precedence exists to
        # close.
        #
        # ``advance_alternatives`` is the caller's own ranked list and is kept,
        # because those are not ways around the decision — they are the ways of
        # carrying it out once it is made. `init`'s unresolved-scope refusal is
        # the case: rank 1 is "choose a project", and the per-candidate commands
        # below it are how the chosen one gets initialized. A caller that
        # supplies them has already ranked the decision above them.
        actions = [selected, *advance_alternatives]
        control: AgentControl = _human_route(selected.why, stop=selected.kind == "stop")
    else:
        actions = [selected, *(item for item in alternatives if item is not selected)][:3]
        route, setup_edit = _agent_route(selected, kind, recheck_command)
        control = derive_agent_control(
            reason=reason,
            next_action=route,
            verify_required=kind == "verify",
            # Deduplicated here rather than left to the union's uniqueness
            # validator to reject: two diagnostics naming the same rerun is
            # ordinary, not a malformed control object.
            allowed_next_commands=list(
                dict.fromkeys(command for item in actions for command in _commands(item))
            ),
        )
    return SetupRouting(
        envelope=_emit(
            control,
            operation=operation,
            decision=decision,
            input_id=input_id,
            execution=execution,
            exit_code=exit_code,
            setup_edit=setup_edit,
        ),
        actions=actions,
    )


def _route_for(diagnostic: Diagnostic) -> tuple[NextAction, AgentActionKind, str]:
    """The one route this diagnostic asks for, with its owner already applied.

    A human-owned diagnostic's remediation is a file edit, and publishing it as
    an ``edit`` action would tell the governed agent to write the declaration.
    It is restated as a ``review`` so that *both* the legacy field and the
    control envelope carry the human route; converting only the envelope would
    have left the executable one in ``next_actions[0]``.
    """

    action = diagnostic.next_actions[0]
    if diagnostic.id in HUMAN_OWNED_SETUP_DIAGNOSTICS and action.kind != "stop":
        location = f" ({action.path})" if action.path else ""
        action = NextAction(
            kind="review",
            why=f"{action.why}{location} This is a declaration a person makes.",
            expects=action.expects,
        )
    decision = SETUP_NOT_APPLICABLE if action.kind == "stop" else SETUP_INCOMPLETE
    return action, SETUP_ACTION_KINDS.get(diagnostic.id, "configure"), decision


def _unrunnable_command(action: NextAction) -> str | None:
    """The command this action names, when nothing can actually run it.

    A diagnostic's ``command`` is an instruction for a reader as often as it is
    an invocation: the unknown-adapter routes read
    ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan …`` — a shell
    assignment, which ``shlex.split`` turns into a program literally named
    ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1`` — and ``pip install
    <third-party-adapter-package>``, a placeholder nobody can install. Promoting
    either into ``control.next_action.command`` published an
    ``agent_action_required`` route whose single step cannot be taken, which is
    worse than publishing no command at all: on that contract the field *is* the
    step.

    ``split_invocation`` already answers this question for #322 using the same
    grammar the commands are rendered with, so this is one parser rather than a
    second guess at what looks runnable. It is also why the fix stops at the
    envelope: ``NextAction`` handles the same case by withholding its computed
    ``executable``/``args`` pair while keeping the string, and the envelope has
    no equivalent way to say "here is the instruction, but it is not argv".
    """

    if action.kind != "command" or not action.command:
        return None
    return None if split_invocation(action.command) is not None else action.command


def _agent_route(
    action: NextAction, kind: AgentActionKind, recheck_command: str | None
) -> tuple[AgentControlAction, SetupEditAction | None]:
    """Type the command's own rank-1 step, and say what the envelope publishes.

    Returns the control action *and*, for a file edit, the typed
    :class:`SetupEditAction` the envelope carries in its place.

    The two differ because the shared ``AgentControl`` union cannot hold an edit
    — six durable published schemas embed it — while the envelope, which is
    stdout-only, can. So the control holds the command that *checks* the edit,
    which is what fixes the state and permissions, and the envelope publishes
    the edit itself.

    Substituting the check for the edit outright was tried and is wrong: an
    envelope-only consumer executing ``control.next_action`` re-ran ``doctor``
    against an unchanged file and received the identical action back forever,
    with the instruction surviving only in ``why``. A route the contract calls
    executable has to be the step, not its postcondition.
    """

    if action.kind == "command" and action.command:
        return (
            CodingAgentCommandAction(kind=kind, command=action.command, why=action.why),
            None,
        )
    if action.kind == "edit" and action.path:
        if recheck_command is None:
            raise ValueError("an edit route needs the command that confirms it")
        expects = action.expects or f"{recheck_command} reports the file as resolved."
        return (
            CodingAgentCommandAction(
                kind=kind,
                command=recheck_command,
                why=f"Edit {action.path}. {action.why}",
            ),
            SetupEditAction(
                kind="edit",
                path=action.path,
                expects=expects,
                why=action.why,
            ),
        )
    # A ``review``/``stop`` action reaching here would already have been routed
    # to a human by the caller; anything else is a malformed diagnostic.
    raise ValueError(f"cannot type {action.kind!r} as a coding-agent control action")


def _human_route(why: str, *, stop: bool = False) -> AgentControl:
    return derive_agent_control(
        reason=why,
        next_action=HumanControlAction(kind="stop" if stop else "review", why=why),
        human_review_required=True,
        human_review_why=why,
        stop_reason=why,
    )


_PLACEHOLDER_REVIEW_TAIL = (
    " must be supplied by a human. These fields declare what this agent is for "
    "and what it is permitted to do; Shipgate never invents them, and a value a "
    "coding agent supplied is a declaration nobody made."
)


def _placeholder_review_why(
    entries: Sequence[Mapping[str, object]],
    manifest_display_path: str | None,
) -> str:
    """Name the exact fields and lines a person has to fill in.

    Exact locations rather than a count: the point of routing this to a human is
    that they can act on it without reading the manifest to find out what is
    being asked, and #325 requires the paths and source lines explicitly.

    The list is fitted to the envelope's prose budget rather than written out in
    full and left to be cut. ``truncate_prose`` caps this field at
    ``MAX_ENVELOPE_PROSE_BYTES``, so a manifest with a dozen unresolved
    declarations — or one very long field path — silently lost the later
    locations and could lose the sentence explaining what they were. What is
    dropped here is dropped *visibly*, with a count and a pointer at the
    ``placeholders[]`` field of the same payload.

    That pointer is only honest if the array is there and describes the same
    manifest: ``doctor`` did not publish it at all, and ``init`` published the
    generated template's list rather than the manifest it routed on. Both now
    publish the locations of the manifest this route was selected from.
    """

    def size(text: str) -> int:
        return len(text.encode("utf-8"))

    budget = MAX_ENVELOPE_PROSE_BYTES - size(_PLACEHOLDER_REVIEW_TAIL)
    manifest = manifest_display_path or "shipgate.yaml"
    # The manifest is named once and the lines refer to it, rather than repeated
    # per entry. Repeating it spent the whole budget on a deep absolute path
    # before the first line number was reached.
    prefix = f"In {manifest}: "
    if size(prefix) > budget // 2:
        # A path this long leaves no room for what it is a path to. The payload
        # carries the full spelling in its own `path` / `config` field, so the
        # short form here is a label, not the only copy.
        prefix = f"In {_basename(manifest)} (full path in this payload): "
    # ...and a "basename" can itself be unbounded. Whatever survives, the
    # location must: an unshortened prefix consumed the whole budget and the
    # 400-byte envelope cap then removed the line number and the field name from
    # the only human action, which is the one thing this sentence exists to say.
    if size(prefix) > budget // 2:
        prefix = "In the manifest at the path in this payload: "

    def render(entry: Mapping[str, object], allowance: int) -> str:
        line = entry.get("line")
        where = f"line {line}" if line is not None else "unlocated"
        # The location is what a person acts on, so it is never shortened; the
        # field name is context and is elided when the two together do not fit.
        # An entry that loses its own line has lost the whole point.
        field = _field_path(entry)
        ellipsis = "…"
        # What is left for the field name, after the " (" and ")" wrapper. The
        # ellipsis is counted in *bytes*: it is three of them, and reserving one
        # put a single very long path two bytes over the cap.
        room = allowance - size(where) - size(" ()")
        if room <= size(ellipsis):
            return where
        if size(field) > room:
            keep = room - size(ellipsis)
            field = field.encode("utf-8")[:keep].decode("utf-8", errors="ignore") + ellipsis
        return f"{where} ({field})"

    # Share what remains across the entries actually named, so one long field
    # path cannot spend what the others need.
    remaining_budget = budget - size(prefix)
    allowance = max(1, remaining_budget // min(max(len(entries), 1), 3))
    shown: list[str] = []
    used = 0
    for index, entry in enumerate(entries):
        candidate = render(entry, allowance)
        overflow = f", and {len(entries) - index} more in placeholders[]"
        cost = used + (2 if shown else 0) + size(candidate) + size(overflow)
        if shown and cost > remaining_budget:
            shown.append(overflow.removeprefix(", "))
            break
        used += (2 if shown else 0) + size(candidate)
        shown.append(candidate)
    return prefix + ", ".join(shown) + _PLACEHOLDER_REVIEW_TAIL


def _basename(path: str) -> str:
    """The last component of ``path`` under POSIX *or* Windows rules.

    ``PurePosixPath`` does not split on backslashes, so a Windows manifest path
    was one enormous "basename" and shortening it did nothing. The host running
    Shipgate is not necessarily the host the path came from — a manifest path
    reaches here from a payload, a config flag, or a repository checked out
    elsewhere — so both separators are honoured regardless of ``os.sep``.
    """

    return re.split(r"[\\/]", str(path))[-1] or str(path)


def _field_path(entry: Mapping[str, object]) -> str:
    """The manifest field a placeholder sits in.

    ``collect_placeholders`` builds this from the parsed document, so it already
    names a real field whatever spelling the author used.
    """

    return str(entry.get("path", "") or "<root>")


def _commands(action: NextAction) -> list[str]:
    # Same faithfulness test as the rank-1 route: `allowed_next_commands` is an
    # allow-list of things the agent may run, so a string with no argv form has
    # no business in it either.
    if action.kind != "command" or not action.command:
        return []
    return [action.command] if split_invocation(action.command) is not None else []


def _emit(
    control: AgentControl,
    *,
    operation: SetupOperation,
    decision: str,
    input_id: str,
    execution: AgentControlExecution,
    exit_code: int | None,
    setup_edit: SetupEditAction | None = None,
) -> AgentControlEnvelope:
    return envelope_from_setup(
        control,
        operation=operation,
        decision=decision,
        input_id=input_id,
        execution=execution,
        exit_code=exit_code,
        setup_edit=setup_edit,
    )


__all__ = [
    "HUMAN_OWNED_SETUP_DIAGNOSTICS",
    "SETUP_ACTION_KINDS",
    "SETUP_COMPLETE",
    "SETUP_INCOMPLETE",
    "SETUP_NOT_APPLICABLE",
    "SetupOperation",
    "SetupRouting",
    "setup_control_envelope",
    "setup_input_id",
]
