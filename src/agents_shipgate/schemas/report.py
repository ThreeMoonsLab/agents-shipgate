from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.bindings import (
    AgentBindingGraphAssessment,
    BindingSurfaceDiff,
)
from agents_shipgate.schemas.capability_change import (
    CapabilityChangeBlock,
    EffectivePolicy,
    HumanAck,
    ProtectedSurfaceChange,
    VerifierSummary,
)
from agents_shipgate.schemas.codex_plugin import CodexPluginSurface
from agents_shipgate.schemas.common import (
    AgentAction,
    BaselineStatus,
    Confidence,
    ProvenanceKind,
    ReleaseDecisionStatus,
    Severity,
    SourceReference,
)
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.exclusions import SurfaceExclusionLedger
from agents_shipgate.schemas.patches import Patch
from agents_shipgate.schemas.semantic import ToolSemanticEvidence
from agents_shipgate.schemas.surfaces import (
    ActionEffect,
    ActionSurfaceDiff,
    ActionSurfaceFacts,
    ToolSurfaceDiff,
    ToolSurfaceFacts,
)
from agents_shipgate.schemas.verification_identity import CONTENT_ID_PATTERN


class CapabilityPolicyEvidence(BaseModel):
    """Capability-level audit evidence for a policy match.

    This is explanatory metadata only. It lets reviewers see which durable
    capability fact matched a policy rule without folding that metadata into
    the legacy ``Finding.evidence`` fingerprint input.
    """

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    identity: dict[str, Any]
    effect: dict[str, Any]
    authority: dict[str, Any]
    controls: dict[str, Any]
    hashes: dict[str, str]
    matched_predicates: dict[str, Any] = Field(default_factory=dict)
    source: SourceReference | None = None


PolicyMatchStatus = Literal["matched", "not_matched", "indeterminate", "conflicting"]
EvidenceBasis = Literal[
    "reviewed_declaration",
    "protocol_structure",
    "typed_provider_fact",
    "structural_scope",
    "inferred_keyword",
    "inferred_regex",
    "protocol_default",
    "unknown",
]


class PolicyPredicateEvidence(BaseModel):
    """One tri-state policy predicate and the evidence that supports it."""

    model_config = ConfigDict(extra="forbid")

    predicate: str
    status: PolicyMatchStatus
    expected: Any = None
    observed: Any = None
    confidence: Confidence = "low"
    claim_ids: list[str] = Field(default_factory=list)
    evidence_bases: list[EvidenceBasis] = Field(default_factory=list)
    policy_eligible: bool = False
    why: str | None = None


class FindingSupport(BaseModel):
    """Authoritative support for finding confidence and release contribution.

    Rule metadata may request a severity or block, but it cannot upgrade the
    underlying evidence. ``support_hash`` binds baselines and audit surfaces
    to the predicate evidence that actually made the finding eligible.
    """

    model_config = ConfigDict(extra="forbid")

    status: PolicyMatchStatus = "matched"
    confidence: Confidence = "low"
    policy_eligible: bool = False
    blocking_eligible: bool = False
    claim_ids: list[str] = Field(default_factory=list)
    evidence_bases: list[EvidenceBasis] = Field(default_factory=list)
    predicates: list[PolicyPredicateEvidence] = Field(default_factory=list)
    support_hash: str


CapabilityTraceMatchReason = Literal[
    "capability_id",
    "tool_name",
    "unknown_tool",
    "ambiguous_tool",
    "invalid_capability_id",
    "missing_tool_name",
]


class CapabilityTraceEvidenceSummary(BaseModel):
    """Deterministic counts for opt-in local runtime trace evidence."""

    model_config = ConfigDict(extra="forbid")

    source_count: int = 0
    trace_count: int = 0
    matched_trace_count: int = 0
    unmatched_trace_count: int = 0
    approval_trace_count: int = 0
    agent_trace_count: int = 0
    api_trace_count: int = 0
    warning_count: int = 0


class CapabilityTraceEvidenceV1(BaseModel):
    """Allowlisted local trace event linked to a durable capability fact.

    Raw prompts, messages, tool arguments, outputs, and arbitrary payloads
    are intentionally absent. ``observed`` contains only normalized scalar
    evidence from the allowlist enforced by ``inputs.traces``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source_type: str
    source: SourceReference | None = None
    tool_name: str | None = None
    provider: str | None = None
    operation: str | None = None
    capability_id: str | None = None
    matched_capability_id: str | None = None
    matched: bool = False
    match_reason: CapabilityTraceMatchReason
    observed: dict[str, Any] = Field(default_factory=dict)
    event_hash: str
    source_hash: str


class CapabilityRuntimeEvidence(BaseModel):
    """Top-level audit block for declared local runtime trace artifacts."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    summary: CapabilityTraceEvidenceSummary = Field(default_factory=CapabilityTraceEvidenceSummary)
    matched: list[CapabilityTraceEvidenceV1] = Field(default_factory=list)
    unmatched: list[CapabilityTraceEvidenceV1] = Field(default_factory=list)
    source_provenance: list[SourceReference] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PolicyApprovalRouting(BaseModel):
    """Non-enforcing approval-routing metadata from a policy-pack rule."""

    model_config = ConfigDict(extra="forbid")

    required: bool = False
    teams: list[str] = Field(default_factory=list)
    min_approvals: int | None = None
    enforced: Literal[False] = False


class PolicyRoutingMetadata(BaseModel):
    """Reviewer routing metadata carried outside finding evidence."""

    model_config = ConfigDict(extra="forbid")

    owner: str | None = None
    reviewers: list[str] = Field(default_factory=list)
    approval: PolicyApprovalRouting = Field(default_factory=PolicyApprovalRouting)


