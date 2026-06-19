from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate import __version__

VERIFY_RUN_SCHEMA_VERSION = "shipgate.verify_run/v1"


class VerifyRunTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "agents-shipgate"
    version: str = __version__


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


class VerifyRunOutcome(BaseModel):
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


class VerifyRunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.verify_run/v1"] = VERIFY_RUN_SCHEMA_VERSION
    run_id: str
    tool: VerifyRunTool = Field(default_factory=VerifyRunTool)
    subject: VerifyRunSubject
    inputs: VerifyRunInputs
    outcome: VerifyRunOutcome
    artifacts: dict[str, VerifyRunArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _run_id_matches_identity(self) -> VerifyRunArtifact:
        expected = compute_verify_run_id(
            subject=self.subject,
            inputs=self.inputs,
            outcome=self.outcome,
            tool=self.tool,
        )
        if self.run_id != expected:
            raise ValueError(
                "VerifyRunArtifact.run_id must be the stable hash of "
                "tool, subject, inputs, and outcome."
            )
        return self


def compute_verify_run_id(
    *,
    subject: VerifyRunSubject,
    inputs: VerifyRunInputs,
    outcome: VerifyRunOutcome,
    tool: VerifyRunTool | None = None,
) -> str:
    payload = {
        "tool": (tool or VerifyRunTool()).model_dump(mode="json"),
        "subject": subject.model_dump(mode="json"),
        "inputs": inputs.model_dump(mode="json"),
        "outcome": outcome.model_dump(mode="json"),
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
    resolved_tool = tool or VerifyRunTool()
    return VerifyRunArtifact(
        run_id=compute_verify_run_id(
            subject=subject,
            inputs=inputs,
            outcome=outcome,
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
    "VerifyRunArtifactRef",
    "VerifyRunInputs",
    "VerifyRunOutcome",
    "VerifyRunPolicyPack",
    "VerifyRunSubject",
    "VerifyRunTool",
    "build_verify_run_artifact",
    "compute_verify_run_id",
]
