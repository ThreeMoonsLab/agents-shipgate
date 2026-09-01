from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate import __version__
from agents_shipgate.schemas.agent_control import AgentControl, FrozenAgentControl
from agents_shipgate.schemas.manifest_provenance import ManifestProvenance
from agents_shipgate.schemas.verification_identity import (
    CONTENT_ID_PATTERN,
    VerificationExecutor,
    VerificationPlan,
    VerificationPlanV1,
    content_id,
)

VERIFY_RUN_V5_SCHEMA_VERSION = "shipgate.verify_run/v5"
VERIFY_RUN_SCHEMA_VERSION = "shipgate.verify_run/v6"


class VerifyRunTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "agents-shipgate"
    version: str


class VerifyRunSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["git_diff"] = "git_diff"
    workspace: str = "."
    config: str
    base_ref: str | None = None
    head_ref: str = "HEAD"
    base_tree_sha: str | None = None
    head_tree_sha: str | None = None


class VerifyRunPolicyPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    version: str | None = None
    path: str
    sha256: str | None = None
    sha256_status: Literal["unpinned", "verified"] = "unpinned"
    rule_count: int | None = None


class VerifyRunInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_sha256: str | None = None
    baseline_sha256: str | None = None
    policy_packs: list[VerifyRunPolicyPack] = Field(default_factory=list)
    plugins_enabled: bool | None = None
    no_heuristics: bool = False
    ci_mode: str | None = None
    fail_on: list[str] = Field(default_factory=list)


class VerifyRunOutcomeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_code: int
    base_status: str
    head_status: str
    decision: str | None = None
    merge_verdict: str
    can_merge_without_human: bool = False


class VerifyRunArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str | None = None


