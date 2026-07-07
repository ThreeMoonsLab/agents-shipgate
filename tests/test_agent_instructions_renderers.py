"""Renderer-level tests for ``--agent-instructions`` content.

Includes the Rule 3 strict-mode safety guard: ``ci_mode: strict`` must only
appear inside the shared CI-pointer paragraph's "promotion is a human
decision" sentence, never in any other rendered content.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    CLAUDE_CODE_SKILL_PRIOR_RENDER_SHA256,
    CODEX_SKILL_PRIOR_RENDER_SHA256,
    render_agents_md,
    render_claude_code_skill_bundle_text,
    render_claude_code_skill_files,
    render_claude_command_file,
    render_claude_md,
    render_codex_skill_bundle_text,
    render_codex_skill_files,
    render_cursor_file,
    render_local_contract_file,
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
    "claude-command": render_claude_command_file,
    "claude-md": render_claude_md,
    "cursor": render_cursor_file,
    "local-contract": render_local_contract_file,
    "pr-template": render_pr_template,
}
REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_CLAUDE_CODE_SKILL_RENDER_SHA256 = {
    ".claude/skills/agents-shipgate/SKILL.md": (
        "52607a7b0ed5b83f4512618bbb9f277b77649943e66aa120dcf38aca7bfe1e16"
    ),
    ".claude/skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml": (
        "b6f87f58f70b5920442f342b5118419ef685ad9f4ff8b0ff87c2729a92929786"
    ),
    ".claude/skills/agents-shipgate/prompts/add-shipgate-to-repo.md": (
        "b3a3273bf68c3f49abd32585f8ce6e9f562c49be520f8d3f381e39afa4712280"
    ),
    ".claude/skills/agents-shipgate/prompts/decide-shipgate-relevance.md": (
        "8f408aed05cb85e06c9f8bb13ee189131eeccfa66fa2c1119e802c43ae97f19c"
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
        "00da293e63792ccaf980f82d525ac12073807f41fd2d78c5a95498054053e364"
    ),
    ".claude/skills/agents-shipgate/prompts/triage-false-positive.md": (
        "8cfbb0d4b6e2c36569d24260384d3a54165f966276112f4b143b4ac234b51ada"
    ),
    ".claude/skills/agents-shipgate/prompts/upgrade-shipgate-version.md": (
        "992122338eba26ae5d8056b9658117d718a6b477b9928c2a438dd449b5effb68"
    ),
    ".claude/skills/agents-shipgate/prompts/verify-agent-diff.md": (
        "e72ab77443032dbbef9a7236ad0d859a291cb764e30c9eb37f8f1b037c0751b4"
    ),
}
EXPECTED_CODEX_SKILL_RENDER_SHA256 = {
    ".agents/skills/agents-shipgate/SKILL.md": (
        "8d5080cf5c4c429a6920b466fe5c3415d89fec3434760b549883ee9a458a48ac"
    ),
    ".agents/skills/agents-shipgate/agents/openai.yaml": (
        "aa511e933ff663dcd1e0d2af3da2a7101206ce2bb1bb98c4dae801bb3f4e42ef"
    ),
    ".agents/skills/agents-shipgate/assets/advisory-pr-comment.yml": (
        "89580914407edd5516db10c8d7725f22c1a919e827e9b820115007a7a6caab31"
    ),
    ".agents/skills/agents-shipgate/references/recipes.md": (
        "f32d0046473377705e0ba487e19cfcf918edfe33a96a0666147cbbd1ea3f0de7"
    ),
    ".agents/skills/agents-shipgate/references/report-reading.md": (
        "6d2848f3436f6e246bf553e6cf061c990888d6ff39eb82fec9a41f291b2e94fe"
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


def test_agent_instruction_surfaces_name_phase1_control_fields() -> None:
    for name, text in {
        "agents-md": render_agents_md(),
        "claude-md": render_claude_md(),
        "cursor": render_cursor_file(),
    }.items():
        for token in (
            "shipgate check",
            "shipgate.codex_boundary_result/v1",
            "decision",
            "completion_allowed",
            "must_stop",
            "first_next_action",
            "human_review",
            "repair",
            "policy",
            "verify_required",
        ):
            assert token in text, f"{name} missing {token!r}"


def test_committed_cursor_rule_matches_renderer() -> None:
    """The repo-level Cursor rule and the init renderer must not drift."""
    committed = (REPO_ROOT / ".cursor/rules/agents-shipgate.mdc").read_text(encoding="utf-8")
    assert committed == render_cursor_file()


def test_committed_claude_command_matches_renderer() -> None:
    """The repo-level Claude slash command and init renderer must not drift."""
    committed = (REPO_ROOT / ".claude/commands/shipgate.md").read_text(encoding="utf-8")
    assert committed == render_claude_command_file()


def test_local_contract_renderer_exposes_agent_operational_fields() -> None:
    payload = json.loads(render_local_contract_file())
    assert payload["schema_version"] == "2"
    assert payload["agents_shipgate_version"]
    assert payload["contract_version"] == "10"
    assert payload["primary_commands"]["verify_pr"].startswith("agents-shipgate verify")
    assert payload["primary_commands"]["host_audit"].startswith("shipgate audit --host")
    assert "verify_local" not in payload["primary_commands"]
    assert payload["commands"]["verify_local"].startswith("agents-shipgate verify")
    assert payload["verifier_schema_version"] == "0.1"
    assert payload["verify_run_schema_version"] == "shipgate.verify_run/v1"
    assert payload["agent_handoff_schema_version"] == "shipgate.agent_handoff/v1"
    assert payload["agent_handoff_schema_path"] == "docs/agent-handoff-schema.v1.json"
    assert payload["agent_handoff_artifact"] == "agents-shipgate-reports/agent-handoff.json"
    assert (
        payload["codex_boundary_result_schema_version"]
        == "shipgate.codex_boundary_result/v1"
    )
    assert payload["agent_result_schema_version"] == "agent_result_v1"
    assert payload["agent_result_schema_path"] == "docs/agent-result-schema.v1.json"
    assert payload["attestation_schema_version"] == "0.4"
    assert payload["registry_schema_version"] == "0.3"
    assert payload["org_evidence_bundle_schema_version"] == (
        "shipgate.org_evidence_bundle/v1"
    )
    assert payload["host_grants_inventory_schema_version"] == "0.1"
    assert payload["agent_result_control_fields"] == [
        "decision",
        "completion_allowed",
        "must_stop",
        "first_next_action",
        "human_review",
        "repair",
        "policy",
        "verify_required",
    ]
    assert payload["commands"]["agent_check_codex"].startswith("shipgate check")
    assert payload["commands"]["agent_check_claude_code"].startswith("shipgate check")
    assert payload["commands"]["agent_check_cursor"].startswith("shipgate check")
    assert payload["commands"]["agent_handoff"].startswith("agents-shipgate agent handoff")
    assert payload["commands"]["install_agent_workflow"].endswith(
        "--ci --agent-instructions=default --json"
    )
    assert payload["agent_interface_operations"] == [
        "verify_pr",
        "verify_local",
        "verify_preview",
    ]
    assert payload["exit_code_policy"]["3"] == "input parse or missing artifact error"
    assert "shipgate.handoff" in payload["mcp_tools"]
    assert payload["default_paths"]["local_contract"] == ".shipgate/agent-contract.json"
    assert payload["artifacts"]["verifier"] == "agents-shipgate-reports/verifier.json"
    assert payload["artifacts"]["verify_run"] == "agents-shipgate-reports/verify-run.json"
    assert payload["agent_read_order"] == [
        "agent-handoff.json",
        "verifier.json.merge_verdict",
        "verifier.json.agent_controller",
        "verify-run.json",
        "report.json.release_decision.decision",
    ]
    assert payload["gating_signal"] == "release_decision.decision"


def test_target_repo_cursor_snippet_matches_renderer() -> None:
    """The copyable docs snippet must match the generated Cursor file."""
    text = (REPO_ROOT / "docs/target-repo-agent-snippets.md").read_text(encoding="utf-8")
    section = text.split("## `.cursor/rules/agents-shipgate.mdc`", 1)[1]
    start = section.index("```md\n") + len("```md\n")
    end = section.index("\n```", start)
    assert section[start:end] + "\n" == render_cursor_file()


def test_codex_skill_source_matches_renderer() -> None:
    """The checked-in repo-scoped Codex skill and init renderer must not drift."""
    for rel, content in render_codex_skill_files().items():
        assert (REPO_ROOT / rel).read_text(encoding="utf-8") == content


def test_skill_renderers_do_not_embed_long_content_constants() -> None:
    """Skill bundle prose lives in adoption-kit files, not Python constants."""

    renderer_paths = (
        REPO_ROOT / "src/agents_shipgate/cli/discovery/agent_instructions/renderers/codex_skill.py",
        REPO_ROOT
        / "src/agents_shipgate/cli/discovery/agent_instructions/renderers/claude_code_skill.py",
    )
    for path in renderer_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        long_strings = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and len(node.value) > 500
        ]
        assert not long_strings, f"{path} embeds generated content in Python"


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
        "verify-agent-diff",
    ):
        assert f".claude/skills/agents-shipgate/prompts/{prompt_name}.md" in files
    assert ".claude/skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml" in files
    skill = files[".claude/skills/agents-shipgate/SKILL.md"]
    assert "release_decision.decision" in skill
    assert "AGENTS_SHIPGATE_AGENT_MODE=1" in skill
    assert "Do not claim a finding is fixed" in skill
    assert "agents-shipgate verify" in skill


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
    assert "agents-shipgate verify" in skill
    assert "agents-shipgate --version" in skill
    assert "agents-shipgate contract --json" in skill
    assert "install or upgrade `agents-shipgate`" in skill
    recipes = files[".agents/skills/agents-shipgate/references/recipes.md"]
    assert 'contract_version: "10"' in recipes
    assert "shipgate.codex_boundary_result/v1" in recipes


def test_pr_template_uses_conditional_wording() -> None:
    out = render_pr_template()
    # Conditional avoids docs-only false positives.
    assert "If this PR changes" in out


def test_agents_md_includes_report_json_contract() -> None:
    out = render_agents_md()
    assert "agents-shipgate-reports/verifier.json" in out
    assert "merge_verdict" in out
    assert "agents-shipgate-reports/report.json" in out
    assert "release_decision.decision" in out


def test_claude_md_is_self_contained_no_dangling_link() -> None:
    """Generating only --agent-instructions=claude-md must not produce a
    dangling reference to AGENTS.md."""
    out = render_claude_md()
    # Self-contained means it lists its own commands and report.json contract.
    assert "agents-shipgate verify --workspace . --config shipgate.yaml" in out
    assert "shipgate audit --host" in out
    assert "merge_verdict" in out
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
    for name in ("agents-md", "claude-md", "cursor", "claude-command"):
        rendered = ALL_RENDERERS[name]()
        assert "advisory" in rendered.lower(), name
