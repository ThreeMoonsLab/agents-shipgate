"""Tests for ``shipgate detect`` and ``signals.detect_workspace``."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from agents_shipgate.cli.discovery.signals import (
    DetectResult,
    detect_workspace,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def test_detects_langchain_sample() -> None:
    result = detect_workspace(SAMPLES / "simple_langchain_agent")
    assert result.is_agent_project is True
    assert {fw.type for fw in result.frameworks} == {"langchain"}
    langchain = next(fw for fw in result.frameworks if fw.type == "langchain")
    assert langchain.confidence == "high"
    assert any("langchain import" in ev for ev in langchain.evidence)


def test_detects_crewai_sample() -> None:
    result = detect_workspace(SAMPLES / "simple_crewai_agent")
    assert result.is_agent_project is True
    assert {fw.type for fw in result.frameworks} == {"crewai"}


def test_detects_google_adk_sample_and_extracts_agent_name_literal() -> None:
    result = detect_workspace(SAMPLES / "google_adk_agent")
    assert result.is_agent_project is True
    assert any(fw.type == "google_adk" for fw in result.frameworks)
    # ADK sample defines `Agent(name="adk_support_agent", ...)` — must beat
    # the workspace dir name in the ranking.
    assert result.agent_name_candidates[0].source == "Agent_name_literal"
    assert result.agent_name_candidates[0].value == "adk_support_agent"


def test_detects_artifact_only_anthropic_sample() -> None:
    """Anthropic projects ship only artifacts (tools/anthropic-tools.json,
    policies/anthropic-policy.yaml). They have no .py imports and would be
    missed without the strong artifact-anchor rule (per C12)."""
    result = detect_workspace(SAMPLES / "simple_anthropic_agent")
    assert result.is_agent_project is True
    assert {fw.type for fw in result.frameworks} == {"anthropic"}
    anthropic = next(fw for fw in result.frameworks if fw.type == "anthropic")
    assert any("anthropic-tools.json" in ev for ev in anthropic.evidence)
    assert any("anthropic-policy.yaml" in ev for ev in anthropic.evidence)


def test_detects_openai_api_sample_as_openai_api_not_sdk() -> None:
    """OpenAI API artifact projects (openai-config.json + tools/policies/...)
    must classify as ``openai_api`` (artifact-based Messages API surface),
    NOT ``openai_agents_sdk`` (the Python @function_tool surface).

    Per v0.6 reviewer feedback: openai_api and openai_agents_sdk are
    distinct things in the manifest schema (manifest.openai_api block vs
    tool_sources[*].type == 'openai_agents_sdk') and detection must
    reflect that.
    """
    result = detect_workspace(SAMPLES / "simple_openai_api_agent")
    assert result.is_agent_project is True
    assert any(fw.type == "openai_api" for fw in result.frameworks)
    # Must NOT have been mislabeled as the SDK adapter.
    assert not any(fw.type == "openai_agents_sdk" for fw in result.frameworks)


def test_detects_artifact_only_openai_api_workspace(tmp_path: Path) -> None:
    """A workspace with only prompts/ and tools/openai-tools.json must
    register as an agent project so the canonical agent flow doesn't
    skip a repo that init can onboard. Regression for v0.6 reviewer
    feedback."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "prompts" / "support.md").write_text("you are helpful", encoding="utf-8")
    (tmp_path / "tools" / "openai-tools.json").write_text("[]", encoding="utf-8")
    result = detect_workspace(tmp_path)
    assert result.is_agent_project is True
    assert any(fw.type == "openai_api" for fw in result.frameworks)
    assert result.next_action.startswith("agents-shipgate init")


def test_clean_read_only_workspace_is_not_agent_project() -> None:
    """clean_read_only_agent has only a manifest + a tools.json file; that
    is a tool surface, not enough to say the *project* is an agent project."""
    result = detect_workspace(SAMPLES / "clean_read_only_agent")
    assert result.is_agent_project is False


def test_negative_workspace_detects_nothing(tmp_path: Path) -> None:
    """A repo with random Python that imports nothing framework-specific must
    not register as an agent project."""
    (tmp_path / "main.py").write_text(
        textwrap.dedent(
            """
            import json

            def main() -> None:
                print(json.dumps({"hi": "there"}))
            """
        ).strip(),
        encoding="utf-8",
    )
    result = detect_workspace(tmp_path)
    assert result.is_agent_project is False
    assert result.frameworks == []
    assert any(c.value == tmp_path.name for c in result.agent_name_candidates)


