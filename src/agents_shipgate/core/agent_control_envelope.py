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
from collections.abc import Mapping

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
        return CompleteControlEnvelope(control_state="complete", **shared)
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
    action = envelope.next_action
    if action is not None:
        lines.append(f"Next: {action.why}")
        if isinstance(action, CodingAgentCommandAction):
            # Deliberately not the bare ``Run:`` prefix that
            # `primary_evidence_remediation_text` uses for the evidence-gap
            # rerun. #358 requires the human work to precede that line, and
            # these headline lines print first — reusing the prefix would put a
            # `Run:` above the remediation it is not the remediation for.
            lines.append(f"Next command: {action.command}")
        elif isinstance(action, CodingAgentFetchBaseAction):
            lines.append(f"Provide: {action.expects}")
    return lines


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
    root = (artifact_root or "").strip().rstrip("/")
    return {
        key: AgentControlArtifactRef(
            path=f"{root}/{ref.path}" if root else ref.path,
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
    "envelope_from_verifier",
    "project_agent_control_envelope",
    "render_agent_control_envelope",
]
