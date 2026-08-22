from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.core.heuristics import is_broad_scope
from agents_shipgate.schemas.common import Confidence, ProvenanceKind, parse_confidence
from agents_shipgate.schemas.surfaces import ActionEffect

SemanticDimension = Literal["identity", "binding", "effect", "authority"]
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
POLICY_ELIGIBLE_EVIDENCE_BASES = frozenset(
    {
        "reviewed_declaration",
        "protocol_structure",
        "typed_provider_fact",
        "structural_scope",
    }
)


def provenance_for_evidence_basis(
    basis: EvidenceBasis,
    fallback: ProvenanceKind = "static_declaration",
) -> ProvenanceKind:
    if basis == "inferred_keyword":
        return "keyword_heuristic"
    if basis == "inferred_regex":
        return "regex_heuristic"
    if basis == "typed_provider_fact":
        return "ast_extraction"
    if basis in POLICY_ELIGIBLE_EVIDENCE_BASES or basis == "protocol_default":
        return "static_declaration"
    return fallback
IdentityEvidenceStatus = Literal[
    "declared",
    "structural",
    "partial",
    "unknown",
    "conflicting",
]
EffectEvidenceStatus = Literal[
    "declared",
    "structural",
    "protocol_default",
    "inferred",
    "unknown",
    "conflicting",
]
AuthorityEvidenceStatus = Literal[
    "declared",
    "structural",
    "partial",
    "unknown",
    "conflicting",
]
AuthorityMode = Literal["none", "scoped", "unscoped", "ambient", "unknown"]
SemanticIssueKind = Literal[
    "incomplete_tool_identity",
    "conflicting_tool_identity",
    "unresolved_tool_selector",
    "ambiguous_tool_selector",
    "ambiguous_legacy_tool_identity",
    "invalid_tool_binding",
    "incomplete_surface",
    "missing_effect_evidence",
    "inferred_effect_only",
    "conflicting_effect_evidence",
    "missing_authority_evidence",
    "partial_authority_evidence",
    "conflicting_authority_evidence",
    "invalid_semantic_annotation",
    "missing_binding_evidence",
    "partial_binding_evidence",
    "conflicting_binding_evidence",
    "ambiguous_root_agent",
    "unresolved_agent_binding",
    "unresolved_bound_tool",
    "incomplete_handoff_graph",
    "invalid_binding_annotation",
    "invalid_evidence_provenance",
]


class SemanticClaim(BaseModel):
    """One deterministic piece of evidence used by semantic resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str | None = None
    dimension: SemanticDimension
    value: str
    confidence: Confidence
    provenance_kind: ProvenanceKind
    basis: EvidenceBasis = "unknown"
    policy_eligible: bool = False
    source: str
    source_pointer: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def derive_evidence_contract(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        basis = payload.get("basis", "unknown")
        confidence = payload.get("confidence", "low")
        payload["provenance_kind"] = provenance_for_evidence_basis(
            basis,
            payload.get("provenance_kind", "static_declaration"),
        )
        eligible = basis in POLICY_ELIGIBLE_EVIDENCE_BASES and confidence == "high"
        payload["policy_eligible"] = eligible
        if not payload.get("claim_id"):
            identity = {
                "dimension": payload.get("dimension"),
                "value": payload.get("value"),
                "confidence": confidence,
                "basis": basis,
                "source": payload.get("source"),
                "source_pointer": payload.get("source_pointer"),
                "evidence": payload.get("evidence") or {},
            }
            rendered = json.dumps(
                identity,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            payload["claim_id"] = "clm_" + hashlib.sha256(rendered.encode()).hexdigest()[:20]
        return payload


class SemanticIssue(BaseModel):
    """An unsatisfied evidence condition that prevents an evidence-backed pass."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SemanticIssueKind
    dimension: SemanticDimension
    message: str
    source: str | None = None
    source_pointer: str | None = None


class EffectSemanticAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: EffectEvidenceStatus
    confidence: Confidence
    claims: list[SemanticClaim] = Field(default_factory=list)
    issues: list[SemanticIssue] = Field(default_factory=list)


class AuthoritySemanticAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AuthorityEvidenceStatus
    mode: AuthorityMode
    auth_type: str | None = None
    credential_mode: str | None = None
    scopes: list[str] = Field(default_factory=list)
    claims: list[SemanticClaim] = Field(default_factory=list)
    issues: list[SemanticIssue] = Field(default_factory=list)


class ToolIdentityAssessment(BaseModel):
    """Canonical identity and provenance for one capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str
    status: IdentityEvidenceStatus
    provider: str
    binding_id: str | None = None
    primary_observation_id: str
    observation_ids: list[str] = Field(default_factory=list)
    claims: list[SemanticClaim] = Field(default_factory=list)
    issues: list[SemanticIssue] = Field(default_factory=list)
    pass_eligible: bool


class BindingSemanticAssessment(BaseModel):
    """Static proof that a tool is reachable from the configured root agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["declared", "structural", "partial", "unknown", "conflicting"]
    confidence: Confidence
    root_agent_id: str | None = None
    reachable_path: list[str] = Field(default_factory=list)
    claims: list[SemanticClaim] = Field(default_factory=list)
    issues: list[SemanticIssue] = Field(default_factory=list)
    pass_eligible: bool = False


def _legacy_direct_binding() -> BindingSemanticAssessment:
    return BindingSemanticAssessment(
        status="structural",
        confidence="high",
        root_agent_id="legacy_direct",
        reachable_path=["legacy_direct"],
        pass_eligible=True,
    )


def _legacy_direct_identity() -> ToolIdentityAssessment:
    return ToolIdentityAssessment(
        tool_id="legacy_direct",
        status="structural",
        provider="legacy_direct",
        primary_observation_id="legacy_direct",
        observation_ids=["legacy_direct"],
        pass_eligible=True,
    )


class ToolSemanticAssessment(BaseModel):
    """Pass-eligibility result for one statically extracted tool/action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conservative_effect: ActionEffect
    identity: ToolIdentityAssessment = Field(default_factory=_legacy_direct_identity)
    binding: BindingSemanticAssessment = Field(default_factory=_legacy_direct_binding)
    effect: EffectSemanticAssessment
    authority: AuthoritySemanticAssessment
    pass_eligible: bool


class AuthSchemeRequirement(BaseModel):
    """One OpenAPI security-scheme requirement inside an OR alternative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    type: str | None = None
    scopes: list[str] = Field(default_factory=list)