def test_detect_ignores_local_private_and_virtualenv_fixtures(tmp_path: Path) -> None:
    """Local agent state and package fixture installs must not pollute detect."""
    claude_agent = tmp_path / ".claude" / "worktrees" / "fixture" / "agent.py"
    claude_agent.parent.mkdir(parents=True)
    claude_agent.write_text(
        "from langchain.tools import tool\n\n@tool\ndef lookup():\n    return 'x'\n",
        encoding="utf-8",
    )

    private_agent = tmp_path / ".agents-private" / "copy" / "crew.py"
    private_agent.parent.mkdir(parents=True)
    private_agent.write_text(
        "from crewai import Agent\n\nAgent(role='support', goal='help')\n",
        encoding="utf-8",
    )

    venv_tools = (
        tmp_path
        / ".venv-py312"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "agents_shipgate"
        / "_fixtures"
        / "simple_openai_api_agent"
        / "tools"
        / "openai-tools.json"
    )
    venv_tools.parent.mkdir(parents=True)
    venv_tools.write_text("[]", encoding="utf-8")

    generated_report = tmp_path / "agents-shipgate-reports" / "report.json"
    generated_report.parent.mkdir()
    generated_report.write_text('{"report_schema_version": "0.8"}', encoding="utf-8")

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.frameworks == []
    assert result.suggested_sources == []


def test_detect_does_not_skip_workspace_because_parent_is_skipped_name(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / ".claude" / "worktrees" / "agent-review"
    workspace.mkdir(parents=True)
    (workspace / "agent.py").write_text(
        "from langchain.tools import tool\n\n@tool\ndef lookup():\n    return 'x'\n",
        encoding="utf-8",
    )

    result = detect_workspace(workspace)

    assert result.is_agent_project is True
    langchain = next(fw for fw in result.frameworks if fw.type == "langchain")
    assert langchain.candidate_files == ["agent.py"]


def test_detect_respects_gitignored_nested_agent_artifacts(tmp_path: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("git is required for git-aware discovery regression coverage")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored-agent/\n", encoding="utf-8")

    ignored_agent = tmp_path / "ignored-agent" / "agent.py"
    ignored_agent.parent.mkdir()
    ignored_agent.write_text(
        "from agents import Agent, function_tool\n\n"
        "@function_tool\n"
        "def refund_user():\n"
        "    return None\n\n"
        "Agent(name='ignored')\n",
        encoding="utf-8",
    )
    (tmp_path / "ignored-agent" / "openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo:\n  title: ignored\n  version: '1.0'\npaths: {}\n",
        encoding="utf-8",
    )

    result = detect_workspace(tmp_path)

    assert result.is_agent_project is False
    assert result.frameworks == []
    assert result.suggested_sources == []


def test_pyproject_seeds_project_name_not_agent_name(tmp_path: Path) -> None:
    """pyproject [project].name → project_name_candidates, NOT
    agent_name_candidates (post-review correction)."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "shipgate-demo"
            version = "0.1.0"
            """
        ).strip(),
        encoding="utf-8",
    )
    result = detect_workspace(tmp_path)
    project_sources = {c.source for c in result.project_name_candidates}
    agent_sources = {c.source for c in result.agent_name_candidates}
    assert "pyproject" in project_sources
    assert "pyproject" not in agent_sources


def test_emits_next_action_for_detected_project() -> None:
    result = detect_workspace(SAMPLES / "simple_langchain_agent")
    assert result.next_action.startswith("agents-shipgate init")


def test_max_python_files_caps_walk(tmp_path: Path) -> None:
    """Cap defends large monorepos from unbounded AST parses."""
    for i in range(50):
        (tmp_path / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
    # cap below the file count: must not raise
    result = detect_workspace(tmp_path, max_python_files=5)
    assert isinstance(result, DetectResult)


def test_detect_result_serializes_cleanly() -> None:
    result = detect_workspace(SAMPLES / "simple_langchain_agent")
    payload = result.model_dump(mode="json")
    assert payload["is_agent_project"] is True
    assert isinstance(payload["frameworks"], list)
    assert isinstance(payload["agent_name_candidates"], list)
