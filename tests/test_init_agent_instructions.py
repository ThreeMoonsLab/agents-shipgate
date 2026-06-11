"""CLI matrix tests for ``agents-shipgate init --agent-instructions=<selector>``.

Mirrors :mod:`tests.test_init_ci` for the orthogonal-flag matrix. Verifies
dry-run output, write semantics, idempotent re-runs, composability with
``--write`` and ``--ci``, structured errors under ``AGENTS_SHIPGATE_AGENT_MODE``,
and the Rule 3 strict-CI safety guard at the rendered-content level.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.discovery.agent_instructions import DEFAULT_TARGETS
from agents_shipgate.cli.discovery.agent_instructions.targets import SPECS, TARGETS
from agents_shipgate.cli.discovery.ci_workflow import WORKFLOW_RELATIVE_PATH
from agents_shipgate.cli.main import app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
runner = CliRunner()


def _seed_workspace(tmp_path: Path, sample: str) -> Path:
    dst = tmp_path / "ws"
    shutil.copytree(SAMPLES / sample, dst)
    target = dst / "shipgate.yaml"
    if target.exists():
        target.unlink()
    reports = dst / "agents-shipgate-reports"
    if reports.exists():
        shutil.rmtree(reports)
    return dst


# --- dry-run ---------------------------------------------------------------


def test_dry_run_default_targets_emits_section_headers(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--agent-instructions=default"],
    )
    assert result.exit_code == 0, result.output
    # Manifest section header.
    assert "--- shipgate.yaml ---" in result.output
    # Per-target section headers.
    for name in DEFAULT_TARGETS:
        assert f"--- {SPECS[name].relative_path} ---" in result.output
    assert "--- .agents/skills/agents-shipgate ---" not in result.output


def test_dry_run_default_targets_json_has_rendered_content(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--agent-instructions=default",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ai = payload["agent_instructions"]
    assert ai["requested"] == list(DEFAULT_TARGETS)
    assert ai["block_version"] == 1
    statuses = {t["name"]: t["status"] for t in ai["targets"]}
    assert statuses == {name: "would_render" for name in DEFAULT_TARGETS}
    for entry in ai["targets"]:
        assert entry["rendered"]
    kit_sources = {
        entry["name"]: entry.get("kit_source")
        for entry in ai["targets"]
        if entry["name"] in {"codex-skill", "claude-code-skill"}
    }
    assert kit_sources == {}
    # No filesystem changes.
    for name in DEFAULT_TARGETS:
        assert not (workspace / SPECS[name].relative_path).exists()


def test_dry_run_all_targets_json_includes_opt_in_targets(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--agent-instructions=all", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["agent_instructions"]["requested"] == list(TARGETS)
    assert {target["name"] for target in payload["agent_instructions"]["targets"]} == set(TARGETS)


def test_dry_run_subset_selector(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--agent-instructions=agents-md,cursor",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = [t["name"] for t in payload["agent_instructions"]["targets"]]
    assert names == ["agents-md", "cursor"]


def test_dry_run_none_selector_emits_empty_targets_list(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--agent-instructions=none", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["agent_instructions"] == {
        "requested": [],
        "block_version": 1,
        "targets": [],
    }


def test_explicit_agent_instructions_kit_reports_local_source(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    override_root = workspace / ".agents-shipgate/adoption-kit/codex-skill"
    override_root.mkdir(parents=True)
    override_root.joinpath("SKILL.md").write_text(
        "# Custom Codex Skill\n",
        encoding="utf-8",
    )
    kit_path = workspace / ".agents-shipgate/custom-kit.yaml"
    kit_path.write_text(
        "schema_version: 1\n"
        "targets:\n"
        "  codex-skill:\n"
        "    overrides_dir: .agents-shipgate/adoption-kit/codex-skill\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--agent-instructions=codex-skill",
            "--agent-instructions-kit",
            str(kit_path.relative_to(workspace)),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    [target] = payload["agent_instructions"]["targets"]
    assert target["kit_source"] == "bundled_plus_local_override"
    rendered_skill = next(
        file["content"] for file in target["files"] if file["path"].endswith("/SKILL.md")
    )
    assert rendered_skill == "# Custom Codex Skill\n"


def test_auto_discovered_agent_instructions_kit_is_used_on_write(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    override_root = workspace / ".agents-shipgate/adoption-kit/codex-skill"
    override_root.mkdir(parents=True)
    override_root.joinpath("references").mkdir()
    override_root.joinpath("references/report-reading.md").write_text(
        "# Custom Report Reader\n",
        encoding="utf-8",
    )
    (workspace / ".agents-shipgate/adoption-kit.yaml").write_text(
        "schema_version: 1\n"
        "targets:\n"
        "  codex-skill:\n"
        "    overrides_dir: .agents-shipgate/adoption-kit/codex-skill\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=codex-skill",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    [target] = payload["agent_instructions"]["targets"]
    assert target["kit_source"] == "bundled_plus_local_override"
    assert (workspace / ".agents/skills/agents-shipgate/references/report-reading.md").read_text(
        encoding="utf-8"
    ) == "# Custom Report Reader\n"


def test_invalid_agent_instructions_kit_fails_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    kit_path = workspace / ".agents-shipgate/adoption-kit.yaml"
    kit_path.parent.mkdir(parents=True)
    kit_path.write_text("schema_version: 2\n", encoding="utf-8")
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=codex-skill",
        ],
    )

    assert result.exit_code == 2
    assert not (workspace / "shipgate.yaml").exists()
    assert '"error": "config_error"' in result.output
    assert str(kit_path) in result.output


def test_agent_instructions_kit_absolute_override_outside_workspace_error(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    outside = tmp_path / "outside-overrides"
    outside.mkdir()
    kit_path = workspace / ".agents-shipgate/adoption-kit.yaml"
    kit_path.parent.mkdir(parents=True)
    kit_path.write_text(
        f"schema_version: 1\ntargets:\n  codex-skill:\n    overrides_dir: {outside}\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=codex-skill",
        ],
    )

    assert result.exit_code == 2
    assert "resolves outside workspace" in result.output
    assert "is a symlink" not in result.output


def test_invalid_selector_exits_two_with_human_error(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--agent-instructions=bogus"],
    )
    assert result.exit_code == 2
    # Human-readable error mentions the bad selector.
    combined = result.output + (result.stderr if result.stderr_bytes is not None else "")
    assert "bogus" in combined


def test_invalid_selector_emits_structured_error_under_agent_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--agent-instructions=nope"],
    )
    assert result.exit_code == 2
    # Structured stderr line — runner mixes streams; search for the JSON.
    output = result.output
    assert '"error": "config_error"' in output
    assert '"next_action"' in output


# --- --write -------------------------------------------------------------


def test_write_default_targets_on_fresh_workspace(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=default",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ai = payload["agent_instructions"]
    assert {t["status"] for t in ai["targets"]} == {
        "created_with_block",
    }
    assert "workflow" not in payload
    assert payload["local_contract"]["status"] == "created_with_block"
    # Files exist.
    for name in DEFAULT_TARGETS:
        path = workspace / SPECS[name].relative_path
        assert path.exists()
    assert not (workspace / WORKFLOW_RELATIVE_PATH).exists()
    assert not (workspace / ".agents/skills/agents-shipgate").exists()
    assert not (workspace / ".claude/skills/agents-shipgate").exists()
    # AGENTS.md has the H1 preamble + managed block.
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert agents_md.startswith("# Agents")
    assert "<!-- agents-shipgate:start v=1 -->" in agents_md


def test_write_default_idempotent_rerun_is_noop(tmp_path: Path) -> None:
    """The advertised refresh command — `init --write --agent-instructions=default`
    — must be idempotent at the process level. A re-run reports every target as
    ``unchanged``, exits 0 (even though shipgate.yaml already exists, because
    the user's primary intent under --agent-instructions is the snippet
    refresh, not manifest creation), and the workspace is byte-equal."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    first = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=default",
            "--json",
        ],
    )
    assert first.exit_code == 0, first.output
    snapshot = {
        p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()
    }
    second = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=default",
            "--json",
        ],
    )
    # Idempotent at the process level: exit 0 even though shipgate.yaml exists.
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    # Manifest action reports the skip informationally; agent-instructions
    # all unchanged.
    assert payload["manifest_status"] == "skipped_existing"
    assert "workflow" not in payload
    assert {t["status"] for t in payload["agent_instructions"]["targets"]} == {"unchanged"}
    after = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    # Byte-equal across the run — the canonical "safe to run repeatedly" proof.
    assert snapshot == after