class Finding(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    fingerprint: str | None = None
    check_id: str
    title: str
    severity: Severity
    category: str
    tool_id: str | None = None
    tool_name: str | None = None
    agent_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence = "medium"
    # v0.15: records *what kind of rule fired* — declared metadata vs.
    # parsed AST artifact vs. keyword/regex match vs. external policy
    # pack. Independent of `confidence` (sureness) and `source` (which
    # file/pointer the underlying Tool was sourced from).
    #
    # Python-Optional with None default so v0.12-v0.14 reports loaded
    # by explain-finding don't get a misleading "static_declaration"
    # synthesized for every finding. Emitted reports always carry a
    # real value: `tool_finding` and `agent_finding` in checks/base.py
    # require the kwarg, and direct constructors in n8n/policy_packs
    # pass it explicitly. Third-party plugin checks that construct
    # `Finding(...)` directly without setting this field are coerced
    # to `"static_declaration"` by `annotate_remediation` in
    # core/findings/remediation.py so the wire schema's required + non-nullable
    # enum stays satisfied — plugins that want a more specific label
    # should set the field themselves. Required + non-nullable on the
    # wire via scripts/generate_schemas.py.
    provenance_kind: ProvenanceKind | None = None
    source: SourceReference | None = None
    # v0.19 reviewer-grade provenance: dual-source citation for findings
    # whose triggering evidence lives in TWO places — the tool itself
    # (carried by ``source``) and a missing/declared mitigation in the
    # manifest. Approval/confirmation/idempotency/scope/HITL checks
    # populate this field with the manifest pointer (e.g.
    # ``/policies/require_approval_for_tools``) plus the YAML line
    # number resolved through the manifest ``PositionIndex``. Optional
    # because most findings only have one provenance source.
    policy_evidence_source: SourceReference | None = None
    # v0.24: capability-native policy/evidence integration. Policy and
    # policy-pack checks can now cite the durable capability facts that
    # caused the rule to match. Kept outside ``evidence`` so existing
    # finding fingerprints do not churn.
    capability_refs: list[str] = Field(default_factory=list)
    capability_policy_evidence: CapabilityPolicyEvidence | None = None
    # v0.28: non-enforcing policy-pack routing metadata. Kept outside
    # ``evidence`` so owner/reviewer/approval routing changes do not
    # affect fingerprints, suppressions, baselines, or release gating.
    policy_routing: PolicyRoutingMetadata | None = None
    # v0.33: authoritative predicate support. Kept outside legacy evidence so
    # fingerprints stay stable; baselines use support_hash separately.
    support: FindingSupport | None = None
    # v0.25: opt-in local runtime trace/provenance evidence linked to
    # capability facts. Kept outside ``evidence`` so fingerprints,
    # baselines, run IDs, and de-dupe identity do not churn.
    capability_trace_refs: list[str] = Field(default_factory=list)
    recommendation: str
    # v0.16: explicit release-blocking signal for Action Surface Diff
    # policy findings. This is orthogonal to severity: advisory CI can
    # still exit 0 while the release decision names a policy blocker.
    blocks_release: bool = False
    suppressed: bool = False
    suppression_reason: str | None = None
    baseline_status: BaselineStatus | None = None
    # v0.6: populated only when scan ran with --suggest-patches. None
    # default + dict post-processing in write_json_report keeps the JSON
    # contract additive — non-opting callers see no `patches` key at all
    # (per C4).
    patches: list[Patch] | None = None
    # v0.7 remediation enrichment. Populated by `annotate_remediation`
    # in core/findings/remediation.py during build_report (regardless of
    # --suggest-patches), so any consumer reading `report.json` gets
    # remediation policy without opting into patches.
    #
    # Three derivation states:
    # - `patches is None` (scan without --suggest-patches): fields come
    #   from the matching CheckMetadata entry; safe-closed fallback for
    #   unknown check IDs (policy packs, third-party plugins).
    # - `patches == []` (scan WITH --suggest-patches but generator
    #   emitted nothing): safe-closed shape with
    #   `suggested_patch_kind="none"`. Does NOT fall back to catalog —
    #   the report carries no patches, so reporting a catalog-level
    #   kind would be misleading.
    # - `patches` non-empty: derived from the actual emitted patches:
    #   * autofix_safe = True iff EVERY patch is non-manual AND has
    #     confidence == "high". Mixed-state (one safe + one manual, or
    #     one high + one medium) → autofix_safe = False.
    #   * requires_human_review = True iff autofix_safe is False.
    #   * suggested_patch_kind = kind of the first non-manual patch
    #     (even when ManualPatches are also present), or "manual" if
    #     all patches are ManualPatch.
    #
    # `docs_url` always sourced from CheckMetadata.docs_url; patches
    # don't carry per-instance documentation URLs.
    autofix_safe: bool | None = None
    requires_human_review: bool | None = None
    suggested_patch_kind: str | None = None
    docs_url: str | None = None
    # v0.12: deterministic agent_action projection. Set by
    # `annotate_remediation` after the v0.7 fields above are populated.
    # Stays None on synthetic findings constructed for tests that don't
    # exercise the remediation path (kept Python-optional for back-compat;
    # the JSON Schema requires the field on emitted reports).
    agent_action: AgentAction | None = None


class ReportSummary(BaseModel):
    status: str
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    suppressed_count: int = 0
    human_review_recommended: bool = False
    evidence_coverage: str = "static"


class ToolSurfaceSummary(BaseModel):
    total_tools: int
    high_risk_tools: int
    sources: dict[str, int] = Field(default_factory=dict)
    wildcard_tools: int = 0
    missing_descriptions: int = 0


class BaselineSummary(BaseModel):
    path: str
    matched_count: int = 0
    new_count: int = 0
    resolved_count: int = 0


# v0.8: release_decision block — see docs/STABILITY.md for the
# divergence contract with summary.status (which stays baseline-blind for
# backwards compatibility). The ``ReleaseDecisionStatus`` verdict vocabulary
# is now defined once in schemas/common.py and imported above, so the report
# summaries, ReleaseConsequence, and the verifier projection all share the
# exact same enum (one decision engine — no re-spelling, no drift).


class ReleaseDecisionItem(BaseModel):
    id: str | None = None
    fingerprint: str | None = None
    check_id: str
    severity: Severity
    title: str
    baseline_status: BaselineStatus | None = None
    blocks_release: bool = False
    # v0.19 reviewer-grade provenance: mirror the dual-source pointers
    # from the originating ``Finding`` so packet §1 blocker / review
    # item rendering and re-rendering from ``packet.json`` can cite the
    # tool location AND the manifest evidence pointer without a side
    # lookup. Both default to None for backwards compatibility — old
    # consumers ignore the fields.
    source: SourceReference | None = None
    policy_evidence_source: SourceReference | None = None
    # v0.24: mirror Finding.capability_refs so release-decision consumers
    # can audit policy blockers without joining against findings[].
    capability_refs: list[str] = Field(default_factory=list)
    # v0.25: mirror Finding.capability_trace_refs for blocker/review rows.
    capability_trace_refs: list[str] = Field(default_factory=list)
    support: FindingSupport | None = None


class EvidenceGapAction(BaseModel):
    """One concrete, mechanically-executable step that closes a gap.

    Mirrors the agent-mode ``next_actions[]`` error shape
    (``kind``/``command``/``path``/``why``/``expects``) so agents reuse
    one routing vocabulary across error recovery and evidence repair.
    """

    kind: Literal[
        "declare_tool_inventory",
        "provide_source",
        "review_warning",
        "declare_action_effect",
        "declare_action_authority",
        "provide_complete_inventory",
        "resolve_semantic_conflict",
        "declare_source_identity",
        "qualify_tool_selector",
        "provide_tool_binding",
        "resolve_tool_identity_conflict",
        "regenerate_identity_artifact",
        "declare_agent_root",
        "declare_agent_bindings",
        "provide_static_binding_source",
        "provide_complete_binding_graph",
        "resolve_binding_conflict",
        "regenerate_binding_artifact",
        "provide_policy_evidence",
        "review_policy_evidence",
        "resolve_policy_evidence_conflict",
    ]
    command: str | None = None
    path: str | None = None
    why: str
    expects: str
    # v0.29: semantic-evidence repairs are human assertions, never
    # auto-applied patches. These fields make the required declaration
    # mechanically discoverable without asking an agent to infer authority.
    accepted_values: list[str] = Field(default_factory=list)
    # Human-reviewed declaration skeleton only. It is deliberately not a
    # machine-applicable Patch object and is never consumed by apply-patches.
    # The explicit kind keeps agent consumers from mistaking structured YAML
    # guidance for authorization to write it.
    suggested_patch_kind: Literal["manual"] = "manual"
    declaration_template: dict[str, Any] | None = None
    auto_apply: Literal[False] = False
    requires_human_review: Literal[True] = True


class EvidenceGap(BaseModel):
    """v0.26: one structured row per measurable evidence gap.

    ``insufficient_evidence`` previously diagnosed without prescribing;
    each gap names the degraded subject and the specific next action
    that raises extraction confidence. Purely explanatory — gating
    still uses only the counts (the gap list is a projection of them).
    """

    kind: Literal[
        "low_confidence_tool",
        "source_warning",
        "incomplete_surface",
        "missing_effect_evidence",
        "inferred_effect_only",
        "conflicting_effect_evidence",
        "missing_authority_evidence",
        "partial_authority_evidence",
        "conflicting_authority_evidence",
        "invalid_semantic_annotation",
        "incomplete_tool_identity",
        "conflicting_tool_identity",
        "unresolved_tool_selector",
        "ambiguous_tool_selector",
        "ambiguous_legacy_tool_identity",
        "invalid_tool_binding",
        "missing_binding_evidence",
        "partial_binding_evidence",
        "conflicting_binding_evidence",
        "ambiguous_root_agent",
        "unresolved_agent_binding",
        "unresolved_bound_tool",
        "incomplete_handoff_graph",
        "invalid_binding_annotation",
        "invalid_evidence_provenance",
        "inferred_policy_applicability",
        "mixed_policy_evidence",
        "unknown_policy_evidence",
        "conflicting_policy_evidence",
    ]
    subject: str
    source_type: str | None = None
    source_ref: str | None = None
    why: str
    next_action: EvidenceGapAction


class SemanticCoverageDecision(BaseModel):
    """v0.29 pass eligibility across the normalized action surface.

    Unlike extraction-confidence thresholds, semantic gaps are
    zero-tolerance: any non-pass-eligible unknown/partial/conflicting
    dimension prevents ``passed``. Known authority review concerns (for
    example ambient or unscoped credentials) are counted separately so
    they deterministically route to ``review_required`` rather than
    ``insufficient_evidence``.
    """

    total_actions: int = 0
    pass_eligible_actions: int = 0
    gap_count: int = 0
    review_concern_count: int = 0
    reason_counts: dict[str, int] = Field(default_factory=dict)


class IdentityCoverageDecision(BaseModel):
    total_observations: int = 0
    canonical_tools: int = 0
    bound_tools: int = 0
    pass_eligible_tools: int = 0
    ambiguous_name_count: int = 0
    gap_count: int = 0
    reason_counts: dict[str, int] = Field(default_factory=dict)


class BindingCoverageDecision(BaseModel):
    total_catalog_tools: int = 0
    reachable_tools: int = 0
    possible_tools: int = 0
    unbound_tools: int = 0
    pass_eligible: bool = False
    gap_count: int = 0
    reason_counts: dict[str, int] = Field(default_factory=dict)


class EvidenceCoverageDecision(BaseModel):
    level: str
    human_review_recommended: bool
    source_warning_count: int
    low_confidence_tool_count: int
    # v0.26: structured per-gap remediation rows; a deterministic
    # projection of the two counts above. Default empty so older
    # payloads load as baselines unchanged.
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    # v0.29: evidence-backed pass coverage. Default empty preserves
    # round-tripping of frozen pre-v0.29 reports while emitted reports
    # always populate it from Tool.semantic_assessment.
    semantic_coverage: SemanticCoverageDecision = Field(default_factory=SemanticCoverageDecision)
    identity_coverage: IdentityCoverageDecision = Field(default_factory=IdentityCoverageDecision)
    binding_coverage: BindingCoverageDecision = Field(default_factory=BindingCoverageDecision)
    policy_gap_count: int = 0


class BaselineDelta(BaseModel):
    enabled: bool
    path: str | None = None
    matched_count: int = 0
    new_count: int = 0
    resolved_count: int = 0


class FailPolicy(BaseModel):
    ci_mode: str
    fail_on: list[Severity] = Field(default_factory=list)
    new_findings_only: bool = False
    would_fail_ci: bool
    exit_code: int


# v0.17: explicit, deterministic per-finding audit of *why* each finding
# landed in `blockers[]`, `review_items[]`, or was excluded. The set of
# rule names below is the entire grammar of decisions the gate can make;
# the truth table in STABILITY.md "Release decision truth table" is the
# external contract for what each name means and when it fires.
ContributionRuleName = Literal[
    # Active blockers (drive `decision="blocked"` and, in strict mode,
    # exit code 20 when the underlying finding is not baseline-matched
    # via `--baseline-mode new-findings`).
    "policy_block_new",
    "severity_block_new",
    # Accepted as baseline debt; visible in `review_items[]` instead of
    # `blockers[]`. Never escalates the decision past `review_required`.
    "policy_baseline_accepted",
    "severity_baseline_accepted",
    # Routed to `review_items[]` for human attention but does not block
    # by itself.
    "review_required",
    # Below the active gate threshold AND below review tier; recorded
    # for completeness so the audit table is exhaustive over
    # report.findings.
    "sub_threshold",
    "unsupported_evidence",
    # Suppressed via `checks.ignore[]` in the manifest; excluded from
    # the active set entirely.
    "suppressed",
]


class ContributionRule(BaseModel):
    """Per-finding audit row explaining how a finding contributed to the
    release decision.

    Additive in v0.17. Every finding in `report.findings` produces
    exactly one ContributionRule. Reading the contribution rule is
    sufficient to predict the gate outcome for that finding without
    re-deriving the decision logic; the set of valid `(rule, category)`
    pairs is the contract documented in STABILITY.md "Release decision
    truth table".
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    fingerprint: str | None = None
    check_id: str
    category: Literal["blocker", "review_item", "excluded"]
    rule: ContributionRuleName
    rationale: str


class ReleaseDecision(BaseModel):
    decision: ReleaseDecisionStatus
    reason: str
    blockers: list[ReleaseDecisionItem] = Field(default_factory=list)
    review_items: list[ReleaseDecisionItem] = Field(default_factory=list)
    evidence_coverage: EvidenceCoverageDecision
    baseline_delta: BaselineDelta
    fail_policy: FailPolicy
    # v0.29: make the verdict boundary explicit for machine consumers.
    # ``passed`` is an evidence-backed static verdict; it must never be
    # interpreted as proof of runtime behavior or enforcement.
    static_analysis_only: Literal[True] = True
    runtime_behavior_verified: Literal[False] = False
    static_verdict_disclaimer: str = STATIC_VERDICT_DISCLAIMER
    # v0.17: deterministic per-finding audit of how each finding
    # contributed to the decision. Always present (defaults to []) so
    # consumers that read `release_decision.contribution_rules` never
    # need an existence check; older reports loaded via
    # `explain-finding` or test helpers naturally get an empty list.
    contribution_rules: list[ContributionRule] = Field(default_factory=list)


DeclaredIntentionKind = Literal[
    "declared_purpose",
    "prohibited_action",
    "instruction_preview",
]
CapabilityIncludedReason = Literal[
    "high_risk_tag",
    "wildcard_exposure",
    "referenced_by_critical_finding",
    "referenced_by_high_finding",
    "referenced_by_medium_finding",
]
CapabilityControlStatus = Literal["missing", "partial", "present", "unknown"]
MisalignmentKind = Literal[
    "policy_gap",
    "scope_drift",
    "prohibited_action_present",
    "control_missing",
    "intent_mismatch",
    "undetected_gap",
]
SuggestedScenarioType = Literal[
    "approval",
    "confirmation",
    "idempotency_retry",
    "least_privilege_scope",
    "prohibited_action",
    "wildcard_inventory",
    "schema_boundary",
    "prompt_scope_alignment",
    "test_case_coverage",
]


class CapabilityFact(BaseModel):
    id: str
    tool_id: str | None = None
    tool_name: str
    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    capability: str
    # v0.29: the normalized conservative effect and its evidence travel with
    # the reviewer-facing capability projection. Defaults preserve older
    # report readers while every newly emitted fact populates both fields.
    effect: ActionEffect | None = None
    semantic_assessment: ToolSemanticEvidence | None = None
    risk_tags: list[str] = Field(default_factory=list)
    auth_scopes: list[str] = Field(default_factory=list)
    owner: str | None = None
    included_reason: CapabilityIncludedReason
    control_status: CapabilityControlStatus
    related_findings: list[str] = Field(default_factory=list)


class DeclaredIntention(BaseModel):
    id: str
    kind: DeclaredIntentionKind
    text: str
    source: str
    intent_tags: list[str] = Field(default_factory=list)


class Misalignment(BaseModel):
    id: str
    kind: MisalignmentKind
    severity: Severity
    tool_name: str | None = None
    capability_refs: list[str] = Field(default_factory=list)
    intention_refs: list[str] = Field(default_factory=list)
    finding_refs: list[str] = Field(default_factory=list)
    policy_requirement: str
    gap: str
    release_implication: str


class ReleaseConsequence(BaseModel):
    decision: ReleaseDecisionStatus
    summary: str
    blocker_misalignment_count: int = 0
    review_misalignment_count: int = 0
    fail_policy: FailPolicy


class SuggestedScenario(BaseModel):
    id: str
    scenario_type: SuggestedScenarioType
    title: str
    given: str
    expected_control: str
    source_misalignments: list[str] = Field(default_factory=list)
    source_findings: list[str] = Field(default_factory=list)


class LoadedPolicyPack(BaseModel):
    id: str
    name: str
    version: str | None = None
    path: str
    source: str | None = None
    sha256: str | None = None
    sha256_status: Literal["unpinned", "verified"] = "unpinned"
    owner: str | None = None
    rule_count: int


class AgentSummaryAction(BaseModel):
    """A single recommended next step shaped for direct agent consumption.

    Mirrors the ``next_actions[]`` shape used elsewhere in the contract
    (kind/command/why) so callers that already handle diagnostic
    next_actions can reuse the same renderer here.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["command", "info"] = "command"
    command: str | None = None
    why: str


class AgentSummary(BaseModel):
    """Top-level summary block shaped for one-fetch agent consumption.

    Deterministic projection of (``release_decision``, ``findings[].agent_action``).
    A coding agent that wants the headline numbers can read this block
    instead of traversing arrays. All fields are derived; this block
    cannot disagree with the underlying data.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: ReleaseDecisionStatus
    headline: str
    blocker_count: int = 0
    review_item_count: int = 0
    auto_appliable_patches: int = 0
    needs_human_review: int = 0
    first_recommended_action: AgentSummaryAction | None = None


ReviewerSurfaceKind = Literal[
    "release_decision",
    "lens",
    "audit",
    "evidence_matrix",
]


ReviewerSurfaceName = Literal[
    # Lenses (4 in-report; evidence_matrix lives in packet but is
    # projectable from the report payload via build_evidence_matrix).
    "tool_surface_diff",
    "capability_intent_diff",
    "action_surface_diff",
    "evidence_matrix",
    # Audit envelopes (3).
    "policy_audit",
    "privacy_audit",
    "baseline_integrity",
    # Top-level verdict surface.
    "release_decision",
]


class ReviewerSurfacePointer(BaseModel):
    """A single recommended reviewer entry point.

    Mirrors the ``next_actions[]`` shape (kind/path/why) used by other
    contract surfaces so the same renderer pattern works here. ``kind``
    classifies the surface family (``release_decision`` /``lens`` /
    ``audit`` /``evidence_matrix``); ``name`` is the canonical
    machine-readable identifier; ``path`` is a dotted JSON path the
    reviewer can use to navigate. ``why`` is one sentence of reviewer
    rationale, suitable for a PR comment lead.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ReviewerSurfaceKind
    name: ReviewerSurfaceName
    path: str
    why: str


class ReviewerSummary(BaseModel):
    """Top-level summary block shaped for one-fetch reviewer consumption.

    Deterministic projection of the reviewer lens surfaces
    (``tool_surface_diff``, capability/intent diff, ``action_surface_diff``,
    evidence matrix) and the audit envelopes (``policy_audit``,
    ``privacy_audit``, baseline integrity findings). A reviewer who wants
    headline activity counts and a recommended starting surface can read
    this block instead of opening every lens / audit envelope.

    Parallels ``AgentSummary`` but for the audit/lens dimensions:
    ``AgentSummary`` answers "what should an agent do next?" and
    ``ReviewerSummary`` answers "what should a reviewer look at first?".

    All fields are derived; this block cannot disagree with the
    underlying lens/audit data.
    """

    model_config = ConfigDict(extra="forbid")

    # Mirror the release verdict for at-a-glance context. The exact same
    # ``ReleaseDecisionStatus`` alias as ``AgentSummary.verdict`` and
    # ``release_decision.decision`` so a downstream consumer can switch on
    # any block without re-deriving — and the vocabulary cannot drift.
    verdict: ReleaseDecisionStatus
    headline: str

    # Per-lens activity counts. Each is the cheapest "did this lens
    # fire?" count we can project without re-deriving release logic.
    # Zero means the lens produced no reviewer-actionable signal on
    # this scan.
    tool_surface_changes: int = 0
    capability_misalignments: int = 0
    action_surface_changes: int = 0
    evidence_matrix_gaps: int = 0

    # Per-audit envelope counts. ``severity_overrides_tier_crossed``
    # is the subset of ``severity_overrides_applied`` whose application
    # crossed a severity tier boundary (the audit row's ``tier_crossed``
    # flag) — surfaced separately because it is the highest-attention
    # subset for a reviewer.
    severity_overrides_applied: int = 0
    severity_overrides_tier_crossed: int = 0
    privacy_redactions: int = 0
    baseline_integrity_issues: int = 0

    # Deterministic recommended starting surface for the reviewer. None
    # only when the scan is fully clean (verdict=passed + every count
    # above is zero).
    first_recommended_surface: ReviewerSurfacePointer | None = None


class SeverityOverrideAuditEntry(BaseModel):
    """One row in ``ReadinessReport.policy_audit.severity_overrides_applied``.

    v0.17 (M1). Surfaces every manifest-driven severity override so a
    reviewer can see what was downgraded (or upgraded) without diving
    into per-finding evidence. Emitted regardless of whether the override
    matched any active finding — entries for checks that did not fire
    still document reviewer intent.
    """

    model_config = ConfigDict(extra="forbid")

    check_id: str
    default_severity: Severity
    applied_severity: Severity
    # The resolved manifest source (e.g.,
    # ``shipgate.yaml#/checks/severity_overrides/SHIP-...``).
    manifest_path: str
    reason: str | None = None
    # ``True`` when the override crosses a tier boundary
    # (critical / high / medium-low). Tier-crossing downgrades require a
    # matching ``acknowledge_overrides`` entry; tier-crossing upgrades
    # never require ack (strictly more conservative).
    tier_crossed: bool = False
    # ``"downgrade"`` (weaker than default), ``"upgrade"`` (stronger), or
    # ``"same"`` (no-op override — kept in audit for completeness).
    direction: Literal["downgrade", "upgrade", "same"] = "same"
    # ISO date copied verbatim from the matching acknowledgement when
    # present. ``None`` for non-acknowledged overrides.
    expires: str | None = None


class PolicyAudit(BaseModel):
    """v0.17 (M1) top-of-report audit envelope for policy decisions
    applied during scan.

    Carries severity-override audit today; M2 (baseline integrity) and
    M5 (plugin validation) will land sibling fields here so the audit
    envelope stays stable across the trust-hardening releases.
    """

    model_config = ConfigDict(extra="forbid")

    severity_overrides_applied: list[SeverityOverrideAuditEntry] = Field(default_factory=list)


class RedactedPathSummary(BaseModel):
    """One privacy-audit row for a structural output path.

    The row intentionally carries only aggregate counts and secret kinds,
    never the original value or a hash/verifier of that value.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    count: int = 0
    kinds: list[str] = Field(default_factory=list)


class PrivacyAudit(BaseModel):
    """Top-level audit envelope proving the default redaction pass ran."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    rules_version: str
    sensitive_field_inventory_version: str
    redacted_occurrence_count: int = 0
    redacted_paths: list[RedactedPathSummary] = Field(default_factory=list)
    output_surfaces: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# v0.21: stable list of ``provenance_kind`` values that ``--no-heuristics``
# excludes from the active finding set. Pinned as a module-level constant
# so downstream consumers (tests, docs, the contract command) can read the
# same source of truth. ``static_declaration`` and ``ast_extraction``
# describe how the *finding* was produced from declared/parsed-shape data;
# ``keyword_heuristic`` and ``regex_heuristic`` describe token/regex
# matches that are best-effort by nature. ``policy_pack`` stays in scope
# because the rule body is declared, even though the trigger may pattern-
# match — operators who load a policy pack want its findings.
NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS: tuple[str, ...] = (
    "keyword_heuristic",
    "regex_heuristic",
)


class HeuristicsFilter(BaseModel):
    """v0.21: top-level envelope describing the ``--no-heuristics``
    filter pass.

    Emitted on every report regardless of whether the flag was set, so
    consumers always read the same shape. When the flag is unset,
    ``enabled=False`` and the count fields are zero — the active
    finding set is unchanged. When the flag is set, every finding whose
    ``provenance_kind`` is in ``excluded_provenance_kinds`` is marked
    ``suppressed=True`` with ``suppression_reason="filtered by
    --no-heuristics"`` BEFORE the release decision is built, so heuristic
    findings can no longer gate release. Filtered findings stay in
    ``findings[]`` for transparency; the audit envelope here records
    aggregate counts.

    Earns the contract weight of ``Finding.provenance_kind`` (shipped
    v0.15) by giving it a first-class consumer. Same shape pattern as
    ``PrivacyAudit``: an envelope that proves the filter ran and tells
    a reviewer/agent which findings were excluded and why.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    excluded_provenance_kinds: list[str] = Field(default_factory=list)
    filtered_finding_count: int = 0
    # Per-provenance-kind breakdown of filtered counts so a reviewer
    # can tell "we filtered N regex_heuristic findings and M
    # keyword_heuristic findings" without scanning ``findings[]``.
    filtered_by_kind: dict[str, int] = Field(default_factory=dict)


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "0.1"
    # v0.17 trust-hardening: M8 adds ``release_decision.contribution_rules[]``
    # and M1 adds the top-level ``policy_audit`` block. Both are
    # additive — older consumers ignore the new fields.
    # v0.19 reviewer-grade provenance: ``Finding.policy_evidence_source``
    # and ``ReleaseDecisionItem.{source, policy_evidence_source}`` are
    # additive optional fields carrying a second structured pointer
    # (manifest YAML path + line) for high-risk findings whose
    # triggering evidence also lives in the manifest. Old consumers
    # ignore the new fields.
    # v0.22 (verifier cycle, P2/M3): additive top-level blocks for the AI
    # coding workflow verifier — ``capability_change`` (diff-derived
    # capability delta), ``protected_surface_changes`` (touched trust
    # roots), ``effective_policy`` (normalized policy snapshot),
    # ``human_ack`` (declared human-acknowledgement state), and
    # ``verifier_summary`` (composition alias over release_decision +
    # reviewer/agent summaries + capability delta). All are reviewer-facing
    # projections / inputs — none introduces a new release gate
    # (``release_decision.decision`` remains the only gate). Emitted as
    # deterministic projections or empty/default shapes when no evidence
    # exists; older consumers ignore them.
    # v0.23: additive semantic metadata on capability_change members.
    # Existing buckets and summary counts stay intact; new fields explain
    # the capability-hash / semantic reason behind each row when proven.
    # v0.24: additive capability-native policy/evidence fields on findings
    # and release-decision items. release_decision.decision remains the
    # only release gate.
    # v0.25: additive opt-in local runtime trace/provenance evidence
    # linked to capability facts. Runtime trace evidence is declared
    # local audit metadata only; it is not live collection and it is not
    # part of the static capability lock envelope.
    # v0.26: additive structured evidence gaps
    # (``release_decision.evidence_coverage.evidence_gaps[]``) — one
    # actionable remediation row per low-confidence tool / source
    # warning, plus the advisory ``suggested-inventory.json`` artifact.
    # Pure projection of existing counts; gate behavior unchanged.
    # v0.27: additive policy-pack distribution metadata on
    # ``loaded_policy_packs[]`` (source, sha256, sha256_status, owner).
    # v0.28: policy-pack rule owner/reviewer/approval routing metadata
    # moved out of ``Finding.evidence`` into ``Finding.policy_routing``.
    # The release gate is unchanged; these are org-governance audit fields.
    # v0.29: additive semantic assessments and zero-tolerance semantic
    # evidence coverage make ``passed`` evidence-backed.
    # v0.30: provider-scoped canonical tool identity.
    # v0.31: root-reachable agent binding facts, diffs, and coverage.
    # v0.32: required Conductor OSS workflow summary fields.
    # v0.33: typed evidence basis, predicate support, and unsuppressible
    # indeterminate-policy evidence gaps.
    # v0.34: content-addressed verification request and decision bindings.
    # v0.35: the exclusion ledger — one typed record per narrowing decision,
    # plus ``binding_surface_diff.added_unbound_tool_ids``. Additive; the
    # release gate remains ``release_decision.decision``, but a subject this
    # change newly excluded now reaches it as an evidence gap instead of
    # disappearing between stages (#403).
    report_schema_version: str = "0.35"
    run_id: str
    request_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    subject_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    input_set_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    engine_requirement_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    decision_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    # v0.6 (per C13): absolute path to the directory containing
    # shipgate.yaml. apply-patches uses this to enforce a containment
    # check on every patch's target_file. Optional for backwards
    # compatibility with older reports loaded as baselines.
    manifest_dir: str | None = None
    project: dict[str, Any]
    agent: dict[str, Any]
    environment: dict[str, Any]
    summary: ReportSummary
    # v0.8: required at JSON-schema level (see scripts/generate_schemas.py),
    # but Python-optional so older test fixtures and SARIF-only callers
    # can construct minimal reports. build_report() always populates it.
    release_decision: ReleaseDecision | None = None
    # v0.9 capability/intent diff. Populated for emitted scan reports after
    # release_decision is built; defaults keep older test helpers lightweight.
    capability_facts: list[CapabilityFact] = Field(default_factory=list)
    declared_intentions: list[DeclaredIntention] = Field(default_factory=list)
    misalignments: list[Misalignment] = Field(default_factory=list)
    release_consequence: ReleaseConsequence | None = None
    suggested_scenarios: list[SuggestedScenario] = Field(default_factory=list)
    tool_surface: ToolSurfaceSummary
    # v0.10 tool-surface diff. `tool_surface` remains the count summary;
    # these facts/diff fields are explanatory reviewer data.
    tool_surface_facts: ToolSurfaceFacts = Field(default_factory=ToolSurfaceFacts)
    tool_surface_diff: ToolSurfaceDiff = Field(default_factory=ToolSurfaceDiff)
    # v0.16 action-surface diff. This is the first-class PR/release
    # delta of what an agent can do externally, derived from the same
    # static tool surface plus optional manifest action declarations.
    action_surface_facts: ActionSurfaceFacts = Field(default_factory=ActionSurfaceFacts)
    action_surface_diff: ActionSurfaceDiff = Field(default_factory=ActionSurfaceDiff)
    binding_surface_facts: AgentBindingGraphAssessment = Field(
        default_factory=lambda: AgentBindingGraphAssessment(
            root_agent_id="legacy_direct",
            status="structural",
            pass_eligible=True,
        )
    )
    binding_surface_diff: BindingSurfaceDiff = Field(default_factory=BindingSurfaceDiff)
    capability_runtime_evidence: CapabilityRuntimeEvidence = Field(
        default_factory=CapabilityRuntimeEvidence
    )
    api_surface: dict[str, Any] | None = None
    anthropic_surface: dict[str, Any] | None = None
    frameworks: dict[str, Any] = Field(default_factory=dict)
    codex_plugin_surface: CodexPluginSurface | None = None
    baseline: BaselineSummary | None = None
    findings: list[Finding] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    generated_reports: dict[str, str] = Field(default_factory=dict)
    loaded_policy_packs: list[LoadedPolicyPack] = Field(default_factory=list)
    loaded_plugins: list[dict[str, Any]] = Field(default_factory=list)
    # v0.20: third-party adapter provenance. Mirrors loaded_plugins[] but
    # for the agents_shipgate.adapters entry-point group. Always present
    # on emitted scans (empty list when no third-party adapters discovered
    # or when --no-plugins is set). Required + non-nullable on the wire;
    # Python-Optional via default_factory so older test helpers
    # constructing minimal reports keep working.
    loaded_adapters: list[dict[str, Any]] = Field(default_factory=list)
    tool_inventory: list[dict[str, Any]] = Field(default_factory=list)
    tool_catalog: list[dict[str, Any]] = Field(default_factory=list)
    source_warnings: list[str] = Field(default_factory=list)
    # v0.33: indeterminate policy applicability stays outside Finding so it
    # cannot be baselined, suppressed, severity-overridden, or acknowledged.
    policy_evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    # v0.12: top-level agent summary. Deterministic projection of
    # release_decision + findings[].agent_action. Optional at Python
    # level so older test helpers can construct minimal reports;
    # build_report() always populates it for emitted scans.
    agent_summary: AgentSummary | None = None
    # v0.17 (M1): top-of-report audit of manifest-driven policy decisions
    # applied during scan (severity overrides today; baseline integrity
    # and plugin validation in upcoming trust-hardening releases). Always
    # present on emitted scans; Python-Optional so older test helpers can
    # construct minimal reports.
    policy_audit: PolicyAudit | None = None
    # v0.18: top-level privacy audit. Emitted scans always carry this
    # envelope after the default-on redaction pass has sanitized public
    # outputs. Optional at Python level for older fixtures.
    privacy_audit: PrivacyAudit | None = None
    # v0.21: top-level heuristics-filter envelope. Required + non-nullable
    # on the wire (mirrors privacy_audit shape). When enabled=False the
    # active finding set is unchanged; when enabled=True every finding
    # whose ``provenance_kind`` is in ``excluded_provenance_kinds`` has
    # been marked ``suppressed=True`` before the release decision was
    # built. Optional at the Python level for older test helpers that
    # construct minimal reports.
    heuristics_filter: HeuristicsFilter | None = None
    # v0.20: top-level reviewer summary. Deterministic projection of
    # the reviewer lens surfaces (tool_surface_diff, capability/intent
    # diff, action_surface_diff, evidence matrix) and audit envelopes
    # (policy_audit, privacy_audit, baseline integrity findings). A
    # reviewer who wants headline activity counts and a recommended
    # starting surface reads this block instead of opening every lens
    # and audit envelope. Parallels ``agent_summary`` (v0.12) but for
    # the audit/lens dimensions. Optional at Python level so older
    # test helpers can construct minimal reports; build_report() always
    # populates it for emitted scans.
    reviewer_summary: ReviewerSummary | None = None
    # v0.22 (verifier cycle, P2/M3): the diff-derived capability delta,
    # grouped into added/removed/broadened/narrowed member lists. A
    # reviewer-facing projection over action_surface_diff /
    # tool_surface_diff — it never gates on its own. Always present on
    # emitted scans (deterministic empty/disabled shape when no base diff
    # is available). Optional at the Python level for older test helpers.
    capability_change: CapabilityChangeBlock | None = None
    # v0.22: touched protected paths/policies (trust roots). Tier A
    # trust-root protection records *which* protected surface a PR
    # touched; the ordinary SHIP-VERIFY-* findings are what gate. Always
    # present on emitted scans (empty list when no verification context /
    # no trust root touched). Optional in Python for older fixtures.
    protected_surface_changes: list[ProtectedSurfaceChange] = Field(default_factory=list)
    # v0.22: normalized effective-policy snapshot. A semantic (not text)
    # view of the policy surface so the verify comparator can answer
    # "was the gate weakened?". Always present on emitted scans
    # (deterministic default shape). Optional in Python for older
    # fixtures.
    effective_policy: EffectivePolicy | None = None
    # v0.22: declared human-acknowledgement state. Within the static
    # boundary acknowledgement can only be declared evidence, never
    # inferred. Default shape is not-required / satisfied with empty
    # lists. Always present on emitted scans; Optional in Python for
    # older fixtures.
    human_ack: HumanAck | None = None
    # v0.22: top-level verifier composition alias. Byte-stable projection
    # bundling the release verdict, finding counts, capability-delta
    # summary, and trust-root / acknowledgement flags. ``verdict`` always
    # mirrors ``release_decision.decision`` (Principle 2 — one decision
    # engine). Always present on emitted scans; Optional in Python for
    # older fixtures.
    verifier_summary: VerifierSummary | None = None
    # v0.35: every narrowing decision this run made, as one typed record per
    # removed subject. Read it to answer "what did the gate decide not to
    # look at, and did the verdict know?" — the counts are what
    # ``build_release_decision`` consumes, the entries are what a reviewer
    # reads. Always present on emitted scans (empty ledger when nothing was
    # narrowed); Optional-free because an absent ledger and an empty one
    # must not be confusable.
    surface_exclusions: SurfaceExclusionLedger = Field(
        default_factory=SurfaceExclusionLedger
    )
