from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.action_effects import (
    EFFECT_RISK_RANK,
    RISK_TAG_EFFECTS,
    declaration_covers,
    declaration_effects,
)
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
from agents_shipgate.schemas.patches import (
    ACTION_SELECTOR_KEYS,
    DeclareActionPatch,
    Patch,
)
from agents_shipgate.schemas.semantic import ToolSemanticEvidence
from agents_shipgate.schemas.surfaces import (
    ActionDeclarationFacts,
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


class EvidenceReading(BaseModel):
    """v0.37: one effect the evidence for an action can be read as.

    A blank asking for an action's effect is only answerable in place if the
    reader can see what the scan actually saw. These rows are that: the
    distinct readings the non-declaration effect claims support, each with the
    producers that support it.

    ``observed`` separates evidence *about this action* from a default standing
    in for the absence of any. An MCP tool with no annotations gets a
    ``write`` reading from the protocol default — worth showing, never worth
    pre-filling, because it is a statement about the protocol rather than an
    observation of the tool.

    v0.39: ``policy_eligible`` is the reading's *strength* — true when at least
    one non-manifest claim behind it is evidence the scanner may act on
    (protocol structure, a typed provider fact, or a source-owned structural
    scope) rather than a heuristic that may only challenge. Reviewed manifest
    declarations are constraints, not observations, and therefore never
    produce a reading. Strength is the strongest class among the remaining
    claims, never a per-producer flag. Published because it is half of what
    ``action_surface.actions[].basis`` pins: without it, a reading whose
    authoritative half was deleted is indistinguishable from one that never had
    it, and a consumer cannot reproduce the pin from the row it is printed on.
    """

    model_config = ConfigDict(extra="forbid")

    effect: str
    sources: list[str] = Field(default_factory=list)
    observed: bool = True
    policy_eligible: bool = False


#: The reserved value a ``declaration_template`` carries where the scan has
#: nothing to propose and a human must answer.
#:
#: Defined here, beside the model whose invariant is stated in terms of it: an
#: agent-authorable row is exactly one whose template carries none of these,
#: and a rule enforced by a constant imported from the module that *emits*
#: templates would be checkable only by that module. ``ci.release_decision``
#: re-exports it under its historical name.
REVIEW_REQUIRED_SENTINEL = "<REVIEW_REQUIRED>"

#: Gap-action kinds whose answer a scan can draft in full.
#:
#: One kind, and it is a claim about the *vocabulary*, not about the row: an
#: effect is a fact about code, drawn from a closed enum, that the scan reads
#: directly. Authority is a fact about a deployment (which credential the
#: process runs with), an override is a reviewer's judgement, and a tool
#: inventory is a completeness claim — none of the three is in the repository
#: for a scanner to read, so none of them may ever be drafted by one (#410).
AGENT_AUTHORABLE_GAP_ACTION_KINDS: frozenset[str] = frozenset({"declare_action_effect"})

#: Gap *kinds* whose answer is a person's however answerable it looks.
#:
#: ``declaration_drift`` asks a reviewer to look again at an answer they already
#: gave, because the evidence behind it moved (#410 §E). Its repair is spelled
#: ``declare_action_effect`` and its template is complete — it restates the
#: declared effect beside the new pin — so the content rule alone would read it
#: as an agent's to draft. Letting one write it would re-stamp the pin and
#: close the request to look, which is the only thing the row is for. Keyed on
#: the gap kind rather than the action kind for exactly that reason: the action
#: names the claim, and two different questions can ask for the same claim.
HUMAN_ONLY_GAP_KINDS: frozenset[str] = frozenset({"declaration_drift"})

_ACTION_EFFECT_VALUES: frozenset[str] = frozenset(get_args(ActionEffect))


def template_is_complete(template: Mapping[str, Any] | None) -> bool:
    """Did the scan fill every blank in this declaration template?

    Recursive, and deliberately over-strict: any ``<REVIEW_REQUIRED>`` anywhere
    inside — at any depth, inside a list, as a mapping key — means a human
    still owes an answer here, so the whole template is not the scan's to
    write. An empty template is not complete either; it proposes nothing.
    """

    if not template:
        return False
    return not _carries_sentinel(template)


def _carries_sentinel(value: Any) -> bool:
    if isinstance(value, str):
        return value == REVIEW_REQUIRED_SENTINEL
    if isinstance(value, Mapping):
        return any(
            _carries_sentinel(key) or _carries_sentinel(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_carries_sentinel(item) for item in value)
    return False


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
    # v0.41: who may write the first draft of this answer.
    #
    # A separate question from ``requires_human_review`` below, which stays
    # pinned ``true``: the manifest is the trust root, so *every* declaration
    # reaches the gate through a human merge, whoever typed it. What this
    # field adds is whether the scan already knows the answer well enough for
    # an agent to propose it from its own observations. Existing reviewed
    # manifest constraints may shape a human-visible proposal, but never grant
    # coding-agent authorship. "An agent may propose what evidence supports;
    # only a human may assert against it" (#410 §D).
    #
    # It is decided by content, never by authorship: ``coding_agent`` exactly
    # where the scan filled every blank in ``declaration_template`` from its
    # own observations, using the closed effect vocabulary and a value never
    # weaker than any reading. A template still carrying a
    # ``<REVIEW_REQUIRED>`` blank — every authority block, every override —
    # is an assertion an agent would have to invent, so it stays ``human``.
    authorable_by: Literal["coding_agent", "human"] = "human"
    # Declaration skeleton for a human to complete, and — since v0.41, and
    # only where ``authorable_by`` is ``coding_agent`` — the exact patch that
    # writes it. ``manual`` still means what it always meant: no
    # machine-applicable change is published for this row.
    #
    # ``declare_action`` does not make the row auto-applicable. It is outside
    # ``apply-patches --kinds`` by default, it is applied only by the route
    # that proposes it, and it never overwrites an answer the manifest
    # already carries.
    suggested_patch_kind: Literal["manual", "declare_action"] = "manual"
    declaration_template: dict[str, Any] | None = None
    # v0.41: the machine-applicable form of ``declaration_template`` — the same
    # answer, split into the row it identifies and the fields it writes,
    # because those are the two halves ``apply-patches`` treats differently.
    # The split is exact and the validator below proves it: this patch can
    # never write a field the template does not carry.
    patch: DeclareActionPatch | None = None
    # v0.37: what the scan read this action's effect as, so the row can be
    # answered without opening ``action_surface_facts`` to find out. Populated
    # for effect gaps; empty everywhere else. Where the readings support one
    # conservative answer, ``declaration_template`` carries it pre-filled
    # instead of a ``<REVIEW_REQUIRED>`` blank. Existing reviewed manifest
    # constraints may strengthen that proposal and are named separately in
    # ``expects``; they never appear here. The template is a proposal to
    # confirm, never an assertion: nothing consumes it, and only a reviewed
    # edit to the manifest can make any of it operative.
    observed_readings: list[EvidenceReading] = Field(default_factory=list)
    auto_apply: Literal[False] = False
    requires_human_review: Literal[True] = True

    @model_validator(mode="after")
    def _authorship_matches_the_template(self) -> EvidenceGapAction:
        """An agent-authorable row is one whose answer the scan actually wrote.

        Enforced on the model rather than trusted to the one builder that sets
        it today. ``authorable_by`` is what decides whether a coding agent may
        write into the trust root, and ``patch`` is what it would write, so
        both rules have to hold for every row this type can express —
        including one a consumer assembles from a stored payload, and
        including a gap kind added later whose builder forgets to think about
        authorship.

        The binding between the two is exact: ``patch`` is the template, split
        into the half that names the action and the half that is written. A
        row therefore cannot publish a tag that says "evidence-derived" beside
        a patch that writes something else — an authority block, an effect
        outside the vocabulary, or a field the template never mentioned.

        One thing this layer cannot check, and it is stated rather than
        implied: whether the *template* is itself consistent with
        ``observed_readings`` needs the effect-covering relation, which lives
        in ``core.semantic_assessment`` and may not be imported here. What is
        checked instead is the one direction where accepting a weaker answer
        loses safety rather than over-declaring — a declared ``read`` against
        an observation that is not ``read`` (#357). The full covering property
        is proven where it is produced, and swept over every published row by
        ``test_declaration_authoring``.
        """

        if self.authorable_by == "coding_agent":
            if self.kind not in AGENT_AUTHORABLE_GAP_ACTION_KINDS:
                raise ValueError(
                    f"{self.kind!r} is not an answer a scan may draft; "
                    "authorable_by must be 'human'"
                )
            if not template_is_complete(self.declaration_template):
                raise ValueError(
                    "authorable_by='coding_agent' requires a declaration_template "
                    "with no <REVIEW_REQUIRED> blank left in it"
                )
            # A template that is only a selector names an action and declares
            # nothing about it. It is "complete" in the sense that no blank is
            # left, and it is still not an answer: the patch built from it
            # would write no field, and the question would be counted as one
            # an agent can close while nothing closes it.
            if set(self.declaration_template or {}) <= set(ACTION_SELECTOR_KEYS):
                raise ValueError(
                    "authorable_by='coding_agent' requires a declaration_template "
                    "that declares something beyond the action it names"
                )
        elif self.patch is not None or self.suggested_patch_kind != "manual":
            raise ValueError(
                "a row only a human may author publishes no machine-applicable patch"
            )
        if self.patch is None:
            if self.suggested_patch_kind != "manual":
                raise ValueError(
                    "suggested_patch_kind names a patch this row does not publish"
                )
            return self
        if self.suggested_patch_kind != "declare_action":
            raise ValueError("suggested_patch_kind must name the patch this row publishes")
        self._patch_writes_exactly_the_template()
        return self

    def _patch_writes_exactly_the_template(self) -> None:
        patch = self.patch
        assert patch is not None  # guarded by the caller
        if patch.confidence != "high":
            raise ValueError(
                "a declaration patch is published only at high confidence; "
                "the route that applies it filters on nothing else"
            )
        selector, declaration = patch.selector, patch.declaration
        if not set(selector) <= set(ACTION_SELECTOR_KEYS):
            raise ValueError("a declaration patch selector names only the action")
        if set(declaration) & set(ACTION_SELECTOR_KEYS):
            raise ValueError(
                "a declaration patch writes declared fields, never the keys that "
                "identify the row"
            )
        if {**selector, **declaration} != (self.declaration_template or {}):
            raise ValueError(
                "a declaration patch writes exactly the declaration_template this "
                "row publishes, split into the action it names and the fields it "
                "declares"
            )
        effect = declaration.get("effect")
        if effect is not None and effect not in _ACTION_EFFECT_VALUES:
            raise ValueError(
                f"{effect!r} is not an effect; a drafted declaration comes from "
                "the closed vocabulary, never from source content"
            )
        if effect == "read" and any(
            reading.observed and reading.effect != "read"
            for reading in self.observed_readings
        ):
            raise ValueError(
                "a drafted declaration is never weaker than what was observed; "
                "read-only is the one reading a scan may not assert"
            )


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
        "unattested_surface",
        "missing_effect_evidence",
        "inferred_effect_only",
        "conflicting_effect_evidence",
        "declaration_below_inferred_evidence",
        "declaration_drift",
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
    # v0.35: the canonical tool id when this gap is about exactly one catalog
    # tool. ``subject`` is a display label — ``name [provider]`` — and two
    # catalog ids can legitimately render the same one, so joining on it marked
    # both rows accounted-for when only one was (PR #404 review 2). Consumers
    # display ``subject``; anything that needs identity joins on this.
    #
    # v0.38: ``subject_kind`` says which id space ``subject_id`` is in. Almost
    # every row is about one action; a source-wide authority question is about
    # the ``tool_sources`` entry that answers it, because one block answers
    # every action of that source (#410). Tool ids and source ids are
    # independent repository-chosen namespaces, so a consumer joining one
    # against the other has to be able to tell them apart rather than discover
    # the difference on a collision.
    subject_id: str | None = None
    subject_kind: Literal["action", "tool_source"] = "action"
    source_type: str | None = None
    source_ref: str | None = None
    # The policy whose applicability produced this row. Kept separate from
    # ``why`` so adopter prose never has to expose an engine-owned id, while
    # machines retain the exact identity needed to join the gap back to its
    # rule. ``builtin-*`` is forbidden only in adopter-facing prose; it is the
    # correct value in this structured field.
    policy_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    why: str
    next_action: EvidenceGapAction

    @model_validator(mode="after")
    def _a_human_only_question_is_never_drafted(self) -> EvidenceGap:
        """Some questions are a person's however answerable the answer looks.

        ``authorable_by`` is decided from the *action* — its kind and its
        template — and that is right for every row whose question is "what is
        this effect?". A drift row asks a different question with the same
        repair spelled on it: "you answered this once, and the evidence has
        moved; look again." Its template is complete because it restates the
        answer beside the new pin, so the content rule alone would hand it to
        an agent, which would re-stamp the pin and close the request to look.
        The gap kind is the only place that distinction exists, so the rule
        lives here rather than one model down.
        """

        if (
            self.kind in HUMAN_ONLY_GAP_KINDS
            and self.next_action.authorable_by == "coding_agent"
        ):
            raise ValueError(
                f"a {self.kind!r} row asks a person to look again, so it is "
                "never one a scan may draft"
            )
        return self


class AcknowledgedEffectOverride(BaseModel):
    """v0.36: one reviewed exception, in the shape a reviewer has to judge it.

    A count is not a review surface. #409 asks for the override to appear as an
    explicit row naming the action, both readings, and the reason a human gave
    — the reviewer reads exceptions, not every action, and that is what makes
    attestation real rather than rubber-stamped. Carried on the coverage block
    that already owns ``review_concern_count``, so the report, the verifier,
    the packet's §1, and the PR comment all project the same record.
    """

    model_config = ConfigDict(extra="forbid")

    #: Display label — ``name [provider]``, the same spelling evidence gaps use.
    subject: str
    #: Canonical tool id. Anything joining on identity joins on this.
    subject_id: str | None = None
    declared_effect: str
    inferred_effect: str
    #: Claim sources that read the stronger effect (``risk_hint:keyword``, …).
    inferred_sources: list[str] = Field(default_factory=list)
    #: Source evidence that agrees with the declared value, if any. Present so
    #: a reviewer can see the override was not written against the source.
    corroborating_sources: list[str] = Field(default_factory=list)
    #: What the reviewer checked, and why the inference does not apply.
    evidence: str
    reason: str
    manifest_path: str


class DeclarationReviewRow(BaseModel):
    """One changed head declaration, classified from the head evidence only."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"change_type": {"const": "removed"}},
                        "required": ["change_type"],
                    },
                    "then": {
                        "properties": {"bucket": {"const": "unverified"}},
                        "required": ["bucket"],
                    },
                },
                {
                    "if": {
                        "properties": {"bucket": {"const": "evidence_consistent"}},
                        "required": ["bucket"],
                    },
                    "then": {
                        "properties": {
                            "subject_id": {"type": "string", "minLength": 1},
                            "observed_readings": {
                                "minItems": 1,
                                "contains": {
                                    "properties": {"observed": {"const": True}},
                                    "required": ["observed"],
                                },
                                "items": {
                                    "if": {
                                        "properties": {"observed": {"const": True}},
                                        "required": ["observed"],
                                    },
                                    "then": {
                                        "properties": {
                                            "policy_eligible": {"const": True}
                                        },
                                        "required": ["policy_eligible"],
                                    },
                                },
                            },
                            "acknowledged_overrides": {"maxItems": 0},
                        },
                        "anyOf": [
                            {
                                "properties": {
                                    "declared_effect": {
                                        "enum": list(EFFECT_RISK_RANK)
                                    }
                                },
                                "required": ["declared_effect"],
                            },
                            {
                                "properties": {
                                    "declared_risk_tags": {
                                        "contains": {
                                            "enum": [
                                                tag
                                                for tag, effect in RISK_TAG_EFFECTS.items()
                                                if effect != "read"
                                            ]
                                        }
                                    }
                                },
                                "required": ["declared_risk_tags"],
                            },
                        ],
                        "required": [
                            "subject_id",
                            "observed_readings",
                            "acknowledged_overrides",
                        ],
                    },
                },
                {
                    "if": {
                        "properties": {"bucket": {"const": "acknowledged_override"}},
                        "required": ["bucket"],
                    },
                    "then": {
                        "properties": {
                            "subject_id": {"type": "string", "minLength": 1},
                            "acknowledged_overrides": {"minItems": 1},
                        },
                        "required": ["subject_id", "acknowledged_overrides"],
                    },
                    "else": {
                        "properties": {"acknowledged_overrides": {"maxItems": 0}},
                        "required": ["acknowledged_overrides"],
                    },
                },
            ]
        },
    )

    row_id: str = Field(min_length=1)
    change_type: Literal["added", "modified", "removed"]
    bucket: Literal[
        "evidence_consistent",
        "unverified",
        "acknowledged_override",
    ]
    subject: str
    subject_id: str | None = Field(default=None, min_length=1)
    declared_effect: str | None = None
    declared_risk_tags: list[str] = Field(default_factory=list)
    observed_readings: list[EvidenceReading] = Field(default_factory=list)
    reason: str
    manifest_path: str
    acknowledged_overrides: list[AcknowledgedEffectOverride] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def bucket_matches_evidence(self) -> DeclarationReviewRow:
        if self.change_type == "removed" and self.bucket != "unverified":
            raise ValueError(
                "removed declaration-review rows must remain unverified"
            )
        if self.bucket == "acknowledged_override":
            if not self.acknowledged_overrides:
                raise ValueError(
                    "acknowledged_override declaration-review rows require exact overrides"
                )
            if self.subject_id is None:
                raise ValueError(
                    "acknowledged_override declaration-review rows require subject_id"
                )
            identities: set[tuple[object, ...]] = set()
            for override in self.acknowledged_overrides:
                if (
                    override.subject_id != self.subject_id
                    or override.subject != self.subject
                    or override.declared_effect != self.declared_effect
                ):
                    raise ValueError(
                        "declaration-review override must exactly match its changed row"
                    )
                identity = (
                    override.subject_id,
                    override.subject,
                    override.declared_effect,
                    override.inferred_effect,
                    tuple(override.inferred_sources),
                    tuple(override.corroborating_sources),
                    override.evidence,
                    override.reason,
                    override.manifest_path,
                )
                if identity in identities:
                    raise ValueError(
                        "declaration-review row cannot repeat an acknowledged override"
                    )
                identities.add(identity)
            return self
        if self.acknowledged_overrides:
            raise ValueError(
                "only acknowledged_override declaration-review rows may carry overrides"
            )
        if self.bucket != "evidence_consistent":
            return self
        if self.subject_id is None:
            raise ValueError("evidence_consistent declaration-review row requires subject_id")
        proposals = declaration_effects(self.declared_effect, self.declared_risk_tags)
        if not proposals:
            raise ValueError(
                "evidence_consistent declaration-review row requires an effect-bearing proposal"
            )
        observed = [reading for reading in self.observed_readings if reading.observed]
        if not observed:
            raise ValueError(
                "evidence_consistent declaration-review row requires an observed reading"
            )
        if any(not reading.policy_eligible for reading in observed):
            raise ValueError(
                "evidence_consistent declaration-review row requires policy-eligible readings"
            )
        uncovered = [
            reading.effect
            for reading in observed
            if not any(declaration_covers(proposal, reading.effect) for proposal in proposals)
        ]
        if uncovered:
            raise ValueError(
                "evidence_consistent declaration-review row has uncovered readings: "
                + ", ".join(sorted(set(uncovered)))
            )
        return self


class DeclarationReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_consistent: int = 0
    unverified: int = 0
    acknowledged_override: int = 0


class DeclarationReviewDecision(BaseModel):
    """Base-vs-head review surface for changed action declaration rows.

    This projection never gates. ``base_comparison_requested=true`` with
    ``enabled=false`` means a comparison was requested but no trustworthy
    declaration-row base was available; renderers must say so.
    ``changed_count=0`` with ``enabled=true`` means the base was comparable but
    no declaration answer changed.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {
                "properties": {"enabled": {"const": True}},
                "required": ["enabled"],
            },
            "then": {
                "properties": {"base_kind": {"enum": ["report", "absent_manifest"]}},
                "required": ["base_kind"],
            },
            "else": {
                "properties": {
                    "base_kind": {"const": "none"},
                    "changed_count": {"const": 0},
                    "rows": {"maxItems": 0},
                },
                "required": ["base_kind", "changed_count", "rows"],
            },
        },
    )

    enabled: bool = False
    base_comparison_requested: bool = False
    base_kind: Literal["none", "report", "absent_manifest"] = "none"
    changed_count: int = 0
    summary: DeclarationReviewSummary = Field(default_factory=DeclarationReviewSummary)
    rows: list[DeclarationReviewRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def counts_match_rows(self) -> DeclarationReviewDecision:
        counts = {
            "evidence_consistent": self.summary.evidence_consistent,
            "unverified": self.summary.unverified,
            "acknowledged_override": self.summary.acknowledged_override,
        }
        actual = {key: 0 for key in counts}
        row_ids: set[str] = set()
        for row in self.rows:
            if row.row_id in row_ids:
                raise ValueError("declaration_review row_ids must be unique")
            row_ids.add(row.row_id)
            actual[row.bucket] += 1
        if self.changed_count != len(self.rows):
            raise ValueError("declaration_review.changed_count must equal len(rows)")
        if counts != actual:
            raise ValueError("declaration_review.summary must count rows by bucket")
        if self.enabled and self.base_kind == "none":
            raise ValueError(
                "enabled declaration_review requires report or absent_manifest base_kind"
            )
        if not self.enabled and self.base_kind != "none":
            raise ValueError("disabled declaration_review requires base_kind='none'")
        if not self.enabled and (self.rows or self.changed_count):
            raise ValueError("disabled declaration_review cannot carry changed rows")
        return self


class DeclarationQuestionRow(BaseModel):
    """v0.37: one open declaration question, in the order to answer it.

    Ordered by how much answering can move the verdict, rather than by what
    sorts first alphabetically. Answering the two actions that moved money and
    communicated outward is what reached a verdict on the fourth
    ``adk-samples#1745`` walk. Ordering is ranking only — it decides what to
    read first and can never change a verdict.

    v0.38: a question is one blank a reviewer fills, so ``answer_path`` — the
    manifest block that blank lives in — is its identity. Actions answered by
    the same block are one question: a source of 117 actions with no authority
    evidence owes one ``tool_sources[].authority`` block, and counting that as
    117 questions describes one edit as a backlog (#410 increment 3).

    v0.38 also ranks by the **ceiling** of what an answer can establish rather
    than by the effect already inferred. The actions nothing has bounded lead —
    no effect evidence at all, a protocol default standing in for its absence,
    or only a heuristic reading this scan may not act on — because an unbounded
    action is unmeasured rather than safe, and its answer can still turn out to
    be anything. Actions whose effect a reviewed declaration or policy-eligible
    source evidence established keep their evidence rank behind them, strongest
    first. Position is not severity: the action at the top is the one *least*
    is known about.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    # Joins to ``EvidenceGap.subject_id`` *within one* ``subject_kind``.
    # ``subject`` is a display label and two catalog ids can render the same
    # one, so anything matching a question to its gap row joins on the id.
    subject_id: str | None = None
    subject_kind: Literal["action", "tool_source"] = "action"
    # The manifest block this question is answered in — an
    # ``action_surface.actions`` row, or the ``tool_sources`` entry whose
    # ``authority`` every action of that source inherits. Machine-readable
    # route and question identity in one, derived once beside the evidence-gap
    # row's own ``next_action.path`` so the two cannot name different blocks.
    answer_path: str = ""
    dimension: str
    # v0.41: who may write the first draft of this answer — the fold of the
    # same tag on every evidence-gap row this question absorbs. Open wins, the
    # way ``answered`` does: a block whose effect the scan can propose but
    # whose authority it cannot is a question a human still has to finish, and
    # a counter that said otherwise would name a finish line no agent reaches.
    authorable_by: Literal["coding_agent", "human"] = "human"


class DeclarationQuestionCoverage(BaseModel):
    """v0.37: how much of the per-action declaration work is done.

    A gap count names a symptom and has no finish line. This is the same facts
    as a task with an end: how many questions this repository was ever asked,
    how many a reviewed declaration has answered, and how many remain.

    The denominator counts only what both halves can be measured on — the
    ``effect`` and ``authority`` a reviewed declaration answers. An action
    whose effect the scan established by itself was never asked about and is
    not counted; an inventory or an ``agent_bindings`` declaration is a human
    answer too but has no counterfactual to score against, so it is excluded
    rather than guessed at. See
    ``agents_shipgate.core.declaration_questions``.

    v0.38: the unit is one blank a reviewer fills, so actions answered by the
    same manifest block are one question. An authority every action of a source
    shares is answered once in that source's ``tool_sources[].authority``
    block; ``open_questions[].answer_path`` names the block, and
    ``subject_kind`` says which id space ``subject_id`` is in.

    ``total == answered + open`` always. ``open_by_dimension`` sums to
    ``open``: a question belongs to exactly one dimension.
    """

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    answered: int = 0
    open: int = 0
    open_by_dimension: dict[str, int] = Field(default_factory=dict)
    # The open questions themselves, in answer order. The counts say how far
    # along the work is; this says what the work is, and it is what lets the
    # generated questionnaire number its blocks with the same numbers this
    # block reports.
    open_questions: list[DeclarationQuestionRow] = Field(default_factory=list)


class SemanticCoverageDecision(BaseModel):
    """v0.29 pass eligibility across the normalized action surface.

    Unlike extraction-confidence thresholds, semantic gaps are
    zero-tolerance: any non-pass-eligible unknown/partial/conflicting
    dimension prevents ``passed``. Known review concerns are counted
    separately so they deterministically route to ``review_required``
    rather than ``insufficient_evidence``: ambient or unscoped
    credentials, and (v0.36+) a declared effect that a reviewer
    acknowledged as weaker than the evidence inferred for it.
    ``reason_counts`` names which.
    """

    total_actions: int = 0
    pass_eligible_actions: int = 0
    gap_count: int = 0
    review_concern_count: int = 0
    reason_counts: dict[str, int] = Field(default_factory=dict)
    # v0.36: one row per acknowledged effect override, in emission order.
    # ``reason_counts["acknowledged_effect_override"]`` counts them; this is
    # what a reviewer actually has to read.
    acknowledged_overrides: list[AcknowledgedEffectOverride] = Field(default_factory=list)
    # Base-vs-head action declaration attestation.  Only changed declaration
    # rows appear; the renderer names the two human-attention buckets and
    # deliberately keeps evidence-consistent row names out of prose.
    declaration_review: DeclarationReviewDecision = Field(
        default_factory=DeclarationReviewDecision
    )
    # v0.37: the same action surface counted as a questionnaire rather than as
    # a pile of gaps. Purely a projection — nothing here gates.
    declaration_questions: DeclarationQuestionCoverage = Field(
        default_factory=DeclarationQuestionCoverage
    )


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


def without_machine_patches(coverage: EvidenceCoverageDecision) -> EvidenceCoverageDecision:
    """A copy of ``coverage`` carrying no machine-applicable patch on any row.

    Exactly one artifact is meant to carry a ``declare_action`` patch: the
    ``report.json`` an agent points ``apply-patches`` at. Everything else that
    embeds the same coverage block is evidence *about* a run — a reviewer
    packet, a cached base scan — and a patch on those rows is wrong twice
    over. It is unexecutable (a base report describes a commit nobody is
    editing), and it carries an absolute ``target_file`` that, for a run
    against an archived checkout, names a temporary directory which will not
    exist when anyone reads it. Two identical runs then differ only by that
    directory name, which is enough to move a packet digest and the receipt's
    artifact set with it.

    Returns a copy on purpose. Stripping in place would take the patch off the
    report the route depends on, one caller away from the artifact it meant to
    clean.
    """

    cleaned = coverage.model_copy(deep=True)
    for gap in cleaned.evidence_gaps:
        if gap.next_action.patch is None:
            continue
        gap.next_action.patch = None
        gap.next_action.suggested_patch_kind = "manual"
    return cleaned


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
    # v0.36: acknowledged effect overrides, as reviewable rows.
    # v0.37: the declaration questionnaire — pre-filled effect proposals, the
    # readings behind them, and a progress counter.
    # v0.38: per-source authority. A declaration question is identified by the
    # manifest block that answers it (``answer_path``), so the actions one
    # ``tool_sources[].authority`` block covers are one question and one
    # evidence-gap row rather than N of each (#410 increment 3).
    report_schema_version: str = "0.43"
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
    # Parsed manifest declaration rows.  ``action_surface_facts`` describes
    # resolved capabilities and intentionally cannot distinguish adding a row
    # from changing an answer in one; this snapshot preserves that PR-review
    # distinction while joining evidence only through canonical subject ids.
    action_declaration_facts: ActionDeclarationFacts = Field(
        default_factory=ActionDeclarationFacts
    )
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
