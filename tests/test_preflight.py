from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.preflight import _read_plan
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.host_grants import build_host_grants_baseline, host_audit_inventory
from agents_shipgate.core.preflight import (
    build_preflight_result,
    build_trust_root_graph,
    classify_protected_touches,
    forbidden_file_edits,
    required_evidence_for_capability_request,
)
from agents_shipgate.schemas.preflight import (
    CapabilityRequestV1,
    PreflightResultV1,
    PreflightResultV2,
    PreflightResultV3,
)

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
    assert result.control.state == "human_review_required"
    assert result.control.completion_allowed is False
    assert result.control.must_stop is True
    assert result.control.next_action.kind == "stop"
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
        (".cursor/cli.json", "host_boundary", "whole_file"),
        ("AGENTS.override.md", "host_boundary", "whole_file"),
        (".github/workflows/deploy.yml", "host_boundary", "whole_file"),
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


def test_every_registered_repository_boundary_path_is_preflight_protected() -> None:
    candidates: list[str] = []
    for adapter in BOUNDARY_ADAPTERS:
        candidates.extend(adapter.exact_paths)
        candidates.extend(
            pattern.replace("**", "nested").replace("*", "item")
            for pattern in adapter.globs
        )

    touches = classify_protected_touches(candidates)

    assert {touch.path for touch in touches} == set(candidates)


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
    changed_patterns = {node.pattern for node in head.trust_root_graph.nodes if node.id in modified}
    assert "**/policies/**" in changed_patterns
    assert "**/.codex/hooks/**" in changed_patterns


def test_base_preflight_accepts_legacy_v1_payload(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    base = build_preflight_result(workspace=root)
    base_payload = {
        field: value
        for field, value in base.model_dump(mode="json").items()
        if field in PreflightResultV1.model_fields
    }
    base_payload["preflight_schema_version"] = "0.1"
    legacy_base = PreflightResultV1.model_validate(base_payload)

    (root / "AGENTS.md").write_text("Run Shipgate before completion.\n", encoding="utf-8")
    head = build_preflight_result(workspace=root, base_preflight=legacy_base)

    assert head.preflight_schema_version == "0.3"
    assert head.trust_root_graph_diff is not None
    assert head.trust_root_graph_diff.changed is True


def test_preflight_plan_routes_multiple_capability_and_host_requests(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)

    result = build_preflight_result(
        workspace=root,
        plan={
            "schema_version": "preflight_plan_v1",
            "changed_files": ["docs/readme.md"],
            "capability_requests": [
                {"tool_name": "lookup_case", "effect": "read"},
                {
                    "tool_name": "refund_customer",
                    "provider": "stripe",
                    "effect": "financial_write",
                    "risk_tags": ["financial_action"],
                    "scopes": ["*"],
                },
            ],
            "host_permission_requests": [
                {
                    "host": "claude-code",
                    "surface": "permissions.allow",
                    "operation": "add",
                    "path": ".claude/settings.json",
                    "subject": "Bash(*)",
                    "requested_access": {"allow": ["Bash(*)"]},
                    "reason": "let the agent run any shell command",
                }
            ],
            "context": {"agent": "codex", "task": "add refund support"},
        },
    )

    assert result.preflight_schema_version == "0.3"
    assert result.requires_human_review is True
    assert result.requires_verify is True
    assert result.plan_summary["capability_request_count"] == 2
    assert result.plan_summary["host_permission_request_count"] == 1
    assert result.first_next_action.actor == "human"
    assert result.control.state == "human_review_required"
    assert {signal.kind for signal in result.signals} >= {
        "least_privilege",
        "missing_evidence",
        "verify_required",
    }


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
    assert payload["preflight_schema_version"] == "0.3"
    assert payload["requires_human_review"] is True
    assert payload["requires_verify"] is True
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["must_stop"] is True
    assert {touch["path"] for touch in payload["protected_surface_touches"]} == {
        ".codex/config.toml",
        "shipgate.yaml",
    }
    assert any(signal["kind"] == "protected_surface_touch" for signal in payload["signals"])


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
    assert any(
        item["id"].endswith(":approval_policy")
        for item in payload["required_evidence"]
        if not item["satisfied"]
    )


def test_cli_preflight_plan_stdin_routes_clean_docs_to_verify(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plan = {
        "schema_version": "preflight_plan_v1",
        "changed_files": ["docs/readme.md"],
        "context": {"agent": "codex", "task": "update docs"},
    }

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--plan",
            "-",
            "--json",
        ],
        input=json.dumps(plan),
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_schema_version"] == "0.3"
    assert payload["requires_human_review"] is False
    assert payload["first_next_action"]["kind"] == "verify"
    assert payload["allowed_next_commands"] == [
        "agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --json"
    ]
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["completion_allowed"] is False
    assert payload["control"]["must_stop"] is False
    assert payload["control"]["next_action"]["kind"] == "verify"


def test_cli_preflight_plan_empty_stdin_is_empty_plan(tmp_path: Path) -> None:
    root = _workspace(tmp_path)

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--plan",
            "-",
            "--json",
        ],
        input="",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_schema_version"] == "0.3"
    assert payload["changed_files"] == []
    assert payload["requires_human_review"] is False
    assert payload["requires_verify"] is False
    assert payload["first_next_action"]["kind"] == "continue"
    assert payload["control"]["state"] == "complete"
    assert payload["control"]["completion_allowed"] is True
    assert payload["control"]["must_stop"] is False


