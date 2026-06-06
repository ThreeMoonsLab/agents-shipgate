"""Verifier-cycle capability-change domain types (report schema v0.23).

These are the additive v0.22 report blocks for the AI coding workflow
verifier (see docs/engineering/ai-coding-workflow-verifier.md §7, §8):

- ``CapabilityChange`` / ``CapabilityChangeBlock`` — the diff-derived
  capability delta, grouped into added / removed / broadened / narrowed
  member lists. A reviewer-facing *projection* over the existing
  ``action_surface_diff`` / ``tool_surface_diff`` facts; it never gates
  on its own (Principle 1/2: legibility layer, one decision engine).
  v0.23 adds semantic metadata fields to each member while preserving
  the existing buckets.
- ``ProtectedSurfaceChange`` / list — touched protected paths/policies.
- ``EffectivePolicy`` — a normalized policy snapshot block.
- ``HumanAck`` — declared human-acknowledgement state.
- ``VerifierSummary`` — a composition alias over ``release_decision``,
  the reviewer/agent summaries, the capability delta, and trigger
  context. ``verdict`` always mirrors ``release_decision.decision``
  (Principle 2 — it cannot derive an independent verdict).

The report builders populate these blocks as deterministic projections
from already-computed scan facts, findings, manifest policy, and declared
human acknowledgement entries. Empty/default shapes are still emitted
when the corresponding input surface has no evidence (for example, a
plain scan with no base diff), so consumers can rely on stable keys.

Determinism contract: every list these models hold is emitted in sorted
order via the ``sorted_*`` helpers / model validators below, and every
model uses ``ConfigDict(extra="forbid")`` so the wire shape is closed —
the same pattern as ``AgentSummary`` / ``ReviewerSummary``. No
wall-clock, no randomness, no set-iteration order leaks into output.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.capability_semantics import (
    CapabilityHashName,
    CapabilitySemanticChange,
    CapabilitySemanticDirection,
)
from agents_shipgate.schemas.common import Confidence, ReleaseDecisionStatus

# --- Capability change -------------------------------------------------------

# How a single capability member moved between base and head. ``added`` /
# ``removed`` are membership changes; ``broadened`` / ``narrowed`` are
# scope-direction changes on a member that exists on both sides (e.g. a
# tool whose auth scope widened or narrowed). Kept as a closed Literal so
# the wire enum is stable; consumers fall back on unknown future values.
CapabilityChangeDirection = Literal[
    "added",
    "removed",
    "broadened",
    "narrowed",
]

# What kind of thing the capability member identifies. Mirrors the
# ``subject_kind`` vocabulary in the roadmap's CapabilityChange model so a
# downstream consumer can switch on the same closed set across blocks.
CapabilitySubjectKind = Literal[
    "tool",
    "action",
    "scope",
    "policy",
    "ci",
    "baseline",
    "agent_instruction",
    "manifest",
    "unknown",
]

# Reviewer-facing release impact of a capability change. Same closed set
# as the roadmap CapabilityChange.release_impact. This is descriptive
# only — it never gates; ``release_decision.decision`` is the gate.
CapabilityReleaseImpact = Literal[
    "none",
    "informational",
    "review_required",
    "blocks_release",
    "insufficient_evidence",
]


class CapabilityChangeMember(BaseModel):
    """One capability member in a base→head delta.

    Carries enough to identify the capability — ``tool`` plus the
    ``action`` and ``scope`` it concerns — and, for ``broadened`` /
    ``narrowed`` directions, the ``before_scope`` / ``after_scope`` so a
    reviewer can see exactly how the scope moved. Membership changes
    (``added`` / ``removed``) leave the before/after scope fields ``None``.

    A deterministic ``id`` supplied by the capability-change builder keeps
    the member stable across runs for byte-equivalent output.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    direction: CapabilityChangeDirection
    subject_kind: CapabilitySubjectKind = "unknown"
    # Capability identity. ``tool`` is the owning tool; ``action`` is the
    # operation on it; ``scope`` is the auth/permission scope the change
    # concerns. All three are plain strings so the member is fully
    # self-describing in the report without a side lookup.
    tool: str
    action: str | None = None
    scope: str | None = None
    # Populated only for ``broadened`` / ``narrowed`` members — the auth
    # scope (or scope set, rendered as a sorted string) on each side.
    # ``None`` for ``added`` / ``removed`` membership changes.
    before_scope: str | None = None
    after_scope: str | None = None
    before_capability_id: str | None = None
    after_capability_id: str | None = None
    changed_hashes: list[CapabilityHashName] = Field(default_factory=list)
    semantic_direction: CapabilitySemanticDirection = "unknown"
    semantic_changes: list[CapabilitySemanticChange] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    release_impact: CapabilityReleaseImpact = "informational"
    provenance_kind: str | None = None
    confidence: Confidence = "medium"
    rationale: str = ""
    related_finding_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_lists(self) -> CapabilityChangeMember:
        # Deterministic ordering — sort the free lists so the same logical
        # member serializes byte-identically regardless of build order.
        self.risk_tags = sorted(self.risk_tags)
        self.changed_hashes = sorted(set(self.changed_hashes))
        self.semantic_changes = sorted(
            self.semantic_changes,
            key=lambda change: (
                change.kind,
                change.field,
                json.dumps(change.before, sort_keys=True, default=str),
                json.dumps(change.after, sort_keys=True, default=str),
            ),
        )
        self.related_finding_ids = sorted(self.related_finding_ids)
        return self


