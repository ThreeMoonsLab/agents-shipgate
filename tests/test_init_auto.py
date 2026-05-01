"""Tests for ``shipgate init`` auto-default behavior + ``--minimal`` snapshot.

The auto-default produces a *valid* shipgate.yaml that scans cleanly
against the real loaders, replacing v0.5's CHANGE_ME-heavy template for
workspaces that already look like agent projects.

``--minimal`` preserves byte-exact compatibility with the v0.5 output.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.discovery import (
    detect_workspace,
    render_auto_manifest,
    render_manifest_template,
)
from agents_shipgate.cli.main import app
from agents_shipgate.config.schema import AgentsShipgateManifest


SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _copy_sample(name: str, dst: Path) -> Path:
    """Copy a sample workspace to ``dst`` minus the curated shipgate.yaml,
    so ``init`` writes a fresh one."""
    src = SAMPLES / name
    shutil.copytree(src, dst)
    target = dst / "shipgate.yaml"
    if target.exists():
        target.unlink()
    reports = dst / "agents-shipgate-reports"
    if reports.exists():
        shutil.rmtree(reports)
    return dst


def _validates(text: str) -> AgentsShipgateManifest:
    """Helper: parse + validate a generated manifest."""
    return AgentsShipgateManifest.model_validate(yaml.safe_load(text))


def test_auto_init_langchain_emits_valid_manifest_with_python_source(tmp_path: Path) -> None:
    workspace = _copy_sample("simple_langchain_agent", tmp_path / "lc")
    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    assert any(
        s.type == "langchain" and s.path == "agent.py"
        for s in manifest.tool_sources
    )


def test_auto_init_anthropic_emits_artifact_block_not_tool_source(tmp_path: Path) -> None:
    """Anthropic is artifact-only: per C3 it lives under ``anthropic:``,
    NOT as a tool_sources entry."""
    workspace = _copy_sample("simple_anthropic_agent", tmp_path / "anth")
    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    # Must NOT have an "anthropic" tool source (no such type).
    assert not any(s.type == "anthropic" for s in manifest.tool_sources)
    assert manifest.anthropic is not None
    assert manifest.anthropic.prompt_files == ["prompts/support_refund.md"]
    assert [t.path for t in manifest.anthropic.tools] == ["tools/anthropic-tools.json"]
    assert [p.path for p in manifest.anthropic.policy_rules] == [
        "policies/anthropic-policy.yaml"
    ]


def test_auto_init_adk_extracts_agent_name_from_literal(tmp_path: Path) -> None:
    """ADK sample defines ``Agent(name="adk_support_agent")``. Auto-init
    must use this for ``agent.name`` (not the dir name, not pyproject)."""
    workspace = _copy_sample("google_adk_agent", tmp_path / "adk")
    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    assert manifest.agent.name == "adk_support_agent"
    assert any(s.type == "google_adk" for s in manifest.tool_sources)


def test_auto_init_openai_api_emits_full_artifact_block(tmp_path: Path) -> None:
    workspace = _copy_sample("simple_openai_api_agent", tmp_path / "openai")
    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    assert manifest.openai_api is not None
    assert manifest.openai_api.prompt_files == ["prompts/support_refund.md"]
    assert [t.path for t in manifest.openai_api.tools] == ["tools/openai-tools.json"]


def test_auto_init_empty_workspace_falls_back_to_change_me_stub(tmp_path: Path) -> None:
    """No detected framework → emit a CHANGE_ME tool_sources entry so the
    schema (which requires ≥ 1 source/config block) still passes."""
    detect = detect_workspace(tmp_path)
    text = render_auto_manifest(tmp_path, detect)
    manifest = _validates(text)
    assert any(s.id == "CHANGE_ME" for s in manifest.tool_sources)


def test_minimal_template_byte_exact_to_legacy_output(tmp_path: Path) -> None:
    """``--minimal`` must reproduce the v0.5 template character-for-character
    so users with snapshot tests against today's `init` output can pin to it."""
    (tmp_path / "api.openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: T\n  version: '1'\npaths: {}\n",
        encoding="utf-8",
    )
    legacy = render_manifest_template(tmp_path.resolve())
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(tmp_path), "--minimal"],
    )
    assert result.exit_code == 0
    # CliRunner trims a trailing newline; legacy has its own trailing
    # newline. Compare with both stripped to avoid runner-specific quirks.
    assert result.output.rstrip("\n") == legacy.rstrip("\n")


def test_init_cli_auto_default_emits_auto_detected_payload(tmp_path: Path) -> None:
    workspace = _copy_sample("simple_langchain_agent", tmp_path / "lc")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["created"] is True
    assert "auto_detected" in payload
    assert payload["auto_detected"]["is_agent_project"] is True
    assert any(
        fw["type"] == "langchain"
        for fw in payload["auto_detected"]["frameworks"]
    )


def test_init_auto_flag_is_accepted_as_no_op(tmp_path: Path) -> None:
    """``--auto`` is a self-documenting alias; auto is the default since v0.6."""
    workspace = _copy_sample("simple_langchain_agent", tmp_path / "lc")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--auto", "--write"],
    )
    assert result.exit_code == 0
    assert (workspace / "shipgate.yaml").exists()
