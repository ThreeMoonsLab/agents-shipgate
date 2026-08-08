from __future__ import annotations

import json

from agents_shipgate import __version__
from agents_shipgate.cli.discovery.local_contract import (
    LOCAL_CONTRACT_RELATIVE_PATH,
    LOCAL_CONTRACT_SCHEMA_VERSION,
    build_local_agent_contract,
    render_local_agent_contract,
)
from agents_shipgate.schemas.contract import CONTRACT_VERSION, GATING_SIGNAL, PRIMARY_COMMANDS


def test_local_agent_contract_is_minimal_agent_operational_payload() -> None:
    payload = build_local_agent_contract().model_dump(mode="json")

    assert list(payload) == [
        "schema_version",
        "agents_shipgate_version",
        "contract_version",
        "minimum_control_contract_version",
        "default_paths",
        "primary_commands",
        "commands",
        "artifacts",
        "agent_read_order",
        "verifier_read_order",
        "gating_signal",
        "verifier_schema_version",
        "verify_run_schema_version",
        "verification_plan_schema_version",
        "verification_unit_result_schema_version",
        "verification_artifact_manifest_schema_version",
        "verification_receipt_schema_version",
        "current_control_schema_version",
        "current_control_schema_path",
        "current_control_artifact",
        "agent_refresh_triggers",
        "current_control_fallback_read_order",
        "human_authorization_request_schema_version",
        "human_authorization_schema_version",
        "human_authorization_evaluation_schema_version",
        "human_authorization_trust_policy_schema_version",
        "human_authorization_trust_policy_default_path",
        "human_authorization_schema_path",
        "agent_handoff_schema_version",
        "agent_handoff_schema_path",
        "agent_handoff_artifact",
        "codex_boundary_result_schema_version",
        "agent_boundary_result_schema_version",
        "agent_boundary_result_schema_path",
        "attestation_schema_version",
        "registry_schema_version",
        "org_evidence_bundle_schema_version",
        "host_grants_inventory_schema_version",
        "host_grants_baseline_schema_version",
        "host_grants_drift_schema_version",
        "trigger_catalog_schema_version",
        "agent_result_schema_version",
        "agent_result_schema_path",
        "agent_result_control_fields",
        "agent_control_fields",
        "agent_control_states",
        "agent_interface_operations",
        "exit_code_policy",
        "mcp_tools",
        "merge_verdicts",
        "release_decisions",
        "do_not_auto_assert",
    ]
    assert payload["schema_version"] == LOCAL_CONTRACT_SCHEMA_VERSION == "8"
    assert payload["agents_shipgate_version"] == __version__
    assert payload["contract_version"] == CONTRACT_VERSION == "20"
    assert payload["minimum_control_contract_version"] == "14"
    assert payload["default_paths"]["local_contract"] == LOCAL_CONTRACT_RELATIVE_PATH
    assert payload["primary_commands"] == dict(PRIMARY_COMMANDS)
    assert set(payload["primary_commands"]) == {
        "check_codex",
        "check_claude_code",
        "check_cursor",
        "verify_pr",
        "host_audit",
    }
    assert payload["primary_commands"]["verify_pr"].startswith("agents-shipgate verify")
    assert payload["commands"]["verify_local"].startswith("agents-shipgate verify")
    assert payload["commands"]["install_agent_workflow"] == (
        "agents-shipgate init --workspace . --write --json"
    )
    assert payload["commands"]["agent_check_codex"] == (
        "shipgate check --agent codex --workspace . --format agent-boundary-json"
    )
    assert payload["commands"]["agent_check_claude_code"] == (
        "shipgate check --agent claude-code --workspace . --format agent-boundary-json"
    )
    assert payload["commands"]["agent_check_cursor"] == (
        "shipgate check --agent cursor --workspace . --format agent-boundary-json"
    )
    assert payload["artifacts"]["verifier"] == "agents-shipgate-reports/verifier.json"
    assert payload["artifacts"]["verify_run"] == "agents-shipgate-reports/verify-run.json"
    assert payload["artifacts"]["agent_handoff"] == "agents-shipgate-reports/agent-handoff.json"
    assert payload["agent_read_order"] == [
        "current-control.json",
        "current-control.json.current_control_id",
        "current-control.json.lifecycle_state",
        "current-control.json.control.state",
        "verification-receipt.json",
        "verification-receipt.json.request_id",
        "verification-receipt.json.receipt_id",
        "agent-handoff.json",
        "agent-handoff.json.control.state",
        "agent-handoff.json.authorization",
        "verifier.json.control.state",
        "verify-run.json",
        "report.json.release_decision.decision",
    ]
    assert payload["verifier_read_order"][:3] == [
        "control.state",
        "authorization",
        "execution",
    ]
    assert payload["verifier_read_order"][-2:] == ["request_id", "decision_id"]
    assert payload["gating_signal"] == GATING_SIGNAL
    assert payload["verifier_schema_version"] == "0.7"
    assert payload["verify_run_schema_version"] == "shipgate.verify_run/v3"
    assert payload["human_authorization_request_schema_version"] == (
        "shipgate.human_authorization_request/v1"
    )
    assert payload["human_authorization_schema_version"] == (
        "shipgate.human_authorization/v1"
    )
    assert payload["human_authorization_evaluation_schema_version"] == (
        "shipgate.human_authorization_evaluation/v1"
    )
    assert payload["human_authorization_trust_policy_schema_version"] == (
        "shipgate.human_authorization_trust_policy/v1"
    )
    assert payload["human_authorization_trust_policy_default_path"] == (
        "~/.config/agents-shipgate/human-authorization-trust-policy.json"
    )
    assert payload["human_authorization_schema_path"] == (
        "docs/human-authorization-schema.v1.json"
    )
    assert payload["agent_handoff_schema_version"] == "shipgate.agent_handoff/v6"
    assert payload["agent_handoff_schema_path"] == "docs/agent-handoff-schema.v6.json"
    assert payload["agent_handoff_artifact"] == "agents-shipgate-reports/agent-handoff.json"
    assert payload["codex_boundary_result_schema_version"] == "shipgate.codex_boundary_result/v2"
    assert payload["agent_boundary_result_schema_version"] == ("shipgate.agent_boundary_result/v1")
    assert payload["agent_boundary_result_schema_path"] == (
        "docs/agent-boundary-result-schema.v1.json"
    )
    assert payload["attestation_schema_version"] == "0.5"
    assert payload["registry_schema_version"] == "0.4"
    assert payload["org_evidence_bundle_schema_version"] == ("shipgate.org_evidence_bundle/v2")
    assert payload["host_grants_inventory_schema_version"] == "0.2"
    assert payload["host_grants_baseline_schema_version"] == "0.2"
    assert payload["host_grants_drift_schema_version"] == "0.2"
    assert payload["trigger_catalog_schema_version"] == "0.3"
    assert payload["agent_result_schema_version"] == "agent_result_v2"
    assert payload["agent_result_schema_path"] == "docs/agent-result-schema.v2.json"
    assert payload["agent_result_control_fields"] == [
        "decision",
        "control",
        "repair",
        "policy",
    ]
    assert payload["agent_control_states"] == [
        "complete",
        "agent_action_required",
        "human_review_required",
    ]
    assert "stop_reason" in payload["agent_control_fields"]
    assert payload["agent_interface_operations"] == [
        "verify_pr",
        "verify_local",
        "verify_preview",
    ]
    assert payload["exit_code_policy"]["3"] == "input parse or missing artifact error"
    assert "shipgate.handoff" in payload["mcp_tools"]
    assert "blocked" in payload["merge_verdicts"]
    assert "passed" in payload["release_decisions"]
    assert "approval" in payload["do_not_auto_assert"]
    assert "human-authorization" in payload["do_not_auto_assert"]
    assert "action_effect" in payload["do_not_auto_assert"]
    assert "action_authority" in payload["do_not_auto_assert"]


def test_local_agent_contract_renders_stable_pretty_json() -> None:
    rendered = render_local_agent_contract()

    assert rendered.endswith("\n")
    parsed = json.loads(rendered)
    assert parsed == build_local_agent_contract().model_dump(mode="json")
