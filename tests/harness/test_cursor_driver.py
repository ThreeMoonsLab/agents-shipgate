"""Cursor static-lint driver tests.

Pins finding P2 from the third review: the driver must parse YAML
frontmatter, not substring-match the file body. A glob mentioned only in
prose or a comment must not count toward coverage.
"""
from __future__ import annotations

from pathlib import Path

from harness.adoption.drivers.base import DriverInputs
from harness.adoption.drivers.cursor import CursorStaticDriver, _parse_declared_globs
from harness.adoption.observer.transcript import TranscriptWriter


def _write_cursor_rule(workspace: Path, content: str) -> Path:
    rule = workspace / ".cursor" / "rules" / "agents-shipgate.mdc"
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text(content, encoding="utf-8")
    return rule


# -- _parse_declared_globs --------------------------------------------------


def test_parse_declared_globs_returns_frontmatter_list() -> None:
    text = "---\ndescription: x\nglobs:\n  - 'a/*.py'\n  - 'b/*.json'\n---\nbody\n"
    declared, ok, err = _parse_declared_globs(text)
    assert ok is True
    assert err is None
    assert declared == ["a/*.py", "b/*.json"]


def test_parse_declared_globs_rejects_missing_frontmatter() -> None:
    text = "no frontmatter here\nglobs:\n  - 'foo'\n"
    declared, ok, err = _parse_declared_globs(text)
    assert ok is False
    assert declared == []
    assert err and "frontmatter" in err.lower()


def test_parse_declared_globs_rejects_malformed_yaml() -> None:
    text = "---\nglobs:\n  - 'unclosed\n---\nbody\n"
    declared, ok, err = _parse_declared_globs(text)
    assert ok is False
    assert declared == []


def test_parse_declared_globs_ignores_body_globs() -> None:
    """Globs mentioned only in the body of the rule do NOT count."""
    text = (
        "---\n"
        "description: x\n"
        "globs:\n"
        "  - 'declared/*.py'\n"
        "---\n"
        "When a body mentions '**/*openapi*.yaml' that should not count.\n"
    )
    declared, ok, err = _parse_declared_globs(text)
    assert ok is True
    assert "**/*openapi*.yaml" not in declared
    assert declared == ["declared/*.py"]


# -- driver-level lint ------------------------------------------------------


def test_body_only_globs_score_as_globs_incomplete(tmp_path: Path) -> None:
    """A rule whose frontmatter declares NO globs but mentions them all in
    prose must NOT score as ``rule_active``."""
    text = (
        "---\n"
        "description: prose-only globs\n"
        "---\n"
        "When a change affects shipgate.yaml or **/*openapi*.yaml or "
        "n8n/*.json, run agents-shipgate.\n"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_cursor_rule(workspace, text)
    raw = tmp_path / "raw"
    raw.mkdir()
    with TranscriptWriter(raw) as writer:
        result = CursorStaticDriver().run(
            DriverInputs(
                workspace=workspace,
                prompt_text="(static)",
                artifacts_dir=tmp_path,
                cell_id="openai-agents-sdk__30-cursor-rule__01-prepare-for-release__cursor-static",
                agent_name="cursor-static",
                model=None,
            ),
            writer,
        )
    assert "rule_present_but_globs_incomplete" in result.summary_text
    assert "verdict: rule_active" not in result.summary_text


def test_well_formed_rule_with_matching_glob_scores_active(tmp_path: Path) -> None:
    text = (
        "---\n"
        "description: ok\n"
        "globs:\n"
        "  - 'shipgate.yaml'\n"
        "  - '**/*openapi*.yaml'\n"
        "  - '**/*mcp*.json'\n"
        "  - '**/*tools*.json'\n"
        "  - 'n8n/*.json'\n"
        "  - 'workflows/*.json'\n"
        "  - '.github/workflows/agents-shipgate.yml'\n"
        "---\n"
        "body\n"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_cursor_rule(workspace, text)
    # Create a trigger file the openai-agents-sdk archetype expects:
    (workspace / "specs").mkdir()
    (workspace / "specs" / "support-tools.openapi.yaml").write_text(
        "openapi: '3.0.3'\ninfo: {title: x, version: '1'}\npaths: {}\n",
        encoding="utf-8",
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    with TranscriptWriter(raw) as writer:
        result = CursorStaticDriver().run(
            DriverInputs(
                workspace=workspace,
                prompt_text="(static)",
                artifacts_dir=tmp_path,
                cell_id="openai-agents-sdk__30-cursor-rule__01-prepare-for-release__cursor-static",
                agent_name="cursor-static",
                model=None,
            ),
            writer,
        )
    assert "verdict: rule_active" in result.summary_text