class AuthAlternative(BaseModel):
    """One OpenAPI security requirement object (OR across alternatives)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemes: list[AuthSchemeRequirement] = Field(default_factory=list)
    anonymous: bool = False


class AuthInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    scopes: list[str] = Field(default_factory=list)
    credential_mode: str | None = None
    source: str | None = None
    mode: AuthorityMode = "unknown"
    explicit: bool = False
    inherited: bool = False
    alternatives: list[AuthAlternative] = Field(default_factory=list)
    # Source adapters preserve malformed annotations here so the semantic
    # resolver can fail closed without losing the rest of the tool inventory.
    invalid_annotations: list[str] = Field(default_factory=list)


#: ``Tool.extraction["surface"]`` — an adapter's answer to "did you read the
#: whole tool surface, or only report what you could see?".
#:
#: Only an adapter writes ``extraction``; no loader copies it out of a parsed
#: input file, so unlike ``annotations`` this claim cannot be self-declared by
#: an untrusted artifact. Absence of the key is *not* ``SURFACE_PARTIAL`` with
#: extra steps — it means the adapter has no answer, which every consumer must
#: treat as incomplete. Defined here so the producers (``inputs/*``) and the
#: consumer (``core.semantic_assessment``) share one spelling rather than two
#: string literals that have to agree (#393).
SURFACE_ENUMERATED = "enumerated"
SURFACE_PARTIAL = "partial"


class ToolParameter(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    type: str | None = None
    required: bool = False
    description: str | None = None
    enum: list[Any] | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    format: str | None = None
    default: Any = None
    risk_hints: list[str] = Field(default_factory=list)


class ToolRiskHint(BaseModel):
    model_config = ConfigDict(extra="allow")

    tag: str
    source: str
    confidence: Confidence
    basis: EvidenceBasis = "unknown"
    provenance_kind: ProvenanceKind = "static_declaration"
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def bind_provenance_to_basis(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        basis = payload.get("basis", "unknown")
        payload["provenance_kind"] = provenance_for_evidence_basis(
            basis,
            payload.get("provenance_kind", "static_declaration"),
        )
        return payload


class Tool(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    provider: str | None = None
    native_locator: str | None = None
    observation_id: str | None = None
    observation_ids: list[str] = Field(default_factory=list)
    description: str | None = None
    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    source_location: str | None = None
    source_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_column: int | None = None
    source_pointer: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    parameters: list[ToolParameter] = Field(default_factory=list)
    function_signature: str | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)
    auth: AuthInfo = Field(default_factory=AuthInfo)
    risk_hints: list[ToolRiskHint] = Field(default_factory=list)
    owner: str | None = None
    extraction_confidence: Confidence = "low"
    extraction: dict[str, Any] = Field(default_factory=dict)
    # Per-field donor provenance for evidence a reviewed binding preserved from
    # a *member* observation rather than the primary one. Backfilling keeps the
    # evidence but the row keeps the primary's locators, so a finding raised on
    # a backfilled field would cite an artifact that does not contain it — the
    # restored free-form-output finding pointed at an inventory JSON with no
    # output schema at all (#386 review). Keyed by Tool field name
    # (``output_schema``, ``auth.source``, …); values are ``SourceReference``
    # kwargs for the observation that actually supplied the value.
    #
    # In-memory only, like the assessments below: report schemas expose
    # provenance through their own versioned models, and ``tool_finding``
    # consumes this to point a finding at its real source.
    evidence_provenance: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        exclude=True,
    )
    # In-memory semantic resolution. Excluded from generic Tool dumps because
    # report schemas expose this evidence through their own versioned models.
    semantic_assessment: ToolSemanticAssessment | None = Field(
        default=None,
        exclude=True,
    )
    identity_assessment: ToolIdentityAssessment | None = Field(
        default=None,
        exclude=True,
    )
    binding_assessment: BindingSemanticAssessment | None = Field(
        default=None,
        exclude=True,
    )
    resolved_controls: list[str] = Field(default_factory=list, exclude=True)

    @model_validator(mode="after")
    def normalize_extraction_confidence(self) -> Tool:
        raw_confidence = self.extraction.get("confidence")
        if isinstance(raw_confidence, str) and raw_confidence in get_args(Confidence):
            self.extraction_confidence = parse_confidence(raw_confidence)
        else:
            self.extraction["confidence"] = self.extraction_confidence
        return self


class Agent(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    source: dict[str, Any] = Field(default_factory=dict)
    instructions: dict[str, Any] = Field(default_factory=dict)
    declared_purpose: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    handoffs: list[str] = Field(default_factory=list)
    guardrails: dict[str, Any] = Field(default_factory=dict)
    extraction: dict[str, Any] = Field(default_factory=dict)


class ToolkitScopeBound(BaseModel):
    """Statically-parsed least-privilege bound on a dynamically-loaded agent toolkit.

    Some agent toolkits (e.g. ``stripe_agent_toolkit``) expose their tools
    through a runtime factory (``*toolkit.get_tools()``) that the static
    extractor cannot enumerate. What IS statically parseable is the
    *configuration* passed to the toolkit constructor — an explicit
    allowlist of ``resource:verb`` permissions. Capturing that bound lets
    the verifier diff it base-vs-head and catch a silent broadening (the
    allowlist removed or widened) even though the individual tools stay
    opaque.

    ``bounded`` is ``True`` when the constructor declared an explicit
    permission allowlist; ``scopes`` is that allowlist (sorted, normalized
    to ``resource:verb``). ``bounded=False`` means no configuration was
    supplied, so the full toolkit surface is mounted (the broadest state) —
    ``scopes`` is empty in that case and is NOT a least-privilege grant.
    """

    model_config = ConfigDict(extra="allow")

    provider: str
    constructor: str
    bounded: bool
    scopes: list[str] = Field(default_factory=list)
    binding: str | None = None
    source_ref: str | None = None
    source_line: int | None = None
    # Config-bound capability detection
    # (docs/engineering/config-bound-capability-detection.md): how the
    # constructor's authority-bearing argument was bound.
    #
    # - ``"literal"`` — parsed to an explicit allowlist (``scopes`` above).
    # - ``"absent"``  — no authority argument; the framework defaults mount.
    # - ``"config"``  — statically traceable to a config read (json/yaml/toml
    #   load, ``os.environ``, pydantic settings); the effective surface is
    #   configuration-defined and NOT statically enumerable.
    # - ``"unknown"`` — present but not statically readable. Never fires the
    #   removal check (the design doc's false-positive guard).
    # - ``None``      — legacy fact (pre-feature report round-trip); treated
    #   per ``bounded`` by the decoders.
    config_binding: Literal["literal", "config", "absent", "unknown"] | None = None
    # Repo-relative path of the config file feeding a ``"config"`` binding
    # when the read named a literal path; ``None`` for env/settings reads.
    config_path: str | None = None


class AgentBindingObservation(BaseModel):
    """One framework parser's normalized, agent-level binding observation."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    source_id: str | None = None
    source: str
    source_pointer: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    handoff_names: list[str] = Field(default_factory=list)
    tools_complete: bool = True
    handoffs_complete: bool = True
    issues: list[str] = Field(default_factory=list)


