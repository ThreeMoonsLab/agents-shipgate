from __future__ import annotations

import math

from agents_shipgate.ci.exit_policy import (
    effective_fail_on,
    exit_code_for_report,
)
from agents_shipgate.core.domain import SemanticIssueKind, Tool
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.report import (
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
        or any(
            gap.kind == "source_warning"
            and gap.next_action.path == "--diff-from"
            and gap.next_action.command == "agents-shipgate scan -c shipgate.yaml --format json"
            for gap in evidence.evidence_gaps
        )
        or evidence.low_confidence_tool_count >= _low_confidence_tool_threshold(tool_count)
        or evidence.source_warning_count > _MAX_TOLERATED_SOURCE_WARNINGS
    )


def build_release_decision(
    *,
    report: ReadinessReport,
    tools: list[Tool],
    ci_mode: str,
    fail_on: list[Severity] | None,
    new_findings_only: bool,
) -> ReleaseDecision:
    fail_on_resolved = effective_fail_on(ci_mode, fail_on)

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
    binding_coverage, binding_gaps = _binding_coverage(report)
    evidence = EvidenceCoverageDecision(
        level=report.summary.evidence_coverage,
        human_review_recommended=report.summary.human_review_recommended,
        source_warning_count=len(report.source_warnings),
        low_confidence_tool_count=low_confidence_tool_count,
        # Semantic gaps lead because they are zero-tolerance gate inputs;
        # extraction/source gaps retain their existing deterministic order.
        evidence_gaps=[*binding_gaps, *semantic_gaps, *_evidence_gaps(report, tools)],
        semantic_coverage=semantic_coverage,
        identity_coverage=identity_coverage,
        binding_coverage=binding_coverage,
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
        has_semantic_gaps=has_semantic_gaps or has_binding_gaps,
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
) -> tuple[BindingCoverageDecision, list[EvidenceGap]]:
    graph = report.binding_surface_facts
    reason_counts: dict[str, int] = {}
    gaps: list[EvidenceGap] = []
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
        gaps.append(
            EvidenceGap(
                kind=issue.kind,
                subject=issue.tool_id or issue.agent_id or graph.root_agent_id or "agent_binding_graph",
                source_ref=issue.source_pointer or issue.source,
                why=issue.message,
                next_action=EvidenceGapAction(
                    kind=action_kind,  # type: ignore[arg-type]
                    command=_SEMANTIC_RERUN_COMMAND,
                    path=path,
                    why="A complete root-reachable static binding graph is required for passed.",
                    expects=expects,
                    accepted_values=accepted_values,
                    declaration_template={
                        "agent_bindings": {
                            "root": {"source_id": "<REVIEW_REQUIRED>", "object": "<REVIEW_REQUIRED>"},
                            "declarations": [
                                {
                                    "agent": "root",
                                    "complete": True,
                                    "tools": [],
                                    "handoffs": [],
                                    "reason": "<REVIEW_REQUIRED>",
                                }
                            ],
                        }
                    },
                ),
            )
        )
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


def _semantic_coverage(
    tools: list[Tool],
) -> tuple[SemanticCoverageDecision, list[EvidenceGap]]:
    """Project normalized tool assessments into zero-tolerance gate evidence."""

    gaps: list[EvidenceGap] = []
    reason_counts: dict[str, int] = {}
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
        ),
        gaps,
    )


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
        declaration_template = {
            "tool": tool.name,
            "tool_id": tool.id,
            "provider": tool.provider,
        }
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
            "tool": tool.name,
            "effect": "<REVIEW_REQUIRED>",
        }
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
        declaration_template = {
            "tool": tool.name,
            "authority": {"mode": "<REVIEW_REQUIRED>"},
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
            path=(
                "shipgate.yaml#tool_sources"
                if kind == "incomplete_surface"
                else (
                    "shipgate.yaml#tool_identity"
                    if kind in {
                        "incomplete_tool_identity",
                        "conflicting_tool_identity",
                        "unresolved_tool_selector",
                        "ambiguous_tool_selector",
                        "ambiguous_legacy_tool_identity",
                        "invalid_tool_binding",
                    }
                    else (
                        "shipgate.yaml#agent_bindings"
                        if kind in {
                            "missing_binding_evidence",
                            "partial_binding_evidence",
                            "conflicting_binding_evidence",
                            "ambiguous_root_agent",
                            "unresolved_agent_binding",
                            "unresolved_bound_tool",
                            "incomplete_handoff_graph",
                            "invalid_binding_annotation",
                        }
                        else f"shipgate.yaml#action_surface.actions[tool={tool.name!r}]"
                    )
                )
            ),
            why=action_why,
            expects=expects,
            accepted_values=accepted_values,
            declaration_template=declaration_template,
        ),
    )


