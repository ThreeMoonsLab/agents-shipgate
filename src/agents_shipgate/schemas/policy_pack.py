from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.common import Confidence, Severity


class PolicyPackParameterMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    names: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    missing_maximum: bool | None = None
    required: bool | None = None


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
