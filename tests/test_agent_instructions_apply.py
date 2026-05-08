"""Library-level tests for ``apply.py`` and ``targets.py``.

Covers the per-target decision tree (every status in the enum), the selector
parser, and PR template path resolution edge cases.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agents_shipgate.cli.discovery.agent_instructions import (
    BLOCK_VERSION,
    TARGETS,
    InvalidSelector,
    apply_agent_instructions,
    parse_selector,
)
from agents_shipgate.cli.discovery.agent_instructions.apply import (
    PR_TEMPLATE_DIR,
    PR_TEMPLATE_LOWER,
    PR_TEMPLATE_UPPER,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    cursor as cursor_module,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    render_agents_md,
    render_cursor_file,
)


def _filesystem_is_case_sensitive(path: Path) -> bool:
    probe = path / ".__case_probe__"
    probe.write_bytes(b"x")
    try:
        return not (path / ".__CASE_PROBE__").exists()
    finally:
        probe.unlink()


case_sensitive_fs = pytest.mark.skipif(
    not _filesystem_is_case_sensitive(Path(__file__).parent),
    reason="PR-template casing tests require a case-sensitive filesystem.",
)

# --- selector parsing ------------------------------------------------------


def test_parse_selector_all_returns_every_target() -> None:
    assert parse_selector("all") == list(TARGETS)


def test_parse_selector_none_returns_empty_list() -> None:
    assert parse_selector("none") == []


def test_parse_selector_csv_preserves_canonical_order() -> None:
    # Selector order is normalized to TARGETS order so JSON output is stable.
    assert parse_selector("cursor,agents-md") == ["agents-md", "cursor"]


def test_parse_selector_strips_whitespace() -> None:
    assert parse_selector(" agents-md ,  cursor ") == ["agents-md", "cursor"]


def test_parse_selector_empty_value_is_invalid() -> None:
    with pytest.raises(InvalidSelector):
        parse_selector("")


def test_parse_selector_unknown_target_is_invalid() -> None:
    with pytest.raises(InvalidSelector) as exc:
        parse_selector("agents-md,bogus")
    assert "bogus" in str(exc.value)
    # Error message advertises valid targets so agents can self-correct.
    for valid in TARGETS:
        assert valid in str(exc.value)


# --- dry-run (write=False) -------------------------------------------------


def test_apply_dry_run_does_not_touch_filesystem(tmp_path: Path) -> None:
    result = apply_agent_instructions(tmp_path, list(TARGETS), write=False)
    assert result.exit_code == 0
    assert {t.status for t in result.targets} == {"would_render"}
    assert all(t.rendered for t in result.targets)
    # No files created in tmp_path.
    assert list(tmp_path.iterdir()) == []


# --- fresh workspace -------------------------------------------------------


def test_apply_write_fresh_workspace_creates_all_targets(tmp_path: Path) -> None:
    result = apply_agent_instructions(tmp_path, list(TARGETS), write=True)
    assert result.exit_code == 0
    assert {t.status for t in result.targets} == {"created_with_block"}
    # Files exist where expected.
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / ".cursor/rules/agents-shipgate.mdc").exists()
    assert (tmp_path / PR_TEMPLATE_LOWER).exists()
    # AGENTS.md preamble + block.
    agents_md = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agents_md.startswith("# Agents")
    assert "<!-- agents-shipgate:start v=1 -->" in agents_md
    assert "<!-- agents-shipgate:end -->" in agents_md


def test_apply_write_idempotent_repeat(tmp_path: Path) -> None:
    """Re-running with no changes is a no-op (UNCHANGED, byte-equal)."""
    apply_agent_instructions(tmp_path, list(TARGETS), write=True)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    second = apply_agent_instructions(tmp_path, list(TARGETS), write=True)
    assert second.exit_code == 0
    assert {t.status for t in second.targets} == {"unchanged"}
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert snapshot == after


# --- AGENTS.md edge cases --------------------------------------------------


def test_apply_appends_to_existing_agents_md_without_markers(tmp_path: Path) -> None:
    original = "# My Project\n\nExisting content.\n"
    (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8")
    result = apply_agent_instructions(tmp_path, ["agents-md"], write=True)
    [outcome] = result.targets
    assert outcome.status == "appended"
    after = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    # User content preserved byte-for-byte at the start.
    assert after.startswith(original)
    assert "<!-- agents-shipgate:start v=1 -->" in after


def test_apply_updates_existing_block_when_content_differs(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "<!-- agents-shipgate:start v=1 -->\n"
        "outdated body\n"
        "<!-- agents-shipgate:end -->\n",
        encoding="utf-8",
    )
    result = apply_agent_instructions(tmp_path, ["agents-md"], write=True)
    [outcome] = result.targets
    assert outcome.status == "updated"
    # New block matches current renderer output.
    after = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert render_agents_md().splitlines()[0] in after


def test_apply_skips_when_block_version_is_newer(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "<!-- agents-shipgate:start v=99 -->\n"
        "future content\n"
        "<!-- agents-shipgate:end -->\n",
        encoding="utf-8",
    )
    result = apply_agent_instructions(tmp_path, ["agents-md"], write=True)
    [outcome] = result.targets
    assert outcome.status == "skipped_newer_version"
    assert outcome.exit_contribution == 2
    assert result.exit_code == 2
    # File untouched.
    assert "future content" in (tmp_path / "AGENTS.md").read_text()


def test_apply_skips_when_markers_are_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "<!-- agents-shipgate:start v=1 -->\n"
        "a\n"
        "<!-- agents-shipgate:start v=1 -->\n"
        "b\n"
        "<!-- agents-shipgate:end -->\n",
        encoding="utf-8",
    )
    result = apply_agent_instructions(tmp_path, ["agents-md"], write=True)
    [outcome] = result.targets
    assert outcome.status == "skipped_ambiguous"
    assert result.exit_code == 2


# --- cursor edge cases -----------------------------------------------------


def test_cursor_unchanged_when_file_matches_current_render(tmp_path: Path) -> None:
    target = tmp_path / ".cursor/rules/agents-shipgate.mdc"
    target.parent.mkdir(parents=True)
    target.write_text(render_cursor_file(), encoding="utf-8")
    result = apply_agent_instructions(tmp_path, ["cursor"], write=True)
    [outcome] = result.targets
    assert outcome.status == "unchanged"


def test_cursor_skipped_when_user_modified(tmp_path: Path) -> None:
    target = tmp_path / ".cursor/rules/agents-shipgate.mdc"
    target.parent.mkdir(parents=True)
    target.write_text("# my own cursor rule, hands off\n", encoding="utf-8")
    result = apply_agent_instructions(tmp_path, ["cursor"], write=True)
    [outcome] = result.targets
    assert outcome.status == "skipped_user_modified"
    assert outcome.exit_contribution == 2
    # File untouched.
    assert target.read_text(encoding="utf-8") == "# my own cursor rule, hands off\n"


def test_cursor_migrated_when_file_matches_prior_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate a prior shipped render whose hash is registered."""
    prior_text = "stub-prior-render\n"
    prior_sha = hashlib.sha256(prior_text.encode("utf-8")).hexdigest()
    monkeypatch.setattr(cursor_module, "PRIOR_RENDER_SHA256", (prior_sha,))
    # Reload the symbol that apply imported at module load.
    from agents_shipgate.cli.discovery.agent_instructions import renderers as _r
    monkeypatch.setattr(_r, "CURSOR_PRIOR_RENDER_SHA256", (prior_sha,))
    from agents_shipgate.cli.discovery.agent_instructions import apply as apply_module
    monkeypatch.setattr(apply_module, "CURSOR_PRIOR_RENDER_SHA256", (prior_sha,))

    target = tmp_path / ".cursor/rules/agents-shipgate.mdc"
    target.parent.mkdir(parents=True)
    target.write_text(prior_text, encoding="utf-8")

    result = apply_agent_instructions(tmp_path, ["cursor"], write=True)
    [outcome] = result.targets
    assert outcome.status == "migrated"
    # File now matches current render.
    assert target.read_text(encoding="utf-8") == render_cursor_file()


