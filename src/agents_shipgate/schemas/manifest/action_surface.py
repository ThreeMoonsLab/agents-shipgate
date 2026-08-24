from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from agents_shipgate.schemas._text import (
    has_visible_content,
    unsafe_prose_characters,
)
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG

ActionEffect = Literal[
    "read",
    "write",
    "destructive",
    "external_communication",
    "financial_write",
    "production_operation",
    "privileged_data_access",
    "code_execution",
    "identity_access",
]
ActionRiskTag = Literal[
    "read_only",
    "write",
    "writes_data",
    "destructive",
    "external_write",
    "external_communication",
    "customer_communication",
    "financial_action",
    "financial_write",
    "external_side_effect",
    "infrastructure_change",
    "production_operation",
    "production_ops",
    "sensitive_data_access",
    "privileged_data_access",
    "privileged_data",
    "code_execution",
    "identity_access",
    "network_access",
    "filesystem_write",
    "customer_data",
    "secret_access",
    "irreversible",
]
_ACTION_EFFECT_VALUES = set(get_args(ActionEffect))
_ACTION_REQUIRE_ALIASES = {
    "approval.required": "approval_policy.required",
    "approval.threshold": "approval_policy.threshold",
    "scopes": "required_scopes",
}
_ACTION_REQUIRE_BOOL_PATHS = {
    "approval_policy.required",
    "safeguards.idempotency",
    "safeguards.audit_log",
    "safeguards.rollback",
    "safeguards.dry_run",
}
_ACTION_REQUIRE_STR_PATHS = {
    "action_id",
    "agent_id",
    "tool_id",
    "tool_name",
    "provider",
    "source_type",
    "source_id",
    "operation",
    "approval_policy.threshold",
    "evidence.owner",
    "evidence.runbook",
    "evidence.approval_ticket",
    "input_schema_hash",
    "hashes.identity_hash",
    "hashes.schema_hash",
    "hashes.policy_hash",
    "hashes.risk_hash",
}
_ACTION_REQUIRE_STR_LIST_PATHS = {
    "risk_tags",
    "required_scopes",
    "input_fields",
    "required_input_fields",
}


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _raise_on_duplicates(values: list[str | None], label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        joined = ", ".join(repr(value) for value in sorted(duplicates))
        raise ValueError(f"Duplicate {label}: {joined}")


class ActionApprovalConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    required: bool | None = None
    threshold: str | None = None


class ActionSafeguardsConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    idempotency: bool | None = None
    audit_log: bool | None = None
    rollback: bool | None = None
    dry_run: bool | None = None


class ActionEvidenceConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    owner: str | None = None
    runbook: str | None = None
    approval_ticket: str | None = None


ActionAuthorityMode = Literal["none", "scoped", "unscoped", "ambient"]


class ActionAuthorityConfig(BaseModel):
    """Reviewed authority evidence for one declared action.

    Scopes intentionally remain on ``ActionDeclarationConfig.scopes`` so
    there is one canonical permission list in the manifest.
    """

    model_config = STRICT_MODEL_CONFIG

    mode: ActionAuthorityMode
    auth_type: str | None = None
    credential_mode: str | None = None
    reason: str | None = None


class ActionOverrideConfig(BaseModel):
    """A recorded, reviewed de-escalation below the evidence Shipgate observed.

    Declarations may freely agree with or escalate above derived evidence; that
    stays silent. Declaring an effect *below* an inferred observation is the
    one direction that needs a record, because it is the path of least
    resistance for anyone the gate is blocking (#409).

    ``evidence`` lists the inferred effect values this declaration overrides,
    and it must name exactly the values currently observed above the declared
    effect — no more, no less. Listing more would let one edit pre-acknowledge
    evidence that has not appeared yet and go permanently silent; listing fewer
    leaves the uncovered value unanswered. Both re-open the question, which is
    what keeps the override honest as the code underneath it changes.
    """

    model_config = STRICT_MODEL_CONFIG

    evidence: list[ActionEffect] = Field(min_length=1)
    reason: str

    @field_validator("evidence")
    @classmethod
    def validate_unique_evidence(cls, evidence: list[ActionEffect]) -> list[ActionEffect]:
        if len(set(evidence)) != len(evidence):
            raise ValueError(
                "action_surface.actions[].override.evidence must not repeat an effect"
            )
        return evidence

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, reason: str) -> str:
        # ``strip()`` is not the boundary this field needs. A reason made only
        # of U+200B survives it, and that reason then satisfies a requirement
        # whose entire purpose is for a human to read it — restoring pass
        # eligibility on a declaration nobody explained (PR #412 review).
        if not has_visible_content(reason):
            raise ValueError(
                "action_surface.actions[].override.reason must state why the declared "
                "effect is correct despite the observed evidence; a value with no "
                "visible characters explains nothing"
            )
        offenders = unsafe_prose_characters(reason)
        if offenders:
            raise ValueError(
                "action_surface.actions[].override.reason must not contain "
                f"invisible or direction-altering characters: {', '.join(offenders)}. "
                "This text is rendered to reviewers, and those code points can make "
                "it read as something it is not."
            )
        return reason.strip()


class ActionDeclarationConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    tool: str
    tool_id: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    id: str | None = None
    provider: str | None = None
    operation: str | None = None
    effect: ActionEffect | None = None
    override: ActionOverrideConfig | None = None
    risk_tags: list[ActionRiskTag] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    authority: ActionAuthorityConfig | None = None
    approval: ActionApprovalConfig | None = None
    safeguards: ActionSafeguardsConfig | None = None
    evidence: ActionEvidenceConfig | None = None

    @field_validator("scopes")
    @classmethod
    def validate_concrete_scopes(cls, scopes: list[str]) -> list[str]:
        normalized: list[str] = []
        for scope in scopes:
            value = scope.strip()
            if not value:
                raise ValueError(
                    "action_surface.actions[].scopes must contain concrete, non-blank scope strings"
                )
            normalized.append(value)
        return normalized

    @model_validator(mode="after")
    def validate_override(self) -> ActionDeclarationConfig:
        override = self.override
        if override is None:
            return self
        if self.effect is None:
            raise ValueError(
                "action_surface.actions[].override requires effect; an override with "
                "no declared effect overrides nothing"
            )
        if self.effect in override.evidence:
            raise ValueError(
                "action_surface.actions[].override.evidence must not repeat the declared "
                f"effect {self.effect!r}; it lists the evidence being overridden"
            )
        return self

    @model_validator(mode="after")
    def validate_authority(self) -> ActionDeclarationConfig:
        authority = self.authority
        if authority is None:
            return self

        auth_type = (authority.auth_type or "").strip()
        reason = (authority.reason or "").strip()
        if authority.mode == "none":
            if self.scopes:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'none' requires empty scopes"
                )
            if auth_type:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'none' requires no auth_type"
                )
        elif authority.mode == "scoped":
            if not auth_type:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'scoped' requires auth_type"
                )
            if not self.scopes:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'scoped' requires non-empty scopes"
                )
        elif authority.mode == "unscoped":
            if not auth_type:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'unscoped' requires auth_type"
                )
            if self.scopes:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'unscoped' requires empty scopes"
                )
            if not reason:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'unscoped' requires reason"
                )
        elif authority.mode == "ambient":
            if self.scopes:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'ambient' requires empty scopes"
                )
            if not reason:
                raise ValueError(
                    "action_surface.actions[].authority.mode 'ambient' requires reason"
                )
        return self


class ActionPolicyMatchConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    action_ids: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    effects: list[ActionEffect] = Field(default_factory=list)
    risk_tags: list[ActionRiskTag] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


class ActionPolicyConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    id: str
    match: ActionPolicyMatchConfig = Field(default_factory=ActionPolicyMatchConfig)
    require: dict[str, Any] = Field(default_factory=dict)
    severity: Severity
    block: bool = True
    message: str | None = None
    recommendation: str | None = None

    @model_validator(mode="after")
    def validate_require_value_types(self) -> ActionPolicyConfig:
        for raw_path, expected in self.require.items():
            path = _ACTION_REQUIRE_ALIASES.get(raw_path, raw_path)
            if expected is None:
                continue
            if path in _ACTION_REQUIRE_BOOL_PATHS and type(expected) is not bool:
                raise ValueError(
                    f"action_surface.policies.require {raw_path!r} must be a boolean value"
                )
            if path in _ACTION_REQUIRE_STR_PATHS and not isinstance(expected, str):
                raise ValueError(
                    f"action_surface.policies.require {raw_path!r} must be a string value"
                )
            if path in _ACTION_REQUIRE_STR_LIST_PATHS and not _is_string_list(expected):
                raise ValueError(
                    f"action_surface.policies.require {raw_path!r} must be a list of strings"
                )
            if path == "effect" and (
                not isinstance(expected, str) or expected not in _ACTION_EFFECT_VALUES
            ):
                raise ValueError(
                    "action_surface.policies.require "
                    f"{raw_path!r} must be one of {sorted(_ACTION_EFFECT_VALUES)}"
                )
        return self


class ActionSurfaceConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    require_explicit_actions: bool = False
    actions: list[ActionDeclarationConfig] = Field(default_factory=list)
    policies: list[ActionPolicyConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_action_declarations(self) -> ActionSurfaceConfig:
        selectors = [
            (
                action.tool,
                action.tool_id,
                action.provider,
                action.source_type,
                action.source_id,
            )
            for action in self.actions
        ]
        if len(set(selectors)) != len(selectors):
            raise ValueError("Duplicate action_surface.actions[] tool selectors")
        explicit_ids = [action.id for action in self.actions if action.id]
        _raise_on_duplicates(explicit_ids, "action_surface.actions[].id")
        return self
