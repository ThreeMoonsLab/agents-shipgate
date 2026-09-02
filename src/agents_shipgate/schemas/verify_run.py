from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate import __version__
from agents_shipgate.schemas.agent_control import AgentControl, FrozenAgentControl
from agents_shipgate.schemas.verification_identity import (
    CONTENT_ID_PATTERN,
    VerificationExecutor,
    VerificationPlan,
    content_id,
)

VERIFY_RUN_SCHEMA_VERSION = "shipgate.verify_run/v5"


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
                }
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

    @model_validator(mode="after")
    def _control_projects_outcome(self) -> VerifyRunOutcome:
        expected_applicability = (
            "verified"
            if self.decision is not None
            else {
                "not_run": "not_evaluated",
                "skipped": "not_applicable",
                "failed": "failed",
            }.get(self.execution, "not_evaluated")
        )
        if self.applicability != expected_applicability:
            raise ValueError("verify-run applicability contradicts execution")
        if self.decision is not None and self.execution != "succeeded":
            raise ValueError("verify-run decisions require succeeded execution")
        expected_verdict = (
            {
                "passed": "mergeable",
                "review_required": "human_review_required",
                "insufficient_evidence": "insufficient_evidence",
                "blocked": "blocked",
            }.get(self.decision, "human_review_required")
            if self.decision is not None
            else ("mergeable" if self.execution == "skipped" else "unknown")
        )
        if self.merge_verdict != expected_verdict:
            raise ValueError("verify-run merge verdict contradicts its decision")
        expected = bool(
            self.decision == "passed"
            or (self.decision is None and self.applicability == "not_applicable")
        )
        if self.can_merge_without_human != expected:
            raise ValueError("verify-run merge authority must project from the outcome")
        if self.control.completion_allowed != expected:
            raise ValueError("verify-run control must exactly project merge authority")
        if not self.control.completion_allowed and self.control.permissions.publishes:
            # Publication asserts an evaluated change. Bind it to the same
            # substrate the verifier does, keyed on the permission vector so an
            # agent repair route is held to the identical standard.
            if self.execution != "succeeded" or self.decision is None:
                raise ValueError(
                    "publication authority requires a succeeded verify-run with a decision"
                )
            if self.decision == "blocked" and not self.declaration_continuation:
                raise ValueError("a blocked verify-run cannot authorize publication")
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


class VerifyRunArtifact(BaseModel):
    """Current run projection bound to a content-addressed request plan."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verify_run/v5"] = VERIFY_RUN_SCHEMA_VERSION
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
        if self.run_id != self.request_id:
            raise ValueError("verify-run run_id must be the exact deprecated request_id alias")
        if self.request_id != self.plan.request_id:
            raise ValueError("verify-run request_id must match its verification plan")
        if self.executor.engine_requirement_id != self.plan.engine.engine_requirement_id:
            raise ValueError("verify-run executor does not satisfy the plan engine")
        if len(set(self.unit_result_ids)) != len(self.unit_result_ids):
            raise ValueError("verify-run unit_result_ids must be unique")
        if any(not re.fullmatch(CONTENT_ID_PATTERN, value) for value in self.unit_result_ids):
            raise ValueError("verify-run unit_result_ids must be SHA-256 content IDs")
        expected = content_id(
            {
                "request_id": self.request_id,
                "unit_result_ids": sorted(self.unit_result_ids),
                "decision": self.outcome.decision,
                "merge_verdict": self.outcome.merge_verdict,
                "can_merge_without_human": self.outcome.can_merge_without_human,
            }
        )
        if self.decision_id != expected:
            raise ValueError("verify-run decision_id must hash its request, units, and outcome")
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


__all__ = [
    "VERIFY_RUN_SCHEMA_VERSION",
    "VerifyRunArtifact",
    "VerifyRunArtifactV1",
    "VerifyRunArtifactV2",
    "VerifyRunArtifactRef",
    "VerifyRunInputs",
    "VerifyRunOutcome",
    "VerifyRunOutcomeV1",
    "VerifyRunPolicyPack",
    "VerifyRunSubject",
    "VerifyRunTool",
    "build_verify_run_artifact",
    "compute_verify_run_id",
]
