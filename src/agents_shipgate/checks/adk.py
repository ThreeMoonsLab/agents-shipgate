from __future__ import annotations

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.risk_hints import is_high_risk_tool


def run(context: ScanContext):
    artifacts = context.adk_artifacts
    if not artifacts:
        return []

    findings = []
    explicit_inventory = _has_explicit_inventory(context)

    dynamic_toolsets = [
        toolset
        for toolset in artifacts.toolsets
        if toolset.dynamic or not toolset.resolved
    ]
    if not explicit_inventory:
        for toolset in dynamic_toolsets:
            findings.append(
                agent_finding(
                    check_id="SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE",
                    title="Google ADK toolset cannot be statically enumerated",
                    severity="high",
                    category="adk",
                    evidence={
                        "toolset": {
                            "kind": toolset.kind,
                            "source_ref": toolset.source_ref,
                            "agent_name": toolset.agent_name,
                        },
                        "explicit_inventory": False,
                    },
                    confidence="high",
                    recommendation=(
                        "Provide explicit MCP/OpenAPI/tool inventory inputs for dynamic ADK "
                        "toolsets before release review."
                    ),
                    context=context,
                )
            )

    for toolset in artifacts.toolsets:
        if toolset.kind == "mcp" and toolset.filtered is False:
            findings.append(
                agent_finding(
                    check_id="SHIP-ADK-MCP-TOOLSET-UNFILTERED",
                    title="Google ADK McpToolset lacks a static tool filter",
                    severity="high"
                    if context.manifest.environment.target in {"production_like", "production"}
                    else "medium",
                    category="adk",
                    evidence={
                        "source_ref": toolset.source_ref,
                        "agent_name": toolset.agent_name,
                        "inventory_path": toolset.inventory_path,
                    },
                    confidence="high",
                    recommendation=(
                        "Declare a tool_filter for ADK McpToolset usage and provide a "
                        "local MCP tool inventory for review."
                    ),
                    context=context,
                )
            )

    for tool in context.tools:
        if tool.source_type not in {"google_adk_function", "google_adk_config"}:
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
                check_id="SHIP-ADK-FUNCTION-TOOL-METADATA-MISSING",
                title=f"{tool.name} lacks static ADK function-tool metadata",
                severity="medium",
                category="adk",
                evidence={"missing": missing, "source_type": tool.source_type},
                confidence="high",
                recommendation=(
                    "Add docstrings, type annotations, or explicit local tool inventory "
                    f"metadata for ADK tool {tool.name}."
                ),
                context=context,
            )
        )

    for tool in context.tools:
        if tool.annotations.get("long_running") is not True:
            continue
        if (
            tool.annotations.get("long_running_contract") is True
            or _has_long_running_contract(tool.output_schema)
        ):
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-ADK-LONGRUNNING-CONTRACT-MISSING",
                title=f"{tool.name} lacks a long-running operation contract",
                severity="high",
                category="adk",
                evidence={"output_schema": tool.output_schema},
                confidence="medium",
                recommendation=(
                    "Document the operation id, status/progress fields, and completion "
                    f"contract for long-running ADK tool {tool.name}."
                ),
                context=context,
            )
        )

    if not artifacts.callbacks and not artifacts.plugins:
        high_risk_adk_tools = [
            tool.name
            for tool in context.tools
            if tool.source_type.startswith("google_adk") and is_high_risk_tool(tool)
        ]
        if high_risk_adk_tools:
            findings.append(
                agent_finding(
                    check_id="SHIP-ADK-GUARDRAIL-EVIDENCE-MISSING",
                    title="High-risk Google ADK tools lack static guardrail evidence",
                    severity="high",
                    category="adk",
                    evidence={"tools": high_risk_adk_tools},
                    confidence="medium",
                    recommendation=(
                        "Attach ADK callbacks/plugins or manifest policies that document "
                        "approval, confirmation, and validation guardrails."
                    ),
                    context=context,
                )
            )

    if (
        context.manifest.environment.target in {"production_like", "production"}
        and not artifacts.eval_files
    ):
        findings.append(
            agent_finding(
                check_id="SHIP-ADK-EVAL-COVERAGE-MISSING",
                title="Google ADK eval coverage is not declared",
                severity="medium",
                category="adk",
                evidence={
                    "agent_count": len(artifacts.agents),
                    "eval_file_count": 0,
                },
                confidence="high",
                recommendation=(
                    "Declare ADK eval files that cover expected responses and tool-use "
                    "trajectories for this release."
                ),
                context=context,
            )
        )

    return findings


def _has_explicit_inventory(context: ScanContext) -> bool:
    if context.adk_artifacts and context.adk_artifacts.tool_inventory_files:
        return True
    return any(
        source.type in {"mcp", "openapi"}
        for source in context.manifest.tool_sources
    )


def _has_long_running_contract(output_schema: dict[str, object]) -> bool:
    properties = output_schema.get("properties")
    if not isinstance(properties, dict):
        return False
    keys = {str(key).lower() for key in properties}
    has_status = any(
        "status" in key
        or "progress" in key
        or key in {"state", "phase", "done", "result", "metadata"}
        for key in keys
    )
    has_id = any(
        key.endswith("_id")
        or key in {"id", "operation", "operation_id", "name"}
        for key in keys
    )
    return has_status and has_id
