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


def test_body_missing_canonical_phrases_scores_incomplete(tmp_path: Path) -> None:
    """Pins round-fourteen finding P2.1: a rule with all canonical globs
    but an empty/wrong body must NOT score as ``rule_active``. The doc
    promises the lint checks 'canonical content'; this enforces it."""
    from harness.adoption.drivers.cursor import CANONICAL_GLOBS_REQUIRED

    globs_yaml = "\n".join(f"  - '{g}'" for g in CANONICAL_GLOBS_REQUIRED)
    text = (
        "---\n"
        "description: ok\n"
        f"globs:\n{globs_yaml}\n"
        "---\n"
        "body only, no Shipgate adoption guidance here\n"
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
    assert "rule_present_but_body_incomplete" in result.summary_text
    assert "verdict: rule_active" not in result.summary_text


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


def test_canonical_globs_match_target_snippet_doc() -> None:
    """Pin the cursor-static lint to the canonical snippet in
    docs/target-repo-agent-snippets.md. If that doc adds globs (e.g.
    new framework hooks), the lint must learn about them at the same
    time — otherwise the static driver would happily score drifted rules
    as 'active'."""
    import re

    from harness.adoption import cli as cli_mod
    from harness.adoption.drivers.cursor import CANONICAL_GLOBS_REQUIRED

    doc = (
        Path(cli_mod._repo_root())
        / "docs"
        / "target-repo-agent-snippets.md"
    ).read_text(encoding="utf-8")
    # Carve out the Cursor frontmatter block.
    match = re.search(
        r"## `\.cursor/rules/agents-shipgate\.mdc`[\s\S]*?```md\s*\n([\s\S]*?)\n```",
        doc,
    )
    assert match, "couldn't locate the canonical Cursor block in target-repo-agent-snippets.md"
    canonical_block = match.group(1)
    fm_match = re.search(r"---\s*\n([\s\S]*?)\n---", canonical_block)
    assert fm_match, "Cursor block has no frontmatter"
    import yaml

    fm = yaml.safe_load(fm_match.group(1))
    doc_globs = set(fm.get("globs") or [])
    lint_globs = set(CANONICAL_GLOBS_REQUIRED)
    missing_in_lint = doc_globs - lint_globs
    assert not missing_in_lint, (
        f"cursor-static lint missing canonical globs: {sorted(missing_in_lint)}"
    )


def test_well_formed_rule_with_matching_glob_scores_active(tmp_path: Path) -> None:
    # Build a frontmatter that declares every canonical glob AND a body
    # that includes every canonical phrase — anything less and the new
    # sync check would (correctly) flag the rule as incomplete.
    from harness.adoption.drivers.cursor import (
        CANONICAL_BODY_PHRASES,
        CANONICAL_GLOBS_REQUIRED,
    )

    globs_yaml = "\n".join(f"  - '{g}'" for g in CANONICAL_GLOBS_REQUIRED)
    canonical_body = " ".join(CANONICAL_BODY_PHRASES)
    text = (
        "---\n"
        "description: ok\n"
        f"globs:\n{globs_yaml}\n"
        "---\n"
        f"{canonical_body}\n"
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
