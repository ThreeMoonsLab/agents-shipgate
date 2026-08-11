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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from agents_shipgate.cli.diagnostics import ranked_diagnostics
from agents_shipgate.cli.discovery.placeholders import human_owned_placeholders
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_control_envelope import envelope_from_setup
from agents_shipgate.schemas.agent_control import (
    AgentActionKind,
    AgentControl,
    AgentControlAction,
    CodingAgentCommandAction,
    CodingAgentEditAction,
    HumanControlAction,
)
from agents_shipgate.schemas.agent_control_envelope import (
    AgentControlEnvelope,
    AgentControlExecution,
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
) -> str:
    """Content-address the subject this setup answer was computed against.

    ``check`` binds its authority to an ``audit_id`` and ``verify`` to a
    ``request_id`` for the same reason: an answer that cannot name its own
    subject is one a reader has no way to check, and two unrelated workspaces
    would otherwise produce byte-identical envelopes.

    The manifest bytes are folded in when one exists, so editing
    ``shipgate.yaml`` changes the identity of the answer about it — which is
    precisely the event after which a cached setup route must not be reused.
    """

    digest = hashlib.sha256()
    digest.update(operation.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(workspace).encode("utf-8"))
    digest.update(b"\0")
    if manifest_path is not None:
        digest.update(str(manifest_path).encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(manifest_path.read_bytes())
        except OSError:
            # An unreadable manifest is a real state (`doctor` reports it), and
            # it is *not* the same state as an absent one. Mark it rather than
            # silently hashing to the no-manifest identity.
            digest.update(b"<unreadable>")
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
    placeholders: Sequence[Mapping[str, object]] | None = None,
    manifest_display_path: str | None = None,
    execution: AgentControlExecution = "succeeded",
    exit_code: int | None = None,
) -> AgentControlEnvelope:
    """Project one setup command's already-published facts onto the envelope.

    ``diagnostics`` is exactly what the command puts in its ``diagnostics[]``
    field, and ``advance`` is the step it already names when nothing is wrong —
    so ``control.next_action`` and ``next_actions[0]`` describe the same work by
    construction rather than by two authors agreeing.

    Precedence, most urgent first:

    1. a ``block``-severity diagnostic — something is wrong *now*;
    2. an unresolved human-owned placeholder — the manifest is loadable but a
       person still owes it a declaration, and no command may be offered that
       would carry that unfilled value into a release decision (#325);
    3. any remaining diagnostic, in severity order;
    4. ``advance`` — the next stage of the adoption walk.

    Blocking diagnostics outrank the placeholder obligation on purpose: a
    manifest the loader rejects has to be repaired before anyone can usefully
    review what it declares, and the obligation is not lost — the next run
    surfaces it, because it is derived from the manifest rather than remembered.

    ``advance_decision`` is the caller's own statement about whether that onward
    step *finishes* setup or merely continues it. ``detect`` pointing at ``init``
    has not configured anything yet; ``doctor`` pointing at ``verify`` has. It is
    not inferred from the command string, because that would be a second reading
    of a fact the caller already holds.
    """

    ordered = ranked_diagnostics(list(diagnostics))
    pending_human = human_owned_placeholders(placeholders)

    blocking = next((diag for diag in ordered if diag.severity == "block"), None)
    if blocking is not None:
        return _from_diagnostic(
            blocking,
            operation=operation,
            input_id=input_id,
            reason=reason,
            execution=execution,
            exit_code=exit_code,
            alternatives=ordered,
        )
    if pending_human:
        return _emit(
            _human_route(_placeholder_review_why(pending_human, manifest_display_path)),
            operation=operation,
            decision=SETUP_INCOMPLETE,
            input_id=input_id,
            execution=execution,
            exit_code=exit_code,
        )
    if ordered:
        return _from_diagnostic(
            ordered[0],
            operation=operation,
            input_id=input_id,
            reason=reason,
            execution=execution,
            exit_code=exit_code,
            alternatives=ordered,
        )
    if advance is None:
        # No obligation and no onward step is not "done": setup never completes
        # a task, so the honest answer is that a person decides what happens
        # next. Synthesizing a plausible command here is how a routing surface
        # starts inventing work.
        return _emit(
            _human_route(reason),
            operation=operation,
            decision=advance_decision,
            input_id=input_id,
            execution=execution,
            exit_code=exit_code,
        )
    return _emit(
        derive_agent_control(
            reason=reason,
            next_action=_agent_route(advance, advance_kind),
            verify_required=advance_kind == "verify",
            allowed_next_commands=_commands(advance),
        ),
        operation=operation,
        decision=advance_decision,
        input_id=input_id,
        execution=execution,
        exit_code=exit_code,
    )


