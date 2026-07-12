"""Authoritative operational control contract for coding agents.

``AgentControl`` is deliberately a discriminated union instead of a model with
independent booleans.  The state tag fixes the completion, stop, routing, and
human-review fields, so contradictory instructions are rejected by both
Pydantic and generated JSON Schema validators.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

AgentControlState = Literal[
    "complete",
    "agent_action_required",
    "human_review_required",
]
AgentActionKind = Literal[
    "verify",
    "discover",
    "configure",
    "initialize",
    "repair",
    "install",
    "rerun",
]
HumanActionKind = Literal["review", "stop"]

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
ExactCommand = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class CodingAgentCommandAction(BaseModel):
    """An executable, exact next step owned by the coding agent."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["actor", "kind", "command", "expects", "why"]},
    )

    actor: Literal["coding_agent"] = "coding_agent"
    kind: AgentActionKind
    command: ExactCommand
    expects: None = None
    why: NonEmptyText


class CodingAgentFetchBaseAction(BaseModel):
    """A structured input request when an exact fetch command is unavailable.

    Shipgate never fetches refs itself.  ``expects`` therefore names the exact
    ref or artifact a caller must make available before rerunning verification.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["actor", "kind", "command", "expects", "why"]},
    )

    actor: Literal["coding_agent"] = "coding_agent"
    kind: Literal["fetch_base"]
    command: None = None
    expects: NonEmptyText
    why: NonEmptyText


class HumanControlAction(BaseModel):
    """A human-owned route.  Human actions never expose executable commands."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["actor", "kind", "command", "expects", "why"]},
    )

    actor: Literal["human"] = "human"
    kind: HumanActionKind
    command: None = None
    expects: None = None
    why: NonEmptyText


type CodingAgentAction = Annotated[
    CodingAgentCommandAction | CodingAgentFetchBaseAction,
    Field(discriminator="kind"),
]
type AgentControlAction = Annotated[
    CodingAgentCommandAction | CodingAgentFetchBaseAction | HumanControlAction,
    Field(discriminator="kind"),
]


class NoHumanReview(BaseModel):
    """Exact negative human-review projection for non-stopping states."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["required", "why", "required_reviewers"]},
    )

    required: Literal[False] = False
    why: None = None
    required_reviewers: list[str] = Field(default_factory=list, max_length=0)


class RequiredHumanReview(BaseModel):
    """Human-review evidence carried by the stopping state."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": ["required", "why", "required_reviewers"]},
    )

    required: Literal[True] = True
    why: NonEmptyText
    required_reviewers: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reviewers_are_unique(self) -> RequiredHumanReview:
        if len(self.required_reviewers) != len(set(self.required_reviewers)):
            raise ValueError("required_reviewers must not contain duplicates")
        self.required_reviewers = sorted(self.required_reviewers)
        return self


class _AgentControlBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "required": [
                "state",
                "reason",
                "completion_allowed",
                "must_stop",
                "verify_required",
                "next_action",
                "allowed_next_commands",
                "human_review",
                "stop_reason",
            ]
        },
    )

    state: AgentControlState
    reason: NonEmptyText
    completion_allowed: bool
    must_stop: bool
    verify_required: bool
    next_action: AgentControlAction | None
    allowed_next_commands: list[ExactCommand]
    human_review: NoHumanReview | RequiredHumanReview
    # ``reason`` explains every state.  ``stop_reason`` is present only when
    # execution must stop, preserving the existing controller affordance while
    # making its presence structurally consistent with ``must_stop``.
    stop_reason: NonEmptyText | None

    @model_validator(mode="after")
    def _commands_are_unique(self) -> _AgentControlBase:
        if len(self.allowed_next_commands) != len(set(self.allowed_next_commands)):
            raise ValueError("allowed_next_commands must not contain duplicates")
        return self


class CompleteAgentControl(_AgentControlBase):
    """Terminal state: the coding agent may report the task complete."""

    state: Literal["complete"]
    completion_allowed: Literal[True] = True
    must_stop: Literal[False] = False
    verify_required: Literal[False] = False
    next_action: None = None
    allowed_next_commands: list[ExactCommand] = Field(default_factory=list, max_length=0)
    human_review: NoHumanReview = Field(default_factory=NoHumanReview)
    stop_reason: None = None


class AgentActionRequiredControl(_AgentControlBase):
    """Non-terminal state with one exact coding-agent-owned next step."""

    state: Literal["agent_action_required"]
    completion_allowed: Literal[False] = False
    must_stop: Literal[False] = False
    verify_required: bool = False
    next_action: CodingAgentAction
    allowed_next_commands: list[ExactCommand] = Field(default_factory=list)
    human_review: NoHumanReview = Field(default_factory=NoHumanReview)
    stop_reason: None = None

    @model_validator(mode="after")
    def _action_is_an_allowed_route(self) -> AgentActionRequiredControl:
        action = self.next_action
        if isinstance(action, CodingAgentFetchBaseAction):
            # Structured requests deliberately carry no command.  Additional
            # exact recovery commands may still be offered by the caller.
            self.allowed_next_commands = sorted(self.allowed_next_commands)
            return self
        if action.command not in self.allowed_next_commands:
            raise ValueError(
                "the coding-agent next_action.command must be present in allowed_next_commands"
            )
        if action.kind == "verify" and not self.verify_required:
            raise ValueError("verify actions must preserve verify_required=true")
        # The primary route is already encoded by ``next_action``.  Keep it
        # first for readers, then normalize every alternative so semantically
        # identical input orderings produce byte-identical control artifacts.
        self.allowed_next_commands = [
            action.command,
            *sorted(command for command in self.allowed_next_commands if command != action.command),
        ]
        return self


