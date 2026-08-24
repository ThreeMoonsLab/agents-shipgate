from __future__ import annotations

import math
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agents_shipgate.ci.exit_policy import (
    effective_fail_on,
    exit_code_for_report,
)
from agents_shipgate.core.domain import (
    DECLARATION_OVERRIDE_SOURCE,
    SemanticIssueKind,
    Tool,
    ToolSemanticAssessment,
)
from agents_shipgate.core.evidence_actions import (
    evidence_gap_command,
    evidence_gap_headline,
    evidence_gap_target,
    primary_evidence_gap,
    yaml_scalar,
)
from agents_shipgate.core.semantic_assessment import (
    effect_repair,
)
from agents_shipgate.core.source_warnings import unresolved_adk_tool_symbols
from agents_shipgate.core.surface_exclusions import (
    catalog_subject,
    unavailable_base_subject,
)
from agents_shipgate.schemas.bindings import (
    AgentBindingGraphAssessment,
    AgentBindingIssue,
    AgentBindingNode,
)
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.report import (
    AcknowledgedEffectOverride,
    BaselineDelta,
    BindingCoverageDecision,
    ContributionRule,
    ContributionRuleName,
    EvidenceCoverageDecision,
    EvidenceGap,
    EvidenceGapAction,
    FailPolicy,
    Finding,
    IdentityCoverageDecision,
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
    ReleaseDecisionStatus,
    SemanticCoverageDecision,
)

# Thresholds for the `insufficient_evidence` decision state. Private
# module-level constants so they're tunable in code without expanding
# the manifest or CLI surface. Examined and deliberately HELD at these
# values: see benchmark/miner/CALIBRATION.md — the available corpora cannot
# justify a change (the real corpus is unlabeled; the labeled constructed set
# has a single threshold-exercising point at the robust extreme). Editing
# either constant fails test_ie_threshold_constants_are_frozen
# (tests/test_release_decision.py), so a change is a deliberate recalibration
# that must update CALIBRATION.md too. Recalibrate only after the human
# labeling pass + a re-mine (prerequisites in that doc).
_LOW_CONFIDENCE_TOOL_RATIO = 0.5
_MAX_TOLERATED_SOURCE_WARNINGS = 3


def _low_confidence_tool_threshold(tool_count: int) -> int:
    return max(1, math.ceil(tool_count * _LOW_CONFIDENCE_TOOL_RATIO))


def evidence_below_ie_threshold(evidence: EvidenceCoverageDecision, *, tool_count: int) -> bool:
    """True when extraction evidence is too weak to gate release on its own.

    This is the exact predicate `build_release_decision` uses to raise the
    `insufficient_evidence` verdict, exposed so downstream projections can
    reason about it directly. An active high/critical review finding *elevates*
    such a case to `review_required` (a more actionable verdict), so the verdict
    label alone no longer tells a consumer whether evidence was degraded — the
    verify fix_task authority routing needs this predicate to keep
    degraded-evidence cases human-routed regardless of which of the two
    non-mergeable verdicts they landed on.
    """
    return (
        evidence.binding_coverage.gap_count > 0
        or
        evidence.semantic_coverage.gap_count > 0
        or evidence.policy_gap_count > 0
        or any(
            gap.kind == "source_warning" and gap.next_action.kind == "provide_source"
            for gap in evidence.evidence_gaps
        )
        or evidence.low_confidence_tool_count >= _low_confidence_tool_threshold(tool_count)
        or evidence.source_warning_count > _MAX_TOLERATED_SOURCE_WARNINGS
    )


def has_measurable_evidence_gaps(evidence: EvidenceCoverageDecision) -> bool:
    """True when the scan actually measured an evidence gap.

    Deliberately *not* ``evidence.human_review_recommended``: that flag is
    overloaded — ``summarize_findings`` also sets it for any critical/high
    finding — so a clean static scan with one high finding and zero gaps reads
    as "evidence incomplete" to anything that trusts it. ``_decision_reason``
    already guards its "evidence coverage is incomplete" wording with the same
    measurable inputs; this exposes that rule so agent-facing projections
    cannot invent a gap the report does not contain (#362 review 3).

    Broader than :func:`evidence_below_ie_threshold`, which asks whether the
    gaps are bad enough to withhold a verdict. This asks only whether any
    exist.
    """

    return (
        evidence.binding_coverage.gap_count > 0
        or evidence.semantic_coverage.gap_count > 0
        or evidence.policy_gap_count > 0
        or evidence.low_confidence_tool_count > 0
        or evidence.source_warning_count > 0
        or bool(evidence.evidence_gaps)
    )


def build_release_decision(
    *,
    report: ReadinessReport,
    tools: list[Tool],
    ci_mode: str,
    fail_on: list[Severity] | None,
    new_findings_only: bool,
    tool_catalog: Sequence[Tool] | None = None,
) -> ReleaseDecision:
    """Compute the release decision.

    ``tools`` is the root-reachable surface — the population every count and
    every gate reads. ``tool_catalog`` is everything extraction found, reachable
    or not, and is used for exactly one thing: scaffolding the binding
    declaration for a repository whose catalog is populated and whose reachable
    set is therefore empty (#361). Nothing gates on it. It defaults to ``tools``
    so a caller that never had the distinction keeps its behaviour.
    """

    fail_on_resolved = effective_fail_on(ci_mode, fail_on)
    catalog = tools if tool_catalog is None else tool_catalog

    # blockers/review_items consider the full findings set, NOT
    # new_findings_only: baseline-matched criticals must remain visible
    # as accepted debt in review_items. The new_findings_only filter
    # only affects fail_policy.exit_code (via exit_code_for_report).
    # v0.17: iterate report.findings directly so the contribution_rules
    # audit row set is exhaustive (suppressed findings get an audit row
    # too, classified as excluded/suppressed).
    blockers: list[ReleaseDecisionItem] = []
    review_items: list[ReleaseDecisionItem] = []
    contribution_rules: list[ContributionRule] = []
    blocker_severities: set[Severity] = {"critical", *fail_on_resolved}
    reachable_tool_ids = set(report.binding_surface_facts.reachable_tool_ids)
    # Phase 2c: an active (non-accepted) high/critical finding routed to
    # review is a *named* concern — the gate has decided "a human must look",
    # which is more specific than "insufficient_evidence". Tracked here so the
    # decision can prefer review_required over IE when one exists (see below).
    has_active_high_review = False

    # v0.17: iterate the FULL findings list (not just `active`) so the
    # audit row set is exhaustive over report.findings. The branching
    # below mirrors the original active classification exactly — same
    # `if/elif/elif` shape, same fall-through to silent-drop — so the
    # blockers[]/review_items[] lists are byte-identical to v0.16. The
    # only addition is one ContributionRule per finding documenting
    # which branch fired (or, for the silent-drop tail, which baseline
    # acceptance silently consumed it).
    for finding in report.findings:
        if finding.support is not None and not finding.support.policy_eligible:
            contribution_rules.append(
                _rule(
                    finding,
                    category="excluded",
                    rule="unsupported_evidence",
                    rationale=(
                        "Finding lacks policy-eligible predicate support; it cannot "
                        "become a blocker or named review concern."
                    ),
                )
            )
            continue
        if finding.suppressed:
            if _is_mandatory_current_control(finding):
                # A suppression explains accepted noise; it cannot satisfy a
                # mandatory current-surface control. Keep built-in control
                # blockers non-waivable so a missing rollback/approval/etc.
                # cannot be converted into `passed` by checks.ignore.
                blockers.append(_to_item(finding))
                contribution_rules.append(
                    _rule(
                        finding,
                        category="blocker",
                        rule="policy_block_new",
                        rationale=(
                            "blocks_release=true; suppression cannot waive a "
                            "mandatory current-surface control."
                        ),
                    )
                )
                continue
            contribution_rules.append(
                _rule(
                    finding,
                    category="excluded",
                    rule="suppressed",
                    rationale="Finding suppressed via checks.ignore in the manifest.",
                )
            )
            continue
        # Branch 1: explicit policy blocker, not baseline-matched.
        if finding.blocks_release and finding.baseline_status != "matched":
            blockers.append(_to_item(finding))
            contribution_rules.append(
                _rule(
                    finding,
                    category="blocker",
                    rule="policy_block_new",
                    rationale=(
                        f"blocks_release=true and baseline_status="
                        f"{finding.baseline_status or 'null'}; "
                        "explicit policy blocker."
                    ),
                )
            )
            continue
        # Branch 2: severity in active blocker tier, not baseline-matched.
        if finding.baseline_status != "matched" and finding.severity in blocker_severities:
            blockers.append(_to_item(finding))
            contribution_rules.append(
                _rule(
                    finding,
                    category="blocker",
                    rule="severity_block_new",
                    rationale=(
                        f"severity={finding.severity} is in blocker tier "
                        f"({sorted(blocker_severities)}); "
                        f"baseline_status={finding.baseline_status or 'null'}."
                    ),
                )
            )
            continue
        # Branch 3: review tier (severity C/H/M or requires_human_review).
        # The rule name distinguishes WHY the finding landed here:
        # - matched policy → policy_baseline_accepted
        # - matched severity-tier → severity_baseline_accepted
        # - otherwise → review_required (severity in C/H/M without
        #   matching blocker tier, or requires_human_review=True)
        if (
            finding.severity in {"critical", "high", "medium"}
            or finding.requires_human_review is True
        ):
            review_items.append(_to_item(finding))
            if (
                finding.severity in {"critical", "high"}
                and finding.baseline_status != "matched"
                and finding.tool_id in reachable_tool_ids
            ):
                has_active_high_review = True
            contribution_rules.append(
                _rule(
                    finding,
                    category="review_item",
                    rule=_review_rule_for(finding, blocker_severities),
                    rationale=_review_rationale_for(finding, blocker_severities),
                )
            )
            continue
        # Branch 4 (fall-through): sub-threshold or silently-accepted
        # baseline debt below the review tier. Original code dropped
        # these silently; v0.17 records why.
        contribution_rules.append(
            _rule(
                finding,
                category="excluded",
                rule=_excluded_rule_for(finding, blocker_severities),
                rationale=_excluded_rationale_for(finding, blocker_severities),
            )
        )

    low_confidence_tool_count = sum(1 for tool in tools if tool.extraction_confidence != "high")
    semantic_coverage, semantic_gaps = _semantic_coverage(tools)
    identity_coverage = _identity_coverage(tools)
    binding_coverage, binding_gaps = _binding_coverage(report, catalog)
    evidence = EvidenceCoverageDecision(
        level=report.summary.evidence_coverage,
        human_review_recommended=report.summary.human_review_recommended,
        source_warning_count=len(report.source_warnings),
        low_confidence_tool_count=low_confidence_tool_count,
        # Semantic gaps lead because they are zero-tolerance gate inputs;
        # extraction/source gaps retain their existing deterministic order.
        evidence_gaps=[
            *binding_gaps,
            *semantic_gaps,
            *report.policy_evidence_gaps,
            *_evidence_gaps(report, tools),
        ],
        semantic_coverage=semantic_coverage,
        identity_coverage=identity_coverage,
        binding_coverage=binding_coverage,
        policy_gap_count=len(report.policy_evidence_gaps),
    )

    if report.baseline is None:
        baseline_delta = BaselineDelta(enabled=False)
    else:
        baseline_delta = BaselineDelta(
            enabled=True,
            path=report.baseline.path,
            matched_count=report.baseline.matched_count,
            new_count=report.baseline.new_count,
            resolved_count=report.baseline.resolved_count,
        )

    evidence_is_degraded = evidence_below_ie_threshold(evidence, tool_count=len(tools))
    has_semantic_gaps = semantic_coverage.gap_count > 0
    has_binding_gaps = binding_coverage.gap_count > 0
    has_policy_gaps = bool(report.policy_evidence_gaps)
    has_semantic_review_concerns = semantic_coverage.review_concern_count > 0

    decision: ReleaseDecisionStatus
    if blockers:
        decision = "blocked"
    elif has_active_high_review:
        # Phase 2c: a named, active high/critical concern on a proven-reachable
        # capability is not "insufficient evidence" — the
        # gate HAS something concrete for a human to review. Prefer the
        # actionable review_required over the vaguer insufficient_evidence.
        # Both are equally non-auto-mergeable (can_merge_without_human=False),
        # so this loses no safety; the low-confidence detail is still carried
        # in evidence_coverage.evidence_gaps. IE stays the verdict when the
        # only signal is weak evidence with no named high concern. NOTE: when
        # evidence is *also* degraded here, the verify fix_task still routes to
        # a human via evidence_below_ie_threshold (the same predicate), so the
        # elevation never opens an auto-fix path on weak evidence.
        decision = "review_required"
    elif has_binding_gaps:
        decision = "insufficient_evidence"
    elif has_semantic_gaps:
        # v0.29: semantic gaps are not Findings. This makes them immune to
        # baselines, suppressions, severity overrides, human acknowledgement,
        # and --no-heuristics. One unresolved action is sufficient; healthy
        # actions never dilute it.
        decision = "insufficient_evidence"
    elif has_policy_gaps:
        decision = "insufficient_evidence"
    elif evidence_is_degraded:
        decision = "insufficient_evidence"
    elif (
        review_items
        or has_semantic_review_concerns
        or evidence.human_review_recommended
        or evidence.source_warning_count > 0
    ):
        # Sub-threshold source warnings still warrant review.
        # summarize_findings() doesn't fold source_warning_count into
        # human_review_recommended (it tracks only tool confidence and
        # critical/high findings), so route any source warning here
        # explicitly. Otherwise 1-3 warnings with no findings would
        # silently pass.
        decision = "review_required"
    else:
        decision = "passed"

    reason = _decision_reason(decision, blockers, review_items, evidence)

    # The canonical decision is computed before FailPolicy. Semantic gaps
    # remain strict-CI failures even when a higher-precedence named concern
    # labels the decision review_required instead of insufficient_evidence.
    exit_code = exit_code_for_report(
        report,
        ci_mode,
        fail_on=fail_on,
        new_findings_only=new_findings_only,
        release_decision=decision,
        has_semantic_gaps=has_semantic_gaps or has_binding_gaps or has_policy_gaps,
    )
    fail_policy = FailPolicy(
        ci_mode=ci_mode,
        fail_on=fail_on_resolved,
        new_findings_only=new_findings_only,
        would_fail_ci=(exit_code != 0),
        exit_code=exit_code,
    )

    return ReleaseDecision(
        decision=decision,
        reason=reason,
        blockers=blockers,
        review_items=review_items,
        evidence_coverage=evidence,
        baseline_delta=baseline_delta,
        fail_policy=fail_policy,
        contribution_rules=contribution_rules,
    )


