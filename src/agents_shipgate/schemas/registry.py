from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

REGISTRY_SCHEMA_VERSION = "0.2"


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
    attestation_schema_version: str | None = None
    cli_version: str | None = None
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


class RegistryQueryResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.2"] = REGISTRY_SCHEMA_VERSION
    registry: str
    count: int
    rows: list[RegistryRowV1] = Field(default_factory=list)


class RegistryBypassReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.2"] = REGISTRY_SCHEMA_VERSION
    registry: str
    bypass_count: int
    rows: list[RegistryRowV1] = Field(default_factory=list)


__all__ = [
    "REGISTRY_SCHEMA_VERSION",
    "RegistryBypassReportV1",
    "RegistryQueryResultV1",
    "RegistryRowV1",
]
