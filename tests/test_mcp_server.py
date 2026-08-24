"""Tests for the optional read-only MCP adapter."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.mcp_server import (
    build_server,
    shipgate_capabilities,
    shipgate_check,
    shipgate_explain,
    shipgate_handoff,
    shipgate_preflight,
)

_HAS_MCP_SDK = importlib.util.find_spec("mcp") is not None


def _snapshot(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def test_shipgate_check_returns_boundary_result_without_writes(tmp_path: Path) -> None:
    diff = """diff --git a/.codex/config.toml b/.codex/config.toml
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/.codex/config.toml
@@ -0,0 +1,4 @@
+[mcp_servers.filesystem]
+command = "filesystem-mcp"
+enabled_tools = ["write_file"]
+default_tools_approval_mode = "approve"
"""
    before = _snapshot(tmp_path)

    payload = shipgate_check(
        agent="cursor",
        workspace=str(tmp_path),
        diff_text=diff,
    )

    after = _snapshot(tmp_path)
    assert after == before
    assert payload["schema_version"] == "shipgate.agent_boundary_result/v2"
    assert payload["agent"] == "cursor"
    assert payload["actor"] == "cursor"
    assert payload["affected_hosts"] == ["codex"]
    assert payload["input_coverage"] == "complete"
    assert payload["decision"] == "block"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["next_action"]["kind"] == "stop"
    json.dumps(payload)


def test_mcp_check_never_authorizes_verify_for_detached_diff_text(
    tmp_path: Path,
) -> None:
    payload = shipgate_check(
        workspace=str(tmp_path),
        diff_text=(
            "diff --git a/.codex/config.toml b/.codex/config.toml\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/.codex/config.toml\n"
            "@@ -0,0 +1,3 @@\n"
            "+[permissions.workspace.network]\n"
            "+enabled = true\n"
            '+surprise = "value"\n'
        ),
    )

    assert payload["decision"] == "require_review"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["allowed_next_commands"] == []
    assert payload["control"]["next_action"]["command"] is None
    assert "not bound to a checkout state" in payload["control"]["stop_reason"]
    assert payload["summary"] == payload["control"]["reason"]
    assert "Re-run check against the intended worktree" in payload["summary"]


def test_mcp_preflight_handler_is_read_only(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)
    before = _snapshot(workspace)

    payload = shipgate_preflight(
        workspace=str(workspace),
        changed_files=["shipgate.yaml"],
        diff_text=(
            "diff --git a/.cursor/rules/agents-shipgate.mdc "
            "b/.cursor/rules/agents-shipgate.mdc\n"
            "--- a/.cursor/rules/agents-shipgate.mdc\n"
            "+++ b/.cursor/rules/agents-shipgate.mdc\n"
        ),
    )

    assert payload["preflight_schema_version"] == "0.3"
    assert payload["requires_human_review"] is True
    assert payload["requires_verify"] is True
    assert payload["control"]["state"] == "human_review_required"
    assert {touch["path"] for touch in payload["protected_surface_touches"]} >= {
        "shipgate.yaml",
        ".cursor/rules/agents-shipgate.mdc",
    }
    assert any(signal["kind"] == "protected_surface_touch" for signal in payload["signals"])
    assert _snapshot(workspace) == before


def test_mcp_preflight_keeps_the_source_side_of_a_rename(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)

    payload = shipgate_preflight(
        workspace=str(workspace),
        diff_text=(
            "diff --git a/shipgate.yaml b/retired.txt\n"
            "similarity index 100%\n"
            "rename from shipgate.yaml\n"
            "rename to retired.txt\n"
        ),
    )

    assert payload["changed_files"] == ["retired.txt", "shipgate.yaml"]
    assert payload["requires_human_review"] is True
    assert any(
        touch["path"] == "shipgate.yaml"
        for touch in payload["protected_surface_touches"]
    )


def test_mcp_preflight_accepts_plan_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)
    before = _snapshot(workspace)

    payload = shipgate_preflight(
        workspace=str(workspace),
        plan={
            "schema_version": "preflight_plan_v1",
            "changed_files": ["docs/readme.md"],
            "host_permission_requests": [
                {
                    "host": "claude-code",
                    "surface": "permissions.allow",
                    "operation": "add",
                    "path": ".claude/settings.json",
                    "subject": "Write(*)",
                    "requested_access": {"allow": ["Write(*)"]},
                    "reason": "auto approve write tools",
                }
            ],
        },
    )

    assert payload["preflight_schema_version"] == "0.3"
    assert payload["first_next_action"]["actor"] == "human"
    assert payload["control"]["state"] == "human_review_required"
    assert any(signal["kind"] == "least_privilege" for signal in payload["signals"])
    assert _snapshot(workspace) == before


def test_mcp_preflight_rejects_plan_combined_with_direct_diff(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)

    with pytest.raises(
        ConfigError,
        match=r"plan cannot be combined with diff_text",
    ):
        shipgate_preflight(
            workspace=str(workspace),
            plan={"schema_version": "preflight_plan_v1"},
            diff_text=(
                "diff --git a/shipgate.yaml b/shipgate.yaml\n"
                "--- a/shipgate.yaml\n"
                "+++ b/shipgate.yaml\n"
                "@@ -1 +1 @@\n"
                '-version: "0.1"\n'
                '+version: "0.2"\n'
            ),
        )


def test_mcp_preflight_rejects_mixed_shape_before_validating_direct_input(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)

    with pytest.raises(
        ConfigError,
        match=r"plan cannot be combined with capability_request",
    ):
        shipgate_preflight(
            workspace=str(workspace),
            plan={"schema_version": "preflight_plan_v1"},
            capability_request={"not": "a capability request"},
        )


def test_mcp_explain_handler_returns_check_metadata() -> None:
    payload = shipgate_explain(
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        no_plugins=True,
    )

    assert payload["id"] == "SHIP-POLICY-APPROVAL-MISSING"
    assert payload["category"] == "policy"


def test_mcp_capabilities_handler_does_not_write_reports(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)
    before = _snapshot(workspace)

    payload = shipgate_capabilities(
        config=str(workspace / "shipgate.yaml"),
        no_plugins=True,
    )

    assert payload["capability_lock_schema_version"] == "0.7"
    assert _snapshot(workspace) == before


def test_mcp_handoff_handler_is_read_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "verifier.json",
        {
            "workspace": str(tmp_path),
            "config": "shipgate.yaml",
            "execution": "succeeded",
            "head_status": "succeeded",
            "diff_status": {"completeness": "complete"},
            "release_decision": {
                "decision": "passed",
                "reason": "All required static verification passed.",
                "blockers": [],
                "review_items": [],
                "evidence_coverage": {
                    "level": "complete",
                    "human_review_recommended": False,
                    "source_warning_count": 0,
                    "low_confidence_tool_count": 0,
                    "evidence_gaps": [],
                },
                "baseline_delta": {"enabled": False},
                "fail_policy": {
                    "ci_mode": "advisory",
                    "fail_on": [],
                    "new_findings_only": False,
                    "would_fail_ci": False,
                    "exit_code": 0,
                },
            },
            "decision": "passed",
            "merge_verdict": "mergeable",
            "applicability": "verified",
            "can_merge_without_human": True,
                "control": {
                "state": "complete",
                "reason": "All required static verification passed.",
                "completion_allowed": True,
                "must_stop": False,
                "verify_required": False,
                "next_action": None,
                "human_review": {
                    "required": False,
                    "why": None,
                    "required_reviewers": [],
                },
                "stop_reason": None,
                    "allowed_next_commands": [],
                },
                "authorization": {
                    "schema_version": "shipgate.human_authorization_evaluation/v1",
                    "status": "not_requested",
                    "authorization_id": None,
                    "authorization_request_id": None,
                    "trust_policy_id": None,
                    "key_id": None,
                    "provider": None,
                    "principal": None,
                    "operation_id": None,
                    "command": None,
                    "issued_at": None,
                    "expires_at": None,
                    "reason_codes": [],
                },
            "forbidden_file_edits": [],
            "forbidden_actions": [],
            "artifacts": {
                "verifier_json": str(output_dir / "verifier.json"),
                "agent_handoff_json": str(output_dir / "agent-handoff.json"),
            },
        },
    )
    _write_json(output_dir / "verify-run.json", {"run_id": "sha256:" + "b" * 64})
    before = _snapshot(tmp_path)

    payload = shipgate_handoff(verifier_path=str(output_dir / "verifier.json"))

    assert payload["schema_version"] == "shipgate.agent_handoff/v7"
    assert payload["gate"]["merge_verdict"] == "mergeable"
    assert payload["control"]["state"] == "complete"
    assert _snapshot(tmp_path) == before


@pytest.mark.skipif(_HAS_MCP_SDK, reason="mcp extra installed; error path n/a")
def test_build_server_without_sdk_raises_config_error() -> None:
    with pytest.raises(ConfigError, match=r"agents-shipgate\[mcp\]"):
        build_server()


@pytest.mark.skipif(not _HAS_MCP_SDK, reason="requires the optional mcp extra")
def test_build_server_registers_read_only_tools() -> None:
    server = build_server()

    import asyncio

    listed = asyncio.run(server.list_tools())
    assert {tool.name for tool in listed} == {
        "shipgate.capabilities",
        "shipgate.check",
        "shipgate.explain",
        "shipgate.handoff",
        "shipgate.preflight",
    }
    # Every tool advertises its read-only, closed-world nature in the
    # machine-readable contract, not just the server prose instructions.
    for tool in listed:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.readOnlyHint is True, tool.name
        assert tool.annotations.openWorldHint is False, tool.name


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