def _is_mandatory_current_control(finding: Finding) -> bool:
    if finding.check_id in _MANDATORY_CURRENT_CONTROL_CHECKS:
        return True
    return (
        finding.check_id == "SHIP-ACTION-POLICY-VIOLATION"
        and finding.evidence.get("policy_id") == "builtin-high-impact-approval"
    )


# Framework source-type prefixes that support an explicit local tool
# inventory in the manifest. The gap action points the user at the exact
# manifest key; everything else degrades to the generic provide_source
# action (full MCP export / OpenAPI spec / explicit inventory file).
_INVENTORY_MANIFEST_KEYS: tuple[tuple[str, str], ...] = (
    ("langchain", "langchain.tool_inventories"),
    ("crewai", "crewai.tool_inventories"),
    ("google_adk", "google_adk.tool_inventories"),
    ("n8n", "n8n.tool_inventories"),
)

# Filename of the advisory skeleton scan writes next to report.json when
# low-confidence tools exist (see cli/scan/writing.py). Referenced here
# so the gap rows and the artifact never drift apart.
SUGGESTED_INVENTORY_FILENAME = "suggested-inventory.json"

# Filename of the advisory declaration scaffold scan writes next to
# report.json whenever any evidence gap carries a ``declaration_template``
# (see cli/scan/writing.py). The templates themselves are generated here; the
# scaffold only assembles them into one reviewable manifest snippet so the
# one-time human declaration is a paste, not a schema hunt. Every value stays
# ``<REVIEW_REQUIRED>``: the tool must never guess an effect, an authority, or
# a binding, and a placeholder can never satisfy a gap.
SUGGESTED_DECLARATIONS_FILENAME = "suggested-declarations.yaml"
REVIEW_REQUIRED_SENTINEL = "<REVIEW_REQUIRED>"


def _inventory_remediation(
    manifest_key: str, source_id: str | None, *, rerun: str
) -> str:
    """The prescribed repair for a source static extraction cannot enumerate.

    Two gap kinds prescribe the same repair (``incomplete_surface`` and
    ``low_confidence_tool``), so the words live here once. ``source_id`` is not
    decoration: an inventory referenced without it is an *independent* source
    whose entries merely share names with the extracted ones, which grows the
    catalog, leaves this gap open, and makes the action selectors that used to
    resolve ambiguous (#386). Naming the source is what turns the file into a
    completion of the surface that raised the gap.
    """

    binding = (
        f"`- {{path: <saved file>, source_id: {yaml_scalar(source_id)}}}`"
        if source_id
        else "an entry carrying `source_id: <the source above>`"
    )
    return (
        "Review the skeleton written next to report.json, save it in your "
        f"repo, then reference it from `{manifest_key}` in shipgate.yaml as "
        f"{binding} — without `source_id` the inventory adds same-named tools "
        f"beside this source instead of completing it — then {rerun}"
    )


# Root-selection scaffold for binding gaps. Module level so the guard test can
# see it: a template must ask, never answer, and this one previously shipped a
# ``declarations`` row pre-filled with ``complete: true`` / ``tools: []``,
# which stated that the agent definitively reaches no tools.
AGENT_BINDINGS_ROOT_TEMPLATE: dict[str, object] = {
    "agent_bindings": {
        "root": {
            "source_id": REVIEW_REQUIRED_SENTINEL,
            "object": REVIEW_REQUIRED_SENTINEL,
        },
    }
}

# Ceiling on the tool rows a closed-world binding declaration is scaffolded
# with. Above it the template is withheld rather than truncated: a
# ``declarations`` row is a claim that the listed tools are ALL the agent can
# reach, so a list silently cut at N would be false exactly where the reviewer
# is least able to notice — and a repository with hundreds of unbound catalog
# entries is telling us to wire the binding in source, not to retype it.
_MAX_SCAFFOLDED_BINDING_TOOLS = 50


def _inventory_declaration_template(
    manifest_key: str, source_id: str | None
) -> dict[str, object] | None:
    """The manifest wiring that joins a reviewed inventory to its source.

    ``_inventory_remediation`` already prescribes this entry as prose. Emitting
    it as a template puts it in the file the reader was actually told to edit,
    which is the whole point of the scaffold (#388): the vocabulary and the
    shape stop being something to reconstruct from a sentence.

    Withheld when the source has no id: an inventory referenced without
    ``source_id`` is an *independent* source added beside the extracted tools
    instead of completing them, so the gap that asked for the file stays open
    (#386). A template that quietly does that is worse than none.
    """

    if not source_id:
        return None
    block, _, key = manifest_key.partition(".")
    if not block or not key:
        return None
    return {
        block: {key: [{"path": REVIEW_REQUIRED_SENTINEL, "source_id": source_id}]}
    }


