from __future__ import annotations

import re
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

# Keyword classifier sets. Word-tokenized, so each keyword must match a full
# alphabetic token in the tool name or description (or in the joined auth
# scopes). This avoids substring false positives like "deploy" matching the
# token "deployments" — listing deployments is a read, not an infra change.
# Plurals are listed explicitly where production scopes commonly use them
# (e.g. "stripe:refunds:write"). Compound forms like "deployment" or
# "deployments" are intentionally NOT included for write-implying tags so
# read-only listings don't trip mutation flags.
READ_ONLY_KEYWORDS = {
    "get",
    "list",
    "lookup",
    "preview",
    "search",
    "status",
    "view",
}
WRITE_KEYWORDS = {
    "cancel",
    "charge",
    "create",
    "delete",
    "issue",
    "refund",
    "remove",
    "send",
    "update",
    "write",
}
DESTRUCTIVE_KEYWORDS = {"cancel", "delete", "destroy", "remove"}
FINANCIAL_KEYWORDS = {
    "billing",
    "charge",
    "charges",
    "invoice",
    "invoices",
    "payment",
    "payments",
    "refund",
    "refunds",
}
COMMS_KEYWORDS = {"email", "emails", "message", "messages", "sms"}
EXTERNAL_ACTION_KEYWORDS = {"customer", "external", "send"}
SENSITIVE_KEYWORDS = {
    "credential",
    "credentials",
    "personal",
    "pii",
    "secret",
    "secrets",
    "ssn",
}
CODE_EXEC_KEYWORDS = {"bash", "command", "execute", "python", "shell"}
INFRASTRUCTURE_KEYWORDS = {
    "aws",
    "azure",
    "cluster",
    "clusters",
    "deploy",
    "droplet",
    "droplets",
    "gcp",
    "kubernetes",
    "terraform",
}

_KEYWORD_GATED_SOURCE_TYPES = {"openai_api", "anthropic_api", "sdk_function"}


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


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _add_automatic_hints(tool: Tool) -> None:
    text = f"{tool.name} {tool.description or ''}"
    tokens = _tokenize(text)
    scope_tokens = _tokenize(" ".join(tool.auth.scopes))
    method = str(tool.annotations.get("httpMethod") or "").upper()

    # SDK preview-only safety net runs first so subsequent classifiers can
    # rely on is_effectively_read_only short-circuiting. A function named
    # *_preview that does not declare an HTTP method is treated as read-only
    # at high confidence and exempt from name-keyword write inference.
    is_sdk_preview = (
        tool.source_type == "sdk_function" and "preview" in tokens and not method
    )
    if is_sdk_preview:
        _add_hint(tool, "read_only", "keyword", "high", {"preview_only": True})

    keyword_eligible = (
        tool.source_type in _KEYWORD_GATED_SOURCE_TYPES and not is_sdk_preview
    )
    keyword_source = (
        "openai_api_keyword"
        if tool.source_type == "openai_api"
        else "anthropic_api_keyword"
        if tool.source_type == "anthropic_api"
        else "sdk_keyword"
        if tool.source_type == "sdk_function"
        else "keyword"
    )

    if keyword_eligible and READ_ONLY_KEYWORDS & tokens:
        _add_hint(tool, "read_only", keyword_source, "medium", {})
    if keyword_eligible and WRITE_KEYWORDS & tokens:
        _add_hint(tool, "write", keyword_source, "medium", {})
    if tool.annotations.get("readOnlyHint") is True:
        _add_hint(tool, "read_only", "mcp_annotation", "high", {"readOnlyHint": True})
    if tool.annotations.get("destructiveHint") is True:
        _add_hint(tool, "destructive", "mcp_annotation", "high", {"destructiveHint": True})
        _add_hint(tool, "write", "mcp_annotation", "high", {"destructiveHint": True})

    if method == "DELETE" or DESTRUCTIVE_KEYWORDS & tokens:
        _add_hint(
            tool,
            "destructive",
            "openapi_method" if method else "keyword",
            "high" if method == "DELETE" else "medium",
            {"method": method or None},
        )
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        _add_hint(tool, "write", "openapi_method", "high", {"method": method})

    financial_in_text = bool(FINANCIAL_KEYWORDS & tokens)
    financial_in_scope = bool(FINANCIAL_KEYWORDS & scope_tokens)
    if financial_in_text or financial_in_scope:
        if is_effectively_read_only(tool):
            confidence = "low"
        elif is_write_tool(tool) or "write" in scope_tokens:
            confidence = "high"
        else:
            confidence = "low"
        _add_hint(
            tool,
            "financial_action",
            "auth_scope" if financial_in_scope else "keyword",
            confidence,
            {"scopes": tool.auth.scopes, "method": method or None},
        )
    if COMMS_KEYWORDS & (tokens | scope_tokens):
        confidence = (
            "high"
            if is_write_tool(tool)
            else "low"
            if is_effectively_read_only(tool)
            else "medium"
        )
        _add_hint(tool, "customer_communication", "keyword", confidence, {"method": method or None})
        if (
            not is_effectively_read_only(tool)
            and EXTERNAL_ACTION_KEYWORDS & tokens
        ):
            _add_hint(
                tool, "external_write", "keyword", confidence, {"method": method or None}
            )
    if SENSITIVE_KEYWORDS & (tokens | scope_tokens):
        _add_hint(tool, "sensitive_data_access", "keyword", "medium", {})
    if CODE_EXEC_KEYWORDS & tokens:
        _add_hint(tool, "code_execution", "keyword", "medium", {})
    if INFRASTRUCTURE_KEYWORDS & tokens:
        _add_hint(tool, "infrastructure_change", "keyword", "medium", {})

    # GET endpoints without any mutation evidence are read-only with high
    # confidence. Bumping from medium to high lets is_effectively_read_only
    # short-circuit policy/scope checks for clear reads, even when they pick
    # up a topical keyword like "kubernetes" elsewhere in the path.
    if method == "GET" and not has_risk_tag(tool, WRITE_TAGS):
        _add_hint(tool, "read_only", "openapi_method", "high", {"method": method})


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
