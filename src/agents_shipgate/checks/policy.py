from __future__ import annotations

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.risk_hints import has_risk_tag, is_effectively_read_only, risk_tags

APPROVAL_TAGS = {
    "financial_action",
    "destructive",
    "infrastructure_change",
    "code_execution",
}

CONFIRMATION_TAGS = {
    "destructive",
    "external_write",
    "customer_communication",
}


def run(context: ScanContext):
    findings = []
    approval_tools = set(context.manifest.policies.approval_tools())
    confirmation_tools = set(context.manifest.policies.confirmation_tools())
    if context.api_artifacts:
        approval_tools.update(context.api_artifacts.approval_tools())
        confirmation_tools.update(context.api_artifacts.confirmation_tools())
    if context.anthropic_artifacts:
        approval_tools.update(context.anthropic_artifacts.approval_tools())
        confirmation_tools.update(context.anthropic_artifacts.confirmation_tools())
    for tool in context.tools:
        if is_effectively_read_only(tool):
            continue
        if (
            has_risk_tag(tool, APPROVAL_TAGS, min_confidence="medium")
            and tool.name not in approval_tools
        ):
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-POLICY-APPROVAL-MISSING",
                    title=f"{tool.name} lacks a declared approval policy",
                    severity="critical",
                    category="policy",
                    evidence={"risk_tags": risk_tags(tool, min_confidence="medium"), "policy_match": None},
                    confidence="high",
                    recommendation=f"Declare an approval policy for {tool.name} or remove this tool from the release.",
                    context=context,
                )
            )
        if (
            has_risk_tag(tool, CONFIRMATION_TAGS, min_confidence="medium")
            and tool.name not in confirmation_tools
        ):
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-POLICY-CONFIRMATION-MISSING",
                    title=f"{tool.name} lacks a declared confirmation policy",
                    severity="high",
                    category="policy",
                    evidence={"risk_tags": risk_tags(tool, min_confidence="medium"), "policy_match": None},
                    confidence="high",
                    recommendation=f"Declare a user confirmation policy for {tool.name} or remove this action from the release.",
                    context=context,
                )
            )
    return findings
