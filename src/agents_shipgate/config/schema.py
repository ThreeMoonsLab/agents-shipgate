from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_shipgate.core.models import Severity

STRICT_MODEL_CONFIG = ConfigDict(extra="forbid")


class ProjectConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    name: str
    owner: str | None = None
    repo: str | None = None


class AgentSdkConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    type: str | None = None
    language: str | None = None
    entrypoint: str | None = None
    object: str | None = None
    static_extract: bool = True
    deep_import: bool = False


class AgentConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    name: str
    sdk: AgentSdkConfig | None = None
    declared_purpose: list[str] = Field(default_factory=list)
    instructions_preview: str | None = None
    prohibited_actions: list[str] = Field(default_factory=list)


class EnvironmentConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    target: Literal["local", "staging", "production_like", "production"]
    promotion_from: str | None = None
    promotion_to: str | None = None


class ToolSourceConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    id: str
    type: Literal[
        "mcp",
        "openapi",
        "openai_agents_sdk",
        "google_adk",
        "langchain",
        "crewai",
    ]
    path: str | None = None
    trust: str | None = None
    mode: str | None = None
    optional: bool = False

    @model_validator(mode="after")
    def require_path_when_needed(self) -> ToolSourceConfig:
        if self.type in {"mcp", "openapi", "google_adk", "langchain", "crewai"} and not self.path:
            raise ValueError(f"tool source {self.id!r} requires path")
        return self


class ArtifactPathConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    path: str
    optional: bool = False


class NamedArtifactPathConfig(ArtifactPathConfig):
    name: str | None = None
    downstream_critical_fields: list[str] = Field(default_factory=list)