# --- PR template path discovery -------------------------------------------


def test_pr_template_creates_lowercase_when_neither_exists(tmp_path: Path) -> None:
    result = apply_agent_instructions(tmp_path, ["pr-template"], write=True)
    [outcome] = result.targets
    assert outcome.status == "created_with_block"
    assert (tmp_path / PR_TEMPLATE_LOWER).exists()
    # Resolved path uses the lowercase form per GitHub's documented convention.
    assert outcome.path.endswith("pull_request_template.md")


@case_sensitive_fs
def test_pr_template_uses_uppercase_when_only_uppercase_exists(tmp_path: Path) -> None:
    upper = tmp_path / PR_TEMPLATE_UPPER
    upper.parent.mkdir(parents=True)
    upper.write_text("# Existing\n", encoding="utf-8")
    result = apply_agent_instructions(tmp_path, ["pr-template"], write=True)
    [outcome] = result.targets
    assert outcome.status == "appended"
    assert outcome.path.endswith("PULL_REQUEST_TEMPLATE.md")
    assert not (tmp_path / PR_TEMPLATE_LOWER).exists()


@case_sensitive_fs
def test_pr_template_picks_marked_one_when_both_exist(tmp_path: Path) -> None:
    upper = tmp_path / PR_TEMPLATE_UPPER
    upper.parent.mkdir(parents=True)
    upper.write_text("# Untouched\n", encoding="utf-8")
    lower = tmp_path / PR_TEMPLATE_LOWER
    lower.write_text(
        "# With marker\n"
        "<!-- agents-shipgate:start v=1 -->\n"
        "stale\n"
        "<!-- agents-shipgate:end -->\n",
        encoding="utf-8",
    )
    result = apply_agent_instructions(tmp_path, ["pr-template"], write=True)
    [outcome] = result.targets
    assert outcome.status == "updated"
    # Used the marked file, ignored the unmarked one.
    assert outcome.path.endswith("pull_request_template.md")
    assert upper.read_text(encoding="utf-8") == "# Untouched\n"


