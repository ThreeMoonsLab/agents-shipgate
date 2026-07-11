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
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )


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
    assert payload["schema_version"] == "shipgate.codex_boundary_result/v1"
    assert payload["agent"] == "cursor"
    assert payload["decision"] == "block"
    assert payload["first_next_action"]["kind"] in {"repair", "stop"}
    json.dumps(payload)


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

    assert payload["preflight_schema_version"] == "0.2"
    assert payload["requires_human_review"] is True
    assert payload["requires_verify"] is True
    assert {
        touch["path"] for touch in payload["protected_surface_touches"]
    } >= {"shipgate.yaml", ".cursor/rules/agents-shipgate.mdc"}
    assert any(signal["kind"] == "protected_surface_touch" for signal in payload["signals"])
    assert _snapshot(workspace) == before


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

    assert payload["preflight_schema_version"] == "0.2"
    assert payload["first_next_action"]["actor"] == "human"
    assert any(signal["kind"] == "least_privilege" for signal in payload["signals"])
    assert _snapshot(workspace) == before


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

    assert payload["capability_lock_schema_version"] == "0.5"
    assert _snapshot(workspace) == before


def test_mcp_handoff_handler_is_read_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "verifier.json",
        {
            "workspace": str(tmp_path),
            "config": "shipgate.yaml",
            "head_status": "succeeded",
            "release_decision": {"decision": "passed", "blockers": [], "review_items": []},
            "decision": "passed",
            "merge_verdict": "mergeable",
            "applicability": "verified",
            "can_merge_without_human": True,
            "agent_controller": {
                "completion_allowed": True,
                "must_stop": False,
                "stop_reason": None,
                "allowed_next_commands": [],
                "forbidden_file_edits": [],
                "forbidden_actions": [],
            },
            "artifacts": {
                "verifier_json": str(output_dir / "verifier.json"),
                "agent_handoff_json": str(output_dir / "agent-handoff.json"),
            },
        },
    )
    _write_json(output_dir / "verify-run.json", {"run_id": "sha256:" + "b" * 64})
    before = _snapshot(tmp_path)

    payload = shipgate_handoff(verifier_path=str(output_dir / "verifier.json"))

    assert payload["schema_version"] == "shipgate.agent_handoff/v2"
    assert payload["gate"]["merge_verdict"] == "mergeable"
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
