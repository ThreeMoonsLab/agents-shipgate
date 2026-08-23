from __future__ import annotations

import ast
import json
from pathlib import Path

from agents_shipgate.packet.json_packet import serialize_packet_json
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.baseline import BaselineFile, BaselineFinding
from agents_shipgate.schemas.contract import ContractPayload
from agents_shipgate.schemas.detect import DetectResult
from agents_shipgate.schemas.diagnostics import Diagnostic, NextAction
from agents_shipgate.schemas.packet import (
    ApprovalCoverageSection,
    CapabilityIntentDiff,
    DynamicScenariosSection,
    EvidencePacket,
    HighRiskSurfaceSection,
    HumanInTheLoopEvidence,
    IdempotencyRiskSection,
    MemoryIsolationStatus,
    NotProvenSection,
    ReleaseDecisionSection,
    ScopeCoverageSection,
)
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "agents_shipgate"
SCHEMAS_ROOT = SRC_ROOT / "schemas"
CHECK_ROOTS = (
    SRC_ROOT,
    REPO_ROOT / "tests",
    REPO_ROOT / "scripts",
)

REMOVED_SCHEMA_IMPORTS = {
    "agents_shipgate.config.schema",
    "agents_shipgate.contract",
    "agents_shipgate.core.models",
    "agents_shipgate.core.patches",
    "agents_shipgate.packet.models",
}
FORBIDDEN_SCHEMA_LAYER_PREFIXES = ("agents_shipgate.core",)
ACTION_FACT_SOURCE_FIELDS = {
    "source_path",
    "source_ref",
    "source_pointer",
    "source_location",
    "source_start_line",
    "source_start_column",
    "source_end_line",
}


def test_repo_does_not_import_removed_schema_boundaries() -> None:
    offenders: list[str] = []
    for root in CHECK_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == Path(__file__).resolve():
                continue
            _collect_removed_schema_imports(path, offenders)

    assert offenders == []


def test_schemas_do_not_import_core_modules() -> None:
    offenders: list[str] = []
    for path in sorted(SCHEMAS_ROOT.rglob("*.py")):
        _collect_forbidden_import_prefixes(
            path,
            offenders,
            prefixes=FORBIDDEN_SCHEMA_LAYER_PREFIXES,
        )

    assert offenders == []


def test_frozen_v025_report_schema_does_not_backport_v026_action_fact_sources() -> None:
    v25 = _report_schema_action_fact_properties("0.25")
    v26 = _report_schema_action_fact_properties("0.26")
    v27 = _report_schema_action_fact_properties("0.27")
    v28 = _report_schema_action_fact_properties("0.28")
    v29 = _report_schema_action_fact_properties("0.29")

    assert ACTION_FACT_SOURCE_FIELDS.isdisjoint(v25)
    assert ACTION_FACT_SOURCE_FIELDS.issubset(v26)
    assert ACTION_FACT_SOURCE_FIELDS.issubset(v27)
    assert ACTION_FACT_SOURCE_FIELDS.issubset(v28)
    assert ACTION_FACT_SOURCE_FIELDS.issubset(v29)


def test_frozen_v026_report_schema_does_not_backport_v027_policy_pack_metadata() -> None:
    v26 = _loaded_policy_pack_properties("0.26")
    v27 = _loaded_policy_pack_properties("0.27")
    v28 = _loaded_policy_pack_properties("0.28")
    v29 = _loaded_policy_pack_properties("0.29")
    v27_fields = {"source", "sha256", "sha256_status", "owner"}

    assert v27_fields.isdisjoint(v26)
    assert v27_fields.issubset(v27)
    assert v27_fields.issubset(v28)
    assert v27_fields.issubset(v29)


def test_frozen_v027_report_schema_does_not_backport_v028_policy_routing() -> None:
    v27 = _finding_properties("0.27")
    v28 = _finding_properties("0.28")

    assert "policy_routing" not in v27
    assert "policy_routing" in v28


