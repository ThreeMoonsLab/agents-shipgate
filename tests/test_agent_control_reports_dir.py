"""Where `agents-shipgate agent control` looks for `current-control.json` (#575).

`verify --workspace <repo>` publishes under `<repo>/agents-shipgate-reports`
wherever it is run from. `agent control --workspace <repo>` used to look for its
default reports directory under the *caller's* current directory instead, so a
valid run appeared `missing` — or, when the caller stood in another verified
repository, that repository's pointer was read and checked against this one.

The rules pinned here:

* With no `--reports-dir`, the directory is the one a default `verify
  --workspace` writes: `agents-shipgate-reports` under the workspace.
* An explicit `--reports-dir` keeps its existing meaning: absolute as given,
  relative against the current directory.
* A refusal names the directory that was actually searched, and the recovery
  command writes back to that same directory from any current directory.
* Reports verified into a directory outside the repository are read, not
  refused as an unobservable workspace (#627 case recorded on #575).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.orchestrator import run_verify

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
REPORTS = "agents-shipgate-reports"
# Locked text environment: no colour, no CI annotations, no terminal wrapping.
ENV = {"NO_COLOR": "1", "GITHUB_ACTIONS": None, "FORCE_COLOR": None, "COLUMNS": "200"}
AGENT_ENV = {**ENV, "AGENTS_SHIPGATE_AGENT_MODE": "1"}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

runner = CliRunner()


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _committed_sample(path: Path) -> Path:
    """The clean read-only sample as a one-commit repository."""

    path.mkdir(parents=True)
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copy(SAMPLE / name, path / name)
    (path / ".gitignore").write_text(f"{REPORTS}/\n", encoding="utf-8")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.test")
    _git(path, "config", "user.name", "Test User")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "fixture")
    return path


def _cli_verify(workspace: Path) -> None:
    """`verify --workspace` with its default output, from wherever the test stands."""

    result = runner.invoke(
        app,
        [
            "verify", "--workspace", str(workspace), "--config", "shipgate.yaml",
            "--head", "HEAD", "--format", "control",
        ],
        env=ENV,
    )
    assert result.exit_code == 0, _plain(result.output)
    assert json.loads(result.stdout)["control_state"] == "complete", result.stdout


def _pointer_id(reports: Path) -> str:
    return json.loads((reports / "current-control.json").read_text(encoding="utf-8"))[
        "current_control_id"
    ]


def _control(*args: str, env: dict[str, str | None] = ENV):
    return runner.invoke(app, ["agent", "control", *args], env=env)


def _error_line(output: str) -> dict:
    lines = [line for line in _plain(output).splitlines() if line.startswith('{"error"')]
    assert lines, f"no agent-mode error line in:\n{output}"
    return json.loads(lines[-1])


def _option(command: str, flag: str) -> str:
    argv = shlex.split(command)
    return argv[argv.index(flag) + 1]


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A verified repository, and a caller standing in a *different* verified one.

    The caller's own reports are real and valid, so a reader that resolves the
    default against the current directory finds a passing pointer — just not
    the one it was asked about.
    """

    repo = _committed_sample(tmp_path / "repo")
    caller = _committed_sample(tmp_path / "caller")
    monkeypatch.chdir(caller)
    _cli_verify(repo)
    _cli_verify(caller)
    assert (repo / REPORTS / "current-control.json").is_file()
    assert (caller / REPORTS / "current-control.json").is_file()
    assert _pointer_id(repo / REPORTS) != _pointer_id(caller / REPORTS)
    return {"tmp": tmp_path, "repo": repo, "caller": caller}


