"""External review decisions; no operation or release authority is encoded."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.human_authorization import (
    HumanAuthorizationPrincipalV1,
    HumanAuthorizationProofV1,
)
from agents_shipgate.schemas.human_review_request import HumanReviewRequestV1
from agents_shipgate.schemas.verification_identity import CONTENT_ID_PATTERN, content_id

HUMAN_REVIEW_DECISION_SCHEMA_VERSION = "shipgate.human_review_decision/v1"
HUMAN_REVIEW_EVALUATION_SCHEMA_VERSION = "shipgate.human_review_evaluation/v1"
HUMAN_REVIEW_DECISION_SCHEMA_PATH = "docs/human-review-decision-schema.v1.json"
HUMAN_REVIEW_DECISION_SIGNATURE_DOMAIN = b"agents-shipgate:human-review-decision:v1\x00"
ReviewOutcome = Literal["accepted", "rejected", "disputed"]
InertText = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"^[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f][^\x00-\x1f\x7f]*$")]


def _timestamp_input(value: object) -> object:
    if not isinstance(value, (str, datetime)):
        raise ValueError("review times must be date-time strings or datetime values")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("review times require an explicit timezone")
    return value.astimezone(UTC)


class HumanReviewDecisionStatementV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: HumanReviewRequestV1
    review_instance_id: InertText
    eligibility_context_id: str = Field(pattern=CONTENT_ID_PATTERN)
    principal: HumanAuthorizationPrincipalV1
    outcome: ReviewOutcome
    reason: InertText
    issued_at: datetime
    expires_at: datetime

    _time_inputs = field_validator("issued_at", "expires_at", mode="before")(_timestamp_input)
    _times_are_utc = field_validator("issued_at", "expires_at")(_utc)

    @model_validator(mode="after")
    def _ordered_window(self) -> HumanReviewDecisionStatementV1:
        if self.issued_at >= self.expires_at:
            raise ValueError("issued_at must precede expires_at")
        return self


class HumanReviewDecisionV1(BaseModel):
    """A detached signature over a review decision, never a signed push."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.human_review_decision/v1"] = HUMAN_REVIEW_DECISION_SCHEMA_VERSION
    review_decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    statement: HumanReviewDecisionStatementV1
    proof: HumanAuthorizationProofV1

    @model_validator(mode="after")
    def _content_identity(self) -> HumanReviewDecisionV1:
        expected = content_id(self.model_dump(mode="json", exclude={"schema_version", "review_decision_id"}))
        if self.review_decision_id != expected:
            raise ValueError("review_decision_id must hash the complete signed decision")
        return self


def build_human_review_decision(
    *, statement: HumanReviewDecisionStatementV1, proof: HumanAuthorizationProofV1
) -> HumanReviewDecisionV1:
    """Package an externally supplied signature; no private key is read here."""

    payload = {"statement": statement.model_dump(mode="json"), "proof": proof.model_dump(mode="json")}
    return HumanReviewDecisionV1(review_decision_id=content_id(payload), **payload)


class _ReviewEvaluationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.human_review_evaluation/v1"] = HUMAN_REVIEW_EVALUATION_SCHEMA_VERSION
    evaluated_at: datetime
    authority: Literal["none"] = "none"
    remaining_obligations: Literal["existing_release_gate_and_control"] = "existing_release_gate_and_control"

    _time_input = field_validator("evaluated_at", mode="before")(_timestamp_input)
    _time_is_utc = field_validator("evaluated_at")(_utc)


class ApplicableHumanReviewDecisionV1(_ReviewEvaluationBase):
    status: Literal["applicable"] = "applicable"
    outcome: ReviewOutcome
    review_decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    review_request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    source_receipt_id: str = Field(pattern=CONTENT_ID_PATTERN)
    current_control_id: str = Field(pattern=CONTENT_ID_PATTERN)
    review_instance_id: InertText
    eligibility_context_id: str = Field(pattern=CONTENT_ID_PATTERN)
    principal: HumanAuthorizationPrincipalV1
    # The current human-owned continuation is copied, never manufactured from
    # the signer's prose. It cannot carry a shell command in either schema.
    next_action: HumanControlAction
    trust_policy_id: str = Field(pattern=CONTENT_ID_PATTERN)
    expires_at: datetime

    _expiry_input = field_validator("expires_at", mode="before")(_timestamp_input)
    _expiry_is_utc = field_validator("expires_at")(_utc)


class InapplicableHumanReviewDecisionV1(_ReviewEvaluationBase):
    status: Literal["not_applicable"] = "not_applicable"
    reason_codes: list[InertText] = Field(min_length=1)
    outcome: None = None
    next_action: None = None


HumanReviewEvaluationV1 = Annotated[
    ApplicableHumanReviewDecisionV1 | InapplicableHumanReviewDecisionV1,
    Field(discriminator="status"),
]
