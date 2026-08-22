from __future__ import annotations

from agents_shipgate.core.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.core.domain import Tool, ToolSemanticAssessment
from agents_shipgate.core.surface_exclusions import catalog_subject
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.semantic import ToolSemanticEvidence


class SemanticConsistencyError(RuntimeError):
    """Raised when a public artifact diverges from the central assessment."""


def validate_semantic_consistency(
    report: ReadinessReport,
    tools: list[Tool],
) -> None:
    """Fail closed when semantic evidence and public gate surfaces drift."""

    decision = report.release_decision
    if decision is None:
        raise SemanticConsistencyError("emitted report has no release decision")
    if (
        decision.static_analysis_only is not True
        or decision.runtime_behavior_verified is not False
        or decision.static_verdict_disclaimer != STATIC_VERDICT_DISCLAIMER
    ):
        raise SemanticConsistencyError("release decision lost the static-verdict boundary")

    assessments: dict[tuple[str, str | None, str | None], ToolSemanticAssessment] = {}
    for tool in tools:
        if tool.semantic_assessment is None:
            raise SemanticConsistencyError(f"tool {tool.name!r} has no central semantic assessment")
        key = (tool.id, tool.source_id, tool.source_ref)
        if key in assessments:
            raise SemanticConsistencyError(f"duplicate assessed tool identity for {tool.name!r}")
        assessments[key] = tool.semantic_assessment

    assessed_ids = {key[0] for key in assessments}
    graph = report.binding_surface_facts
    if assessed_ids != set(graph.reachable_tool_ids):
        raise SemanticConsistencyError(
            "binding graph reachable tools do not match the assessed tool surface"
        )
    inventory_ids = {str(item.get("tool_id")) for item in report.tool_inventory}
    if inventory_ids != assessed_ids:
        raise SemanticConsistencyError("tool_inventory is not the reachable tool surface")
    catalog_ids = {str(item.get("tool_id")) for item in report.tool_catalog}
    graph_catalog_ids = (
        set(graph.reachable_tool_ids)
        | set(graph.possible_tool_ids)
        | set(graph.unbound_tool_ids)
    )
    if catalog_ids != graph_catalog_ids:
        raise SemanticConsistencyError("tool_catalog does not match binding graph partitions")
    _validate_exclusion_ledger(report)

    actions = {
        (action.tool_id, action.source_id, action.source_ref): action
        for action in report.action_surface_facts.actions
    }
    if set(actions) != set(assessments):
        raise SemanticConsistencyError(
            "action surface does not enumerate exactly the assessed tool surface"
        )
    for key, assessment in assessments.items():
        action = actions[key]
        tool_name = action.tool_name
        expected_evidence = ToolSemanticEvidence.model_validate(
            assessment.model_dump(mode="python")
        )
        if action.effect != assessment.conservative_effect:
            raise SemanticConsistencyError(
                f"action effect drift for {tool_name!r}: "
                f"{action.effect!r} != {assessment.conservative_effect!r}"
            )
        if action.semantic_assessment is None or action.semantic_assessment != expected_evidence:
            raise SemanticConsistencyError(f"action semantic evidence drift for {tool_name!r}")

    for capability in report.capability_facts:
        assessment = assessments.get(
            (capability.tool_id or "", capability.source_id, capability.source_ref)
        )
        if assessment is None and capability.tool_id is None:
            candidates = [
                candidate
                for (tool_id, source_id, source_ref), candidate in assessments.items()
                if source_id == capability.source_id and source_ref == capability.source_ref
            ]
            assessment = candidates[0] if len(candidates) == 1 else None
        if assessment is None:
            raise SemanticConsistencyError(
                f"capability {capability.id!r} references an unassessed tool"
            )
        if capability.effect != assessment.conservative_effect:
            raise SemanticConsistencyError(f"capability effect drift for {capability.tool_name!r}")
        if (
            capability.semantic_assessment is None
            or capability.semantic_assessment
            != ToolSemanticEvidence.model_validate(assessment.model_dump(mode="python"))
        ):
            raise SemanticConsistencyError(
                f"capability semantic evidence drift for {capability.tool_name!r}"
            )

    coverage = decision.evidence_coverage.semantic_coverage
    pass_eligible = sum(1 for assessment in assessments.values() if assessment.pass_eligible)
    if coverage.total_actions != len(assessments):
        raise SemanticConsistencyError("semantic total_actions does not match tools")
    if coverage.pass_eligible_actions != pass_eligible:
        raise SemanticConsistencyError("semantic pass_eligible_actions does not match assessments")
    identity_coverage = decision.evidence_coverage.identity_coverage
    binding_coverage = decision.evidence_coverage.binding_coverage
    identity_eligible = sum(
        1 for assessment in assessments.values() if assessment.identity.pass_eligible
    )
    if identity_coverage.canonical_tools != len(assessments):
        raise SemanticConsistencyError("identity canonical_tools does not match tools")
    if identity_coverage.pass_eligible_tools != identity_eligible:
        raise SemanticConsistencyError("identity pass_eligible_tools does not match assessments")
    if decision.decision == "passed" and (
        coverage.gap_count
        or binding_coverage.gap_count
        or not graph.pass_eligible
        or bool(graph.possible_tool_ids)
        or coverage.review_concern_count
        or identity_coverage.gap_count
        or identity_eligible != len(assessments)
        or pass_eligible != len(assessments)
        or bool(report.policy_evidence_gaps)
        or decision.evidence_coverage.policy_gap_count
    ):
        raise SemanticConsistencyError(
            "passed requires every semantic assessment to be pass-eligible"
        )
    if decision.evidence_coverage.policy_gap_count != len(report.policy_evidence_gaps):
        raise SemanticConsistencyError("policy evidence gap coverage drift")
    if any(
        finding.support is not None
        and not finding.support.blocking_eligible
        and finding.blocks_release
        for finding in report.findings
    ):
        raise SemanticConsistencyError(
            "unsupported finding cannot assert blocks_release"
        )
    if any(
        item.support is not None and not item.support.blocking_eligible
        for item in decision.blockers
    ):
        raise SemanticConsistencyError(
            "release blocker lacks authoritative predicate support"
        )
    if (
        decision.decision == "insufficient_evidence"
        and decision.fail_policy.ci_mode == "strict"
        and (decision.fail_policy.would_fail_ci is not True or decision.fail_policy.exit_code != 20)
    ):
        raise SemanticConsistencyError(
            "strict insufficient_evidence must produce would_fail_ci=true, exit_code=20"
        )


