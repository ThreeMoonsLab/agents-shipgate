"""Pydantic models for the Release Evidence Packet.

These models define the JSON contract for ``packet.json`` (validated
against ``docs/packet-schema.v0.1.json``). Every field is explicit; no
free-form ``dict`` slots leak through except where the underlying scan
data is already a stable dict (e.g. project / agent / environment
copied from ``ReadinessReport``).

The schema version is a real field on ``EvidencePacket`` (not injected
at serialize time) so the generated JSON Schema and the emitted JSON
cannot drift.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.core.models import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    ReleaseDecisionItem,
    ReleaseDecisionStatus,
    Severity,
)

VerdictLabel = Literal["PASSED", "REVIEW REQUIRED", "BLOCKED"]
SectionStatus = Literal["covered", "partial", "not_declared", "missing", "informational"]


class ReleaseDecisionSection(BaseModel):
    """§1 — release decision. Verdict derives from
    ``release_decision.decision`` only; ``fail_policy`` is rendered as
    separate CI behavior metadata, never as the verdict source."""

    model_config = ConfigDict(extra="forbid")

    decision: ReleaseDecisionStatus
    verdict: VerdictLabel
    reason: str
    blockers: list[ReleaseDecisionItem] = Field(default_factory=list)
    review_items: list[ReleaseDecisionItem] = Field(default_factory=list)
    evidence_coverage: EvidenceCoverageDecision
    baseline_delta: BaselineDelta
    fail_policy: FailPolicy


class CapabilityIntentRow(BaseModel):
    """One declared/observed pair in the §2 capability ↔ intent diff."""

    model_config = ConfigDict(extra="forbid")

    label: str
    declared: list[str] = Field(default_factory=list)
    observed: list[str] = Field(default_factory=list)
    divergent: list[str] = Field(default_factory=list)


class CapabilityIntentDiff(BaseModel):
    """§2 — capability ↔ intent diff."""

    model_config = ConfigDict(extra="forbid")

    status: SectionStatus
    declared_purpose: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    observed_tools: list[str] = Field(default_factory=list)
    rows: list[CapabilityIntentRow] = Field(default_factory=list)
    divergence_findings: list[ReleaseDecisionItem] = Field(default_factory=list)


class HighRiskToolEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source_type: str
    risk_tags: list[str] = Field(default_factory=list)
    has_approval_policy: bool = False
    has_idempotency_policy: bool = False


class HighRiskSurfaceSection(BaseModel):
    """§3 — high-risk tool surface."""

    model_config = ConfigDict(extra="forbid")

    status: SectionStatus
    total_tools: int = 0
    high_risk_count: int = 0
    tools: list[HighRiskToolEntry] = Field(default_factory=list)


class ApprovalCoverageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    declared: bool
    source: str | None = None  # "openai_api" | "anthropic" | "policies" | None
    gap_finding_ids: list[str] = Field(default_factory=list)


class ApprovalCoverageSection(BaseModel):
    """§4 — approval policy coverage."""

    model_config = ConfigDict(extra="forbid")

    status: SectionStatus
    rows: list[ApprovalCoverageRow] = Field(default_factory=list)
    gap_findings: list[ReleaseDecisionItem] = Field(default_factory=list)


class IdempotencyRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    declared: bool
    source: str | None = None
    gap_finding_ids: list[str] = Field(default_factory=list)


class IdempotencyRiskSection(BaseModel):
    """§5 — idempotency / retry risk."""

    model_config = ConfigDict(extra="forbid")

    status: SectionStatus
    rows: list[IdempotencyRow] = Field(default_factory=list)
    gap_findings: list[ReleaseDecisionItem] = Field(default_factory=list)
    retry_policy_declared: bool = False


class ScopeCoverageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    declared: bool
    used_by_tools: list[str] = Field(default_factory=list)


class ScopeCoverageSection(BaseModel):
    """§6 — scope coverage."""

    model_config = ConfigDict(extra="forbid")

    status: SectionStatus
    declared_scopes: list[str] = Field(default_factory=list)
    rows: list[ScopeCoverageRow] = Field(default_factory=list)
    unused_declared: list[str] = Field(default_factory=list)
    missing_declared: list[str] = Field(default_factory=list)
    gap_findings: list[ReleaseDecisionItem] = Field(default_factory=list)


class MemoryIsolationStatus(BaseModel):
    """§7 — memory isolation. v0.1 always renders ``is_declared=False``;
    a future manifest schema may add ``agent.memory`` and populate this
    section. Until then the structural slot is preserved so packets
    have a consistent shape across versions."""

    model_config = ConfigDict(extra="forbid")

    status: SectionStatus = "not_declared"
    is_declared: bool = False
    notes: str = (
        "Manifest does not declare a memory isolation policy. "
        "The current manifest schema (v0.1) has no agent.memory field. "
        "See §10 for the residual review item."
    )


class HumanInTheLoopEvidence(BaseModel):
    """§8 — human-in-the-loop evidence."""

    model_config = ConfigDict(extra="forbid")

    status: SectionStatus
    is_configured: bool = False
    human_review_recommended: bool = False
    approval_required_tools: list[str] = Field(default_factory=list)
    confirmation_required_tools: list[str] = Field(default_factory=list)
    trace_findings: list[ReleaseDecisionItem] = Field(default_factory=list)


class DynamicScenarioRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str
    why: str
    finding_ids: list[str] = Field(default_factory=list)


class DynamicScenariosSection(BaseModel):
    """§9 — required dynamic scenarios."""

    model_config = ConfigDict(extra="forbid")

    status: SectionStatus
    scenarios: list[DynamicScenarioRequirement] = Field(default_factory=list)


class NotProvenItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    body: str


class NotProvenSection(BaseModel):
    """§10 — what Shipgate did NOT prove. Combines unconditional
    disclaimers with per-run residuals so reviewers can see both the
    static scope and what's missing for this specific scan."""

    model_config = ConfigDict(extra="forbid")

    headline: str
    unconditional: list[NotProvenItem] = Field(default_factory=list)
    source_warnings: list[str] = Field(default_factory=list)
    low_confidence_tools: list[str] = Field(default_factory=list)
    suppressed_finding_ids: list[str] = Field(default_factory=list)
    additional_residuals: list[str] = Field(default_factory=list)


