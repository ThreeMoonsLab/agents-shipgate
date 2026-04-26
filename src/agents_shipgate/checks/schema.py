from __future__ import annotations

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.heuristics import (
    BROAD_FREE_TEXT_PARAMETER_NAMES,
    RISKY_NUMERIC_PARAMETER_NAMES,
)
from agents_shipgate.core.models import Tool, ToolParameter
from agents_shipgate.core.risk_hints import has_risk_tag, is_effectively_read_only, is_write_tool


def run(context: ScanContext):
    findings = []
    for tool in context.tools:
        if _has_freeform_output(tool):
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-SCHEMA-FREEFORM-OUTPUT",
                    title=f"{tool.name} returns free-form text output",
                    severity="medium",
                    category="schema",
                    evidence={"output_schema": tool.output_schema or {"type": "string"}},
                    confidence="medium",
                    recommendation=(
                        f"Prefer a structured output schema for {tool.name}, especially when output "
                        "is later passed back into model context."
                    ),
                    context=context,
                )
            )
        for parameter in tool.parameters:
            if _is_broad_free_text(parameter) and _action_like_tool(tool):
                findings.append(
                    tool_finding(
                        tool=tool,
                        check_id="SHIP-SCHEMA-BROAD-FREE-TEXT",
                        title=f"{tool.name} accepts broad free-form action input",
                        severity="high",
                        category="schema",
                        evidence={"parameter": parameter.name, "type": parameter.type},
                        confidence="medium",
                        recommendation=f"Constrain {tool.name}.{parameter.name} with an enum, structured schema, or narrower field-specific parameters.",
                        context=context,
                    )
                )
            if _is_missing_bound(parameter) and _bounded_tool(tool):
                findings.append(
                    tool_finding(
                        tool=tool,
                        check_id="SHIP-SCHEMA-MISSING-BOUNDS",
                        title=f"{tool.name}.{parameter.name} has no maximum bound",
                        severity="high",
                        category="schema",
                        evidence={"parameter": parameter.name, "type": parameter.type},
                        confidence="high",
                        recommendation=f"Add a maximum bound to {tool.name}.{parameter.name} or document an equivalent limit in the tool policy.",
                        context=context,
                    )
                )
    return findings


def _is_broad_free_text(parameter: ToolParameter) -> bool:
    if parameter.name.lower() not in BROAD_FREE_TEXT_PARAMETER_NAMES:
        return False
    if parameter.enum:
        return False
    return parameter.type in {None, "string", "object"}


def _is_missing_bound(parameter: ToolParameter) -> bool:
    return (
        parameter.name.lower() in RISKY_NUMERIC_PARAMETER_NAMES
        and parameter.type in {"number", "integer"}
        and parameter.maximum is None
    )


def _action_like_tool(tool: Tool) -> bool:
    if is_effectively_read_only(tool):
        return False
    return is_write_tool(tool) or has_risk_tag(
        tool,
        {
            "external_write",
            "customer_communication",
            "destructive",
            "code_execution",
            "infrastructure_change",
        },
        min_confidence="medium",
    )


def _bounded_tool(tool: Tool) -> bool:
    if is_effectively_read_only(tool):
        return False
    return is_write_tool(tool) or has_risk_tag(tool, {"financial_action"}, min_confidence="medium")


def _has_freeform_output(tool: Tool) -> bool:
    schema = tool.output_schema or {}
    if schema.get("type") == "string":
        return True
    if tool.source_type == "sdk_function" and not schema:
        signature = tool.function_signature or ""
        return "-> str" in signature
    return False
