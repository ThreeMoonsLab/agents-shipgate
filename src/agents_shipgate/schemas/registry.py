from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

REGISTRY_SCHEMA_VERSION = "0.3"


class RegistryRowV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    registry_schema_version: str = REGISTRY_SCHEMA_VERSION
    row_id: str
    repo: str = ""
    org_id: str | None = None
    service: str | None = None
    tier: str | None = None
    pr_number: str | None = None
    workflow_run_id: str | None = None
    actor: str | None = None
    merge_sha: str | None = None
    event_time: str | None = None
    source_url: str | None = None
    branch: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    attestation_schema_version: str | None = None
    cli_version: str | None = None
    run_id: str | None = None
    source_attestation_sha256: str | None = None
    source_verify_run_sha256: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    base_tree_sha: str | None = None
    head_tree_sha: str | None = None
    merge_verdict: str | None = None
    decision: str | None = None
    can_merge_without_human: bool | None = None
    capability_added: int | None = None
    capability_modified: int | None = None
    capability_removed: int | None = None
    capability_change_ids: list[str] = Field(default_factory=list)
    trust_root_touched: bool | None = None
    policy_weakened: bool | None = None
    human_ack_required: bool | None = None
    human_ack_satisfied: bool | None = None
    human_ack_outstanding: list[str] = Field(default_factory=list)
    human_ack: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot_sha256: str | None = None
    artifact_sha256: dict[str, str] = Field(default_factory=dict)
    capability_lock: dict[str, Any] = Field(default_factory=dict)
    capability_diff: dict[str, Any] | None = None
    policy_packs: list[dict[str, Any]] = Field(default_factory=list)
    previous_row_id: str | None = None
    previous_row_sha256: str | None = None


class RegistrySkippedRowV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1)
    reason: str


class RegistryQueryResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.3"] = REGISTRY_SCHEMA_VERSION
    registry: str
    count: int
    skipped_count: int = 0
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)
    rows: list[RegistryRowV1] = Field(default_factory=list)


class RegistryBypassReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.3"] = REGISTRY_SCHEMA_VERSION
    registry: str
    bypass_count: int
    skipped_count: int = 0
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)
    rows: list[RegistryRowV1] = Field(default_factory=list)


class RegistrySummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.3"] = REGISTRY_SCHEMA_VERSION
    registry: str
    count: int
    skipped_count: int = 0
    by_merge_verdict: dict[str, int] = Field(default_factory=dict)
    by_decision: dict[str, int] = Field(default_factory=dict)
    by_repo: dict[str, int] = Field(default_factory=dict)
    by_service: dict[str, int] = Field(default_factory=dict)
    by_tier: dict[str, int] = Field(default_factory=dict)
    bypass_count: int = 0
    trust_root_touched_count: int = 0
    policy_weakened_count: int = 0
    human_ack_required_count: int = 0
    human_ack_unsatisfied_count: int = 0
    policy_pack_unverified_count: int = 0
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)


class RegistryVerificationIssueV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str | None = None
    repo: str = ""
    kind: str
    message: str


class RegistryVerificationResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.3"] = REGISTRY_SCHEMA_VERSION
    registry: str
    row_count: int
    skipped_count: int = 0
    issue_count: int = 0
    ok: bool = True
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)
    issues: list[RegistryVerificationIssueV1] = Field(default_factory=list)


__all__ = [
    "REGISTRY_SCHEMA_VERSION",
    "RegistryBypassReportV1",
    "RegistryQueryResultV1",
    "RegistryRowV1",
    "RegistrySkippedRowV1",
    "RegistrySummaryV1",
    "RegistryVerificationIssueV1",
    "RegistryVerificationResultV1",
]