class VerifyRunArtifactV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verify_run/v1"] = "shipgate.verify_run/v1"
    run_id: str
    tool: VerifyRunTool
    subject: VerifyRunSubject
    inputs: VerifyRunInputs
    outcome: VerifyRunOutcomeV1
    artifacts: dict[str, VerifyRunArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _run_id_matches_identity(self) -> VerifyRunArtifactV1:
        expected = compute_verify_run_id(
            subject=self.subject,
            inputs=self.inputs,
            tool=self.tool,
        )
        if self.run_id != expected:
            raise ValueError(
                "VerifyRunArtifact.run_id must be the stable hash of tool, subject, and inputs."
            )
        return self


class VerifyRunOutcomeV5(BaseModel):
    """Frozen provenance-free outcome reader for verify-run v5."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int
    base_status: str
    execution: Literal["not_run", "succeeded", "skipped", "failed"]
    applicability: Literal["not_evaluated", "verified", "not_applicable", "failed"]
    decision: str | None = None
    merge_verdict: str
    can_merge_without_human: bool = False
    control: AgentControl
    declaration_continuation: bool = False

    @model_validator(mode="after")
    def _control_projects_outcome(self) -> VerifyRunOutcomeV5:
        _validate_verify_run_outcome(self)
        return self


def _validate_verify_run_outcome(
    value: VerifyRunOutcomeV5 | VerifyRunOutcome,
) -> None:
    expected_applicability = (
        "verified"
        if value.decision is not None
        else {
            "not_run": "not_evaluated",
            "skipped": "not_applicable",
            "failed": "failed",
        }.get(value.execution, "not_evaluated")
    )
    if value.applicability != expected_applicability:
        raise ValueError("verify-run applicability contradicts execution")
    if value.decision is not None and value.execution != "succeeded":
        raise ValueError("verify-run decisions require succeeded execution")
    expected_verdict = (
        {
            "passed": "mergeable",
            "review_required": "human_review_required",
            "insufficient_evidence": "insufficient_evidence",
            "blocked": "blocked",
        }.get(value.decision, "human_review_required")
        if value.decision is not None
        else ("mergeable" if value.execution == "skipped" else "unknown")
    )
    if value.merge_verdict != expected_verdict:
        raise ValueError("verify-run merge verdict contradicts its decision")
    expected = bool(
        value.decision == "passed"
        or (value.decision is None and value.applicability == "not_applicable")
    )
    if value.can_merge_without_human != expected:
        raise ValueError("verify-run merge authority must project from the outcome")
    if value.control.completion_allowed != expected:
        raise ValueError("verify-run control must exactly project merge authority")
    if not value.control.completion_allowed and value.control.permissions.publishes:
        if value.execution != "succeeded" or value.decision is None:
            raise ValueError(
                "publication authority requires a succeeded verify-run with a decision"
            )
        if value.decision == "blocked" and not value.declaration_continuation:
            raise ValueError("a blocked verify-run cannot authorize publication")


class VerifyRunOutcome(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            # A second, independent conditional: `if/then/else` above pins the
            # merge-authority projection; this one pins publication to an
            # evaluated, non-blocked outcome.
            "allOf": [
                {
                    # See ``VerifierArtifact``: the continuation is part of the
                    # condition, and an artifact that omits the field still
                    # matches and is still held to the original requirement.
                    "if": {
                        "properties": {
                            "declaration_continuation": {"not": {"const": True}},
                            "control": {
                                "properties": {
                                    "completion_allowed": {"const": False},
                                    "permissions": {
                                        "properties": {"update_pr": {"const": True}},
                                        "required": ["update_pr"],
                                    },
                                },
                                "required": ["completion_allowed", "permissions"],
                            },
                        },
                        "required": ["control"],
                    },
                    "then": {
                        "required": ["execution", "decision"],
                        "properties": {
                            "execution": {"const": "succeeded"},
                            "decision": {"type": "string", "not": {"const": "blocked"}},
                        },
                    },
                },
                {
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
                            "control": {
                                "properties": {
                                    "state": {"not": {"const": "complete"}},
                                    "completion_allowed": {"const": False},
                                    "permissions": {
                                        "properties": {
                                            "merge": {"const": False},
                                            "report_complete": {"const": False},
                                        },
                                        "required": ["merge", "report_complete"],
                                    },
                                },
                                "required": [
                                    "state",
                                    "completion_allowed",
                                    "permissions",
                                ],
                            },
                        },
                        "required": [
                            "decision",
                            "merge_verdict",
                            "can_merge_without_human",
                            "control",
                        ],
                    },
                },
            ],
            "if": {
                "properties": {"can_merge_without_human": {"const": True}},
                "required": ["can_merge_without_human"],
            },
            "then": {
                "properties": {
                    "control": {
                        "properties": {"state": {"const": "complete"}},
                        "required": ["state"],
                    }
                },
                "oneOf": [
                    {
                        "properties": {
                            "execution": {"const": "succeeded"},
                            "applicability": {"const": "verified"},
                            "decision": {"const": "passed"},
                        }
                    },
                    {
                        "properties": {
                            "execution": {"const": "skipped"},
                            "applicability": {"const": "not_applicable"},
                            "decision": {"type": "null"},
                        }
                    },
                ],
            },
            "else": {
                "properties": {
                    "control": {
                        "properties": {
                            "state": {
                                "enum": [
                                    "agent_action_required",
                                    "review_publishable",
                                    "human_review_required",
                                ]
                            }
                        },
                        "required": ["state"],
                    }
                }
            },
        },
    )

    exit_code: int
    base_status: str
    execution: Literal["not_run", "succeeded", "skipped", "failed"]
    applicability: Literal["not_evaluated", "verified", "not_applicable", "failed"]
    decision: str | None = None
    merge_verdict: str
    can_merge_without_human: bool = False
    control: AgentControl
    #: Carried from the verifier: this run's trust-root delta is a declaration
    #: continuation, receipt-pinned on both sides, so a blocked decision may
    #: authorize publication and nothing more (#429).
    declaration_continuation: bool = False
    manifest_provenance: ManifestProvenance

    @model_validator(mode="after")
    def _control_projects_outcome(self) -> VerifyRunOutcome:
        _validate_verify_run_outcome(self)
        if not self.manifest_provenance.release_authoritative and (
            self.decision == "passed"
            or self.merge_verdict == "mergeable"
            or self.can_merge_without_human
            or self.control.state == "complete"
            or self.control.completion_allowed
            or self.control.permissions.merge
            or self.control.permissions.report_complete
        ):
            raise ValueError("non-authoritative manifest verify-run cannot carry release authority")
        return self


class FrozenVerifyRunOutcome(BaseModel):
    """The v2 outcome shape, snapshotted.

    ``VerifyRunOutcome`` is the *current* model and keeps evolving with the
    control union. Reusing it here made the frozen reader
    serialization-unstable: reading a valid v2 payload and dumping it back out
    added ``control.permissions`` and failed the unchanged v2 schema.
    """

    model_config = ConfigDict(extra="forbid")

    exit_code: int
    base_status: str
    execution: Literal["not_run", "succeeded", "skipped", "failed"]
    applicability: Literal["not_evaluated", "verified", "not_applicable", "failed"]
    decision: str | None = None
    merge_verdict: str
    can_merge_without_human: bool = False
    control: FrozenAgentControl


class VerifyRunArtifactV2(BaseModel):
    """Frozen reader for the pre-identity verify-run contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verify_run/v2"] = "shipgate.verify_run/v2"
    run_id: str
    tool: VerifyRunTool
    subject: VerifyRunSubject
    inputs: VerifyRunInputs
    outcome: FrozenVerifyRunOutcome
    artifacts: dict[str, VerifyRunArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _run_id_matches_identity(self) -> VerifyRunArtifactV2:
        expected = compute_verify_run_id(subject=self.subject, inputs=self.inputs, tool=self.tool)
        if self.run_id != expected:
            raise ValueError(
                "VerifyRunArtifact.run_id must be the stable hash of tool, subject, and inputs."
            )
        return self


class VerifyRunArtifactV5(BaseModel):
    """Frozen reader for the content-addressed, provenance-free v5 run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verify_run/v5"] = VERIFY_RUN_V5_SCHEMA_VERSION
    request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    run_id: str = Field(pattern=CONTENT_ID_PATTERN)
    plan: VerificationPlanV1
    executor: VerificationExecutor
    unit_result_ids: list[str] = Field(min_length=1)
    decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    outcome: VerifyRunOutcomeV5
    artifacts: dict[str, VerifyRunArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _identity_graph_is_consistent(self) -> VerifyRunArtifactV5:
        _validate_verify_run_identity(self)
        return self


def _validate_verify_run_identity(value: VerifyRunArtifactV5 | VerifyRunArtifact) -> None:
    if value.run_id != value.request_id:
        raise ValueError("verify-run run_id must be the exact deprecated request_id alias")
    if value.request_id != value.plan.request_id:
        raise ValueError("verify-run request_id must match its verification plan")
    if value.executor.engine_requirement_id != value.plan.engine.engine_requirement_id:
        raise ValueError("verify-run executor does not satisfy the plan engine")
    if len(set(value.unit_result_ids)) != len(value.unit_result_ids):
        raise ValueError("verify-run unit_result_ids must be unique")
    if any(not re.fullmatch(CONTENT_ID_PATTERN, item) for item in value.unit_result_ids):
        raise ValueError("verify-run unit_result_ids must be SHA-256 content IDs")
    expected = content_id(
        {
            "request_id": value.request_id,
            "unit_result_ids": sorted(value.unit_result_ids),
            "decision": value.outcome.decision,
            "merge_verdict": value.outcome.merge_verdict,
            "can_merge_without_human": value.outcome.can_merge_without_human,
        }
    )
    if value.decision_id != expected:
        raise ValueError("verify-run decision_id must hash its request, units, and outcome")


class VerifyRunArtifact(BaseModel):
    """Current run projection bound to a content-addressed request plan."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verify_run/v6"] = VERIFY_RUN_SCHEMA_VERSION
    request_id: str = Field(pattern=CONTENT_ID_PATTERN)
    # Deprecated for one compatibility cycle; it is an exact alias, never a
    # separately-computed identity.
    run_id: str = Field(pattern=CONTENT_ID_PATTERN)
    plan: VerificationPlan
    executor: VerificationExecutor
    unit_result_ids: list[str] = Field(min_length=1)
    decision_id: str = Field(pattern=CONTENT_ID_PATTERN)
    outcome: VerifyRunOutcome
    artifacts: dict[str, VerifyRunArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _identity_graph_is_consistent(self) -> VerifyRunArtifact:
        _validate_verify_run_identity(self)
        if self.outcome.manifest_provenance != self.plan.inputs.manifest_provenance:
            raise ValueError(
                "verify-run outcome manifest provenance disagrees with the verification plan"
            )
        return self


def compute_verify_run_id(
    *,
    subject: VerifyRunSubject,
    inputs: VerifyRunInputs,
    tool: VerifyRunTool | None = None,
) -> str:
    payload = {
        "tool": (tool or VerifyRunTool(version=__version__)).model_dump(mode="json"),
        "subject": subject.model_dump(mode="json"),
        "inputs": inputs.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def build_verify_run_artifact(
    *,
    plan: VerificationPlan,
    executor: VerificationExecutor,
    unit_result_ids: list[str],
    outcome: VerifyRunOutcome,
    artifacts: dict[str, VerifyRunArtifactRef],
) -> VerifyRunArtifact:
    decision_id = content_id(
        {
            "request_id": plan.request_id,
            "unit_result_ids": sorted(unit_result_ids),
            "decision": outcome.decision,
            "merge_verdict": outcome.merge_verdict,
            "can_merge_without_human": outcome.can_merge_without_human,
        }
    )
    return VerifyRunArtifact(
        request_id=plan.request_id,
        run_id=plan.request_id,
        plan=plan,
        executor=executor,
        unit_result_ids=sorted(unit_result_ids),
        decision_id=decision_id,
        outcome=outcome,
        artifacts=artifacts,
    )


ReadableVerifyRunArtifact = (
    VerifyRunArtifactV1 | VerifyRunArtifactV2 | VerifyRunArtifactV5 | VerifyRunArtifact
)


def load_verify_run_artifact(payload: object) -> ReadableVerifyRunArtifact:
    """Validate a verify-run under the schema version that minted its IDs."""

    if isinstance(payload, BaseModel):
        value = payload.model_dump(mode="json")
    elif isinstance(payload, (str, bytes, bytearray)):
        try:
            value = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"verify-run artifact is not valid JSON: {exc}") from exc
    elif isinstance(payload, dict):
        value = dict(payload)
    else:
        raise ValueError("verify-run artifact must be a JSON object")
    if not isinstance(value, dict):
        raise ValueError("verify-run artifact must be a JSON object")
    version = value.get("schema_version")
    readers: dict[str, type[BaseModel]] = {
        "shipgate.verify_run/v1": VerifyRunArtifactV1,
        "shipgate.verify_run/v2": VerifyRunArtifactV2,
        VERIFY_RUN_V5_SCHEMA_VERSION: VerifyRunArtifactV5,
        VERIFY_RUN_SCHEMA_VERSION: VerifyRunArtifact,
    }
    model = readers.get(str(version))
    if model is None:
        raise ValueError(
            "unsupported verify-run schema_version: "
            f"{version!r}; expected one of {', '.join(sorted(readers))}"
        )
    return model.model_validate(value)  # type: ignore[return-value]


__all__ = [
    "VERIFY_RUN_SCHEMA_VERSION",
    "VERIFY_RUN_V5_SCHEMA_VERSION",
    "VerifyRunArtifact",
    "VerifyRunArtifactV1",
    "VerifyRunArtifactV2",
    "VerifyRunArtifactV5",
    "VerifyRunArtifactRef",
    "VerifyRunInputs",
    "VerifyRunOutcome",
    "VerifyRunOutcomeV1",
    "VerifyRunOutcomeV5",
    "VerifyRunPolicyPack",
    "VerifyRunSubject",
    "VerifyRunTool",
    "build_verify_run_artifact",
    "compute_verify_run_id",
    "load_verify_run_artifact",
]
