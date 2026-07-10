"""Versioned wire projection of deterministic tool semantic evidence.

The in-memory resolver models live in :mod:`agents_shipgate.core.domain`.
They cannot be imported by ``schemas.surfaces`` without creating a cycle
because the domain already consumes ``ActionEffect``. These strict schema
equivalents are the single public shape used by action and capability facts.
Builders validate ``ToolSemanticAssessment.model_dump()`` into this model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.common import Confidence, ProvenanceKind

SemanticActionEffect = Literal[
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
SemanticDimension = Literal["effect", "authority"]
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
    "incomplete_surface",
    "missing_effect_evidence",
    "inferred_effect_only",
    "conflicting_effect_evidence",
    "missing_authority_evidence",
    "partial_authority_evidence",
    "conflicting_authority_evidence",
    "invalid_semantic_annotation",
]


class SemanticClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: SemanticDimension
    value: str
    confidence: Confidence
    provenance_kind: ProvenanceKind
    source: str
    source_pointer: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class SemanticIssueEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SemanticIssueKind
    dimension: SemanticDimension
    message: str
    source: str | None = None
    source_pointer: str | None = None


class EffectSemanticEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: EffectEvidenceStatus
    confidence: Confidence
    claims: list[SemanticClaimEvidence] = Field(default_factory=list)
    issues: list[SemanticIssueEvidence] = Field(default_factory=list)


class AuthoritySemanticEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AuthorityEvidenceStatus
    mode: AuthorityMode
    auth_type: str | None = None
    credential_mode: str | None = None
    scopes: list[str] = Field(default_factory=list)
    claims: list[SemanticClaimEvidence] = Field(default_factory=list)
    issues: list[SemanticIssueEvidence] = Field(default_factory=list)


class ToolSemanticEvidence(BaseModel):
    """Public semantic assessment attached to action and capability facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conservative_effect: SemanticActionEffect
    effect: EffectSemanticEvidence
    authority: AuthoritySemanticEvidence
    pass_eligible: bool


__all__ = [
    "AuthorityEvidenceStatus",
    "AuthorityMode",
    "AuthoritySemanticEvidence",
    "EffectEvidenceStatus",
    "EffectSemanticEvidence",
    "SemanticActionEffect",
    "SemanticClaimEvidence",
    "SemanticDimension",
    "SemanticIssueEvidence",
    "SemanticIssueKind",
    "ToolSemanticEvidence",
]
