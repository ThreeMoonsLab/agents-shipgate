"""Re-export of the packaged ``ScorecardV1`` model.

The model itself lives in
:mod:`agents_shipgate.schemas.adoption_scorecard` so that a wheel-installed
agents-shipgate ships the contract. This module exists only so existing
imports of the form ``from harness.adoption.scorer.schema import …`` keep
working — please prefer the canonical path in new code.
"""
from __future__ import annotations

from agents_shipgate.schemas.adoption_scorecard import (
    SCORECARD_SCHEMA_VERSION,
    Blocker,
    CriterionResult,
    CriterionStatus,
    ScorecardV1,
    Severity,
)

__all__ = [
    "Blocker",
    "CriterionResult",
    "CriterionStatus",
    "SCORECARD_SCHEMA_VERSION",
    "ScorecardV1",
    "Severity",
]
