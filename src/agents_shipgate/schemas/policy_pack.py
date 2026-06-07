from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, RootModel

from agents_shipgate.schemas.common import Confidence, Severity
from agents_shipgate.schemas.surfaces import ActionEffect

POLICY_PACK_SCHEMA_VERSION = "0.1"


class PolicyPackParameterMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    names: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    missing_maximum: bool | None = None
    required: bool | None = None


class PolicyPackCapabilityMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_names: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    effects: list[ActionEffect] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    broad_scope: bool | None = None
    externally_visible: bool | None = None
    handles_sensitive_data: bool | None = None
    financial: bool | None = None
    code_execution: bool | None = None
    high_risk: bool | None = None
    auth_types: list[str] = Field(default_factory=list)
    credential_modes: list[str] = Field(default_factory=list)
    missing_owner: bool | None = None
    missing_auth_scopes: bool | None = None
    missing_approval_policy: bool | None = None
    missing_confirmation_policy: bool | None = None
    missing_idempotency_policy: bool | None = None
    parameters: list[PolicyPackParameterMatch] = Field(default_factory=list)


class PolicyPackMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_tags: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    environment_targets: list[str] = Field(default_factory=list)
    missing_owner: bool | None = None
    missing_auth_scopes: bool | None = None
    missing_approval_policy: bool | None = None
    missing_confirmation_policy: bool | None = None
    missing_idempotency_policy: bool | None = None
    parameters: list[PolicyPackParameterMatch] = Field(default_factory=list)
    capability: PolicyPackCapabilityMatch | None = None


class PolicyPackRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    description: str | None = None
    category: str = "policy_pack"
    severity: Severity
    block: bool = False
    confidence: Confidence = "medium"
    recommendation: str
    match: PolicyPackMatch


class PolicyPackFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    version: str | None = None
    rules: list[PolicyPackRule]


class PolicyPackArtifactV1(RootModel[PolicyPackFile]):
    root: PolicyPackFile
