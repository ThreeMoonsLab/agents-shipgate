"""Runtime contract metadata for local agent consumers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from agents_shipgate import __version__
from agents_shipgate.schemas.capabilities import (
    CAPABILITY_LOCK_DIFF_SCHEMA_VERSION,
    CAPABILITY_LOCK_SCHEMA_VERSION,
    CAPABILITY_STANDARD_VERSION,
)
from agents_shipgate.schemas.governance_benchmark import (
    GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION,
    GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION,
)
from agents_shipgate.schemas.packet import EvidencePacket
from agents_shipgate.schemas.preflight import PREFLIGHT_SCHEMA_VERSION
from agents_shipgate.schemas.report import ReadinessReport

CONTRACT_VERSION: Literal["3"] = "3"
GATING_SIGNAL: Literal["release_decision.decision"] = "release_decision.decision"
EXTERNAL_INTEGRATION_SURFACES: tuple[str, ...] = (
    "preflight",
    "capability_lock",
    "capability_lock_diff",
    "capability_standard",
    "governance_benchmark_catalog",
    "governance_benchmark_result",
)
# Wire-stable enum-id -> display-alias tuple. Enum-ids match adapter
# `source_type` ClassVars on inputs/*.py and the `inputs[]` array in
# .well-known/agents-shipgate.json. Alias tuples are ordered
# longest-first so substring resolution prefers more specific names
# (e.g. "Anthropic Messages API" wins over "Anthropic"). Iteration
# order is the canonical public order; the wire array in .well-known
# is pinned to list(SUPPORTED_INPUTS).
SUPPORTED_INPUTS: dict[str, tuple[str, ...]] = {
    "mcp": ("Model Context Protocol (MCP)", "Model Context Protocol", "MCP"),
    "openapi": ("OpenAPI 3.x", "OpenAPI"),
    "openai_agents_sdk": ("OpenAI Agents SDK",),
    "anthropic_api": ("Anthropic Messages API", "Anthropic"),
    "google_adk": ("Google ADK",),
    "langchain": ("LangChain and LangGraph", "LangChain/LangGraph", "LangChain"),
    "crewai": ("CrewAI",),
    "openai_api": ("OpenAI API",),
    "codex_config": ("Codex repo config", "Codex config"),
    "codex_plugin": ("Codex plugin packages and marketplaces", "Codex plugin"),
    "n8n": ("n8n",),
}
MANUAL_REVIEW_SIGNALS: tuple[str, ...] = (
    "release_decision.review_items",
    # v0.17: per-finding decision audit. Reviewers triaging
    # `release_decision.review_items` use the corresponding
    # `contribution_rules[]` row to see WHY each item was classified
    # as a review item (`policy_baseline_accepted`,
    # `severity_baseline_accepted`, or `review_required`).
    "release_decision.contribution_rules",
    "findings[].requires_human_review",
    "findings[].blocks_release",
    # v0.15: provenance is a reviewer triage/filter axis only. It
    # never changes release_decision, severity, fingerprints,
    # baselines, or CI exit behavior.
    "findings[].provenance_kind",
    "summary.human_review_recommended",
    "action_surface_diff",
    "codex_plugin_surface",
    "packet.evidence_matrix.rows",
    "packet.capability_intent.divergence_findings",
    "packet.approval_coverage.gap_findings",
    "packet.idempotency_risk.gap_findings",
    "packet.scope_coverage.gap_findings",
    "packet.human_in_the_loop.trace_findings",
    "packet.dynamic_scenarios.scenarios",
)
DEFAULT_PATHS: dict[str, str] = {
    "manifest": "shipgate.yaml",
    "reports_dir": "agents-shipgate-reports",
    "local_contract": ".shipgate/agent-contract.json",
}
COMMANDS: dict[str, str] = {
    "preflight": "agents-shipgate preflight --workspace . --config shipgate.yaml --json",
    "preview": "agents-shipgate verify --preview --json",
    "install_agent_workflow": (
        "agents-shipgate init --workspace . --write --ci --agent-instructions=default --json"
    ),
    "verify_local": (
        "agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --json"
    ),
    "verify_pr": (
        "agents-shipgate verify --workspace . --config shipgate.yaml "
        "--base origin/main --head HEAD --ci-mode advisory --json"
    ),
    "contract": "agents-shipgate contract --json",
}
ARTIFACTS: dict[str, str] = {
    "verifier": "agents-shipgate-reports/verifier.json",
    "report": "agents-shipgate-reports/report.json",
    "pr_comment": "agents-shipgate-reports/pr-comment.md",
    "agent_result": "agents-shipgate-reports/agent-result.json",
    "packet": "agents-shipgate-reports/packet.json",
}
VERIFIER_READ_ORDER: tuple[str, ...] = (
    "merge_verdict",
    "can_merge_without_human",
    "first_next_action",
    "fix_task",
    "capability_review.top_changes",
    "agent_controller",
    "release_decision.decision",
)
MERGE_VERDICTS: tuple[str, ...] = (
    "mergeable",
    "human_review_required",
    "insufficient_evidence",
    "blocked",
    "unknown",
)
RELEASE_DECISIONS: tuple[str, ...] = (
    "passed",
    "review_required",
    "insufficient_evidence",
    "blocked",
)
DO_NOT_AUTO_ASSERT: tuple[str, ...] = (
    "approval",
    "confirmation",
    "idempotency",
    "broad-scope",
    "prohibited-action",
    "runtime-trace",
    "suppression",
    "waiver",
    "baseline",
    "policy-weakening",
)


class ContractPayload(BaseModel):
    """Stable JSON payload emitted by ``agents-shipgate contract --json``."""

    # New fields must be deliberate contract changes with a version bump.
    model_config = ConfigDict(extra="forbid")

    contract_version: str
    cli_version: str
    report_schema_version: str
    packet_schema_version: str
    capability_lock_schema_version: str
    capability_lock_diff_schema_version: str
    preflight_schema_version: str
    capability_standard_version: str
    governance_benchmark_catalog_schema_version: str
    governance_benchmark_result_schema_version: str
    external_integration_surfaces: list[str]
    gating_signal: str
    manual_review_signals: list[str]
    commands: dict[str, str]
    default_paths: dict[str, str]
    artifacts: dict[str, str]
    verifier_read_order: list[str]
    merge_verdicts: list[str]
    release_decisions: list[str]
    do_not_auto_assert: list[str]


def build_contract_payload() -> ContractPayload:
    """Build the local CLI contract from runtime constants."""

    report_schema_version = ReadinessReport.model_fields["report_schema_version"].default
    packet_schema_version = EvidencePacket.model_fields["packet_schema_version"].default
    return ContractPayload(
        contract_version=CONTRACT_VERSION,
        cli_version=__version__,
        report_schema_version=str(report_schema_version),
        packet_schema_version=str(packet_schema_version),
        capability_lock_schema_version=CAPABILITY_LOCK_SCHEMA_VERSION,
        capability_lock_diff_schema_version=CAPABILITY_LOCK_DIFF_SCHEMA_VERSION,
        preflight_schema_version=PREFLIGHT_SCHEMA_VERSION,
        capability_standard_version=CAPABILITY_STANDARD_VERSION,
        governance_benchmark_catalog_schema_version=(GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION),
        governance_benchmark_result_schema_version=(GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION),
        external_integration_surfaces=list(EXTERNAL_INTEGRATION_SURFACES),
        gating_signal=GATING_SIGNAL,
        manual_review_signals=list(MANUAL_REVIEW_SIGNALS),
        commands=dict(COMMANDS),
        default_paths=dict(DEFAULT_PATHS),
        artifacts=dict(ARTIFACTS),
        verifier_read_order=list(VERIFIER_READ_ORDER),
        merge_verdicts=list(MERGE_VERDICTS),
        release_decisions=list(RELEASE_DECISIONS),
        do_not_auto_assert=list(DO_NOT_AUTO_ASSERT),
    )


__all__ = [
    "CONTRACT_VERSION",
    "ARTIFACTS",
    "COMMANDS",
    "DEFAULT_PATHS",
    "DO_NOT_AUTO_ASSERT",
    "EXTERNAL_INTEGRATION_SURFACES",
    "GATING_SIGNAL",
    "MANUAL_REVIEW_SIGNALS",
    "MERGE_VERDICTS",
    "RELEASE_DECISIONS",
    "SUPPORTED_INPUTS",
    "VERIFIER_READ_ORDER",
    "ContractPayload",
    "build_contract_payload",
]