def _inventory_manifest_key(source_type: str) -> str | None:
    for prefix, manifest_key in _INVENTORY_MANIFEST_KEYS:
        if source_type == prefix or source_type.startswith(f"{prefix}_"):
            return manifest_key
    return None


def _evidence_gaps(report: ReadinessReport, tools: list[Tool]) -> list[EvidenceGap]:
    """v0.26: one actionable row per measurable evidence gap.

    Deterministic projection of the same inputs the counts use:
    low-confidence tools (sorted by name) first, then source warnings in
    report order. Never gates — `build_release_decision` keeps deciding
    on the counts alone.
    """
    gaps: list[EvidenceGap] = []
    low_confidence = sorted(
        (tool for tool in tools if tool.extraction_confidence != "high"),
        key=lambda tool: (tool.name, tool.source_type),
    )
    for tool in low_confidence:
        manifest_key = _inventory_manifest_key(tool.source_type)
        if manifest_key is not None:
            action = EvidenceGapAction(
                kind="declare_tool_inventory",
                path=SUGGESTED_INVENTORY_FILENAME,
                why=(
                    f"{tool.source_type} extraction is static-only; an "
                    "explicit local tool inventory is the supported way to "
                    "raise this tool to high confidence."
                ),
                expects=(
                    f"Review the skeleton written next to report.json, save "
                    f"it in your repo, reference it from `{manifest_key}` in "
                    "shipgate.yaml, then rerun the scan."
                ),
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
                ),
                next_action=action,
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
                command="agents-shipgate scan -c shipgate.yaml --format json",
                path="--diff-from",
                why=(
                    "The base report must be regenerated by the current "
                    "semantic engine before any capability delta is computed."
                ),
                expects=(
                    "Regenerate report.json in the base source workspace, then "
                    "rerun the head scan with --diff-from pointing to that report."
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
    )


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
        if evidence.semantic_coverage.gap_count > 0:
            parts.append(f"{evidence.semantic_coverage.gap_count} semantic evidence gap(s)")
        if evidence.low_confidence_tool_count > 0:
            parts.append(f"{evidence.low_confidence_tool_count} low-confidence tool(s)")
        if evidence.source_warning_count > 0:
            parts.append(f"{evidence.source_warning_count} source warning(s)")
        detail = " and ".join(parts) if parts else "degraded evidence"
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
        has_evidence_gaps = (
            evidence.semantic_coverage.gap_count > 0
            or evidence.low_confidence_tool_count > 0
            or evidence.source_warning_count > 0
        )
        if review_items and matched_criticals == n_reviews and matched_criticals > 0:
            return (
                "All critical findings are baseline-matched; review accepted debt before shipping."
            )
        if review_items and has_evidence_gaps:
            noun = "finding" if n_reviews == 1 else "findings"
            return f"{n_reviews} {noun} need review and evidence coverage is incomplete."
        if review_items:
            noun = "finding" if n_reviews == 1 else "findings"
            verb = "requires" if n_reviews == 1 else "require"
            return f"{n_reviews} {noun} {verb} human review before shipping."
        if evidence.semantic_coverage.review_concern_count > 0:
            n = evidence.semantic_coverage.review_concern_count
            noun = "action" if n == 1 else "actions"
            return (
                f"{n} {noun} use known unscoped or ambient authority; "
                "human review is required before shipping."
            )
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
