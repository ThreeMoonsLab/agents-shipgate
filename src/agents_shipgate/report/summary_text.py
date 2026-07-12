from __future__ import annotations

from agents_shipgate.schemas.report import EvidenceCoverageDecision


def evidence_coverage_text(evidence: EvidenceCoverageDecision) -> str:
    extras: list[str] = []
    if evidence.low_confidence_tool_count:
        extras.append(f"{evidence.low_confidence_tool_count} low-confidence tool(s)")
    if evidence.source_warning_count:
        extras.append(f"{evidence.source_warning_count} source warning(s)")
    semantic = evidence.semantic_coverage
    binding = evidence.binding_coverage
    if binding.gap_count:
        extras.append(f"{binding.gap_count} binding evidence gap(s)")
    if binding.total_catalog_tools:
        extras.append(
            f"{binding.reachable_tools}/{binding.total_catalog_tools} catalog tools reachable"
        )
    if semantic.gap_count:
        extras.append(f"{semantic.gap_count} semantic evidence gap(s)")
    if semantic.review_concern_count:
        extras.append(
            f"{semantic.review_concern_count} semantic review concern(s)"
        )
    if semantic.total_actions:
        extras.append(
            f"{semantic.pass_eligible_actions}/{semantic.total_actions} "
            "actions pass-eligible"
        )
    if evidence.human_review_recommended:
        extras.append("human review recommended")
    suffix = f" ({'; '.join(extras)})" if extras else ""
    return f"{evidence.level}{suffix}"