class SourceSurfaceOmission(BaseModel):
    """A subject an adapter read and could not carry into the catalog.

    The typed counterpart to a warning string. Adapters already *say* when
    they drop an entry — ``Skipping non-object MCP tool entry`` — but only in
    prose, and prose cannot be told apart from the many warnings that report
    degraded confidence about a tool that *did* enter the catalog. The
    exclusion ledger needs the difference: it once mapped every warning to
    "part of that input never entered the catalog", which was false of most of
    them (PR #404 review).

    ``warning`` is the exact text the adapter appended alongside, so the row
    joins to the ``source_warning`` evidence gap that carries it. Populate this
    only where an entry is genuinely dropped.
    """

    model_config = ConfigDict(extra="forbid")

    #: What was omitted, in a form that names it without quoting file content
    #: (``tools[3]``). The reader locates it through ``source_id`` + this.
    subject: str
    #: Stable token for the kind of omission.
    reason: str
    #: One sentence a reviewer can act on.
    detail: str
    #: The source warning reporting this omission — the gap subject it joins to.
    warning: str


class LoadedToolSource(BaseModel):
    source_id: str
    source_type: str
    tools: list[Tool] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Entries this source dropped. Empty for every adapter that has not been
    # taught to record them, which is why the exclusion ledger reports what it
    # can prove rather than guessing from `warnings`.
    omissions: list[SourceSurfaceOmission] = Field(default_factory=list)
    # Statically-parsed least-privilege bounds on dynamically-loaded
    # toolkits found in this source (e.g. ``stripe_agent_toolkit``). Empty
    # for sources that declare no recognized agent-toolkit constructor.
    toolkit_bounds: list[ToolkitScopeBound] = Field(default_factory=list)
    # Framework-owned observations about Agent(...) wiring. These are kept
    # separate from Tool.annotations so catalog-controlled metadata can never
    # become authority-bearing binding evidence.
    binding_observations: list[AgentBindingObservation] = Field(default_factory=list)
    # For a reviewed tool inventory declared with
    # ``<framework>.tool_inventories[].source_id``: the id of the source whose
    # surface this file enumerates. ``build_tool_identity_catalog`` turns it
    # into reviewed identity bindings, so an inventory *completes* the source
    # that raised ``incomplete_surface`` instead of shadowing it with
    # same-named duplicates (#386). ``None`` for every other source, including
    # an inventory declared without the field.
    completes_source_id: str | None = None
    # True for a source loaded from a ``<framework>.tool_inventories`` entry.
    # Kept separate from ``completes_source_id`` because the *unbound* case is
    # the one that needs naming: an inventory that completes nothing while
    # duplicating names the extracted sources already produced is the exact
    # degraded shape #386 reported, and it has to be distinguishable from an
    # ordinary source to be reported at all.
    is_tool_inventory: bool = False


