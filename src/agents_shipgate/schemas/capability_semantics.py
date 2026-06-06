from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

CapabilityHashName = Literal[
    "identity_hash",
    "effect_hash",
    "authority_hash",
    "control_hash",
    "schema_hash",
    "risk_hash",
    "evidence_hash",
]

CapabilitySemanticDirection = Literal[
    "added",
    "removed",
    "broadened",
    "narrowed",
    "mixed",
    "unknown",
    "evidence_only",
]


class CapabilitySemanticChange(BaseModel):
    """One semantic explanation for a capability delta."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    field: str
    before: Any = None
    after: Any = None
    rationale: str


__all__ = [
    "CapabilityHashName",
    "CapabilitySemanticChange",
    "CapabilitySemanticDirection",
]
