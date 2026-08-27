"""The single projection that produces ``shipgate.agent_control/v1``.

Every surface that emits the envelope comes through here — ``verify --format
control``, ``check --format agent-control-json``, ``agents-shipgate agent
control``, and the ``control`` field on ``detect``/``init``/``doctor``
``--json``. That is the point: an envelope assembled independently at each call
site would be a second control vocabulary within a month, which is the outcome
#333 and #323 were merged to prevent.

The setup commands are the one family whose control state is *not* a projection
of a release decision, because none exists when they run. They are not therefore
a second gate: they carry ``decision_source: "setup"``, they authorize nothing
at all, and the ``complete`` variant is unreachable for them in the published
schema. See :func:`envelope_from_setup`.

Nothing in this module decides anything. Every field is lifted from a producer
that already published it, and the two places where a choice looks like it is
being made are both refusals:

* When a current-control pointer *downgraded* the state its own run reported —
  the completion refusal in
  :func:`agents_shipgate.core.current_control.project_agent_control` — the
  pointer wins and the run's route is dropped. The pointer is the authority on
  what is current; a route computed under a decision that is no longer current
  is not a safer answer than no route.
* When no route can be recovered at all, the caller refuses rather than
  inventing one. A synthesized command that does not reproduce the subject is
  worse than an honest "re-run verification".
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.schemas.agent_control import (
    COMMANDLESS_CODING_AGENT_ACTIONS,
    AgentControl,
    AgentControlAction,
    CodingAgentCommandAction,
    CodingAgentFetchBaseAction,
    HumanControlAction,
    HumanReviewAction,
    HumanReviewRequiredControl,
    NoHumanReview,
    RequiredHumanReview,
)
from agents_shipgate.schemas.agent_control_envelope import (
    MAX_ENVELOPE_QUESTIONS,
    SETUP_DECISIONS,
    SETUP_OPERATIONS,
    AgentActionControlEnvelope,
    AgentControlArtifactRef,
    AgentControlDecisionSource,
    AgentControlEnvelope,
    AgentControlExecution,
    AgentControlOperation,
    AgentControlPendingReview,
    AgentControlSource,
    CompleteControlEnvelope,
    ConfirmDeclarationsAction,
    EnvelopeCodingAgentAction,
    EnvelopeDeclarationQuestion,
    HumanReviewRequiredControlEnvelope,
    ReviewPublishableControlEnvelope,
    SetupEditAction,
    truncate_prose,
)
from agents_shipgate.schemas.agent_result import AgentResultV2
from agents_shipgate.schemas.contract import CONTRACT_VERSION
from agents_shipgate.schemas.current_control import CurrentControlPointer
from agents_shipgate.schemas.verifier import VerifierArtifact


class AgentControlRouteUnavailable(ValueError):
    """No published route could be recovered for a refresh read."""


def project_agent_control_envelope(
    *,
    control: AgentControl,
    operation: AgentControlOperation,
    source: AgentControlSource,
    execution: AgentControlExecution,
    exit_code: int | None,
    decision: str | None,
    decision_source: AgentControlDecisionSource,
    input_id: str | None = None,
    pending_review: Sequence[AgentControlPendingReview] = (),
    current_control_id: str | None = None,
    artifacts: Mapping[str, AgentControlArtifactRef] | None = None,
    setup_edit: SetupEditAction | None = None,
    declaration_route: ConfirmDeclarationsAction | None = None,
) -> AgentControlEnvelope:
    """Project one authoritative control object onto the compact envelope.

    The only transformation applied to anything is the prose cap in
    :func:`truncate_prose`. Every other field is a copy — with one exception,
    fenced so that it cannot become a habit.

    ``setup_edit`` replaces the *action* on a setup route with the typed edit
    that action stands for. The shared union cannot carry an edit (see
    :class:`SetupEditAction`), so the underlying control holds the command that
    checks the edit, and the envelope publishes the edit itself. Nothing about
    authority moves: the state, the permission vector, and the human-review
    shape all still come from ``control``, and the substitution is refused
    outside a setup operation on an agent route.

    ``declaration_route`` is the same substitution one surface over, and for
    the same reason. Here the underlying control does hold a real command — the
    step *is* an ``apply-patches`` invocation — so what the envelope adds is the
    question list that command does not answer. The caller builds it only after
    checking that this control's route is that exact command; see
    :func:`envelope_from_verifier`. The two substitutions are mutually
    exclusive: one action is published, and a caller supplying both has not
    decided which route this is.
    """

    if setup_edit is not None and declaration_route is not None:
        raise ValueError("an envelope publishes one next action, not two")
    if setup_edit is not None and (
        operation not in SETUP_OPERATIONS or control.state != "agent_action_required"
    ):
        raise ValueError(
            "a typed edit route is only publishable on a setup operation's "
            "coding-agent route"
        )
    if declaration_route is not None and (
        operation != "verify" or control.state != "agent_action_required"
    ):
        raise ValueError(
            "a declaration confirmation is only publishable on verify's "
            "coding-agent route"
        )

    shared = {
        "contract_version": CONTRACT_VERSION,
        "operation": operation,
        "source": source,
        "execution": execution,
        "exit_code": exit_code,
        "decision": truncate_prose(decision) if decision else None,
        "decision_source": decision_source,
        "input_id": input_id,
        "pending_review": list(pending_review),
        "reason": truncate_prose(control.reason),
        "current_control_id": current_control_id,
        "artifacts": dict(artifacts or {}),
    }
    action = _bounded_action(setup_edit or declaration_route or control.next_action)
    review = _bounded_human_review(control.human_review)

    # One variant per control state, selected from the tag the union already
    # fixed. `permissions` is carried through verbatim on the two states that
    # admit more than one vector; the others pin it, so re-deriving anything
    # here is impossible by construction rather than by discipline.
    if control.state == "complete":
        return CompleteControlEnvelope(control_state="complete", **shared)  # noqa: E501
    if control.state == "agent_action_required":
        return AgentActionControlEnvelope(
            control_state="agent_action_required",
            permissions=control.permissions,
            verify_required=control.verify_required,
            next_action=action,
            **shared,
        )
    if control.state == "review_publishable":
        return ReviewPublishableControlEnvelope(
            control_state="review_publishable",
            permissions=control.permissions,
            verify_required=control.verify_required,
            next_action=action,
            human_review=review,
            **shared,
        )
    return HumanReviewRequiredControlEnvelope(
        control_state="human_review_required",
        verify_required=control.verify_required,
        next_action=action,
        human_review=review,
        **shared,
    )


def envelope_from_verifier(
    verifier: VerifierArtifact,
    *,
    operation: AgentControlOperation,
    source: AgentControlSource,
    exit_code: int | None,
    pointer: CurrentControlPointer | None = None,
    artifact_root: str | None = None,
) -> AgentControlEnvelope:
    """Project a verifier run, reconciled against the pointer it published.

    ``pointer`` supplies the content-addressed artifact set and the currency
    identity. It also has the last word on state: the pointer refuses to carry
    completion authority that its run could not bind a terminal receipt for, and
    that refusal must not be undone by reading the run's own optimistic control
    block instead.

    ``artifact_root`` is the reports directory as the *caller* would spell it.
    The pointer records artifact paths relative to itself, which is unambiguous
    inside that directory and useless on stdout.
    """

    control = _reconcile_with_pointer(verifier.control, pointer)
    release = verifier.release_decision
    decision = release.decision if release is not None else None
    return project_agent_control_envelope(
        control=control,
        operation=operation,
        source=source,
        execution=verifier.execution,
        exit_code=exit_code,
        decision=decision,
        decision_source="release_decision" if decision is not None else "none",
        input_id=verifier.request_id,
        current_control_id=pointer.current_control_id if pointer is not None else None,
        artifacts=_artifact_refs(pointer, artifact_root),
        declaration_route=(
            _declaration_route(control, verifier)
            if operation == "verify" and decision is not None
            else None
        ),
    )


def _declaration_route(
    control: AgentControl,
    verifier: VerifierArtifact,
) -> ConfirmDeclarationsAction | None:
    """The typed declaration route, when this control's step is that one.

    The join is a command comparison, and it is deliberately the strictest one
    available. ``fix_task.declaration_confirmation`` says a declaration route
    was *built*; ``control.next_action`` says which step was actually
    *selected*. Those can differ — the pointer reconciliation above can drop a
    run's route entirely, and a later branch of the control derivation can win
    — and publishing the richer form off the first alone would describe a step
    the control is not asking for.

    Returns ``None`` on every mismatch rather than raising: an envelope that
    falls back to the plain ``repair`` action is still correct and still
    runnable. It just says less.
    """

    fix_task = verifier.fix_task
    confirmation = fix_task.declaration_confirmation if fix_task is not None else None
    if confirmation is None or control.state != "agent_action_required":
        return None
    action = control.next_action
    if not isinstance(action, CodingAgentCommandAction):
        return None
    if action.command != confirmation.command:
        return None
    drafts = [
        item for item in confirmation.questions if item.authorable_by == "coding_agent"
    ]
    human = [item for item in confirmation.questions if item.authorable_by == "human"]
    if not drafts:
        return None
    return ConfirmDeclarationsAction(
        kind="confirm_declarations",
        command=action.command,
        expects=(
            f"{len(drafts)} declaration(s) written into the manifest"
            # Only where this control actually grants it. A route says what
            # its own permission vector allows, and promising a commit beside
            # ``commit: false`` would be the envelope contradicting itself in
            # the same object.
            + (
                " and committed to this branch"
                if control.permissions.publishes
                else " (this run authorizes the named command only)"
            )
            + (
                f"; {len(human)} question(s) then remain for a human."
                if human
                else "; no question then remains."
            )
        ),
        why=action.why,
        questions=[
            EnvelopeDeclarationQuestion(
                subject=item.subject,
                dimension=item.dimension,
                answer_path=item.answer_path,
                authorable_by=item.authorable_by,
            )
            # Human-owned rows first, then the drafted ones, each half keeping
            # the report's own ranking. Not a re-ranking of the questionnaire —
            # it decides only which rows survive the cap, and the two halves are
            # not equally droppable: a drafted row is answered by the command
            # whether or not it is printed, while a human-owned row is the thing
            # the agent has to hand a person. Truncating in report order would
            # cut exactly the rows the route exists to name.
            for item in (*human, *drafts)[:MAX_ENVELOPE_QUESTIONS]
        ],
        agent_authorable=len(drafts),
        human_authorable=len(human),
    )


def denied_control_envelope(
    *,
    operation: AgentControlOperation,
    source: AgentControlSource,
    execution: AgentControlExecution,
    exit_code: int | None,
    reason: str,
) -> AgentControlEnvelope:
    """The answer when no control authority can be established.

    Used where a caller must still receive a well-formed envelope rather than an
    exception — most importantly ``verify --format control`` when the run's own
    pointer no longer describes the live workspace. The run's verdict is not
    suppressed; what is withheld is authority, which is the only safe direction
    when the subject of the decision has moved.
    """

    return project_agent_control_envelope(
        control=_human_stop(reason),
        operation=operation,
        source=source,
        execution=execution,
        exit_code=exit_code,
        decision=None,
        decision_source="none",
    )


def envelope_from_agent_result(
    result: AgentResultV2,
    *,
    execution: AgentControlExecution = "succeeded",
) -> AgentControlEnvelope:
    """Project a local boundary check.

    ``check`` runs before any release decision exists, so ``decision`` carries
    the boundary verdict and ``decision_source`` says so explicitly. It publishes
    no pointer and binds no artifacts: the check reads a diff and writes nothing.

    ``execution`` is supplied by the caller rather than assumed, because a check
    that could not resolve its diff still emits a considered ``block`` — the
    verdict succeeded, the evaluation did not. ``verify`` reports the same
    situation as ``failed``, and the two commands must not describe one
    condition two ways.

    ``exit_code`` is always 0: ``check`` has no gate exit code, and a caller
    that read the process status as authority would be wrong on every ``block``.
    """

    return project_agent_control_envelope(
        control=result.control,
        operation="check",
        source="run",
        execution=execution,
        exit_code=0,
        decision=result.decision,
        decision_source="agent_boundary",
        # `check` writes nothing, so there is no pointer and no artifact to bind
        # authority to. `audit_id` is the only identity of the request it
        # assessed; without it two unrelated diffs project byte-identical
        # `complete` envelopes that both grant merge.
        input_id=result.audit_id,
        # Carried, not dropped: a graded row routes the agent onward while still
        # owing a human a look, and the compact form is what the agent reports.
        pending_review=[
            AgentControlPendingReview(
                rule_id=item.rule_id,
                risk_level=item.risk_level,
                path=item.path,
                reviewers=list(item.reviewers),
            )
            for item in getattr(result, "pending_review", ())
        ],
    )


def envelope_from_setup(
    control: AgentControl,
    *,
    operation: AgentControlOperation,
    decision: str,
    input_id: str,
    execution: AgentControlExecution = "succeeded",
    exit_code: int | None = None,
    setup_edit: SetupEditAction | None = None,
) -> AgentControlEnvelope:
    """Project a setup command — ``detect``, ``init``, ``doctor`` — onto the envelope.

    These run before a release decision can exist, so ``decision`` carries the
    setup verdict and ``decision_source`` says ``setup``. That naming is the
    whole reason the field exists: without it ``control_state`` would mean "the
    gate says" on ``verify`` and "setup says" on ``init``, and a reader would
    have no way to tell which it was holding.

    Two properties are enforced here rather than left to each caller, because
    "every setup command remembered to deny authority" is not a property a
    contract can rest on:

    * **Setup authorizes nothing but its own next action.** No setup command
      reads a diff, so none of them has an evaluated change to stand behind.
      ``permissions`` is therefore all-false on every setup envelope, and the
      published schema additionally makes ``complete`` unreachable for these
      operations by fixing ``CompleteControlEnvelope.operation`` to
      ``verify``/``check``.
    * **Setup binds no artifacts and no control identity.** It publishes no
      pointer, so there is nothing whose hash it could vouch for.

    ``input_id`` names the workspace state the setup answer was computed
    against, for the same reason ``check`` records its ``audit_id``: an answer
    detached from its subject is one a reader cannot check.
    """

    if operation not in SETUP_OPERATIONS:
        raise ValueError(f"{operation!r} is not a setup operation")
    if decision not in SETUP_DECISIONS:
        raise ValueError(f"{decision!r} is not one of {SETUP_DECISIONS}")
    if control.permissions.authorizes_anything:
        raise ValueError(
            "setup read no change, so it cannot authorize edit, commit, push, "
            "update_pr, merge, or report_complete"
        )
    return project_agent_control_envelope(
        control=control,
        operation=operation,
        source="run",
        execution=execution,
        exit_code=exit_code,
        decision=decision,
        decision_source="setup",
        input_id=input_id,
        setup_edit=setup_edit,
    )


def envelope_from_routeless_pointer(
    pointer: CurrentControlPointer,
    *,
    verify_command: str,
    decision_withheld: str,
    artifact_root: str | None = None,
) -> AgentControlEnvelope:
    """Project a *current* generation that reaches no verifier route.

    A `scan` pointer is current — it passes the generation-safe read and the
    live-workspace comparison — but binds no verifier, so it carries no route.
    Refusing it conflated two different answers: the published contract says a
    non-zero exit means *no current identity exists*, and here one does.

    No release verdict is published from here, and ``decision_withheld`` is the
    required sentence saying why. A `scan` does reach a decision, but its pointer
    records no HEAD, no worktree overlay, and no input set, so nothing about that
    verdict can be reconfirmed against the workspace as it stands; an earlier
    revision lifted it anyway and published a stale `passed` after a referenced
    tool source changed. Requiring the explanation rather than allowing a bare
    ``decision: null`` is what keeps this distinguishable from an envelope
    produced before any engine ran, which is the ambiguity #323 exists to remove.

    `permissions` and the state come from the pointer exactly as before.

    The route is not invented. `scan` is not the gate, so "run verify on this
    workspace" is the step this generation is actually short of, and it is
    derived from the caller's own workspace and reports directory rather than a
    hardcoded default that would silently retarget the subject just validated.

    Authority is unchanged: the pointer's permission vector is carried through,
    `merge` and `report_complete` stay denied, and `execution` reports
    `not_run` because no verification did.
    """

    projected = pointer.control
    reason = f"{projected.reason} {decision_withheld}"
    if projected.state == "human_review_required":
        control: AgentControl = _human_stop(reason)
    else:
        control = derive_agent_control(
            reason=reason,
            next_action=CodingAgentCommandAction(
                kind="verify",
                command=verify_command,
                why=(
                    "This directory's current control was published by a command "
                    "whose release decision cannot be shown to still describe the "
                    "workspace. Run verify to obtain one that can."
                ),
            ),
            verify_required=True,
            allowed_next_commands=[verify_command],
            publication_allowed=projected.permissions.publishes,
        )
    return project_agent_control_envelope(
        control=control,
        operation=pointer.operation,
        source="refresh",
        execution="not_run",
        exit_code=None,
        decision=None,
        decision_source="none",
        input_id=pointer.request_id,
        current_control_id=pointer.current_control_id,
        artifacts=_artifact_refs(pointer, artifact_root),
    )


def envelope_from_pointer(
    pointer: CurrentControlPointer,
    *,
    verifier: VerifierArtifact | None,
    exit_code: int | None,
    artifact_root: str | None = None,
) -> AgentControlEnvelope:
    """Project a validated pointer read at a refresh boundary.

    ``verifier`` is the bound run artifact the pointer references, already
    hash-validated by :func:`read_current_control`. It is the only place the
    route, the execution status, and the release decision survive; the pointer
    deliberately records none of them so that it cannot become a second verdict.
    Without it there is nothing to route on, and the caller is told to re-verify
    rather than handed a fabricated step.
    """

    if verifier is None:
        raise AgentControlRouteUnavailable(
            "The current control pointer binds no verifier artifact, so no "
            "published route could be recovered for this workspace."
        )
    operation: AgentControlOperation = pointer.operation
    return envelope_from_verifier(
        verifier,
        operation=operation,
        source="refresh",
        exit_code=exit_code,
        pointer=pointer,
        artifact_root=artifact_root,
    )


def render_agent_control_envelope(envelope: AgentControlEnvelope) -> str:
    """Render the one canonical text form every emitter prints."""

    return json.dumps(envelope.model_dump(mode="json"), indent=2, sort_keys=True)


def control_headline_lines(envelope: AgentControlEnvelope) -> list[str]:
    """The human-facing lead: operational state, next actor, then authority.

    Human output and the compact JSON are rendered from the same envelope so a
    person reading the terminal and an agent parsing stdout can never be told
    two different things about who acts next.
    """

    granted = [name for name in _PERMISSION_ORDER if getattr(envelope.permissions, name)]
    denied = [name for name in _PERMISSION_ORDER if not getattr(envelope.permissions, name)]
    lines = [
        f"Control: {envelope.control_state} — next actor: {envelope.next_actor}",
        "You may: " + (", ".join(granted) if granted else "nothing until this is resolved"),
    ]
    if denied:
        lines.append("You may not: " + ", ".join(denied))
    if envelope.verify_required:
        lines.append("Verification is still required before this can complete.")
    if envelope.pending_review:
        owed = ", ".join(single_line_text(item.rule_id) for item in envelope.pending_review)
        lines.append(f"Still owed human review: {owed}")
    action = envelope.next_action
    if action is not None:
        lines.append(f"Next: {single_line_text(action.why)}")
        if isinstance(action, CodingAgentCommandAction):
            # Deliberately not the bare ``Run:`` prefix that
            # `primary_evidence_remediation_text` uses for the evidence-gap
            # rerun. #358 requires the human work to precede that line, and
            # these headline lines print first — reusing the prefix would put a
            # `Run:` above the remediation it is not the remediation for.
            lines.append(f"Next command: {single_line_text(action.command)}")
        elif isinstance(action, SetupEditAction):
            lines.append(f"Next edit: {single_line_text(action.path)}")
            lines.append(f"Expected: {single_line_text(action.expects)}")
        elif isinstance(action, CodingAgentFetchBaseAction):
            lines.append(f"Provide: {single_line_text(action.expects)}")
    return lines


# C0 and C1 control characters, plus DEL. Newlines are the dangerous ones here,
# but a bare CR overwrites a rendered line and ANSI escapes can repaint one.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def single_line_text(value: str) -> str:
    """Render one field on one line, showing control characters rather than obeying them.

    These lines state authority, and every value interpolated into them —
    ``why``, ``command``, ``expects`` — can contain a workspace path, a Git
    error, or a ref, none of which is under Shipgate's control. A path
    containing newlines produced forged ``Control: complete`` and ``You may:
    ... merge`` lines *below* the real denial, which is the reading a human or a
    line-scraping tool takes away.

    JSON output is unaffected and keeps the exact bytes: ``json.dumps`` already
    escapes these, and a consumer parsing structure cannot be confused by them.
    """

    return _CONTROL_CHARACTERS.sub(lambda match: f"\\x{ord(match.group()):02x}", value)


_PERMISSION_ORDER = ("edit", "commit", "push", "update_pr", "merge", "report_complete")


def _reconcile_with_pointer(
    control: AgentControl,
    pointer: CurrentControlPointer | None,
) -> AgentControl:
    """Let the pointer overrule a run that claimed more than it can bind."""

    if pointer is None:
        return control
    projected = pointer.control
    # State alone is not the authority. A pointer carrying
    # `agent_action_required` with no permissions and a verifier carrying the
    # same state with publish-only permissions agree on the tag and disagree on
    # what is authorized; taking the verifier's widened publication authority
    # on a tag match is the fail-open direction.
    if projected.state == control.state and (
        projected.permissions.model_dump() == control.permissions.model_dump()
    ):
        return control
    # `project_agent_control` only ever narrows, so a disagreement is either
    # that narrowing or an inconsistency neither artifact can explain. Both stop.
    return _human_stop(projected.reason)


def _human_stop(reason: str) -> HumanReviewRequiredControl:
    """The one control object that authorizes nothing at all."""

    bounded = truncate_prose(reason)
    return HumanReviewRequiredControl(
        state="human_review_required",
        reason=bounded,
        next_action=HumanControlAction(kind="review", why=bounded),
        human_review=RequiredHumanReview(why=bounded),
        stop_reason=bounded,
    )


def _artifact_refs(
    pointer: CurrentControlPointer | None,
    artifact_root: str | None,
) -> dict[str, AgentControlArtifactRef]:
    if pointer is None:
        return {}
    # Joined structurally, never by string arithmetic. Trimming corrupted path
    # identity: a root of "/" collapsed to "" and emitted a path relative to the
    # invocation directory, and "/tmp/reports " lost a significant trailing
    # space — in both cases naming a file other than the one whose hash was
    # validated, while still exiting successfully.
    root = PurePosixPath(artifact_root) if artifact_root else None
    return {
        key: AgentControlArtifactRef(
            path=str(root / ref.path) if root is not None else ref.path,
            sha256=ref.sha256,
        )
        for key, ref in sorted(pointer.artifacts.items())
    }


def _bounded_action(
    action: EnvelopeCodingAgentAction | AgentControlAction | None,
) -> EnvelopeCodingAgentAction | AgentControlAction | None:
    """Cap the action's prose without ever touching its command or expectation."""

    if action is None:
        return None
    why = truncate_prose(action.why)
    if why == action.why:
        return action
    if isinstance(
        action,
        (
            CodingAgentCommandAction,
            SetupEditAction,
            ConfirmDeclarationsAction,
            *COMMANDLESS_CODING_AGENT_ACTIONS,
        ),
    ):
        # ``command``, ``path`` and ``expects`` are executed, opened and checked,
        # not read, so the cap applies to ``why`` alone. The setup edit is in
        # this branch for exactly that reason: its ``why`` is a diagnostic
        # message and can be any length, and routing it around the cap published
        # a 1,134-byte field on a contract that documents 400.
        return action.model_copy(update={"why": why})
    if isinstance(action, HumanReviewAction):
        return HumanReviewAction(why=why)
    return HumanControlAction(kind=action.kind, why=why)


def _bounded_human_review(
    review: NoHumanReview | RequiredHumanReview,
) -> NoHumanReview | RequiredHumanReview:
    if isinstance(review, NoHumanReview):
        return review
    why = truncate_prose(review.why)
    if why == review.why:
        return review
    return RequiredHumanReview(why=why, required_reviewers=list(review.required_reviewers))


__all__ = [
    "AgentControlRouteUnavailable",
    "control_headline_lines",
    "envelope_from_agent_result",
    "envelope_from_pointer",
    "envelope_from_routeless_pointer",
    "envelope_from_setup",
    "envelope_from_verifier",
    "project_agent_control_envelope",
    "render_agent_control_envelope",
    "single_line_text",
]
