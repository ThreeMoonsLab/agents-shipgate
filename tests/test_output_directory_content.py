"""An output directory that holds repository content is refused, not hidden (#804).

`verify` leaves its output directory out of every working-tree read — the
change set, the overlay its pointer binds, the manifest-free host comparison,
the static input census — and `agent control` leaves the reports directory out
of the change set it checks, so that a run's own reports never count as part of
the change. When `--out` named a directory holding anything else, that content
was hidden: `--out docs` kept a pointer `complete` across every later edit to a
tracked `docs/a.txt`, and `--out .claude` turned a widened
`.claude/settings.json` from `human_review_required` into `complete` and
`mergeable`.

The rules pinned here:

* `verify`, `--head`, `--preview` and manifest-free `verify` refuse, before
  anything is written, an output directory inside the repository that holds a
  path committed at `HEAD`, a path a worktree change removes from the merge
  base it is compared against, a staged or untracked unignored path that is
  not a Shipgate artifact, or a trust-root path whatever its name, and a
  directory inside a trust root Git does not ignore. An archived `--head` run
  compares only its head, whose own diff shows a removal.
* The directory is judged by physical identity: a symlink into the repository
  and a case-variant spelling are the directory they physically are, and the
  default directory is recognized the same way.
* A gitignored directory, the default `agents-shipgate-reports`, a directory
  holding only Shipgate artifacts, a sibling outside the repository, and
  `verify --preview` outside Git all keep working, and every file real runs
  over the shipped samples, and the `skill` commands' default reports, leave
  behind is a recognized artifact.
* Every reader refuses a pointer already sitting in such a directory, for every
  control state, as `workspace_unverifiable`, and a classification that fails
  refuses instead of skipping the currency checks. The recovery it names keeps
  the producing run's request, and never routes back into the default
  directory when that is the one refused.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
REPORTS = "agents-shipgate-reports"
ENV = {"NO_COLOR": "1", "GITHUB_ACTIONS": None, "FORCE_COLOR": None, "COLUMNS": "200"}
AGENT_ENV = {**ENV, "AGENTS_SHIPGATE_AGENT_MODE": "1"}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
WIDENING_FINDING = "SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED"

runner = CliRunner()


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(
    path: Path,
    *,
    claude: bool = True,
    docs: bool = False,
    ignore: str = f"{REPORTS}/\n",
) -> Path:
    """The clean read-only sample, committed on `main`, checked out on `feature`."""

    path.mkdir(parents=True)
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copy(SAMPLE / name, path / name)
    if claude:
        (path / ".claude").mkdir()
        _write_settings(path / ".claude" / "settings.json", ["Bash(git status)"])
    if docs:
        (path / "docs").mkdir()
        (path / "docs" / "a.txt").write_text("a\n", encoding="utf-8")
    (path / ".gitignore").write_text(ignore, encoding="utf-8")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.test")
    _git(path, "config", "user.name", "Test User")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "fixture")
    _git(path, "checkout", "-q", "-b", "feature")
    return path


def _write_settings(path: Path, allow: list[str]) -> None:
    path.write_text(
        json.dumps({"permissions": {"allow": allow}}, indent=2) + "\n", encoding="utf-8"
    )


def _widen(repo: Path) -> None:
    _write_settings(
        repo / ".claude" / "settings.json", ["Bash(git status)", "Bash(curl:*)", "Bash(rm:*)"]
    )


def _verify(repo: Path, *extra: str, env=ENV):
    return runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", *extra,
         "--format", "control"],
        env=env,
    )


def _control(repo: Path, reports: Path | None = None, env=ENV):
    args = ["agent", "control", "--workspace", str(repo)]
    if reports is not None:
        args += ["--reports-dir", str(reports)]
    return runner.invoke(app, args, env=env)


def _error_line(output: str) -> dict:
    lines = [line for line in _plain(output).splitlines() if line.startswith('{"error"')]
    assert lines, f"no agent-mode error line in:\n{output}"
    return json.loads(lines[-1])


def _refusal(result) -> str:
    lines = [
        line
        for line in _plain(result.output).splitlines()
        if line.startswith("Current control is unavailable")
    ]
    assert lines, _plain(result.output)
    return lines[-1]


def _assert_refused(result, *, shown: str, found: str) -> None:
    """A writer refusal: exit 2, naming the directory, what it holds, and the way out."""

    text = _plain(result.output)
    assert result.exit_code == 2, text
    assert f"Verifier --out {shown} {found}" in text, text
    assert "Omit --out to use the default agents-shipgate-reports" in text, text
    line = _error_line(result.output)
    assert line["error"] == "config_error"
    assert line["exit_code"] == 2
    action = line["next_actions"][0]
    # Nothing about the manifest is wrong; the route is choosing a directory.
    assert action["kind"] == "review", action
    assert "agents-shipgate-reports" in action["why"], action


def _nothing_written(directory: Path, before: set[str]) -> None:
    after = {path.name for path in directory.iterdir()} if directory.is_dir() else set()
    assert after == before, sorted(after - before)


# -- the writer --------------------------------------------------------------


@pytest.mark.parametrize("route", ["worktree", "head", "preview"])
def test_every_verify_route_refuses_a_directory_holding_tracked_files(
    tmp_path: Path, route: str
):
    repo = _repo(tmp_path / "repo", claude=False, docs=True)
    extra = {"worktree": [], "head": ["--head", "HEAD"], "preview": ["--preview"]}[route]

    result = _verify(repo, *extra, "--out", str(repo / "docs"), env=AGENT_ENV)

    _assert_refused(result, shown="docs", found="holds tracked repository files (docs/a.txt)")
    # Refused before the pointer is invalidated or anything is written.
    _nothing_written(repo / "docs", {"a.txt"})
    assert _git(repo, "status", "--porcelain") == ""


def test_manifest_free_verify_refuses_the_same_directory(tmp_path: Path):
    repo = _repo(tmp_path / "repo")
    _git(repo, "rm", "-q", "shipgate.yaml", "tools.json")
    _git(repo, "commit", "-q", "-m", "manifest-free")
    _widen(repo)

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--base", "main", "--out",
         str(repo / ".claude"), "--format", "control"],
        env=AGENT_ENV,
    )

    _assert_refused(
        result, shown=".claude", found="holds tracked repository files (.claude/settings.json)"
    )
    _nothing_written(repo / ".claude", {"settings.json"})


def test_manifest_free_verify_compares_the_base_its_host_comparison_uses(tmp_path: Path):
    """Without a remote, the host comparison diffs against the local `main`.

    The pointer records that merge base, and the reader counts what it held.
    Resolving the base only the way the configured verifier does (which skips a
    local `main`) accepted `--out .vscode` over a committed removal of
    `.vscode/mcp.json`, and the run's own pointer then refused as soon as it
    was read.
    """

    repo = tmp_path / "repo"
    (repo / ".vscode").mkdir(parents=True)
    (repo / ".vscode" / "mcp.json").write_text(
        json.dumps({"servers": {"fs": {"command": "npx", "args": ["-y", "server-fs", "/"]}}})
        + "\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(f"{REPORTS}/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "rm", "-q", ".vscode/mcp.json")
    _git(repo, "commit", "-q", "-m", "drop the mcp server")

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--out", str(repo / ".vscode"),
         "--format", "control"],
        env=AGENT_ENV,
    )

    _assert_refused(
        result,
        shown=".vscode",
        found=(
            "held committed repository files that the change being verified "
            "removes (.vscode/mcp.json)"
        ),
    )
    assert not (repo / ".vscode").exists()


def test_a_manifest_named_tool_source_directory_is_refused(tmp_path: Path):
    """The tool source a manifest names is a decision input, and it was hidden.

    With `--out tools`, a destructive tool added to `tools/tools.json` after the
    run left `agent control` at `complete` with `merge=true`, and the plan
    stopped binding the tool source at all.
    """

    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "shipgate.yaml").write_text(
        (SAMPLE / "shipgate.yaml").read_text(encoding="utf-8").replace(
            "path: tools.json", "path: tools/tools.json"
        ),
        encoding="utf-8",
    )
    shutil.copy(SAMPLE / "tools.json", repo / "tools" / "tools.json")
    (repo / ".gitignore").write_text(f"{REPORTS}/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")

    refused = _verify(repo, "--out", str(repo / "tools"), env=AGENT_ENV)
    _assert_refused(
        refused, shown="tools", found="holds tracked repository files (tools/tools.json)"
    )
    _nothing_written(repo / "tools", {"tools.json"})

    # The default run binds the directory as an input, which is why it matters.
    verified = _verify(repo)
    assert verified.exit_code == 0, _plain(verified.output)
    plan = json.loads((repo / REPORTS / "verification-plan.json").read_text(encoding="utf-8"))
    assert [source["path"] for source in plan["inputs"]["tool_sources"]] == ["tools/tools.json"]


@pytest.mark.parametrize("committed", [False, True], ids=["uncommitted", "committed"])
def test_a_claude_permission_widening_cannot_be_hidden_by_the_output_directory(
    tmp_path: Path, committed: bool
):
    """The headline case: the decision itself flipped to `complete`/`mergeable`."""

    repo = _repo(tmp_path / "repo")
    _widen(repo)
    if committed:
        _git(repo, "commit", "-q", "-am", "widen shell permissions")

    decided = _verify(repo, "--base", "main")
    assert decided.exit_code == 0, _plain(decided.output)
    envelope = json.loads(decided.stdout)
    assert envelope["control_state"] != "complete", envelope
    assert envelope["permissions"]["merge"] is False, envelope
    verifier = json.loads((repo / REPORTS / "verifier.json").read_text(encoding="utf-8"))
    report = json.loads((repo / REPORTS / "report.json").read_text(encoding="utf-8"))
    assert verifier["merge_verdict"] == "human_review_required"
    assert ".claude/settings.json" in verifier["changed_files"]
    assert WIDENING_FINDING in {finding["check_id"] for finding in report["findings"]}

    hidden = _verify(repo, "--base", "main", "--out", str(repo / ".claude"), env=AGENT_ENV)
    _assert_refused(
        hidden, shown=".claude", found="holds tracked repository files (.claude/settings.json)"
    )
    _nothing_written(repo / ".claude", {"settings.json"})


def test_an_untracked_new_settings_file_is_repository_content(tmp_path: Path):
    """A rule about tracked files alone would miss a brand-new host configuration."""

    repo = _repo(tmp_path / "repo", claude=False)
    (repo / ".claude").mkdir()
    _write_settings(repo / ".claude" / "settings.json", ["Bash(curl:*)"])

    decided = _verify(repo, "--base", "main")
    assert decided.exit_code == 0, _plain(decided.output)
    assert json.loads(decided.stdout)["permissions"]["merge"] is False

    hidden = _verify(repo, "--base", "main", "--out", str(repo / ".claude"), env=AGENT_ENV)
    _assert_refused(
        hidden,
        shown=".claude",
        found=(
            "holds untracked files that Git does not ignore and that are not "
            "Shipgate artifacts (.claude/settings.json)"
        ),
    )


def _deny_rules_removed(repo: Path, *, commit: bool) -> None:
    """`main` carries deny rules; the change deletes the whole settings file."""

    _git(repo, "checkout", "-q", "main")
    _write_settings(repo / ".claude" / "settings.json", ["Bash(git status)"])
    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    settings["permissions"]["deny"] = ["Bash(curl:*)", "Bash(rm:*)"]
    (repo / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    _git(repo, "commit", "-q", "-am", "deny shell egress")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "main")
    _git(repo, "rm", "-q", "-r", ".claude")
    if commit:
        _git(repo, "commit", "-q", "-m", "drop the claude settings")
    assert not (repo / ".claude").exists()


@pytest.mark.parametrize(
    "shape", ["staged", "committed_explicit_base", "committed_detected_base"]
)
def test_a_deletion_leaves_nothing_on_disk_and_is_still_refused(tmp_path: Path, shape: str):
    """An absent directory is not an empty one when the change deletes what it held.

    Deleting a settings file that carries deny rules removes them
    (`SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED`). With `--out .claude` there
    was no directory left to find content in, the exclusion hid the deletion,
    and the run read `complete` and `mergeable`.
    """

    repo = _repo(tmp_path / "repo")
    _deny_rules_removed(repo, commit=shape != "staged")
    base = ["--base", "main"] if shape == "committed_explicit_base" else []
    if shape == "committed_detected_base":
        # Only remote refs are detected, and this run resolves the same one.
        _git(repo, "update-ref", "refs/remotes/origin/main", "main")

    decided = _verify(repo, *base)
    assert decided.exit_code == 0, _plain(decided.output)
    assert json.loads(decided.stdout)["permissions"]["merge"] is False
    report = json.loads((repo / REPORTS / "report.json").read_text(encoding="utf-8"))
    assert "SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED" in {
        finding["check_id"] for finding in report["findings"]
    }

    hidden = _verify(repo, *base, "--out", str(repo / ".claude"), env=AGENT_ENV)
    # A staged deletion is named as the removal it is, not as files the
    # directory "holds": nothing is left in the index or on disk.
    _assert_refused(
        hidden,
        shown=".claude",
        found=(
            "held committed repository files that the change being verified "
            "removes (.claude/settings.json)"
        ),
    )
    assert not (repo / ".claude").exists()


@pytest.mark.parametrize("directory", ["docs", ".claude"])
def test_a_pointer_that_hid_a_committed_deletion_refuses_on_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str
):
    """The reader counts the merge base the pointer records, not only HEAD.

    `docs` pins that read. `.claude` is refused before its merge base is
    consulted, because it is a trust root Git does not ignore, and the
    `1.0.0` pointer that hid the deny-rule deletion there still goes non-current.
    """

    repo = _repo(tmp_path / "repo", docs=True)
    if directory == ".claude":
        _deny_rules_removed(repo, commit=True)
    else:
        _git(repo, "rm", "-q", "-r", "docs")
        _git(repo, "commit", "-q", "-m", "drop docs")
    _without_writer_refusal(monkeypatch)
    reports = repo / directory
    published = _verify(repo, "--base", "main", "--out", str(reports))
    assert published.exit_code == 0, _plain(published.output)
    # What 1.0.0 decided: the deletion was hidden and the change mergeable.
    verifier = json.loads((reports / "verifier.json").read_text(encoding="utf-8"))
    assert (verifier["merge_verdict"], verifier["changed_files"]) == ("mergeable", [])
    # The run's own control read is already the refresh, and withholds it.
    envelope = json.loads(published.stdout)
    assert envelope["control_state"] != "complete", envelope
    assert envelope["permissions"]["merge"] is False, envelope

    result = _control(repo, reports)
    assert result.exit_code == 4, _plain(result.output)
    refusal = _refusal(result)
    assert "(workspace_unverifiable)" in refusal
    if directory == "docs":
        assert "removes (docs/a.txt)" in refusal
    else:
        assert "lies inside a trust root (agent_instructions)" in refusal


@pytest.mark.skipif(os.name == "nt", reason="Directory symlinks need privileges on Windows.")
def test_a_symlinked_spelling_is_refused_as_the_directory_it_reaches(tmp_path: Path):
    repo = _repo(tmp_path / "repo")
    _widen(repo)
    _git(repo, "commit", "-q", "-am", "widen shell permissions")
    into_repo = tmp_path / "link-to-repo"
    into_repo.symlink_to(repo, target_is_directory=True)
    into_claude = tmp_path / "link-to-claude"
    into_claude.symlink_to(repo / ".claude", target_is_directory=True)

    for spelling in (into_repo / ".claude", into_claude):
        result = _verify(repo, "--base", "main", "--out", str(spelling), env=AGENT_ENV)
        _assert_refused(
            result,
            shown=".claude",
            found="holds tracked repository files (.claude/settings.json)",
        )
    _nothing_written(repo / ".claude", {"settings.json"})


def test_a_case_variant_spelling_is_refused_where_the_filesystem_folds_case(tmp_path: Path):
    """Both the repository directory and a directory below it, spelled in another case."""

    repo = _repo(tmp_path / "repo")
    if not (tmp_path / "REPO").is_dir():
        pytest.skip("The temporary filesystem is case-sensitive.")
    _widen(repo)
    _git(repo, "commit", "-q", "-am", "widen shell permissions")

    parent_variant = _verify(
        repo, "--base", "main", "--out", str(tmp_path / "REPO" / ".claude"), env=AGENT_ENV
    )
    text = _plain(parent_variant.output)
    assert parent_variant.exit_code == 2, text
    assert "holds tracked repository files (.claude/settings.json)" in text, text

    # Git matches a pathspec byte for byte: `.CLAUDE` excluded nothing and listed
    # nothing, while the run wrote its reports into `.claude`.
    below_variant = _verify(
        repo, "--base", "main", "--out", str(repo / ".CLAUDE"), env=AGENT_ENV
    )
    _assert_refused(
        below_variant,
        shown=".CLAUDE",
        found="holds tracked repository files (.claude/settings.json)",
    )
    _nothing_written(repo / ".claude", {"settings.json"})

    # The same respelling makes a safe case-variant directory work: the
    # exclusion names `plain-reports`, where the run writes, rather than a
    # spelling Git matches nowhere, which read the run's own reports as changes.
    _git(repo, "checkout", "-q", "main")
    assert _verify(repo, "--out", str(repo / "plain-reports")).exit_code == 0
    again = _verify(repo, "--out", str(repo / "PLAIN-REPORTS"))
    assert again.exit_code == 0, _plain(again.output)
    assert json.loads(again.stdout)["control_state"] == "complete"
    refreshed = _control(repo, repo / "PLAIN-REPORTS")
    assert refreshed.exit_code == 0, _plain(refreshed.output)
    assert json.loads(refreshed.stdout)["control_state"] == "complete"


def _stage_skill(repo: Path) -> None:
    skill = repo / ".claude" / "skills" / "verification-inputs" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: verification-inputs\ndescription: Fetch and clean up.\n"
        "allowed-tools: Bash(curl:*), Bash(rm:*)\n---\n\nFetch, then clean up.\n",
        encoding="utf-8",
    )
    _git(repo, "add", str(skill.relative_to(repo)))


def _untracked_command(repo: Path) -> None:
    command = repo / ".claude" / "commands" / "packet.md"
    command.parent.mkdir(parents=True)
    command.write_text(
        "---\nallowed-tools: Bash(curl:*), Bash(rm -rf:*)\n---\n\nShip the packet.\n",
        encoding="utf-8",
    )


def _untracked_lock(repo: Path) -> None:
    lock = repo / ".agents-shipgate" / "capabilities.lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text('{"schema_version": "0.1", "tools": []}\n', encoding="utf-8")


@pytest.mark.parametrize(
    ("directory", "plant", "surface", "planted"),
    [
        (".claude/commands", _untracked_command, "agent_instructions", "packet.md"),
        (".claude/skills", _stage_skill, "agent_instructions", "verification-inputs"),
        (".agents-shipgate", _untracked_lock, "shipgate_state", "capabilities.lock.json"),
    ],
    ids=["untracked-command", "staged-skill", "untracked-lock"],
)
def test_a_trust_root_named_like_an_artifact_cannot_be_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory: str,
    plant,
    surface: str,
    planted: str,
):
    """An artifact's name is not an artifact where the path is a trust root.

    `packet.md`, `capabilities.lock.json` and anything under
    `verification-inputs/` are names a Shipgate run writes. Beneath `.claude`
    or `.agents-shipgate` the same names are a slash command, a skill and a
    Shipgate lock. Granting them the artifact allowance let `--out` over their
    directory turn a `human_review_required` change into `complete`.
    """

    repo = _repo(tmp_path / "repo")
    plant(repo)

    decided = _verify(repo, "--base", "main")
    assert decided.exit_code == 0, _plain(decided.output)
    assert json.loads(decided.stdout)["permissions"]["merge"] is False

    reports = repo / directory
    before = {path.name for path in reports.iterdir()}
    assert planted in before
    hidden = _verify(repo, "--base", "main", "--out", str(reports), env=AGENT_ENV)
    _assert_refused(
        hidden,
        shown=directory,
        found=(
            f"lies inside a trust root ({surface}) that Git does not ignore, where "
            "a file named like a Shipgate report is a trust-root file and not a report"
        ),
    )
    _nothing_written(reports, before)

    # A pointer 1.0.0 published there is refused on every read.
    _without_writer_refusal(monkeypatch)
    published = _verify(repo, "--base", "main", "--out", str(reports))
    assert published.exit_code == 0, _plain(published.output)
    envelope = json.loads(published.stdout)
    assert envelope["control_state"] != "complete", envelope
    assert envelope["permissions"]["merge"] is False, envelope
    result = _control(repo, reports)
    assert result.exit_code == 4, _plain(result.output)
    assert "(workspace_unverifiable)" in _refusal(result)


def test_a_gitignored_directory_inside_a_trust_root_hides_nothing(tmp_path: Path):
    """What Git ignores is in no change set, whatever trust root it sits in."""

    repo = _repo(tmp_path / "repo", ignore=f"{REPORTS}/\n.agents-shipgate/reports/\n")
    reports = repo / ".agents-shipgate" / "reports"
    for _ in range(2):
        verified = _verify(repo, "--out", str(reports))
        assert verified.exit_code == 0, _plain(verified.output)
        assert json.loads(verified.stdout)["control_state"] == "complete"
    refreshed = _control(repo, reports)
    assert refreshed.exit_code == 0, _plain(refreshed.output)
    assert json.loads(refreshed.stdout)["control_state"] == "complete"


def test_the_recovery_keeps_the_producing_runs_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Following the printed recovery reaches the same decision, not `complete`.

    The recovery is the producing run's own command with only `--out` removed.
    Rebuilding a bare `verify --workspace <repo>` dropped `--base main` and
    `--config`; with no remote the run skipped the local base, and a committed
    `.claude` widening read `complete` with `merge: true`.
    """

    repo = _repo(tmp_path / "repo", docs=True)
    _widen(repo)
    _git(repo, "commit", "-q", "-am", "widen shell permissions")
    reports = repo / "docs"
    with monkeypatch.context() as legacy:
        legacy.setattr(
            "agents_shipgate.cli.verify.orchestrator._reject_output_directory_content",
            lambda **_: None,
        )
        published = _verify(repo, "--base", "main", "--out", str(reports))
    assert published.exit_code == 0, _plain(published.output)
    verifier = json.loads((reports / "verifier.json").read_text(encoding="utf-8"))
    assert verifier["merge_verdict"] == "human_review_required"

    refused = _control(repo, reports, env=AGENT_ENV)
    assert refused.exit_code == 4, _plain(refused.output)
    action = _error_line(refused.output)["next_actions"][0]
    assert action["kind"] == "command", action
    args = action["args"]
    assert args[0] == "verify", action
    assert "--out" not in args, action
    assert args[args.index("--config") + 1] == "shipgate.yaml", action
    assert args[args.index("--base") + 1] == "main", action
    assert Path(args[args.index("--workspace") + 1]).resolve() == repo.resolve(), action

    followed = runner.invoke(app, args, env=ENV)
    assert followed.exit_code == 0, _plain(followed.output)
    rerun = json.loads((repo / REPORTS / "verifier.json").read_text(encoding="utf-8"))
    assert rerun["merge_verdict"] == "human_review_required", rerun["merge_verdict"]
    assert ".claude/settings.json" in rerun["changed_files"]
    refreshed = _control(repo)
    assert refreshed.exit_code == 0, _plain(refreshed.output)
    envelope = json.loads(refreshed.stdout)
    assert envelope["control_state"] != "complete", envelope
    assert envelope["permissions"]["merge"] is False, envelope


