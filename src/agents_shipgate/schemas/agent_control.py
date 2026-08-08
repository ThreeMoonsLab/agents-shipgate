"""Authoritative operational control contract for coding agents.

``AgentControl`` is deliberately a discriminated union instead of a model with
independent booleans.  The state tag fixes the completion, stop, routing,
permission, and human-review fields, so contradictory instructions are rejected
by both Pydantic and generated JSON Schema validators.

Human review gates *merge and completion*, not *publication of the evidence a
human needs in order to review*.  ``permissions`` therefore separates the two
authorities an agent working on a pull request actually needs to distinguish:
progress actions (``edit`` / ``commit`` / ``push`` / ``update_pr``) and
terminal actions (``merge`` / ``report_complete``).  ``review_publishable``
is the state where the first group is authorized and the second is not.
``human_review_required`` remains the fail-closed stop for untrusted input and
policy blocks, where Shipgate authorizes nothing at all.
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
    "review_publishable",
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
# Coding-agent routes that run *before* Shipgate has read a diff at all: the
# requested refs are unavailable, or the gate binary itself is missing. They
# authorize their own ``next_action`` and nothing else.
UNEVALUATED_ACTION_KINDS: frozenset[str] = frozenset({"fetch_base", "install"})

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


class HumanReviewAction(HumanControlAction):
    """A human *review* route, never a hard stop.

    A distinct type rather than a runtime check on ``kind``: the publishable
    review state must reject a stop route in generated JSON Schema too, not
    only in Pydantic.
    """

    kind: Literal["review"] = "review"


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


_PERMISSION_FIELDS = (
    "edit",
    "commit",
    "push",
    "update_pr",
    "merge",
    "report_complete",
)


class _AgentPermissionsBase(BaseModel):
    """Action-scoped authority, fixed by the control state that carries it.

    ``edit``/``commit``/``push``/``update_pr`` are *progress* actions: they
    publish the change so a human can review it.  ``merge`` and
    ``report_complete`` are *terminal* actions: they assert the change is done.
    Only the terminal pair is tied to ``completion_allowed``.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"required": list(_PERMISSION_FIELDS)},
    )

    edit: bool
    commit: bool
    push: bool
    update_pr: bool
    merge: bool
    report_complete: bool

    @property
    def publishes(self) -> bool:
        """True when any progress action is authorized."""

        return self.edit or self.commit or self.push or self.update_pr

    @property
    def authorizes_anything(self) -> bool:
        """True when any of the six actions is authorized."""

        return self.publishes or self.merge or self.report_complete


class FullAgentPermissions(_AgentPermissionsBase):
    """Terminal authority: the verifier already authorizes merge."""

    edit: Literal[True] = True
    commit: Literal[True] = True
    push: Literal[True] = True
    update_pr: Literal[True] = True
    merge: Literal[True] = True
    report_complete: Literal[True] = True


class PublishOnlyPermissions(_AgentPermissionsBase):
    """Progress authority without merge or completion authority."""

    edit: Literal[True] = True
    commit: Literal[True] = True
    push: Literal[True] = True
    update_pr: Literal[True] = True
    merge: Literal[False] = False
    report_complete: Literal[False] = False


class NoAgentPermissions(_AgentPermissionsBase):
    """None of the six pull-request actions is authorized.

    Two different states carry this vector, for the same underlying reason —
    Shipgate has no assessment it is willing to stand behind.
    ``human_review_required`` additionally ends the turn.  An
    ``agent_action_required`` route whose subject was never evaluated
    (``fetch_base``, ``install``) does not end the turn, but the only thing it
    authorizes is the named ``next_action``: there is no diff yet, so there is
    nothing to publish and no basis for saying publishing it is safe.
    """

    edit: Literal[False] = False
    commit: Literal[False] = False
    push: Literal[False] = False
    update_pr: Literal[False] = False
    merge: Literal[False] = False
    report_complete: Literal[False] = False


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
    permissions: FullAgentPermissions | PublishOnlyPermissions | NoAgentPermissions
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

    @model_validator(mode="after")
    def _permissions_project_the_state(self) -> _AgentControlBase:
        """Bind the coarse booleans to ``permissions`` in the fail-closed direction.

        ``must_stop`` is retained for pre-contract-20 consumers and keeps its
        v14 meaning exactly — it is true precisely for
        ``human_review_required``, which is pinned by the variant Literals.
        The implication enforced here is one-directional on purpose:
        ``must_stop`` authorizes nothing, but ``must_stop=false`` does *not*
        promise publication, because an agent route whose subject was never
        evaluated authorizes only its own ``next_action``.
        """

        if self.permissions.report_complete != self.completion_allowed:
            raise ValueError("permissions.report_complete must equal completion_allowed")
        if self.permissions.merge != self.completion_allowed:
            raise ValueError("permissions.merge must equal completion_allowed")
        if self.must_stop and self.permissions.authorizes_anything:
            raise ValueError("a stopping result cannot authorize any action")
        return self