def _validate_exclusion_ledger(report: ReadinessReport) -> None:
    """Conservation: nothing leaves the analysed surface unaccounted for.

    The partition check above is the first half — ``observed == analysed ∪
    excluded`` for the tool catalog. This is the half that was missing: the
    excluded side must be *recorded*, and each record must be true about the
    release decision. ``unbound_tools: 1`` next to ``gap_count: 0`` satisfied
    the partition perfectly; what it violated is that a subject the diff
    removed from analysis reached no gap (#403).

    Three claims, each one a way that state could come back:

    1. every excluded subject is in the ledger — a stage cannot narrow
       silently;
    2. every ``evidence_gap`` record is backed by a gap row carrying the same
       subject — a ledger cannot claim an accounting the decision does not
       have;
    3. a subject *this change* newly excluded is always ``evidence_gap`` — the
       pre-existing/newly-arrived distinction is the whole basis on which a
       ``not_claimed`` record is allowed at all.
    """

    decision = report.release_decision
    assert decision is not None  # caller checked
    ledger = report.surface_exclusions
    graph = report.binding_surface_facts

    if ledger.total != len(ledger.entries) and not ledger.truncated:
        raise SemanticConsistencyError("exclusion ledger total disagrees with its entries")
    if ledger.truncated and ledger.total <= len(ledger.entries):
        raise SemanticConsistencyError("exclusion ledger claims truncation it did not apply")

    excluded_tools = set(graph.possible_tool_ids) | set(graph.unbound_tool_ids)
    binding_subjects = {
        entry.subject for entry in ledger.entries if entry.stage == "binding"
    }
    by_id = {
        str(row.get("tool_id")): row for row in report.tool_catalog if row.get("tool_id")
    }
    expected_subjects = {
        catalog_subject(by_id.get(tool_id) or {"tool_id": tool_id})
        for tool_id in excluded_tools
    }
    # Compared as sets, not counts: two catalog rows can render the same
    # subject, so a count comparison would pass while naming the wrong tool.
    if not ledger.truncated and not expected_subjects <= binding_subjects:
        raise SemanticConsistencyError(
            "binding graph excluded tools the exclusion ledger does not record: "
            f"{sorted(expected_subjects - binding_subjects)}"
        )

    gap_subjects = {gap.subject for gap in decision.evidence_coverage.evidence_gaps}
    for entry in ledger.entries:
        if entry.accounting == "evidence_gap" and entry.subject not in gap_subjects:
            raise SemanticConsistencyError(
                f"exclusion {entry.subject!r} claims an evidence gap that does not exist"
            )
        if entry.reason == "newly_unbound_tool" and entry.accounting != "evidence_gap":
            raise SemanticConsistencyError(
                f"exclusion {entry.subject!r} was introduced by this change and is not gated"
            )
    if ledger.gated and not decision.evidence_coverage.evidence_gaps:
        raise SemanticConsistencyError(
            "exclusion ledger reports gated exclusions with no evidence gaps"
        )


__all__ = ["SemanticConsistencyError", "validate_semantic_consistency"]
