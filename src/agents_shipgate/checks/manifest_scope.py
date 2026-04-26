from __future__ import annotations

import re

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import Tool
from agents_shipgate.core.risk_hints import (
    has_risk_tag,
    is_effectively_read_only,
    is_write_tool,
    risk_tags,
)

STOPWORDS = {
    "a",
    "an",
    "and",
    "before",
    "for",
    "from",
    "the",
    "to",
    "with",
    "without",
}

READ_ONLY_TERMS = {
    "answer",
    "lookup",
    "lookups",
    "read",
    "readonly",
    "read-only",
    "search",
    "status",
    "view",
}

WRITE_TERMS = {
    "cancel",
    "create",
    "delete",
    "email",
    "issue",
    "modify",
    "refund",
    "remove",
    "send",
    "update",
    "write",
}


def run(context: ScanContext):
    findings = []
    purpose_text = " ".join(context.manifest.agent.declared_purpose).lower()
    prohibited_actions = context.manifest.agent.prohibited_actions

    if _purpose_is_read_only(purpose_text):
        for tool in context.tools:
            if is_write_tool(tool) and not is_effectively_read_only(tool):
                findings.append(
                    tool_finding(
                        tool=tool,
                        check_id="SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE",
                        title=f"{tool.name} appears outside the declared read-only purpose",
                        severity="high",
                        category="scope",
                        evidence={
                            "declared_purpose": context.manifest.agent.declared_purpose,
                            "risk_tags": risk_tags(tool, min_confidence="medium"),
                        },
                        confidence="medium",
                        recommendation=(
                            f"Remove {tool.name} from this release or update the declared purpose "
                            "and release review to cover write-capable tools."
                        ),
                        context=context,
                    )
                )

    for tool in context.tools:
        if is_effectively_read_only(tool):
            continue
        for prohibited in prohibited_actions:
            if _prohibited_action_is_mitigated(context, tool, prohibited):
                continue
            if _tool_matches_prohibited_action(tool, prohibited):
                findings.append(
                    tool_finding(
                        tool=tool,
                        check_id="SHIP-SCOPE-PROHIBITED-TOOL-PRESENT",
                        title=f"{tool.name} appears to overlap with a prohibited action",
                        severity="high",
                        category="scope",
                        evidence={
                            "prohibited_action": prohibited,
                            "risk_tags": risk_tags(tool, min_confidence="medium"),
                        },
                        confidence="medium",
                        recommendation=(
                            f"Remove {tool.name}, narrow its policy, or revise prohibited_actions "
                            "so the manifest and tool surface do not contradict each other."
                        ),
                        context=context,
                    )
                )
                break

    return findings


def _purpose_is_read_only(purpose_text: str) -> bool:
    if not purpose_text:
        return False
    normalized = purpose_text.replace("_", "-")
    has_read_terms = any(term in normalized for term in READ_ONLY_TERMS)
    has_write_terms = any(term in normalized for term in WRITE_TERMS)
    return has_read_terms and not has_write_terms


def _tool_matches_prohibited_action(tool: Tool, prohibited_action: str) -> bool:
    action_tokens = _tokens(prohibited_action)
    if not action_tokens:
        return False
    tool_text = f"{tool.name} {tool.description or ''}".lower()
    overlap = [token for token in action_tokens if token in tool_text]
    if len(overlap) >= 2:
        return True
    if {"email", "external"} & action_tokens and has_risk_tag(
        tool,
        {"external_write", "customer_communication"},
        min_confidence="medium",
    ):
        return True
    if "refund" in action_tokens and has_risk_tag(
        tool,
        {"financial_action"},
        min_confidence="medium",
    ) and is_write_tool(tool):
        return True
    if "cancel" in action_tokens and has_risk_tag(
        tool,
        {"destructive"},
        min_confidence="medium",
    ):
        return True
    return False


def _prohibited_action_is_mitigated(
    context: ScanContext, tool: Tool, prohibited_action: str
) -> bool:
    tokens = _tokens(prohibited_action)
    if "approval" in tokens and tool.name in context.manifest.policies.approval_tools():
        return True
    if "confirmation" in tokens and tool.name in context.manifest.policies.confirmation_tools():
        return True
    if "idempotency" in tokens and tool.name in context.manifest.policies.idempotency_tools():
        return True
    return False


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", text.lower())
        if token not in STOPWORDS
    }
