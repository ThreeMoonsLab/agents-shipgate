"""Tests for ``shipgate detect`` and ``signals.detect_workspace``."""

from __future__ import annotations

import textwrap
from pathlib import Path

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


def test_detects_openai_api_sample_via_openai_config() -> None:
    result = detect_workspace(SAMPLES / "simple_openai_api_agent")
    assert result.is_agent_project is True
    assert any(fw.type == "openai_agents_sdk" for fw in result.frameworks)


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
