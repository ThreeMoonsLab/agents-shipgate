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
    shipgate_preflight,
)

_HAS_MCP_SDK = importlib.util.find_spec("mcp") is not None


def _snapshot(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )


def test_shipgate_check_returns_agent_result_without_writes(tmp_path: Path) -> None:
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
    assert payload["schema_version"] == "agent_result_v1"
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

    assert payload["preflight_schema_version"] == "0.1"
    assert payload["requires_human_review"] is True
    assert {
        touch["path"] for touch in payload["protected_surface_touches"]
    } >= {"shipgate.yaml", ".cursor/rules/agents-shipgate.mdc"}
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

    assert payload["capability_lock_schema_version"] == "0.2"
    assert _snapshot(workspace) == before


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
        "shipgate.preflight",
    }
