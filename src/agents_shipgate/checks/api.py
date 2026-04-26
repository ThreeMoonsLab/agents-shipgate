from __future__ import annotations

from typing import Any

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.heuristics import (
    BROAD_FREE_TEXT_PARAMETER_NAMES,
    RISKY_NUMERIC_PARAMETER_NAMES,
)
from agents_shipgate.core.models import Tool, ToolParameter
from agents_shipgate.core.risk_hints import (
    has_risk_tag,
    is_high_risk_tool,
    is_write_tool,
    risk_tags,
)

READ_ONLY_PROMPT_TERMS = (
    "advise only",
    "advice only",
    "only advise",
    "read-only",
    "read only",
    "do not take action",
)
APPROVAL_PROMPT_TERMS = ("approval", "approved", "human review", "requires review")
CONFIRMATION_PROMPT_TERMS = ("confirm", "confirmation", "explicit consent", "ask before")


def run(context: ScanContext):
    if context.api_artifacts is None:
        return []
    findings = []
    findings.extend(_function_schema_strictness(context))
    findings.extend(_structured_output_readiness(context))
    findings.extend(_prompt_tool_scope_mismatch(context))
    findings.extend(_operational_readiness(context))
    return findings


def _function_schema_strictness(context: ScanContext):
    findings = []
    for tool in _api_tools(context):
        issues = _function_schema_issues(tool)
        if not issues:
            continue
        high_risk = is_write_tool(tool) or is_high_risk_tool(tool)
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-API-FUNCTION-SCHEMA-STRICTNESS",
                title=f"{tool.name} function schema is not strict enough",
                severity="high" if high_risk else "medium",
                category="api",
                evidence={"issues": issues, "risk_tags": risk_tags(tool, min_confidence="medium")},
                confidence="high",
                recommendation=(
                    f"Make {tool.name} a strict function schema: object parameters, "
                    "additionalProperties=false, complete required list, and bounded risky fields."
                ),
                context=context,
            )
        )
    return findings


def _structured_output_readiness(context: ScanContext):
    artifacts = context.api_artifacts
    if artifacts is None:
        return []
    high_risk_tools = [tool.name for tool in _api_tools(context) if is_high_risk_tool(tool)]
    if not artifacts.response_formats:
        return [
            agent_finding(
                check_id="SHIP-API-STRUCTURED-OUTPUT-READINESS",
                title="OpenAI API response format is not declared",
                severity="high" if high_risk_tools else "medium",
                category="api",
                evidence={"high_risk_tools": high_risk_tools},
                confidence="high",
                recommendation=(
                    "Declare a structured response format with decision/status, error/refusal, "
                    "and needs_review fields where downstream behavior depends on the output."
                ),
                context=context,
            )
        ]

    findings = []
    for response_format in artifacts.response_formats:
        issues = _response_schema_issues(
            response_format.json_schema,
            response_format.downstream_critical_fields,
        )
        if not issues:
            continue
        findings.append(
            agent_finding(
                check_id="SHIP-API-STRUCTURED-OUTPUT-READINESS",
                title=f"Response format {response_format.path} is under-specified",
                severity="medium",
                category="api",
                evidence={
                    "path": response_format.path,
                    "issues": issues,
                    "downstream_critical_fields": response_format.downstream_critical_fields,
                },
                confidence="medium",
                recommendation=(
                    "Tighten the structured output schema with enums, "
                    "needs_review/refusal/error modeling, and declared critical fields."
                ),
                context=context,
            )
        )
    return findings


