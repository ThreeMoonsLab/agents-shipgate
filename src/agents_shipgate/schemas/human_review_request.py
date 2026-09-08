"""One unsigned review question, separate from the frozen control union (#536)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_shipgate.schemas.human_authorization import (
    HumanAuthorizationReviewItemV1,
    canonical_review_items,
    review_set_id,
)
from agents_shipgate.schemas.verification_identity import (
    CONTENT_ID_PATTERN,
    GIT_OBJECT_PATTERN,
    content_id,
)

HUMAN_REVIEW_REQUEST_SCHEMA_VERSION = "shipgate.human_review_request/v1"
HUMAN_REVIEW_REQUEST_SCHEMA_PATH = "docs/human-review-request-schema.v1.json"
HUMAN_REVIEW_REQUEST_FILENAME = "human-review-request.json"


class HumanReviewQuestionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_item_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=1200, pattern=r"^[^\x00-\x1f\x7f]+$")


class HumanReviewPostconditionV1(BaseModel):
    """Recording a decision satisfies this question, not the release gate."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["authenticated_current_full_scope_decision"] = (
        "authenticated_current_full_scope_decision"
    )
    authenticated_human_required: Literal[True] = True
    trusted_eligibility_required: Literal[True] = True
    author_and_bot_excluded: Literal[True] = True
    current_request_required: Literal[True] = True
    complete_review_set_required: Literal[True] = True
    review_instance_binding_required: Literal[True] = True
    unexpired_decision_required: Literal[True] = True
    accepted: Literal["record_acceptance_preserve_gate"] = "record_acceptance_preserve_gate"
    rejected: Literal["record_rejection_preserve_gate"] = "record_rejection_preserve_gate"
    disputed: Literal["record_dispute_preserve_gate"] = "record_dispute_preserve_gate"
    remaining_obligations: Literal["existing_release_gate_and_control"] = (
        "existing_release_gate_and_control"
    )
    grants_merge_authority: Literal[False] = False
    grants_completion_authority: Literal[False] = False

    @field_validator(
        "authenticated_human_required", "trusted_eligibility_required",
        "author_and_bot_excluded", "current_request_required",
        "complete_review_set_required", "review_instance_binding_required",
        "unexpired_decision_required", "grants_merge_authority",
        "grants_completion_authority", mode="before",
    )
    @classmethod
    def _require_json_boolean(cls, value: object) -> object:
        # Literal[True/False] otherwise accepts numeric 1/0, unlike JSON Schema.
        if type(value) is not bool:
            raise ValueError("postcondition flags must be JSON booleans")
        return value


class HumanReviewRequestV1(BaseModel):
    """Deterministic evidence to show a person; never an authorization receipt.

    The external decision must additionally bind the trusted review instance
    (for example a GitHub PR number). The static request does not invent that
    provider-owned identity from a branch name or environment variable.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.human_review_request/v1"] = (
        HUMAN_REVIEW_REQUEST_SCHEMA_VERSION
    )
    review_request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    request_class: Literal["documentation_quality_complete_evidence"] = (
        "documentation_quality_complete_evidence"
    )
    repository_id: str = Field(min_length=1)
    verification_request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    subject_id: str = Field(pattern=CONTENT_ID_PATTERN)
    input_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    engine_requirement_id: str = Field(pattern=CONTENT_ID_PATTERN)
    decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    base_commit_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    merge_base_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    base_tree_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    head_tree_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    source_head_commit_sha: str = Field(pattern=GIT_OBJECT_PATTERN)
    decision: Literal["review_required"] = "review_required"
    review_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    review_items: list[HumanAuthorizationReviewItemV1] = Field(min_length=1)
    questions: list[HumanReviewQuestionV1] = Field(min_length=1)
    postcondition: HumanReviewPostconditionV1 = Field(default_factory=HumanReviewPostconditionV1)

    @model_validator(mode="after")
    def _identity_and_scope_agree(self) -> HumanReviewRequestV1:
        self.review_items = canonical_review_items(self.review_items)
        if self.review_set_id != review_set_id(self.review_items):
            raise ValueError("review_set_id must bind the complete review scope")
        if [q.review_item_id for q in self.questions] != [i.review_item_id for i in self.review_items]:
            raise ValueError("questions must cover the complete canonical review scope in order")
        payload = self.model_dump(mode="json", exclude={"schema_version", "review_request_id"})
        if self.review_request_id != content_id(payload):
            raise ValueError("review_request_id must hash the complete request")
        return self


def build_human_review_request(**fields: object) -> HumanReviewRequestV1:
    """Fill defaults before hashing; readers still validate every supplied id."""

    provisional = HumanReviewRequestV1.model_construct(**fields)
    payload = provisional.model_dump(mode="json", exclude={"schema_version", "review_request_id"})
    return HumanReviewRequestV1.model_validate({**payload, "review_request_id": content_id(payload)})