class CompleteAgentControl(_AgentControlBase):
    """Terminal state: the coding agent may report the task complete."""

    state: Literal["complete"]
    completion_allowed: Literal[True] = True
    must_stop: Literal[False] = False
    verify_required: Literal[False] = False
    next_action: None = None
    allowed_next_commands: list[ExactCommand] = Field(default_factory=list, max_length=0)
    permissions: FullAgentPermissions = Field(default_factory=FullAgentPermissions)
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
    permissions: PublishOnlyPermissions | NoAgentPermissions
    human_review: NoHumanReview = Field(default_factory=NoHumanReview)
    stop_reason: None = None

    @model_validator(mode="before")
    @classmethod
    def _absent_permissions_follow_the_route(cls, data: Any) -> Any:
        """Derive an omitted vector rather than defaulting it to publish.

        A pre-contract-20 artifact has no ``permissions`` at all, and must keep
        parsing. Reading it off the route it already carries is the only
        reconstruction that cannot invent authority: an ``install`` or
        ``fetch_base`` artifact never had an evaluated subject, whatever
        version wrote it.
        """

        if not isinstance(data, Mapping) or "permissions" in data:
            return data
        action = data.get("next_action")
        kind = action.get("kind") if isinstance(action, Mapping) else getattr(action, "kind", None)
        return {
            **data,
            "permissions": (
                NoAgentPermissions()
                if kind in UNEVALUATED_ACTION_KINDS
                else PublishOnlyPermissions()
            ),
        }

    @model_validator(mode="after")
    def _permissions_match_the_route(self) -> AgentActionRequiredControl:
        """Publication authority follows the evaluated subject, not the state.

        ``fetch_base`` and ``install`` mean Shipgate never saw a diff — the
        refs are unavailable, or the gate itself is missing. Claiming
        commit/push/PR-update authority there would assert something about a
        change that was never read, and would contradict "perform only
        ``next_action``".
        """

        unevaluated = self.next_action.kind in UNEVALUATED_ACTION_KINDS
        if unevaluated and self.permissions.publishes:
            raise ValueError(
                f"a {self.next_action.kind!r} route has no evaluated subject and "
                "cannot authorize publication"
            )
        if not unevaluated and not self.permissions.publishes:
            raise ValueError(
                f"an evaluated {self.next_action.kind!r} route must authorize the "
                "progress actions its own instruction requires"
            )
        return self

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


class ReviewPublishableControl(_AgentControlBase):
    """Human review gates merge; publishing the review evidence does not.

    The change was evaluated and the only outstanding obligation is human
    judgement, so the coding agent keeps the authority it needs to put a
    coherent, reviewable state in front of that human — edit, commit, push,
    update the pull request — while ``merge`` and ``report_complete`` stay
    denied until a fresh verifier artifact says otherwise.

    ``next_action`` stays human-owned: the *route* is still "a person decides".
    ``allowed_next_commands`` carries at most the one exact rerun command that
    regenerates this same evidence after committing, and is empty when the
    caller has no exact command to offer.

    ``permissions`` deliberately has **no default**. Every other variant keeps
    one so that a pre-contract-20 artifact still parses, but this state cannot
    be legacy input — it did not exist before v20 — so an omitted permission
    vector is a malformed current payload, not an old one, and must fail closed
    rather than silently synthesize publication authority.
    """

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
                # Required *only* here. Every other variant leaves it optional
                # so a pre-contract-20 artifact — which has no vector at all —
                # keeps validating against the same schema identifiers it was
                # emitted under. This state did not exist then, so requiring it
                # cannot break an old payload, and omitting it on a new one is
                # a malformed grant of publication authority.
                "permissions",
                "human_review",
                "stop_reason",
            ]
        },
    )

    state: Literal["review_publishable"]
    completion_allowed: Literal[False] = False
    must_stop: Literal[False] = False
    verify_required: bool = False
    next_action: HumanReviewAction
    allowed_next_commands: list[ExactCommand] = Field(default_factory=list, max_length=1)
    permissions: PublishOnlyPermissions
    human_review: RequiredHumanReview
    stop_reason: None = None


