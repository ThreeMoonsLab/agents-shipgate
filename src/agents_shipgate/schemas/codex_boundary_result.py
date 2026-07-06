from __future__ import annotations

from typing import Literal

from agents_shipgate.schemas.agent_result_v1 import (
    AgentResult,
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

CODEX_BOUNDARY_RESULT_SCHEMA_VERSION = "shipgate.codex_boundary_result/v1"

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
        CODEX_BOUNDARY_RESULT_SCHEMA_VERSION
    )

    # ``check`` is boundary-only: it never computes the capability delta.
    # When the diff touches a tool surface (declared or undeclared), the
    # boundary verdict does not cover the capability question and this flag
    # is True — the machine-readable form of "``decision=allow`` is
    # necessary but not sufficient; run ``agents-shipgate verify`` before
    # completion". Deterministic projection of the same deferral that emits
    # the ``capability_change_requires_verify`` /
    # ``undeclared_capability_surface`` diagnostics; it introduces no second
    # verdict.
    verify_required: bool = False


CodexBoundaryResult = CodexBoundaryResultV1

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
    "CodexBoundaryRiskLevel",
    "CodexBoundarySubject",
    "CodexBoundaryTool",
    "CodexBoundaryTraceEvent",
    "CodexBoundaryViolatedRule",
]