def _binding_declaration_template(
    graph: AgentBindingGraphAssessment,
    issue: AgentBindingIssue,
    tool_catalog: Sequence[Tool],
) -> dict[str, object] | None:
    """The manifest block one binding issue is repaired in, or ``None``.

    Two binding issues are scaffoldable, and they want different blocks.

    ``ambiguous_root_agent`` wants root *selection* only. In a decorator-only
    repository there is no agent object for a filled root selector to match, so
    offering one would send the reader after a value that cannot exist.

    ``missing_binding_evidence`` (a resolved root, a populated catalog, and not
    one static edge between them) and ``partial_binding_evidence`` (an agent
    whose ``tools=[...]`` static analysis could only partly read) both want a
    closed-world ``declarations`` row. Everything in it that a human owns stays
    a sentinel: ``complete`` is the closed-world assertion and ``reason`` is how
    they verified it. What is
    pre-filled is only what was *read off the surface*: which agent, which
    catalog tools exist, and which handoffs were observed. That is the
    retyping #361 measured — six tool names already extracted, hand-copied at
    the point the user has the least context — not the judgement.

    The other binding kinds are repaired in source wiring or in an existing
    declaration, so a block aimed at ``agent_bindings.declarations`` would not
    fit the path their action names.
    """

    if issue.kind == "ambiguous_root_agent":
        return deepcopy(AGENT_BINDINGS_ROOT_TEMPLATE) if graph.agents else None
    if issue.kind not in {"missing_binding_evidence", "partial_binding_evidence"}:
        return None
    # Root-scoped only. ``missing_binding_evidence`` is also raised per tool for
    # capabilities bound to an agent the root does not reach
    # (``_unbound_tool_gaps``); those are repaired by wiring the handoff, not by
    # declaring the root's tool set.
    if issue.kind == "missing_binding_evidence" and (
        issue.tool_id is not None or issue.agent_id != graph.root_agent_id
    ):
        return None
    if issue.kind == "partial_binding_evidence" and issue.agent_id != graph.root_agent_id:
        return None
    catalog = {tool.id: tool for tool in tool_catalog}
    # Everything the declaration has to account for. ``unbound`` alone was
    # right only while nothing at all was bound: on a mixed surface — one tool
    # this module defines, one imported — the resolved tool is *already* an
    # edge, and a closed-world row omitting it would assert the agent cannot
    # reach a tool the repository plainly wires to it (PR #401 review).
    candidate_ids = list(
        dict.fromkeys(
            [
                edge.tool_id
                for edge in graph.tool_edges
                if edge.agent_id == issue.agent_id
            ]
            + list(graph.unbound_tool_ids)
        )
    )
    unbound = [catalog[tool_id] for tool_id in candidate_ids if tool_id in catalog]
    if not unbound or len(unbound) > _MAX_SCAFFOLDED_BINDING_TOOLS:
        return None
    names = {agent.agent_id: agent.name for agent in graph.agents}
    root_name = names.get(issue.agent_id or "")
    # ``root`` is the documented alias for the configured root agent, and it is
    # what a duplicated agent name has to fall back to: a declaration naming a
    # name two agents share resolves to neither.
    agent = (
        root_name
        if root_name and sum(1 for value in names.values() if value == root_name) == 1
        else "root"
    )
    handoffs = sorted(
        {
            names.get(edge.target_agent_id, edge.target_agent_id)
            for edge in graph.handoff_edges
            if edge.source_agent_id == issue.agent_id
        }
    )
    # ``handoffs`` is a bare list of names with no source qualifier — the
    # schema has nowhere to put one. A target whose name two agents share
    # therefore resolves to neither, and the block would report an unresolved
    # binding instead of closing the gap it was offered for (PR #401 review).
    # Withhold the whole template rather than the handoff: dropping the target
    # would understate a closed world the reviewer is about to assert, and the
    # real repair for an ambiguous target is the source wiring.
    if any(sum(1 for value in names.values() if value == target) != 1 for target in handoffs):
        return None
    return {
        "agent_bindings": {
            "declarations": [
                {
                    "agent": agent,
                    "complete": REVIEW_REQUIRED_SENTINEL,
                    # The same selector shape the action templates use: both
                    # resolve through ToolSelectorIndex, where ``tool_id`` is
                    # exact and the source qualifier keeps the row readable.
                    "tools": [
                        _action_selector(tool)
                        for tool in sorted(unbound, key=lambda item: (item.name, item.id))
                    ],
                    "handoffs": handoffs,
                    "reason": REVIEW_REQUIRED_SENTINEL,
                }
            ]
        }
    }


_SEMANTIC_RERUN_COMMAND = (
    "agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json"
)
_ACTION_EFFECT_VALUES = [
    "read",
    "write",
    "destructive",
    "external_communication",
    "financial_write",
    "production_operation",
    "privileged_data_access",
    "code_execution",
    "identity_access",
]
_AUTHORITY_MODE_VALUES = ["none", "scoped", "unscoped", "ambient"]
_MANDATORY_CURRENT_CONTROL_CHECKS = frozenset(
    {
        "SHIP-ACTION-WILDCARD-SCOPE",
        "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
        "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
        "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
    }
)


def _binding_coverage(
    report: ReadinessReport,
    tool_catalog: Sequence[Tool] = (),
) -> tuple[BindingCoverageDecision, list[EvidenceGap]]:
    graph = report.binding_surface_facts
    reason_counts: dict[str, int] = {}
    gaps: list[EvidenceGap] = []
    # Every gap that names a catalog tool names it the same way. Two of the
    # emitters below used to spell the subject as the raw canonical id while
    # the others rendered ``name [provider]``, which made the same tool
    # unjoinable with itself: the exclusion ledger looked up one spelling,
    # found the other, and recorded a gated exclusion as ``not_claimed``.
    # ``_gap_subject`` is the single spelling, and
    # ``validate_semantic_consistency`` rejects a raw id reaching this field.
    catalog_rows = {
        str(row["tool_id"]): row for row in report.tool_catalog if row.get("tool_id")
    }

    def _gap_subject(tool_id: str | None, fallback: str) -> str:
        if not tool_id:
            return fallback
        return catalog_subject(catalog_rows.get(tool_id) or {"tool_id": tool_id})
    for issue in graph.issues:
        _increment(reason_counts, issue.kind)
        if issue.kind == "ambiguous_root_agent":
            action_kind = "declare_agent_root"
            path = "shipgate.yaml#agent_bindings.root"
            accepted_values = ["source_id", "object"]
            expects = "Declare the exact root agent object and rerun verification."
        elif issue.kind in {"missing_binding_evidence", "unresolved_bound_tool"}:
            action_kind = "declare_agent_bindings"
            path = "shipgate.yaml#agent_bindings.declarations"
            accepted_values = ["agent", "complete:true", "tools", "handoffs", "reason"]
            expects = "Add a reviewed closed-world binding declaration and rerun verification."
        elif issue.kind in {"partial_binding_evidence", "incomplete_handoff_graph"}:
            action_kind = "provide_complete_binding_graph"
            path = "shipgate.yaml#agent_bindings"
            accepted_values = ["literal_tools", "literal_handoffs", "complete:true"]
            expects = "Provide static wiring or an exact reviewed declaration for every reachable edge."
        elif issue.kind == "conflicting_binding_evidence":
            action_kind = "resolve_binding_conflict"
            path = "shipgate.yaml#agent_bindings.declarations"
            accepted_values = ["match_structural_graph", "correct_source_wiring"]
            expects = "Reconcile the declaration with positive structural evidence and rerun verification."
        elif issue.kind == "unresolved_agent_binding":
            action_kind = "declare_agent_root"
            path = "shipgate.yaml#agent_bindings"
            accepted_values = ["exact_agent_object", "exact_handoff_target"]
            expects = "Correct the agent selector or handoff target and rerun verification."
        else:
            action_kind = "provide_static_binding_source"
            path = "shipgate.yaml#agent_bindings"
            accepted_values = ["literal_binding", "reviewed_declaration"]
            expects = "Correct the binding annotation or provide an exact reviewed declaration."
        template = _binding_declaration_template(graph, issue, tool_catalog)
        gaps.append(
            EvidenceGap(
                kind=issue.kind,
                subject=_gap_subject(
                    issue.tool_id,
                    issue.agent_id or graph.root_agent_id or "agent_binding_graph",
                ),
                subject_id=issue.tool_id,
                source_ref=issue.source_pointer or issue.source,
                why=issue.message,
                next_action=EvidenceGapAction(
                    kind=action_kind,  # type: ignore[arg-type]
                    command=_SEMANTIC_RERUN_COMMAND,
                    path=path,
                    why="A complete root-reachable static binding graph is required for passed.",
                    expects=_with_scaffold_pointer(expects, template),
                    accepted_values=accepted_values,
                    declaration_template=template,
                ),
            )
        )
    covered_possible_ids = {
        issue.tool_id
        for issue in graph.issues
        if issue.kind == "partial_binding_evidence" and issue.tool_id is not None
    }
    for tool_id in sorted(set(graph.possible_tool_ids) - covered_possible_ids):
        _increment(reason_counts, "partial_binding_evidence")
        gaps.append(
            EvidenceGap(
                kind="partial_binding_evidence",
                subject=_gap_subject(tool_id, tool_id),
                subject_id=tool_id,
                why="A possibly reachable tool lacks a complete static binding edge.",
                next_action=EvidenceGapAction(
                    kind="provide_complete_binding_graph",
                    command=_SEMANTIC_RERUN_COMMAND,
                    path="shipgate.yaml#agent_bindings",
                    why="Possibly reachable tools cannot be omitted from the release decision.",
                    expects="Provide static wiring or an exact reviewed declaration.",
                    accepted_values=[
                        "literal_tools",
                        "literal_handoffs",
                        "complete:true",
                    ],
                ),
            )
        )
    for gap in _unreached_tool_gaps(report):
        _increment(reason_counts, gap.kind)
        gaps.append(gap)
    already_named = {gap.subject for gap in gaps}
    for gap in _newly_excluded_tool_gaps(report):
        if gap.subject in already_named:
            continue
        already_named.add(gap.subject)
        _increment(reason_counts, gap.kind)
        gaps.append(gap)
    return (
        BindingCoverageDecision(
            total_catalog_tools=(
                len(graph.reachable_tool_ids)
                + len(graph.possible_tool_ids)
                + len(graph.unbound_tool_ids)
            ),
            reachable_tools=len(graph.reachable_tool_ids),
            possible_tools=len(graph.possible_tool_ids),
            unbound_tools=len(graph.unbound_tool_ids),
            pass_eligible=graph.pass_eligible,
            gap_count=len(gaps),
            reason_counts=dict(sorted(reason_counts.items())),
        ),
        gaps,
    )


