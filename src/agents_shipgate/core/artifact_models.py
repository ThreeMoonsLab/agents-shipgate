from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.codex_plugin import (
    CodexPluginAppSummary,
    CodexPluginComponentPathIssue,
    CodexPluginHookStub,
    CodexPluginMarketplaceSummary,
    CodexPluginMcpServerStub,
    CodexPluginSkillSummary,
    CodexPluginSummary,
    CodexPluginSurface,
)
from agents_shipgate.schemas.common import HitlSourceProvenance


class ApiResponseFormat(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    path: str
    name: str | None = None
    strict: bool | None = None
    json_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")
    downstream_critical_fields: list[str] = Field(default_factory=list)


class OpenAIApiArtifacts(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    prompt_files: list[str] = Field(default_factory=list)
    prompt_text: str | None = None
    tool_files: list[str] = Field(default_factory=list)
    response_formats: list[ApiResponseFormat] = Field(default_factory=list)
    model_config_path: str | None = None
    model_settings: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    test_case_files: list[str] = Field(default_factory=list)
    test_cases: list[dict[str, Any]] = Field(default_factory=list)
    trace_sample_files: list[str] = Field(default_factory=list)
    trace_samples: list[dict[str, Any]] = Field(default_factory=list)
    policy_rule_files: list[str] = Field(default_factory=list)
    policy_rules: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def approval_tools(self) -> set[str]:
        return set(_string_list(self.policy_rules.get("approval_required")))

    def confirmation_tools(self) -> set[str]:
        return set(_string_list(self.policy_rules.get("confirmation_required")))

    def idempotency_tools(self) -> set[str]:
        return set(_string_list(self.policy_rules.get("idempotency_required")))

    def retry_policy(self) -> dict[str, Any]:
        value = self.policy_rules.get("retry_policy")
        if isinstance(value, dict):
            return value
        value = self.model_settings.get("retry_policy")
        return value if isinstance(value, dict) else {}

    def timeouts(self) -> dict[str, Any]:
        value = self.policy_rules.get("timeouts")
        if isinstance(value, dict):
            return value
        value = self.model_settings.get("timeouts")
        return value if isinstance(value, dict) else {}

    def tool_output_schemas(self) -> dict[str, Any]:
        value = self.policy_rules.get("tool_output_schemas")
        return value if isinstance(value, dict) else {}

    def surface_summary(self) -> dict[str, Any]:
        return {
            "prompt_file_count": len(self.prompt_files),
            "tool_file_count": len(self.tool_files),
            "response_format_count": len(self.response_formats),
            "model_config_present": bool(self.model_config_path),
            "test_case_count": len(self.test_cases),
            "trace_sample_count": len(self.trace_samples),
            "policy_rule_count": len(self.policy_rule_files),
        }


class AnthropicArtifacts(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    prompt_files: list[str] = Field(default_factory=list)
    prompt_text: str | None = None
    tool_files: list[str] = Field(default_factory=list)
    policy_rule_files: list[str] = Field(default_factory=list)
    policy_rules: dict[str, Any] = Field(default_factory=dict)
    skipped_server_tools: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # Mirror OpenAIApiArtifacts so checks/api.py can consume either artifact
    # source via the same helpers without branching.
    def approval_tools(self) -> set[str]:
        return set(_string_list(self.policy_rules.get("approval_required")))

    def confirmation_tools(self) -> set[str]:
        return set(_string_list(self.policy_rules.get("confirmation_required")))

    def idempotency_tools(self) -> set[str]:
        return set(_string_list(self.policy_rules.get("idempotency_required")))

    def retry_policy(self) -> dict[str, Any]:
        value = self.policy_rules.get("retry_policy")
        return value if isinstance(value, dict) else {}

    def timeouts(self) -> dict[str, Any]:
        value = self.policy_rules.get("timeouts")
        return value if isinstance(value, dict) else {}

    def tool_output_schemas(self) -> dict[str, Any]:
        value = self.policy_rules.get("tool_output_schemas")
        return value if isinstance(value, dict) else {}

    @property
    def response_formats(self) -> list[Any]:
        # Anthropic has no first-class response-format object in the
        # documented Messages API surface; expose an empty list so the
        # OpenAI-shaped readiness checks early-return cleanly when the
        # only artifact present is an Anthropic one.
        return []

    @property
    def test_cases(self) -> list[Any]:
        return []

    @property
    def trace_samples(self) -> list[Any]:
        return []

    def surface_summary(self) -> dict[str, Any]:
        return {
            "prompt_file_count": len(self.prompt_files),
            "tool_file_count": len(self.tool_files),
            "policy_rule_count": len(self.policy_rule_files),
            "skipped_server_tool_count": len(self.skipped_server_tools),
        }


class ValidationArtifacts(BaseModel):
    model_config = ConfigDict(extra="allow")

    approval_trace_files: list[str] = Field(default_factory=list)
    approval_traces: list[dict[str, Any]] = Field(default_factory=list)
    agent_trace_files: list[str] = Field(default_factory=list)
    agent_traces: list[dict[str, Any]] = Field(default_factory=list)
    override_log_files: list[str] = Field(default_factory=list)
    override_events: list[dict[str, Any]] = Field(default_factory=list)
    high_risk_exclusion_files: list[str] = Field(default_factory=list)
    high_risk_auto_approval_exclusions: list[dict[str, Any]] = Field(
        default_factory=list
    )
    promotion_criteria_files: list[str] = Field(default_factory=list)
    promotion_criteria: list[dict[str, Any]] = Field(default_factory=list)
    source_provenance: list[HitlSourceProvenance] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CodexBoundaryArtifacts(BaseModel):
    model_config = ConfigDict(extra="allow")

    root_path: str | None = None
    config_files: list[str] = Field(default_factory=list)
    hooks_files: list[str] = Field(default_factory=list)
    agent_instruction_files: list[str] = Field(default_factory=list)
    skill_files: list[str] = Field(default_factory=list)
    github_workflow_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def surface_summary(self) -> dict[str, Any]:
        return {
            "config_file_count": len(self.config_files),
            "hooks_file_count": len(self.hooks_files),
            "agent_instruction_file_count": len(self.agent_instruction_files),
            "skill_file_count": len(self.skill_files),
            "github_workflow_file_count": len(self.github_workflow_files),
            "warnings": self.warnings,
        }


class GoogleAdkToolset(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: str
    source_id: str
    source_ref: str | None = None
    agent_name: str | None = None
    name: str | None = None
    filtered: bool | None = None
    filter_values: list[str] = Field(default_factory=list)
    inventory_path: str | None = None
    resolved: bool = False
    dynamic: bool = False


class GoogleAdkArtifacts(BaseModel):
    model_config = ConfigDict(extra="allow")

    python_entrypoints: list[str] = Field(default_factory=list)
    agent_config_files: list[str] = Field(default_factory=list)
    eval_files: list[str] = Field(default_factory=list)
    tool_inventory_files: list[str] = Field(default_factory=list)
    trace_sample_files: list[str] = Field(default_factory=list)
    trace_samples: list[dict[str, Any]] = Field(default_factory=list)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    function_tools: list[dict[str, Any]] = Field(default_factory=list)
    long_running_tools: list[dict[str, Any]] = Field(default_factory=list)
    toolsets: list[GoogleAdkToolset] = Field(default_factory=list)
    callbacks: list[dict[str, Any]] = Field(default_factory=list)
    plugins: list[dict[str, Any]] = Field(default_factory=list)
    sub_agents: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def surface_summary(self) -> dict[str, Any]:
        dynamic_toolsets = [
            item for item in self.toolsets if item.dynamic or not item.resolved
        ]
        return {
            "python_entrypoint_count": len(self.python_entrypoints),
            "agent_config_count": len(self.agent_config_files),
            "agent_count": len(self.agents),
            "function_tool_count": len(self.function_tools),
            "long_running_tool_count": len(self.long_running_tools),
            "toolset_count": len(self.toolsets),
            "dynamic_toolset_count": len(dynamic_toolsets),
            "callback_count": len(self.callbacks),
            "plugin_count": len(self.plugins),
            "sub_agent_count": len(self.sub_agents),
            "eval_file_count": len(self.eval_files),
            "trace_sample_count": len(self.trace_samples),
            "tool_inventory_file_count": len(self.tool_inventory_files),
            "warnings": self.warnings,
        }


class LangChainArtifacts(BaseModel):
    model_config = ConfigDict(extra="allow")

    python_entrypoints: list[str] = Field(default_factory=list)
    tool_inventory_files: list[str] = Field(default_factory=list)
    function_tools: list[dict[str, Any]] = Field(default_factory=list)
    structured_tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_nodes: list[dict[str, Any]] = Field(default_factory=list)
    agent_bindings: list[dict[str, Any]] = Field(default_factory=list)
    dynamic_tool_surfaces: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def surface_summary(self) -> dict[str, Any]:
        return {
            "python_entrypoint_count": len(self.python_entrypoints),
            "function_tool_count": len(self.function_tools),
            "structured_tool_count": len(self.structured_tools),
            "tool_node_count": len(self.tool_nodes),
            "agent_tool_binding_count": len(self.agent_bindings),
            "dynamic_tool_surface_count": len(self.dynamic_tool_surfaces),
            "tool_inventory_file_count": len(self.tool_inventory_files),
            "warnings": self.warnings,
        }


class CrewAiArtifacts(BaseModel):
    model_config = ConfigDict(extra="allow")

    python_entrypoints: list[str] = Field(default_factory=list)
    tool_inventory_files: list[str] = Field(default_factory=list)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    crews: list[dict[str, Any]] = Field(default_factory=list)
    function_tools: list[dict[str, Any]] = Field(default_factory=list)
    class_tools: list[dict[str, Any]] = Field(default_factory=list)
    prebuilt_tools: list[dict[str, Any]] = Field(default_factory=list)
    dynamic_tool_surfaces: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def surface_summary(self) -> dict[str, Any]:
        return {
            "python_entrypoint_count": len(self.python_entrypoints),
            "agent_count": len(self.agents),
            "crew_count": len(self.crews),
            "function_tool_count": len(self.function_tools),
            "class_tool_count": len(self.class_tools),
            "prebuilt_tool_count": len(self.prebuilt_tools),
            "dynamic_tool_surface_count": len(self.dynamic_tool_surfaces),
            "tool_inventory_file_count": len(self.tool_inventory_files),
            "warnings": self.warnings,
        }

class CodexPluginArtifacts(BaseModel):
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

    def surface_summary(self) -> CodexPluginSurface:
        return CodexPluginSurface(
            plugin_count=self.plugin_count,
            marketplace_count=self.marketplace_count,
            skill_count=self.skill_count,
            app_count=self.app_count,
            mcp_server_stub_count=self.mcp_server_stub_count,
            hook_stub_count=self.hook_stub_count,
            mcp_inventory_file_count=self.mcp_inventory_file_count,
            plugins=self.plugins,
            marketplaces=self.marketplaces,
            skills=self.skills,
            apps=self.apps,
            mcp_server_stubs=self.mcp_server_stubs,
            hook_stubs=self.hook_stubs,
            mcp_inventory_files=self.mcp_inventory_files,
            component_path_issues=self.component_path_issues,
            warnings=self.warnings,
        )


class N8nArtifacts(BaseModel):
    model_config = ConfigDict(extra="allow")

    workflow_files: list[str] = Field(default_factory=list)
    credential_stub_files: list[str] = Field(default_factory=list)
    variable_stub_files: list[str] = Field(default_factory=list)
    data_table_schema_files: list[str] = Field(default_factory=list)
    execution_sample_files: list[str] = Field(default_factory=list)
    eval_files: list[str] = Field(default_factory=list)
    tool_inventory_files: list[str] = Field(default_factory=list)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    ai_agents: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    mcp_server_triggers: list[dict[str, Any]] = Field(default_factory=list)
    mcp_server_exposed_tools: list[dict[str, Any]] = Field(default_factory=list)
    mcp_client_tools: list[dict[str, Any]] = Field(default_factory=list)
    workflow_tools: list[dict[str, Any]] = Field(default_factory=list)
    code_tools: list[dict[str, Any]] = Field(default_factory=list)
    http_tools: list[dict[str, Any]] = Field(default_factory=list)
    community_tools: list[dict[str, Any]] = Field(default_factory=list)
    dynamic_tool_surfaces: list[dict[str, Any]] = Field(default_factory=list)
    ingress: list[dict[str, Any]] = Field(default_factory=list)
    credential_refs: list[dict[str, Any]] = Field(default_factory=list)
    credential_stubs: list[dict[str, Any]] = Field(default_factory=list)
    human_review_nodes: list[dict[str, Any]] = Field(default_factory=list)
    secret_exposures: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def surface_summary(self) -> dict[str, Any]:
        return {
            "workflow_file_count": len(self.workflow_files),
            "workflow_count": len(self.workflows),
            "ai_agent_count": len(self.ai_agents),
            "tool_count": len(self.tools),
            "mcp_server_trigger_count": len(self.mcp_server_triggers),
            "mcp_server_exposed_tool_count": len(self.mcp_server_exposed_tools),
            "mcp_client_tool_count": len(self.mcp_client_tools),
            "workflow_tool_count": len(self.workflow_tools),
            "code_tool_count": len(self.code_tools),
            "http_tool_count": len(self.http_tools),
            "community_tool_count": len(self.community_tools),
            "dynamic_tool_surface_count": len(self.dynamic_tool_surfaces),
            "ingress_count": len(self.ingress),
            "credential_ref_count": len(self.credential_refs),
            "credential_stub_file_count": len(self.credential_stub_files),
            "variable_stub_file_count": len(self.variable_stub_files),
            "data_table_schema_file_count": len(self.data_table_schema_files),
            "execution_sample_file_count": len(self.execution_sample_files),
            "eval_file_count": len(self.eval_files),
            "tool_inventory_file_count": len(self.tool_inventory_files),
            "secret_exposure_count": len(self.secret_exposures),
            "human_review_node_count": len(self.human_review_nodes),
            "warnings": self.warnings,
        }


class ConductorArtifacts(BaseModel):
    """Sanitized static facts extracted from Conductor OSS workflow JSON."""

    model_config = ConfigDict(extra="allow")

    workflow_files: list[str] = Field(default_factory=list)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    task_count: int = 0
    llm_tasks: list[dict[str, Any]] = Field(default_factory=list)
    llm_advertised_tools: list[dict[str, Any]] = Field(default_factory=list)
    mcp_discovery_tasks: list[dict[str, Any]] = Field(default_factory=list)
    mcp_call_tasks: list[dict[str, Any]] = Field(default_factory=list)
    human_checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    sub_workflows: list[dict[str, Any]] = Field(default_factory=list)
    dynamic_tool_surfaces: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def surface_summary(self) -> dict[str, Any]:
        structurally_checkpointed = sum(
            1
            for item in self.mcp_call_tasks
            if item.get("preceding_checkpoint_refs")
        )
        return {
            "workflow_file_count": len(self.workflow_files),
            "workflow_count": len(self.workflows),
            "task_count": self.task_count,
            "llm_task_count": len(self.llm_tasks),
            "mcp_discovery_task_count": len(self.mcp_discovery_tasks),
            "mcp_call_task_count": len(self.mcp_call_tasks),
            "human_checkpoint_count": len(self.human_checkpoints),
            "structurally_checkpointed_mcp_call_count": structurally_checkpointed,
            "sub_workflow_task_count": len(self.sub_workflows),
            "dynamic_tool_surface_count": len(self.dynamic_tool_surfaces),
            "unsupported_capability_count": len(self.unsupported_capabilities),
            "warnings": self.warnings,
        }

def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