def test_default_reports_dir_follows_the_workspace_from_another_cwd(layout):
    repo = layout["repo"]

    result = _control("--workspace", str(repo))

    assert result.exit_code == 0, _plain(result.output)
    payload = json.loads(result.stdout)
    assert payload["control_state"] == "complete"
    # The requested repository's generation, never the caller's own.
    assert payload["current_control_id"] == _pointer_id(repo / REPORTS)
    assert payload["current_control_id"] != _pointer_id(layout["caller"] / REPORTS)
    # Every artifact path the envelope prints opens from the caller's directory.
    for ref in payload["artifacts"].values():
        path = Path(ref["path"])
        assert path.is_absolute(), ref
        assert path.is_relative_to(repo / REPORTS), ref
        assert path.is_file(), ref


def test_default_reports_dir_is_unchanged_when_run_inside_the_workspace(
    layout, monkeypatch: pytest.MonkeyPatch
):
    """`--workspace .` from the repository keeps its relative artifact spelling."""

    monkeypatch.chdir(layout["repo"])

    result = _control("--workspace", ".")

    assert result.exit_code == 0, _plain(result.output)
    payload = json.loads(result.stdout)
    assert payload["current_control_id"] == _pointer_id(layout["repo"] / REPORTS)
    paths = [ref["path"] for ref in payload["artifacts"].values()]
    assert paths and all(path.startswith(f"{REPORTS}/") for path in paths), paths


def test_a_missing_default_names_the_searched_path_and_never_uses_the_callers(layout):
    """The caller's valid reports cannot stand in for an unverified workspace."""

    unverified = _committed_sample(layout["tmp"] / "unverified")
    searched = unverified.resolve() / REPORTS

    result = _control("--workspace", str(unverified), env=AGENT_ENV)

    assert result.exit_code == 3, _plain(result.output)
    text = _plain(result.output)
    assert "Current control is unavailable (missing)" in text
    assert str(searched) in text
    assert str(layout["caller"] / REPORTS) not in text

    line = _error_line(result.output)
    assert line["exit_code"] == 3
    assert str(searched) in line["message"]
    command = line["next_actions"][0]["command"]
    # The recovery writes exactly the directory that was searched, and it says
    # so absolutely: `verify` resolves a relative `--out` against the Git root,
    # not against the caller, so a relative spelling would name somewhere else.
    assert Path(_option(command, "--out")) == searched
    assert Path(_option(command, "--workspace")) == unverified


def test_an_explicit_relative_reports_dir_still_resolves_against_the_cwd(
    layout, monkeypatch: pytest.MonkeyPatch
):
    """Explicit spellings keep their meaning; only the omitted default moved."""

    repo, tmp = layout["repo"], layout["tmp"]

    # From the repository's parent the relative spelling names the repo's reports.
    monkeypatch.chdir(tmp)
    found = _control("--workspace", "repo", "--reports-dir", f"repo/{REPORTS}")
    assert found.exit_code == 0, _plain(found.output)
    payload = json.loads(found.stdout)
    assert payload["current_control_id"] == _pointer_id(repo / REPORTS)
    paths = [ref["path"] for ref in payload["artifacts"].values()]
    assert paths and all(path.startswith(f"repo/{REPORTS}/") for path in paths), paths

    # From the caller the same relative spelling is the caller's directory, not
    # the workspace's and not the Git root's: it is refused, not retargeted.
    monkeypatch.chdir(layout["caller"])
    missing = _control(
        "--workspace", str(repo), "--reports-dir", f"repo/{REPORTS}", env=AGENT_ENV
    )
    assert missing.exit_code == 3, _plain(missing.output)
    searched = Path.cwd() / "repo" / REPORTS
    assert str(searched) in _plain(missing.output)
    command = _error_line(missing.output)["next_actions"][0]["command"]
    assert Path(_option(command, "--out")) == searched

    # A bare relative `agents-shipgate-reports` means the caller's own reports,
    # exactly as before — those are checked against the requested workspace
    # and refused, never accepted on its behalf.
    explicit_default = _control("--workspace", str(repo), "--reports-dir", REPORTS)
    assert explicit_default.exit_code != 0, _plain(explicit_default.output)