def _prompt_tool_scope_mismatch(context: ScanContext):
    artifacts = context.api_artifacts
    if artifacts is None or not artifacts.prompt_text:
        return []
    prompt = artifacts.prompt_text.lower()
    api_tools = _api_tools(context)
    write_or_high_risk = [
        tool for tool in api_tools if is_write_tool(tool) or is_high_risk_tool(tool)
    ]
    findings = []
    if write_or_high_risk and any(term in prompt for term in READ_ONLY_PROMPT_TERMS):
        findings.append(
            agent_finding(
                check_id="SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH",
                title=(
                    "Prompt says read-only or advise-only while write/high-risk "
                    "tools are enabled"
                ),
                severity="high",
                category="api",
                evidence={"tools": [tool.name for tool in write_or_high_risk]},
                confidence="high",
                recommendation=(
                    "Align prompt scope with enabled tools or remove write/high-risk tools."
                ),
                context=context,
            )
        )
    needs_confirmation = [
        tool
        for tool in api_tools
        if has_risk_tag(
            tool,
            {"destructive", "external_write", "customer_communication", "financial_action"},
            min_confidence="medium",
        )
    ]
    if needs_confirmation and not (
        any(term in prompt for term in CONFIRMATION_PROMPT_TERMS)
        and any(term in prompt for term in APPROVAL_PROMPT_TERMS)
    ):
        findings.append(
            agent_finding(
                check_id="SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH",
                title="Prompt lacks approval/confirmation language for high-risk tools",
                severity="medium",
                category="api",
                evidence={"tools": [tool.name for tool in needs_confirmation]},
                confidence="medium",
                recommendation=(
                    "Add prompt instructions requiring human approval and explicit confirmation "
                    "before financial, destructive, or external customer actions."
                ),
                context=context,
            )
        )
    return findings


def _operational_readiness(context: ScanContext):
    artifacts = context.api_artifacts
    if artifacts is None:
        return []
    findings = []
    api_tools = _api_tools(context)
    high_risk_tools = [tool for tool in api_tools if is_high_risk_tool(tool)]
    retry_policy = artifacts.retry_policy()
    timeouts = artifacts.timeouts()
    output_schemas = artifacts.tool_output_schemas()

    if high_risk_tools and not retry_policy:
        findings.append(
            agent_finding(
                check_id="SHIP-API-RETRY-POLICY-MISSING",
                title="OpenAI API flow lacks retry policy metadata",
                severity="medium",
                category="api",
                evidence={"high_risk_tools": [tool.name for tool in high_risk_tools]},
                confidence="medium",
                recommendation="Declare retry_policy in openai_api.policy_rules or model_config.",
                context=context,
            )
        )
    if high_risk_tools and not timeouts:
        findings.append(
            agent_finding(
                check_id="SHIP-API-TIMEOUT-MISSING",
                title="OpenAI API flow lacks timeout metadata",
                severity="medium",
                category="api",
                evidence={"high_risk_tools": [tool.name for tool in high_risk_tools]},
                confidence="medium",
                recommendation="Declare tool-call timeout metadata for high-risk OpenAI API flows.",
                context=context,
            )
        )
    if high_risk_tools and not artifacts.test_cases:
        findings.append(
            agent_finding(
                check_id="SHIP-API-TEST-CASES-MISSING",
                title="OpenAI API flow lacks test case metadata for high-risk tools",
                severity="medium",
                category="api",
                evidence={"high_risk_tools": [tool.name for tool in high_risk_tools]},
                confidence="medium",
                recommendation="Add simple OpenAI API test cases for high-risk tool-call flows.",
                context=context,
            )
        )
    for tool in high_risk_tools:
        if tool.name not in output_schemas:
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING",
                    title=f"{tool.name} lacks success/failure output modeling",
                    severity="medium",
                    category="api",
                    evidence={"tool_output_schemas": sorted(output_schemas)},
                    confidence="medium",
                    recommendation=(
                        f"Declare success_fields and failure_fields for {tool.name} "
                        "in openai_api policy rules."
                    ),
                    context=context,
                )
            )
        if retry_policy and _needs_idempotency(tool, artifacts):
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-API-RETRY-WITHOUT-IDEMPOTENCY",
                    title=f"{tool.name} may be retried without idempotency evidence",
                    severity="high",
                    category="api",
                    evidence={
                        "retry_policy": retry_policy,
                        "risk_tags": risk_tags(tool, min_confidence="medium"),
                    },
                    confidence="high",
                    recommendation=(
                        f"Add idempotency evidence for {tool.name} or avoid retrying "
                        "this side effect."
                    ),
                    context=context,
                )
            )
    _append_trace_findings(findings, context)
    return findings


