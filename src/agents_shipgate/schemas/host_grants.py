from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

HOST_GRANTS_INVENTORY_SCHEMA_VERSION = "0.1"


class HostMcpServerGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    file: str
    server: str
    transport: str
    command_or_url: str | None = None
    env_keys: list[str] = Field(default_factory=list)
    config_sha256: str


class HostPermissionRuleGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    kind: str
    rule: str
    wildcard: bool = False


class HostHookGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    event: str
    config_sha256: str


class HostWorkflowGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    triggers: list[str] = Field(default_factory=list)
    pull_request_target: bool = False
    write_all: bool = False
    write_scopes: list[str] = Field(default_factory=list)


class HostGrantsInventoryV1(BaseModel):
    """Versioned host-grant inventory emitted by ``agents-shipgate audit --host``."""

    model_config = ConfigDict(extra="forbid")

    host_grants_inventory_schema_version: Literal["0.1"] = (
        HOST_GRANTS_INVENTORY_SCHEMA_VERSION
    )
    workspace: str
    mcp_servers: list[HostMcpServerGrantV1] = Field(default_factory=list)
    permission_rules: list[HostPermissionRuleGrantV1] = Field(default_factory=list)
    hooks: list[HostHookGrantV1] = Field(default_factory=list)
    workflows: list[HostWorkflowGrantV1] = Field(default_factory=list)
    codex_config_present: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)


class HostGrantsDriftV1(BaseModel):
    """Versioned drift payload emitted by ``audit --host --drift --json``."""

    model_config = ConfigDict(extra="forbid")

    host_grants_schema_version: str
    baseline_file: str
    baseline_sha256: str | None = None
    current_sha256: str | None = None
    has_drift: bool
    drift: dict[str, Any] = Field(default_factory=dict)
    expansion_signals: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)
    load_error: str | None = None


class HostGrantsInventoryArtifactV1(RootModel[HostGrantsInventoryV1]):
    root: HostGrantsInventoryV1


class HostGrantsDriftArtifactV1(RootModel[HostGrantsDriftV1]):
    root: HostGrantsDriftV1


__all__ = [
    "HOST_GRANTS_INVENTORY_SCHEMA_VERSION",
    "HostGrantsDriftArtifactV1",
    "HostGrantsDriftV1",
    "HostGrantsInventoryArtifactV1",
    "HostGrantsInventoryV1",
    "HostHookGrantV1",
    "HostMcpServerGrantV1",
    "HostPermissionRuleGrantV1",
    "HostWorkflowGrantV1",
]
