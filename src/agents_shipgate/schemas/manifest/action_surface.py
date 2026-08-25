from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest._authority import (
    validate_authority_co_requirements,
    validate_authority_scopes,
)
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG
from agents_shipgate.schemas.text import (
    VISIBLE_CONTENT_PATTERN,
    has_visible_content,
)

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


#: The reviewed modes an action row may declare. The same vocabulary the
#: shared validator advertises as ``AUTHORITY_MODE_VALUES``; pinned equal by
#: ``test_both_authority_sites_share_one_vocabulary`` so a mode added to one
#: spelling is added to both.
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


class ActionEffectOverrideConfig(BaseModel):
    """Reviewed acknowledgement that a declared effect sits below evidence.

    A declaration may freely *escalate* past what the scanner inferred — a
    reviewer calling an action more dangerous than the evidence proves needs no
    ceremony. De-escalating past inferred evidence is the asymmetric case: it
    is the one edit that converts a gapped action into a pass-eligible one, so
    it may not be silent. This block is how a reviewer says "I looked, and the
    inference does not apply here"; it never removes the review row, it only
    marks it acknowledged (#409).

    It acknowledges *inferred* evidence only. Where policy-eligible evidence
    outranks the declaration the conflict is blocking and this block changes
    nothing about it.
    """

    model_config = STRICT_MODEL_CONFIG

    # ``pattern`` is carried so the *published* JSON Schema rejects what the
    # runtime rejects: this file is advertised for live editor validation, and
    # a schema that accepts a manifest the CLI refuses is worse than no schema
    # (PR #411 review 4). The validator below stays the authority — it also
    # covers surrogate, private-use, and unassigned code points, which no
    # portable character class can enumerate.
    #: What the reviewer actually checked — the function body, the deployment,
    #: the upstream contract. Named so the next reviewer can re-check it.
    evidence: str = Field(pattern=VISIBLE_CONTENT_PATTERN)
    #: Why the inferred evidence does not establish the stronger effect here.
    reason: str = Field(pattern=VISIBLE_CONTENT_PATTERN)

    @field_validator("evidence", "reason")
    @classmethod
    def require_visible_content(cls, value: str) -> str:
        # ``strip()`` is not the question. A reason made only of U+200B and
        # U+2060 survives it, renders as nothing to the reviewer this block
        # exists for, and would still suppress the mismatch and restore
        # pass-eligibility (PR #411 review 3). The test is whether a reader
        # sees anything at all: whitespace, controls, bidi marks, and every
        # Default_Ignorable code point render as nothing on their own.
        if not has_visible_content(value):
            raise ValueError(
                "action_surface.actions[].override requires evidence and reason "
                "with visible content"
            )
        return value.strip()


class ActionDeclarationConfig(BaseModel):
    # ``validate_override`` is a cross-field rule, which Pydantic cannot derive
    # into JSON Schema. Published explicitly so an editor validating live gives
    # the same answer the CLI does: an ``override`` with no ``effect``
    # acknowledges a claim that was never made.
    model_config = {
        **STRICT_MODEL_CONFIG,
        "json_schema_extra": {
            "allOf": [
                {
                    "if": {
                        "required": ["override"],
                        "properties": {"override": {"not": {"type": "null"}}},
                    },
                    "then": {
                        "required": ["effect"],
                        "properties": {"effect": {"not": {"type": "null"}}},
                    },
                }
            ]
        },
    }

    tool: str
    tool_id: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    id: str | None = None
    provider: str | None = None
    operation: str | None = None
    effect: ActionEffect | None = None
    risk_tags: list[ActionRiskTag] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    authority: ActionAuthorityConfig | None = None
    override: ActionEffectOverrideConfig | None = None
    approval: ActionApprovalConfig | None = None
    safeguards: ActionSafeguardsConfig | None = None
    evidence: ActionEvidenceConfig | None = None

    @field_validator("scopes")
    @classmethod
    def validate_concrete_scopes(cls, scopes: list[str]) -> list[str]:
        return validate_authority_scopes(scopes, label="action_surface.actions[].scopes")

    @model_validator(mode="after")
    def validate_override(self) -> ActionDeclarationConfig:
        # An override acknowledges a *declared* effect that sits below inferred
        # evidence. With no declared effect there is nothing to de-escalate, so
        # the block would read as an acknowledgement of a claim never made.
        if self.override is not None and self.effect is None:
            raise ValueError(
                "action_surface.actions[].override requires a declared effect to acknowledge"
            )
        return self

    @model_validator(mode="after")
    def validate_authority(self) -> ActionDeclarationConfig:
        authority = self.authority
        if authority is None:
            return self
        # ``scopes`` is the action row's sibling field, not part of the
        # authority mapping, so it is passed in rather than read from the
        # block. The rule is the same one ``tool_sources[].authority`` obeys.
        validate_authority_co_requirements(
            mode=authority.mode,
            auth_type=authority.auth_type,
            scopes=self.scopes,
            reason=authority.reason,
            mode_label="action_surface.actions[].authority.mode",
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
