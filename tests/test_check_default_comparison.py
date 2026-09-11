"""#649: `check` compares against the merge base by default.

On a branch whose changes were already committed, `shipgate check` used to
answer `allow` with an empty change set — it compared the working tree with
`HEAD`, so it was truthfully reporting "nothing is uncommitted" to someone
asking "what does this branch change". Reaching the real answer took
`--base main --head HEAD`, a pair the help never suggested and which the
CLI rejected unless both were given.

The ladder these cases pin, in order: both refs, `--base` alone, `--head`
alone, neither, being on the default branch, and no detectable base at all.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()

BASE_SETTINGS = '{"permissions": {"allow": ["Bash(pytest *)", "Read(**)"]}}'
WIDENED_SETTINGS = '{"permissions": {"allow": ["Bash(*)", "Read(**)", "WebFetch(*)"]}}'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path, *, default_branch: str = "main") -> Path:
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(BASE_SETTINGS, encoding="utf-8")
    _git(repo, "init", "-q", "-b", default_branch)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _widen_on_a_branch(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feat/widen")
    (repo / ".claude" / "settings.json").write_text(WIDENED_SETTINGS, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "widen the allow list")


def _check(repo: Path, *extra: str) -> dict:
    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(repo),
            "--agent",
            "claude-code",
            "--format",
            "agent-boundary-json",
            *extra,
        ],
    )
    assert result.output, result
    return json.loads(result.output)


# --- the reported defect ---------------------------------------------------


def test_a_committed_branch_change_is_visible_without_any_flags(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _widen_on_a_branch(repo)

    payload = _check(repo)

    assert payload["decision"] == "block"
    assert payload["changed_files"] == [".claude/settings.json"]
    assert payload["violations"], "the widened allow rule must be named"


def test_the_compared_base_is_published_on_the_result(tmp_path: Path) -> None:
    """A verdict whose base is unnamed cannot be reviewed."""

    repo = _repo(tmp_path)
    _widen_on_a_branch(repo)

    payload = _check(repo)

    assert payload["subject"]["base"] == "main"


def test_uncommitted_work_on_the_branch_is_included(tmp_path: Path) -> None:
    """One comparison spanning committed and uncommitted work, not two."""

    repo = _repo(tmp_path)
    _widen_on_a_branch(repo)
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"pg": {"command": "npx", "args": ["server-postgres"]}}}',
        encoding="utf-8",
    )

    payload = _check(repo)

    assert set(payload["changed_files"]) == {".claude/settings.json", ".mcp.json"}


# --- the rest of the ladder ------------------------------------------------


def test_base_alone_is_accepted_and_compares_against_the_working_tree(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _widen_on_a_branch(repo)

    payload = _check(repo, "--base", "main")

    assert payload["decision"] == "block"
    assert payload["subject"]["base"] == "main"


def test_head_alone_is_accepted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _widen_on_a_branch(repo)

    payload = _check(repo, "--head", "HEAD")

    assert payload["subject"]["base"] == "main"
    assert payload["subject"]["head"] == "HEAD"
    assert payload["decision"] == "block"


def test_base_head_keeps_the_old_uncommitted_only_comparison(tmp_path: Path) -> None:
    """The previous default stays reachable, by name."""

    repo = _repo(tmp_path)
    _widen_on_a_branch(repo)

    payload = _check(repo, "--base", "HEAD")

    assert payload["changed_files"] == []
    assert payload["decision"] == "allow"


def test_on_the_default_branch_the_answer_is_the_working_tree(tmp_path: Path) -> None:
    """Being on the default branch is the commonest reason no other base is
    detected, and there worktree-against-HEAD is complete, not wrong."""

    repo = _repo(tmp_path)
    payload = _check(repo)

    assert payload["decision"] == "allow"
    assert payload["changed_files"] == []

    (repo / ".claude" / "settings.json").write_text(WIDENED_SETTINGS, encoding="utf-8")
    widened = _check(repo)

    assert widened["decision"] == "block"
    assert widened["changed_files"] == [".claude/settings.json"]


def test_an_undetectable_base_stops_instead_of_allowing(tmp_path: Path) -> None:
    """No remote, no main/master: the honest answer is "tell me", never a
    pass computed from a question nobody asked."""

    repo = _repo(tmp_path, default_branch="trunk")
    _git(repo, "checkout", "-q", "-b", "work")
    (repo / ".claude" / "settings.json").write_text(WIDENED_SETTINGS, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "widen")

    result = runner.invoke(
        app,
        ["check", "--workspace", str(repo), "--agent", "claude-code"],
    )

    assert result.exit_code == 2
    assert "No base ref could be detected" in result.output
    assert "--base" in result.output
    assert "allow" not in result.output


# --- the reason the local fallback is narrow -------------------------------


def test_a_stale_local_default_never_beats_a_remote_one(tmp_path: Path) -> None:
    """The local fallback exists only for repositories with no remote. Where
    a remote exists it remains the authority, because a local `main` is
    exactly the ref that goes stale."""

    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "--bare", "-b", "main")

    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    # The local default now moves ahead; the remote is what a PR is against.
    (repo / "README.md").write_text("local only\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "local drift")
    _widen_on_a_branch(repo)

    payload = _check(repo)

    assert payload["subject"]["base"] == "origin/main"


def test_empty_refs_are_still_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = runner.invoke(
        app, ["check", "--workspace", str(repo), "--base", "", "--head", "HEAD"]
    )

    assert result.exit_code == 2
    assert "cannot be empty" in result.output


@pytest.mark.parametrize("flag", ["--base", "--head"])
def test_a_ref_cannot_smuggle_an_option(tmp_path: Path, flag: str) -> None:
    repo = _repo(tmp_path)

    result = runner.invoke(
        app, ["check", "--workspace", str(repo), flag, "--upload-pack=evil"]
    )

    assert result.exit_code == 2
