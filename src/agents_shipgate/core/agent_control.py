"""Pure derivation and fail-closed migration for :mod:`agent_control`.

Callers supply facts and an exact next route.  This module is the only place
that projects those facts onto operational control booleans; callers never set
``completion_allowed`` or ``must_stop`` independently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from agents_shipgate.schemas.agent_control import (
    AgentActionRequiredControl,
    AgentControl,
    AgentControlAction,
    CodingAgentCommandAction,
    CodingAgentFetchBaseAction,
    CompleteAgentControl,
    HumanControlAction,
    HumanReviewRequiredControl,
    NoHumanReview,
    RequiredHumanReview,
    normalize_legacy_agent_control,
)


class AgentControlConsistencyError(ValueError):
    """Raised when canonical facts request mutually exclusive routes."""


def derive_agent_control(
    *,
    reason: str,
    next_action: AgentControlAction | Mapping[str, Any] | None = None,
    verify_required: bool = False,
    human_review_required: bool = False,
    unsafe_block: bool = False,
    allowed_next_commands: Sequence[str] = (),
    human_review_why: str | None = None,
    required_reviewers: Sequence[str] = (),
    stop_reason: str | None = None,
) -> AgentControl:
    """Project normalized obligations onto exactly one control state.

    Precedence is intentionally explicit: human-only review or an unsafe block
    stops; otherwise an exact coding-agent route remains actionable; otherwise
    pending verification without a route is an internal consistency error; and
    only an obligation-free result is complete.

    ``completion_allowed`` and ``must_stop`` are not inputs.  They are fixed by
    the selected union variant and therefore cannot drift across projections.
    """

    action = _validate_action(next_action)
    needs_human = human_review_required or unsafe_block or isinstance(action, HumanControlAction)

    if needs_human:
        if action is not None and not isinstance(action, HumanControlAction):
            raise AgentControlConsistencyError(
                "human-only control facts cannot carry a coding-agent next action"
            )
        review_why = human_review_why or reason
        human_action = action or HumanControlAction(
            kind="stop" if unsafe_block else "review",
            why=review_why,
        )
        return HumanReviewRequiredControl(
            state="human_review_required",
            reason=reason,
            verify_required=verify_required,
            next_action=human_action,
            human_review=RequiredHumanReview(
                why=review_why,
                required_reviewers=list(required_reviewers),
            ),
            stop_reason=stop_reason or review_why,
        )

    if isinstance(action, (CodingAgentCommandAction, CodingAgentFetchBaseAction)):
        commands = list(allowed_next_commands)
        if isinstance(action, CodingAgentCommandAction) and action.command not in commands:
            commands.insert(0, action.command)
        # ``repair`` may rerun a local boundary check rather than full verify;
        # callers preserve the independent obligation explicitly.  Only an
        # action whose kind is itself ``verify`` implies this bit.
        action_requires_verify = action.kind == "verify"
        return AgentActionRequiredControl(
            state="agent_action_required",
            reason=reason,
            verify_required=verify_required or action_requires_verify,
            next_action=action,
            allowed_next_commands=commands,
            human_review=NoHumanReview(),
        )

    if verify_required:
        raise AgentControlConsistencyError(
            "verify_required=true requires an exact coding-agent next action"
        )
    if allowed_next_commands:
        raise AgentControlConsistencyError("a complete result cannot expose allowed next commands")
    if required_reviewers or human_review_why or stop_reason:
        raise AgentControlConsistencyError(
            "a complete result cannot carry a human or stop obligation"
        )
    return CompleteAgentControl(state="complete", reason=reason)


def _validate_action(
    value: AgentControlAction | Mapping[str, Any] | None,
) -> AgentControlAction | None:
    if value is None:
        return None
    try:
        # Reuse the union validator by embedding the action in the matching
        # control is needlessly circular.  Its three variants have disjoint
        # ``kind`` values, so direct model selection stays deterministic.
        if isinstance(
            value, (CodingAgentCommandAction, CodingAgentFetchBaseAction, HumanControlAction)
        ):
            return value
        kind = value.get("kind")
        if kind == "fetch_base":
            return CodingAgentFetchBaseAction.model_validate(value)
        if kind in {"review", "stop"}:
            return HumanControlAction.model_validate(value)
        return CodingAgentCommandAction.model_validate(value)
    except (ValidationError, AttributeError) as exc:
        raise AgentControlConsistencyError(f"invalid next action: {exc}") from exc


__all__ = [
    "AgentControlConsistencyError",
    "derive_agent_control",
    "normalize_legacy_agent_control",
]
