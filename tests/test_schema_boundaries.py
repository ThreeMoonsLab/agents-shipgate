from __future__ import annotations

import ast
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


def _collect_removed_schema_imports(path: Path, offenders: list[str]) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in REMOVED_SCHEMA_IMPORTS:
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.module}"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in REMOVED_SCHEMA_IMPORTS:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {alias.name}"
                    )


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
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.module}"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefixes):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {alias.name}"
                    )


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
    assert report_payload["report_schema_version"] == "0.25"
    assert list(report_payload) == [
        "schema_version",
        "report_schema_version",
        "run_id",
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
        "source_warnings",
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
    assert packet_payload["packet_schema_version"] == "0.7"
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
        "schema_version": "0.5",
        "project": {},
        "agent": {},
        "created_at": "2026-01-01T00:00:00Z",
        "source_report_run_id": "run-1",
        "findings": [
            {
                "fingerprint": "abc",
                "check_id": "SHIP-TEST",
                "tool_name": None,
                "severity": "high",
                "title": "Example",
                "provenance": None,
            }
        ],
        "tool_surface_facts": None,
        "action_surface_facts": None,
        "notes": [],
    }

    assert ContractPayload(
        contract_version="1",
        cli_version="0.0.0",
        report_schema_version="0.17",
        packet_schema_version="0.6",
        gating_signal="release_decision.decision",
        manual_review_signals=[],
    ).model_dump(mode="json") == {
        "contract_version": "1",
        "cli_version": "0.0.0",
        "report_schema_version": "0.17",
        "packet_schema_version": "0.6",
        "gating_signal": "release_decision.decision",
        "manual_review_signals": [],
    }

    assert DetectResult(is_agent_project=False).model_dump(mode="json") == {
        "is_agent_project": False,
        "frameworks": [],
        "agent_name_candidates": [],
        "project_name_candidates": [],
        "suggested_sources": [],
        "codex_plugin_candidates": [],
        "next_action": "",
        "workspace_signals": {
            "python_file_count": 0,
            "has_pyproject_or_requirements": False,
            "has_prompts_dir": False,
            "has_tools_dir": False,
            "conventional_dirs": [],
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
            }
        ],
    }