class EvidencePacket(BaseModel):
    """Top-level packet model. Mirrors the JSON Schema 1:1.

    ``packet_schema_version`` is a literal so the schema generator
    pins the value into the generated JSON Schema; bumping it is a
    single-source change here.
    """

    model_config = ConfigDict(extra="forbid")

    packet_schema_version: Literal["0.1"] = "0.1"
    generated_at: str | None = None
    run_id: str
    project: dict[str, Any] = Field(default_factory=dict)
    agent: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)

    release_decision: ReleaseDecisionSection
    capability_intent: CapabilityIntentDiff
    high_risk_surface: HighRiskSurfaceSection
    approval_coverage: ApprovalCoverageSection
    idempotency_risk: IdempotencyRiskSection
    scope_coverage: ScopeCoverageSection
    memory_isolation: MemoryIsolationStatus
    human_in_the_loop: HumanInTheLoopEvidence
    dynamic_scenarios: DynamicScenariosSection
    not_proven: NotProvenSection


__all__ = [
    "ApprovalCoverageRow",
    "ApprovalCoverageSection",
    "CapabilityIntentDiff",
    "CapabilityIntentRow",
    "DynamicScenarioRequirement",
    "DynamicScenariosSection",
    "EvidencePacket",
    "HighRiskSurfaceSection",
    "HighRiskToolEntry",
    "HumanInTheLoopEvidence",
    "IdempotencyRiskSection",
    "IdempotencyRow",
    "MemoryIsolationStatus",
    "NotProvenItem",
    "NotProvenSection",
    "ReleaseDecisionSection",
    "ScopeCoverageRow",
    "ScopeCoverageSection",
    "SectionStatus",
    "Severity",
    "VerdictLabel",
]