def _unreached_tool_gaps(report: ReadinessReport) -> list[EvidenceGap]:
    """One row per tool an *identified* agent owns that the root cannot reach.

    Everything downstream of the binding graph — findings, the action
    surface, semantic coverage, ``tool_inventory`` — is narrowed to the
    root-reachable tools (``cli/scan/tools_agent.py``). A tool that lands in
    ``unbound_tool_ids`` is therefore never judged, and it used to go
    unmentioned too: the entire write surface of a multi-agent app could
    disappear behind the ratio ``6/12 catalog tools reachable`` while all 25
    evidence gaps named only the six tools that were reached (#385). Being
    told the gate did not look is materially different from being told
    nothing.

    The rows stop at tools carrying a structural edge, and that boundary is
    deliberate. Such a tool is a hole in *our* graph: the scan proved some
    agent in the repository owns it and then failed to connect that agent to
    the root, so the evidence exists and is incomplete. A catalog entry with
    no edge at all is the opposite claim — nothing in the repository says any
    agent can call it — and catalog membership is deliberately not evidence
    of capability (see ``samples/large_multi_framework_agent``, where 58 of
    63 declared spec operations are correctly out of scope). Emitting a gap
    for those would make declaring an OpenAPI spec or an MCP server
    self-blocking. They stay honestly accounted for by
    ``binding_coverage.unbound_tools`` and the ``unbound_tool_ids``
    partition.
    """

    graph = report.binding_surface_facts
    if not graph.unbound_tool_ids:
        return []
    agent_names = {agent.agent_id: agent.name for agent in graph.agents}
    holders: dict[str, set[str]] = {}
    for edge in graph.tool_edges:
        holders.setdefault(edge.tool_id, set()).add(
            agent_names.get(edge.agent_id, edge.agent_id)
        )
    catalog = {
        str(row.get("tool_id")): row
        for row in report.tool_catalog
        if row.get("tool_id")
    }
    gaps: list[EvidenceGap] = []
    for tool_id in graph.unbound_tool_ids:
        owners = sorted(holders.get(tool_id, ()))
        if not owners:
            continue
        row = catalog.get(tool_id, {})
        name = str(row.get("name") or tool_id)
        gaps.append(
            EvidenceGap(
                kind="missing_binding_evidence",
                subject=catalog_subject(row or {"tool_id": tool_id}),
                subject_id=tool_id,
                source_type=str(row["source_type"]) if row.get("source_type") else None,
                source_ref=str(row["source_ref"]) if row.get("source_ref") else None,
                why=(
                    f"{name} is bound to {', '.join(owners)}, which the configured "
                    "root agent does not reach; it was excluded from the analyzed "
                    "surface."
                ),
                next_action=EvidenceGapAction(
                    kind="declare_agent_bindings",
                    command=_SEMANTIC_RERUN_COMMAND,
                    path="shipgate.yaml#agent_bindings.declarations",
                    why=(
                        "A tool outside the root-reachable graph is never judged, "
                        "so the verdict must not be silent about it."
                    ),
                    expects=(
                        "Wire the handoff that reaches this agent in source, or "
                        "declare it under the reaching agent's handoffs, then "
                        "rerun verification."
                    ),
                    accepted_values=[
                        "agent",
                        "complete:true",
                        "tools",
                        "handoffs",
                        "reason",
                    ],
                ),
            )
        )
    return gaps


def _newly_excluded_tool_gaps(report: ReadinessReport) -> list[EvidenceGap]:
    """One row per tool *this change* pushed out of the analysed surface.

    ``_unreached_tool_gaps`` stops at tools carrying a structural edge, and
    that boundary is right for the question it answers: a catalog entry with
    no edge is nobody's claim of capability, and gating on those would make
    declaring an OpenAPI spec or an MCP server self-blocking (#385).

    It is the wrong boundary for a *diff*. ``github/github-mcp-server#3076``
    adds ``delete_repository`` — ``destructiveHint: true`` — to a published
    117-tool MCP server whose reviewed declaration still lists 116. The new
    tool carries no edge, so it drew no gap; it left the surface before
    ``SHIP-POLICY-APPROVAL-MISSING`` and
    ``SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING`` could see it, and the run
    reported ``unbound_tools: 1`` beside ``gap_count: 0``. Nothing about the
    58 deliberately-unwired operations in
    ``samples/large_multi_framework_agent`` argues for that outcome: those
    were unbound before the change and are unbound after it. This one arrived
    with the diff.

    So the discriminator is the base comparison, not the source type: a
    subject that was already excluded stays a reviewer-facing fact, and a
    subject this change newly excluded is an evidence gap. Without a base
    report ``binding_surface_diff`` is disabled and nothing here fires, which
    is why a plain ``scan`` is unaffected.
    """

    diff = report.binding_surface_diff
    graph = report.binding_surface_facts
    if not diff.enabled:
        # A comparison was asked for and could not be performed, so "this tool
        # was already excluded before the change" is a claim about evidence
        # nobody produced. One row, not one per tool: the mechanism is single
        # (the base is unusable) and so is the repair (#361), and inventing a
        # per-tool provenance the run cannot support is the fabrication this
        # ledger exists to stop.
        if diff.base_comparison_requested and (
            graph.unbound_tool_ids or graph.possible_tool_ids
        ):
            return [_unavailable_base_gap(report)]
        return []
    if not diff.added_unbound_tool_ids:
        return []
    unbound = set(graph.unbound_tool_ids)
    catalog = {
        str(row.get("tool_id")): row
        for row in report.tool_catalog
        if row.get("tool_id")
    }
    gaps: list[EvidenceGap] = []
    for tool_id in sorted(set(diff.added_unbound_tool_ids) & unbound):
        row = catalog.get(tool_id, {})
        name = str(row.get("name") or tool_id)
        gaps.append(
            EvidenceGap(
                kind="missing_binding_evidence",
                subject=catalog_subject(row or {"tool_id": tool_id}),
                subject_id=tool_id,
                source_type=str(row["source_type"]) if row.get("source_type") else None,
                source_ref=str(row["source_ref"]) if row.get("source_ref") else None,
                why=(
                    f"This change put {name} in the tool catalog and no static "
                    "edge or reviewed declaration binds it to the root agent, "
                    "so it was excluded from the analyzed surface before any "
                    "check could judge it."
                ),
                next_action=EvidenceGapAction(
                    kind="declare_agent_bindings",
                    command=_SEMANTIC_RERUN_COMMAND,
                    path="shipgate.yaml#agent_bindings.declarations",
                    why=(
                        "A capability the diff introduced must be judged or "
                        "explicitly declared out of reach; it cannot be neither."
                    ),
                    expects=(
                        "Wire the tool to the root agent in source, or add it to "
                        "the reviewed closed-world declaration, then rerun "
                        "verification."
                    ),
                    accepted_values=[
                        "agent",
                        "complete:true",
                        "tools",
                        "handoffs",
                        "reason",
                    ],
                ),
            )
        )
    return gaps


def _unavailable_base_gap(report: ReadinessReport) -> EvidenceGap:
    excluded = len(report.binding_surface_facts.unbound_tool_ids) + len(
        report.binding_surface_facts.possible_tool_ids
    )
    noun = "tool" if excluded == 1 else "tools"
    return EvidenceGap(
        kind="missing_binding_evidence",
        subject=unavailable_base_subject(report),
        source_ref="--diff-from",
        why=(
            f"{excluded} catalog {noun} sit outside the analyzed surface and "
            "the base comparison this run requested could not be performed, "
            "so whether this change introduced any of them was never "
            "established."
        ),
        next_action=EvidenceGapAction(
            kind="provide_source",
            # Deliberately no command. The repair runs in the *base* workspace,
            # whose path this scan does not know, and the obvious one-liner —
            # ``scan -c shipgate.yaml --format json`` — is executable right here
            # against the head, where it drops ``--diff-from`` and clears this
            # very gap without repairing anything. A published command reaches
            # ``fix_task.allowed_repairs``, so that is a machine-readable
            # instruction to delete the evidence (PR #404 review 2). The two
            # steps are spelled in ``expects``; ``path`` keeps the row
            # addressable by naming the input at fault.
            path="--diff-from",
            why=(
                "A base scan that did not happen is not evidence that the "
                "excluded tools predate this change."
            ),
            expects=(
                "Two steps, in the base source workspace and then here: "
                "regenerate report.json there with the current engine, then "
                "rerun this scan with --diff-from pointing at that file. "
                "Rerunning without --diff-from clears this row without "
                "answering it."
            ),
        ),
    )