def test_manifest_skip_still_exits_two_without_agent_instructions(
    tmp_path: Path,
) -> None:
    """Backwards compatibility: `init --write` (no --agent-instructions)
    against an existing shipgate.yaml still exits 2. The idempotency
    accommodation only applies when --agent-instructions is set."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    (workspace / "shipgate.yaml").write_text("# user manifest\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "skipped_existing"


def test_manifest_skip_exits_two_with_agent_instructions_none(
    tmp_path: Path,
) -> None:
    """`--agent-instructions=none` runs no instruction action, so the
    idempotency accommodation should NOT apply — manifest skip still exits 2,
    matching plain `init --write` behavior."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    (workspace / "shipgate.yaml").write_text("# user manifest\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=none",
            "--json",
        ],
    )
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "skipped_existing"
    assert payload["agent_instructions"]["targets"] == []


def test_write_appends_to_existing_agents_md_without_markers(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    original = "# Project AGENTS.md\n\nUser-authored prose.\n"
    (workspace / "AGENTS.md").write_text(original, encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=agents-md",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    [outcome] = payload["agent_instructions"]["targets"]
    assert outcome["status"] == "appended"
    # User content preserved at the start.
    after = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert after.startswith(original)


def test_write_cursor_skipped_when_user_modified_exits_two(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    cursor = workspace / ".cursor/rules/agents-shipgate.mdc"
    cursor.parent.mkdir(parents=True)
    cursor.write_text("# user-authored cursor rule\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=cursor",
            "--json",
        ],
    )
    assert result.exit_code == 2
    payload = json.loads(result.output)
    [outcome] = payload["agent_instructions"]["targets"]
    assert outcome["status"] == "skipped_user_modified"
    # File untouched.
    assert cursor.read_text(encoding="utf-8") == "# user-authored cursor rule\n"


def test_skipped_target_emits_structured_stderr_under_agent_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand-edited cursor + AGENTS_SHIPGATE_AGENT_MODE=1 produces a structured
    next_action JSON line on stderr so coding-agent callers can route to a fix
    without scraping stdout."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    cursor = workspace / ".cursor/rules/agents-shipgate.mdc"
    cursor.parent.mkdir(parents=True)
    cursor.write_text("# user-authored cursor rule\n", encoding="utf-8")
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=cursor",
        ],
    )
    assert result.exit_code == 2
    output = result.output
    assert '"error": "config_already_exists"' in output
    assert '"next_action"' in output
    # The next_action should reference the affected target and re-run command.
    assert "agent-instructions=cursor" in output