def test_an_explicit_absolute_reports_dir_is_used_as_given(layout):
    repo = layout["repo"]

    result = _control("--workspace", str(repo), "--reports-dir", str(repo / REPORTS))

    assert result.exit_code == 0, _plain(result.output)
    payload = json.loads(result.stdout)
    assert payload["current_control_id"] == _pointer_id(repo / REPORTS)
    paths = [ref["path"] for ref in payload["artifacts"].values()]
    assert paths and all(path.startswith(f"{repo / REPORTS}/") for path in paths), paths


def test_reports_verified_outside_the_repository_are_read(tmp_path: Path, monkeypatch):
    """A custom output beside the repository is not a repository pathspec.

    `verify` can publish into a directory outside the checkout, and the core
    reader accepts that generation. The CLI refresh used to pass the directory
    to Git as a change-set exclusion, which Git helpers refuse outside the
    workspace; the refusal was swallowed into "the uncommitted changes could
    not be determined" and exited 4 with a recovery that repeated the same
    location (#627 case on #575). Nothing outside the repository can appear in
    its change set, so there is nothing to exclude.
    """

    repo = _committed_sample(tmp_path / "repo")
    outside = tmp_path / "sibling-reports"
    monkeypatch.chdir(tmp_path)
    _, _, exit_code = run_verify(
        workspace=repo,
        config=Path("shipgate.yaml"),
        base=None,
        head="HEAD",
        archive_head=True,
        out=outside,
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
    )
    assert exit_code == 0
    assert (outside / "current-control.json").is_file()

    result = _control("--workspace", str(repo), "--reports-dir", str(outside))

    assert result.exit_code == 0, _plain(result.output)
    payload = json.loads(result.stdout)
    assert payload["control_state"] == "complete"
    assert payload["current_control_id"] == _pointer_id(outside)

    # Drift is still drift: an uncommitted edit refuses the same read.
    (repo / "tools.json").write_text(
        (repo / "tools.json").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    drifted = _control("--workspace", str(repo), "--reports-dir", str(outside))
    assert drifted.exit_code == 4, _plain(drifted.output)


def test_verify_format_control_reads_its_own_outside_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`verify --format control` shares the same live-workspace observer.

    Its in-process read of the pointer it just published went through the same
    Git exclusion, so a run written beside the repository withheld authority as
    unverifiable even though nothing had moved.
    """

    repo = _committed_sample(tmp_path / "repo")
    outside = tmp_path / "sibling-reports"
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "verify", "--workspace", str(repo), "--config", "shipgate.yaml",
            "--head", "HEAD", "--out", str(outside), "--format", "control",
        ],
        env=ENV,
    )

    assert result.exit_code == 0, _plain(result.output)
    payload = json.loads(result.stdout)
    assert payload["control_state"] == "complete", payload
    assert payload["current_control_id"] == _pointer_id(outside)


def _dirty(repo: Path, kind: str) -> str | None:
    """Leave one uncommitted change of ``kind``; return the path Git reports."""

    if kind == "tracked":
        path = repo / "tools.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return "tools.json"
    if kind == "untracked":
        (repo / "agent.py").write_text("print('hello')\n", encoding="utf-8")
        return "agent.py"
    return None


def _outcome(result) -> tuple[int, str]:
    """A refresh's exit code and its refusal line, or its control state."""

    if result.exit_code == 0:
        return 0, json.loads(result.stdout)["control_state"]
    lines = [
        line
        for line in _plain(result.output).splitlines()
        if line.startswith("Current control is unavailable")
    ]
    assert lines, _plain(result.output)
    return result.exit_code, lines[-1]


def _run_into(tmp_path: Path, where: str, before: str, *extra: str):
    """One verify of a fresh repository, into its own reports or a sibling.

    The workspace is named relative to the caller, who stands in the parent
    directory: the layout a user writing reports beside a checkout has.
    """

    repo = _committed_sample(tmp_path / where / "repo")
    dirty = _dirty(repo, before)
    workspace = f"{where}/repo"
    reports = repo / REPORTS
    args = ["verify", "--workspace", workspace, "--config", "shipgate.yaml"]
    if where == "outside":
        reports = tmp_path / where / "sibling-reports"
        args += ["--out", str(reports)]
    verified = runner.invoke(app, [*args, *extra, "--format", "control"], env=ENV)
    assert verified.exit_code == 0, _plain(verified.output)
    verifier = json.loads((reports / "verifier.json").read_text(encoding="utf-8"))
    return repo, workspace, reports, dirty, json.loads(verified.stdout), verifier


@pytest.mark.parametrize("before", ["clean", "tracked", "untracked"])
def test_plain_verify_into_an_outside_directory_matches_the_in_repository_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: str
):
    """The normal local loop — no `--head` — with reports written beside the checkout.

    The writing run handed the sibling directory to Git as a change-set
    exclusion; the helper refused it, the refusal was swallowed, and the run
    recorded no input census: exit 2, "input directory capture is
    unavailable", and a refresh that could never succeed (#575 review). Nothing
    outside the repository can appear in its change set, so the run and its
    refresh must answer exactly as they do for reports inside it — including
    what a change before or after the run does.
    """

    monkeypatch.chdir(tmp_path)
    outcomes = {}
    for where in ("inside", "outside"):
        repo, workspace, reports, dirty, payload, verifier = _run_into(
            tmp_path, where, before
        )
        assert payload["control_state"] == "complete", payload
        assert payload["current_control_id"] == _pointer_id(reports)
        # A change made before the run is part of what it decided, not hidden.
        if dirty is not None:
            assert dirty in verifier["changed_files"], verifier["changed_files"]

        refreshed = _control("--workspace", workspace, "--reports-dir", str(reports))
        assert _outcome(refreshed) == (0, "complete")
        assert json.loads(refreshed.stdout)["current_control_id"] == payload[
            "current_control_id"
        ]

        # A change made after the run is one it never saw.
        unseen = _dirty(repo, "tracked" if before == "untracked" else "untracked")
        drifted = _outcome(
            _control("--workspace", workspace, "--reports-dir", str(reports))
        )
        assert drifted[0] == 4
        assert "(workspace_changed)" in drifted[1] and f"({unseen})" in drifted[1]
        outcomes[where] = (sorted(verifier["changed_files"]), drifted)

    assert outcomes["outside"] == outcomes["inside"]