def test_a_head_run_may_remove_committed_files_from_its_output_directory(tmp_path: Path):
    """The Action's `--head` diff shows the removal, so nothing is hidden.

    The change that stops committing reports removes them from the directory
    it writes into. An archived-head run diffs the merge base against the head
    in full, so that removal is in its own change set; only the head tree it
    reads excludes the directory. A worktree run excludes the directory from
    its diff, so there the removal would be hidden and is still refused.
    """

    repo = _repo(tmp_path / "repo", ignore="")
    _git(repo, "checkout", "-q", "main")
    (repo / REPORTS).mkdir()
    (repo / REPORTS / "report.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "commit reports")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "main")
    _git(repo, "rm", "-q", "-r", REPORTS)
    (repo / ".gitignore").write_text(f"{REPORTS}/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "stop committing reports")

    head = _verify(repo, "--base", "main", "--head", "HEAD", "--out", str(repo / REPORTS))
    assert head.exit_code == 0, _plain(head.output)
    verifier = json.loads((repo / REPORTS / "verifier.json").read_text(encoding="utf-8"))
    assert verifier["changed_files"] == [".gitignore", f"{REPORTS}/report.json"]
    refreshed = _control(repo, repo / REPORTS)
    assert refreshed.exit_code == 0, _plain(refreshed.output)

    shutil.rmtree(repo / REPORTS)
    worktree = _verify(repo, "--base", "main", env=AGENT_ENV)
    text = _plain(worktree.output)
    assert worktree.exit_code == 2, text
    assert (
        f"held committed repository files that the change being verified removes "
        f"({REPORTS}/report.json)"
    ) in text, text
    assert f"git rm -r --cached {REPORTS}" in text, text


def _samples_with_a_root_manifest() -> list[str]:
    return sorted(
        sample.name
        for sample in (REPO_ROOT / "samples").iterdir()
        if (sample / "shipgate.yaml").is_file()
    )


@pytest.mark.parametrize("sample", _samples_with_a_root_manifest())
def test_every_file_a_real_run_leaves_is_a_recognized_artifact(tmp_path: Path, sample: str):
    """The allowlist, held to what runs actually write rather than to source text.

    An unignored default reports directory, which is what a repository without
    `init`'s `.gitignore` entry has. A name a run writes that the allowlist
    missed (`suggested-inventory.json`, beside a low-confidence scan) made the
    next `agent control` refuse and the next `verify` exit 2, and the Stop hook
    treats that exit as advisory.
    """

    from agents_shipgate.cli.current_workspace import (
        _artifact_shaped,
        _trust_root_class,
        classify_output_directory,
    )

    repo = tmp_path / sample
    shutil.copytree(
        REPO_ROOT / "samples" / sample,
        repo,
        ignore=shutil.ignore_patterns("expected", REPORTS, "__pycache__"),
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")

    for _ in range(2):
        verified = runner.invoke(
            app,
            ["verify", "--workspace", str(repo), "--no-base", "--format", "control"],
            env=ENV,
        )
        assert verified.exit_code == 0, _plain(verified.output)
    produced = sorted(
        path.relative_to(repo).as_posix()
        for path in (repo / REPORTS).rglob("*")
        if path.is_file()
    )
    assert produced
    unrecognized = [
        path
        for path in produced
        if not _artifact_shaped(REPORTS, path) or _trust_root_class(path) is not None
    ]
    assert unrecognized == []
    assert classify_output_directory(repo, repo / REPORTS) is None
    refreshed = _control(repo)
    assert refreshed.exit_code == 0, _plain(refreshed.output)


def test_skill_reports_in_the_default_directory_are_recognized_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`skill lint`, `security` and `review` publish beside `verify`'s reports.

    With `--out` omitted they write `skill-<command>.{md,json,sarif}` into
    `agents-shipgate-reports` under the workspace. In a repository that does
    not gitignore that directory, names missing from the allowlist made the
    next `agent control` refuse as `workspace_unverifiable` and the next
    `verify` exit 2, as though a person had left notes there.
    """

    from agents_shipgate.cli.current_workspace import classify_output_directory

    repo = _repo(tmp_path / "repo", ignore="")
    first = _verify(repo)
    assert first.exit_code == 0, _plain(first.output)
    assert json.loads(first.stdout)["control_state"] == "complete"
    before = _control(repo)
    assert before.exit_code == 0, _plain(before.output)

    # The skill commands take their workspace from the current directory.
    monkeypatch.chdir(repo)
    commands = ("lint", "security", "review")
    reviewed = runner.invoke(app, ["skill", "review"], env=ENV)
    assert reviewed.exit_code == 0, _plain(reviewed.output)
    for command in commands:
        skill = runner.invoke(
            app, ["skill", command, "--format", "markdown,json,sarif"], env=ENV
        )
        assert skill.exit_code == 0, _plain(skill.output)
    written = sorted(
        path.name for path in (repo / REPORTS).iterdir() if path.name.startswith("skill-")
    )
    assert written == sorted(
        f"skill-{command}.{suffix}" for command in commands for suffix in ("json", "md", "sarif")
    )
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "--", REPORTS)
    assert f"{REPORTS}/skill-review.json" in untracked.splitlines()

    refreshed = _control(repo)
    assert refreshed.exit_code == 0, _plain(refreshed.output)
    assert json.loads(refreshed.stdout)["control_state"] == "complete"
    second = _verify(repo)
    assert second.exit_code == 0, _plain(second.output)
    assert json.loads(second.stdout)["control_state"] == "complete"
    again = _control(repo)
    assert again.exit_code == 0, _plain(again.output)
    assert json.loads(again.stdout)["control_state"] == "complete"
    assert classify_output_directory(repo, repo / REPORTS) is None


# -- what keeps working ------------------------------------------------------


@pytest.mark.parametrize(
    "where", ["default", "gitignored", "artifacts_only", "outside"]
)
def test_directories_that_hide_nothing_keep_working(tmp_path: Path, where: str):
    """Each run twice, with every renderer's output already present the second time."""

    repo = _repo(tmp_path / "repo", ignore=f"{REPORTS}/\nignored-reports/\n")
    reports = {
        "default": repo / REPORTS,
        "gitignored": repo / "ignored-reports",
        # Untracked and unignored: allowed because it holds only Shipgate artifacts.
        "artifacts_only": repo / "plain-reports",
        "outside": tmp_path / "sibling-reports",
    }[where]
    out = [] if where == "default" else ["--out", str(reports)]

    first = _verify(repo, *out)
    assert first.exit_code == 0, _plain(first.output)
    assert json.loads(first.stdout)["control_state"] == "complete"
    scanned = runner.invoke(
        app,
        ["scan", "--config", str(repo / "shipgate.yaml"), "--out", str(reports),
         "--format", "markdown,json,sarif", "--packet-format", "md,json,html"],
        env=ENV,
    )
    assert scanned.exit_code == 0, _plain(scanned.output)
    second = _verify(repo, *out)
    assert second.exit_code == 0, _plain(second.output)
    assert json.loads(second.stdout)["control_state"] == "complete"

    refreshed = _control(repo, None if where == "default" else reports)
    assert refreshed.exit_code == 0, _plain(refreshed.output)
    assert json.loads(refreshed.stdout)["control_state"] == "complete"

    (repo / "tools.json").write_text(
        (repo / "tools.json").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    drifted = _control(repo, None if where == "default" else reports)
    assert drifted.exit_code == 4, _plain(drifted.output)
    assert "(workspace_changed)" in _refusal(drifted)


def test_a_head_decision_is_unchanged(tmp_path: Path):
    """CI's archived-head route reads the same decision into any safe directory."""

    repo = _repo(tmp_path / "repo", ignore=f"{REPORTS}/\nci-reports/\n")
    _widen(repo)
    _git(repo, "commit", "-q", "-am", "widen shell permissions")

    outcomes = {}
    for label, out in (("default", []), ("gitignored", ["--out", str(repo / "ci-reports")])):
        result = _verify(repo, "--base", "main", "--head", "HEAD", *out)
        assert result.exit_code == 0, _plain(result.output)
        reports = repo / (REPORTS if label == "default" else "ci-reports")
        verifier = json.loads((reports / "verifier.json").read_text(encoding="utf-8"))
        outcomes[label] = (
            json.loads(result.stdout)["control_state"],
            verifier["merge_verdict"],
            verifier["changed_files"],
        )
    assert outcomes["default"] == outcomes["gitignored"]
    assert outcomes["default"][1:] == ("human_review_required", [".claude/settings.json"])


def test_preview_outside_git_still_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Outside Git there is no change set to leave anything out of."""

    workspace = tmp_path / "fresh"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "notes" / "a.md").write_text("a\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    for out in ([], ["--out", "notes"]):
        previewed = runner.invoke(
            app, ["verify", "--workspace", ".", "--preview", *out, "--format", "control"],
            env=ENV,
        )
        assert previewed.exit_code == 0, _plain(previewed.output)
        refreshed = _control(Path("."), Path(out[1]) if out else None)
        assert refreshed.exit_code == 0, _plain(refreshed.output)


# -- the reader --------------------------------------------------------------


def _without_writer_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish the way 1.0.0 did, so a pointer lands in such a directory."""

    from agents_shipgate.cli.verify import orchestrator

    monkeypatch.setattr(orchestrator, "_reject_output_directory_content", lambda **_: None)


def _publish_plan_less(reports: Path, repo: Path, control) -> None:
    """A plan-less worktree pointer, the preview shape, in ``state``."""

    from agents_shipgate.cli.verify.git import commit_sha, repository_identity, tree_sha
    from agents_shipgate.core.current_control import publish_current_control
    from agents_shipgate.schemas.current_control import CurrentControlWorkspaceIdentity

    publish_current_control(
        reports,
        operation="preview",
        control=control,
        workspace_identity=CurrentControlWorkspaceIdentity(
            repository=repository_identity(repo),
            head_ref="HEAD",
            head_commit_sha=commit_sha(repo, "HEAD"),
            head_tree_sha=tree_sha(repo, "HEAD"),
            snapshot_kind="worktree_overlay",
            worktree_overlay_sha256=None,
        ),
        artifact_keys={"verifier"},
    )


def test_a_pointer_already_in_such_a_directory_refuses_in_every_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Pointers 1.0.0 published into a tracked directory go non-current on upgrade.

    Each one is shown to be current when the directory is not classified, so
    the refusal is this rule and not some other drift in the fixture.
    """

    from agents_shipgate.cli import current_workspace
    from agents_shipgate.cli.current_workspace import live_workspace
    from agents_shipgate.core.current_control import (
        CurrentControlUnavailable,
        read_current_control,
    )
    from agents_shipgate.schemas.agent_control import PublishOnlyPermissions
    from agents_shipgate.schemas.current_control import (
        AgentActionRequiredCurrentControl,
        HumanReviewRequiredCurrentControl,
        ReviewPublishableCurrentControl,
    )

    _without_writer_refusal(monkeypatch)
    pointers: dict[str, tuple[Path, Path]] = {}

    complete = _repo(tmp_path / "complete", docs=True)
    assert _verify(complete, "--out", str(complete / "docs")).exit_code == 0
    pointers["complete"] = (complete, complete / "docs")

    publishable = _repo(tmp_path / "publishable", docs=True)
    _widen(publishable)
    assert _verify(publishable, "--base", "main", "--out", str(publishable / "docs")).exit_code == 0
    pointers["review_publishable (plan-bound)"] = (publishable, publishable / "docs")

    for state, control in (
        ("agent_action_required", AgentActionRequiredCurrentControl(
            state="agent_action_required", reason="A preview reached no decision.")),
        ("human_review_required", HumanReviewRequiredCurrentControl(
            state="human_review_required", reason="A person must review this.")),
        ("review_publishable", ReviewPublishableCurrentControl(
            state="review_publishable", reason="Publishable for review.",
            permissions=PublishOnlyPermissions())),
    ):
        repo = _repo(tmp_path / state, docs=True)
        shutil.copy(complete / "docs" / "verifier.json", repo / "docs" / "verifier.json")
        _publish_plan_less(repo / "docs", repo, control)
        pointers[f"{state} (plan-less)"] = (repo, repo / "docs")

    def read(repo: Path, reports: Path):
        return read_current_control(reports, live=lambda: live_workspace(repo, reports))

    with monkeypatch.context() as unclassified:
        unclassified.setattr(
            current_workspace, "output_directory_refusal", lambda *_, **__: None
        )
        for label, (repo, reports) in pointers.items():
            state = read(repo, reports).pointer.control.state
            assert label.startswith(state), (label, state)

    refused = {}
    for label, (repo, reports) in pointers.items():
        with pytest.raises(CurrentControlUnavailable) as failure:
            read(repo, reports)
        refused[label] = failure.value.reason
        assert f"The reports directory {reports}" in str(failure.value)
        assert "holds tracked repository files (docs/a.txt)" in str(failure.value)
    assert set(refused.values()) == {"workspace_unverifiable"}, refused

    # And through the CLI refresh an agent actually runs, on the pointer that
    # authorized a merge.
    repo, reports = pointers["complete"]
    result = _control(repo, reports, env=AGENT_ENV)
    assert result.exit_code == 4, _plain(result.output)
    assert "(workspace_unverifiable)" in _refusal(result)
    line = _error_line(result.output)
    assert "holds tracked repository files" in line["message"]
    # The producing run's own command would publish back into `docs` and be
    # refused before writing, so the recovery goes to the default directory.
    action = line["next_actions"][0]
    assert action["kind"] == "command", action
    assert action["args"][:3] == ["verify", "--workspace", str(repo)], action
    assert "--out" not in action["args"], action
    assert str(repo / REPORTS / "current-control.json") in action["why"], action


def _never_omit_out(text: str) -> None:
    """Advice for the default directory must not route back into it."""

    lowered = text.lower()
    assert "omit --out" not in lowered, text
    assert "without --out" not in lowered, text


@pytest.mark.parametrize("route", ["worktree", "action"])
def test_a_default_directory_holding_committed_files_is_refused_without_a_looping_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
):
    """Committed files in the default directory: the same rule, no route back into it.

    The Action's shape names the default directory explicitly
    (`--out agents-shipgate-reports --head HEAD`); it is still the default
    directory, decided by what the directory is and not by whether `--out` was
    passed.
    """

    repo = _repo(tmp_path / "repo", ignore="")
    (repo / REPORTS).mkdir()
    (repo / REPORTS / "README.md").write_text("reports live here\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "commit a reports readme")
    extra = [] if route == "worktree" else ["--out", str(repo / REPORTS), "--head", "HEAD"]

    refused = _verify(repo, *extra, env=AGENT_ENV)
    text = _plain(refused.output)
    assert refused.exit_code == 2, text
    subject = (
        f"The default verifier output directory {REPORTS}"
        if route == "worktree"
        else f"Verifier --out {REPORTS}"
    )
    assert f"{subject} holds tracked repository files ({REPORTS}/README.md)" in text, text
    assert "Pass --out naming a directory that is gitignored or outside the repository" in text
    assert f"git rm -r --cached {REPORTS}" in text, text
    _never_omit_out(text)
    action = _error_line(refused.output)["next_actions"][0]
    assert action["kind"] == "review", action
    assert action["why"].startswith("The default output directory "), action
    _never_omit_out(action["why"])
    _nothing_written(repo / REPORTS, {"README.md"})

    # The way out it names works.
    elsewhere = _verify(repo, *extra[2:], "--out", str(tmp_path / "outside"))
    assert elsewhere.exit_code == 0, _plain(elsewhere.output)

    _without_writer_refusal(monkeypatch)
    assert _verify(repo).exit_code == 0
    spellings = [None]
    if (repo / REPORTS.upper()).is_dir():
        # A case-variant spelling of the default is still the default.
        spellings.append(repo / REPORTS.upper())
    for spelling in spellings:
        result = _control(repo, spelling, env=AGENT_ENV)
        assert result.exit_code == 4, _plain(result.output)
        assert "(workspace_unverifiable)" in _refusal(result)
        action = _error_line(result.output)["next_actions"][0]
        assert action["kind"] == "review", (spelling, action)
        assert "Pass --out naming a directory that is gitignored or outside the repository" in (
            action["why"]
        )
        _never_omit_out(action["why"])


def test_a_default_directory_holding_stray_files_names_moving_them_out(tmp_path: Path):
    """Untracked notes beside the reports: moving them out is a way out there."""

    repo = _repo(tmp_path / "repo", ignore="")
    (repo / REPORTS).mkdir()
    (repo / REPORTS / "notes.md").write_text("notes\n", encoding="utf-8")

    refused = _verify(repo, env=AGENT_ENV)
    text = _plain(refused.output)
    assert refused.exit_code == 2, text
    assert (
        f"Move the files that are not Shipgate artifacts out of {REPORTS} "
        f"(unstaging any that are staged), gitignore {REPORTS}, or pass --out"
    ) in text, text
    _never_omit_out(text)

    (repo / REPORTS / "notes.md").unlink()
    assert _verify(repo).exit_code == 0
    assert _control(repo).exit_code == 0


def test_a_directory_that_gains_content_after_the_run_refuses(tmp_path: Path):
    """Safe when written, unsafe when read: the reader classifies it again."""

    repo = _repo(tmp_path / "repo", claude=False)
    reports = repo / "notes"
    verified = _verify(repo, "--base", "main", "--out", str(reports))
    assert verified.exit_code == 0, _plain(verified.output)
    assert _control(repo, reports).exit_code == 0

    (reports / "todo.md").write_text("ship it\n", encoding="utf-8")

    result = _control(repo, reports)
    assert result.exit_code == 4, _plain(result.output)
    refusal = _refusal(result)
    assert "(workspace_unverifiable)" in refusal
    assert "notes/todo.md" in refusal


def test_a_classification_that_fails_refuses_instead_of_skipping_the_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An exception must not become "no live workspace".

    ``live_workspace`` returning ``None`` withholds completion only; every other
    state is returned without the currency checks. So a classifier that could
    not run has to refuse on its own, for a non-completion pointer too.
    """

    from agents_shipgate.cli.verify import git as verify_git

    repo = _repo(tmp_path / "repo")
    _widen(repo)
    decided = _verify(repo, "--base", "main")
    assert decided.exit_code == 0, _plain(decided.output)
    assert json.loads(decided.stdout)["control_state"] == "review_publishable"
    assert _control(repo).exit_code == 0

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("git ls-files is unavailable")

    monkeypatch.setattr(verify_git, "output_directory_inventory", unavailable)

    result = _control(repo)
    assert result.exit_code == 4, _plain(result.output)
    refusal = _refusal(result)
    assert "(workspace_unverifiable)" in refusal
    assert "could not be shown to hold only Shipgate artifacts" in refusal

    rerun = _verify(repo, "--base", "main", env=AGENT_ENV)
    assert rerun.exit_code == 2, _plain(rerun.output)
    assert "could not be shown to hold only Shipgate artifacts" in _plain(rerun.output)


# -- the classifier ----------------------------------------------------------


def test_the_classifier_names_what_leaving_the_directory_out_would_hide(tmp_path: Path):
    from agents_shipgate.cli.current_workspace import output_directory_refusal

    repo = _repo(tmp_path / "repo", docs=True, ignore=f"{REPORTS}/\nignored/\n")

    # Nothing to hide: outside, absent, ignored, or only artifacts.
    assert output_directory_refusal(repo, tmp_path / "sibling") is None
    assert output_directory_refusal(repo, repo / "not-there-yet") is None
    (repo / "ignored").mkdir()
    (repo / "ignored" / "settings.json").write_text("{}\n", encoding="utf-8")
    assert output_directory_refusal(repo, repo / "ignored") is None
    artifacts = repo / "rpt"
    (artifacts / "verification-inputs" / "policy_packs").mkdir(parents=True)
    for name in ("current-control.json", "report.json", "verifier.json"):
        (artifacts / name).write_text("{}\n", encoding="utf-8")
    (artifacts / "verification-inputs" / "policy_packs" / "abc-policy.yaml").write_text(
        "x: 1\n", encoding="utf-8"
    )
    (artifacts / ".current-control.json.x1y2.tmp").write_text("{}\n", encoding="utf-8")
    assert output_directory_refusal(repo, artifacts) is None

    # Something to hide.
    assert output_directory_refusal(repo, repo / "docs") == (
        "holds tracked repository files (docs/a.txt)"
    )
    (artifacts / "nested").mkdir()
    (artifacts / "nested" / "report.json").write_text("{}\n", encoding="utf-8")
    assert output_directory_refusal(repo, artifacts) == (
        "holds untracked files that Git does not ignore and that are not Shipgate "
        "artifacts (rpt/nested/report.json)"
    )
    shutil.rmtree(artifacts / "nested")
    # Generated reports staged by mistake are an advisory case `verify` warns
    # about, not content; a staged file that is not an artifact is content.
    _git(repo, "add", "-f", "rpt/report.json")
    assert output_directory_refusal(repo, artifacts) is None
    (artifacts / "notes.md").write_text("notes\n", encoding="utf-8")
    _git(repo, "add", "-f", "rpt/notes.md")
    assert output_directory_refusal(repo, artifacts) == (
        "holds tracked repository files (rpt/notes.md)"
    )
    _git(repo, "rm", "-q", "--cached", "rpt/notes.md")
    (artifacts / "notes.md").unlink()
    # Committed, an artifact's name is still repository content: the run
    # would overwrite it and hide the edit.
    _git(repo, "commit", "-q", "-m", "commit a report")
    assert output_directory_refusal(repo, artifacts) == (
        "holds tracked repository files (rpt/report.json)"
    )
    # A committed file deleted from the index and the disk is a staged removal.
    _git(repo, "rm", "-q", "docs/a.txt")
    assert not (repo / "docs").exists()
    assert output_directory_refusal(repo, repo / "docs") == (
        "held committed repository files that the change being verified removes "
        "(docs/a.txt)"
    )
    # Once committed, the deletion is only visible against the commit before it.
    _git(repo, "commit", "-q", "-m", "drop docs")
    assert output_directory_refusal(repo, repo / "docs") is None
    assert output_directory_refusal(repo, repo / "docs", compared=("HEAD~1",)) == (
        "held committed repository files that the change being verified removes "
        "(docs/a.txt)"
    )
    # A compared ref that does not resolve compares nothing.
    assert output_directory_refusal(repo, repo / "docs", compared=("no-such-ref",)) is None
    # A trust root is judged by more than names. A directory inside one that
    # Git does not ignore is refused before anything is there, because every
    # report written into it would be a trust-root file; one Git ignores
    # hides nothing. A trust-root path named like an artifact is not one.
    assert output_directory_refusal(repo, repo / ".claude" / "reports") == (
        "lies inside a trust root (agent_instructions) that Git does not ignore, "
        "where a file named like a Shipgate report is a trust-root file and not "
        "a report"
    )
    # `suggested-declarations.yaml` in `.github/workflows` would be a workflow.
    assert output_directory_refusal(repo, repo / ".github" / "workflows") == (
        "lies inside a trust root (host_boundary) that Git does not ignore, "
        "where a file named like a Shipgate report is a trust-root file and not "
        "a report"
    )
    (repo / ".gitignore").write_text(
        f"{REPORTS}/\nignored/\n.agents-shipgate/reports/\n", encoding="utf-8"
    )
    assert output_directory_refusal(repo, repo / ".agents-shipgate" / "reports") is None
    disguised = repo / "rpt2"
    (disguised / "verification-inputs" / ".claude").mkdir(parents=True)
    (disguised / "current-control.json").write_text("{}\n", encoding="utf-8")
    (disguised / "verification-inputs" / ".claude" / "settings.json").write_text(
        "{}\n", encoding="utf-8"
    )
    assert output_directory_refusal(repo, disguised) == (
        "holds trust-root files named like Shipgate artifacts "
        "(rpt2/verification-inputs/.claude/settings.json)"
    )
    # The repository itself, and its ancestors.
    for whole in (repo, tmp_path):
        assert output_directory_refusal(repo, whole) == (
            "is the repository root or one of its ancestors"
        )


def test_every_name_a_run_writes_is_a_recognized_artifact():
    """The allowlist restates registries; hold it to each of them.

    A name missing here makes the second run into an unignored reports
    directory refuse, and every refresh of it too. Some writers put a file
    beside a report rather than into a named output directory, and the GitHub
    Action writes into its `output_dir` from scripts and shell steps, and the
    `skill` commands name theirs from a basename table; each is read here. `test_every_file_a_real_run_leaves_is_a_recognized_artifact`
    holds the allowlist to what runs actually leave behind.
    """

    from agents_shipgate.ci import release_decision
    from agents_shipgate.cli import evidence_packet, scenario
    from agents_shipgate.cli._artifact_lifecycle import (
        REPORTS_DIRECTORY_ARTIFACT_NAMES,
        VERIFIER_ROUTE_ARTIFACT_NAMES,
    )
    from agents_shipgate.cli.scan import output_helpers
    from agents_shipgate.cli.verify import orchestrator
    from agents_shipgate.core.current_control import CURRENT_CONTROL_ARTIFACT_FILENAMES
    from agents_shipgate.schemas.contract import ARTIFACTS
    from agents_shipgate.schemas.current_control import CURRENT_CONTROL_ARTIFACT_NAME
    from agents_shipgate.schemas.declaration_continuation import (
        DECLARATION_CONTINUATION_ARTIFACT_NAME,
    )
    from agents_shipgate.skill import runner as skill_runner

    expected = {
        *VERIFIER_ROUTE_ARTIFACT_NAMES,
        *CURRENT_CONTROL_ARTIFACT_FILENAMES.values(),
        CURRENT_CONTROL_ARTIFACT_NAME,
        DECLARATION_CONTINUATION_ARTIFACT_NAME,
        release_decision.SUGGESTED_INVENTORY_FILENAME,
        release_decision.SUGGESTED_DECLARATIONS_FILENAME,
        *(
            PurePosixPath(path).name
            for path in ARTIFACTS.values()
            if path.startswith(f"{REPORTS}/")
        ),
    }
    written_literally = re.compile(r'(?:out_dir|output_dir) / "([^"/]+)"')
    for module in (orchestrator, output_helpers, evidence_packet):
        expected.update(written_literally.findall(inspect.getsource(module)))
    # Beside the report a command reads.
    expected.update(re.findall(r'from_path\.parent / "([^"/]+)"', inspect.getsource(scenario)))
    # The Action: its output scripts, and its shell and github-script steps.
    for script in ("github_action_outputs.py", "github_action_annotations.py",
                   "github_check_run.py"):
        source = (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8")
        expected.update(written_literally.findall(source))
        expected.update(re.findall(r'^PAYLOAD_FILENAME = "([^"/]+)"$', source, flags=re.M))
    action = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
    expected.update(re.findall(r'\$\{OUTPUT_DIR\}/([^"/\s]+)"', action))
    expected.update(re.findall(r'path\.join\(outputDir, "([^"/]+)"\)', action))
    assert {
        "suggested-inventory.json",
        "suggested-scenarios.yaml",
        "check-annotations.json",
        "check-run-payload.json",
    } <= expected
    removed = inspect.getsource(orchestrator._remove_scan_artifacts)
    expected.update(re.findall(r'"([^"/]+\.(?:json|md|sarif|html|pdf))"', removed))
    for constant in re.findall(r"^\s+([A-Z_]+_FILENAME),$", removed, flags=re.MULTILINE):
        expected.add(getattr(orchestrator, constant))
    # `skill lint`, `skill security` and `skill review` publish into the same
    # default directory when `--out` is omitted, one basename per command in
    # every format `_write_reports` renders.
    assert f'workspace / "{REPORTS}"' in inspect.getsource(skill_runner.run_skill_review)
    skill_suffixes = re.findall(
        r'\("[a-z]+", "([a-z]+)"\)', inspect.getsource(skill_runner._write_reports)
    )
    assert sorted(skill_suffixes) == ["json", "md", "sarif"]
    expected.update(
        f"{basename}.{suffix}"
        for basename in skill_runner.REPORT_BASENAME.values()
        for suffix in skill_suffixes
    )

    assert expected - REPORTS_DIRECTORY_ARTIFACT_NAMES == set()