def test_base_preflight_accepts_frozen_v2_payload(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    current = build_preflight_result(workspace=root)
    payload = {
        field: value
        for field, value in current.model_dump(mode="json").items()
        if field in PreflightResultV2.model_fields
    }
    payload["preflight_schema_version"] = "0.2"
    legacy = PreflightResultV2.model_validate(payload)

    head = build_preflight_result(
        workspace=root,
        changed_files=["docs/readme.md"],
        base_preflight=legacy,
    )

    assert isinstance(head, PreflightResultV3)
    assert head.preflight_schema_version == "0.3"
    assert head.control.state == "agent_action_required"


def test_preflight_legacy_projection_cannot_contradict_control_in_model_or_schema(
    tmp_path: Path,
) -> None:
    payload = build_preflight_result(workspace=_workspace(tmp_path)).model_dump(mode="json")
    payload["first_next_action"] = {
        "actor": "coding_agent",
        "kind": "verify",
        "command": "agents-shipgate verify --json",
        "why": "Contradict complete control.",
    }
    with pytest.raises(ValidationError):
        PreflightResultV3.model_validate(payload)
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "docs/preflight-schema.v0.3.json").read_text(
            encoding="utf-8"
        )
    )
    assert list(Draft202012Validator(schema).iter_errors(payload))


def test_read_plan_tty_stdin_is_empty_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    class TtyStdin:
        def isatty(self) -> bool:
            return True

        def read(self) -> str:
            raise AssertionError("TTY plan stdin should not be read")

    monkeypatch.setattr(sys, "stdin", TtyStdin())

    plan = _read_plan(Path("-"))

    assert plan.changed_files == []
    assert plan.capability_requests == []
    assert plan.host_permission_requests == []


def test_cli_preflight_plan_file_rejects_legacy_flag_mix(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    plan = tmp_path / "plan.json"
    changed = tmp_path / "changed.txt"
    plan.write_text('{"schema_version": "preflight_plan_v1"}\n', encoding="utf-8")
    changed.write_text("shipgate.yaml\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--plan",
            str(plan),
            "--changed-files",
            str(changed),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "--plan cannot be combined with --changed-files" in result.output


def test_cli_preflight_reports_host_grant_drift_when_baseline_present(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    baseline = build_host_grants_baseline(host_audit_inventory(root))
    baseline_path = root / ".agents-shipgate" / "host-grants.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    _write(
        root,
        ".claude/settings.json",
        json.dumps({"permissions": {"allow": ["Bash(*)"]}}),
    )

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host_grant_drift"]["has_drift"] is True
    assert payload["first_next_action"]["actor"] == "human"
    assert any(signal["kind"] == "host_grant_drift" for signal in payload["signals"])


def test_cli_preflight_default_corrupt_host_baseline_warns_and_continues(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    baseline_path = root / ".agents-shipgate" / "host-grants.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text("{", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host_grant_drift"] is None
    assert any("host-grant drift skipped" in note for note in payload["notes"])
    assert not any(signal["kind"] == "host_grant_drift" for signal in payload["signals"])


def test_cli_preflight_explicit_missing_or_corrupt_host_baseline_fails(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    missing = tmp_path / "missing-baseline.json"
    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--host-baseline",
            str(missing),
            "--json",
        ],
    )
    assert result.exit_code == 2
    assert "No host-grants baseline" in result.output

    corrupt = tmp_path / "corrupt-baseline.json"
    corrupt.write_text("{", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "preflight",
            "--workspace",
            str(root),
            "--host-baseline",
            str(corrupt),
            "--json",
        ],
    )
    assert result.exit_code == 2
    assert "not valid JSON" in result.output


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
    active_check_ids = {finding.check_id for finding in report.findings if not finding.suppressed}
    assert {
        "SHIP-POLICY-APPROVAL-MISSING",
        "SHIP-SIDEFX-IDEMPOTENCY-MISSING",
    } & active_check_ids
