from __future__ import annotations

from collections import Counter

from agents_shipgate.core.domain import Tool
from agents_shipgate.core.risk_hints import is_high_risk_tool, risk_tags
from agents_shipgate.schemas.common import confidence_rank
from agents_shipgate.schemas.report import Finding, ReportSummary, ToolSurfaceSummary

from .constants import SEVERITY_ORDER


def summarize_findings(findings: list[Finding], tools: list[Tool]) -> ReportSummary:
    active = [finding for finding in findings if not finding.suppressed]
    counts = Counter(finding.severity for finding in active)
    suppressed_count = len(findings) - len(active)
    if counts["critical"] > 0:
        status = "release_blockers_detected"
    elif active:
        status = "warnings_detected"
    elif any(tool.extraction_confidence != "high" for tool in tools):
        status = "human_review_recommended"
    else:
        status = "no_release_blockers_detected"
    return ReportSummary(
        status=status,
        critical_count=counts["critical"],
        high_count=counts["high"],
        medium_count=counts["medium"],
        low_count=counts["low"],
        info_count=counts["info"],
        suppressed_count=suppressed_count,
        human_review_recommended=counts["critical"] > 0 or counts["high"] > 0 or status == "human_review_recommended",
        evidence_coverage="mixed" if _has_mixed_evidence(tools) else "static",
    )


def summarize_tool_surface(tools: list[Tool]) -> ToolSurfaceSummary:
    sources = Counter(tool.source_type for tool in tools)
    return ToolSurfaceSummary(
        total_tools=len(tools),
        high_risk_tools=sum(1 for tool in tools if is_high_risk_tool(tool)),
        sources=dict(sorted(sources.items())),
        wildcard_tools=sum(1 for tool in tools if tool.annotations.get("wildcard_tools") is True),
        missing_descriptions=sum(1 for tool in tools if not (tool.description or "").strip()),
    )


def recommended_actions(findings: list[Finding]) -> list[str]:
    active = sorted(
        [finding for finding in findings if not finding.suppressed],
        key=lambda finding: (SEVERITY_ORDER[finding.severity], finding.check_id),
    )
    actions: list[str] = []
    seen: set[str] = set()
    for finding in active:
        if finding.recommendation in seen:
            continue
        actions.append(finding.recommendation)
        seen.add(finding.recommendation)
        if len(actions) >= 8:
            break
    return actions


def tool_inventory(tools: list[Tool]) -> list[dict[str, object]]:
    # v0.19 reviewer-grade provenance: ``source_path`` / ``source_start_line``
    # are additive optional keys per row. Post-scan renderers
    # (scenario YAML, downstream consumers reading ``report.json``)
    # use this lookup to cite ``path:line`` for tools touched by a
    # finding without re-parsing the artifact. Older consumers ignore
    # the new keys; new consumers can require them for high-risk tools.
    return [
        {
            "tool_id": tool.id,
            "name": tool.name,
            "provider": tool.provider or tool.source_id or tool.source_type,
            "observation_ids": list(tool.observation_ids),
            "source_type": tool.source_type,
            "source_ref": tool.source_ref,
            "source_path": tool.source_path,
            "source_start_line": tool.source_start_line,
            "source_pointer": tool.source_pointer,
            "risk_tags": risk_tags(tool, min_confidence="medium"),
            "risk_tag_confidence": _risk_tag_confidence(tool, min_confidence="medium"),
            "auth_scopes": tool.auth.scopes,
            "owner": tool.owner,
            "confidence": tool.extraction_confidence,
            "semantic_assessment": (
                tool.semantic_assessment.model_dump(mode="json")
                if tool.semantic_assessment is not None
                else None
            ),
        }
        for tool in sorted(tools, key=lambda item: item.id)
    ]


def _risk_tag_confidence(tool: Tool, min_confidence: str) -> dict[str, str]:
    threshold = confidence_rank(min_confidence)
    by_tag: dict[str, str] = {}
    for hint in tool.risk_hints:
        if confidence_rank(hint.confidence) < threshold:
            continue
        current = by_tag.get(hint.tag)
        if current is None or confidence_rank(hint.confidence) > confidence_rank(current):
            by_tag[hint.tag] = hint.confidence
    return dict(sorted(by_tag.items()))


def _has_mixed_evidence(tools: list[Tool]) -> bool:
    return any(
        tool.source_type == "sdk_function" or tool.extraction_confidence != "high"
        for tool in tools
    )
