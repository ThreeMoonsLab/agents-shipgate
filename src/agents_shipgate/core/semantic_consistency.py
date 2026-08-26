from __future__ import annotations

from agents_shipgate.core.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.core.domain import Tool, ToolSemanticAssessment
from agents_shipgate.core.surface_exclusions import (
    BINDING_GAP_KINDS,
    catalog_subject,
    derived_id_kind,
    unavailable_base_subject,
)
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

    Five claims, each one a way that state could come back:

    1. every excluded subject is in the ledger — a stage cannot narrow
       silently;
    2. every ``evidence_gap`` record is backed by a gap row carrying the same
       subject — a ledger cannot claim an accounting the decision does not
       have;
    3. a subject *this change* newly excluded is always ``evidence_gap`` — the
       pre-existing/newly-arrived distinction is the whole basis on which a
       ``not_claimed`` record is allowed at all;
    4. *no* gap puts a canonical tool id anywhere in ``subject``, which is a
       display label — identity has its own field, and a digest in the label is
       what a reader is shown;
    5. an excluded tool the decision *did* gap is never recorded
       ``not_claimed`` — (2) with the sign flipped, which is the direction a
       second spelling breaks;
    6. an ``unverified`` record is backed by a gap naming the base comparison
       it could not perform, so the word is a pointer and not a softer way of
       saying nothing — and, conversely, no binding row may be ``not_claimed``
       while that comparison stands unperformed, or the fail-closed state is
       erasable by rewriting the rows;
    7. ``gated`` matches the rows it summarizes. Consumers gate on the count,
       and a count nothing checked can be forged past both Pydantic and the
       JSON Schema.

    Claims (2), (3) and (5) join on the canonical tool id rather than the
    display label, because two catalog ids can render the same
    ``name [provider]``.
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

    gaps = decision.evidence_coverage.evidence_gaps
    gap_subjects = {gap.subject for gap in gaps}
    unavailable_base = unavailable_base_subject(report)
    # Requested and not performed. In that state the run cannot distinguish a
    # pre-existing exclusion from one this change introduced, so `not_claimed`
    # — which asserts exactly that distinction — is unavailable to it.
    comparison_unusable = (
        report.binding_surface_diff.base_comparison_requested
        and not report.binding_surface_diff.enabled
    )
    if ledger.gated > ledger.total:
        raise SemanticConsistencyError("exclusion ledger gated exceeds its total")
    visible_gated = sum(
        1 for entry in ledger.entries if entry.accounting != "not_claimed"
    )
    if not ledger.truncated and ledger.gated != visible_gated:
        raise SemanticConsistencyError(
            "exclusion ledger gated does not match its entries"
        )
    if ledger.truncated and ledger.gated < visible_gated:
        raise SemanticConsistencyError(
            "exclusion ledger gated is lower than the acted-on rows it shows"
        )
    for entry in ledger.entries:
        # One join, by the pointer the row carries. Every accounting that
        # claims a gap names it explicitly, so nothing here has to guess which
        # of three subject shapes applies.
        if entry.accounted_by is not None and entry.accounted_by not in gap_subjects:
            raise SemanticConsistencyError(
                f"exclusion {entry.subject!r} points at gap "
                f"{entry.accounted_by!r}, which the decision does not carry"
            )
        if entry.reason == "newly_unbound_tool" and entry.accounting != "evidence_gap":
            raise SemanticConsistencyError(
                f"exclusion {entry.subject!r} was introduced by this change and is not gated"
            )
        # `unverified` says a gap stands in for the per-subject one, and that
        # gap is specifically the unavailable-base one — not any gap the row
        # happens to point at.
        if entry.accounting == "unverified" and entry.accounted_by != unavailable_base:
            raise SemanticConsistencyError(
                f"exclusion {entry.subject!r} is unverified but does not point "
                "at the unavailable base comparison"
            )
        # And the converse. Without it the new fail-closed state was erasable:
        # rewrite the row to `not_claimed`, drop `gated` to 0, and the check
        # passed while the base gap still stood (PR #404 review 2).
        if (
            comparison_unusable
            and entry.stage == "binding"
            and entry.accounting == "not_claimed"
        ):
            raise SemanticConsistencyError(
                f"exclusion {entry.subject!r} is recorded not_claimed while the "
                "base comparison this run requested could not be performed"
            )

    # The join above only catches the ledger over-claiming. Under-claiming — a
    # gap exists and the ledger says `not_claimed` — is the same failure with
    # the sign flipped, and it is what a subject spelled two ways produces: a
    # `partial_binding_evidence` gap carrying the raw canonical id could not be
    # matched against a ledger row carrying `name [provider]`, so a gated tool
    # read as unclaimed and the whole suite stayed green.
    #
    # Checked at the source rather than by re-running the same join, which
    # would just repeat any shared mistake: a gap may never name a catalog tool
    # by its raw id. With one spelling enforced here, the join above is exact.
    #
    # Every kind, not only the kinds the ledger joins. Scoping the rule to the
    # join set is what let the *policy* gaps keep a 64-hex digest in `subject`,
    # which `evidence_gap_headline` prints verbatim into `Improve evidence:` —
    # a reader got a hash where a tool name belongs. Since review 2 the join
    # runs on `subject_id`, so this rule is no longer load-bearing for the join
    # at all: it is what keeps `subject` a *label*, which is the one thing the
    # field is documented to be.
    #
    # Matched by *shape*, not by membership in this run's catalog. Comparing
    # the whole subject against `tool_catalog` missed both spellings that
    # actually shipped: `inputs/policy_packs.py` wrapped the id in a label
    # (`create_refund [tool_v2_6dcebe…]`), and a check plugin can raise a
    # finding carrying a stale or invented id that is in no catalog to compare
    # against. Both are exactly as unreadable as the bare form (PR #408
    # review), and neither is recognisable to a membership test.
    #
    # Every derived id, not only a tool's. Scoping the rule to `tool_v…` left
    # the identical defect standing one subject kind over: a binding issue
    # naming no tool fell back to `agent_v1:7205d836…`, and
    # `samples/conductor_agent` shipped that digest in `subject` and in the
    # decision `reason` under it (#329). A guard scoped to one shape passes
    # vacuously for every other shape — scope it to the property.
    #
    # Position is not quite enough on its own: a *bare* subject that is exactly
    # an id shape is refused, and an adopter may legally name a tool
    # `tool_v2_deadbeef`. So the run's own names exonerate it — structured
    # provenance where there is some, shape where there is none, which is what
    # keeps the plugin-supplied id in no catalog refused (#329 review 3).
    adopter_names = {
        str(row.get("name") or "") for row in report.tool_catalog if isinstance(row, dict)
    } | {node.name for node in report.binding_surface_facts.agents}
    for gap in gaps:
        noun = derived_id_kind(gap.subject)
        if noun is not None and gap.subject.strip() in adopter_names:
            continue
        if noun is not None:
            article = "an" if noun[0] in "aeiou" else "a"
            raise SemanticConsistencyError(
                f"evidence gap labels {article} {noun} {gap.subject!r} with a "
                "derived id rather than its display subject; the id belongs "
                "in subject_id, and this string is what a reader is shown"
            )
    gated_binding_subjects = {
        entry.accounted_by
        for entry in ledger.entries
        if entry.stage == "binding" and entry.accounting == "evidence_gap"
    }
    unrecorded = {
        gap.subject_id
        for gap in gaps
        if gap.kind in BINDING_GAP_KINDS
        and gap.subject_id in excluded_tools
    } - {
        gap.subject_id
        for gap in gaps
        if gap.subject in gated_binding_subjects and gap.subject_id
    }
    if not ledger.truncated and unrecorded:
        raise SemanticConsistencyError(
            "excluded tools carry a binding evidence gap the ledger does not "
            f"account for: {sorted(unrecorded)}"
        )


__all__ = ["SemanticConsistencyError", "validate_semantic_consistency"]
