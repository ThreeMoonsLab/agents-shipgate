"""Tests for the optional MCP server projection layer.

The tool functions are pure wrappers over run_preview / run_verify /
explain_finding_payload, so they are tested without the MCP SDK. The SDK
wiring (build_server) is exercised only when the optional ``mcp`` extra
is installed; otherwise it must fail with a clear ConfigError.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from agents_shipgate.cli.fixture import materialize_git_pr_fixture
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.fixtures import fixture_path
from agents_shipgate.mcp_server import (
    build_server,
    explain_finding_tool,
    preview_tool,
    verify_tool,
)

_HAS_MCP_SDK = importlib.util.find_spec("mcp") is not None


@pytest.fixture()
def refund_pr_workspace(tmp_path: Path) -> Path:
    """The verify-native blocked refund PR as a real git base/head repo."""
    src = fixture_path("ai_generated_refund_pr")
    target = tmp_path / "repo"
    shutil.copytree(src, target)
    head_payload = (target / "_head" / "tools.json").read_text(encoding="utf-8")
    shutil.rmtree(target / "_head")
    materialize_git_pr_fixture(
        target,
        head_files={"tools.json": head_payload},
        user_email="fixture@example.com",
        user_name="Agents Shipgate Fixture",
        base_commit_message="base support agent",
        head_commit_message="codex adds refund tool",
    )
    return target


def test_verify_tool_returns_blocked_projection(refund_pr_workspace: Path) -> None:
    result = verify_tool(
        workspace=str(refund_pr_workspace),
        base="origin/main",
        head="HEAD",
    )
    assert result["merge_verdict"] == "blocked"
    assert result["can_merge_without_human"] is False
    assert result["release_decision"]["decision"] == "blocked"
    # The projection leads with the agent read order.
    keys = list(result)
    assert keys.index("merge_verdict") == 0
    # Artifacts land exactly where the CLI writes them.
    assert (refund_pr_workspace / "agents-shipgate-reports" / "verifier.json").is_file()


def test_verify_tool_is_json_serializable(refund_pr_workspace: Path) -> None:
    result = verify_tool(
        workspace=str(refund_pr_workspace), base="origin/main", head="HEAD"
    )
    json.dumps(result)


def test_preview_tool_reports_relevance(refund_pr_workspace: Path) -> None:
    result = preview_tool(
        workspace=str(refund_pr_workspace),
        base="origin/main",
        head="HEAD",
    )
    assert "merge_verdict" in result
    json.dumps(result)


def test_verify_tool_error_path_returns_structured_payload(tmp_path: Path) -> None:
    # Not a git repo / missing manifest must not raise — agents need a
    # structured error with a next action, mirroring agent-mode errors.
    result = verify_tool(workspace=str(tmp_path))
    assert result["merge_verdict"] == "unknown"
    assert result["error"]
    assert "next_action" in result


def test_explain_finding_tool_explains_blocker(refund_pr_workspace: Path) -> None:
    verify_tool(
        workspace=str(refund_pr_workspace), base="origin/main", head="HEAD"
    )
    report_path = refund_pr_workspace / "agents-shipgate-reports" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    blockers = report["release_decision"]["blockers"]
    assert blockers
    fingerprint = blockers[0]["fingerprint"]

    explained = explain_finding_tool(fingerprint, str(report_path))
    assert "error" not in explained
    payload_text = json.dumps(explained)
    assert blockers[0]["check_id"] in payload_text


def test_explain_finding_tool_unknown_fingerprint_is_structured(
    refund_pr_workspace: Path,
) -> None:
    verify_tool(
        workspace=str(refund_pr_workspace), base="origin/main", head="HEAD"
    )
    report_path = refund_pr_workspace / "agents-shipgate-reports" / "report.json"
    result = explain_finding_tool("not-a-real-fingerprint", str(report_path))
    assert result.get("error")


@pytest.mark.skipif(_HAS_MCP_SDK, reason="mcp extra installed; error path n/a")
def test_build_server_without_sdk_raises_config_error() -> None:
    with pytest.raises(ConfigError, match=r"agents-shipgate\[mcp\]"):
        build_server()


@pytest.mark.skipif(not _HAS_MCP_SDK, reason="requires the optional mcp extra")
def test_build_server_registers_three_tools() -> None:
    server = build_server()
    # FastMCP exposes list_tools as an async API; run it synchronously.
    import asyncio

    listed = asyncio.run(server.list_tools())
    names = {tool.name for tool in listed}
    assert {
        "shipgate_preview",
        "shipgate_verify",
        "shipgate_explain_finding",
    } <= names


def test_verify_tool_defaults_to_working_tree_without_base_or_head(
    refund_pr_workspace: Path,
) -> None:
    """Omitting base/head must verify local work like the CLI does
    (head defaults to HEAD, no archive) — not fail on an empty head ref.
    Regression for the `head or ""` bug caught in PR #192 review."""
    result = verify_tool(workspace=str(refund_pr_workspace))
    assert "error" not in result, result
    assert result["merge_verdict"] in {
        "mergeable",
        "human_review_required",
        "insufficient_evidence",
        "blocked",
    }
