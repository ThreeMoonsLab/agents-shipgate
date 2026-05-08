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

from typer.testing import CliRunner

from agents_shipgate.cli.discovery.agent_instructions import TARGETS
from agents_shipgate.cli.discovery.agent_instructions.targets import SPECS
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


def test_dry_run_all_targets_emits_section_headers(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--agent-instructions=all"],
    )
    assert result.exit_code == 0, result.output
    # Manifest section header.
    assert "--- shipgate.yaml ---" in result.output
    # Per-target section headers.
    for name in TARGETS:
        assert f"--- {SPECS[name].relative_path} ---" in result.output


def test_dry_run_all_targets_json_has_rendered_content(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--agent-instructions=all", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    ai = payload["agent_instructions"]
    assert ai["requested"] == list(TARGETS)
    assert ai["block_version"] == 1
    statuses = {t["name"]: t["status"] for t in ai["targets"]}
    assert statuses == {name: "would_render" for name in TARGETS}
    for entry in ai["targets"]:
        assert entry["rendered"]
    # No filesystem changes.
    for name in TARGETS:
        assert not (workspace / SPECS[name].relative_path).exists()


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
    monkeypatch,
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


def test_write_all_targets_on_fresh_workspace(tmp_path: Path) -> None:
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
    ai = payload["agent_instructions"]
    assert {t["status"] for t in ai["targets"]} == {"created_with_block"}
    # Files exist.
    for name in TARGETS:
        path = workspace / SPECS[name].relative_path
        assert path.exists()
    # AGENTS.md has the H1 preamble + managed block.
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert agents_md.startswith("# Agents")
    assert "<!-- agents-shipgate:start v=1 -->" in agents_md


def test_write_idempotent_rerun_is_noop(tmp_path: Path) -> None:
    """The advertised refresh command — `init --write --agent-instructions=all`
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
            "--agent-instructions=all",
            "--json",
        ],
    )
    assert first.exit_code == 0, first.output
    snapshot = {
        rel: (workspace / rel).read_bytes()
        for rel in (SPECS[name].relative_path for name in TARGETS)
    }
    second = runner.invoke(
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
    # Idempotent at the process level: exit 0 even though shipgate.yaml exists.
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    # Manifest action reports the skip informationally; agent-instructions
    # all unchanged.
    assert payload["manifest_status"] == "skipped_existing"
    assert {t["status"] for t in payload["agent_instructions"]["targets"]} == {"unchanged"}
    after = {
        rel: (workspace / rel).read_bytes()
        for rel in (SPECS[name].relative_path for name in TARGETS)
    }
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
    tmp_path: Path, monkeypatch
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
            "--agent-instructions=all",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # All three orthogonal actions present.
    assert payload["manifest_status"] == "written"
    assert payload["workflow"]["status"] == "written"
    assert payload["agent_instructions"]["block_version"] == 1
    # Files on disk.
    assert (workspace / "shipgate.yaml").exists()
    assert (workspace / WORKFLOW_RELATIVE_PATH).exists()
    for name in TARGETS:
        assert (workspace / SPECS[name].relative_path).exists()


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