def _semantic_coverage(
    tools: list[Tool],
) -> tuple[SemanticCoverageDecision, list[EvidenceGap]]:
    """Project normalized tool assessments into zero-tolerance gate evidence."""

    gaps: list[EvidenceGap] = []
    reason_counts: dict[str, int] = {}
    acknowledged_overrides: list[AcknowledgedEffectOverride] = []
    pass_eligible_actions = 0
    review_concern_count = 0

    for tool in sorted(tools, key=lambda item: (item.name, item.source_type, item.id)):
        assessment = tool.semantic_assessment
        if assessment is None:
            gap = _semantic_gap(
                tool,
                kind="incomplete_surface",
                why=(
                    "No normalized semantic assessment was produced for this "
                    "tool; effect and authority evidence were not evaluated."
                ),
            )
            gaps.append(gap)
            _increment(reason_counts, gap.kind)
            continue

        issues = sorted(
            [
                *assessment.identity.issues,
                *assessment.binding.issues,
                *assessment.effect.issues,
                *assessment.authority.issues,
            ],
            key=lambda issue: (
                issue.kind,
                issue.dimension,
                issue.source or "",
                issue.source_pointer or "",
                issue.message,
            ),
        )
        seen_issues: set[tuple[str, str, str | None, str | None, str]] = set()
        for issue in issues:
            key = (
                issue.kind,
                issue.dimension,
                issue.source,
                issue.source_pointer,
                issue.message,
            )
            if key in seen_issues:
                continue
            seen_issues.add(key)
            gap = _semantic_gap(
                tool,
                kind=issue.kind,
                why=issue.message,
                source_ref=issue.source_pointer or issue.source,
            )
            gaps.append(gap)
            _increment(reason_counts, gap.kind)

        mode = assessment.authority.mode
        if mode in {"unscoped", "ambient"}:
            review_concern_count += 1
            _increment(reason_counts, f"{mode}_authority")

        # An acknowledged effect override is accepted — the declaration stands
        # and the action stays pass-eligible — but it is never silent (#409).
        # A review concern is exactly that tier: it cannot reach ``passed`` and
        # it cannot block, so the reviewer sees the exception and decides. The
        # count says how many; the row says what a reviewer has to judge.
        for row in _acknowledged_overrides(tool, assessment):
            acknowledged_overrides.append(row)
            review_concern_count += 1
            _increment(reason_counts, "acknowledged_effect_override")

        if (
            assessment.pass_eligible
            and not seen_issues
            and mode
            not in {
                "unscoped",
                "ambient",
            }
        ):
            pass_eligible_actions += 1
        elif not seen_issues and mode not in {"unscoped", "ambient"}:
            # Defensive invariant: a non-pass-eligible assessment must explain
            # itself. If a future resolver violates that contract, fail closed.
            gap = _semantic_gap(
                tool,
                kind="invalid_semantic_annotation",
                why=(
                    "The semantic resolver marked this tool non-pass-eligible "
                    "without an actionable issue."
                ),
            )
            gaps.append(gap)
            _increment(reason_counts, gap.kind)

    return (
        SemanticCoverageDecision(
            total_actions=len(tools),
            pass_eligible_actions=pass_eligible_actions,
            gap_count=len(gaps),
            review_concern_count=review_concern_count,
            reason_counts=dict(sorted(reason_counts.items())),
            acknowledged_overrides=acknowledged_overrides,
        ),
        gaps,
    )


def _acknowledged_overrides(
    tool: Tool,
    assessment: ToolSemanticAssessment,
) -> list[AcknowledgedEffectOverride]:
    """Project each reviewed exception into the row a reviewer has to judge.

    Read off the override claim the resolver authored rather than re-derived,
    so the row and the assessment cannot disagree about what was acknowledged.
    """

    rows: list[AcknowledgedEffectOverride] = []
    for claim in assessment.effect.claims:
        if claim.source != DECLARATION_OVERRIDE_SOURCE:
            continue
        evidence = claim.evidence if isinstance(claim.evidence, dict) else {}
        # One row per suppressed observation. A single row carrying only the
        # strongest reading meant the second observation an override waived
        # vanished from every reviewer surface the moment it was acknowledged —
        # the reviewer is judging exceptions, and one of them was invisible
        # (PR #413 review 2).
        for observation in _overridden_observations(evidence):
            rows.append(
                AcknowledgedEffectOverride(
                    subject=(
                        f"{tool.name} "
                        f"[{tool.provider or tool.source_id or tool.source_type}]"
                    ),
                    subject_id=tool.id or None,
                    declared_effect=str(claim.value),
                    inferred_effect=observation["effect"],
                    inferred_sources=observation["sources"],
                    corroborating_sources=_string_list(
                        evidence.get("corroborating_sources")
                    ),
                    evidence=str(evidence.get("evidence") or ""),
                    reason=str(evidence.get("reason") or ""),
                    manifest_path=(
                        f"shipgate.yaml#action_surface.actions[tool={tool.name!r}].override"
                    ),
                )
            )
    return rows


