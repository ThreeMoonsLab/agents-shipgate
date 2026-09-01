"""Content-addressed identities for reproducible verification execution.

The models in this module deliberately separate a verification request from an
execution attempt and from the eventual decision/artifact closure.  All
authoritative identifiers are SHA-256 hashes of canonical JSON payloads.  An
``attempt_id`` is diagnostic only and is never accepted as a trust binding.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_shipgate.schemas.content_identity import (
    CONTENT_ID_PATTERN,
    GIT_OBJECT_PATTERN,
    canonical_json,
    content_id,
    validate_portable_path,
)
from agents_shipgate.schemas.manifest_provenance import ManifestProvenance

VERIFICATION_PLAN_V1_SCHEMA_VERSION = "shipgate.verification_plan/v1"
VERIFICATION_PLAN_SCHEMA_VERSION = "shipgate.verification_plan/v2"
VERIFICATION_UNIT_RESULT_SCHEMA_VERSION = "shipgate.verification_unit_result/v1"
VERIFICATION_ARTIFACT_MANIFEST_SCHEMA_VERSION = "shipgate.verification_artifact_manifest/v1"
VERIFICATION_RECEIPT_V1_SCHEMA_VERSION = "shipgate.verification_receipt/v1"
VERIFICATION_RECEIPT_SCHEMA_VERSION = "shipgate.verification_receipt/v2"


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, Any]:
    return model.model_dump(
        mode="json",
        exclude={identity_field, "schema_version"},
        exclude_none=False,
    )


_validate_portable_path = validate_portable_path


class VerificationBlob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=CONTENT_ID_PATTERN)
    size_bytes: int = Field(ge=0)
    source: Literal[
        "git_blob",
        "worktree",
        "generated",
        "external_input",
        "artifact",
    ]
    git_blob_oid: str | None = None

    _path_is_portable = field_validator("path")(_validate_portable_path)


class VerificationGitSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str
    base_ref: str | None = None
    base_commit_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    base_tree_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    head_ref: str
    source_head_commit_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    head_commit_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    head_tree_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    merge_base_sha: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    snapshot_kind: Literal["committed_tree", "worktree_overlay"]
    worktree_overlay_sha256: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)


class VerificationSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(pattern=CONTENT_ID_PATTERN)
    git: VerificationGitSubject

    @model_validator(mode="after")
    def _subject_id_is_content_addressed(self) -> VerificationSubject:
        expected = content_id(self.git)
        if self.subject_id != expected:
            raise ValueError("subject_id must hash the resolved Git subject")
        return self


class VerificationInputSetV1(BaseModel):
    """Frozen input-set reader for verification-plan v1.

    v1 predates manifest provenance. Its content identity must continue to hash
    exactly the fields published in the v1 schema; adding a defaulted field
    here would rewrite every historical ``input_set_id``.
    """

    model_config = ConfigDict(extra="forbid")

    input_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    evaluation_date: str
    config: VerificationBlob
    diff: VerificationBlob
    baseline: VerificationBlob | None = None
    diff_from: VerificationBlob | None = None
    policy_packs: list[VerificationBlob] = Field(default_factory=list)
    tool_sources: list[VerificationBlob] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    changed_files: list[VerificationBlob] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _input_set_id_is_content_addressed(self) -> VerificationInputSetV1:
        _validate_input_set_collections(self)
        expected = content_id(_identity_payload(self, "input_set_id"))
        if self.input_set_id != expected:
            raise ValueError("input_set_id must hash the complete normalized input set")
        return self


def _validate_input_set_collections(value: VerificationInputSetV1 | VerificationInputSet) -> None:
    for path in value.changed_paths:
        _validate_portable_path(path)
    if value.changed_paths != sorted(set(value.changed_paths)):
        raise ValueError("changed_paths must be sorted and unique")
    for name, blobs in (
        ("policy_packs", value.policy_packs),
        ("tool_sources", value.tool_sources),
        ("changed_files", value.changed_files),
    ):
        paths = [blob.path for blob in blobs]
        if paths != sorted(set(paths)):
            raise ValueError(f"{name} paths must be sorted and unique")


class VerificationInputSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    evaluation_date: str
    manifest_provenance: ManifestProvenance
    config: VerificationBlob
    diff: VerificationBlob
    baseline: VerificationBlob | None = None
    diff_from: VerificationBlob | None = None
    policy_packs: list[VerificationBlob] = Field(default_factory=list)
    tool_sources: list[VerificationBlob] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    changed_files: list[VerificationBlob] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _input_set_id_is_content_addressed(self) -> VerificationInputSet:
        _validate_input_set_collections(self)
        expected = content_id(_identity_payload(self, "input_set_id"))
        if self.input_set_id != expected:
            raise ValueError("input_set_id must hash the complete normalized input set")
        return self


class VerificationEngineRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_requirement_id: str = Field(pattern=CONTENT_ID_PATTERN)
    package: str = "agents-shipgate"
    version: str
    python_implementation: str
    python_version: str
    platform: str
    engine_distribution_sha256: str = Field(pattern=CONTENT_ID_PATTERN)
    dependency_set_sha256: str = Field(pattern=CONTENT_ID_PATTERN)
    adapter_set_sha256: str = Field(pattern=CONTENT_ID_PATTERN)
    plugin_set_sha256: str = Field(pattern=CONTENT_ID_PATTERN)
    policy_catalog_sha256: str = Field(pattern=CONTENT_ID_PATTERN)

    @model_validator(mode="after")
    def _engine_id_is_content_addressed(self) -> VerificationEngineRequirement:
        expected = content_id(_identity_payload(self, "engine_requirement_id"))
        if self.engine_requirement_id != expected:
            raise ValueError("engine_requirement_id must hash the engine requirements")
        return self


class VerificationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=CONTENT_ID_PATTERN)
    kind: Literal["extract", "normalize", "evaluate"]
    shard: int = Field(ge=0)
    shard_count: int = Field(ge=1)
    input_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _task_id_is_content_addressed(self) -> VerificationTask:
        if self.input_paths != sorted(set(self.input_paths)):
            raise ValueError("task input_paths must be sorted and unique")
        expected = content_id(_identity_payload(self, "task_id"))
        if self.task_id != expected:
            raise ValueError("task_id must hash the normalized task")
        return self


class VerificationPlanV1(BaseModel):
    """Frozen verification-plan v1 reader.

    The v1 request identity points at the v1 input-set identity. It must not be
    normalized into a v2 plan because doing so would silently mint a different
    request under historical bytes.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verification_plan/v1"] = (
        VERIFICATION_PLAN_V1_SCHEMA_VERSION
    )
    request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    subject: VerificationSubject
    inputs: VerificationInputSetV1
    engine: VerificationEngineRequirement
    tasks: list[VerificationTask] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def _request_id_is_content_addressed(self) -> VerificationPlanV1:
        _validate_plan_identity(self)
        return self


