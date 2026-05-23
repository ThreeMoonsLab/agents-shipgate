"""Renderer-level tests for ``--agent-instructions`` content.

Includes the Rule 3 strict-mode safety guard: ``ci_mode: strict`` must only
appear inside the shared CI-pointer paragraph's "promotion is a human
decision" sentence, never in any other rendered content.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    CLAUDE_CODE_SKILL_PRIOR_RENDER_SHA256,
    CODEX_SKILL_PRIOR_RENDER_SHA256,
    render_agents_md,
    render_claude_code_skill_bundle_text,
    render_claude_code_skill_files,
    render_claude_md,
    render_codex_skill_bundle_text,
    render_codex_skill_files,
    render_cursor_file,
    render_pr_template,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers._shared import (
    CI_POINTER_PARAGRAPH,
)
from harness.adoption import overlay as overlay_mod

ALL_RENDERERS = {
    "agents-md": render_agents_md,
    "codex-skill": render_codex_skill_bundle_text,
    "claude-code-skill": render_claude_code_skill_bundle_text,
    "claude-md": render_claude_md,
    "cursor": render_cursor_file,
    "pr-template": render_pr_template,
}
REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_CLAUDE_CODE_SKILL_RENDER_SHA256 = {
    ".claude/skills/agents-shipgate/SKILL.md": (
        "011c3c3daeb537566651ba230b94bc3128b2c51fc51e3d360e8a74cf11f7a48f"
    ),
    ".claude/skills/agents-shipgate/prompts/add-shipgate-to-repo.md": (
        "1ea69b1d3d418080c76540fff3b20044f70ed6787418eb5e4d3d39e036b34014"
    ),
    ".claude/skills/agents-shipgate/prompts/decide-shipgate-relevance.md": (
        "8fab0595326b127fb1678828fd9b15c63cbe98f0229aad5bb87d47030e4b9ca6"
    ),
    ".claude/skills/agents-shipgate/prompts/explain-finding-to-user.md": (
        "18031ed870b3c937a2996173820639ef441afe0a45e8171f16468826cd389829"
    ),
    ".claude/skills/agents-shipgate/prompts/fix-top-finding.md": (
        "90d36fbe91668fdc64e5e73727ec8285ee62c584d695b866261ef569fea07074"
    ),
    ".claude/skills/agents-shipgate/prompts/recommend-fixes.md": (
        "162aa2fb96066535425d9cf86a247a6782b8ec7cc661a18b42dbedf394779475"
    ),
    ".claude/skills/agents-shipgate/prompts/stabilize-strict-mode.md": (
        "bb97c3fbd3b52d5755f6960878f350d484837849c3e536d99aab3fab3e353405"
    ),
    ".claude/skills/agents-shipgate/prompts/triage-false-positive.md": (
        "8cfbb0d4b6e2c36569d24260384d3a54165f966276112f4b143b4ac234b51ada"
    ),
    ".claude/skills/agents-shipgate/prompts/upgrade-shipgate-version.md": (
        "992122338eba26ae5d8056b9658117d718a6b477b9928c2a438dd449b5effb68"
    ),
    ".claude/skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml": (
        "c3756c86f52cf00a594b3fe38179b66e0f07dc8c52b98b9e76f4a15939901c77"
    ),
}
EXPECTED_CODEX_SKILL_RENDER_SHA256 = {
    ".agents/skills/agents-shipgate/SKILL.md": (
        "59ec0a31f9747acf569f731561236ff4ef6d8734b614edfa04ea6ff10043f21a"
    ),
    ".agents/skills/agents-shipgate/references/recipes.md": (
        "df5110bfa05eeabd9b918d8902b5c054fa547d1155be61ef6e7d7d63378bf210"
    ),
    ".agents/skills/agents-shipgate/references/report-reading.md": (
        "75a655059f3d45db365c744b0ff82d1c9d69c3638acacf640fd667ae87260d05"
    ),
    ".agents/skills/agents-shipgate/assets/advisory-pr-comment.yml": (
        "d4005102df70a627d3883334e827c4bc7527a35a2278573699e18a43afed3bcb"
    ),
    ".agents/skills/agents-shipgate/agents/openai.yaml": (
        "4d94a724336e5d36a2769630495f341007580e4dee306bc42a1aeca1af9e867b"
    ),
}


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


def test_codex_skill_source_matches_renderer() -> None:
    """The checked-in repo-scoped Codex skill and init renderer must not drift."""
    for rel, content in render_codex_skill_files().items():
        assert (REPO_ROOT / rel).read_text(encoding="utf-8") == content


def test_codex_skill_benchmark_variant_uses_renderer(tmp_path: Path) -> None:
    """The Codex adoption-harness overlay must use the same skill files."""
    variant = REPO_ROOT / "benchmark/setup-variants/25-codex-skill"
    overlay_mod.apply_overlay(variant_dir=variant, workspace_root=tmp_path, placeholders={})
    for rel, content in render_codex_skill_files().items():
        assert (tmp_path / rel).read_text(encoding="utf-8") == content


def test_codex_skill_render_hashes_change_intentionally() -> None:
    """Content changes require updating this snapshot.

    After the first shipped Codex skill release, move the old hash for any
    changed file into CODEX_SKILL_PRIOR_RENDER_SHA256 before updating this map.
    """
    actual = {
        rel: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for rel, content in render_codex_skill_files().items()
    }
    assert actual == EXPECTED_CODEX_SKILL_RENDER_SHA256
    assert set(CODEX_SKILL_PRIOR_RENDER_SHA256).issubset(actual)
    for rel, prior_hashes in CODEX_SKILL_PRIOR_RENDER_SHA256.items():
        assert actual[rel] not in prior_hashes


def test_claude_code_skill_source_matches_renderer() -> None:
    """The checked-in repo-scoped Claude Code skill and init renderer must not drift."""
    for rel, content in render_claude_code_skill_files().items():
        source_rel = rel.removeprefix(".claude/")
        source_path = REPO_ROOT / source_rel
        if source_rel.endswith("advisory-pr-comment.yml"):
            continue
        assert source_path.read_text(encoding="utf-8") == content


def test_claude_code_skill_render_hashes_change_intentionally() -> None:
    """Content changes require updating this snapshot.

    After the first shipped Claude Code skill release, move the old hash for
    any changed file into CLAUDE_CODE_SKILL_PRIOR_RENDER_SHA256 before
    updating this map.
    """
    actual = {
        rel: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for rel, content in render_claude_code_skill_files().items()
    }
    assert actual == EXPECTED_CLAUDE_CODE_SKILL_RENDER_SHA256
    assert set(CLAUDE_CODE_SKILL_PRIOR_RENDER_SHA256).issubset(actual)
    for rel, prior_hashes in CLAUDE_CODE_SKILL_PRIOR_RENDER_SHA256.items():
        assert actual[rel] not in prior_hashes


def test_claude_code_skill_has_required_surfaces() -> None:
    files = render_claude_code_skill_files()
    assert ".claude/skills/agents-shipgate/SKILL.md" in files
    for prompt_name in (
        "add-shipgate-to-repo",
        "decide-shipgate-relevance",
        "explain-finding-to-user",
        "fix-top-finding",
        "recommend-fixes",
        "stabilize-strict-mode",
        "triage-false-positive",
        "upgrade-shipgate-version",
    ):
        assert f".claude/skills/agents-shipgate/prompts/{prompt_name}.md" in files
    assert ".claude/skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml" in files
    skill = files[".claude/skills/agents-shipgate/SKILL.md"]
    assert "release_decision.decision" in skill
    assert "AGENTS_SHIPGATE_AGENT_MODE=1" in skill
    assert "Do not claim a finding is fixed" in skill


def test_codex_skill_has_required_surfaces() -> None:
    files = render_codex_skill_files()
    assert ".agents/skills/agents-shipgate/SKILL.md" in files
    assert ".agents/skills/agents-shipgate/references/recipes.md" in files
    assert ".agents/skills/agents-shipgate/references/report-reading.md" in files
    assert ".agents/skills/agents-shipgate/assets/advisory-pr-comment.yml" in files
    assert ".agents/skills/agents-shipgate/agents/openai.yaml" in files
    skill = files[".agents/skills/agents-shipgate/SKILL.md"]
    assert "release_decision.decision" in skill
    assert "AGENTS_SHIPGATE_AGENT_MODE=1" in skill
    assert "Do not auto-assert approval" in skill


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
    "promotion is a human decision" framing.

    File-tree skill bundles (codex-skill, claude-code-skill) are excluded:
    they contain task-specific recipe prompts (e.g. stabilize-strict-mode.md)
    whose purpose is to describe the strict-mode workflow."""
    assert "ci_mode: strict" in CI_POINTER_PARAGRAPH
    pattern = re.compile(r"ci_mode:\s*strict|strict\s+mode|strict\s+CI", re.IGNORECASE)
    excluded = {"codex-skill", "claude-code-skill"}
    for name, fn in ALL_RENDERERS.items():
        if name in excluded:
            continue
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
