"""Renderer-level tests for ``--agent-instructions`` content.

Includes the Rule 3 strict-mode safety guard: ``ci_mode: strict`` must only
appear inside the shared CI-pointer paragraph's "promotion is a human
decision" sentence, never in any other rendered content.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    render_agents_md,
    render_claude_md,
    render_cursor_file,
    render_pr_template,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers._shared import (
    CI_POINTER_PARAGRAPH,
)

ALL_RENDERERS = {
    "agents-md": render_agents_md,
    "claude-md": render_claude_md,
    "cursor": render_cursor_file,
    "pr-template": render_pr_template,
}
REPO_ROOT = Path(__file__).resolve().parent.parent


def test_each_renderer_returns_nonempty_string() -> None:
    for name, fn in ALL_RENDERERS.items():
        out = fn()
        assert isinstance(out, str), name
        assert out.strip(), name


def test_cursor_renders_full_mdc_with_frontmatter() -> None:
    out = render_cursor_file()
    assert out.startswith("---\n")
    assert "alwaysApply: false" in out
    assert "globs:" in out
    # Path-based trigger globs. Diff-only Python decorator triggers are
    # intentionally not represented by a broad "**/*.py" Cursor glob.
    for token in (
        "openapi",
        "swagger",
        "mcp",
        "tools",
        "n8n/*.json",
        "workflows/*.json",
        "**/*workflow*.json",
        ".agents-shipgate",
        "prompts/**",
        "policies/**",
        ".github/workflows/agents-shipgate",
    ):
        assert token in out
    assert '"**/*.py"' not in out


def test_committed_cursor_rule_matches_renderer() -> None:
    """The repo-level Cursor rule and the init renderer must not drift."""
    committed = (REPO_ROOT / ".cursor/rules/agents-shipgate.mdc").read_text(
        encoding="utf-8"
    )
    assert committed == render_cursor_file()


def test_target_repo_cursor_snippet_matches_renderer() -> None:
    """The copyable docs snippet must match the generated Cursor file."""
    text = (REPO_ROOT / "docs/target-repo-agent-snippets.md").read_text(
        encoding="utf-8"
    )
    section = text.split("## `.cursor/rules/agents-shipgate.mdc`", 1)[1]
    start = section.index("```md\n") + len("```md\n")
    end = section.index("\n```", start)
    assert section[start:end] + "\n" == render_cursor_file()


def test_pr_template_uses_conditional_wording() -> None:
    out = render_pr_template()
    # Conditional avoids docs-only false positives.
    assert "If this PR changes" in out


def test_agents_md_includes_report_json_contract() -> None:
    out = render_agents_md()
    assert "agents-shipgate-reports/report.json" in out
    assert "release_decision.decision" in out


def test_claude_md_is_self_contained_no_dangling_link() -> None:
    """Generating only --agent-instructions=claude-md must not produce a
    dangling reference to AGENTS.md."""
    out = render_claude_md()
    # Self-contained means it lists its own commands and report.json contract.
    assert "agents-shipgate detect" in out
    assert "release_decision.decision" in out
    # Cross-link to AGENTS.md is intentionally omitted.
    assert "AGENTS.md" not in out


def test_strict_mode_token_only_in_ci_pointer_paragraph() -> None:
    """Rule 3: ``ci_mode: strict`` (or `strict mode`/`strict CI`) must only
    appear inside the shared CI-pointer paragraph and only in the
    "promotion is a human decision" framing."""
    assert "ci_mode: strict" in CI_POINTER_PARAGRAPH
    pattern = re.compile(r"ci_mode:\s*strict|strict\s+mode|strict\s+CI", re.IGNORECASE)
    for name, fn in ALL_RENDERERS.items():
        rendered = fn()
        # Strip the CI_POINTER_PARAGRAPH out and assert no match in remainder.
        without_pointer = rendered.replace(CI_POINTER_PARAGRAPH, "")
        assert not pattern.search(without_pointer), (
            f"{name} mentions strict CI outside the shared pointer paragraph"
        )


def test_advisory_default_appears_in_agent_facing_targets() -> None:
    """The agent-facing targets (AGENTS.md, CLAUDE.md, Cursor rule) should
    communicate advisory-by-default. The PR template intentionally omits the
    CI-pointer paragraph — it's a reviewer checklist, not CI documentation."""
    for name in ("agents-md", "claude-md", "cursor"):
        rendered = ALL_RENDERERS[name]()
        assert "advisory" in rendered.lower(), name
