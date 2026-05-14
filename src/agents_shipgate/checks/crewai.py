from __future__ import annotations

from agents_shipgate.checks._framework_common import (
    DynamicSurfaceConfig,
    collect_dynamic_surface_findings,
)
from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import CrewAiArtifacts


def run(context: ScanContext):
    artifacts = context.artifact("crewai", CrewAiArtifacts)
    if not artifacts:
        return []

    findings = []
    has_inventory = bool(artifacts.tool_inventory_files)
    findings.extend(
        collect_dynamic_surface_findings(
            context,
            surfaces=artifacts.dynamic_tool_surfaces,
            config=DynamicSurfaceConfig(
                check_id="SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
                title="CrewAI tool surface cannot be statically enumerated",
                severity="high",
                category="crewai",
                confidence="medium",
                evidence_key="surface",
                recommendation=(
                    "Provide explicit MCP-style tool inventory metadata for dynamic "
                    "CrewAI tool lists before release review."
                ),
                suppress=lambda _surface: has_inventory,
            ),
        )
    )

    for tool in context.tools:
        if tool.source_type not in {"crewai_function", "crewai_class_tool"}:
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
                check_id="SHIP-CREWAI-FUNCTION-TOOL-METADATA-MISSING",
                title=f"{tool.name} lacks static CrewAI function-tool metadata",
                severity="medium",
                category="crewai",
                evidence={"missing": missing, "source_type": tool.source_type},
                confidence="high",
                recommendation=(
                    "Add a docstring, description, type annotations, args_schema, or "
                    f"explicit local inventory metadata for CrewAI tool {tool.name}."
                ),
                context=context,
            )
        )

    return findings
