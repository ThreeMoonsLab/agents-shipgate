from __future__ import annotations

from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "0.1"
    report_schema_version: str = "0.2"
    run_id: str
    project: dict[str, Any]
    agent: dict[str, Any]
    environment: dict[str, Any]
    summary: ReportSummary
    tool_surface: ToolSurfaceSummary
    api_surface: dict[str, Any] | None = None
    baseline: BaselineSummary | None = None
    findings: list[Finding] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    generated_reports: dict[str, str] = Field(default_factory=dict)
    loaded_plugins: list[dict[str, Any]] = Field(default_factory=list)
    tool_inventory: list[dict[str, Any]] = Field(default_factory=list)
    source_warnings: list[str] = Field(default_factory=list)


class LoadedToolSource(BaseModel):
    source_id: str
    source_type: str
    tools: list[Tool] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