def _validate_plan_identity(value: VerificationPlanV1 | VerificationPlan) -> None:
    if len({task.task_id for task in value.tasks}) != len(value.tasks):
        raise ValueError("verification plan task IDs must be unique")
    payload = {
        "subject_id": value.subject.subject_id,
        "input_set_id": value.inputs.input_set_id,
        "engine_requirement_id": value.engine.engine_requirement_id,
        "task_ids": [task.task_id for task in value.tasks],
    }
    expected = content_id(payload)
    if value.request_id != expected:
        raise ValueError("request_id must hash the verification plan identity")


class VerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verification_plan/v2"] = VERIFICATION_PLAN_SCHEMA_VERSION
    request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    subject: VerificationSubject
    inputs: VerificationInputSet
    engine: VerificationEngineRequirement
    tasks: list[VerificationTask] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def _request_id_is_content_addressed(self) -> VerificationPlan:
        _validate_plan_identity(self)
        return self


class VerificationExecutor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor_id: str = Field(pattern=CONTENT_ID_PATTERN)
    engine_requirement_id: str = Field(pattern=CONTENT_ID_PATTERN)
    runtime_sha256: str = Field(pattern=CONTENT_ID_PATTERN)

    @model_validator(mode="after")
    def _executor_id_is_content_addressed(self) -> VerificationExecutor:
        expected = content_id(_identity_payload(self, "executor_id"))
        if self.executor_id != expected:
            raise ValueError("executor_id must hash the executor descriptor")
        return self


