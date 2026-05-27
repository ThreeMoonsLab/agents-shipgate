from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    _parse_artifact_entries,
)
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class ValidationRequiredEvidenceConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    approval_trace_required: bool = False
    override_reason_required: bool = False
    high_risk_auto_approval_exclusion_required: bool = False


class ValidationEvidenceConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    approval_traces: list[ArtifactPathConfig] = Field(default_factory=list)
    override_logs: list[ArtifactPathConfig] = Field(default_factory=list)
    high_risk_exclusions: list[ArtifactPathConfig] = Field(default_factory=list)
    promotion_criteria: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator(
        "approval_traces",
        "override_logs",
        "high_risk_exclusions",
        "promotion_criteria",
        mode="before",
    )
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)


class ValidationConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    mode: Literal["human_in_the_loop"]
    target_review_posture: Literal[
        "recommendation_only",
        "limited_auto_approval",
    ] = "recommendation_only"
    required_evidence: ValidationRequiredEvidenceConfig = Field(
        default_factory=ValidationRequiredEvidenceConfig
    )
    evidence: ValidationEvidenceConfig = Field(default_factory=ValidationEvidenceConfig)