def _append_trace_findings(findings: list, context: ScanContext) -> None:
    artifacts = context.api_artifacts
    if artifacts is None:
        return
    approval_tools = context.manifest.policies.approval_tools() | artifacts.approval_tools()
    confirmation_tools = (
        context.manifest.policies.confirmation_tools() | artifacts.confirmation_tools()
    )
    for event in artifacts.trace_samples:
        tool_name = event.get("tool_name")
        if not isinstance(tool_name, str):
            continue
        if tool_name in approval_tools and event.get("approved") is False:
            findings.append(
                agent_finding(
                    check_id="SHIP-API-TRACE-APPROVAL-MISSING",
                    title=f"Trace sample shows {tool_name} without approval",
                    severity="medium",
                    category="api",
                    evidence={"tool_name": tool_name, "approved": event.get("approved")},
                    confidence="medium",
                    recommendation=f"Require approval before calling {tool_name}.",
                    context=context,
                )
            )
        if tool_name in confirmation_tools and event.get("confirmed") is False:
            findings.append(
                agent_finding(
                    check_id="SHIP-API-TRACE-CONFIRMATION-MISSING",
                    title=f"Trace sample shows {tool_name} without confirmation",
                    severity="medium",
                    category="api",
                    evidence={"tool_name": tool_name, "confirmed": event.get("confirmed")},
                    confidence="medium",
                    recommendation=f"Require explicit confirmation before calling {tool_name}.",
                    context=context,
                )
            )


def _function_schema_issues(tool: Tool) -> list[str]:
    issues: list[str] = []
    schema = tool.input_schema
    if not schema:
        return ["missing_parameters_schema"]
    if tool.annotations.get("openaiStrict") is not True:
        issues.append("missing_strict_true")
    if schema.get("type") != "object":
        issues.append("parameters_schema_not_object")
    if schema.get("additionalProperties") is not False:
        issues.append("additional_properties_not_false")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = set(schema.get("required") or [])
        missing_required = sorted(set(properties) - required)
        if missing_required:
            issues.append(f"properties_missing_from_required:{','.join(missing_required)}")
    for parameter in tool.parameters:
        if _risky_field_without_bounds_or_enum(parameter):
            issues.append(f"risky_field_unbounded:{parameter.name}")
        if _broad_free_text(parameter):
            issues.append(f"broad_free_text:{parameter.name}")
    return issues


def _response_schema_issues(schema: dict[str, Any], critical_fields: list[str]) -> list[str]:
    issues: list[str] = []
    if schema.get("type") != "object":
        issues.append("response_schema_not_object")
    if schema.get("additionalProperties") is not False:
        issues.append("additional_properties_not_false")
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if not critical_fields:
        issues.append("missing_downstream_critical_fields")
    else:
        missing_critical = sorted(set(critical_fields) - set(properties))
        if missing_critical:
            issues.append(f"critical_fields_missing_from_schema:{','.join(missing_critical)}")
    if not any(field in properties for field in ("refusal", "needs_review", "error")):
        issues.append("missing_refusal_needs_review_or_error_field")
    for field in ("decision", "status"):
        value = properties.get(field)
        if isinstance(value, dict) and not value.get("enum"):
            issues.append(f"missing_enum:{field}")
    return issues


def _needs_idempotency(tool: Tool, artifacts) -> bool:
    if tool.name in artifacts.idempotency_tools():
        return False
    if tool.annotations.get("idempotentHint") is True:
        return False
    if any(parameter.name == "idempotency_key" for parameter in tool.parameters):
        return False
    return is_write_tool(tool) and has_risk_tag(
        tool,
        {"financial_action", "destructive", "external_write"},
        min_confidence="medium",
    )


def _risky_field_without_bounds_or_enum(parameter: ToolParameter) -> bool:
    name = parameter.name.lower()
    risky_name = any(token in name for token in RISKY_NUMERIC_PARAMETER_NAMES)
    return (
        risky_name
        and parameter.type in {"number", "integer"}
        and parameter.maximum is None
        and not parameter.enum
    )


def _broad_free_text(parameter: ToolParameter) -> bool:
    return (
        parameter.name.lower() in BROAD_FREE_TEXT_PARAMETER_NAMES
        and parameter.type in {None, "string", "object"}
        and not parameter.enum
    )


def _api_tools(context: ScanContext) -> list[Tool]:
    return [tool for tool in context.tools if tool.source_type == "openai_api"]
