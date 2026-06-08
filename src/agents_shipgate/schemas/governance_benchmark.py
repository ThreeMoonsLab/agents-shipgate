from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from agents_shipgate.schemas.capability_semantics import (
    CapabilityHashName,
    CapabilitySemanticDirection,
)
from agents_shipgate.schemas.common import ReleaseDecisionStatus
from agents_shipgate.schemas.surfaces import ActionEffect
from agents_shipgate.schemas.verifier import MergeVerdict

GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION = "0.2"
GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION = "0.2"

GovernanceCaseStatus = Literal["executable", "catalog_only", "external_evidence"]
GovernanceMetricName = Literal[
    "unsafe_merge_prevention",
    "safe_pass_rate",
    "authority_routing",
    "explanation_usefulness",
    "remediation_boundary",
    "capability_semantic_fidelity",
]
GovernanceNextActor = Literal["human", "coding_agent"]
CapabilityExpectationBucket = Literal[
    "added",
    "removed",
    "changed",
    "reidentified",
    "evidence_changed",
]
CapabilityExpectationSide = Literal["before", "after", "either"]


class CapabilityExpectationV1(BaseModel):
    """One benchmark assertion over a capability-lock diff bucket."""

    model_config = ConfigDict(extra="forbid")

    bucket: CapabilityExpectationBucket
    description: str | None = None
    side: CapabilityExpectationSide = "after"
    tool_name: str | None = None
    operation: str | None = None
    provider: str | None = None
    effect: ActionEffect | None = None
    high_risk: bool | None = None
    financial: bool | None = None
    code_execution: bool | None = None
    broad_scope: bool | None = None
    scope_contains: tuple[str, ...] = Field(default_factory=tuple)
    scope_absent: tuple[str, ...] = Field(default_factory=tuple)
    risk_tags_contains: tuple[str, ...] = Field(default_factory=tuple)
    changed_hashes_contains: tuple[CapabilityHashName, ...] = Field(default_factory=tuple)
    semantic_direction: CapabilitySemanticDirection | None = None
    semantic_change_fields_contains: tuple[str, ...] = Field(default_factory=tuple)


class GovernanceBenchmarkCaseV1(BaseModel):
    """One governance case from the benchmark catalog."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    title: str
    status: GovernanceCaseStatus = "catalog_only"
    fixture: str | None = None
    artifacts: tuple[str, ...] = Field(default_factory=tuple)
    decision: ReleaseDecisionStatus
    merge_verdict: MergeVerdict
    next_actor: GovernanceNextActor
    evidence: tuple[str, ...] = Field(default_factory=tuple)
    metrics: tuple[GovernanceMetricName, ...] = Field(default_factory=tuple)
    capability_expectations: tuple[CapabilityExpectationV1, ...] = Field(
        default_factory=tuple
    )

    @model_validator(mode="after")
    def _executable_cases_have_fixture(self) -> GovernanceBenchmarkCaseV1:
        if self.status == "executable" and not self.fixture:
            raise ValueError("executable governance cases require fixture")
        return self


class GovernanceBenchmarkCatalogV1(BaseModel):
    """Executable governance benchmark catalog."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.2"] = GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION
    name: str
    description: str
    metrics: tuple[GovernanceMetricName, ...]
    cases: list[GovernanceBenchmarkCaseV1]

    @model_validator(mode="after")
    def _sort_and_validate_cases(self) -> GovernanceBenchmarkCatalogV1:
        ids = [case.id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate governance benchmark case ids: {duplicates}")
        self.cases.sort(key=lambda case: case.id)
        return self


class GovernanceGateActualV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str | None = None
    merge_verdict: str | None = None
    next_actor: str | None = None
    can_merge_without_human: bool | None = None
    fix_task_actor: str | None = None
    fix_task_safe_to_attempt: bool | None = None


class GovernanceCapabilityDiffActualV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: int = 0
    removed: int = 0
    reidentified: int = 0
    changed: int = 0
    evidence_changed: int = 0
    unchanged: int = 0


class GovernanceMetricResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: GovernanceMetricName
    passed: bool
    rationale: str


class GovernanceCaseResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    title: str
    status: GovernanceCaseStatus
    fixture: str | None = None
    passed: bool
    skipped: bool = False
    failures: list[str] = Field(default_factory=list)
    expected: dict[str, object] = Field(default_factory=dict)
    actual: GovernanceGateActualV1 | None = None
    capability_diff: GovernanceCapabilityDiffActualV1 | None = None
    metric_results: list[GovernanceMetricResultV1] = Field(default_factory=list)


class GovernanceBenchmarkSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_cases: int = 0
    executable_cases: int = 0
    catalog_only_cases: int = 0
    external_evidence_cases: int = 0
    selected_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    skipped_cases: int = 0


class GovernanceBenchmarkMetricSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: GovernanceMetricName
    total: int = 0
    passed: int = 0
    failed: int = 0


class GovernanceBenchmarkResultV1(BaseModel):
    """Deterministic benchmark result artifact."""

    model_config = ConfigDict(extra="forbid")

    governance_benchmark_result_schema_version: Literal["0.2"] = (
        GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION
    )
    experimental: Literal[False] = False
    catalog_schema_version: str
    catalog_path: str | None = None
    summary: GovernanceBenchmarkSummaryV1
    metrics: list[GovernanceBenchmarkMetricSummaryV1] = Field(default_factory=list)
    cases: list[GovernanceCaseResultV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_results(self) -> GovernanceBenchmarkResultV1:
        self.metrics.sort(key=lambda metric: metric.metric)
        self.cases.sort(key=lambda case: case.id)
        return self


class GovernanceBenchmarkCatalogArtifactV1(RootModel[GovernanceBenchmarkCatalogV1]):
    root: GovernanceBenchmarkCatalogV1


class GovernanceBenchmarkResultArtifactV1(RootModel[GovernanceBenchmarkResultV1]):
    root: GovernanceBenchmarkResultV1


__all__ = [
    "GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION",
    "GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION",
    "CapabilityExpectationV1",
    "GovernanceBenchmarkCatalogArtifactV1",
    "GovernanceBenchmarkCatalogV1",
    "GovernanceBenchmarkCaseV1",
    "GovernanceBenchmarkMetricSummaryV1",
    "GovernanceBenchmarkResultArtifactV1",
    "GovernanceBenchmarkResultV1",
    "GovernanceBenchmarkSummaryV1",
    "GovernanceCapabilityDiffActualV1",
    "GovernanceCaseResultV1",
    "GovernanceCaseStatus",
    "GovernanceGateActualV1",
    "GovernanceMetricName",
    "GovernanceMetricResultV1",
    "GovernanceNextActor",
]
