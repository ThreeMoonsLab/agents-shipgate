from __future__ import annotations

from agents_shipgate.schemas.report import EvidenceCoverageDecision


def evidence_coverage_text(evidence: EvidenceCoverageDecision) -> str:
    extras: list[str] = []
    if evidence.low_confidence_tool_count:
        extras.append(f"{evidence.low_confidence_tool_count} low-confidence tool(s)")
    if evidence.source_warning_count:
        extras.append(f"{evidence.source_warning_count} source warning(s)")
    if evidence.human_review_recommended:
        extras.append("human review recommended")
    suffix = f" ({'; '.join(extras)})" if extras else ""
    return f"{evidence.level}{suffix}"