class VerificationUnitResult(BaseModel):
    """Decision-free normalized worker output consumed by the assembler."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verification_unit_result/v1"] = (
        VERIFICATION_UNIT_RESULT_SCHEMA_VERSION
    )
    unit_result_id: str = Field(pattern=CONTENT_ID_PATTERN)
    request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    task_id: str = Field(pattern=CONTENT_ID_PATTERN)
    executor: VerificationExecutor
    status: Literal["succeeded", "failed"]
    normalized_ir: dict[str, Any] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unit_result_id_is_content_addressed(self) -> VerificationUnitResult:
        forbidden = {"decision", "merge_verdict", "can_merge_without_human", "control"}
        if _contains_forbidden_key(self.normalized_ir, forbidden):
            raise ValueError("workers may emit normalized IR, never release decisions")
        expected = content_id(_identity_payload(self, "unit_result_id"))
        if self.unit_result_id != expected:
            raise ValueError("unit_result_id must hash the normalized worker result")
        return self


def _contains_forbidden_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(forbidden.intersection(value)) or any(
            _contains_forbidden_key(item, forbidden) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(item, forbidden) for item in value)
    return False


class VerificationArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=CONTENT_ID_PATTERN)
    size_bytes: int = Field(ge=0)
    media_type: str

    _path_is_portable = field_validator("path")(_validate_portable_path)


class VerificationArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verification_artifact_manifest/v1"] = (
        VERIFICATION_ARTIFACT_MANIFEST_SCHEMA_VERSION
    )
    artifact_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    artifacts: dict[str, VerificationArtifactRef] = Field(min_length=1)

    @model_validator(mode="after")
    def _artifact_set_id_is_content_addressed(self) -> VerificationArtifactManifest:
        expected = content_id(_identity_payload(self, "artifact_set_id"))
        if self.artifact_set_id != expected:
            raise ValueError("artifact_set_id must hash the complete artifact manifest")
        return self


class VerificationReceiptV1(BaseModel):
    """Frozen terminal receipt reader for the provenance-free v1 contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verification_receipt/v1"] = (
        VERIFICATION_RECEIPT_V1_SCHEMA_VERSION
    )
    receipt_id: str = Field(pattern=CONTENT_ID_PATTERN)
    request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    subject_id: str = Field(pattern=CONTENT_ID_PATTERN)
    input_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    engine_requirement_id: str = Field(pattern=CONTENT_ID_PATTERN)
    executor_id: str = Field(pattern=CONTENT_ID_PATTERN)
    decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    artifact_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    unit_result_ids: list[str] = Field(min_length=1)
    attempt_id: str | None = None
    decision: (
        Literal[
            "passed",
            "review_required",
            "insufficient_evidence",
            "blocked",
        ]
        | None
    ) = None
    merge_verdict: Literal[
        "mergeable",
        "human_review_required",
        "insufficient_evidence",
        "blocked",
        "unknown",
    ]
    can_merge_without_human: bool
    artifact_manifest: VerificationArtifactManifest

    @model_validator(mode="after")
    def _receipt_closes_one_identity_graph(self) -> VerificationReceiptV1:
        _validate_receipt_identity(self)
        return self


def _validate_receipt_identity(value: VerificationReceiptV1 | VerificationReceipt) -> None:
    if len(set(value.unit_result_ids)) != len(value.unit_result_ids):
        raise ValueError("receipt unit_result_ids must be unique")
    if any(not re.fullmatch(CONTENT_ID_PATTERN, item) for item in value.unit_result_ids):
        raise ValueError("receipt unit_result_ids must be SHA-256 content IDs")
    expected_decision_id = content_id(
        {
            "request_id": value.request_id,
            "unit_result_ids": sorted(value.unit_result_ids),
            "decision": value.decision,
            "merge_verdict": value.merge_verdict,
            "can_merge_without_human": value.can_merge_without_human,
        }
    )
    if value.decision_id != expected_decision_id:
        raise ValueError("receipt decision_id must hash its request, units, and outcome")
    decision_projection = {
        "passed": ("mergeable", True),
        "review_required": ("human_review_required", False),
        "insufficient_evidence": ("insufficient_evidence", False),
        "blocked": ("blocked", False),
    }
    if value.decision in decision_projection:
        expected_merge, expected_can_merge = decision_projection[value.decision]
        if (value.merge_verdict, value.can_merge_without_human) != (
            expected_merge,
            expected_can_merge,
        ):
            raise ValueError("receipt outcome contradicts its release decision")
    elif (value.merge_verdict, value.can_merge_without_human) not in {
        ("mergeable", True),
        ("unknown", False),
    }:
        raise ValueError("receipt outcome contradicts a decision-free execution")
    if value.artifact_manifest.request_id != value.request_id:
        raise ValueError("receipt and artifact manifest request_id disagree")
    if value.artifact_manifest.decision_id != value.decision_id:
        raise ValueError("receipt and artifact manifest decision_id disagree")
    if value.artifact_manifest.artifact_set_id != value.artifact_set_id:
        raise ValueError("receipt and artifact manifest artifact_set_id disagree")
    expected = content_id(
        value.model_dump(
            mode="json",
            exclude={"receipt_id", "schema_version", "attempt_id"},
            exclude_none=False,
        )
    )
    if value.receipt_id != expected:
        raise ValueError("receipt_id must hash the complete terminal receipt")


