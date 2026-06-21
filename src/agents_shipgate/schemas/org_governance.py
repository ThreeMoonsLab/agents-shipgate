from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ORG_GOVERNANCE_SCHEMA_VERSION = "0.1"

ExceptionKind = Literal[
    "baseline",
    "suppression",
    "severity_override",
    "override_acknowledgement",
    "human_ack",
    "host_grant_baseline",
]


class ExceptionRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ExceptionKind
    source_ref: str
    owner: str | None = None
    reason: str | None = None
    expires: str | None = None
    recorded_at: str | None = None
    subject: str | None = None
    check_id: str | None = None
    fingerprint: str | None = None
    violations: list[str] = Field(default_factory=list)


class PolicyPackPinRecordV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    version: str | None = None
    owner: str | None = None
    rule_count: int | None = None
    path: str
    source: str | None = None
    sha256: str | None = None
    actual_sha256: str | None = None
    status: Literal["unpinned", "verified", "mismatch", "missing", "unreadable"]
    violations: list[str] = Field(default_factory=list)


class OrgGovernanceSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exception_count: int = 0
    exception_violation_count: int = 0
    policy_pack_count: int = 0
    policy_pack_violation_count: int = 0
    host_grant_drift: bool = False
    registry_configured: bool = False


class OrgGovernanceStatusV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_governance_schema_version: Literal["0.1"] = ORG_GOVERNANCE_SCHEMA_VERSION
    organization: dict[str, Any] | None = None
    config: str
    as_of: str
    summary: OrgGovernanceSummaryV1
    exceptions: list[ExceptionRecordV1] = Field(default_factory=list)
    policy_packs: list[PolicyPackPinRecordV1] = Field(default_factory=list)
    host_grant_drift: dict[str, Any] | None = None
    registry: dict[str, Any] | None = None
    violations: list[dict[str, str]] = Field(default_factory=list)


class OrgPolicyPacksStatusV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_governance_schema_version: Literal["0.1"] = ORG_GOVERNANCE_SCHEMA_VERSION
    config: str
    policy_pack_count: int = 0
    policy_pack_violation_count: int = 0
    policy_packs: list[PolicyPackPinRecordV1] = Field(default_factory=list)
    violations: list[dict[str, str]] = Field(default_factory=list)


__all__ = [
    "ORG_GOVERNANCE_SCHEMA_VERSION",
    "ExceptionKind",
    "ExceptionRecordV1",
    "OrgGovernanceStatusV1",
    "OrgGovernanceSummaryV1",
    "OrgPolicyPacksStatusV1",
    "PolicyPackPinRecordV1",
]
