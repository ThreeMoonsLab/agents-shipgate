from __future__ import annotations

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.context import ScanContext


def run(context: ScanContext):
    artifacts = context.langchain_artifacts
    if not artifacts:
        return []

    findings = []
    if not artifacts.tool_inventory_files:
        for surface in artifacts.dynamic_tool_surfaces:
            findings.append(
                agent_finding(
                    check_id="SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
                    title="LangChain tool surface cannot be statically enumerated",
                    severity="high",
                    category="langchain",
                    evidence={
                        "surface": surface,
                        "explicit_inventory": False,
                    },
                    confidence="medium",
                    recommendation=(
                        "Provide explicit MCP-style tool inventory metadata for dynamic "
                        "LangChain or LangGraph tool lists before release review."
                    ),
                    context=context,
                )
            )

    for tool in context.tools:
        if tool.source_type not in {"langchain_function", "langchain_structured_tool"}:
            continue
        missing = []
        if not (tool.description or "").strip():
            missing.append("description")
        if not tool.parameters and not tool.input_schema.get("properties"):
            missing.append("parameters")
        if not missing:
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-LANGCHAIN-FUNCTION-TOOL-METADATA-MISSING",
                title=f"{tool.name} lacks static LangChain function-tool metadata",
                severity="medium",
                category="langchain",
                evidence={"missing": missing, "source_type": tool.source_type},
                confidence="high",
                recommendation=(
                    "Add a docstring, description, type annotations, args_schema, or "
                    f"explicit local inventory metadata for LangChain tool {tool.name}."
                ),
                context=context,
            )
        )

    return findings