# --- composability with --ci ---------------------------------------------


def test_triple_combo_init_write_ci_agent_instructions(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--ci",
            "--agent-instructions=default",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # All three orthogonal actions present.
    assert payload["manifest_status"] == "written"
    assert payload["workflow"]["status"] == "written"
    assert payload["local_contract"]["status"] == "created_with_block"
    assert payload["agent_instructions"]["block_version"] == 1
    # Files on disk.
    assert (workspace / "shipgate.yaml").exists()
    assert (workspace / WORKFLOW_RELATIVE_PATH).exists()
    for name in DEFAULT_TARGETS:
        assert (workspace / SPECS[name].relative_path).exists()


def test_write_all_targets_includes_opt_in_targets_without_ci_side_effect(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--agent-instructions=all",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "workflow" not in payload
    assert payload["agent_instructions"]["requested"] == list(TARGETS)
    assert (workspace / ".agents/skills/agents-shipgate/SKILL.md").exists()
    assert (workspace / ".claude/skills/agents-shipgate/SKILL.md").exists()
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".github/pull_request_template.md").exists()
    assert not (workspace / WORKFLOW_RELATIVE_PATH).exists()


def test_init_command_documents_agent_instructions() -> None:
    """The init command must expose ``--agent-instructions`` and the help
    string must call out advisory-only behavior (Rule 3).

    Existence is checked via Click param introspection — terminal-width
    rendering varies across CI runners (Rich truncates option names on
    narrow terminals even with COLUMNS set), and we should not gate merge
    on whether the rendered string fits."""
    from typer.main import get_command

    click_app = get_command(app)
    init_cmd = click_app.commands["init"]
    param_names = {p.name for p in init_cmd.params}
    assert "agent_instructions" in param_names

    init_param = next(p for p in init_cmd.params if p.name == "agent_instructions")
    # Decls include the long-form flag; help text mentions advisory.
    assert any("--agent-instructions" in opt for opt in init_param.opts)
    assert "advisory" in (init_param.help or "").lower()


def test_existing_init_tests_unaffected_by_default(tmp_path: Path) -> None:
    """Without --agent-instructions, the JSON payload must NOT include
    ``agent_instructions`` (matches the workflow precedent: presence-only)."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "agent_instructions" not in payload