class HumanReviewRequiredControl(_AgentControlBase):
    """Stopping state: no further coding-agent action is authorized.

    Reserved for results Shipgate cannot vouch for — a policy block, untrusted
    or unreadable input, or an evaluation that did not complete. Publication is
    denied here precisely because there is no trustworthy evidence to publish.
    """

    state: Literal["human_review_required"]
    completion_allowed: Literal[False] = False
    must_stop: Literal[True] = True
    verify_required: bool = False
    next_action: HumanControlAction
    allowed_next_commands: list[ExactCommand] = Field(default_factory=list, max_length=0)
    permissions: NoAgentPermissions = Field(default_factory=NoAgentPermissions)
    human_review: RequiredHumanReview
    stop_reason: NonEmptyText


type AgentControl = Annotated[
    CompleteAgentControl
    | AgentActionRequiredControl
    | ReviewPublishableControl
    | HumanReviewRequiredControl,
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
    """Read v1 control fields fail-closed into the canonical union.

    A pre-contract-20 payload carried one universal ``must_stop`` with no way
    to say whether publication was safe, so every human route it describes
    normalizes to ``human_review_required``.  ``review_publishable`` is only
    ever produced by a current emitter that asserted the publication fact.
    """

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
                permissions=(
                    NoAgentPermissions()
                    if mapped_kind in UNEVALUATED_ACTION_KINDS
                    else PublishOnlyPermissions()
                ),
                human_review=NoHumanReview(),
            )
        except ValidationError:
            return _legacy_human_fallback("Legacy control contains an invalid coding-agent action.")

    if payload.get("completion_allowed") is True and payload.get("must_stop") is not True:
        return CompleteAgentControl(state="complete", reason=reason)
    return _legacy_human_fallback("Legacy control is contradictory or cannot be resolved safely.")


def project_legacy_agent_control(control: AgentControl) -> dict[str, Any]:
    """Render a control object in the pre-contract-20 shape.

    Frozen compatibility surfaces — currently the deprecated
    ``shipgate.codex_boundary_result/v2`` projection, whose published JSON
    Schema forbids unknown properties and knows only three states — must keep
    receiving exactly the fields and states they were promised.

    The projection is deliberately *restrictive*: ``permissions`` is dropped,
    and ``review_publishable`` collapses back onto ``human_review_required``
    with the universal stop. A consumer that has not been taught the
    publication/merge split therefore keeps the conservative reading it always
    had, and can never mistake the absence of ``permissions`` for permission.
    """

    payload = control.model_dump(mode="json")
    payload.pop("permissions", None)
    if payload.get("state") != "review_publishable":
        return payload
    review = control.human_review
    stop_reason = getattr(review, "why", None) or control.reason
    payload.update(
        {
            "state": "human_review_required",
            "must_stop": True,
            "allowed_next_commands": [],
            "stop_reason": stop_reason,
        }
    )
    return payload


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
    "FullAgentPermissions",
    "UNEVALUATED_ACTION_KINDS",
    "HumanActionKind",
    "HumanControlAction",
    "HumanReviewAction",
    "HumanReviewRequiredControl",
    "NoAgentPermissions",
    "NoHumanReview",
    "PublishOnlyPermissions",
    "RequiredHumanReview",
    "ReviewPublishableControl",
    "normalize_legacy_agent_control",
    "project_legacy_agent_control",
    "validate_agent_control",
]
