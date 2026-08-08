from __future__ import annotations

from typing import Any, Literal

from pydantic import SerializerFunctionWrapHandler, model_serializer

from agents_shipgate.schemas.agent_control import project_legacy_agent_control
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


class CodexBoundaryResultV2(AgentResultV2):
    """Frozen, deprecated local boundary contract.

    Its published schema is ``additionalProperties: false`` with a three-state
    control discriminator, and it shares ``control`` with the current
    ``AgentControl`` union — so the freeze has to hold at serialization, not
    just at one emit site. Every dump of this model therefore renders the
    pre-contract-20 control shape.

    The two places that copy fields *out* of this model into the current
    ``shipgate.agent_boundary_result/v1`` pass ``control`` explicitly instead of
    taking it from the dump, so the downgrade cannot leak into the format that
    is supposed to carry the new state.
    """

    schema_version: Literal["shipgate.codex_boundary_result/v2"] = (
        CODEX_BOUNDARY_RESULT_SCHEMA_VERSION
    )

    @model_serializer(mode="wrap")
    def _freeze_control(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict) and "control" in data:
            data["control"] = project_legacy_agent_control(self.control)
        return data


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
]