def _sorted_members(
    members: list[CapabilityChangeMember],
) -> list[CapabilityChangeMember]:
    """Stable sort key for a capability-member list.

    Orders by the natural identity tuple so two builds that produce the
    same members emit them in the same order. ``id`` is last as the
    deterministic tie-break.
    """
    return sorted(
        members,
        key=lambda m: (
            m.subject_kind,
            m.tool,
            m.action or "",
            m.scope or "",
            m.id,
        ),
    )


class CapabilityChangeBlock(BaseModel):
    """The diff-derived capability delta, grouped by direction.

    Reviewer-facing projection over ``action_surface_diff`` /
    ``tool_surface_diff`` (roadmap §7.1). Four member lists —
    ``added`` / ``removed`` / ``broadened`` / ``narrowed`` — plus
    ``enabled`` (mirrors the surface-diff enabled flag: ``False`` when no
    base is available, so the block is a stable empty shape rather than
    absent). Never gates on its own.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    added: list[CapabilityChangeMember] = Field(default_factory=list)
    removed: list[CapabilityChangeMember] = Field(default_factory=list)
    broadened: list[CapabilityChangeMember] = Field(default_factory=list)
    narrowed: list[CapabilityChangeMember] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_lists(self) -> CapabilityChangeBlock:
        self.added = _sorted_members(self.added)
        self.removed = _sorted_members(self.removed)
        self.broadened = _sorted_members(self.broadened)
        self.narrowed = _sorted_members(self.narrowed)
        return self


# --- Protected surface changes ----------------------------------------------


class ProtectedSurfaceChange(BaseModel):
    """One touched protected path / policy surface (trust root).

    Tier A trust-root protection (roadmap §5.1/§5.2) records *which*
    protected surface a PR touched. ``path`` is the changed file; ``kind``
    is the classifier bucket it matched (e.g. ``manifest``, ``ci_gate``,
    ``policy``); ``glob`` is the matched pattern. ``related_finding_ids``
    links to the ordinary ``SHIP-VERIFY-*`` findings that gate.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str = "unknown"
    glob: str | None = None
    related_finding_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_lists(self) -> ProtectedSurfaceChange:
        self.related_finding_ids = sorted(self.related_finding_ids)
        return self


# --- Effective policy snapshot ----------------------------------------------


# Severity tier rank — shared by the snapshot's ``fail_on`` ordering and
# the weakening classifier's "lowered across a tier" comparison. Higher
# rank == stronger gate.
SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def _sorted_severities(values: list[str]) -> list[str]:
    """Sort a severity set by tier rank, then name (stable for unknowns)."""
    return sorted(set(values), key=lambda s: (SEVERITY_RANK.get(s, -1), s))


class EffectivePolicy(BaseModel):
    """Normalized effective-policy snapshot block (roadmap §5.3).

    A semantic — not text — view of the release policy surface, so the
    base-vs-head comparator can answer "was the gate weakened?" without a
    text diff. Every field is derived deterministically from the manifest
    (plus, for ``baseline_fingerprints``, the accepted-debt findings), and
    every list/dict is emitted in sorted order so two builds of the same
    manifest serialize byte-identically.

    Fields are chosen so the weakening / expansion classifiers can answer
    the §5.3 questions by set/rank comparison rather than counting:

    - ``ci_mode`` — was the CI gate weakened (strict -> advisory)?
    - ``fail_on`` — was the fail-on severity set loosened (head ⊂ base)?
    - ``severity_overrides`` — was a check's severity lowered across a tier?
    - ``suppressed_check_ids`` — was a blocking check newly suppressed?
    - ``waiver_scopes`` — was a waiver scope expanded (head ⊋ base)?
    - ``baseline_fingerprints`` — was the accepted-debt baseline expanded?
    - ``baseline_integrity_mode`` — was baseline tamper-checking weakened?
    - ``ci_gate_present`` — was Shipgate CI removed from an opted-in repo?
    """

    model_config = ConfigDict(extra="forbid")

    ci_mode: str | None = None
    # Severities that fail CI, sorted by tier rank (info -> critical).
    fail_on: list[str] = Field(default_factory=list)
    # Sorted unique check IDs suppressed via ``checks.ignore``.
    suppressed_check_ids: list[str] = Field(default_factory=list)
    # Sorted ``"check_id:tool"`` waiver scopes (``tool`` is ``*`` when the
    # suppression is tool-agnostic). A superset on head vs base is an
    # expansion.
    waiver_scopes: list[str] = Field(default_factory=list)
    # ``check_id -> applied severity`` from ``checks.severity_overrides``.
    # Key order is normalized (sorted) for byte-stable output.
    severity_overrides: dict[str, str] = Field(default_factory=dict)
    baseline_integrity_mode: str | None = None
    # Sorted accepted-debt baseline fingerprints (findings whose
    # ``baseline_status == "matched"``). Populated on the emitted report;
    # the in-check head snapshot leaves it empty (findings are not yet
    # baseline-matched at check time), so the classifier compares only the
    # manifest-derived fields.
    baseline_fingerprints: list[str] = Field(default_factory=list)
    ci_gate_present: bool = True

    @model_validator(mode="after")
    def _sort(self) -> EffectivePolicy:
        self.fail_on = _sorted_severities(self.fail_on)
        self.suppressed_check_ids = sorted(set(self.suppressed_check_ids))
        self.waiver_scopes = sorted(set(self.waiver_scopes))
        self.severity_overrides = dict(sorted(self.severity_overrides.items()))
        self.baseline_fingerprints = sorted(set(self.baseline_fingerprints))
        return self


