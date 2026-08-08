"""Runtime contract metadata for local agent consumers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from agents_shipgate import __version__
from agents_shipgate.schemas.agent_boundary import (
    AGENT_BOUNDARY_RESULT_SCHEMA_VERSION,
)
from agents_shipgate.schemas.agent_handoff import (
    AGENT_HANDOFF_SCHEMA_PATH,
    AGENT_HANDOFF_SCHEMA_VERSION,
)
from agents_shipgate.schemas.attestation import ATTESTATION_SCHEMA_VERSION
from agents_shipgate.schemas.capabilities import (
    CAPABILITY_LOCK_DIFF_SCHEMA_VERSION,
    CAPABILITY_LOCK_SCHEMA_VERSION,
    CAPABILITY_STANDARD_VERSION,
)
from agents_shipgate.schemas.codex_boundary_result import (
    CODEX_BOUNDARY_RESULT_SCHEMA_VERSION,
)
from agents_shipgate.schemas.governance_benchmark import (
    GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION,
    GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION,
)
from agents_shipgate.schemas.host_grants import (
    HOST_GRANTS_BASELINE_SCHEMA_VERSION,
    HOST_GRANTS_DRIFT_SCHEMA_VERSION,
    HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
)
from agents_shipgate.schemas.human_authorization import (
    HUMAN_AUTHORIZATION_EVALUATION_SCHEMA_VERSION,
    HUMAN_AUTHORIZATION_REQUEST_SCHEMA_VERSION,
    HUMAN_AUTHORIZATION_SCHEMA_PATH,
    HUMAN_AUTHORIZATION_SCHEMA_VERSION,
    HUMAN_AUTHORIZATION_TRUST_POLICY_ACCOUNT_PATH,
    HUMAN_AUTHORIZATION_TRUST_POLICY_SCHEMA_VERSION,
)
from agents_shipgate.schemas.org_evidence_bundle import ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION
from agents_shipgate.schemas.packet import EvidencePacket
from agents_shipgate.schemas.preflight import PREFLIGHT_SCHEMA_VERSION
from agents_shipgate.schemas.registry import REGISTRY_SCHEMA_VERSION
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.verification_identity import (
    VERIFICATION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    VERIFICATION_PLAN_SCHEMA_VERSION,
    VERIFICATION_RECEIPT_SCHEMA_VERSION,
    VERIFICATION_UNIT_RESULT_SCHEMA_VERSION,
)
from agents_shipgate.schemas.verifier import VerifierArtifact
from agents_shipgate.schemas.verify_run import VERIFY_RUN_SCHEMA_VERSION

CONTRACT_VERSION: Literal["20"] = "20"
MINIMUM_CONTROL_CONTRACT_VERSION: Literal["20"] = "20"
GATING_SIGNAL: Literal["release_decision.decision"] = "release_decision.decision"
AGENT_RESULT_SCHEMA_VERSION: Literal["agent_result_v3"] = "agent_result_v3"
AGENT_RESULT_SCHEMA_PATH: Literal["docs/agent-result-schema.v3.json"] = (
    "docs/agent-result-schema.v3.json"
)
AGENT_BOUNDARY_RESULT_SCHEMA_PATH: Literal["docs/agent-boundary-result-schema.v2.json"] = (
    "docs/agent-boundary-result-schema.v2.json"
)
TRIGGER_CATALOG_SCHEMA_VERSION: Literal["0.3"] = "0.3"
# Fields of the SHARED agent result (``agent_result_schema_path``). The graded
# ``pending_review[]`` obligation is deliberately absent: it exists only on
# ``shipgate.agent_boundary_result/v1``, because adding it to the shared base
# would change the frozen deprecated codex projection. Consumers learn it from
# the boundary-result schema and docs/agents/protocol.md.
AGENT_RESULT_CONTROL_FIELDS: tuple[str, ...] = (
    "decision",
    "control",
    "repair",
    "policy",
)
AGENT_CONTROL_FIELDS: tuple[str, ...] = (
    "state",
    "reason",
    "completion_allowed",
    "must_stop",
    "verify_required",
    "next_action",
    "allowed_next_commands",
    "permissions",
    "human_review",
    "stop_reason",
)
# Action-scoped authority (contract v20). ``merge`` and ``report_complete``
# are the terminal pair and track ``completion_allowed``; the rest are the
# progress actions a change needs in order to become reviewable at all.
AGENT_CONTROL_PERMISSIONS: tuple[str, ...] = (
    "edit",
    "commit",
    "push",
    "update_pr",
    "merge",
    "report_complete",
)
AGENT_CONTROL_STATES: tuple[str, ...] = (
    "complete",
    "agent_action_required",
    "review_publishable",
    "human_review_required",
)
EXTERNAL_INTEGRATION_SURFACES: tuple[str, ...] = (
    "agent_handoff",
    "preflight",
    "capability_lock",
    "capability_lock_diff",
    "capability_standard",
    "attestation",
    "registry",
    "org_governance",
    "org_evidence_bundle",
    "verification_plan",
    "verification_unit_result",
    "verification_artifact_manifest",
    "verification_receipt",
    "human_authorization",
    "host_grants_inventory",
    "host_grants_baseline",
    "host_grants_drift",
    "agent_boundary_result",
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
    "conductor": (
        "Conductor OSS workflow JSON",
        "Netflix Conductor",
        "Conductor OSS",
        "Conductor",
    ),
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
AGENT_INTERFACE_OPERATIONS: tuple[str, ...] = (
    "verify_pr",
    "verify_local",
    "verify_preview",
)
EXIT_CODE_POLICY: dict[str, str] = {
    "0": "command completed; inspect JSON verdict fields for release state",
    "2": "configuration or CLI flag error",
    "3": "input parse or missing artifact error",
    "4": "other or internal error",
    "6": "baseline integrity failure",
    "20": "strict-mode gate failure or opt-in governance failure",
}
MCP_TOOLS: tuple[str, ...] = (
    "shipgate.check",
    "shipgate.preflight",
    "shipgate.explain",
    "shipgate.capabilities",
    "shipgate.handoff",
)
COMMANDS: dict[str, str] = {
    "agent_check_codex": (
        "shipgate check --agent codex --workspace . --format agent-boundary-json"
    ),
    "agent_check_claude_code": (
        "shipgate check --agent claude-code --workspace . --format agent-boundary-json"
    ),
    "agent_check_cursor": (
        "shipgate check --agent cursor --workspace . --format agent-boundary-json"
    ),
    "preflight": ("agents-shipgate preflight --workspace . --config shipgate.yaml --plan - --json"),
    "preview": "agents-shipgate verify --preview --json",
    "install_agent_workflow": "agents-shipgate init --workspace . --write --json",
    "verify_local": (
        "agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --json"
    ),
    "verify_pr": (
        "agents-shipgate verify --workspace . --config shipgate.yaml "
        "--base origin/main --head HEAD --ci-mode advisory --json"
    ),
    "verification_prepare": (
        "agents-shipgate verification prepare --workspace . --config shipgate.yaml "
        "--base origin/main --head HEAD --out "
        "agents-shipgate-reports/verification-plan.json"
    ),
    "verification_worker": (
        "agents-shipgate verification worker --plan "
        "agents-shipgate-reports/verification-plan.json --workspace . --out "
        "agents-shipgate-reports/verification-unit-result.json"
    ),
    "verification_assemble": (
        "agents-shipgate verification assemble --plan "
        "agents-shipgate-reports/verification-plan.json --unit-result "
        "agents-shipgate-reports/verification-unit-result.json --verifier "
        "agents-shipgate-reports/verifier.json --artifacts-root "
        "agents-shipgate-reports --out "
        "agents-shipgate-reports/verification-receipt.json"
    ),
    "verification_reproduce": (
        "agents-shipgate verification reproduce --receipt "
        "agents-shipgate-reports/verification-receipt.json --artifacts-root "
        "agents-shipgate-reports"
    ),
    "authorization_request": (
        "agents-shipgate authorization request --receipt "
        "agents-shipgate-reports/verification-receipt.json --artifacts-root "
        "agents-shipgate-reports --remote origin --destination-ref "
        "refs/heads/<branch> --expected-lease-oid <oid> --out "
        "agents-shipgate-reports/human-authorization-request.json"
    ),
    "authorization_execute": (
        "agents-shipgate authorization execute --workspace . --receipt "
        "agents-shipgate-reports/verification-receipt.json --artifacts-root "
        "agents-shipgate-reports"
    ),
    "agent_handoff": (
        "agents-shipgate agent handoff --from agents-shipgate-reports/verifier.json --json"
    ),
    "attest": (
        "agents-shipgate attest --from agents-shipgate-reports/verifier.json "
        "--out agents-shipgate-reports/attestation.json"
    ),
    "org_bundle": (
        "agents-shipgate org bundle --config shipgate.yaml "
        "--from agents-shipgate-reports/verifier.json "
        "--out agents-shipgate-reports/org-evidence-bundle.json --json"
    ),
    "org_policy_packs": "agents-shipgate org policy-packs --config shipgate.yaml --json",
    "registry_summary": "agents-shipgate registry summary --registry .agents-shipgate/registry.jsonl --json",
    "registry_verify": "agents-shipgate registry verify --registry .agents-shipgate/registry.jsonl --json",
    "host_audit": ("shipgate audit --host --json --out agents-shipgate-reports/host-grants.json"),
    "contract": "agents-shipgate contract --json",
}
PRIMARY_COMMANDS: dict[str, str] = {
    "check_codex": ("shipgate check --agent codex --workspace . --format agent-boundary-json"),
    "check_claude_code": (
        "shipgate check --agent claude-code --workspace . --format agent-boundary-json"
    ),
    "check_cursor": ("shipgate check --agent cursor --workspace . --format agent-boundary-json"),
    "verify_pr": (
        "agents-shipgate verify --workspace . --config shipgate.yaml "
        "--base origin/main --head HEAD --ci-mode advisory --json"
    ),
    "host_audit": ("shipgate audit --host --json --out agents-shipgate-reports/host-grants.json"),
}
ARTIFACTS: dict[str, str] = {
    "verifier": "agents-shipgate-reports/verifier.json",
    "verify_run": "agents-shipgate-reports/verify-run.json",
    "agent_handoff": "agents-shipgate-reports/agent-handoff.json",
    "verification_plan": "agents-shipgate-reports/verification-plan.json",
    "verification_unit_result": "agents-shipgate-reports/verification-unit-result.json",
    "verification_artifact_manifest": "agents-shipgate-reports/verification-artifacts.json",
    "verification_receipt": "agents-shipgate-reports/verification-receipt.json",
    "human_authorization_request": (
        "agents-shipgate-reports/human-authorization-request.json"
    ),
    "human_authorization": "agents-shipgate-reports/human-authorization.json",
    "report": "agents-shipgate-reports/report.json",
    "pr_comment": "agents-shipgate-reports/pr-comment.md",
    "packet": "agents-shipgate-reports/packet.json",
    "attestation": "agents-shipgate-reports/attestation.json",
    "org_evidence_bundle": "agents-shipgate-reports/org-evidence-bundle.json",
    "host_grants": "agents-shipgate-reports/host-grants.json",
    "org_status": "agents-shipgate-reports/org-status.json",
    "registry": ".agents-shipgate/registry.jsonl",
}
AGENT_READ_ORDER: tuple[str, ...] = (
    "verification-receipt.json",
    "verification-receipt.json.request_id",
    "verification-receipt.json.receipt_id",
    "agent-handoff.json",
    "agent-handoff.json.control.state",
    "agent-handoff.json.authorization",
    "verifier.json.control.state",
    "verify-run.json",
    "report.json.release_decision.decision",
)
VERIFIER_READ_ORDER: tuple[str, ...] = (
    "control.state",
    "authorization",
    "execution",
    "merge_verdict",
    "applicability",
    "can_merge_without_human",
    "control.next_action",
    "fix_task",
    "capability_review.top_changes",
    "release_decision.decision",
    "request_id",
    "decision_id",
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
    # v11: these close semantic evidence gaps and therefore remain reviewed
    # human assertions. Agents may route the structured next action, but must
    # never invent or auto-fill either declaration.
    "action_effect",
    "action_authority",
    "agent_binding",
    "approval",
    "confirmation",
    "idempotency",
    "broad-scope",
    "prohibited-action",
    "runtime-trace",
    "human-ack",
    "human-authorization",
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
    minimum_control_contract_version: str
    cli_version: str
    report_schema_version: str
    packet_schema_version: str
    verifier_schema_version: str
    verify_run_schema_version: str
    verification_plan_schema_version: str
    verification_unit_result_schema_version: str
    verification_artifact_manifest_schema_version: str
    verification_receipt_schema_version: str
    human_authorization_request_schema_version: str
    human_authorization_schema_version: str
    human_authorization_evaluation_schema_version: str
    human_authorization_trust_policy_schema_version: str
    human_authorization_trust_policy_default_path: str
    human_authorization_schema_path: str
    agent_handoff_schema_version: str
    agent_handoff_schema_path: str
    agent_handoff_artifact: str
    codex_boundary_result_schema_version: str
    agent_boundary_result_schema_version: str
    agent_boundary_result_schema_path: str
    capability_lock_schema_version: str
    capability_lock_diff_schema_version: str
    preflight_schema_version: str
    capability_standard_version: str
    governance_benchmark_catalog_schema_version: str
    governance_benchmark_result_schema_version: str
    attestation_schema_version: str
    registry_schema_version: str
    org_evidence_bundle_schema_version: str
    host_grants_inventory_schema_version: str
    host_grants_baseline_schema_version: str
    host_grants_drift_schema_version: str
    trigger_catalog_schema_version: str
    deprecated_surfaces: dict[str, str]
    external_integration_surfaces: list[str]
    gating_signal: str
    agent_result_schema_version: str
    agent_result_schema_path: str
    agent_result_control_fields: list[str]
    agent_control_fields: list[str]
    agent_control_permissions: list[str]
    agent_control_states: list[str]
    manual_review_signals: list[str]
    agent_interface_operations: list[str]
    exit_code_policy: dict[str, str]
    mcp_tools: list[str]
    primary_commands: dict[str, str]
    commands: dict[str, str]
    default_paths: dict[str, str]
    artifacts: dict[str, str]
    agent_read_order: list[str]
    verifier_read_order: list[str]
    merge_verdicts: list[str]
    release_decisions: list[str]
    do_not_auto_assert: list[str]


def build_contract_payload() -> ContractPayload:
    """Build the local CLI contract from runtime constants."""

    report_schema_version = ReadinessReport.model_fields["report_schema_version"].default
    packet_schema_version = EvidencePacket.model_fields["packet_schema_version"].default
    verifier_schema_version = VerifierArtifact.model_fields["verifier_schema_version"].default
    return ContractPayload(
        contract_version=CONTRACT_VERSION,
        minimum_control_contract_version=MINIMUM_CONTROL_CONTRACT_VERSION,
        cli_version=__version__,
        report_schema_version=str(report_schema_version),
        packet_schema_version=str(packet_schema_version),
        verifier_schema_version=str(verifier_schema_version),
        verify_run_schema_version=VERIFY_RUN_SCHEMA_VERSION,
        verification_plan_schema_version=VERIFICATION_PLAN_SCHEMA_VERSION,
        verification_unit_result_schema_version=VERIFICATION_UNIT_RESULT_SCHEMA_VERSION,
        verification_artifact_manifest_schema_version=(
            VERIFICATION_ARTIFACT_MANIFEST_SCHEMA_VERSION
        ),
        verification_receipt_schema_version=VERIFICATION_RECEIPT_SCHEMA_VERSION,
        human_authorization_request_schema_version=(
            HUMAN_AUTHORIZATION_REQUEST_SCHEMA_VERSION
        ),
        human_authorization_schema_version=HUMAN_AUTHORIZATION_SCHEMA_VERSION,
        human_authorization_evaluation_schema_version=(
            HUMAN_AUTHORIZATION_EVALUATION_SCHEMA_VERSION
        ),
        human_authorization_trust_policy_schema_version=(
            HUMAN_AUTHORIZATION_TRUST_POLICY_SCHEMA_VERSION
        ),
        human_authorization_trust_policy_default_path=(
            HUMAN_AUTHORIZATION_TRUST_POLICY_ACCOUNT_PATH
        ),
        human_authorization_schema_path=HUMAN_AUTHORIZATION_SCHEMA_PATH,
        agent_handoff_schema_version=AGENT_HANDOFF_SCHEMA_VERSION,
        agent_handoff_schema_path=AGENT_HANDOFF_SCHEMA_PATH,
        agent_handoff_artifact=ARTIFACTS["agent_handoff"],
        codex_boundary_result_schema_version=CODEX_BOUNDARY_RESULT_SCHEMA_VERSION,
        agent_boundary_result_schema_version=AGENT_BOUNDARY_RESULT_SCHEMA_VERSION,
        agent_boundary_result_schema_path=AGENT_BOUNDARY_RESULT_SCHEMA_PATH,
        capability_lock_schema_version=CAPABILITY_LOCK_SCHEMA_VERSION,
        capability_lock_diff_schema_version=CAPABILITY_LOCK_DIFF_SCHEMA_VERSION,
        preflight_schema_version=PREFLIGHT_SCHEMA_VERSION,
        capability_standard_version=CAPABILITY_STANDARD_VERSION,
        governance_benchmark_catalog_schema_version=(GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION),
        governance_benchmark_result_schema_version=(GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION),
        attestation_schema_version=ATTESTATION_SCHEMA_VERSION,
        registry_schema_version=REGISTRY_SCHEMA_VERSION,
        org_evidence_bundle_schema_version=ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        host_grants_inventory_schema_version=HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
        host_grants_baseline_schema_version=HOST_GRANTS_BASELINE_SCHEMA_VERSION,
        host_grants_drift_schema_version=HOST_GRANTS_DRIFT_SCHEMA_VERSION,
        trigger_catalog_schema_version=TRIGGER_CATALOG_SCHEMA_VERSION,
        deprecated_surfaces={
            "codex-boundary-json": (
                "Deprecated compatibility projection through 0.16.x; use agent-boundary-json."
            )
        },
        external_integration_surfaces=list(EXTERNAL_INTEGRATION_SURFACES),
        gating_signal=GATING_SIGNAL,
        agent_result_schema_version=AGENT_RESULT_SCHEMA_VERSION,
        agent_result_schema_path=AGENT_RESULT_SCHEMA_PATH,
        agent_result_control_fields=list(AGENT_RESULT_CONTROL_FIELDS),
        agent_control_fields=list(AGENT_CONTROL_FIELDS),
        agent_control_permissions=list(AGENT_CONTROL_PERMISSIONS),
        agent_control_states=list(AGENT_CONTROL_STATES),
        manual_review_signals=list(MANUAL_REVIEW_SIGNALS),
        agent_interface_operations=list(AGENT_INTERFACE_OPERATIONS),
        exit_code_policy=dict(EXIT_CODE_POLICY),
        mcp_tools=list(MCP_TOOLS),
        primary_commands=dict(PRIMARY_COMMANDS),
        commands=dict(COMMANDS),
        default_paths=dict(DEFAULT_PATHS),
        artifacts=dict(ARTIFACTS),
        agent_read_order=list(AGENT_READ_ORDER),
        verifier_read_order=list(VERIFIER_READ_ORDER),
        merge_verdicts=list(MERGE_VERDICTS),
        release_decisions=list(RELEASE_DECISIONS),
        do_not_auto_assert=list(DO_NOT_AUTO_ASSERT),
    )


__all__ = [
    "CONTRACT_VERSION",
    "MINIMUM_CONTROL_CONTRACT_VERSION",
    "AGENT_CONTROL_FIELDS",
    "AGENT_CONTROL_PERMISSIONS",
    "AGENT_CONTROL_STATES",
    "AGENT_BOUNDARY_RESULT_SCHEMA_PATH",
    "AGENT_BOUNDARY_RESULT_SCHEMA_VERSION",
    "AGENT_RESULT_CONTROL_FIELDS",
    "AGENT_RESULT_SCHEMA_PATH",
    "AGENT_RESULT_SCHEMA_VERSION",
    "AGENT_READ_ORDER",
    "AGENT_HANDOFF_SCHEMA_PATH",
    "AGENT_HANDOFF_SCHEMA_VERSION",
    "AGENT_INTERFACE_OPERATIONS",
    "ARTIFACTS",
    "ATTESTATION_SCHEMA_VERSION",
    "CODEX_BOUNDARY_RESULT_SCHEMA_VERSION",
    "COMMANDS",
    "DEFAULT_PATHS",
    "DO_NOT_AUTO_ASSERT",
    "EXIT_CODE_POLICY",
    "EXTERNAL_INTEGRATION_SURFACES",
    "GATING_SIGNAL",
    "HOST_GRANTS_INVENTORY_SCHEMA_VERSION",
    "HOST_GRANTS_BASELINE_SCHEMA_VERSION",
    "HOST_GRANTS_DRIFT_SCHEMA_VERSION",
    "HUMAN_AUTHORIZATION_EVALUATION_SCHEMA_VERSION",
    "HUMAN_AUTHORIZATION_REQUEST_SCHEMA_VERSION",
    "HUMAN_AUTHORIZATION_SCHEMA_PATH",
    "HUMAN_AUTHORIZATION_SCHEMA_VERSION",
    "HUMAN_AUTHORIZATION_TRUST_POLICY_ACCOUNT_PATH",
    "HUMAN_AUTHORIZATION_TRUST_POLICY_SCHEMA_VERSION",
    "MANUAL_REVIEW_SIGNALS",
    "MERGE_VERDICTS",
    "MCP_TOOLS",
    "ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION",
    "PRIMARY_COMMANDS",
    "REGISTRY_SCHEMA_VERSION",
    "RELEASE_DECISIONS",
    "SUPPORTED_INPUTS",
    "TRIGGER_CATALOG_SCHEMA_VERSION",
    "VERIFY_RUN_SCHEMA_VERSION",
    "VERIFICATION_ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "VERIFICATION_PLAN_SCHEMA_VERSION",
    "VERIFICATION_RECEIPT_SCHEMA_VERSION",
    "VERIFICATION_UNIT_RESULT_SCHEMA_VERSION",
    "VERIFIER_READ_ORDER",
    "ContractPayload",
    "build_contract_payload",
]