def _overridden_observations(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """The suppressed observations, falling back to the singular pair.

    ``overridden_observations`` is written by the resolver. The singular
    ``overridden_effect``/``overridden_sources`` pair stays readable so a
    report produced before this change still projects one row rather than none.
    """

    rows = evidence.get("overridden_observations")
    resolved: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            effect = str(row.get("effect") or "")
            if effect:
                resolved.append(
                    {"effect": effect, "sources": _string_list(row.get("sources"))}
                )
    if resolved:
        return resolved
    return [
        {
            "effect": str(evidence.get("overridden_effect") or ""),
            "sources": _string_list(evidence.get("overridden_sources")),
        }
    ]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _identity_coverage(tools: list[Tool]) -> IdentityCoverageDecision:
    names: dict[str, int] = {}
    reasons: dict[str, int] = {}
    total_observations = 0
    bound_tools = 0
    eligible = 0
    gaps = 0
    for tool in tools:
        names[tool.name] = names.get(tool.name, 0) + 1
        identity = tool.identity_assessment
        if identity is None:
            gaps += 1
            _increment(reasons, "incomplete_tool_identity")
            total_observations += 1
            continue
        total_observations += len(identity.observation_ids)
        bound_tools += int(identity.binding_id is not None)
        eligible += int(identity.pass_eligible)
        gaps += len(identity.issues)
        for issue in identity.issues:
            _increment(reasons, issue.kind)
    return IdentityCoverageDecision(
        total_observations=total_observations,
        canonical_tools=len(tools),
        bound_tools=bound_tools,
        pass_eligible_tools=eligible,
        ambiguous_name_count=sum(1 for count in names.values() if count > 1),
        gap_count=gaps,
        reason_counts=dict(sorted(reasons.items())),
    )


def _increment(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def _semantic_gap(
    tool: Tool,
    *,
    kind: SemanticIssueKind,
    why: str,
    source_ref: str | None = None,
) -> EvidenceGap:
    action_kind: str
    accepted_values: list[str]
    declaration_template: dict[str, object] | None = None
    # Whether ``expects`` should also name the on-disk scaffold. Off for the
    # inventory repair: that row's own ``path`` is the skeleton to open and its
    # remediation spells the manifest entry inline, so naming a second file for
    # the same one-line edit splits one instruction in two. The block is still
    # written to the scaffold for a reader who works from there.
    name_scaffold = True
    if kind in {"incomplete_tool_identity"}:
        action_kind = "declare_source_identity"
        accepted_values = ["unique_source_id", "stable_native_locator"]
        action_why = "A stable source-scoped identity is required for every observation."
        expects = "Declare a unique source identity and regenerate the report."
    elif kind in {"unresolved_tool_selector", "ambiguous_tool_selector", "ambiguous_legacy_tool_identity"}:
        action_kind = "qualify_tool_selector"
        accepted_values = ["tool_id", "provider", "source_type", "source_id"]
        action_why = "A one-to-one selector must resolve before policy or evidence can apply."
        expects = "Add tool_id or source/provider qualifiers, then rerun verification."
        # No template: the action points at ``tool_identity``, whose schema
        # accepts only ``bindings`` entries, so the flat {tool, tool_id,
        # provider} shape this used to offer could not be pasted anywhere. A
        # template a reviewer cannot use is worse than none, and inventing a
        # ``bindings`` row here would assert that separate observations are one
        # capability — precisely the reviewed claim this gap is asking for.
    elif kind == "invalid_tool_binding":
        action_kind = "provide_tool_binding"
        accepted_values = ["exact_primary", "exact_members", "unique_binding_id"]
        action_why = "Cross-source equivalence requires an exact reviewed binding."
        expects = "Correct tool_identity.bindings so every member resolves exactly once."
    elif kind == "conflicting_tool_identity":
        action_kind = "resolve_tool_identity_conflict"
        accepted_values = ["split_binding", "align_schema", "align_authority", "align_annotations"]
        action_why = "Conflicting bound observations cannot share one canonical capability."
        expects = "Split the binding or reconcile its structural evidence, then rerun verification."
    elif kind == "invalid_evidence_provenance":
        action_kind = "provide_policy_evidence"
        accepted_values = [
            "reviewed_declaration",
            "protocol_structure",
            "typed_provider_fact",
            "structural_scope",
        ]
        action_why = "Free-form source labels are not evidence of policy eligibility."
        expects = "Use a typed first-party evidence producer and rerun verification."
    elif kind in {
        "missing_binding_evidence",
        "partial_binding_evidence",
        "unresolved_agent_binding",
        "unresolved_bound_tool",
        "incomplete_handoff_graph",
        "ambiguous_root_agent",
    }:
        action_kind = (
            "declare_agent_root"
            if kind in {"ambiguous_root_agent", "unresolved_agent_binding"}
            else "declare_agent_bindings"
        )
        accepted_values = ["root", "complete:true", "tools", "handoffs", "reason"]
        action_why = "An exact root-reachable binding graph is required."
        expects = "Provide static wiring or a reviewed closed-world declaration, then rerun verification."
    elif kind in {"conflicting_binding_evidence", "invalid_binding_annotation"}:
        action_kind = "resolve_binding_conflict"
        accepted_values = ["match_structural_graph", "correct_source_wiring"]
        action_why = "Conflicting binding evidence cannot be auto-resolved."
        expects = "Reconcile positive structural evidence and reviewed declarations, then rerun verification."
    elif kind == "incomplete_surface":
        manifest_key = inventory_manifest_key(tool.source_type)
        if manifest_key is not None:
            action_kind = "declare_tool_inventory"
            accepted_values = ["reviewed_explicit_inventory"]
            action_why = (
                f"{tool.source_type} extraction is static-only; an explicit "
                "local tool inventory bound to this source is the supported "
                "way to make the full surface enumerable."
            )
            expects = _inventory_remediation(
                manifest_key, tool.source_id, rerun="rerun verification."
            )
            declaration_template = _inventory_declaration_template(
                manifest_key, tool.source_id
            )
            name_scaffold = False
        else:
            action_kind = "provide_complete_inventory"
            accepted_values = [
                "complete_mcp_export",
                "openapi_spec",
                "reviewed_explicit_inventory",
            ]
            action_why = "The complete statically-bound tool surface must be enumerable."
            expects = (
                "Provide a complete MCP export, OpenAPI spec, or reviewed explicit "
                "tool inventory, then rerun verification."
            )
    elif kind in {
        "missing_effect_evidence",
        "inferred_effect_only",
    }:
        action_kind = "declare_action_effect"
        accepted_values = list(_ACTION_EFFECT_VALUES)
        action_why = "A reviewed or structural effect is required for an evidence-backed pass."
        expects = (
            "Declare the conservative effect under action_surface.actions in "
            "shipgate.yaml, then rerun verification."
        )
        declaration_template = {
            **_action_selector(tool),
            "effect": "<REVIEW_REQUIRED>",
        }
    elif kind == "declaration_below_inferred_evidence":
        # Two routes close this row, and the reviewer owns the choice: account
        # for every observation, or state on the record that they do not apply
        # here. The first route's shape depends on the row — see
        # ``effect_repair`` — so the template and the accepted values are
        # derived from it rather than fixed, and the override block is always
        # offered as the second.
        action_kind = "resolve_semantic_conflict"
        action_why = (
            "A declaration that does not account for inferred evidence is accepted, "
            "but not silently."
        )
        # The instruction has to name the values. The short headline every
        # human surface renders is the per-kind phrase, not this row's ``why``,
        # so an instruction that said "raise it to the inferred effect this row
        # names" named it nowhere the user could see.
        repair = (
            effect_repair(tool.semantic_assessment.effect)
            if tool.semantic_assessment is not None
            else None
        )
        override_block = {
            "evidence": REVIEW_REQUIRED_SENTINEL,
            "reason": REVIEW_REQUIRED_SENTINEL,
        }
        if repair is not None and repair.kind == "declare_risk_tags":
            accepted_values = list(repair.risk_tags)
            declaration_template = {
                **_action_selector(tool),
                "risk_tags": list(repair.risk_tags),
                "override": override_block,
            }
        else:
            accepted_values = list(_ACTION_EFFECT_VALUES)
            declaration_template = {
                **_action_selector(tool),
                "override": override_block,
            }
        first_route = (
            repair.instruction
            if repair is not None
            else "Raise action_surface.actions[].effect to the inferred effect"
        )
        expects = (
            f"{first_route}, or acknowledge the difference with an override "
            "naming the evidence you checked and why it does not apply, then "
            "rerun verification."
        )
    elif kind in {
        "missing_authority_evidence",
        "partial_authority_evidence",
    }:
        action_kind = "declare_action_authority"
        accepted_values = list(_AUTHORITY_MODE_VALUES)
        action_why = "A complete authority mode and grant are required."
        expects = (
            "Declare reviewed authority under action_surface.actions in "
            "shipgate.yaml, then rerun verification."
        )
        # Every mode except ``none`` also requires ``auth_type``, and
        # ``scoped`` requires non-empty ``scopes`` — a template offering
        # ``mode`` alone is unfillable for the answers people actually give,
        # so it names the co-required fields and lets the reviewer delete what
        # ``mode: none`` does not take.
        # Co-required fields differ per mode: every mode except ``none`` needs
        # ``auth_type``; ``scoped`` needs non-empty ``scopes``; ``unscoped``
        # and ``ambient`` need ``reason`` and empty ``scopes``. Naming all of
        # them keeps the template fillable for every supported answer, and the
        # scaffold tells the reviewer to delete what their mode does not take.
        declaration_template = {
            **_action_selector(tool),
            "scopes": [REVIEW_REQUIRED_SENTINEL],
            "authority": {
                "mode": REVIEW_REQUIRED_SENTINEL,
                "auth_type": REVIEW_REQUIRED_SENTINEL,
                "reason": REVIEW_REQUIRED_SENTINEL,
            },
        }
    else:
        action_kind = "resolve_semantic_conflict"
        if kind == "conflicting_effect_evidence":
            accepted_values = list(_ACTION_EFFECT_VALUES)
        elif kind == "conflicting_authority_evidence":
            accepted_values = list(_AUTHORITY_MODE_VALUES)
        else:
            accepted_values = [
                "exact_boolean:true",
                "exact_boolean:false",
                *_ACTION_EFFECT_VALUES,
                *_AUTHORITY_MODE_VALUES,
            ]
        action_why = "Conflicting or invalid semantic evidence cannot be auto-resolved."
        expects = (
            "Correct the source declaration or add a conservative reviewed action "
            "declaration, then rerun verification."
        )

    return EvidenceGap(
        kind=kind,
        subject=f"{tool.name} [{tool.provider or tool.source_id or tool.source_type}]",
        subject_id=tool.id or None,
        source_type=tool.source_type,
        source_ref=(
            source_ref
            or tool.source_location
            or tool.source_ref
            or tool.source_path
            or tool.source_pointer
        ),
        why=why,
        next_action=EvidenceGapAction(
            kind=action_kind,  # type: ignore[arg-type]
            command=_SEMANTIC_RERUN_COMMAND,
            path=_semantic_gap_path(kind, tool),
            why=action_why,
            expects=_with_scaffold_pointer(
                expects, declaration_template if name_scaffold else None
            ),
            accepted_values=accepted_values,
            declaration_template=declaration_template,
        ),
    )


_TOOL_IDENTITY_KINDS = frozenset(
    {"incomplete_tool_identity", "conflicting_tool_identity", "invalid_tool_binding"}
)
# An ambiguous SELECTOR is qualified on the action row that uses it.
# ``tool_identity`` takes reviewed bindings asserting that separate
# observations are one capability — a different claim, and a different repair.
_SELECTOR_KINDS = frozenset(
    {
        "unresolved_tool_selector",
        "ambiguous_tool_selector",
        "ambiguous_legacy_tool_identity",
    }
)
_AGENT_BINDING_KINDS = frozenset(
    {
        "missing_binding_evidence",
        "partial_binding_evidence",
        "conflicting_binding_evidence",
        "ambiguous_root_agent",
        "unresolved_agent_binding",
        "unresolved_bound_tool",
        "incomplete_handoff_graph",
        "invalid_binding_annotation",
    }
)


def _semantic_gap_path(kind: str, tool: Tool) -> str:
    """The manifest location that repairs this gap kind."""

    action_row = f"shipgate.yaml#action_surface.actions[tool={tool.name!r}]"
    if kind == "incomplete_surface":
        if inventory_manifest_key(tool.source_type) is not None:
            return SUGGESTED_INVENTORY_FILENAME
        return "shipgate.yaml#tool_sources"
    if kind in _SELECTOR_KINDS:
        return action_row
    if kind in _TOOL_IDENTITY_KINDS:
        return "shipgate.yaml#tool_identity"
    if kind in _AGENT_BINDING_KINDS:
        return "shipgate.yaml#agent_bindings"
    return action_row


def _action_selector(tool: Tool) -> dict[str, object]:
    """Selector fields that identify exactly one action row.

    ``tool`` alone is the display name, so two canonical tools sharing a name
    render identical rows: merging both is rejected as duplicate selectors, and
    merging one resolves neither uniquely. ``tool_id`` disambiguates, and the
    source qualifiers keep the row readable about which surface it came from.
    """

    selector: dict[str, object] = {"tool": tool.name}
    if tool.id:
        selector["tool_id"] = tool.id
    if tool.source_id:
        selector["source_id"] = tool.source_id
    elif tool.source_type:
        selector["source_type"] = tool.source_type
    return selector


def _with_scaffold_pointer(
    expects: str,
    declaration_template: dict[str, object] | None,
) -> str:
    """Name the on-disk scaffold whenever one will carry this template.

    The template alone is only reachable by walking report.json; the scaffold
    is the file a human can open and complete, so the instruction should say
    where it is.
    """

    if not declaration_template:
        return expects
    return (
        f"{expects} A ready-to-review block is written to "
        f"{SUGGESTED_DECLARATIONS_FILENAME} next to report.json."
    )


def inventory_manifest_key(source_type: str) -> str | None:
    """The ``<framework>.tool_inventories`` key that completes this source type.

    ``None`` for source types with no such key — nothing may prescribe an
    inventory entry, or the ``source_id`` on one, for those.
    """

    for prefix, manifest_key in _INVENTORY_MANIFEST_KEYS:
        if source_type == prefix or source_type.startswith(f"{prefix}_"):
            return manifest_key
    return None


def _surface_gap_note(tool: Tool) -> str:
    """Name the constructs that held this tool below high confidence (#393).

    "static extraction could not prove the full tool surface" was the same
    sentence on every AST-extracted tool in every repository, so it told a
    reader nothing about *their* code and nothing about what to change. An
    adapter that measures completeness can say which construct is responsible;
    one that does not is unchanged.
    """

    raw_gaps = tool.extraction.get("surface_gaps")
    if not isinstance(raw_gaps, list):
        return ""
    reasons = sorted({value for value in raw_gaps if isinstance(value, str) and value})
    if not reasons:
        return ""
    return f" Unresolved: {', '.join(reasons)}."


def _evidence_gaps(report: ReadinessReport, tools: list[Tool]) -> list[EvidenceGap]:
    """v0.26: one actionable row per measurable evidence gap.

    Deterministic projection of the same inputs the counts use:
    low-confidence tools (sorted by name) first, then unenumerable sources by
    id, then source warnings in report order. Never gates —
    `build_release_decision` keeps deciding on the counts alone.
    """
    gaps: list[EvidenceGap] = []
    low_confidence = sorted(
        (tool for tool in tools if tool.extraction_confidence != "high"),
        key=lambda tool: (tool.name, tool.source_type),
    )
    for tool in low_confidence:
        manifest_key = inventory_manifest_key(tool.source_type)
        if tool.extraction.get("tool_set_proven") is False:
            # An unproven tool *set* is not a missing artifact. Routing it by
            # source type asked for an inventory or spec the repository had
            # already supplied — the OpenAPI file is right there and complete;
            # what is unresolved is the ADK module that decides which tools the
            # agent gets (#400 review). Name that instead.
            action = EvidenceGapAction(
                kind="provide_source",
                why=(
                    "The tool surface this source belongs to could not be "
                    "enumerated, so the set of tools is unknown even where an "
                    "individual tool's schema is not."
                ),
                expects=(
                    "Resolve the construct named in this row's reason in the "
                    "source module — a dynamic tools expression, a toolset "
                    "with no static inventory, an agent built from unpacked "
                    "keyword arguments, or a tool list mutated after "
                    "construction — then rerun the scan. A tool inventory "
                    "cannot close this: it describes tools, not which tools "
                    "an agent has."
                ),
            )
        elif manifest_key is not None:
            inventory_template = _inventory_declaration_template(
                manifest_key, tool.source_id
            )
            action = EvidenceGapAction(
                kind="declare_tool_inventory",
                path=SUGGESTED_INVENTORY_FILENAME,
                why=(
                    f"{tool.source_type} extraction is static-only; an "
                    "explicit local tool inventory bound to this source is "
                    "the supported way to raise this tool to high confidence."
                ),
                expects=_inventory_remediation(
                    manifest_key, tool.source_id, rerun="rerun the scan."
                ),
                # No scaffold pointer: this row's own ``path`` is the skeleton
                # to open, and its remediation already spells the manifest
                # entry inline. Naming a second file for the same one-line edit
                # splits one instruction across two artifacts. The block is
                # still in the scaffold for a reader who works from there.
                declaration_template=inventory_template,
            )
        else:
            action = EvidenceGapAction(
                kind="provide_source",
                why=(f"{tool.source_type} extraction could not fully enumerate this tool surface."),
                expects=(
                    "Provide a complete MCP export, OpenAPI spec, or "
                    "explicit local tool inventory for this source, then "
                    "rerun the scan."
                ),
            )
        gaps.append(
            EvidenceGap(
                kind="low_confidence_tool",
                subject=f"{tool.name} [{tool.provider or tool.source_id or tool.source_type}]",
                source_type=tool.source_type,
                source_ref=tool.source_location or tool.source_ref,
                why=(
                    f"extraction_confidence={tool.extraction_confidence}; "
                    "static extraction could not prove the full tool surface."
                    f"{_surface_gap_note(tool)}"
                ),
                next_action=action,
            )
        )
    covered_sources = {
        tool.source_id for tool in low_confidence if tool.source_id
    }
    for source in unresolved_symbol_sources(report):
        if source.source_id in covered_sources:
            # A low-confidence row for this source already prescribes the same
            # inventory; two rows would be one repair asked for twice.
            continue
        inventory_template = _inventory_declaration_template(
            source.manifest_key, source.source_id
        )
        listed = ", ".join(source.symbols[:_MAX_LISTED_SYMBOLS])
        more = (
            f" (+{len(source.symbols) - _MAX_LISTED_SYMBOLS} more)"
            if len(source.symbols) > _MAX_LISTED_SYMBOLS
            else ""
        )
        agents = ", ".join(source.agents)
        gaps.append(
            EvidenceGap(
                kind="incomplete_surface",
                subject=f"{source.source_id} [{source.manifest_key.split('.')[0]}]",
                source_type=source.manifest_key.split(".")[0],
                source_ref=source.source_ref,
                why=(
                    f"{len(source.symbols)} tool symbol(s) {agents} lists are "
                    f"not defined in this entrypoint, so the source produced no "
                    f"observation for them: {listed}{more}."
                ),
                next_action=EvidenceGapAction(
                    kind="declare_tool_inventory",
                    path=f"shipgate.yaml#{source.manifest_key}",
                    why=(
                        "The complete statically-bound tool surface must be "
                        "enumerable before any tool of it can be judged."
                    ),
                    expects=_with_scaffold_pointer(
                        _inventory_remediation(
                            source.manifest_key,
                            source.source_id,
                            rerun="rerun the scan.",
                        ),
                        inventory_template,
                    ),
                    accepted_values=["reviewed_explicit_inventory"],
                    declaration_template=inventory_template,
                ),
            )
        )
    for warning in report.source_warnings:
        if (
            "predates report schema" in warning
            and "semantic evidence" in warning
            and "not comparable with --diff-from" in warning
        ):
            action = EvidenceGapAction(
                kind="provide_source",
                # No command, for the same reason as the unavailable-base gap
                # above: the regeneration runs in the base workspace, and the
                # command spelled for it is executable against the head, where
                # it silently drops the comparison.
                path="--diff-from",
                why=(
                    "The base report must be regenerated by the current "
                    "semantic engine before any capability delta is computed."
                ),
                expects=(
                    "Two steps: regenerate report.json in the base source "
                    "workspace, then rerun the head scan with --diff-from "
                    "pointing to that report. Rerunning without --diff-from "
                    "clears this row without answering it."
                ),
            )
        else:
            action = EvidenceGapAction(
                kind="review_warning",
                why="The warning text names the degraded source.",
                expects=(
                    "Fix or re-declare the named source so the loader "
                    "stops warning, then rerun the scan."
                ),
            )
        gaps.append(
            EvidenceGap(
                kind="source_warning",
                subject=warning,
                why="A source loader degraded while reading declared inputs.",
                next_action=action,
            )
        )
    return gaps


# Symbols quoted in a gap's reason before it stops being a sentence.
_MAX_LISTED_SYMBOLS = 8


@dataclass(frozen=True)
class UnresolvedSymbolSource:
    """A configured source whose agent names tool symbols it never observed.

    The tool surface of such a source is not enumerable from its entrypoint —
    the symbols are imported, or built at run time — so the repository owes a
    reviewed inventory joined to it. Held per source rather than per symbol:
    one mechanism, one repair, one row (#361).
    """

    source_id: str
    manifest_key: str
    source_ref: str | None
    agents: tuple[str, ...]
    symbols: tuple[str, ...]


def unresolved_symbol_names(report: ReadinessReport) -> list[str]:
    """Tool symbols still standing unanswered on this report's warnings.

    No name matching, deliberately. Subtracting a catalog-wide set of tool
    names was wrong in both directions (PR #401 review): an unrelated source
    exposing a same-named tool read as a repair that had not happened, and an
    inventory that correctly split a toolset symbol into the tools it exposes
    never matched the symbol, so its source was prescribed the same inventory
    forever. The completion relationship is the reviewed
    ``tool_inventories[].source_id``, and it is applied where both halves of it
    are known — ``withdraw_completed_adk_tool_warnings``, during the scan. A
    warning that survived to the report is one nothing has answered.
    """

    return sorted(
        {symbol for _agent, symbol in unresolved_adk_tool_symbols(report.source_warnings)}
    )


def unresolved_symbol_sources(
    report: ReadinessReport,
) -> list[UnresolvedSymbolSource]:
    """One row per source whose agent names tool symbols it never observed.

    A repository where every tool symbol is imported extracts *nothing*, so
    none of the per-tool gap rows exist and the only rows the first scan
    produces are source warnings routed to ``review_warning`` — a dead end with
    no path, no command, and no template (#361). The repair is a reviewed
    inventory joined to the source: exactly what an ``incomplete_surface`` row
    already prescribes, so the first scan raises one instead of leaving the
    reader with prose.

    Per *source*, never per warning. Six unresolved symbols are one fact
    restated six times (see ``core.source_warnings``), and attaching a repair
    to each row would put raw loader prose back in the headline the grouping
    work removed.

    The source id comes from the binding graph's agent nodes, not from the
    warning: the prose names the agent, the graph knows which configured source
    produced it. An agent name two sources both publish is skipped rather than
    guessed — an inventory joined to the wrong source completes nothing (#386).
    """

    symbols = unresolved_adk_tool_symbols(report.source_warnings)
    if not symbols:
        return []
    manifest_key = inventory_manifest_key("google_adk")
    if manifest_key is None:  # pragma: no cover - google_adk is registered
        return []
    nodes: dict[str, list[AgentBindingNode]] = {}
    for agent in report.binding_surface_facts.agents:
        if agent.source_id:
            nodes.setdefault(agent.name, []).append(agent)
    by_source: dict[str, dict[str, Any]] = {}
    for agent_name, symbol in symbols:
        candidates = nodes.get(agent_name, [])
        if len({node.source_id for node in candidates}) != 1:
            continue
        node = candidates[0]
        source_id = node.source_id
        if source_id is None:  # pragma: no cover - filtered when nodes was built
            continue
        entry = by_source.setdefault(
            source_id,
            {"source_ref": node.source_ref, "agents": [], "symbols": set()},
        )
        if agent_name not in entry["agents"]:
            entry["agents"].append(agent_name)
        entry["symbols"].add(symbol)
    return [
        UnresolvedSymbolSource(
            source_id=source_id,
            manifest_key=manifest_key,
            source_ref=entry["source_ref"],
            agents=tuple(entry["agents"]),
            symbols=tuple(sorted(entry["symbols"])),
        )
        for source_id, entry in sorted(by_source.items())
    ]


def _rule(
    finding: Finding,
    *,
    category: str,
    rule: ContributionRuleName,
    rationale: str,
) -> ContributionRule:
    # `Finding.id` and `Finding.fingerprint` are Python-Optional —
    # `assign_finding_ids()` populates them on the normal scan path,
    # but direct/internal callers (tests constructing minimal Findings,
    # `explain-finding` rebuilding from a stripped report, plugin
    # checks that emit Findings before id assignment) may pass
    # findings with both unset. ContributionRule.finding_id is
    # required-as-string on the wire, so fall back through fingerprint
    # to check_id (which is always a non-empty string per the model
    # contract). The audit row stays useful in every case: even
    # without an id, a reviewer can match the row back to the finding
    # via fingerprint or, last resort, the check_id.
    return ContributionRule(
        finding_id=finding.id or finding.fingerprint or finding.check_id,
        fingerprint=finding.fingerprint,
        check_id=finding.check_id,
        category=category,  # type: ignore[arg-type]
        rule=rule,
        rationale=rationale,
    )


def _review_rule_for(finding: Finding, blocker_severities: set[Severity]) -> ContributionRuleName:
    """Disambiguate the rule name when a finding lands in review_items.

    Three cases reach the review-tier branch in build_release_decision:
    - Policy finding (`blocks_release=True`) + baseline_status="matched":
      would have been a `policy_block_new` blocker if not matched →
      `policy_baseline_accepted`.
    - Severity in active blocker tier + baseline_status="matched":
      would have been `severity_block_new` if not matched →
      `severity_baseline_accepted`.
    - Otherwise (severity in {C,H,M} but not in blocker tier, or
      requires_human_review=True): plain `review_required`.
    """
    if finding.blocks_release and finding.baseline_status == "matched":
        return "policy_baseline_accepted"
    if finding.baseline_status == "matched" and finding.severity in blocker_severities:
        return "severity_baseline_accepted"
    return "review_required"


def _review_rationale_for(finding: Finding, blocker_severities: set[Severity]) -> str:
    if finding.blocks_release and finding.baseline_status == "matched":
        return (
            "blocks_release=true and baseline_status=matched; "
            "accepted as policy debt and routed to review_items."
        )
    if finding.baseline_status == "matched" and finding.severity in blocker_severities:
        return (
            f"severity={finding.severity} is in blocker tier "
            f"({sorted(blocker_severities)}) but baseline_status=matched; "
            "accepted as debt."
        )
    if finding.requires_human_review is True:
        return f"requires_human_review=true (severity={finding.severity}); routed to review_items."
    return (
        f"severity={finding.severity}; below active blocker tier "
        f"({sorted(blocker_severities)}) but in review tier "
        "{critical, high, medium}."
    )


def _excluded_rule_for(finding: Finding, blocker_severities: set[Severity]) -> ContributionRuleName:
    """Disambiguate the rule name when a finding falls through to excluded.

    Two reachable cases:
    - blocks_release=True + matched + severity below review tier:
      original code drops silently → `policy_baseline_accepted` (with
      excluded category, since severity didn't reach the review fall-
      through above).
    - severity in blocker tier + matched + severity below review tier:
      same shape → `severity_baseline_accepted`.
    - Otherwise: plain `sub_threshold`.
    """
    if finding.blocks_release and finding.baseline_status == "matched":
        return "policy_baseline_accepted"
    if finding.baseline_status == "matched" and finding.severity in blocker_severities:
        return "severity_baseline_accepted"
    return "sub_threshold"


def _excluded_rationale_for(finding: Finding, blocker_severities: set[Severity]) -> str:
    if finding.blocks_release and finding.baseline_status == "matched":
        return (
            "blocks_release=true and baseline_status=matched, but "
            f"severity={finding.severity} is below review tier; "
            "excluded from blockers and review_items."
        )
    if finding.baseline_status == "matched" and finding.severity in blocker_severities:
        return (
            f"severity={finding.severity} in blocker tier with "
            "baseline_status=matched, but below review tier; excluded."
        )
    return f"severity={finding.severity}; below active blocker tier and below review tier."


def _to_item(finding: Finding) -> ReleaseDecisionItem:
    # v0.19 reviewer-grade provenance: mirror the dual-source pointers
    # so packet §1 and re-renderers (which consume ReleaseDecisionItem,
    # not the full Finding) can cite both the tool location and the
    # manifest evidence pointer without a side lookup.
    return ReleaseDecisionItem(
        id=finding.id,
        fingerprint=finding.fingerprint,
        check_id=finding.check_id,
        severity=finding.severity,
        title=finding.title,
        baseline_status=finding.baseline_status,
        blocks_release=finding.blocks_release,
        source=finding.source,
        policy_evidence_source=finding.policy_evidence_source,
        capability_refs=list(finding.capability_refs),
        capability_trace_refs=list(finding.capability_trace_refs),
        support=finding.support,
    )


def _join_clauses(parts: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c`` — a list that stays a sentence.

    Four evidence dimensions can now appear at once, and ``" and ".join`` on
    that many reads as one run-on clause with no separators a reader can scan.
    """

    if len(parts) <= 2:
        return " and ".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _decision_reason(
    decision: ReleaseDecisionStatus,
    blockers: list[ReleaseDecisionItem],
    review_items: list[ReleaseDecisionItem],
    evidence: EvidenceCoverageDecision,
) -> str:
    if decision == "blocked":
        n = len(blockers)
        noun = "finding" if n == 1 else "findings"
        verb = "blocks" if n == 1 else "block"
        return f"{n} active {noun} {verb} release."
    if decision == "insufficient_evidence":
        parts: list[str] = []
        if evidence.binding_coverage.gap_count > 0:
            # First, because it is the dimension that answers "was anything
            # left out of the analysis?" — and a run whose only gaps were
            # binding gaps used to describe itself as "degraded evidence",
            # which names no dimension at all (#403).
            parts.append(f"{evidence.binding_coverage.gap_count} binding evidence gap(s)")
        if evidence.semantic_coverage.gap_count > 0:
            parts.append(f"{evidence.semantic_coverage.gap_count} semantic evidence gap(s)")
        if evidence.low_confidence_tool_count > 0:
            parts.append(f"{evidence.low_confidence_tool_count} low-confidence tool(s)")
        if evidence.source_warning_count > 0:
            parts.append(f"{evidence.source_warning_count} source warning(s)")
        detail = _join_clauses(parts) or "degraded evidence"
        # Counts are the symptom; an addressable gap — one naming a target or
        # carrying a runnable command — is the work. When
        # one exists, lead with it and demote the counts to context — the old
        # wording put the tally first and contradicted the `Improve evidence:`
        # line printed directly beneath it (#362). The gap chosen here is the
        # same one every other surface projects, so the three lines agree.
        gap = primary_evidence_gap(evidence)
        target = evidence_gap_target(gap) if gap is not None else ""
        command = evidence_gap_command(gap) if gap is not None else ""
        # A command-only row locates its work with the command; saying only
        # "Insufficient evidence: <headline>." would drop the one affordance
        # the row carries (#362 review 4).
        locator = f"Fix at {target}." if target else (f"Run: {command}." if command else "")
        if locator:
            # Both interpolated values are repository-derived (a gap subject is
            # a tool name or an agent id; a semantic gap's path embeds the tool
            # name), and this string is printed as one line by the CLI and the
            # GitHub step summary. `evidence_gap_target` one-lines the path and
            # is the same predicate ranking used to pick this gap.
            return (
                f"Insufficient evidence: {evidence_gap_headline(gap)}. "
                f"{locator} Context: {detail}; "
                "scan results are not trustworthy enough to gate release."
            )
        return (
            f"Evidence coverage below threshold ({detail}); "
            "scan results are not trustworthy enough to gate release."
        )
    if decision == "review_required":
        matched_criticals = sum(
            1
            for item in review_items
            if item.severity == "critical" and item.baseline_status == "matched"
        )
        n_reviews = len(review_items)
        # Gate "evidence coverage is incomplete" wording on actual
        # measurable gaps. summary.human_review_recommended is also True
        # for any critical/high finding (see findings.summarize_findings),
        # so using it here would falsely claim evidence gaps for clean
        # static scans that simply have high-severity findings.
        # The shared predicate, not a narrower copy of it: omitting binding,
        # policy, and typed-gap inputs here dropped the evidence clause from a
        # mixed review whose selected action names a binding declaration
        # (#362 review 4).
        has_evidence_gaps = has_measurable_evidence_gaps(evidence)
        if review_items and matched_criticals == n_reviews and matched_criticals > 0:
            return (
                "All critical findings are baseline-matched; review accepted debt before shipping."
            )
        if review_items and has_evidence_gaps:
            noun = "finding" if n_reviews == 1 else "findings"
            verb = "needs" if n_reviews == 1 else "need"
            return (
                f"{n_reviews} {noun} {verb} review and evidence coverage is incomplete."
            )
        if review_items:
            noun = "finding" if n_reviews == 1 else "findings"
            verb = "requires" if n_reviews == 1 else "require"
            return f"{n_reviews} {noun} {verb} human review before shipping."
        if evidence.semantic_coverage.review_concern_count > 0:
            # Two different concerns share this tier, and naming the wrong one
            # is worse than naming neither: an acknowledged effect override has
            # nothing to do with authority mode.
            counts = evidence.semantic_coverage.reason_counts
            overrides = counts.get("acknowledged_effect_override", 0)
            authority = (
                counts.get("unscoped_authority", 0) + counts.get("ambient_authority", 0)
            )
            phrases: list[str] = []
            if authority:
                noun, verb = ("action", "uses") if authority == 1 else ("actions", "use")
                phrases.append(
                    f"{authority} {noun} {verb} known unscoped or ambient authority"
                )
            if overrides:
                noun = "declaration" if overrides == 1 else "declarations"
                verb = "sits" if overrides == 1 else "sit"
                phrases.append(
                    f"{overrides} acknowledged {noun} {verb} below inferred effect evidence"
                )
            # Anything this function does not have a phrase for still has to
            # appear: the sentence claims to explain why review is required,
            # so a concern it cannot name must still be counted.
            remainder = (
                evidence.semantic_coverage.review_concern_count - authority - overrides
            )
            if remainder > 0:
                noun = "concern" if remainder == 1 else "concerns"
                phrases.append(
                    f"{remainder} other semantic review {noun}"
                    if phrases
                    else f"{remainder} semantic review {noun}"
                )
            return f"{'; '.join(phrases)}; human review is required before shipping."
        if evidence.low_confidence_tool_count > 0:
            return "Static-only scan with low-confidence evidence; human review recommended."
        if evidence.source_warning_count > 0:
            # Reachable when no review_items and human_review_recommended
            # is False but source warnings tipped us into review_required
            # via the explicit source-warning branch in
            # build_release_decision. Checked after the low-confidence
            # branch so a scan with both gaps surfaces the more
            # actionable wording first.
            n = evidence.source_warning_count
            noun = "warning" if n == 1 else "warnings"
            return f"{n} source-loader {noun}; review evidence before shipping."
        # Defensive: review_required with no review_items and no
        # measurable evidence gaps. summarize_findings doesn't produce
        # this combination today, but cover the case explicitly.
        return "Human review recommended."
    return (
        "All in-scope actions have complete, conflict-free explicit or "
        "structural static effect and authority evidence; no active blockers. "
        "Runtime behavior was not verified."
    )
