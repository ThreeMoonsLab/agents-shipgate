from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_shipgate.schemas.common import ReleaseDecisionStatus
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER

SAFETY_CORPUS_SCHEMA_VERSION = "shipgate.safety_corpus/v4"
SAFETY_RECEIPT_INDEX_SCHEMA_VERSION = "shipgate.safety_receipt_index/v4"
# v5 carries the ``pre_1_0`` tier and the production_qualified/tier invariant
# added for issue #341. The corpus and receipt-index envelopes stay at v4: their
# grammar did not move, and versions here track grammar, not release batches.
SAFETY_QUALIFICATION_SCHEMA_VERSION = "shipgate.safety_qualification/v5"

SafetyProfile = Literal[
    "mcp",
    "openapi",
    "explicit_framework_inventory",
    "coding_agent_trust_roots",
    "mcp_openapi_declared_binding",
    "openai_agents_sdk",
    "langchain_crewai",
    "google_adk",
    "n8n",
    "multi_agent_handoffs",
]
SafetyCaseOrigin = Literal[
    "real_history",
    "rejected_or_reverted",
    "design_partner",
    "synthetic",
]
SafetyCorpusSplit = Literal["tuning", "holdout"]
SafetyLabelRole = Literal["security_governance", "framework_tooling"]
SafetyAdjudicationState = Literal["agreement", "adjudicated_disagreement"]
QualificationTier = Literal["beta", "pre_1_0", "test"]

# Envelopes whose readers predate ``pre_1_0`` and the production_qualified/tier
# invariant. They are still *read*, but only when the payload actually uses the
# vocabulary they can express -- see ``_upgrade_prior_envelopes``.
LEGACY_QUALIFICATION_ENVELOPES = frozenset(
    {
        "shipgate.safety_qualification/v1",
        "shipgate.safety_qualification/v2",
        "shipgate.safety_qualification/v4",
    }
)
LEGACY_QUALIFICATION_TIERS: frozenset[str] = frozenset({"beta", "test"})

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class IndependentHumanLabelV1(BaseModel):
    """One blind, attributable label from one of the two required disciplines."""

    model_config = ConfigDict(extra="forbid")

    role: SafetyLabelRole
    reviewer_id: str = Field(min_length=1)
    decision: ReleaseDecisionStatus
    rationale: str = Field(min_length=1)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    shipgate_output_seen: Literal[False] = False

    @field_validator("reviewer_id", "rationale")
    @classmethod
    def _required_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("human label identity and rationale must not be blank")
        return value

    @field_validator("evidence_references")
    @classmethod
    def _evidence_references_are_not_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("human label evidence references must not be blank")
        return value


class HumanAdjudicationV1(BaseModel):
    """The frozen final label and, for disagreements, the third-human decision."""

    model_config = ConfigDict(extra="forbid")

    state: SafetyAdjudicationState
    final_decision: ReleaseDecisionStatus
    reviewer_id: str | None = None
    rationale: str = Field(min_length=1)
    evidence_references: tuple[str, ...] = Field(min_length=1)
    shipgate_output_seen: Literal[False] = False

    @field_validator("reviewer_id")
    @classmethod
    def _optional_reviewer_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("adjudicator identity must not be blank")
        return value

    @field_validator("rationale")
    @classmethod
    def _rationale_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("adjudication rationale must not be blank")
        return value

    @field_validator("evidence_references")
    @classmethod
    def _evidence_references_are_not_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("adjudication evidence references must not be blank")
        return value


