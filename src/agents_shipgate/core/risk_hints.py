from __future__ import annotations

from collections.abc import Iterable

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import Tool, ToolRiskHint, confidence_rank, parse_confidence

HIGH_RISK_TAGS = {
    "destructive",
    "external_write",
    "financial_action",
    "customer_communication",
    "sensitive_data_access",
    "infrastructure_change",
    "code_execution",
}

WRITE_TAGS = {"write", "destructive", "external_write"}


def enrich_tools_with_risk_hints(
    manifest: AgentsShipgateManifest, tools: list[Tool]
) -> list[Tool]:
    enriched = [tool.model_copy(deep=True) for tool in tools]
    for tool in enriched:
        _add_automatic_hints(tool)
        _apply_manual_override(manifest, tool)
    return enriched


def has_risk_tag(tool: Tool, tags: Iterable[str], min_confidence: str | None = None) -> bool:
    wanted = set(tags)
    best_confidence = 0
    for hint in tool.risk_hints:
        if hint.tag in wanted:
            best_confidence = max(best_confidence, confidence_rank(hint.confidence))
    if min_confidence:
        return best_confidence >= confidence_rank(min_confidence)
    return best_confidence > 0


def risk_tags(tool: Tool, min_confidence: str | None = None) -> list[str]:
    threshold = confidence_rank(min_confidence) if min_confidence else 0
    return sorted(
        {
            hint.tag
            for hint in tool.risk_hints
            if confidence_rank(hint.confidence) >= threshold
        }
    )


def is_effectively_read_only(tool: Tool) -> bool:
    if tool.annotations.get("readOnlyHint") is True:
        return True
    return has_risk_tag(tool, {"read_only"}, min_confidence="high") and not has_risk_tag(
        tool, WRITE_TAGS, min_confidence="medium"
    )


def is_high_risk_tool(tool: Tool) -> bool:
    if is_effectively_read_only(tool):
        return False
    return has_risk_tag(tool, HIGH_RISK_TAGS, min_confidence="medium")


def is_write_tool(tool: Tool) -> bool:
    if is_effectively_read_only(tool):
        return False
    return has_risk_tag(tool, WRITE_TAGS, min_confidence="medium")


def _add_automatic_hints(tool: Tool) -> None:
    text = f"{tool.name} {tool.description or ''}".lower()
    method = str(tool.annotations.get("httpMethod") or "").upper()

    if tool.source_type == "openai_api" and any(
        word in text for word in ["get", "list", "lookup", "search", "status", "preview"]
    ):
        _add_hint(tool, "read_only", "openai_api_keyword", "medium", {})
    if tool.source_type == "openai_api" and any(
        word in text
        for word in [
            "create",
            "update",
            "write",
            "send",
            "refund",
            "cancel",
            "delete",
            "remove",
            "charge",
            "issue",
        ]
    ):
        _add_hint(tool, "write", "openai_api_keyword", "medium", {})

    if tool.source_type == "sdk_function" and "preview" in text and not method:
        _add_hint(tool, "read_only", "keyword", "high", {"preview_only": True})
    if tool.annotations.get("readOnlyHint") is True:
        _add_hint(tool, "read_only", "mcp_annotation", "high", {"readOnlyHint": True})
    if tool.annotations.get("destructiveHint") is True:
        _add_hint(tool, "destructive", "mcp_annotation", "high", {"destructiveHint": True})
        _add_hint(tool, "write", "mcp_annotation", "high", {"destructiveHint": True})
    if method == "DELETE" or any(word in text for word in ["cancel", "delete", "remove"]):
        _add_hint(tool, "destructive", "openapi_method" if method else "keyword", "high" if method == "DELETE" else "medium", {"method": method or None})
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        _add_hint(tool, "write", "openapi_method", "high", {"method": method})

    scopes = " ".join(tool.auth.scopes).lower()
    financial_in_text = any(word in text for word in ["refund", "payment", "charge", "invoice"])
    financial_in_scope = any(word in scopes for word in ["refund", "payment", "charge", "invoice"])
    if financial_in_text or financial_in_scope:
        if is_effectively_read_only(tool):
            confidence = "low"
        elif is_write_tool(tool) or "write" in scopes:
            confidence = "high"
        else:
            confidence = "low"
        _add_hint(tool, "financial_action", "auth_scope" if financial_in_scope else "keyword", confidence, {"scopes": tool.auth.scopes, "method": method or None})
    if any(word in text or word in scopes for word in ["send_email", "email", "sms", "message", "customer_email"]):
        confidence = "high" if is_write_tool(tool) else "low" if is_effectively_read_only(tool) else "medium"
        _add_hint(tool, "customer_communication", "keyword", confidence, {"method": method or None})
        if not is_effectively_read_only(tool) and any(word in text for word in ["send", "external", "customer"]):
            _add_hint(tool, "external_write", "keyword", confidence, {"method": method or None})
    if any(word in text or word in scopes for word in ["ssn", "pii", "personal data", "secret", "credential"]):
        _add_hint(tool, "sensitive_data_access", "keyword", "medium", {})
    if any(word in text for word in ["shell", "execute", "command", "python", "bash"]):
        _add_hint(tool, "code_execution", "keyword", "medium", {})
    if any(word in text for word in ["deploy", "kubernetes", "terraform", "aws", "gcp", "azure"]):
        _add_hint(tool, "infrastructure_change", "keyword", "medium", {})

    if method == "GET" and not has_risk_tag(tool, WRITE_TAGS):
        _add_hint(tool, "read_only", "openapi_method", "medium", {"method": method})


def _apply_manual_override(manifest: AgentsShipgateManifest, tool: Tool) -> None:
    override = manifest.risk_overrides.tools.get(tool.name)
    if not override:
        return
    if override.owner:
        tool.owner = override.owner
    if override.remove_tags:
        remove = set(override.remove_tags)
        tool.risk_hints = [hint for hint in tool.risk_hints if hint.tag not in remove]
    for tag in override.tags:
        _add_hint(
            tool,
            tag,
            "manual",
            "high",
            {"reason": override.reason, "confidence": override.confidence},
        )


def _add_hint(
    tool: Tool, tag: str, source: str, confidence: str, evidence: dict[str, object]
) -> None:
    confidence_value = confidence if confidence in {"low", "medium", "high"} else "medium"
    for existing in tool.risk_hints:
        if existing.tag == tag and existing.source == source:
            if confidence_rank(confidence_value) > confidence_rank(existing.confidence):
                existing.confidence = parse_confidence(confidence_value)
                existing.evidence.update(evidence)
            return
    tool.risk_hints.append(
        ToolRiskHint(
            tag=tag,
            source=source,
            confidence=parse_confidence(confidence_value),
            evidence={key: value for key, value in evidence.items() if value is not None},
        )
    )
