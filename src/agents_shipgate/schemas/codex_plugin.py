from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.common import Confidence


class CodexPluginSourceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str | None = None
    source_path: str | None = None
    source_pointer: str | None = None
    source_start_line: int | None = None
    source_start_column: int | None = None


class CodexPluginSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    name: str
    root_path: str
    manifest_path: str
    version: str | None = None
    description: str | None = None
    marketplace: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    name_mismatch: bool = False
    duplicate_root: bool = False
    duplicate_name: bool = False
    location: CodexPluginSourceLocation = Field(
        default_factory=CodexPluginSourceLocation
    )


class CodexPluginMarketplaceSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    name: str | None = None
    path: str
    plugin_count: int = 0
    missing_policy_entries: list[dict[str, Any]] = Field(default_factory=list)
    skipped_entries: list[dict[str, Any]] = Field(default_factory=list)


class CodexPluginSkillSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugin: str
    name: str | None = None
    description: str | None = None
    path: str
    missing_fields: list[str] = Field(default_factory=list)
    duplicate: bool = False
    location: CodexPluginSourceLocation = Field(
        default_factory=CodexPluginSourceLocation
    )


class CodexPluginAppSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugin: str
    name: str
    connector_id: str | None = None
    path: str
    location: CodexPluginSourceLocation = Field(
        default_factory=CodexPluginSourceLocation
    )


class CodexPluginMcpServerStub(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugin: str
    server: str
    path: str
    command: str | None = None
    inventory_path: str | None = None
    inventory_loaded: bool = False
    location: CodexPluginSourceLocation = Field(
        default_factory=CodexPluginSourceLocation
    )


class CodexPluginHookStub(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugin: str
    name: str
    command: str
    path: str
    risk_tags: list[str] = Field(default_factory=lambda: ["code_execution"])
    confidence: Confidence = "medium"
    location: CodexPluginSourceLocation = Field(
        default_factory=CodexPluginSourceLocation
    )


class CodexPluginComponentPathIssue(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugin: str | None = None
    component: str
    path: str
    reason: str
    source_ref: str | None = None


class CodexPluginSurface(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugin_count: int = 0
    marketplace_count: int = 0
    skill_count: int = 0
    app_count: int = 0
    mcp_server_stub_count: int = 0
    hook_stub_count: int = 0
    mcp_inventory_file_count: int = 0
    plugins: list[CodexPluginSummary] = Field(default_factory=list)
    marketplaces: list[CodexPluginMarketplaceSummary] = Field(default_factory=list)
    skills: list[CodexPluginSkillSummary] = Field(default_factory=list)
    apps: list[CodexPluginAppSummary] = Field(default_factory=list)
    mcp_server_stubs: list[CodexPluginMcpServerStub] = Field(default_factory=list)
    hook_stubs: list[CodexPluginHookStub] = Field(default_factory=list)
    mcp_inventory_files: list[str] = Field(default_factory=list)
    component_path_issues: list[CodexPluginComponentPathIssue] = Field(
        default_factory=list
    )
    warnings: list[str] = Field(default_factory=list)