def _from_diagnostic(
    diagnostic: Diagnostic,
    *,
    operation: SetupOperation,
    input_id: str,
    reason: str,
    execution: AgentControlExecution,
    exit_code: int | None,
    alternatives: Sequence[Diagnostic],
) -> AgentControlEnvelope:
    action = diagnostic.next_actions[0]
    why = action.why
    if diagnostic.id in HUMAN_OWNED_SETUP_DIAGNOSTICS or action.kind in {"review", "stop"}:
        control = _human_route(why, stop=action.kind == "stop")
        decision = SETUP_NOT_APPLICABLE if action.kind == "stop" else SETUP_INCOMPLETE
        return _emit(
            control,
            operation=operation,
            decision=decision,
            input_id=input_id,
            execution=execution,
            exit_code=exit_code,
        )
    kind = SETUP_ACTION_KINDS.get(diagnostic.id, "configure")
    # Alternatives are the *other* diagnostics' rank-1 commands, so an agent
    # that cannot take the primary route still sees what else this run found.
    # Only commands: `allowed_next_commands` is a list of runnable strings, and
    # an edit path put in it would be handed to a shell.
    commands = [
        command
        for other in alternatives
        if other is not diagnostic
        for command in _commands(other.next_actions[0])
    ]
    return _emit(
        derive_agent_control(
            reason=reason,
            next_action=_agent_route(action, kind),
            verify_required=kind == "verify",
            # Deduplicated here rather than left to the union's uniqueness
            # validator to reject: two diagnostics naming the same rerun is
            # ordinary, not a malformed control object.
            allowed_next_commands=list(dict.fromkeys([*_commands(action), *commands])),
        ),
        operation=operation,
        decision=SETUP_INCOMPLETE,
        input_id=input_id,
        execution=execution,
        exit_code=exit_code,
    )


def _agent_route(action: NextAction, kind: AgentActionKind) -> AgentControlAction:
    """Type the command's own rank-1 step as a coding-agent control action."""

    if action.kind == "command" and action.command:
        return CodingAgentCommandAction(kind=kind, command=action.command, why=action.why)
    if action.kind == "edit" and action.path:
        return CodingAgentEditAction(
            kind="edit",
            path=action.path,
            # ``expects`` is required on the control action and optional on the
            # diagnostic. Falling back to ``why`` reuses what the author wrote
            # rather than inventing an acceptance criterion they did not state.
            expects=action.expects or action.why,
            why=action.why,
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


def _placeholder_review_why(
    entries: Sequence[Mapping[str, object]],
    manifest_display_path: str | None,
) -> str:
    """Name the exact fields and lines a person has to fill in.

    Exact locations rather than a count: the point of routing this to a human is
    that they can act on it without reading the manifest to find out what is
    being asked, and #325 requires the paths and source lines explicitly.
    """

    manifest = manifest_display_path or "shipgate.yaml"
    located = ", ".join(
        f"{manifest}:{entry['line']} ({_field_path(entry)})"
        if entry.get("line") is not None
        else f"{manifest} ({_field_path(entry)})"
        for entry in entries
    )
    return (
        f"{located} must be supplied by a human. These fields declare what this "
        "agent is for and what it is permitted to do; Shipgate never invents "
        "them, and a value a coding agent supplied is a declaration nobody made."
    )


def _field_path(entry: Mapping[str, object]) -> str:
    """The manifest field a placeholder sits in, without the list-item artifact.

    ``collect_placeholders`` names a sequence item by its own text, so a
    placeholder under ``declared_purpose: [CHANGE_ME]`` arrives as
    ``agent.declared_purpose.CHANGE_ME``. Showing that to a person names a field
    the manifest does not have; the trailing segment is dropped only when it is
    literally the placeholder value, so a real field named after it is untouched.
    """

    path = str(entry.get("path", "") or "<root>")
    current = str(entry.get("current", "") or "")
    suffix = f".{current}"
    if current and path.endswith(suffix):
        return path[: -len(suffix)] or "<root>"
    return path


def _commands(action: NextAction) -> list[str]:
    return [action.command] if action.kind == "command" and action.command else []


def _emit(
    control: AgentControl,
    *,
    operation: SetupOperation,
    decision: str,
    input_id: str,
    execution: AgentControlExecution,
    exit_code: int | None,
) -> AgentControlEnvelope:
    return envelope_from_setup(
        control,
        operation=operation,
        decision=decision,
        input_id=input_id,
        execution=execution,
        exit_code=exit_code,
    )


__all__ = [
    "HUMAN_OWNED_SETUP_DIAGNOSTICS",
    "SETUP_ACTION_KINDS",
    "SETUP_COMPLETE",
    "SETUP_INCOMPLETE",
    "SETUP_NOT_APPLICABLE",
    "SetupOperation",
    "setup_control_envelope",
    "setup_input_id",
]
