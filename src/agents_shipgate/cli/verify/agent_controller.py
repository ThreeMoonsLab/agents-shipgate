"""Deterministic ``agent_controller`` projection for ``agents-shipgate verify``.

The ``agent_controller`` block answers the four questions an autonomous coding
agent must resolve without human interpretation: may I claim the task done, must
I stop for a human, what may I run next, and what must I never edit or do to get
past the gate. It is a pure projection of the verdict the orchestrator already
computed (``merge_verdict``, ``can_merge_without_human``, ``fix_task``,
``capability_review``) plus the static trust-root surface list — it introduces
no new decision, and ``completion_allowed`` is locked to
``can_merge_without_human`` by ``VerifierArtifact``.
"""

from __future__ import annotations

# ``PROTECTED_FILE_EDITS`` (the standing whole-file trust-root deny-list) is
# defined alongside ``TRUST_ROOT_SURFACES`` in ``checks.verify`` so the verify
# controller here and the ``agent_handoff`` preview fallback share one source.
# Re-exported for existing consumers/tests that import it from this module.
from agents_shipgate.checks.verify import PROTECTED_FILE_EDITS
from agents_shipgate.cli.verify.fix_task import FORBIDDEN_SHORTCUTS
from agents_shipgate.schemas.verifier import (
    AgentController,
    AgentStopReason,
    MergeVerdict,
    VerifierCapabilityReview,
    VerifierFixTask,
    VerifierHumanReview,
)


def build_agent_controller(
    *,
    merge_verdict: MergeVerdict,
    can_merge_without_human: bool,
    fix_task: VerifierFixTask | None,
    capability_review: VerifierCapabilityReview | None,
    human_review: VerifierHumanReview | None,
    headline: str | None,
) -> AgentController:
    """Project the computed verdict onto the imperative agent controller.

    Pure function of values the orchestrator already derived; never an LLM
    judgment. Not emitted for ``--preview`` (a pre-gate relevance check) — the
    caller passes ``None`` there.
    """
    self_approval = capability_review is not None and (
        capability_review.policy_weakened or capability_review.trust_root_touched
    )
    # The only non-stop, non-done state: every gating gap is mechanical, so the
    # agent may repair and re-verify (``fix_task`` routed to the coding agent).
    agent_safe_fix = (
        fix_task is not None
        and fix_task.actor == "coding_agent"
        and fix_task.safe_to_attempt
    )
    completion_allowed = can_merge_without_human
    # Stop only when the agent can neither finish nor safely repair — i.e. the
    # next move belongs to a human (authority gap, blocker, degraded evidence).
    must_stop = not completion_allowed and not agent_safe_fix

    stop_reason: AgentStopReason | None = None
    if must_stop:
        if self_approval:
            stop_reason = "self_approval_prohibited"
        elif merge_verdict == "blocked":
            stop_reason = "blocked_findings"
        elif merge_verdict == "insufficient_evidence":
            stop_reason = "insufficient_evidence"
        elif merge_verdict == "unknown":
            stop_reason = "scan_incomplete"
        else:
            stop_reason = "human_review_required"

    allowed_next_commands: list[str] = []
    if agent_safe_fix and fix_task is not None and fix_task.verification_command:
        # The only command that helps: apply the mechanical fix, then re-verify.
        allowed_next_commands = [fix_task.verification_command]

    return AgentController(
        completion_allowed=completion_allowed,
        must_stop=must_stop,
        stop_reason=stop_reason,
        allowed_next_commands=allowed_next_commands,
        forbidden_file_edits=list(PROTECTED_FILE_EDITS),
        forbidden_actions=list(FORBIDDEN_SHORTCUTS),
        user_message_template=_user_message(
            completion_allowed=completion_allowed,
            agent_safe_fix=agent_safe_fix,
            fix_task=fix_task,
            human_review=human_review,
            headline=headline,
        ),
    )


def _user_message(
    *,
    completion_allowed: bool,
    agent_safe_fix: bool,
    fix_task: VerifierFixTask | None,
    human_review: VerifierHumanReview | None,
    headline: str | None,
) -> str:
    if completion_allowed:
        return headline or "No agent-capability changes gate this PR; safe to merge."
    if agent_safe_fix:
        cmd = fix_task.verification_command if fix_task is not None else None
        tail = f" then re-run `{cmd}`" if cmd else " then re-run verify"
        return (
            f"Shipgate found mechanical gaps you may fix;{tail}. Do not weaken "
            "the gate, suppress findings, or invent approval/idempotency evidence."
        )
    why = human_review.why if human_review is not None and human_review.why else None
    base = why or headline or "A human must review this agent-capability change before merge."
    return f"{base} A coding agent cannot clear this gate itself."


__all__ = ["PROTECTED_FILE_EDITS", "build_agent_controller"]
