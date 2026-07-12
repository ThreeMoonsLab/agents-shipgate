from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate import __version__
from agents_shipgate.schemas.agent_control import AgentControl

VERIFY_RUN_SCHEMA_VERSION = "shipgate.verify_run/v2"


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
        return self


class VerifyRunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verify_run/v2"] = VERIFY_RUN_SCHEMA_VERSION
    run_id: str
    tool: VerifyRunTool
    subject: VerifyRunSubject
    inputs: VerifyRunInputs
    outcome: VerifyRunOutcome
    artifacts: dict[str, VerifyRunArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _run_id_matches_identity(self) -> VerifyRunArtifact:
        expected = compute_verify_run_id(subject=self.subject, inputs=self.inputs, tool=self.tool)
        if self.run_id != expected:
            raise ValueError(
                "VerifyRunArtifact.run_id must be the stable hash of tool, subject, and inputs."
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
    subject: VerifyRunSubject,
    inputs: VerifyRunInputs,
    outcome: VerifyRunOutcome,
    artifacts: dict[str, VerifyRunArtifactRef],
    tool: VerifyRunTool | None = None,
) -> VerifyRunArtifact:
    resolved_tool = tool or VerifyRunTool(version=__version__)
    return VerifyRunArtifact(
        run_id=compute_verify_run_id(
            subject=subject,
            inputs=inputs,
            tool=resolved_tool,
        ),
        tool=resolved_tool,
        subject=subject,
        inputs=inputs,
        outcome=outcome,
        artifacts=artifacts,
    )


__all__ = [
    "VERIFY_RUN_SCHEMA_VERSION",
    "VerifyRunArtifact",
    "VerifyRunArtifactV1",
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