def test_frozen_v028_report_schema_does_not_backport_v029_semantic_evidence() -> None:
    v28_action = _report_schema_action_fact_properties("0.28")
    v29_action = _report_schema_action_fact_properties("0.29")
    v28_capability = _report_schema_definition_properties("0.28", "CapabilityFact")
    v29_capability = _report_schema_definition_properties("0.29", "CapabilityFact")
    v28_coverage = _report_schema_definition_properties("0.28", "EvidenceCoverageDecision")
    v29_coverage = _report_schema_definition_properties("0.29", "EvidenceCoverageDecision")
    v28_decision = _report_schema_definition_properties("0.28", "ReleaseDecision")
    v29_decision = _report_schema_definition_properties("0.29", "ReleaseDecision")
    v28_gap_action = _report_schema_definition_properties("0.28", "EvidenceGapAction")
    v29_gap_action = _report_schema_definition_properties("0.29", "EvidenceGapAction")
    static_boundary = {
        "static_analysis_only",
        "runtime_behavior_verified",
        "static_verdict_disclaimer",
    }

    assert "semantic_assessment" not in v28_action
    assert "semantic_assessment" in v29_action
    assert "semantic_assessment" not in v28_capability
    assert "semantic_assessment" in v29_capability
    assert "semantic_coverage" not in v28_coverage
    assert "semantic_coverage" in v29_coverage
    assert static_boundary.isdisjoint(v28_decision)
    assert static_boundary.issubset(v29_decision)
    assert "suggested_patch_kind" not in v28_gap_action
    assert "suggested_patch_kind" in v29_gap_action


def test_frozen_v07_packet_schema_does_not_backport_v08_static_boundary() -> None:
    v7 = _packet_schema_definition_properties("0.7", "ReleaseDecisionSection")
    v8 = _packet_schema_definition_properties("0.8", "ReleaseDecisionSection")
    static_boundary = {
        "static_analysis_only",
        "runtime_behavior_verified",
        "static_verdict_disclaimer",
    }

    assert static_boundary.isdisjoint(v7)
    assert static_boundary.issubset(v8)
    v7_gap_action = _packet_schema_definition_properties("0.7", "EvidenceGapAction")
    v8_gap_action = _packet_schema_definition_properties("0.8", "EvidenceGapAction")
    assert "suggested_patch_kind" not in v7_gap_action
    assert "suggested_patch_kind" in v8_gap_action


def test_controller_schemas_publish_the_static_verdict_boundary() -> None:
    verifier = json.loads(
        (REPO_ROOT / "docs" / "verifier-schema.v0.1.json").read_text(encoding="utf-8")
    )
    handoff = json.loads(
        (REPO_ROOT / "docs" / "agent-handoff-schema.v2.json").read_text(encoding="utf-8")
    )
    boundary = {
        "static_analysis_only",
        "runtime_behavior_verified",
        "static_verdict_disclaimer",
    }

    assert boundary.issubset(verifier["properties"])
    assert boundary.issubset(handoff["$defs"]["AgentHandoffGate"]["properties"])
    assert verifier["properties"]["static_analysis_only"]["const"] is True
    assert verifier["properties"]["runtime_behavior_verified"]["const"] is False


def _report_schema_action_fact_properties(version: str) -> set[str]:
    schema = json.loads(
        (REPO_ROOT / "docs" / f"report-schema.v{version}.json").read_text(encoding="utf-8")
    )
    return set(schema["$defs"]["ActionFact"]["properties"])


def _loaded_policy_pack_properties(version: str) -> set[str]:
    schema = json.loads(
        (REPO_ROOT / "docs" / f"report-schema.v{version}.json").read_text(encoding="utf-8")
    )
    return set(schema["$defs"]["LoadedPolicyPack"]["properties"])


def _finding_properties(version: str) -> set[str]:
    schema = json.loads(
        (REPO_ROOT / "docs" / f"report-schema.v{version}.json").read_text(encoding="utf-8")
    )
    return set(schema["$defs"]["Finding"]["properties"])


