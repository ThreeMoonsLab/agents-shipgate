from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.agent_control import FrozenAgentControl, freeze_agent_control
from agents_shipgate.schemas.agent_result import AgentResult, AgentResultV2
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultActor,
    AgentResultAffectedFile,
    AgentResultAgent,
    AgentResultDecision,
    AgentResultDiagnostic,
    AgentResultDiagnosticLevel,
    AgentResultHumanReview,
    AgentResultNextAction,
    AgentResultPolicy,
    AgentResultPolicySource,
    AgentResultRepair,
    AgentResultRiskLevel,
    AgentResultSubject,
    AgentResultTool,
    AgentResultTraceEvent,
    AgentResultV1,
    AgentResultViolatedRule,
)

CODEX_BOUNDARY_RESULT_SCHEMA_VERSION = "shipgate.codex_boundary_result/v2"

CodexBoundaryAgent = AgentResultAgent
CodexBoundaryDecision = AgentResultDecision
CodexBoundaryRiskLevel = AgentResultRiskLevel
CodexBoundaryActor = AgentResultActor
CodexBoundaryDiagnosticLevel = AgentResultDiagnosticLevel
CodexBoundaryPolicySource = AgentResultPolicySource
CodexBoundaryTool = AgentResultTool
CodexBoundarySubject = AgentResultSubject
CodexBoundaryNextAction = AgentResultNextAction
CodexBoundaryViolatedRule = AgentResultViolatedRule
CodexBoundaryAffectedFile = AgentResultAffectedFile
CodexBoundaryHumanReview = AgentResultHumanReview
CodexBoundaryRepair = AgentResultRepair
CodexBoundaryPolicy = AgentResultPolicy
CodexBoundaryDiagnostic = AgentResultDiagnostic
CodexBoundaryTraceEvent = AgentResultTraceEvent


class CodexBoundaryResultV1(AgentResultV1):
    """Local Codex boundary-result contract for ``shipgate check``.

    This deliberately shares the evolved field set and validators from the
    legacy ``agent_result_v1`` shape, but carries a boundary-specific schema
    version so consumers do not confuse local boundary checks with the verify
    controller artifact.
    """

    schema_version: Literal["shipgate.codex_boundary_result/v1"] = (
        "shipgate.codex_boundary_result/v1"
    )

    # ``verify_required`` (contract v10) is inherited from the shared
    # ``AgentResultV1`` base: ``check`` is boundary-only and never computes
    # the capability delta, so when the diff touches a tool surface the
    # evaluator escalates to ``decision="warn"`` and sets the flag — the
    # machine-readable form of "run ``agents-shipgate verify`` before
    # completion". Deterministic projection of the same deferral that emits
    # the ``capability_change_requires_verify`` /
    # ``undeclared_capability_surface`` diagnostics; no second verdict.


class CodexBoundaryResultV2(BaseModel):
    """Frozen, deprecated local boundary contract.

    Deliberately **not** a subclass of the current ``AgentResultV2``. A frozen
    wire contract that inherits from an evolving one is not frozen: pydantic
    serializes a subclass instance through the base type whenever the declared
    type is the base, so ``TypeAdapter(AgentResultV2).dump_python(...)`` — or
    any field annotated with the base — would emit whatever the live union
    happens to contain, bypassing a subclass serializer entirely. The same
    inheritance made ``model_json_schema()`` advertise the live union rather
    than the three states this contract published.

    The field set is a snapshot, and ``control`` is the frozen pre-contract-20
    union. Build one with :func:`freeze_codex_boundary_result`; nothing should
    construct it from scratch.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.codex_boundary_result/v2"] = (
        CODEX_BOUNDARY_RESULT_SCHEMA_VERSION
    )
    agent: AgentResultAgent = "codex"
    tool: AgentResultTool = Field(default_factory=AgentResultTool)
    subject: AgentResultSubject = Field(default_factory=AgentResultSubject)
    decision: AgentResultDecision
    risk_level: AgentResultRiskLevel
    audit_id: str
    policy_version: str
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    control: FrozenAgentControl
    repair: AgentResultRepair = Field(default_factory=AgentResultRepair)
    policy: AgentResultPolicy
    violated_rules: list[AgentResultViolatedRule] = Field(default_factory=list)
    affected_files: list[AgentResultAffectedFile] = Field(default_factory=list)
    required_reviewers: list[str] = Field(default_factory=list)
    explanation: str | None = None
    suggested_fixes: list[str] = Field(default_factory=list)
    agent_repair_instructions: list[str] = Field(default_factory=list)
    diagnostics: list[AgentResultDiagnostic] = Field(default_factory=list)
    trace: list[AgentResultTraceEvent] = Field(default_factory=list)
    source_artifacts: dict[str, str] = Field(default_factory=dict)
    release_decision: dict[str, Any] | None = None
    trigger: dict[str, Any] | None = None
    finding_fingerprints: list[str] = Field(default_factory=list)
    policy_snapshot_sha256: str | None = None


def freeze_codex_boundary_result(result: AgentResultV2) -> CodexBoundaryResultV2:
    """Project a current result onto the frozen deprecated contract."""

    payload = result.model_dump(mode="json", exclude={"schema_version", "control"})
    return CodexBoundaryResultV2(
        **payload,
        control=freeze_agent_control(result.control),
    )


CodexBoundaryResult = CodexBoundaryResultV2

__all__ = [
    "CODEX_BOUNDARY_RESULT_SCHEMA_VERSION",
    "AgentResult",
    "CodexBoundaryActor",
    "CodexBoundaryAffectedFile",
    "CodexBoundaryAgent",
    "CodexBoundaryDecision",
    "CodexBoundaryDiagnostic",
    "CodexBoundaryDiagnosticLevel",
    "CodexBoundaryHumanReview",
    "CodexBoundaryNextAction",
    "CodexBoundaryPolicy",
    "CodexBoundaryPolicySource",
    "CodexBoundaryRepair",
    "CodexBoundaryResult",
    "CodexBoundaryResultV1",
    "CodexBoundaryResultV2",
    "CodexBoundaryRiskLevel",
    "CodexBoundarySubject",
    "CodexBoundaryTool",
    "CodexBoundaryTraceEvent",
    "CodexBoundaryViolatedRule",
    "freeze_codex_boundary_result",
]