@case_sensitive_fs
def test_pr_template_ambiguous_when_both_exist_without_marker(tmp_path: Path) -> None:
    upper = tmp_path / PR_TEMPLATE_UPPER
    lower = tmp_path / PR_TEMPLATE_LOWER
    upper.parent.mkdir(parents=True)
    upper.write_text("# upper\n", encoding="utf-8")
    lower.write_text("# lower\n", encoding="utf-8")
    result = apply_agent_instructions(tmp_path, ["pr-template"], write=True)
    [outcome] = result.targets
    assert outcome.status == "skipped_ambiguous"
    assert result.exit_code == 2


def test_pr_template_skips_when_directory_form_exists(tmp_path: Path) -> None:
    directory = tmp_path / PR_TEMPLATE_DIR
    directory.mkdir(parents=True)
    (directory / "feature.md").write_text("# template a\n", encoding="utf-8")
    result = apply_agent_instructions(tmp_path, ["pr-template"], write=True)
    [outcome] = result.targets
    assert outcome.status == "skipped_directory_template"
    assert result.exit_code == 2


# --- aggregate exit code ---------------------------------------------------


def test_apply_exit_code_is_max_of_target_contributions(tmp_path: Path) -> None:
    """Mix one success and one skip; result.exit_code must be 2."""
    # Force cursor into skipped_user_modified.
    cursor_path = tmp_path / ".cursor/rules/agents-shipgate.mdc"
    cursor_path.parent.mkdir(parents=True)
    cursor_path.write_text("custom cursor content\n", encoding="utf-8")
    result = apply_agent_instructions(tmp_path, ["agents-md", "cursor"], write=True)
    statuses = {t.name: t.status for t in result.targets}
    assert statuses["agents-md"] == "created_with_block"
    assert statuses["cursor"] == "skipped_user_modified"
    assert result.exit_code == 2


def test_block_version_constant_is_one() -> None:
    """v1 is the initial release; bump only on incompatible content changes."""
    assert BLOCK_VERSION == 1
