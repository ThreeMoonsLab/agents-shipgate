"""The optional MCP stdio server — a thin wrapper, never a second engine.

Tool handlers are plain sync functions, testable without the optional
``mcp`` SDK. The dispatch surface mirrors the CLI: ``shipgate_verify`` is
``verify --json`` (compact agent result), ``shipgate_explain`` is
``explain`` / ``explain-finding``, ``shipgate_status`` reads the existing
artifacts. Errors come back as payloads, never exceptions, so agent loops
always get machine-readable output.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from agents_shipgate.cli.mcp_server import TOOLS, dispatch

HAS_MCP_SDK = importlib.util.find_spec("mcp") is not None


def test_tool_catalog_shape() -> None:
    names = [tool["name"] for tool in TOOLS]
    assert names == ["shipgate_verify", "shipgate_explain", "shipgate_status"]
    for tool in TOOLS:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"


def test_dispatch_unknown_tool_returns_payload() -> None:
    payload = dispatch("shipgate_frobnicate", {})
    assert payload["error"] == "unknown_tool"
    assert "shipgate_verify" in payload["known_tools"]


def test_status_without_artifacts(tmp_path: Path) -> None:
    payload = dispatch("shipgate_status", {"workspace": str(tmp_path)})
    assert payload["status"] == "no_verify_run"
    assert "shipgate_verify" in payload["message"]


def test_explain_check_id_returns_metadata() -> None:
    payload = dispatch("shipgate_explain", {"id": "SHIP-POLICY-APPROVAL-MISSING"})
    assert payload["id"] == "SHIP-POLICY-APPROVAL-MISSING"
    assert payload["recommendation"]
    assert payload["rationale"]


def test_explain_unknown_check_id_suggests() -> None:
    payload = dispatch("shipgate_explain", {"id": "SHIP-POLICY-APPROVAL-MISING"})
    assert payload["error"] == "unknown_check_id"
    assert payload["suggestion"] == "SHIP-POLICY-APPROVAL-MISSING"


def test_explain_requires_id() -> None:
    payload = dispatch("shipgate_explain", {})
    assert payload["error"] == "config_error"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _docs_only_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "docs")
    return repo


def test_verify_returns_compact_agent_result(tmp_path: Path) -> None:
    repo = _docs_only_repo(tmp_path)

    payload = dispatch("shipgate_verify", {"workspace": str(repo)})

    assert payload["schema_version"] == "shipgate.agent_result/v1"
    assert payload["decision"] == "allow"
    assert payload["merge_verdict"] == "mergeable"
    # Artifacts landed on disk, so a follow-up status call works.
    status = dispatch("shipgate_status", {"workspace": str(repo)})
    assert status["merge_verdict"] == "mergeable"
    assert status["status"] == "verified"


def test_verify_outside_git_returns_error_payload(tmp_path: Path) -> None:
    payload = dispatch("shipgate_verify", {"workspace": str(tmp_path)})
    assert payload["error"] == "config_error"
    assert "next_action" in payload


def test_dispatch_payloads_are_json_serializable(tmp_path: Path) -> None:
    for name, args in (
        ("shipgate_status", {"workspace": str(tmp_path)}),
        ("shipgate_explain", {"id": "SHIP-POLICY-APPROVAL-MISSING"}),
        ("nope", {}),
    ):
        json.dumps(dispatch(name, args))


@pytest.mark.skipif(not HAS_MCP_SDK, reason="optional [mcp] extra not installed")
def test_server_wiring_constructs() -> None:
    """The lazy SDK imports and decorator wiring inside serve() resolve.

    Constructs the Server and registers handlers exactly as serve() does,
    without entering the stdio loop.
    """
    import mcp.types as types
    from mcp.server import Server

    server = Server("agents-shipgate-test")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=tool["name"],
                description=tool["description"],
                inputSchema=tool["inputSchema"],
            )
            for tool in TOOLS
        ]

    assert server.name == "agents-shipgate-test"