class HumanReviewRequiredControl(_AgentControlBase):
    """Stopping state: no further coding-agent action is authorized."""

    state: Literal["human_review_required"]
    completion_allowed: Literal[False] = False
    must_stop: Literal[True] = True
    verify_required: bool = False
    next_action: HumanControlAction
    allowed_next_commands: list[ExactCommand] = Field(default_factory=list, max_length=0)
    human_review: RequiredHumanReview
    stop_reason: NonEmptyText


type AgentControl = Annotated[
    CompleteAgentControl | AgentActionRequiredControl | HumanReviewRequiredControl,
    Field(discriminator="state"),
]

AGENT_CONTROL_ADAPTER = TypeAdapter(AgentControl)


def validate_agent_control(value: object) -> AgentControl:
    """Validate an arbitrary value against the authoritative union."""

    return AGENT_CONTROL_ADAPTER.validate_python(value)


def normalize_legacy_agent_control(
    payload: Mapping[str, Any],
    *,
    verification_command: str | None = None,
) -> AgentControl:
    """Read v1 control fields fail-closed into the canonical union."""

    existing = payload.get("control")
    if existing is not None:
        try:
            return AGENT_CONTROL_ADAPTER.validate_python(existing)
        except ValidationError:
            return _legacy_human_fallback(
                "The embedded agent control is invalid and requires human review."
            )

    action_raw = payload.get("first_next_action") or payload.get("next_action")
    action = action_raw if isinstance(action_raw, Mapping) else {}
    actor = str(action.get("actor") or "")
    kind = str(action.get("kind") or "")
    reason = (
        _non_empty(action.get("why"))
        or _non_empty(payload.get("summary"))
        or "Legacy control state was normalized."
    )
    human = payload.get("human_review")
    human_required = bool(isinstance(human, Mapping) and human.get("required") is True)
    if actor == "human" or human_required:
        review_why = (
            _non_empty(human.get("why")) if isinstance(human, Mapping) else None
        ) or reason
        reviewers = human.get("required_reviewers", []) if isinstance(human, Mapping) else []
        return HumanReviewRequiredControl(
            state="human_review_required",
            reason=reason,
            verify_required=bool(payload.get("verify_required", False)),
            next_action=HumanControlAction(
                kind="stop" if kind == "stop" else "review",
                why=review_why,
            ),
            human_review=RequiredHumanReview(
                why=review_why,
                required_reviewers=_string_sequence(reviewers),
            ),
            stop_reason=_non_empty(payload.get("stop_reason")) or review_why,
        )

    command = (
        _non_empty(action.get("command"))
        or _non_empty(verification_command)
        or _non_empty(payload.get("verification_command"))
        or _first_command(payload.get("allowed_next_commands"))
    )
    verify_required = bool(payload.get("verify_required", False))
    mapped_kind: str | None = None
    if kind in {
        "verify",
        "discover",
        "configure",
        "initialize",
        "repair",
        "install",
        "rerun",
    }:
        mapped_kind = kind
    elif kind == "command" and actor == "coding_agent" and command:
        lowered = command.lower()
        if " install " in f" {lowered} " or " upgrade " in f" {lowered} ":
            mapped_kind = "install"
        elif "verify --preview" in lowered:
            mapped_kind = "configure"
        elif "verify" in lowered:
            mapped_kind = "verify"
        else:
            mapped_kind = "rerun"
    elif verify_required:
        mapped_kind = "verify"

    requires_agent_route = (
        verify_required
        or kind == "install"
        or (kind == "repair" and actor == "coding_agent")
        or (actor == "coding_agent" and mapped_kind is not None)
    )
    if requires_agent_route:
        if mapped_kind is None or command is None:
            return _legacy_human_fallback(
                "Legacy control requires a coding-agent action but does not "
                "provide an exact command."
            )
        try:
            next_action = CodingAgentCommandAction(
                kind=mapped_kind,  # type: ignore[arg-type]
                command=command,
                why=reason,
            )
            return AgentActionRequiredControl(
                state="agent_action_required",
                reason=reason,
                verify_required=verify_required or mapped_kind == "verify",
                next_action=next_action,
                allowed_next_commands=_commands_with(payload.get("allowed_next_commands"), command),
                human_review=NoHumanReview(),
            )
        except ValidationError:
            return _legacy_human_fallback("Legacy control contains an invalid coding-agent action.")

    if payload.get("completion_allowed") is True and payload.get("must_stop") is not True:
        return CompleteAgentControl(state="complete", reason=reason)
    return _legacy_human_fallback("Legacy control is contradictory or cannot be resolved safely.")


def _legacy_human_fallback(reason: str) -> HumanReviewRequiredControl:
    return HumanReviewRequiredControl(
        state="human_review_required",
        reason=reason,
        next_action=HumanControlAction(kind="review", why=reason),
        human_review=RequiredHumanReview(why=reason),
        stop_reason=reason,
    )


def _non_empty(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _string_sequence(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _first_command(value: Any) -> str | None:
    commands = _string_sequence(value)
    return commands[0] if commands else None


def _commands_with(value: Any, command: str) -> list[str]:
    commands = _string_sequence(value)
    if command not in commands:
        commands.insert(0, command)
    return commands


__all__ = [
    "AGENT_CONTROL_ADAPTER",
    "AgentActionKind",
    "AgentActionRequiredControl",
    "AgentControl",
    "AgentControlAction",
    "AgentControlState",
    "CodingAgentAction",
    "CodingAgentCommandAction",
    "CodingAgentFetchBaseAction",
    "CompleteAgentControl",
    "HumanActionKind",
    "HumanControlAction",
    "HumanReviewRequiredControl",
    "NoHumanReview",
    "RequiredHumanReview",
    "normalize_legacy_agent_control",
    "validate_agent_control",
]
