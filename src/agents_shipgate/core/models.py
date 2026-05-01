from __future__ import annotations

from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.core.patches import Patch

Severity = Literal["info", "low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
BaselineStatus = Literal["new", "matched", "resolved"]


def parse_severity(value: str) -> Severity:
    if value not in get_args(Severity):
        raise ValueError(f"Unsupported severity: {value}")
    return cast(Severity, value)


def parse_confidence(value: str) -> Confidence:
    if value not in get_args(Confidence):
        raise ValueError(f"Unsupported confidence: {value}")
    return cast(Confidence, value)


def confidence_rank(confidence: str | None) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(confidence or "", 0)


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    ref: str | None = None
    location: str | None = None


class AuthInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    scopes: list[str] = Field(default_factory=list)
    credential_mode: str | None = None
    source: str | None = None


class ToolParameter(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    type: str | None = None
    required: bool = False
    description: str | None = None
    enum: list[Any] | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    format: str | None = None
    default: Any = None
    risk_hints: list[str] = Field(default_factory=list)


class ToolRiskHint(BaseModel):
    model_config = ConfigDict(extra="allow")

    tag: str
    source: str
    confidence: Confidence
    evidence: dict[str, Any] = Field(default_factory=dict)


class Tool(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str | None = None
    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    source_location: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    parameters: list[ToolParameter] = Field(default_factory=list)
    function_signature: str | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)
    auth: AuthInfo = Field(default_factory=AuthInfo)
    risk_hints: list[ToolRiskHint] = Field(default_factory=list)
    owner: str | None = None
    extraction_confidence: Confidence = "low"
    extraction: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_extraction_confidence(self) -> Tool:
        raw_confidence = self.extraction.get("confidence")
        if isinstance(raw_confidence, str) and raw_confidence in get_args(Confidence):
            self.extraction_confidence = parse_confidence(raw_confidence)
        else:
            self.extraction["confidence"] = self.extraction_confidence
        return self


class Agent(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    source: dict[str, Any] = Field(default_factory=dict)
    instructions: dict[str, Any] = Field(default_factory=dict)
    declared_purpose: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    handoffs: list[str] = Field(default_factory=list)
    guardrails: dict[str, Any] = Field(default_factory=dict)
    extraction: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    fingerprint: str | None = None
    check_id: str
    title: str
    severity: Severity
    category: str
    tool_id: str | None = None
    tool_name: str | None = None
    agent_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence = "medium"
    source: SourceReference | None = None
    recommendation: str
    suppressed: bool = False
    suppression_reason: str | None = None
    baseline_status: BaselineStatus | None = None
    # v0.6: populated only when scan ran with --suggest-patches. None
    # default + dict post-processing in write_json_report keeps the JSON
    # contract additive — non-opting callers see no `patches` key at all
    # (per C4).
    patches: list[Patch] | None = None


class ReportSummary(BaseModel):
    status: str
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    suppressed_count: int = 0
    human_review_recommended: bool = False
    evidence_coverage: str = "static"


class ToolSurfaceSummary(BaseModel):
    total_tools: int
    high_risk_tools: int
    sources: dict[str, int] = Field(default_factory=dict)
    wildcard_tools: int = 0
    missing_descriptions: int = 0


class BaselineSummary(BaseModel):
    path: str
    matched_count: int = 0
    new_count: int = 0
    resolved_count: int = 0


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


class LoadedPolicyPack(BaseModel):
    id: str
    name: str
    version: str | None = None
    path: str
    rule_count: int


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "0.1"
    report_schema_version: str = "0.6"
    run_id: str
    # v0.6 (per C13): absolute path to the directory containing
    # shipgate.yaml. apply-patches uses this to enforce a containment
    # check on every patch's target_file. Optional for backwards
    # compatibility with older reports loaded as baselines.
    manifest_dir: str | None = None
    project: dict[str, Any]
    agent: dict[str, Any]
    environment: dict[str, Any]
    summary: ReportSummary
    tool_surface: ToolSurfaceSummary
    api_surface: dict[str, Any] | None = None
    anthropic_surface: dict[str, Any] | None = None
    frameworks: dict[str, Any] = Field(default_factory=dict)
    baseline: BaselineSummary | None = None
    findings: list[Finding] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    generated_reports: dict[str, str] = Field(default_factory=dict)
    loaded_policy_packs: list[LoadedPolicyPack] = Field(default_factory=list)
    loaded_plugins: list[dict[str, Any]] = Field(default_factory=list)
    tool_inventory: list[dict[str, Any]] = Field(default_factory=list)
    source_warnings: list[str] = Field(default_factory=list)


class LoadedToolSource(BaseModel):
    source_id: str
    source_type: str
    tools: list[Tool] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


SuggestedPatchKind = Literal[
    "manual",
    "remove_pointer",
    "append_pointer",
    "set_pointer",
    "none",
]


class CheckMetadata(BaseModel):
    id: str
    category: str
    default_severity: Severity
    description: str
    rationale: str | None = None
    fires_when: str | None = None
    evidence_fields: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    docs_url: str | None = None
    # v0.7 remediation policy: describes per-check policy independent of
    # any scan run. The mirror Finding-level fields land in PR 3 and are
    # derived from `Finding.patches` when present, falling back to these
    # check-level values otherwise.
    autofix_safe: bool = False
    requires_human_review: bool = True
    suggested_patch_kind: SuggestedPatchKind = "manual"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
