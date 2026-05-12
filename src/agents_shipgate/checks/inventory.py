from __future__ import annotations

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.context import ScanContext


def run(context: ScanContext):
    findings = []
    if not context.tools:
        if context.codex_plugin_artifacts is None:
            findings.append(
                agent_finding(
                    check_id="SHIP-INVENTORY-NOT-ENUMERABLE",
                    title="Tool surface cannot be enumerated",
                    severity="high",
                    category="inventory",
                    evidence={"tool_sources": [source.id for source in context.manifest.tool_sources]},
                    confidence="high",
                    recommendation="Declare at least one local MCP tools JSON or OpenAPI source with enumerable tools.",
                    context=context,
                )
            )
    for tool in context.tools:
        if tool.annotations.get("wildcard_tools") is True:
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-INVENTORY-WILDCARD-TOOLS",
                    title="Wildcard tool exposure declared",
                    severity="high",
                    category="inventory",
                    evidence={"source_id": tool.source_id, "source_ref": tool.source_ref},
                    confidence="high",
                    recommendation="Replace wildcard tool exposure with an explicit tool allowlist before release review.",
                    context=context,
                )
            )
    if len(context.tools) > 50:
        findings.append(
            agent_finding(
                check_id="SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE",
                title="Large tool surface requires review",
                severity="medium",
                category="inventory",
                evidence={"tool_count": len(context.tools), "threshold": 50},
                confidence="medium",
                recommendation="Review whether the release needs all declared tools or split high-risk capabilities into a smaller surface.",
                context=context,
            )
        )
    if context.manifest.environment.target == "production":
        low_confidence_tools = [
            tool.name for tool in context.tools if tool.extraction_confidence != "high"
        ]
        if low_confidence_tools:
            findings.append(
                agent_finding(
                    check_id="SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE",
                    title="Production target includes low-confidence tool extraction",
                    severity="high",
                    category="inventory",
                    evidence={"tools": low_confidence_tools},
                    confidence="high",
                    recommendation=(
                        "Replace low-confidence SDK-derived metadata with manifest, MCP, or OpenAPI "
                        "tool declarations before production promotion."
                    ),
                    context=context,
                )
            )
    return findings
