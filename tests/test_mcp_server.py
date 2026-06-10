"""Tests for the optional read-only MCP ``shipgate.check`` adapter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.mcp_server import build_server, shipgate_check

_HAS_MCP_SDK = importlib.util.find_spec("mcp") is not None


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
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    payload = shipgate_check(
        agent="cursor",
        workspace=str(tmp_path),
        diff_text=diff,
    )

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before
    assert payload["schema_version"] == "agent_result_v1"
    assert payload["agent"] == "cursor"
    assert payload["decision"] == "block"
    assert payload["first_next_action"]["kind"] in {"repair", "stop"}
    json.dumps(payload)


@pytest.mark.skipif(_HAS_MCP_SDK, reason="mcp extra installed; error path n/a")
def test_build_server_without_sdk_raises_config_error() -> None:
    with pytest.raises(ConfigError, match=r"agents-shipgate\[mcp\]"):
        build_server()


@pytest.mark.skipif(not _HAS_MCP_SDK, reason="requires the optional mcp extra")
def test_build_server_registers_only_shipgate_check() -> None:
    server = build_server()

    import asyncio

    listed = asyncio.run(server.list_tools())
    assert {tool.name for tool in listed} == {"shipgate.check"}
