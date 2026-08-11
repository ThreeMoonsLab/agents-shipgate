"""The single projection that produces ``shipgate.agent_control/v1``.

Three surfaces emit the envelope — ``verify --format control``,
``check --format agent-control-json``, and ``agents-shipgate agent control`` —
and all three come through here. That is the point: an envelope assembled
independently at each call site would be a second control vocabulary within a
month, which is the outcome #333 and #323 were merged to prevent.

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
    AgentActionControlEnvelope,
    AgentControlArtifactRef,
    AgentControlDecisionSource,
    AgentControlEnvelope,
    AgentControlExecution,
    AgentControlOperation,
    AgentControlPendingReview,
    AgentControlSource,
    CompleteControlEnvelope,
    HumanReviewRequiredControlEnvelope,
    ReviewPublishableControlEnvelope,
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
) -> AgentControlEnvelope:
    """Project one authoritative control object onto the compact envelope.

    The only transformation applied to anything is the prose cap in
    :func:`truncate_prose`. Every other field is a copy.
    """

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
    action = _bounded_action(control.next_action)
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


def envelope_from_routeless_pointer(
    pointer: CurrentControlPointer,
    *,
    verify_command: str,
    artifact_root: str | None = None,
) -> AgentControlEnvelope:
    """Project a *current* generation that reaches no release decision.

    A `scan` pointer is current — it passes the generation-safe read and the
    live-workspace comparison — but binds no verifier, so it carries no route
    and no decision. Refusing it conflated two different answers: the published
    contract says a non-zero exit means *no current identity exists*, and here
    one does.

    The route is not invented. `scan` is not the gate, so "run verify on this
    workspace" is the step this generation is actually short of, and it is
    derived from the caller's own workspace and reports directory rather than a
    hardcoded default that would silently retarget the subject just validated.

    Authority is unchanged: the pointer's permission vector is carried through,
    `merge` and `report_complete` stay denied, and `execution` reports
    `not_run` because no verification did.
    """

    projected = pointer.control
    reason = projected.reason
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
                    "that reaches no release decision. Run verify to obtain one."
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
        owed = ", ".join(_single_line(item.rule_id) for item in envelope.pending_review)
        lines.append(f"Still owed human review: {owed}")
    action = envelope.next_action
    if action is not None:
        lines.append(f"Next: {_single_line(action.why)}")
        if isinstance(action, CodingAgentCommandAction):
            # Deliberately not the bare ``Run:`` prefix that
            # `primary_evidence_remediation_text` uses for the evidence-gap
            # rerun. #358 requires the human work to precede that line, and
            # these headline lines print first — reusing the prefix would put a
            # `Run:` above the remediation it is not the remediation for.
            lines.append(f"Next command: {_single_line(action.command)}")
        elif isinstance(action, CodingAgentFetchBaseAction):
            lines.append(f"Provide: {_single_line(action.expects)}")
    return lines


# C0 and C1 control characters, plus DEL. Newlines are the dangerous ones here,
# but a bare CR overwrites a rendered line and ANSI escapes can repaint one.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _single_line(value: str) -> str:
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

    if pointer is None or pointer.control.state == control.state:
        return control
    # `project_agent_control` only ever downgrades, and only onto this state.
    # Anything else means the two artifacts disagree in a way neither can
    # explain, which is itself a reason to stop.
    return _human_stop(pointer.control.reason)


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


def _bounded_action(action: AgentControlAction | None) -> AgentControlAction | None:
    """Cap the action's prose without ever touching its command or expectation."""

    if action is None:
        return None
    why = truncate_prose(action.why)
    if why == action.why:
        return action
    if isinstance(action, CodingAgentCommandAction):
        return action.model_copy(update={"why": why})
    if isinstance(action, CodingAgentFetchBaseAction):
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
    "envelope_from_verifier",
    "project_agent_control_envelope",
    "render_agent_control_envelope",
]
