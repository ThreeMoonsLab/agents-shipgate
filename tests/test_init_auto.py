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


def test_artifact_only_openai_workspace_emits_openai_api_block(tmp_path: Path) -> None:
    """A workspace with prompts/ and tools/openai-tools.json — but NO
    Python framework imports — must still get an ``openai_api:`` block
    rather than a CHANGE_ME stub.

    Regression for v0.6 reviewer feedback: the openai_api block was
    gated on framework detection, which only fires for openai-config.json
    or `from agents import` Python source.
    """
    workspace = tmp_path / "openai_artifact_only"
    workspace.mkdir()
    (workspace / "prompts").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "prompts" / "support.md").write_text("You are helpful.", encoding="utf-8")
    (workspace / "tools" / "openai-tools.json").write_text("[]", encoding="utf-8")

    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    manifest = _validates(text)
    assert manifest.openai_api is not None
    assert manifest.openai_api.prompt_files == ["prompts/support.md"]
    assert [t.path for t in manifest.openai_api.tools] == ["tools/openai-tools.json"]


def test_artifact_only_openai_workspace_does_not_emit_anthropic_block(tmp_path: Path) -> None:
    """The OpenAI artifact-only workspace must NOT also emit an
    ``anthropic:`` block (prompts/ overlaps both adapters by glob)."""
    workspace = tmp_path / "openai_artifact_only2"
    workspace.mkdir()
    (workspace / "prompts").mkdir()
    (workspace / "tools").mkdir()
    (workspace / "prompts" / "support.md").write_text("hi", encoding="utf-8")
    (workspace / "tools" / "openai-tools.json").write_text("[]", encoding="utf-8")

    detect = detect_workspace(workspace)
    text = render_auto_manifest(workspace, detect)
    assert "openai_api:" in text
    assert "anthropic:" not in text


def test_init_json_agent_name_matches_yaml_when_no_literal(tmp_path: Path) -> None:
    """JSON ``auto_detected.agent_name`` must reflect the value the
    manifest actually carries, not the first candidate. Regression for
    v0.6 reviewer feedback: the JSON used to claim a workspace-dir name
    while the YAML still had CHANGE_ME.
    """
    workspace = _copy_sample("simple_langchain_agent", tmp_path / "lc")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    yaml_text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    if "name: CHANGE_ME" in yaml_text:
        # When the template emits CHANGE_ME, JSON must NOT report a value.
        assert payload["auto_detected"]["agent_name"] is None
    else:
        # When a strong literal IS used, JSON value must match YAML.
        assert payload["auto_detected"]["agent_name"] is not None
        assert (
            f"name: {payload['auto_detected']['agent_name']}" in yaml_text
            or f'name: "{payload["auto_detected"]["agent_name"]}"' in yaml_text
        )
    # All candidates surfaced separately so agents can override.
    assert "agent_name_candidates" in payload["auto_detected"]
    assert all(
        "source" in c for c in payload["auto_detected"]["agent_name_candidates"]
    )


def test_init_json_agent_name_matches_yaml_when_literal_present(tmp_path: Path) -> None:
    """ADK fixture has ``Agent(name="adk_support_agent")`` — JSON must
    report it and the YAML must use it (no CHANGE_ME)."""
    workspace = _copy_sample("google_adk_agent", tmp_path / "adk")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output)
    assert payload["auto_detected"]["agent_name"] == "adk_support_agent"
    yaml_text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    assert "name: adk_support_agent" in yaml_text
    assert "name: CHANGE_ME" not in yaml_text


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
