from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.codex_plugin import CodexPluginSurface
from agents_shipgate.schemas.common import (
    AgentAction,
    BaselineStatus,
    Confidence,
    ProvenanceKind,
    Severity,
    SourceReference,
)
from agents_shipgate.schemas.patches import Patch
from agents_shipgate.schemas.surfaces import (
    ActionSurfaceDiff,
    ActionSurfaceFacts,
    ToolSurfaceDiff,
    ToolSurfaceFacts,
)


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
# divergence contract with summary.status (which stays baseline-blind
# for backwards compatibility).
ReleaseDecisionStatus = Literal[
    "blocked",
    "review_required",
    "insufficient_evidence",
    "passed",
]


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


class EvidenceCoverageDecision(BaseModel):
    level: str
    human_review_recommended: bool
    source_warning_count: int
    low_confidence_tool_count: int


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
    tool_name: str
    source_type: str
    source_ref: str | None = None
    capability: str
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

    verdict: Literal[
        "blocked",
        "review_required",
        "insufficient_evidence",
        "passed",
    ]
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

    # Mirror the release verdict for at-a-glance context. Same enum as
    # ``AgentSummary.verdict`` so a downstream consumer can switch on
    # either block without re-deriving.
    verdict: Literal[
        "blocked",
        "review_required",
        "insufficient_evidence",
        "passed",
    ]
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

    severity_overrides_applied: list[SeverityOverrideAuditEntry] = Field(
        default_factory=list
    )


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
    report_schema_version: str = "0.21"
    run_id: str
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
    source_warnings: list[str] = Field(default_factory=list)
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