def _report_schema_definition_properties(version: str, definition: str) -> set[str]:
    schema = json.loads(
        (REPO_ROOT / "docs" / f"report-schema.v{version}.json").read_text(encoding="utf-8")
    )
    return set(schema["$defs"][definition]["properties"])


def _packet_schema_definition_properties(version: str, definition: str) -> set[str]:
    schema = json.loads(
        (REPO_ROOT / "docs" / f"packet-schema.v{version}.json").read_text(encoding="utf-8")
    )
    return set(schema["$defs"][definition]["properties"])


def _collect_removed_schema_imports(path: Path, offenders: list[str]) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in REMOVED_SCHEMA_IMPORTS:
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in REMOVED_SCHEMA_IMPORTS:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {alias.name}")


def _collect_forbidden_import_prefixes(
    path: Path,
    offenders: list[str],
    *,
    prefixes: tuple[str, ...],
) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith(prefixes):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefixes):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {alias.name}")


def test_representative_schema_payloads_keep_wire_fields() -> None:
    report = ReadinessReport(
        run_id="run-1",
        project={"name": "demo"},
        agent={"name": "agent"},
        environment={"target": "local"},
        summary=ReportSummary(status="passed"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
    )
    report_payload = report_json_payload(report)
    assert report_payload["report_schema_version"] == "0.36"
    assert list(report_payload) == [
        "schema_version",
        "report_schema_version",
        "run_id",
        "request_id",
        "subject_id",
        "input_set_id",
        "engine_requirement_id",
        "decision_id",
        "manifest_dir",
        "project",
        "agent",
        "environment",
        "summary",
        "release_decision",
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
        "tool_surface",
        "tool_surface_facts",
        "tool_surface_diff",
        "action_surface_facts",
        "action_surface_diff",
        "binding_surface_facts",
        "binding_surface_diff",
        "capability_runtime_evidence",
        "api_surface",
        "anthropic_surface",
        "frameworks",
        "codex_plugin_surface",
        "baseline",
        "findings",
        "recommended_actions",
        "generated_reports",
        "loaded_policy_packs",
        "loaded_plugins",
        "loaded_adapters",
        "tool_inventory",
        "tool_catalog",
        "source_warnings",
        "policy_evidence_gaps",
        "agent_summary",
        "policy_audit",
        "privacy_audit",
        "heuristics_filter",
        "reviewer_summary",
        # v0.22+ verifier-cycle blocks (additive).
        "capability_change",
        "protected_surface_changes",
        "effective_policy",
        "human_ack",
        "verifier_summary",
        # v0.35: the exclusion ledger (additive).
        "surface_exclusions",
    ]

    packet = EvidencePacket(
        run_id="run-1",
        release_decision=ReleaseDecisionSection(
            decision="passed",
            verdict="PASSED",
            reason="ok",
            evidence_coverage=EvidenceCoverageDecision(
                level="static",
                human_review_recommended=False,
                source_warning_count=0,
                low_confidence_tool_count=0,
            ),
            baseline_delta=BaselineDelta(enabled=False),
            fail_policy=FailPolicy(
                ci_mode="advisory",
                would_fail_ci=False,
                exit_code=0,
            ),
        ),
        capability_intent=CapabilityIntentDiff(status="covered"),
        high_risk_surface=HighRiskSurfaceSection(status="covered"),
        approval_coverage=ApprovalCoverageSection(status="covered"),
        idempotency_risk=IdempotencyRiskSection(status="covered"),
        scope_coverage=ScopeCoverageSection(status="covered"),
        memory_isolation=MemoryIsolationStatus(),
        human_in_the_loop=HumanInTheLoopEvidence(status="covered"),
        dynamic_scenarios=DynamicScenariosSection(status="covered"),
        not_proven=NotProvenSection(headline="not proven"),
    )
    packet_payload = serialize_packet_json(packet)
    assert packet_payload["packet_schema_version"] == "0.12"
    assert "generated_at" not in packet_payload
    assert "action_surface_diff" in packet_payload
    assert report_payload["capability_runtime_evidence"]["enabled"] is False

    baseline = BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="run-1",
        findings=[
            BaselineFinding(
                fingerprint="abc",
                check_id="SHIP-TEST",
                severity="high",
                title="Example",
            )
        ],
    )
    assert baseline.model_dump(mode="json") == {
        "schema_version": "0.8",
        "project": {},
        "agent": {},
        "created_at": "2026-01-01T00:00:00Z",
        "source_report_run_id": "run-1",
        "findings": [
            {
                "fingerprint": "abc",
                "check_id": "SHIP-TEST",
                "tool_id": None,
                "tool_name": None,
                "fingerprint_version": "1",
                "severity": "high",
                "title": "Example",
                "provenance": None,
                "support_hash": None,
            }
        ],
        "tool_surface_facts": None,
        "action_surface_facts": None,
        "notes": [],
    }

    assert ContractPayload(
        contract_version="12",
        minimum_control_contract_version="12",
        cli_version="0.0.0",
        report_schema_version="0.31",
        packet_schema_version="0.9",
        verifier_schema_version="0.2",
        verify_run_schema_version="shipgate.verify_run/v1",
        verification_plan_schema_version="shipgate.verification_plan/v1",
        verification_unit_result_schema_version="shipgate.verification_unit_result/v1",
        verification_artifact_manifest_schema_version=(
            "shipgate.verification_artifact_manifest/v1"
        ),
        verification_receipt_schema_version="shipgate.verification_receipt/v1",
        current_control_schema_version="shipgate.current_control/v1",
        current_control_schema_path="docs/current-control-schema.v1.json",
        current_control_artifact="agents-shipgate-reports/current-control.json",
        agent_refresh_triggers=["before declaring the task complete"],
        current_control_fallback_read_order=["current-control.json"],
        agent_control_schema_version="shipgate.agent_control/v1",
        agent_control_schema_path="docs/agent-control-schema.v1.json",
        agent_control_budget_bytes=4096,
        human_authorization_request_schema_version=(
            "shipgate.human_authorization_request/v1"
        ),
        human_authorization_schema_version="shipgate.human_authorization/v1",
        human_authorization_evaluation_schema_version=(
            "shipgate.human_authorization_evaluation/v1"
        ),
        human_authorization_trust_policy_schema_version=(
            "shipgate.human_authorization_trust_policy/v1"
        ),
        human_authorization_trust_policy_default_path=(
            "~/.config/agents-shipgate/human-authorization-trust-policy.json"
        ),
        human_authorization_schema_path="docs/human-authorization-schema.v1.json",
        agent_handoff_schema_version="shipgate.agent_handoff/v2",
        agent_handoff_schema_path="docs/agent-handoff-schema.v2.json",
        agent_handoff_artifact="agents-shipgate-reports/agent-handoff.json",
        codex_boundary_result_schema_version="shipgate.codex_boundary_result/v1",
        agent_boundary_result_schema_version="shipgate.agent_boundary_result/v2",
        agent_boundary_result_schema_path="docs/agent-boundary-result-schema.v2.json",
        capability_lock_schema_version="0.4",
        capability_lock_diff_schema_version="0.5",
        preflight_schema_version="0.2",
        capability_standard_version="0.3",
        governance_benchmark_catalog_schema_version="0.2",
        governance_benchmark_result_schema_version="0.2",
        attestation_schema_version="0.5",
        registry_schema_version="0.4",
        org_evidence_bundle_schema_version="shipgate.org_evidence_bundle/v2",
        host_grants_inventory_schema_version="0.1",
        host_grants_baseline_schema_version="0.1",
        host_grants_drift_schema_version="0.1",
        trigger_catalog_schema_version="0.1",
        deprecated_surfaces={},
        external_integration_surfaces=[],
        gating_signal="release_decision.decision",
        agent_result_schema_version="agent_result_v1",
        agent_result_schema_path="docs/agent-result-schema.v1.json",
        agent_result_control_fields=["decision"],
        agent_control_fields=[],
        agent_control_permissions=[],
        agent_control_states=[],
        manual_review_signals=[],
        agent_interface_operations=["verify_pr"],
        exit_code_policy={"3": "input parse or missing artifact error"},
        mcp_tools=["shipgate.handoff"],
        primary_commands={"verify_pr": "agents-shipgate verify --json"},
        commands={"preview": "agents-shipgate verify --preview --json"},
        default_paths={"manifest": "shipgate.yaml"},
        artifacts={
            "verifier": "agents-shipgate-reports/verifier.json",
            "verify_run": "agents-shipgate-reports/verify-run.json",
            "agent_handoff": "agents-shipgate-reports/agent-handoff.json",
        },
        agent_read_order=[
            "agent-handoff.json",
            "verifier.json.merge_verdict",
            "verifier.json.agent_controller",
            "verify-run.json",
            "report.json.release_decision.decision",
        ],
        verifier_read_order=["merge_verdict"],
        merge_verdicts=["mergeable", "blocked"],
        release_decisions=["passed", "blocked"],
        do_not_auto_assert=["approval"],
    ).model_dump(mode="json") == {
        "contract_version": "12",
        "minimum_control_contract_version": "12",
        "cli_version": "0.0.0",
        "report_schema_version": "0.31",
        "packet_schema_version": "0.9",
        "verifier_schema_version": "0.2",
        "verify_run_schema_version": "shipgate.verify_run/v1",
        "verification_plan_schema_version": "shipgate.verification_plan/v1",
        "verification_unit_result_schema_version": "shipgate.verification_unit_result/v1",
        "verification_artifact_manifest_schema_version": (
            "shipgate.verification_artifact_manifest/v1"
        ),
        "verification_receipt_schema_version": "shipgate.verification_receipt/v1",
        "current_control_schema_version": "shipgate.current_control/v1",
        "current_control_schema_path": "docs/current-control-schema.v1.json",
        "current_control_artifact": "agents-shipgate-reports/current-control.json",
        "agent_refresh_triggers": ["before declaring the task complete"],
        "current_control_fallback_read_order": ["current-control.json"],
        "agent_control_schema_version": "shipgate.agent_control/v1",
        "agent_control_schema_path": "docs/agent-control-schema.v1.json",
        "agent_control_budget_bytes": 4096,
        "human_authorization_request_schema_version": (
            "shipgate.human_authorization_request/v1"
        ),
        "human_authorization_schema_version": "shipgate.human_authorization/v1",
        "human_authorization_evaluation_schema_version": (
            "shipgate.human_authorization_evaluation/v1"
        ),
        "human_authorization_trust_policy_schema_version": (
            "shipgate.human_authorization_trust_policy/v1"
        ),
        "human_authorization_trust_policy_default_path": (
            "~/.config/agents-shipgate/human-authorization-trust-policy.json"
        ),
        "human_authorization_schema_path": "docs/human-authorization-schema.v1.json",
        "agent_handoff_schema_version": "shipgate.agent_handoff/v2",
        "agent_handoff_schema_path": "docs/agent-handoff-schema.v2.json",
        "agent_handoff_artifact": "agents-shipgate-reports/agent-handoff.json",
        "codex_boundary_result_schema_version": "shipgate.codex_boundary_result/v1",
        "agent_boundary_result_schema_version": "shipgate.agent_boundary_result/v2",
        "agent_boundary_result_schema_path": "docs/agent-boundary-result-schema.v2.json",
        "capability_lock_schema_version": "0.4",
        "capability_lock_diff_schema_version": "0.5",
        "preflight_schema_version": "0.2",
        "capability_standard_version": "0.3",
        "governance_benchmark_catalog_schema_version": "0.2",
        "governance_benchmark_result_schema_version": "0.2",
        "attestation_schema_version": "0.5",
        "registry_schema_version": "0.4",
        "org_evidence_bundle_schema_version": "shipgate.org_evidence_bundle/v2",
        "host_grants_inventory_schema_version": "0.1",
        "host_grants_baseline_schema_version": "0.1",
        "host_grants_drift_schema_version": "0.1",
        "trigger_catalog_schema_version": "0.1",
        "deprecated_surfaces": {},
        "external_integration_surfaces": [],
        "gating_signal": "release_decision.decision",
        "agent_result_schema_version": "agent_result_v1",
        "agent_result_schema_path": "docs/agent-result-schema.v1.json",
        "agent_result_control_fields": ["decision"],
        "agent_control_fields": [],
        "agent_control_permissions": [],
        "agent_control_states": [],
        "manual_review_signals": [],
        "agent_interface_operations": ["verify_pr"],
        "exit_code_policy": {"3": "input parse or missing artifact error"},
        "mcp_tools": ["shipgate.handoff"],
        "primary_commands": {"verify_pr": "agents-shipgate verify --json"},
        "commands": {"preview": "agents-shipgate verify --preview --json"},
        "default_paths": {"manifest": "shipgate.yaml"},
        "artifacts": {
            "verifier": "agents-shipgate-reports/verifier.json",
            "verify_run": "agents-shipgate-reports/verify-run.json",
            "agent_handoff": "agents-shipgate-reports/agent-handoff.json",
        },
        "agent_read_order": [
            "agent-handoff.json",
            "verifier.json.merge_verdict",
            "verifier.json.agent_controller",
            "verify-run.json",
            "report.json.release_decision.decision",
        ],
        "verifier_read_order": ["merge_verdict"],
        "merge_verdicts": ["mergeable", "blocked"],
        "release_decisions": ["passed", "blocked"],
        "do_not_auto_assert": ["approval"],
    }

    assert DetectResult(is_agent_project=False).model_dump(mode="json") == {
        "is_agent_project": False,
        "frameworks": [],
        "agent_name_candidates": [],
        "project_name_candidates": [],
        "agent_scope": "single",
        "agent_project_candidates": [],
        "agent_scope_truncated": False,
        "python_parse_truncated": False,
        "suggested_sources": [],
        "excluded_sources": [],
        "codex_plugin_candidates": [],
        "next_action": "",
        "workspace_signals": {
            "python_file_count": 0,
            "python_file_total": 0,
            "project_root_count": 0,
            "has_pyproject_or_requirements": False,
            "has_prompts_dir": False,
            "has_tools_dir": False,
            "conventional_dirs": [],
        },
        "surface_exclusions": {
            "entries": [],
            "total": 0,
            "gated": 0,
            "gap_backed": 0,
            "truncated": False,
        },
    }

    diagnostic = Diagnostic(
        id="SHIP-DIAG-EXAMPLE",
        title="Example",
        severity="info",
        next_actions=[NextAction(kind="review", why="check")],
    )
    assert diagnostic.model_dump(mode="json") == {
        "id": "SHIP-DIAG-EXAMPLE",
        "title": "Example",
        "severity": "info",
        "next_actions": [
            {
                "kind": "review",
                "command": None,
                "path": None,
                "why": "check",
                "expects": None,
                # No executable/args. Contract v23 omits the pair rather than
                # emitting nulls, so an action that can never carry an argv is
                # byte-for-byte what it was before #322.
            }
        ],
    }

    command_action = Diagnostic(
        id="SHIP-DIAG-EXAMPLE",
        title="Example",
        severity="info",
        next_actions=[
            NextAction(
                kind="command",
                command="agents-shipgate detect --workspace . --json",
                why="check",
            )
        ],
    )
    assert command_action.model_dump(mode="json")["next_actions"] == [
        {
            "kind": "command",
            "command": "agents-shipgate detect --workspace . --json",
            "path": None,
            "why": "check",
            "expects": None,
            "executable": ["agents-shipgate"],
            "args": ["detect", "--workspace", ".", "--json"],
        }
    ]
