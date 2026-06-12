from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.preflight import (
    build_preflight_result,
    build_trust_root_graph,
    forbidden_file_edits,
    required_evidence_for_capability_request,
)
from agents_shipgate.schemas.preflight import CapabilityRequestV1

runner = CliRunner()


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: preflight-test
agent:
  name: support-agent
  declared_purpose:
    - answer support questions
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )
    (root / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (root / "AGENTS.md").write_text("Run Shipgate.\n", encoding="utf-8")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "agents-shipgate.yml").write_text(
        "name: Agents Shipgate\n",
        encoding="utf-8",
    )
    (root / ".codex").mkdir()
    (root / ".codex" / "config.toml").write_text("[profiles.default]\n", encoding="utf-8")
    return root


def _write(root: Path, path: str, text: str = "x\n") -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_preflight_routes_protected_surface_touches_to_human(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = build_preflight_result(
        workspace=root,
        changed_files=[
            "shipgate.yaml",
            ".github/workflows/agents-shipgate.yml",
            ".codex/config.toml",
            "src/agent.py",
        ],
    )

    assert result.requires_human_review is True
    assert result.first_next_action.actor == "human"
    by_path = {touch.path: touch for touch in result.protected_surface_touches}
    assert by_path["shipgate.yaml"].kind == "manifest"
    assert by_path[".github/workflows/agents-shipgate.yml"].kind == "ci_gate"
    assert by_path[".codex/config.toml"].kind == "codex_config"
    assert "**/shipgate.yaml" not in forbidden_file_edits()
    assert any("AGENTS.md" in pattern for pattern in result.forbidden_file_edits)
    assert any(".codex/config.toml" in pattern for pattern in result.forbidden_file_edits)


@pytest.mark.parametrize(
    "path,expected_kind,expected_scope_type",
    [
        ("shipgate.yaml", "manifest", "key_level"),
        (".github/workflows/agents-shipgate.yml", "ci_gate", "whole_file"),
        ("AGENTS.md", "agent_instructions", "whole_file"),
        ("CLAUDE.md", "agent_instructions", "whole_file"),
        (".cursor/rules/agents-shipgate.mdc", "agent_instructions", "whole_file"),
        ("policies/refund.yaml", "policy", "whole_file"),
        (".agents-shipgate/baseline.json", "shipgate_state", "key_level"),
        (".agents-shipgate/waivers.json", "shipgate_state", "key_level"),
        (".codex/config.toml", "codex_config", "whole_file"),
        (".codex/hooks/preflight.sh", "codex_hooks", "whole_file"),
        (".codex-plugin/plugin.json", "codex_plugin", "capability_surface"),
        ("servers/refund/.mcp.json", "tool_surface_decl", "capability_surface"),
        ("plugins/refund/.app.json", "tool_surface_decl", "capability_surface"),
        ("skills/refund/SKILL.md", "tool_surface_decl", "capability_surface"),
    ],
)
def test_preflight_protected_surface_coverage(
    tmp_path: Path,
    path: str,
    expected_kind: str,
    expected_scope_type: str,
) -> None:
    root = _workspace(tmp_path)

    result = build_preflight_result(workspace=root, changed_files=[path])

    assert result.requires_human_review is True
    assert result.protected_surface_touches[0].path == path
    assert result.protected_surface_touches[0].kind == expected_kind
    assert result.protected_surface_touches[0].scope_type == expected_scope_type


def test_capability_request_review_requires_evidence_for_financial_write() -> None:
    request = CapabilityRequestV1(
        tool_name="refund_customer",
        provider="stripe",
        operation="refund_customer",
        effect="financial_write",
        risk_tags=["financial_action"],
    )

    evidence = required_evidence_for_capability_request(request)
    missing = {item.id for item in evidence if not item.satisfied}

    assert {"approval_policy", "idempotency", "auth_scopes", "owner"} <= missing
    assert any(item.severity == "critical" for item in evidence)


def test_capability_request_required_evidence_sorts_by_severity() -> None:
    request = CapabilityRequestV1(
        tool_name="deploy_service",
        effect="production_operation",
        risk_tags=["production_operation"],
    )

    evidence = required_evidence_for_capability_request(request)

    severities = [item.severity for item in evidence]
    assert severities[0] == "critical"
    assert severities[-1] == "medium"


def test_read_only_capability_request_has_no_required_evidence() -> None:
    request = CapabilityRequestV1(tool_name="lookup_case", effect="read")

    assert required_evidence_for_capability_request(request) == []


def test_policy_and_trust_root_hashes_are_deterministic(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    first = build_preflight_result(workspace=root)
    second = build_preflight_result(workspace=root)

    assert first.policy_snapshot_hash == second.policy_snapshot_hash
    assert first.trust_root_graph_hash == second.trust_root_graph_hash
    assert build_trust_root_graph(root).graph_hash == first.trust_root_graph_hash


@pytest.mark.parametrize(
    "pattern,path",
    [
        ("**/.agents-shipgate/**", ".agents-shipgate/baseline.json"),
        ("**/policies/**", "policies/refund.yaml"),
        ("**/prompts/**", "prompts/refund.md"),
        ("**/.claude/**", ".claude/settings.json"),
        ("**/.cursor/rules/**", ".cursor/rules/agents-shipgate.mdc"),
        ("**/.agents/skills/**", ".agents/skills/agents-shipgate/SKILL.md"),
        ("**/.codex/**", ".codex/config.toml"),
        ("**/.codex/hooks/**", ".codex/hooks/preflight.sh"),
        ("**/.codex-plugin/**", ".codex-plugin/plugin.json"),
    ],
)
def test_trust_root_graph_records_recursive_pattern_files(
    tmp_path: Path,
    pattern: str,
    path: str,
) -> None:
    root = _workspace(tmp_path)
    if not (root / path).exists():
        _write(root, path)

    graph = build_trust_root_graph(root)

    node = next(node for node in graph.nodes if node.pattern == pattern)
    assert path in node.present_paths
    assert node.file_hashes[path].startswith("sha256:")


def test_base_preflight_reports_trust_root_graph_drift(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    base = build_preflight_result(workspace=root)
    (root / "AGENTS.md").write_text("Run Shipgate before completion.\n", encoding="utf-8")

    head = build_preflight_result(workspace=root, base_preflight=base)

    assert head.trust_root_graph_diff is not None
    assert head.trust_root_graph_diff.changed is True
    assert head.policy_drift is not None


def test_base_preflight_reports_recursive_trust_root_graph_drift(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    _write(root, "policies/refund.yaml", "limit: 100\n")
    _write(root, ".codex/hooks/preflight.sh", "echo OK\n")
    base = build_preflight_result(workspace=root)

    _write(root, "policies/refund.yaml", "limit: 999999\n")
    _write(root, ".codex/hooks/preflight.sh", "echo HACKED\n")
    head = build_preflight_result(workspace=root, base_preflight=base)

    assert head.trust_root_graph_diff is not None
    assert head.trust_root_graph_diff.changed is True
    modified = set(head.trust_root_graph_diff.modified)
    changed_patterns = {
        node.pattern for node in head.trust_root_graph.nodes if node.id in modified
    }
    assert "**/policies/**" in changed_patterns
    assert "**/.codex/hooks/**" in changed_patterns


def test_cli_preflight_json_changed_files_and_diff(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    changed = tmp_path / "changed.txt"
    changed.write_text("shipgate.yaml\n", encoding="utf-8")
    diff = tmp_path / "change.diff"
    diff.write_text(
        "diff --git a/.codex/config.toml b/.codex/config.toml\n"
        "--- a/.codex/config.toml\n"
        "+++ b/.codex/config.toml\n"
        "@@ -1 +1 @@\n"
        "-[profiles.default]\n"
        "+[profiles.default]\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--changed-files",
            str(changed),
            "--diff",
            str(diff),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_schema_version"] == "0.1"
    assert payload["requires_human_review"] is True
    assert {touch["path"] for touch in payload["protected_surface_touches"]} == {
        ".codex/config.toml",
        "shipgate.yaml",
    }


def test_cli_preflight_capability_request(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "tool_name": "refund_customer",
                "effect": "financial_write",
                "risk_tags": ["financial_action"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--capability-request",
            str(request),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["first_next_action"]["kind"] == "gather_evidence"
    assert "approval_policy" in {
        item["id"] for item in payload["required_evidence"] if not item["satisfied"]
    }


def test_high_risk_capability_without_evidence_does_not_pass(tmp_path: Path) -> None:
    report, _exit_code = run_scan(
        config_path=Path("samples/support_refund_agent/shipgate.yaml"),
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision in {"blocked", "insufficient_evidence"}
    active_check_ids = {
        finding.check_id for finding in report.findings if not finding.suppressed
    }
    assert {
        "SHIP-POLICY-APPROVAL-MISSING",
        "SHIP-SIDEFX-IDEMPOTENCY-MISSING",
    } & active_check_ids
