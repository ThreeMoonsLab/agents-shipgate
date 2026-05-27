from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, model_validator

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


class ActionDeclarationConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    tool: str
    id: str | None = None
    provider: str | None = None
    operation: str | None = None
    effect: ActionEffect | None = None
    risk_tags: list[ActionRiskTag] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    approval: ActionApprovalConfig | None = None
    safeguards: ActionSafeguardsConfig | None = None
    evidence: ActionEvidenceConfig | None = None


class ActionPolicyMatchConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    action_ids: list[str] = Field(default_factory=list)
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
                    "action_surface.policies.require "
                    f"{raw_path!r} must be a boolean value"
                )
            if path in _ACTION_REQUIRE_STR_PATHS and not isinstance(expected, str):
                raise ValueError(
                    "action_surface.policies.require "
                    f"{raw_path!r} must be a string value"
                )
            if path in _ACTION_REQUIRE_STR_LIST_PATHS and not _is_string_list(expected):
                raise ValueError(
                    "action_surface.policies.require "
                    f"{raw_path!r} must be a list of strings"
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
        _raise_on_duplicates(
            [action.tool for action in self.actions],
            "action_surface.actions[].tool",
        )
        explicit_ids = [action.id for action in self.actions if action.id]
        _raise_on_duplicates(explicit_ids, "action_surface.actions[].id")
        return self