@pytest.mark.parametrize("before", ["clean", "untracked"])
def test_preview_into_an_outside_directory_matches_the_in_repository_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: str
):
    """`verify --preview` reads the working tree and binds its overlay by the same rule.

    With a sibling `--out` it could read neither: the preview evaluated an empty
    change set ("Preview could not read the working tree"), its pointer bound no
    overlay, and an untracked `agent.py` the preview had in fact been run on was
    then reported as "an uncommitted change this decision never saw" — on every
    rerun (#575 review). It must see what an in-repository preview sees and its
    pointer must refresh the same way.
    """

    monkeypatch.chdir(tmp_path)
    outcomes = {}
    for where in ("inside", "outside"):
        repo, workspace, reports, dirty, payload, verifier = _run_into(
            tmp_path, where, before, "--preview"
        )
        assert "could not read the working tree" not in json.dumps(verifier)
        if dirty is not None:
            assert dirty in verifier["changed_files"], verifier["changed_files"]
        refreshed = _outcome(
            _control("--workspace", workspace, "--reports-dir", str(reports))
        )
        _dirty(repo, "tracked")
        drifted = _outcome(
            _control("--workspace", workspace, "--reports-dir", str(reports))
        )
        assert drifted[0] == 4
        assert "(workspace_changed)" in drifted[1] and "(tools.json)" in drifted[1]
        outcomes[where] = (
            payload["control_state"],
            payload["next_action"]["why"],
            sorted(verifier["changed_files"]),
            refreshed,
            drifted,
        )

    assert outcomes["outside"] == outcomes["inside"]
    assert "never saw" not in outcomes["outside"][1]


