from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

HOST_GRANTS_INVENTORY_SCHEMA_VERSION = "0.2"
HOST_GRANTS_BASELINE_SCHEMA_VERSION = "0.2"
HOST_GRANTS_DRIFT_SCHEMA_VERSION = "0.2"

HostName = Literal["codex", "claude-code", "cursor", "vscode", "github"]
HostGrantScope = Literal["repository", "local_static"]


# Frozen v0.1 models. They remain readable but are never emitted by current code.
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
    model_config = ConfigDict(extra="forbid")

    host_grants_inventory_schema_version: Literal["0.1"] = "0.1"
    workspace: str
    mcp_servers: list[HostMcpServerGrantV1] = Field(default_factory=list)
    permission_rules: list[HostPermissionRuleGrantV1] = Field(default_factory=list)
    hooks: list[HostHookGrantV1] = Field(default_factory=list)
    workflows: list[HostWorkflowGrantV1] = Field(default_factory=list)
    codex_config_present: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)


class HostGrantsInventoryArtifactV1(RootModel[HostGrantsInventoryV1]):
    root: HostGrantsInventoryV1


class HostArtifactV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    host: HostName
    scope: HostGrantScope
    path: str
    kind: Literal[
        "config", "mcp", "hooks", "workflow", "instructions", "requirements"
    ]
    parse_status: Literal["parsed", "failed", "unsupported"]
    redacted_sha256: str | None = None


class HostCoverageV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: HostName
    scope: HostGrantScope
    status: Literal["complete", "partial", "experimental"]
    sources_expected: list[str] = Field(default_factory=list)
    sources_observed: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)


class HostInventoryIssueV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    kind: Literal[
        "parse_failed",
        "unreadable",
        "unsupported",
        "dynamic_source_excluded",
        "remote_source_excluded",
    ]
    host: HostName
    source: str
    message: str
    blocking: bool


class HostGrantBaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str
    host: HostName
    scope: HostGrantScope
    source: str
    config_sha256: str
    access: Literal["none", "read", "write", "execute", "external", "admin", "unknown"]
    risk: Literal["none", "low", "medium", "high", "critical", "unknown"]


class HostMcpServerGrantV2(HostGrantBaseV2):
    kind: Literal["mcp_server"] = "mcp_server"
    server: str
    transport: str
    endpoint: str | None = None
    env_keys: list[str] = Field(default_factory=list)
    header_keys: list[str] = Field(default_factory=list)


class HostPermissionRuleGrantV2(HostGrantBaseV2):
    kind: Literal["permission_rule"] = "permission_rule"
    disposition: Literal["allow", "ask", "deny"]
    rule: str
    wildcard: bool = False


class HostPermissionModeGrantV2(HostGrantBaseV2):
    kind: Literal["permission_mode"] = "permission_mode"
    setting: str
    value: str


class HostHookGrantV2(HostGrantBaseV2):
    kind: Literal["hook"] = "hook"
    event: str


class HostSandboxGrantV2(HostGrantBaseV2):
    kind: Literal["sandbox"] = "sandbox"
    setting: str
    value: str


class HostAdditionalPathGrantV2(HostGrantBaseV2):
    kind: Literal["additional_path"] = "additional_path"
    path: str


class HostPluginGrantV2(HostGrantBaseV2):
    kind: Literal["plugin_or_app"] = "plugin_or_app"
    name: str
    enabled: bool | None = None


class HostWorkflowGrantV2(HostGrantBaseV2):
    kind: Literal["workflow"] = "workflow"
    triggers: list[str] = Field(default_factory=list)
    pull_request_target: bool = False
    write_all: bool = False
    write_scopes: list[str] = Field(default_factory=list)


class HostInstructionGrantV2(HostGrantBaseV2):
    kind: Literal["instruction_trust_root"] = "instruction_trust_root"
    path: str


HostGrantV2 = Annotated[
    HostMcpServerGrantV2
    | HostPermissionRuleGrantV2
    | HostPermissionModeGrantV2
    | HostHookGrantV2
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostWorkflowGrantV2
    | HostInstructionGrantV2,
    Field(discriminator="kind"),
]


class HostGrantsInventoryV2(BaseModel):
    """Typed, redacted static inventory emitted by ``audit --host``."""

    model_config = ConfigDict(extra="forbid")

    host_grants_inventory_schema_version: Literal["0.2"] = (
        HOST_GRANTS_INVENTORY_SCHEMA_VERSION
    )
    workspace: str
    scope: HostGrantScope = "repository"
    artifacts: list[HostArtifactV2] = Field(default_factory=list)
    host_coverage: list[HostCoverageV2] = Field(default_factory=list)
    grants: list[HostGrantV2] = Field(default_factory=list)
    issues: list[HostInventoryIssueV2] = Field(default_factory=list)
    excluded_scopes: list[str] = Field(default_factory=list)
    static_analysis_only: Literal[True] = True
    runtime_session_verified: Literal[False] = False


class HostGrantsBaselineV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_grants_schema_version: Literal["0.2"] = HOST_GRANTS_BASELINE_SCHEMA_VERSION
    scope: HostGrantScope
    inventory_sha256: str
    inventory: dict[str, Any]


class HostGrantChangeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str
    baseline: dict[str, Any] | None = None
    current: dict[str, Any] | None = None


class HostArtifactChangeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    baseline: dict[str, Any] | None = None
    current: dict[str, Any] | None = None


class HostGrantsDriftV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_grants_schema_version: Literal["0.2"] = HOST_GRANTS_DRIFT_SCHEMA_VERSION
    baseline_file: str
    scope: HostGrantScope
    comparison_status: Literal["comparable", "incomparable"]
    baseline_sha256: str | None = None
    current_sha256: str | None = None
    has_drift: bool | None
    changes: list[HostGrantChangeV2] = Field(default_factory=list)
    artifact_changes: list[HostArtifactChangeV2] = Field(default_factory=list)
    expansion_signals: list[str] = Field(default_factory=list)
    issues: list[HostInventoryIssueV2] = Field(default_factory=list)
    incomparable_reasons: list[str] = Field(default_factory=list)
    next_action: str | None = None


class HostGrantsInventoryArtifactV2(RootModel[HostGrantsInventoryV2]):
    root: HostGrantsInventoryV2


class HostGrantsBaselineArtifactV2(RootModel[HostGrantsBaselineV2]):
    root: HostGrantsBaselineV2


class HostGrantsDriftArtifactV2(RootModel[HostGrantsDriftV2]):
    root: HostGrantsDriftV2


# Frozen, permissive drift reader retained for pre-0.2 artifacts.
class HostGrantsDriftV1(BaseModel):
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


class HostGrantsDriftArtifactV1(RootModel[HostGrantsDriftV1]):
    root: HostGrantsDriftV1


__all__ = [name for name in globals() if name.startswith("Host") or name.startswith("HOST_")]