# ---------------------------------------------------------------------------
# Typed Action / Scope / SideEffect domain types.
#
# These are *in-memory* representations used by the action-surface and
# risk-hint machinery. The canonical wire shapes remain:
#
# - ``schemas.surfaces.ActionFact`` for action snapshots (extra="forbid").
# - ``AuthInfo.scopes: list[str]`` and ``ActionFact.required_scopes:
#   list[str]`` for scope serialization.
# - ``ActionFact.effect: ActionEffect`` + ``risk_tags: list[str]`` for
#   side-effect serialization.
#
# Adding these types is purely additive — no field on any wire model
# changes, and ``report_schema_version`` does not bump.
# ---------------------------------------------------------------------------


# Verb tokens recognized when parsing a scope's verb position. Read/write
# subsets drive ``Scope.is_read`` / ``Scope.is_write`` without forcing a
# strict format on user scope strings — unknown verbs leave both as False.
_SCOPE_VERB_TOKENS: frozenset[str] = frozenset(
    {
        "*",
        "admin",
        "all",
        "create",
        "delete",
        "destroy",
        "execute",
        "get",
        "list",
        "manage",
        "read",
        "run",
        "send",
        "update",
        "view",
        "write",
    }
)
_SCOPE_READ_VERBS: frozenset[str] = frozenset({"get", "list", "read", "view"})
_SCOPE_WRITE_VERBS: frozenset[str] = frozenset(
    {
        "admin",
        "create",
        "delete",
        "destroy",
        "execute",
        "manage",
        "run",
        "send",
        "update",
        "write",
    }
)