# --- Human acknowledgement ---------------------------------------------------


class HumanAckEntry(BaseModel):
    """One declared human-acknowledgement record.

    Within the static boundary acknowledgement can only be *declared*
    evidence (roadmap §5.4) — never inferred. An entry names the human
    ``owner``, the ``reason``, an optional ``expires`` date (copied
    verbatim from the source, never generated), and the
    ``affected_surface`` the ack covers.
    """

    model_config = ConfigDict(extra="forbid")

    owner: str
    reason: str
    affected_surface: str
    expires: str | None = None
    source: str | None = None


class HumanAck(BaseModel):
    """Human-acknowledgement state block (Decision 4 shape).

    ``required`` is set by the human-ack builder when a trust-root
    weakening finding needs declared human authority; ``satisfied`` is
    ``True`` when every required acknowledgement is present. ``acks``
    lists the declared entries; ``outstanding`` lists the surfaces still
    needing one. The default empty shape is not required and therefore
    satisfied.
    """

    model_config = ConfigDict(extra="forbid")

    required: bool = False
    satisfied: bool = True
    acks: list[HumanAckEntry] = Field(default_factory=list)
    outstanding: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_lists(self) -> HumanAck:
        self.acks = sorted(
            self.acks,
            key=lambda a: (a.affected_surface, a.owner, a.reason),
        )
        self.outstanding = sorted(self.outstanding)
        return self


# --- Verifier summary --------------------------------------------------------

# Back-compat alias for the canonical verdict vocabulary. ``VerifierSummary.verdict``
# MUST mirror ``release_decision.decision`` (Principle 2), so it reuses the exact
# same ``ReleaseDecisionStatus`` enum rather than re-spelling it. Kept as a named
# alias because callers and tests import ``VerifierVerdict`` directly.
VerifierVerdict = ReleaseDecisionStatus


class VerifierCapabilityDeltaSummary(BaseModel):
    """Counts of the capability delta, by direction.

    Equal by construction to the lengths of the matching
    ``CapabilityChangeBlock`` member lists (roadmap §8 required test:
    reviewer/verifier capability counts equal the underlying rollup).
    """

    model_config = ConfigDict(extra="forbid")

    added: int = 0
    removed: int = 0
    broadened: int = 0
    narrowed: int = 0


class VerifierReasonCodeCount(BaseModel):
    """One ``(reason_code, count)`` pair for the verifier summary.

    A list of these (rather than a dict) keeps the ordering explicit and
    byte-stable; the builder sorts them deterministically.
    """

    model_config = ConfigDict(extra="forbid")

    reason_code: str
    count: int = 0


class VerifierSummary(BaseModel):
    """Top-level verifier composition alias (Decision 5 shape).

    A byte-stable projection bundling the release verdict, finding counts,
    the capability delta summary, and trust-root / acknowledgement flags
    for one-fetch controller consumption. Composition only — it cannot
    introduce a finding-independent blocker, and ``verdict`` MUST mirror
    ``release_decision.decision`` (Principle 2; enforced by a property
    test). Parallels ``agent_summary`` / ``reviewer_summary``.

    ``top_reason_codes`` is capped at the top five by the builder, ranked
    severity desc, count desc, then reason code asc.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: VerifierVerdict
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_reason_code: dict[str, int] = Field(default_factory=dict)
    capability_delta_summary: VerifierCapabilityDeltaSummary = Field(
        default_factory=VerifierCapabilityDeltaSummary
    )
    protected_surface_touched: bool = False
    policy_weakened: bool = False
    human_ack_required: bool = False
    human_ack_satisfied: bool = True
    top_reason_codes: list[VerifierReasonCodeCount] = Field(default_factory=list)