class VerificationReceipt(BaseModel):
    """Terminal closure record. It is valid only after every artifact exists."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {
                "properties": {
                    "manifest_provenance": {
                        "properties": {"release_authoritative": {"const": False}},
                        "required": ["release_authoritative"],
                    }
                },
                "required": ["manifest_provenance"],
            },
            "then": {
                "properties": {
                    "decision": {"not": {"const": "passed"}},
                    "merge_verdict": {"not": {"const": "mergeable"}},
                    "can_merge_without_human": {"const": False},
                },
                "required": [
                    "decision",
                    "merge_verdict",
                    "can_merge_without_human",
                ],
            },
        },
    )

    schema_version: Literal["shipgate.verification_receipt/v2"] = (
        VERIFICATION_RECEIPT_SCHEMA_VERSION
    )
    receipt_id: str = Field(pattern=CONTENT_ID_PATTERN)
    request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    subject_id: str = Field(pattern=CONTENT_ID_PATTERN)
    input_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    engine_requirement_id: str = Field(pattern=CONTENT_ID_PATTERN)
    executor_id: str = Field(pattern=CONTENT_ID_PATTERN)
    decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    artifact_set_id: str = Field(pattern=CONTENT_ID_PATTERN)
    manifest_provenance: ManifestProvenance
    unit_result_ids: list[str] = Field(min_length=1)
    attempt_id: str | None = None
    decision: (
        Literal[
            "passed",
            "review_required",
            "insufficient_evidence",
            "blocked",
        ]
        | None
    ) = None
    merge_verdict: Literal[
        "mergeable",
        "human_review_required",
        "insufficient_evidence",
        "blocked",
        "unknown",
    ]
    can_merge_without_human: bool
    artifact_manifest: VerificationArtifactManifest

    @model_validator(mode="after")
    def _receipt_closes_one_identity_graph(self) -> VerificationReceipt:
        _validate_receipt_identity(self)
        if not self.manifest_provenance.release_authoritative and (
            self.decision == "passed"
            or self.merge_verdict == "mergeable"
            or self.can_merge_without_human
        ):
            raise ValueError("non-authoritative manifest receipt cannot carry release authority")
        return self


ReadableVerificationPlan = VerificationPlanV1 | VerificationPlan
ReadableVerificationReceipt = VerificationReceiptV1 | VerificationReceipt


def _versioned_object(payload: Any, *, label: str) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        value = payload.model_dump(mode="json")
    elif isinstance(payload, (str, bytes, bytearray)):
        try:
            value = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{label} is not valid JSON: {exc}") from exc
    elif isinstance(payload, dict):
        value = dict(payload)
    else:
        raise ValueError(f"{label} must be a JSON object")
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def load_verification_plan(payload: Any) -> ReadableVerificationPlan:
    """Dispatch a plan by its immutable schema discriminator.

    The v1 path validates the historical content identities in place. It does
    not add provenance or rewrite the payload under v2.
    """

    value = _versioned_object(payload, label="verification plan")
    version = value.get("schema_version")
    if version == VERIFICATION_PLAN_V1_SCHEMA_VERSION:
        return VerificationPlanV1.model_validate(value)
    if version == VERIFICATION_PLAN_SCHEMA_VERSION:
        return VerificationPlan.model_validate(value)
    raise ValueError(
        "unsupported verification plan schema_version: "
        f"{version!r}; expected {VERIFICATION_PLAN_V1_SCHEMA_VERSION!r} or "
        f"{VERIFICATION_PLAN_SCHEMA_VERSION!r}"
    )


def load_verification_receipt(payload: Any) -> ReadableVerificationReceipt:
    """Dispatch a receipt without upgrading its content-addressed identity."""

    value = _versioned_object(payload, label="verification receipt")
    version = value.get("schema_version")
    if version == VERIFICATION_RECEIPT_V1_SCHEMA_VERSION:
        return VerificationReceiptV1.model_validate(value)
    if version == VERIFICATION_RECEIPT_SCHEMA_VERSION:
        return VerificationReceipt.model_validate(value)
    raise ValueError(
        "unsupported verification receipt schema_version: "
        f"{version!r}; expected {VERIFICATION_RECEIPT_V1_SCHEMA_VERSION!r} or "
        f"{VERIFICATION_RECEIPT_SCHEMA_VERSION!r}"
    )


__all__ = [
    "VERIFICATION_ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "VERIFICATION_PLAN_SCHEMA_VERSION",
    "VERIFICATION_PLAN_V1_SCHEMA_VERSION",
    "VERIFICATION_RECEIPT_SCHEMA_VERSION",
    "VERIFICATION_RECEIPT_V1_SCHEMA_VERSION",
    "VERIFICATION_UNIT_RESULT_SCHEMA_VERSION",
    "VerificationArtifactManifest",
    "VerificationArtifactRef",
    "VerificationBlob",
    "VerificationEngineRequirement",
    "VerificationExecutor",
    "VerificationGitSubject",
    "VerificationInputSet",
    "VerificationInputSetV1",
    "VerificationPlan",
    "VerificationPlanV1",
    "VerificationReceipt",
    "VerificationReceiptV1",
    "VerificationSubject",
    "VerificationTask",
    "VerificationUnitResult",
    "canonical_json",
    "content_id",
    "load_verification_plan",
    "load_verification_receipt",
    "validate_portable_path",
]
