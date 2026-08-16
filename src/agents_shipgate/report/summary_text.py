from __future__ import annotations

from agents_shipgate.core.evidence_actions import (
    evidence_gap_action_text,
    primary_evidence_gap,
)
from agents_shipgate.schemas.report import EvidenceCoverageDecision

_GENERIC_EVIDENCE_REMEDIATION = (
    "Provide a complete MCP export, OpenAPI spec, explicit local tool inventory, "
    "or a broader supported source path; then rerun the scan."
)


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


def primary_evidence_remediation_text(evidence: EvidenceCoverageDecision) -> str:
    """Render the decision engine's rank-1 evidence-gap action.

    Short-form surfaces must project the selected gap's ``next_action``
    instead of replacing framework-specific guidance with a generic source
    list.  Selection is :func:`primary_evidence_gap` — the same one the
    decision ``reason`` and ``agent_summary.first_recommended_action`` read.

    That shared selection is not a promise that the three lines always say the
    same thing; they align on an ``insufficient_evidence`` verdict with an
    addressable gap, and answer different questions otherwise (see
    ``docs/agent-contract-current.md``).  This line always renders the selected
    gap's action, including when nothing is addressable and the reason keeps
    its threshold wording — it is a remediation hint, not a restatement.  The
    fallback exists only for older reports that predate structured gap rows.
    """

    # Current decisions always carry a structured gap here. Only compatibility
    # reports from before evidence_gaps existed can reach this fallback.
    gap = primary_evidence_gap(evidence)
    if gap is None:
        return _GENERIC_EVIDENCE_REMEDIATION
    return evidence_gap_action_text(gap)
