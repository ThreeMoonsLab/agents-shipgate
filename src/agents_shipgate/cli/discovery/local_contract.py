"""Downstream local agent contract written by ``init --agent-instructions=default``."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from agents_shipgate import __version__
from agents_shipgate.schemas.contract import (
    AGENT_BOUNDARY_RESULT_SCHEMA_PATH,
    AGENT_BOUNDARY_RESULT_SCHEMA_VERSION,
    AGENT_CONTROL_FIELDS,
    AGENT_CONTROL_PERMISSIONS,
    AGENT_CONTROL_STATES,
    AGENT_HANDOFF_SCHEMA_PATH,
    AGENT_HANDOFF_SCHEMA_VERSION,
    AGENT_INTERFACE_OPERATIONS,
    AGENT_READ_ORDER,
    AGENT_RESULT_CONTROL_FIELDS,
    AGENT_RESULT_SCHEMA_PATH,
    AGENT_RESULT_SCHEMA_VERSION,
    ARTIFACTS,
    ATTESTATION_SCHEMA_VERSION,
    CODEX_BOUNDARY_RESULT_SCHEMA_VERSION,
    COMMANDS,
    CONTRACT_VERSION,
    DEFAULT_PATHS,
    DO_NOT_AUTO_ASSERT,
    EXIT_CODE_POLICY,
    GATING_SIGNAL,
    HOST_GRANTS_BASELINE_SCHEMA_VERSION,
    HOST_GRANTS_DRIFT_SCHEMA_VERSION,
    HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
    HUMAN_AUTHORIZATION_EVALUATION_SCHEMA_VERSION,
    HUMAN_AUTHORIZATION_REQUEST_SCHEMA_VERSION,
    HUMAN_AUTHORIZATION_SCHEMA_PATH,
    HUMAN_AUTHORIZATION_SCHEMA_VERSION,
    HUMAN_AUTHORIZATION_TRUST_POLICY_ACCOUNT_PATH,
    HUMAN_AUTHORIZATION_TRUST_POLICY_SCHEMA_VERSION,
    MCP_TOOLS,
    MERGE_VERDICTS,
    MINIMUM_CONTROL_CONTRACT_VERSION,
    ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    PRIMARY_COMMANDS,
    REGISTRY_SCHEMA_VERSION,
    RELEASE_DECISIONS,
    TRIGGER_CATALOG_SCHEMA_VERSION,
    VERIFICATION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    VERIFICATION_PLAN_SCHEMA_VERSION,
    VERIFICATION_RECEIPT_SCHEMA_VERSION,
    VERIFICATION_UNIT_RESULT_SCHEMA_VERSION,
    VERIFIER_READ_ORDER,
    VERIFY_RUN_SCHEMA_VERSION,
)
from agents_shipgate.schemas.verifier import VerifierArtifact

LOCAL_CONTRACT_SCHEMA_VERSION = "8"
LOCAL_CONTRACT_RELATIVE_PATH = ".shipgate/agent-contract.json"


class LocalAgentContract(BaseModel):
    """Minimal local contract for cold-start coding agents in downstream repos."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    agents_shipgate_version: str
    contract_version: str
    minimum_control_contract_version: str
    default_paths: dict[str, str]
    primary_commands: dict[str, str]
    commands: dict[str, str]
    artifacts: dict[str, str]
    agent_read_order: list[str]
    verifier_read_order: list[str]
    gating_signal: str
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
    attestation_schema_version: str
    registry_schema_version: str
    org_evidence_bundle_schema_version: str
    host_grants_inventory_schema_version: str
    host_grants_baseline_schema_version: str
    host_grants_drift_schema_version: str
    trigger_catalog_schema_version: str
    agent_result_schema_version: str
    agent_result_schema_path: str
    agent_result_control_fields: list[str]
    agent_control_fields: list[str]
    agent_control_permissions: list[str]
    agent_control_states: list[str]
    agent_interface_operations: list[str]
    exit_code_policy: dict[str, str]
    mcp_tools: list[str]
    merge_verdicts: list[str]
    release_decisions: list[str]
    do_not_auto_assert: list[str]


def build_local_agent_contract() -> LocalAgentContract:
    """Build the local downstream contract from runtime contract constants."""

    return LocalAgentContract(
        schema_version=LOCAL_CONTRACT_SCHEMA_VERSION,
        agents_shipgate_version=__version__,
        contract_version=CONTRACT_VERSION,
        minimum_control_contract_version=MINIMUM_CONTROL_CONTRACT_VERSION,
        default_paths=dict(DEFAULT_PATHS),
        primary_commands=dict(PRIMARY_COMMANDS),
        commands=dict(COMMANDS),
        artifacts=dict(ARTIFACTS),
        agent_read_order=list(AGENT_READ_ORDER),
        verifier_read_order=list(VERIFIER_READ_ORDER),
        gating_signal=GATING_SIGNAL,
        verifier_schema_version=str(
            VerifierArtifact.model_fields["verifier_schema_version"].default
        ),
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
        attestation_schema_version=ATTESTATION_SCHEMA_VERSION,
        registry_schema_version=REGISTRY_SCHEMA_VERSION,
        org_evidence_bundle_schema_version=ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        host_grants_inventory_schema_version=HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
        host_grants_baseline_schema_version=HOST_GRANTS_BASELINE_SCHEMA_VERSION,
        host_grants_drift_schema_version=HOST_GRANTS_DRIFT_SCHEMA_VERSION,
        trigger_catalog_schema_version=TRIGGER_CATALOG_SCHEMA_VERSION,
        agent_result_schema_version=AGENT_RESULT_SCHEMA_VERSION,
        agent_result_schema_path=AGENT_RESULT_SCHEMA_PATH,
        agent_result_control_fields=list(AGENT_RESULT_CONTROL_FIELDS),
        agent_control_fields=list(AGENT_CONTROL_FIELDS),
        agent_control_permissions=list(AGENT_CONTROL_PERMISSIONS),
        agent_control_states=list(AGENT_CONTROL_STATES),
        agent_interface_operations=list(AGENT_INTERFACE_OPERATIONS),
        exit_code_policy=dict(EXIT_CODE_POLICY),
        mcp_tools=list(MCP_TOOLS),
        merge_verdicts=list(MERGE_VERDICTS),
        release_decisions=list(RELEASE_DECISIONS),
        do_not_auto_assert=list(DO_NOT_AUTO_ASSERT),
    )


def render_local_agent_contract() -> str:
    """Render the local contract as stable pretty JSON."""

    payload = build_local_agent_contract().model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


__all__ = [
    "LOCAL_CONTRACT_RELATIVE_PATH",
    "LOCAL_CONTRACT_SCHEMA_VERSION",
    "LocalAgentContract",
    "build_local_agent_contract",
    "render_local_agent_contract",
]
