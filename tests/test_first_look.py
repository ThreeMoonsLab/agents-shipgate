from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.first_look import _next_command
from agents_shipgate.cli.main import app

runner = CliRunner()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, message: str = "init") -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)


def test_bare_invocation_runs_first_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _commit(repo)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "first look" in result.output
    assert "working tree:" in result.output
    assert "→ Next:" in result.output


def test_first_look_routes_to_verify_when_manifest_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text("version: '0.1'\n", encoding="utf-8")
    _commit(repo)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "shipgate.yaml present" in result.output
    assert "agents-shipgate verify" in result.output


def test_first_look_reports_host_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"docs": {"command": "docs-server"}}}\n', encoding="utf-8"
    )
    _commit(repo)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "host grants:" in result.output
    assert "MCP server" in result.output


def test_first_look_never_hides_incomplete_host_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".mcp.json").write_text("{not valid json\n", encoding="utf-8")
    _commit(repo)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "host grants:" in result.output
    assert "coverage incomplete" in result.output
    assert "no agent tool surface or host config detected" not in result.output


def test_first_look_reports_untracked_files_as_not_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _commit(repo)
    # A new tool file the agent created but never `git add`-ed. Its content is
    # absent from the diff body, but it is uncommitted work — not "clean".
    (repo / "new_tool.py").write_text("# new tool\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    working_tree_line = next(
        line for line in result.output.splitlines() if "working tree:" in line
    )
    assert "untracked" in working_tree_line
    assert "clean" not in working_tree_line


def test_first_look_evaluates_untracked_host_boundary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _commit(repo)
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        '{"permissions":{"allow":["Bash(*)"]}}\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    working_tree_line = next(
        line for line in result.output.splitlines() if "working tree:" in line
    )
    assert "human_review" in working_tree_line or "block" in working_tree_line
    assert "allow" not in working_tree_line


def test_bare_invocation_outside_git_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "plain"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "not a git checkout" in result.output


def test_named_subcommand_is_not_hijacked_by_first_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _commit(repo)
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["detect", "--json"])

    assert result.exit_code == 0, result.output
    assert '"is_agent_project"' in result.output
    assert "first look" not in result.output


def test_version_flag_still_short_circuits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert "Agents Shipgate" in result.output
    assert "first look" not in result.output


def test_next_command_routing_priority() -> None:
    # Manifest present wins regardless of the other signals.
    assert "verify" in _next_command(True, True, True)
    assert "verify" in _next_command(True, False, False)
    # No manifest, but an agent tool surface: stay inside the verify/check flows.
    assert "verify --preview" in _next_command(False, True, False)
    assert "check" in _next_command(False, True, False)
    # No manifest, no agent surface, but host grants: route to the host audit.
    assert "audit --host" in _next_command(False, False, True)
    # Nothing relevant: say so plainly.
    assert "may not apply" in _next_command(False, False, False)