class Scope(BaseModel):
    """Typed view of a permission scope string.

    The canonical wire form is the original ``raw`` string — this type
    is the in-memory derivation used for matching and least-privilege
    reasoning. The parser is permissive: it never raises, and unknown
    shapes fall back to a single-``provider`` form.

    Supported shapes:

    - ``provider:resource:verb``    — Stripe-, AWS-, K8s-style triples.
    - ``provider:resource``         — GitHub-style ``repo:status``.
    - ``provider.resource.verb``    — OpenAI / dot-separated APIs.
    - ``provider:*``                — broad on resource (``Scope.is_broad`` is True).
    - ``provider:resource:*``       — broad on verb (AWS-style; ``Scope.is_broad`` is True).
    - ``admin``, ``*``              — broad tokens (no structural parts).

    Wildcard slotting is **position-dependent** — the wildcard occupies
    whatever slot it would naturally fill by position. ``is_broad()``
    returns True regardless of which slot it ended up in, and
    ``is_read()`` / ``is_write()`` always return False for a wildcard
    verb because ``"*"`` is not a canonical action verb.
    """

    model_config = ConfigDict(frozen=True)

    raw: str
    provider: str | None = None
    resource: str | None = None
    verb: str | None = None

    @classmethod
    def parse(cls, raw: str) -> Scope:
        """Parse a scope string into typed parts. Never raises."""
        raw_str = (raw or "").strip()
        if not raw_str:
            return cls(raw="")
        normalized = raw_str.lower()
        # Broad single-token short-circuits — keep structural parts empty
        # so least-privilege checks don't try to match by provider.
        if normalized in {"*", "admin"}:
            return cls(raw=raw_str)
        if ":" in raw_str:
            return cls._from_parts(raw_str, raw_str.split(":"))
        if "." in raw_str:
            return cls._from_parts(raw_str, raw_str.split("."))
        if "/" in raw_str:
            return cls._from_parts(raw_str, raw_str.split("/"))
        return cls(raw=raw_str, provider=raw_str)

    @classmethod
    def _from_parts(cls, raw: str, parts: list[str]) -> Scope:
        cleaned = [part.strip() for part in parts if part.strip()]
        if not cleaned:
            return cls(raw=raw)
        if len(cleaned) == 1:
            if cleaned[0] == "*":
                return cls(raw=raw)
            return cls(raw=raw, provider=cleaned[0])
        # Wildcard slotting is position-dependent — the wildcard always
        # occupies the slot that part would naturally occupy by position:
        #
        # - 2-part ``provider:*`` → resource is ``"*"``, verb is ``None``.
        #   2-part scopes have no canonical verb position, so the trailing
        #   token is treated as a resource regardless of value.
        # - 3+-part ``provider:resource:*`` → resource is concrete, verb is
        #   ``"*"``. This matches AWS IAM / GCP-style wildcards where the
        #   action axis is the rightmost position (``s3:bucket:*`` means
        #   "all actions on the bucket").
        #
        # In every case ``is_broad()`` returns True via
        # ``core.heuristics.is_broad_scope`` regardless of which slot the
        # wildcard ended up in, so least-privilege gating is unaffected
        # by the slot choice. ``is_read()``/``is_write()`` always return
        # False for ``verb="*"`` because ``"*"`` is not in the
        # _SCOPE_READ_VERBS / _SCOPE_WRITE_VERBS sets.
        if len(cleaned) == 2:
            if cleaned[-1] == "*":
                return cls(raw=raw, provider=cleaned[0], resource="*")
            tail = cleaned[-1].lower()
            if tail in _SCOPE_VERB_TOKENS:
                return cls(raw=raw, provider=cleaned[0], verb=cleaned[-1])
            return cls(raw=raw, provider=cleaned[0], resource=cleaned[-1])
        # 3+ parts.
        provider = cleaned[0]
        if cleaned[-1] == "*":
            resource = ":".join(cleaned[1:-1]) if len(cleaned) > 2 else None
            return cls(raw=raw, provider=provider, resource=resource, verb="*")
        tail = cleaned[-1].lower()
        if tail in _SCOPE_VERB_TOKENS:
            resource = ":".join(cleaned[1:-1]) if len(cleaned) > 2 else None
            return cls(raw=raw, provider=provider, resource=resource, verb=cleaned[-1])
        return cls(raw=raw, provider=provider, resource=":".join(cleaned[1:]))

    def is_broad(self) -> bool:
        """True for wildcard / admin-equivalent scope strings.

        Delegates to ``core.heuristics.is_broad_scope`` so manifest-level
        broad-scope checks and per-scope checks agree on the rule.
        """
        return is_broad_scope(self.raw)

    def is_write(self) -> bool:
        verb = (self.verb or "").lower()
        return verb in _SCOPE_WRITE_VERBS

    def is_read(self) -> bool:
        verb = (self.verb or "").lower()
        return verb in _SCOPE_READ_VERBS

    def __str__(self) -> str:
        return self.raw


# Effect tiers that always count as high-risk regardless of risk-tag set.
# Mirrors the catalog used by ``core/lenses/action_surface.py`` for
# severity classification AND the legacy ``core.risk_hints.HIGH_RISK_TAGS``
# set (the canonical-mapped versions) so the typed ``SideEffect.is_high_risk``
# never under-reports relative to the legacy ``is_high_risk_tool``
# predicate. The mapping back to legacy tags:
#
# - destructive             ← destructive
# - external_communication  ← external_write / customer_communication
# - financial_write         ← financial_action
# - production_operation    ← infrastructure_change → production_ops
# - code_execution          ← code_execution
# - privileged_data_access  ← sensitive_data_access → privileged_data
# - identity_access         ← (not in legacy HIGH_RISK_TAGS; typed-only
#                              escalation, strictly more conservative)
_HIGH_RISK_EFFECTS: frozenset[str] = frozenset(
    {
        "code_execution",
        "destructive",
        "external_communication",
        "financial_write",
        "identity_access",
        "privileged_data_access",
        "production_operation",
    }
)


