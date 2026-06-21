from __future__ import annotations

import json

from agents_shipgate import __version__
from agents_shipgate.cli.discovery.local_contract import (
    LOCAL_CONTRACT_RELATIVE_PATH,
    LOCAL_CONTRACT_SCHEMA_VERSION,
    build_local_agent_contract,
    render_local_agent_contract,
)
from agents_shipgate.schemas.contract import CONTRACT_VERSION, GATING_SIGNAL


def test_local_agent_contract_is_minimal_agent_operational_payload() -> None:
    payload = build_local_agent_contract().model_dump(mode="json")

    assert list(payload) == [
        "schema_version",
        "agents_shipgate_version",
        "contract_version",
        "default_paths",
        "commands",
        "artifacts",
        "agent_read_order",
        "verifier_read_order",
        "gating_signal",
        "verifier_schema_version",
        "verify_run_schema_version",
        "agent_handoff_schema_version",
        "agent_handoff_schema_path",
        "agent_handoff_artifact",
        "codex_boundary_result_schema_version",
        "attestation_schema_version",
        "registry_schema_version",
        "org_evidence_bundle_schema_version",
        "host_grants_inventory_schema_version",
        "agent_result_schema_version",
        "agent_result_schema_path",
        "agent_result_control_fields",
        "agent_interface_operations",
        "exit_code_policy",
        "mcp_tools",
        "merge_verdicts",
        "release_decisions",
        "do_not_auto_assert",
    ]
    assert payload["schema_version"] == LOCAL_CONTRACT_SCHEMA_VERSION
    assert payload["agents_shipgate_version"] == __version__
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["default_paths"]["local_contract"] == LOCAL_CONTRACT_RELATIVE_PATH
    assert payload["commands"]["install_agent_workflow"] == (
        "agents-shipgate init --workspace . --write --ci --agent-instructions=default --json"
    )
    assert payload["commands"]["agent_check_codex"] == (
        "shipgate check --agent codex --workspace . --format codex-boundary-json"
    )
    assert payload["commands"]["agent_check_claude_code"] == (
        "shipgate check --agent claude-code --workspace . --format codex-boundary-json"
    )
    assert payload["commands"]["agent_check_cursor"] == (
        "shipgate check --agent cursor --workspace . --format codex-boundary-json"
    )
    assert payload["artifacts"]["verifier"] == "agents-shipgate-reports/verifier.json"
    assert payload["artifacts"]["verify_run"] == "agents-shipgate-reports/verify-run.json"
    assert payload["artifacts"]["agent_handoff"] == "agents-shipgate-reports/agent-handoff.json"
    assert payload["agent_read_order"] == [
        "agent-handoff.json",
        "verifier.json.merge_verdict",
        "verifier.json.agent_controller",
        "verify-run.json",
        "report.json.release_decision.decision",
    ]
    assert payload["verifier_read_order"][0] == "merge_verdict"
    assert payload["gating_signal"] == GATING_SIGNAL
    assert payload["verifier_schema_version"] == "0.1"
    assert payload["verify_run_schema_version"] == "shipgate.verify_run/v1"
    assert payload["agent_handoff_schema_version"] == "shipgate.agent_handoff/v1"
    assert payload["agent_handoff_schema_path"] == "docs/agent-handoff-schema.v1.json"
    assert payload["agent_handoff_artifact"] == "agents-shipgate-reports/agent-handoff.json"
    assert (
        payload["codex_boundary_result_schema_version"]
        == "shipgate.codex_boundary_result/v1"
    )
    assert payload["attestation_schema_version"] == "0.4"
    assert payload["registry_schema_version"] == "0.3"
    assert payload["org_evidence_bundle_schema_version"] == (
        "shipgate.org_evidence_bundle/v1"
    )
    assert payload["host_grants_inventory_schema_version"] == "0.1"
    assert payload["agent_result_schema_version"] == "agent_result_v1"
    assert payload["agent_result_schema_path"] == "docs/agent-result-schema.v1.json"
    assert payload["agent_result_control_fields"] == [
        "decision",
        "completion_allowed",
        "must_stop",
        "first_next_action",
        "human_review",
        "repair",
        "policy",
    ]
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


def test_local_agent_contract_renders_stable_pretty_json() -> None:
    rendered = render_local_agent_contract()

    assert rendered.endswith("\n")
    parsed = json.loads(rendered)
    assert parsed == build_local_agent_contract().model_dump(mode="json")