def test_only_a_directory_disjoint_from_the_repository_loses_its_exclusion(
    tmp_path: Path,
):
    """One containment rule for every output directory handed to Git."""

    from agents_shipgate.cli.current_workspace import worktree_exclusion
    from agents_shipgate.cli.verify.git import working_tree_context
    from agents_shipgate.core.errors import ConfigError

    repo = _committed_sample(tmp_path / "repo")
    inside = repo / REPORTS

    # Inside the repository — however it is spelled — the exclusion is kept.
    assert worktree_exclusion(repo, inside) == inside
    folded = repo / ".." / "repo" / REPORTS
    assert worktree_exclusion(repo, folded) == folded
    # A name that merely shares the repository's prefix is not inside it.
    sibling = tmp_path / "repo-reports"
    assert worktree_exclusion(repo, sibling) is None

    # The root and its parent are still handed over, and still refused.
    for refused in (repo, tmp_path):
        assert worktree_exclusion(repo, refused) == refused
        with pytest.raises(ConfigError):
            working_tree_context(repo, exclude=worktree_exclusion(repo, refused))

    # Dropping the exclusion can only surface changes, never hide one.
    (repo / "agent.py").write_text("print('hello')\n", encoding="utf-8")
    changed, _ = working_tree_context(repo, exclude=worktree_exclusion(repo, sibling))
    assert "agent.py" in changed


@pytest.mark.skipif(os.name == "nt", reason="Directory symlinks need privileges on Windows.")
def test_a_symlinked_output_spelling_keeps_the_exclusion_rule_it_had(tmp_path: Path):
    """Only a directory disjoint by both spellings changes behaviour."""

    from agents_shipgate.cli.current_workspace import worktree_exclusion

    repo = _committed_sample(tmp_path / "repo")
    inside = repo / REPORTS
    inside.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    # Spelled outside, resolving inside: still excluded, exactly as before.
    into_repo = tmp_path / "link-into-repo"
    into_repo.symlink_to(inside, target_is_directory=True)
    assert worktree_exclusion(repo, into_repo) == into_repo
    # Spelled inside, resolving outside: still handed to the Git helper.
    out_of_repo = repo / "link-out-of-repo"
    out_of_repo.symlink_to(elsewhere, target_is_directory=True)
    assert worktree_exclusion(repo, out_of_repo) == out_of_repo


def test_verify_and_agent_control_share_one_default_rule(tmp_path: Path):
    """One definition, not a second implementation that can drift.

    The default follows the *requested* workspace, including a project nested
    below the Git root; an explicit `verify --out` keeps its own Git-root rule.
    """

    from agents_shipgate.cli.current_workspace import (
        DEFAULT_REPORTS_DIR,
        default_reports_dir,
    )
    from agents_shipgate.cli.verify.orchestrator import DEFAULT_OUT_DIR, _resolve_out_dir
    from agents_shipgate.schemas.contract import DEFAULT_PATHS

    # The leaf spells the name rather than importing the contract module; pin
    # that spelling to the published default so the two cannot drift apart.
    assert DEFAULT_REPORTS_DIR.as_posix() == DEFAULT_PATHS["reports_dir"] == REPORTS
    assert DEFAULT_OUT_DIR is DEFAULT_REPORTS_DIR

    root = (tmp_path / "root").resolve()
    nested = root / "project"
    nested.mkdir(parents=True)

    written = _resolve_out_dir(git_root=root, requested_workspace=nested, out=None)
    assert written == default_reports_dir(nested).resolve() == nested / REPORTS
    assert _resolve_out_dir(
        git_root=root, requested_workspace=nested, out=Path("custom")
    ) == root / "custom"