class SideEffect(BaseModel):
    """Typed side-effect profile of an Action / Tool.

    Replaces the ``risk_tags: list[str]`` string-bag pattern by capturing
    each independently-derivable side-effect property in its own field.
    The flat ``risk_tags`` list remains the wire representation; this
    type is the in-memory view used by checks and the diff machinery.

    ``effect`` is the canonical effect tier (matches
    ``schemas.surfaces.ActionEffect``). The remaining boolean fields are
    structural facts that are independent of the effect tier — a tool
    can be both ``effect="write"`` and ``handles_sensitive_data=True``.
    """

    model_config = ConfigDict(frozen=True)

    effect: ActionEffect
    externally_visible: bool = False
    handles_sensitive_data: bool = False
    financial: bool = False
    code_execution: bool = False
    reversibility: Literal["reversible", "irreversible", "unknown"] = "unknown"
    idempotency_known: bool | None = None

    @property
    def is_high_risk(self) -> bool:
        """Canonical high-risk classifier for severity / gating.

        Maintains the invariant that
        ``tool_side_effect(tool).is_high_risk`` is True whenever the
        legacy ``core.risk_hints.is_high_risk_tool(tool)`` is True.
        Both ``effect`` and the structural fields contribute so that a
        tool whose declared effect tier is lower than its
        ``handles_sensitive_data`` / ``financial`` / ``code_execution``
        signals still classifies as high-risk.
        """
        if self.effect in _HIGH_RISK_EFFECTS:
            return True
        return self.financial or self.code_execution or self.handles_sensitive_data


class Action(BaseModel):
    """Typed runtime representation of an Action.

    Mirror of ``schemas.surfaces.ActionFact`` with two upgrades:

    - ``scopes: list[Scope]`` instead of ``list[str]`` — typed view for
      least-privilege reasoning and broad-scope detection.
    - ``side_effect: SideEffect`` instead of a string-bag mix of
      ``effect`` + ``risk_tags`` — typed view for severity logic.

    ``risk_tags`` is retained as a parallel ``list[str]`` so the typed
    Action serializes cleanly back to the wire ``ActionFact`` shape via
    ``core.lenses.action_surface.action_to_fact``. Outside the action-
    surface pipeline, code should prefer ``side_effect`` for branching.
    """

    model_config = ConfigDict(extra="forbid")

    action_id: str
    agent_id: str
    tool_id: str
    tool_name: str
    provider: str
    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    source_location: str | None = None
    source_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_column: int | None = None
    source_pointer: str | None = None
    operation: str
    side_effect: SideEffect
    risk_tags: list[str] = Field(default_factory=list)
    scopes: list[Scope] = Field(default_factory=list)
    approval_required: bool | None = None
    approval_threshold: str | None = None
    safeguard_idempotency: bool | None = None
    safeguard_audit_log: bool | None = None
    safeguard_rollback: bool | None = None
    safeguard_dry_run: bool | None = None
    evidence_owner: str | None = None
    evidence_runbook: str | None = None
    evidence_approval_ticket: str | None = None
    input_fields: list[str] = Field(default_factory=list)
    required_input_fields: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    parameters_for_hash: list[dict[str, Any]] = Field(default_factory=list)
    semantic_assessment: ToolSemanticAssessment | None = None

    @property
    def scope_strings(self) -> list[str]:
        """Wire-format projection of ``scopes`` — preserves user input order."""
        return [scope.raw for scope in self.scopes]

    @property
    def has_broad_scope(self) -> bool:
        return any(scope.is_broad() for scope in self.scopes)

    @property
    def effect(self) -> ActionEffect:
        """Convenience alias — matches ``ActionFact.effect``."""
        return self.side_effect.effect