def _parse_artifact_entries(value: Any) -> list[ArtifactPathConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("artifact entries must be a list")
    entries: list[ArtifactPathConfig] = []
    for item in value:
        if isinstance(item, ArtifactPathConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(ArtifactPathConfig(path=item))
        elif isinstance(item, dict):
            entries.append(ArtifactPathConfig.model_validate(item))
        else:
            raise TypeError("artifact entries must be strings or objects")
    return entries


def _parse_named_artifact_entries(value: Any) -> list[NamedArtifactPathConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("artifact entries must be a list")
    entries: list[NamedArtifactPathConfig] = []
    for item in value:
        if isinstance(item, NamedArtifactPathConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(NamedArtifactPathConfig(path=item))
        elif isinstance(item, dict):
            entries.append(NamedArtifactPathConfig.model_validate(item))
        else:
            raise TypeError("artifact entries must be strings or objects")
    return entries


class OpenAIApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    prompt_files: list[str] = Field(default_factory=list)
    tools: list[ArtifactPathConfig] = Field(default_factory=list)
    function_schemas: list[NamedArtifactPathConfig] = Field(default_factory=list)
    response_formats: list[NamedArtifactPathConfig] = Field(default_factory=list)
    api_model_config: ArtifactPathConfig | None = Field(default=None, alias="model_config")
    test_cases: list[ArtifactPathConfig] = Field(default_factory=list)
    trace_samples: list[ArtifactPathConfig] = Field(default_factory=list)
    policy_rules: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator("prompt_files", mode="before")
    @classmethod
    def parse_prompt_files(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("prompt_files must be a list")
        files: list[str] = []
        for item in value:
            if isinstance(item, str):
                files.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                files.append(item["path"])
            else:
                raise TypeError("prompt_files entries must be strings or objects with path")
        return files

    @field_validator("tools", "test_cases", "trace_samples", "policy_rules", mode="before")
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    @field_validator("function_schemas", "response_formats", mode="before")
    @classmethod
    def parse_named_artifacts(cls, value: Any) -> list[NamedArtifactPathConfig]:
        return _parse_named_artifact_entries(value)

    @field_validator("api_model_config", mode="before")
    @classmethod
    def parse_model_config(cls, value: Any) -> ArtifactPathConfig | None:
        if value is None:
            return None
        if isinstance(value, str):
            return ArtifactPathConfig(path=value)
        if isinstance(value, dict):
            return ArtifactPathConfig.model_validate(value)
        raise TypeError("model_config must be a string path or object with path")


class AnthropicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    prompt_files: list[str] = Field(default_factory=list)
    tools: list[ArtifactPathConfig] = Field(default_factory=list)
    policy_rules: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator("prompt_files", mode="before")
    @classmethod
    def parse_prompt_files(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("prompt_files must be a list")
        files: list[str] = []
        for item in value:
            if isinstance(item, str):
                files.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                files.append(item["path"])
            else:
                raise TypeError("prompt_files entries must be strings or objects with path")
        return files

    @field_validator("tools", "policy_rules", mode="before")
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any([self.prompt_files, self.tools, self.policy_rules])


class GoogleAdkConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    python_entrypoints: list[ArtifactPathConfig] = Field(default_factory=list)
    agent_configs: list[ArtifactPathConfig] = Field(default_factory=list)
    eval_sets: list[ArtifactPathConfig] = Field(default_factory=list)
    tool_inventories: list[ArtifactPathConfig] = Field(default_factory=list)
    trace_samples: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator(
        "python_entrypoints",
        "agent_configs",
        "eval_sets",
        "tool_inventories",
        "trace_samples",
        mode="before",
    )
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any(
            [
                self.python_entrypoints,
                self.agent_configs,
                self.eval_sets,
                self.tool_inventories,
                self.trace_samples,
            ]
        )


class LangChainConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    python_entrypoints: list[ArtifactPathConfig] = Field(default_factory=list)
    tool_inventories: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator("python_entrypoints", "tool_inventories", mode="before")
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any([self.python_entrypoints, self.tool_inventories])


class CrewAiConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    python_entrypoints: list[ArtifactPathConfig] = Field(default_factory=list)
    tool_inventories: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator("python_entrypoints", "tool_inventories", mode="before")
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any([self.python_entrypoints, self.tool_inventories])


class PolicyToolEntry(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    tool: str
    reason: str | None = None


def _parse_policy_entries(value: Any) -> list[PolicyToolEntry]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("policy value must be a list")
    entries: list[PolicyToolEntry] = []
    for item in value:
        if isinstance(item, PolicyToolEntry):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(PolicyToolEntry(tool=item))
        elif isinstance(item, dict):
            entries.append(PolicyToolEntry.model_validate(item))
        else:
            raise TypeError("policy entries must be strings or objects")
    return entries


class PoliciesConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    require_approval_for_tools: list[PolicyToolEntry] = Field(default_factory=list)
    require_confirmation_for_tools: list[PolicyToolEntry] = Field(default_factory=list)
    require_idempotency_for_tools: list[PolicyToolEntry] = Field(default_factory=list)

    @field_validator(
        "require_approval_for_tools",
        "require_confirmation_for_tools",
        "require_idempotency_for_tools",
        mode="before",
    )
    @classmethod
    def parse_entries(cls, value: Any) -> list[PolicyToolEntry]:
        return _parse_policy_entries(value)

    def approval_tools(self) -> set[str]:
        return {entry.tool for entry in self.require_approval_for_tools}

    def confirmation_tools(self) -> set[str]:
        return {entry.tool for entry in self.require_confirmation_for_tools}

    def idempotency_tools(self) -> set[str]:
        return {entry.tool for entry in self.require_idempotency_for_tools}


class PermissionsConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    scopes: list[str] = Field(default_factory=list)
    credential_mode: str | None = None
    notes: str | None = None


class ToolRiskOverride(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    tags: list[str] = Field(default_factory=list)
    remove_tags: list[str] = Field(default_factory=list)
    owner: str | None = None
    confidence: str = "manual"
    reason: str


class RiskOverridesConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    tools: dict[str, ToolRiskOverride] = Field(default_factory=dict)


class SuppressionConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    check_id: str
    tool: str | None = None
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("suppression reason is required")
        return value


class PolicyPackConfig(ArtifactPathConfig):
    id: str | None = None


def _parse_policy_pack_entries(value: Any) -> list[PolicyPackConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("policy_packs must be a list")
    entries: list[PolicyPackConfig] = []
    for item in value:
        if isinstance(item, PolicyPackConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(PolicyPackConfig(path=item))
        elif isinstance(item, dict):
            entries.append(PolicyPackConfig.model_validate(item))
        else:
            raise TypeError("policy_packs entries must be strings or objects")
    return entries


class ChecksConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    ignore: list[SuppressionConfig] = Field(default_factory=list)
    policy_packs: list[PolicyPackConfig] = Field(default_factory=list)
    severity_overrides: dict[str, Severity] = Field(default_factory=dict)

    @field_validator("policy_packs", mode="before")
    @classmethod
    def parse_policy_packs(cls, value: Any) -> list[PolicyPackConfig]:
        return _parse_policy_pack_entries(value)


class CiConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    mode: Literal["advisory", "strict"] = "advisory"
    fail_on: list[Severity] | None = None
    pr_comment: bool = True
    annotations: bool = False
    upload_artifact: bool = True


class OutputConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    directory: str = "agents-shipgate-reports"
    formats: list[Literal["markdown", "json", "sarif"]] = Field(
        default_factory=lambda: ["markdown", "json"]
    )


class AgentsShipgateManifest(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    version: str
    project: ProjectConfig
    agent: AgentConfig
    environment: EnvironmentConfig
    tool_sources: list[ToolSourceConfig] = Field(default_factory=list)
    openai_api: OpenAIApiConfig | None = None
    anthropic: AnthropicConfig | None = None
    google_adk: GoogleAdkConfig | None = None
    langchain: LangChainConfig | None = None
    crewai: CrewAiConfig | None = None
    policies: PoliciesConfig = Field(default_factory=PoliciesConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    risk_overrides: RiskOverridesConfig = Field(default_factory=RiskOverridesConfig)
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    ci: CiConfig = Field(default_factory=CiConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @model_validator(mode="after")
    def require_sources_and_scope_text(self) -> AgentsShipgateManifest:
        has_google_adk = (
            any(source.type == "google_adk" for source in self.tool_sources)
            or self.google_adk is not None
            and self.google_adk.has_inputs()
        )
        has_langchain = (
            any(source.type == "langchain" for source in self.tool_sources)
            or self.langchain is not None
            and self.langchain.has_inputs()
        )
        has_crewai = (
            any(source.type == "crewai" for source in self.tool_sources)
            or self.crewai is not None
            and self.crewai.has_inputs()
        )
        has_anthropic = self.anthropic is not None and self.anthropic.has_inputs()
        if (
            not self.tool_sources
            and self.openai_api is None
            and not has_anthropic
            and not has_google_adk
            and not has_langchain
            and not has_crewai
        ):
            raise ValueError(
                "At least one of tool_sources, openai_api, anthropic, google_adk, "
                "langchain, or crewai is required"
            )
        if (
            not self.agent.declared_purpose
            and not self.agent.instructions_preview
            and not (self.openai_api and self.openai_api.prompt_files)
            and not (self.anthropic and self.anthropic.prompt_files)
            and not has_google_adk
            and not has_langchain
            and not has_crewai
        ):
            raise ValueError(
                "agent.declared_purpose, agent.instructions_preview, "
                "openai_api.prompt_files, anthropic.prompt_files, or framework "
                "inputs are required"
            )
        return self

    def severity_overrides(self) -> dict[str, Severity]:
        return self.checks.severity_overrides