class SafetyCorpusCaseV1(BaseModel):
    """One independently labeled qualification case."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    profile: SafetyProfile
    origin: SafetyCaseOrigin
    split: SafetyCorpusSplit
    expected_decision: ReleaseDecisionStatus
    evidence_references: tuple[str, ...] = Field(min_length=1)
    remediation_condition: str = Field(min_length=1)
    security_governance_label: IndependentHumanLabelV1
    framework_tooling_label: IndependentHumanLabelV1
    adjudication: HumanAdjudicationV1

    @field_validator("id", "remediation_condition")
    @classmethod
    def _required_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("case identity and remediation condition must not be blank")
        return value

    @field_validator("evidence_references")
    @classmethod
    def _evidence_references_are_not_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("case evidence references must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_independent_labels(self) -> SafetyCorpusCaseV1:
        security = self.security_governance_label
        framework = self.framework_tooling_label
        if security.role != "security_governance":
            raise ValueError("security_governance_label must have role=security_governance")
        if framework.role != "framework_tooling":
            raise ValueError("framework_tooling_label must have role=framework_tooling")
        security_reviewer = security.reviewer_id.strip().casefold()
        framework_reviewer = framework.reviewer_id.strip().casefold()
        if security_reviewer == framework_reviewer:
            raise ValueError("the two primary labels must come from different reviewers")
        if self.expected_decision != self.adjudication.final_decision:
            raise ValueError("expected_decision must equal adjudication.final_decision")

        labels_agree = security.decision == framework.decision
        if labels_agree:
            if self.adjudication.state != "agreement":
                raise ValueError("agreeing primary labels require adjudication.state=agreement")
            if self.adjudication.final_decision != security.decision:
                raise ValueError("agreement must preserve the primary decision")
            if self.adjudication.reviewer_id is not None:
                raise ValueError("agreement must not claim a third adjudicator")
        else:
            if self.adjudication.state != "adjudicated_disagreement":
                raise ValueError(
                    "disagreeing primary labels require adjudication.state=adjudicated_disagreement"
                )
            adjudicator = self.adjudication.reviewer_id
            if not adjudicator:
                raise ValueError("every disagreement requires a third human adjudicator")
            if adjudicator.strip().casefold() in {security_reviewer, framework_reviewer}:
                raise ValueError("the disagreement adjudicator must be a third reviewer")
        return self


def compute_frozen_labels_sha256(cases: list[SafetyCorpusCaseV1]) -> str:
    """Hash only frozen label content, independently of corpus file ordering."""

    payload = [
        {
            "id": case.id,
            "expected_decision": case.expected_decision,
            "evidence_references": list(case.evidence_references),
            "remediation_condition": case.remediation_condition,
            "security_governance_label": case.security_governance_label.model_dump(mode="json"),
            "framework_tooling_label": case.framework_tooling_label.model_dump(mode="json"),
            "adjudication": case.adjudication.model_dump(mode="json"),
        }
        for case in sorted(cases, key=lambda item: item.id)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FrozenSafetyCorpusV1(BaseModel):
    """Frozen, blind-labeled corpus consumed by the qualification runner."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.safety_corpus/v4"] = SAFETY_CORPUS_SCHEMA_VERSION
    corpus_id: str = Field(min_length=1)
    labels_frozen_before_evaluation: Literal[True]
    outputs_hidden_from_labelers: Literal[True]
    frozen_labels_sha256: str = Field(pattern=SHA256_PATTERN)
    cases: list[SafetyCorpusCaseV1] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _upgrade_v1_schema(cls, value: str) -> str:
        return (
            SAFETY_CORPUS_SCHEMA_VERSION
            if value in {"shipgate.safety_corpus/v1", "shipgate.safety_corpus/v2"}
            else value
        )

    @field_validator("corpus_id")
    @classmethod
    def _corpus_id_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("corpus_id must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_frozen_corpus(self) -> FrozenSafetyCorpusV1:
        ids = [case.id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate safety corpus case ids: {duplicates}")
        self.cases.sort(key=lambda case: case.id)
        actual = compute_frozen_labels_sha256(self.cases)
        if self.frozen_labels_sha256 != actual:
            raise ValueError(
                "frozen_labels_sha256 does not match the canonical human-label payload"
            )
        return self


def _validate_relative_path(value: str) -> str:
    from pathlib import PurePosixPath

    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."}:
        raise ValueError("qualification receipt paths must be non-empty relative paths")
    return value


class SafetyReceiptEntryV1(BaseModel):
    """Index row for one real, terminal verification receipt."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    receipt_path: str
    artifact_root: str
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    receipt_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_set_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_mode: Literal["verify"] = "verify"
    fallback_used: Literal[False] = False

    @field_validator("case_id")
    @classmethod
    def _case_id_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("receipt case_id must not be blank")
        return value

    @model_validator(mode="after")
    def _paths_are_relative(self) -> SafetyReceiptEntryV1:
        self.receipt_path = _validate_relative_path(self.receipt_path)
        self.artifact_root = _validate_relative_path(self.artifact_root)
        return self


class SafetyReceiptIndexV1(BaseModel):
    """Content bindings for receipts produced after the labels were frozen."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.safety_receipt_index/v4"] = (
        SAFETY_RECEIPT_INDEX_SCHEMA_VERSION
    )
    wheel_sha256: str = Field(pattern=SHA256_PATTERN)
    engine_version: str = Field(min_length=1)
    corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    labels_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    labels_frozen_before_receipts: Literal[True]
    receipts: list[SafetyReceiptEntryV1] = Field(min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _upgrade_v1_schema(cls, value: str) -> str:
        return (
            SAFETY_RECEIPT_INDEX_SCHEMA_VERSION
            if value
            in {
                "shipgate.safety_receipt_index/v1",
                "shipgate.safety_receipt_index/v2",
            }
            else value
        )

    @field_validator("engine_version")
    @classmethod
    def _engine_version_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("engine_version must not be blank")
        return value

    @model_validator(mode="after")
    def _sort_and_validate_receipts(self) -> SafetyReceiptIndexV1:
        ids = [entry.case_id for entry in self.receipts]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate safety receipt case ids: {duplicates}")
        self.receipts.sort(key=lambda entry: entry.case_id)
        return self


class SafetyStratumRequirementV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: SafetyProfile
    expected_decision: ReleaseDecisionStatus
    count: int = Field(ge=0)


class SafetyQualificationRequirementsV1(BaseModel):
    """Qualification thresholds. The CLI always uses the production defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_strata: tuple[SafetyStratumRequirementV1, ...]
    minimum_qualified_origins: int = Field(ge=0)
    minimum_kappa: float = Field(ge=-1.0, le=1.0)
    minimum_holdout_fraction_per_stratum: float = Field(gt=0.0, le=1.0)
    maximum_unsafe_auto_passes: int = Field(ge=0)
    minimum_safe_passes: int = Field(ge=0)
    minimum_blocked_exact: int = Field(ge=0)
    minimum_review_exact: int = Field(ge=0)
    minimum_insufficient_evidence_exact: int = Field(ge=0)
    required_report_schema_version: str = Field(min_length=1)

    @field_validator("required_report_schema_version")
    @classmethod
    def _report_schema_version_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required_report_schema_version must not be blank")
        return value


def production_safety_requirements() -> SafetyQualificationRequirementsV1:
    profile_counts = {
        "mcp_openapi_declared_binding": {
            "passed": 6,
            "review_required": 4,
            "blocked": 6,
        },
        "openai_agents_sdk": {
            "passed": 5,
            "review_required": 3,
            "blocked": 4,
        },
        "langchain_crewai": {
            "passed": 5,
            "review_required": 3,
            "blocked": 4,
        },
        "google_adk": {
            "passed": 3,
            "review_required": 2,
            "blocked": 3,
        },
        "n8n": {
            "passed": 3,
            "review_required": 2,
            "blocked": 3,
        },
        "multi_agent_handoffs": {
            "passed": 4,
            "review_required": 3,
            "blocked": 5,
        },
        "coding_agent_trust_roots": {
            "passed": 4,
            "review_required": 3,
            "blocked": 5,
        },
    }
    strata = tuple(
        SafetyStratumRequirementV1(
            profile=profile,  # type: ignore[arg-type]
            expected_decision=decision,  # type: ignore[arg-type]
            count=count,
        )
        for profile, outcomes in profile_counts.items()
        for decision, count in outcomes.items()
    )
    return SafetyQualificationRequirementsV1(
        required_strata=strata,
        # 40% of the corpus, rounded up: ceil(0.40 x 80) = 32. The share is
        # what was approved -- it read "40 of 100" while `insufficient_evidence`
        # still held 20 cells. Those cells left the vocabulary (#520), so the
        # corpus is 80 and holding the *count* at 40 would raise the demand to
        # half the corpus as a side effect of deleting a decision. The share is
        # the decision; the count follows it.
        minimum_qualified_origins=32,
        minimum_kappa=0.80,
        minimum_holdout_fraction_per_stratum=0.20,
        maximum_unsafe_auto_passes=0,
        minimum_safe_passes=27,
        minimum_blocked_exact=30,
        minimum_review_exact=19,
        # `insufficient_evidence` is not a ground-truth decision in either
        # tier (#520): it says the reader failed, not what a correct gate
        # should do. The field stays because it is shipped surface.
        minimum_insufficient_evidence_exact=0,
        # The schema the engine this gate qualifies actually emits. Pinned to
        # a literal rather than read from ``ReadinessReport`` so a bump is a
        # deliberate edit a reviewer sees — and pinned *equal* to it by
        # ``test_the_qualification_gate_demands_the_schema_the_engine_emits``,
        # because a gate demanding a schema no build produces rejects every
        # receipt with "qualification report schema mismatch".
        required_report_schema_version="0.43",
    )


# The seven profiles the release policies stratify by, named once so the
# pre-1.0 policy cannot silently cover fewer of them than the production one.
# ``test_the_pre_1_0_policy_covers_every_production_profile`` binds the two.
RELEASE_SAFETY_PROFILES: tuple[SafetyProfile, ...] = (
    "mcp_openapi_declared_binding",
    "openai_agents_sdk",
    "langchain_crewai",
    "google_adk",
    "n8n",
    "multi_agent_handoffs",
    "coding_agent_trust_roots",
)


def pre_release_safety_requirements() -> SafetyQualificationRequirementsV1:
    """The approved pre-1.0 policy: less coverage, identical strictness.

    Recorded by Pengfei Hu on 2026-08-29 under
    ``docs/release-evidence-policy-decision.md`` (issue #341, Route 2). It
    governs ``0.x`` tags only; ``1.0`` and later still require the 100-case
    production policy, and this constructor is never selected for them.

    Two cases per stratum is the smallest allocation that keeps all 28
    profile x decision cells non-empty *and* leaves room for a tuning/holdout
    split in every cell: at one case per cell, ``ceil(1 x 0.20) = 1`` forces
    that case to be holdout. Dropping a cell to zero would delete coverage of a
    profile/outcome pair outright, which is a strictness reduction and not a
    coverage one; the uniform layout is what avoids it at this size.

    What is *enforced* is the holdout floor, not a one-of-each split. A corpus
    that marks more cases holdout is accepted: holdout evidence was never tuned
    on, so more of it is stronger, and a floor on tuning cases would be a
    ceiling on holdout.

    Every threshold below is at least as strict, *as a rate*, as its
    production counterpart -- checked by
    ``test_the_pre_1_0_policy_is_never_laxer_than_production_per_rate``. The
    invariants that may not move at all (zero unsafe auto-passes, the holdout
    fraction, the kappa floor, the report schema) are byte-identical.
    """

    # 21 cells, not 28: `insufficient_evidence` left the ground-truth
    # vocabulary (#520). It is a statement about the reader -- what a gate says
    # when its own extraction failed -- and a *correct* gate with complete
    # inputs can always name the binding, so it cannot be what a correct gate
    # "should do" with a change. The engine still emits the value; a corpus
    # case is never targeted at it.
    #
    # Two cases per cell, except where the material does not exist. The
    # exceptions are all `blocked`, and they are measured rather than assumed:
    # the first corpus round labeled 48 cases blind, and of the seven profiles
    # only four have any real-world `blocked` case at all. `google_adk` and
    # `langchain_crewai` hold a construction and nothing else -- each was
    # sourced with a real candidate that two independent raters then placed at
    # `review_required`; `openai_agents_sdk` and `coding_agent_trust_roots`
    # hold one real case each. Cut B recorded the cause before any of this:
    # a blocked-shaped change is usually stopped before it merges, which is
    # why the inventory hunts `blocked` in the rejected-or-reverted vein.
    #
    # Requiring a second `blocked` in those four cells would be met the only
    # way it could be -- by building another construction -- and a cell filled
    # entirely with constructions measures our imagination rather than the
    # world. One real case is worth more than two invented ones, and the count
    # says so.
    scarce_blocked = frozenset(
        {"openai_agents_sdk", "langchain_crewai", "google_adk", "coding_agent_trust_roots"}
    )
    strata = tuple(
        SafetyStratumRequirementV1(
            profile=profile,
            expected_decision=decision,
            count=1 if decision == "blocked" and profile in scarce_blocked else 2,
        )
        for profile in RELEASE_SAFETY_PROFILES
        for decision in ("passed", "review_required", "blocked")
    )
    return SafetyQualificationRequirementsV1(
        required_strata=strata,
        # 40% of the corpus, rounded up -- the same origin floor production
        # applies (32 of 80), not a smaller share of a smaller corpus:
        # ceil(0.40 x 38) = 16.
        minimum_qualified_origins=16,
        minimum_kappa=0.80,
        minimum_holdout_fraction_per_stratum=0.20,
        maximum_unsafe_auto_passes=0,
        # Production's rates, applied to this corpus's cases and rounded up --
        # the same derivation as before, over 14 `passed`, 14
        # `review_required` and 10 `blocked` (2 x 3 profiles + 1 x 4 scarce):
        # safe 27/30 -> ceil(0.90 x 14) = 13; review 19/20 -> ceil(0.95 x 14)
        # = 14; blocked 30/30 -> ceil(1.00 x 10) = 10. The blocked floor falls
        # from 14 only because four cells hold one case, which is the honest
        # consequence of the scarcity documented above and not a rate anyone
        # relaxed -- `test_the_pre_1_0_policy_is_never_laxer_than_production_
        # per_rate` is what holds that line.
        minimum_safe_passes=13,
        minimum_blocked_exact=10,
        minimum_review_exact=14,
        # No corpus case is targeted at `insufficient_evidence`, so there is
        # no floor to meet. The field stays because it is shipped surface and
        # the `beta` tier still carries one.
        minimum_insufficient_evidence_exact=0,
        required_report_schema_version="0.43",
    )


def requirements_for_tier(
    tier: QualificationTier,
) -> SafetyQualificationRequirementsV1 | None:
    """The named policy a tier claims, or ``None`` for the unnamed ``test`` tier.

    ``None`` is not a permissive default: every caller that gates a release
    treats it as "no named policy backs this artifact" and rejects.
    """

    if tier == "beta":
        return production_safety_requirements()
    if tier == "pre_1_0":
        return pre_release_safety_requirements()
    return None


def tier_for_requirements(
    requirements: SafetyQualificationRequirementsV1,
) -> QualificationTier:
    """Name the policy a requirements object *is*, never what it claims to be.

    Anything that is not byte-equal to a named policy is ``test``: an ad-hoc
    threshold set can never present itself as a release-grade one.
    """

    if requirements == production_safety_requirements():
        return "beta"
    if requirements == pre_release_safety_requirements():
        return "pre_1_0"
    return "test"


class QualificationInputDigestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wheel_name: str
    wheel_version: str
    engine_version: str
    wheel_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_name: str
    corpus_id: str
    corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    labels_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    receipt_index_name: str
    receipt_index_sha256: str = Field(pattern=SHA256_PATTERN)


class SafetyQualificationFailureV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    case_id: str | None = None
    profile: SafetyProfile | None = None
    expected_decision: ReleaseDecisionStatus | None = None


class SafetyQualificationStratumV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: SafetyProfile
    expected_decision: ReleaseDecisionStatus
    required_count: int
    actual_count: int
    tuning_count: int
    holdout_count: int
    minimum_holdout_count: int
    receipt_count: int
    unsafe_auto_pass_count: int
    runtime_failure_count: int


class SafetyConfusionRowV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected: ReleaseDecisionStatus
    actual: dict[ReleaseDecisionStatus, int]


class SafetyConfusionMatrixV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: SafetyProfile | Literal["all"]
    rows: list[SafetyConfusionRowV1]


class WilsonIntervalV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence: Literal[0.95] = 0.95
    method: Literal["wilson_score"] = "wilson_score"
    lower: float
    upper: float


class SafetyQualificationMetricV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    numerator: int
    denominator: int
    rate: float
    interval: WilsonIntervalV1
    requirement: str
    passed: bool


class SafetyQualificationCaseResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    profile: SafetyProfile
    split: SafetyCorpusSplit
    expected_decision: ReleaseDecisionStatus
    actual_decision: ReleaseDecisionStatus | None = None
    exact: bool = False
    receipt_sha256: str | None = None
    runtime_failure: bool = False


class SafetyQualificationSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_cases: int
    qualified_origin_cases: int
    primary_label_agreements: int
    cohen_kappa: float
    receipt_count: int
    exact_count: int
    unsafe_auto_pass_count: int
    runtime_failure_count: int
    outcome_counts: dict[ReleaseDecisionStatus, int]


class SafetyQualificationResultV1(BaseModel):
    """Deterministic, wheel-bound qualification result for a named policy."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.safety_qualification/v5"] = (
        SAFETY_QUALIFICATION_SCHEMA_VERSION
    )
    qualification_tier: QualificationTier
    qualified: bool
    production_qualified: bool
    static_only: Literal[True] = True
    runtime_behavior_proven: Literal[False] = False
    static_verdict_disclaimer: Literal[STATIC_VERDICT_DISCLAIMER] = STATIC_VERDICT_DISCLAIMER
    inputs: QualificationInputDigestV1
    requirements: SafetyQualificationRequirementsV1
    summary: SafetyQualificationSummaryV1
    strata: list[SafetyQualificationStratumV1]
    confusion_matrices: list[SafetyConfusionMatrixV1]
    intervals: list[SafetyQualificationMetricV1]
    cases: list[SafetyQualificationCaseResultV1]
    failures: list[SafetyQualificationFailureV1]

    @model_validator(mode="before")
    @classmethod
    def _upgrade_prior_envelopes(cls, data: Any) -> Any:
        """Read an earlier envelope only when its own vocabulary is used.

        A legacy envelope is upgraded to the current one *conditionally*: its
        reader admits ``beta`` and ``test`` and nothing else, so a payload
        carrying ``pre_1_0`` is not a legacy payload however it is labelled.

        Doing this as a bare ``schema_version`` field validator was wrong, and
        wrong in the direction the version bump existed to close: the field
        validator rewrote v4 to v5 *before* anything could look at
        ``qualification_tier``, so relabelling a conforming ``pre_1_0``
        artifact as v4 was accepted -- recreating the exact v4/``pre_1_0``
        combination an old v4 reader cannot parse.
        """

        if not isinstance(data, dict):
            return data
        declared = data.get("schema_version")
        if declared not in LEGACY_QUALIFICATION_ENVELOPES:
            return data
        if data.get("qualification_tier") not in LEGACY_QUALIFICATION_TIERS:
            raise ValueError(
                f"{declared} admits only qualification_tier "
                f"{sorted(LEGACY_QUALIFICATION_TIERS)}; this payload requires "
                f"{SAFETY_QUALIFICATION_SCHEMA_VERSION}"
            )
        return {**data, "schema_version": SAFETY_QUALIFICATION_SCHEMA_VERSION}

    @model_validator(mode="after")
    def _production_claim_matches_the_tier(self) -> SafetyQualificationResultV1:
        """``production_qualified`` means the 100-case bar, in every artifact.

        Enforced structurally rather than at each gate so the *producer* cannot
        emit the inconsistency in the first place. A pre-1.0 artifact asserting
        the production flag would otherwise be signed before any verifier saw
        it, and rejected only at the last step before publication.
        """

        expected = self.qualified and self.qualification_tier == "beta"
        if self.production_qualified != expected:
            raise ValueError(
                "production_qualified must be true exactly when a qualified "
                "artifact claims the beta tier"
            )
        return self

    @model_validator(mode="after")
    def _sort_output(self) -> SafetyQualificationResultV1:
        self.strata.sort(key=lambda item: (item.profile, item.expected_decision))
        self.confusion_matrices.sort(key=lambda item: item.profile)
        self.intervals.sort(key=lambda item: item.name)
        self.cases.sort(key=lambda item: item.id)
        self.failures.sort(
            key=lambda item: (
                item.code,
                item.case_id or "",
                item.profile or "",
                item.expected_decision or "",
                item.message,
            )
        )
        return self


__all__ = [
    "LEGACY_QUALIFICATION_ENVELOPES",
    "LEGACY_QUALIFICATION_TIERS",
    "RELEASE_SAFETY_PROFILES",
    "SAFETY_CORPUS_SCHEMA_VERSION",
    "SAFETY_QUALIFICATION_SCHEMA_VERSION",
    "SAFETY_RECEIPT_INDEX_SCHEMA_VERSION",
    "FrozenSafetyCorpusV1",
    "HumanAdjudicationV1",
    "IndependentHumanLabelV1",
    "QualificationInputDigestV1",
    "SafetyAdjudicationState",
    "SafetyCaseOrigin",
    "SafetyConfusionMatrixV1",
    "SafetyConfusionRowV1",
    "SafetyCorpusCaseV1",
    "SafetyCorpusSplit",
    "SafetyLabelRole",
    "SafetyProfile",
    "SafetyQualificationCaseResultV1",
    "SafetyQualificationFailureV1",
    "SafetyQualificationMetricV1",
    "SafetyQualificationRequirementsV1",
    "SafetyQualificationResultV1",
    "SafetyQualificationStratumV1",
    "SafetyQualificationSummaryV1",
    "SafetyReceiptEntryV1",
    "SafetyReceiptIndexV1",
    "SafetyStratumRequirementV1",
    "WilsonIntervalV1",
    "compute_frozen_labels_sha256",
    "pre_release_safety_requirements",
    "production_safety_requirements",
    "requirements_for_tier",
    "tier_for_requirements",
]
