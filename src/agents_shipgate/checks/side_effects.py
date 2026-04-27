from __future__ import annotations

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.risk_hints import (
    has_risk_tag,
    is_effectively_read_only,
    is_write_tool,
    risk_tags,
)


def run(context: ScanContext):
    findings = []
    policy_tools = set(context.manifest.policies.idempotency_tools())
    if context.api_artifacts:
        policy_tools.update(context.api_artifacts.idempotency_tools())
    if context.anthropic_artifacts:
        policy_tools.update(context.anthropic_artifacts.idempotency_tools())
    for tool in context.tools:
        if is_effectively_read_only(tool):
            continue
        if not _needs_idempotency(tool):
            continue
        if tool.name in policy_tools or tool.annotations.get("idempotentHint") is True:
            continue
        if any(parameter.name == "idempotency_key" for parameter in tool.parameters):
            continue
        retry_known = bool(tool.annotations.get("retryPolicy"))
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-SIDEFX-IDEMPOTENCY-MISSING",
                title=f"{tool.name} lacks idempotency evidence",
                severity="critical" if retry_known else "high",
                category="side_effects",
                evidence={"risk_tags": risk_tags(tool, min_confidence="medium"), "retry_policy_known": retry_known},
                confidence="high" if retry_known else "medium",
                recommendation=f"Add an idempotency key, idempotent annotation, or declared idempotency policy for {tool.name}.",
                context=context,
            )
        )
    return findings


def _needs_idempotency(tool) -> bool:
    return is_write_tool(tool) and has_risk_tag(
        tool,
        {"financial_action", "destructive", "external_write"},
        min_confidence="medium",
    )
