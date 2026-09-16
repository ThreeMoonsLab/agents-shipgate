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

    # Inside the repository — however it is spelled — the exclusion is kept,
    # respelled beneath the root, which is the one spelling Git can relativize.
    assert worktree_exclusion(repo, inside) == repo.resolve() / REPORTS
    folded = repo / ".." / "repo" / REPORTS
    assert worktree_exclusion(repo, folded) == repo.resolve() / REPORTS
    # A name that merely shares the repository's prefix is not inside it.
    sibling = tmp_path / "repo-reports"
    assert worktree_exclusion(repo, sibling) is None

    # The root and its parent are still handed over, and still refused.
    for refused in (repo, tmp_path):
        assert worktree_exclusion(repo, refused) is not None
        with pytest.raises(ConfigError):
            working_tree_context(repo, exclude=worktree_exclusion(repo, refused))

    # Dropping the exclusion can only surface changes, never hide one.
    (repo / "agent.py").write_text("print('hello')\n", encoding="utf-8")
    changed, _ = working_tree_context(repo, exclude=worktree_exclusion(repo, sibling))
    assert "agent.py" in changed


@pytest.mark.skipif(os.name == "nt", reason="Directory symlinks need privileges on Windows.")
def test_a_symlinked_output_spelling_is_classified_where_it_physically_is(tmp_path: Path):
    """Containment is a property of the directory, not of how it was spelled.

    Keeping the exclusion when *either* spelling looked inside let the writer
    and the reader answer differently for one directory: `verify` resolves
    `--out` before asking, the refresh hands over the spelling it was given, and
    an in-repository symlink pointing outside was "inside" for one and
    "outside" for the other. The Git helper then refused the exclusion, the
    refusal was swallowed into an unreadable change set, and a
    `review_publishable` pointer stayed current over an edited tree (#575
    review cycle 2).
    """

    from agents_shipgate.cli.current_workspace import worktree_exclusion

    repo = _committed_sample(tmp_path / "repo")
    inside = repo / REPORTS
    inside.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    # Spelled outside, resolving inside: excluded, as the directory it is.
    into_repo = tmp_path / "link-into-repo"
    into_repo.symlink_to(inside, target_is_directory=True)
    assert worktree_exclusion(repo, into_repo) == repo.resolve() / REPORTS
    # Spelled inside, resolving outside: disjoint from the tree Git reads, so
    # there is nothing to exclude — and, above all, the same answer `verify`'s
    # own already-resolved `--out` gets for the same place.
    out_of_repo = repo / "link-out-of-repo"
    out_of_repo.symlink_to(elsewhere, target_is_directory=True)
    assert worktree_exclusion(repo, out_of_repo) is None
    assert worktree_exclusion(repo, out_of_repo.resolve()) is None
    assert worktree_exclusion(repo, out_of_repo / "rpt") is None
    assert worktree_exclusion(repo, (out_of_repo / "rpt").resolve()) is None


def test_a_case_variant_spelling_is_the_directory_it_physically_is(tmp_path: Path):
    """`--out <parent>/REPO/rpt` for `repo` is inside `repo` where case is folded.

    A lexical prefix test called it outside, dropped the exclusion, and left the
    run writing into its own input census: `verify` exited 3, "path changed
    identity while it was read" (#575 review cycle 2, nit 1).
    """

    from agents_shipgate.cli.current_workspace import worktree_exclusion

    repo = _committed_sample(tmp_path / "repo")
    variant = tmp_path / "REPO"
    if not variant.is_dir():
        pytest.skip("The temporary filesystem is case-sensitive.")

    assert worktree_exclusion(repo, variant / "rpt") == repo.resolve() / "rpt"


def _sample_with_ignored_link(path: Path, link: str, target: Path) -> Path:
    """The sample repository with a committed ignore for a directory symlink.

    The link is ignored, so it is not itself a change; it exists only to give
    the same directory a second spelling, which is how a writer and a reader
    came to disagree about what the reports directory is.

    ``notes.txt`` is a tracked file the manifest does not declare as an input.
    Editing a *declared* input is caught by the recorded-input check whatever
    the worktree view says, which would mask the skipped unseen-change test
    rather than exercise it.
    """

    repo = _committed_sample(path)
    (repo / ".gitignore").write_text(f"{REPORTS}/\n{link}\n", encoding="utf-8")
    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "notes.txt")
    _git(repo, "commit", "-q", "-m", "ignore the link")
    (repo / link).symlink_to(target, target_is_directory=True)
    return repo


def _edit_outside_the_declared_inputs(repo: Path) -> None:
    """One tracked edit and one new untracked file, neither a manifest input."""

    (repo / "notes.txt").write_text("notes\nedited\n", encoding="utf-8")
    (repo / "new_tool.py").write_text("x = 1\n", encoding="utf-8")


def _verify_into(workspace: str, out: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "verify", "--workspace", workspace, "--config", "shipgate.yaml",
            "--out", str(out), "--format", "control", *extra,
        ],
        env=ENV,
    )


@pytest.mark.skipif(os.name == "nt", reason="Directory symlinks need privileges on Windows.")
def test_a_refresh_spelled_through_an_in_repository_link_still_sees_the_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The same directory, two spellings, one answer — or the pointer goes stale.

    `verify` resolves `--out` before asking whether it is inside the repository;
    the refresh asked about the current-directory-anchored spelling it was
    given. While the rule kept an exclusion that looked inside under *either*
    spelling, `repo/via/sib` was "inside" for the reader and `<parent>/sib` was
    "outside" for the writer. The Git helper refused the reader's exclusion
    ("must remain inside workspace"), `live_workspace` swallowed that into an
    unreadable change set, and the unseen-change test was skipped for a
    non-completion pointer: a `review_publishable` answer, with commit, push and
    update_pr granted, stayed current across a tracked edit and a new untracked
    file (#575 review cycle 2).
    """

    monkeypatch.chdir(tmp_path)
    repo = _sample_with_ignored_link(tmp_path / "repo", "via", tmp_path)
    outside = tmp_path / "sib"

    verified = _verify_into("repo", outside)
    assert verified.exit_code == 0, _plain(verified.output)
    published = json.loads(verified.stdout)
    # The fixture must exercise the skipped path: a *completion* pointer refused
    # an unreadable change set even before this fix, so a `complete` answer here
    # would make the regression vacuous.
    assert published["control_state"] != "complete", published
    assert published["permissions"]["commit"] is True, published

    _edit_outside_the_declared_inputs(repo)

    spellings = {
        "through the link": tmp_path / "repo" / "via" / "sib",
        "directly": outside,
    }
    outcomes = {}
    for label, spelling in spellings.items():
        code, line = _outcome(_control("--workspace", "repo", "--reports-dir", str(spelling)))
        assert code == 4, f"{label}: {line}"
        assert "(workspace_changed)" in line or "(workspace_unverifiable)" in line, line
        outcomes[label] = (code, line)
    # One directory, one answer, whichever way it is named.
    assert outcomes["through the link"] == outcomes["directly"]
    assert "new_tool.py" in outcomes["directly"][1]
    assert "notes.txt" in outcomes["directly"][1]


@pytest.mark.skipif(os.name == "nt", reason="Directory symlinks need privileges on Windows.")
def test_one_spelling_for_out_and_reports_dir_through_a_link_out_of_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Even one string cannot be read two ways.

    `--out` and `--reports-dir` given the *same* text, through an in-repository
    symlink to a directory outside it: the writer resolved it and dropped the
    exclusion, the reader kept it, and the refusal that followed left a
    `review_publishable` pointer current over an edited tree.
    """

    monkeypatch.chdir(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    repo = _sample_with_ignored_link(tmp_path / "repo", "lnkdir", elsewhere)
    same = str(tmp_path / "repo" / "lnkdir" / "rpt")

    verified = _verify_into("repo", Path(same))
    assert verified.exit_code == 0, _plain(verified.output)
    published = json.loads(verified.stdout)
    assert published["control_state"] != "complete", published
    assert (elsewhere / "rpt" / "current-control.json").is_file()

    _edit_outside_the_declared_inputs(repo)

    code, line = _outcome(_control("--workspace", "repo", "--reports-dir", same))
    assert code == 4, line
    assert "(workspace_changed)" in line, line
    assert "new_tool.py" in line and "notes.txt" in line, line


def test_a_case_variant_out_is_the_repository_directory_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`--out <parent>/REPO/rpt` for `repo`, where the filesystem folds case.

    The directory is physically inside the repository, so it must be excluded
    from the change set like any other in-repository output. Classifying it by
    spelling dropped the exclusion, and the run then read its own output as an
    input that changed underneath it: exit 3, "path changed identity while it
    was read" (#575 review cycle 2, nit 1).
    """

    monkeypatch.chdir(tmp_path)
    repo = _committed_sample(tmp_path / "repo")
    variant = tmp_path / "REPO" / "rpt"
    if not (tmp_path / "REPO").is_dir():
        pytest.skip("The temporary filesystem is case-sensitive.")

    verified = _verify_into("repo", variant)
    assert verified.exit_code == 0, _plain(verified.output)
    published = json.loads(verified.stdout)
    assert published["control_state"] == "complete", published

    refreshed = _control("--workspace", "repo", "--reports-dir", str(variant))
    assert _outcome(refreshed) == (0, "complete"), _plain(refreshed.output)

    (repo / "tools.json").write_text(
        (repo / "tools.json").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    code, line = _outcome(_control("--workspace", "repo", "--reports-dir", str(variant)))
    assert code == 4 and "(workspace_changed)" in line, line


def test_an_unreadable_change_set_refuses_every_pointer_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Defence in depth: `changed_paths=None` is unknown, never "no drift".

    One containment rule keeps the writer and the reader from disagreeing, but
    a change set can still fail to be read — a Git failure, an exclusion at the
    repository root. Whatever the cause, a currency test that could not be run
    denies the pointer instead of being skipped. It used to be skipped for every
    state but `complete`, which is exactly what let a stale `review_publishable`
    pointer, and a plan-less preview pointer, keep answering (#575 review cycle
    2).
    """

    from agents_shipgate.cli.verify.git import (
        commit_sha,
        merge_base_sha,
        repository_identity,
        tree_sha,
    )
    from agents_shipgate.core.current_control import (
        CurrentControlUnavailable,
        LiveWorkspace,
        publish_current_control,
        read_current_control,
    )
    from agents_shipgate.schemas.current_control import (
        AgentActionRequiredCurrentControl,
        CurrentControlWorkspaceIdentity,
    )

    monkeypatch.chdir(tmp_path)
    repo = _sample_with_ignored_link(tmp_path / "repo", "via", tmp_path)
    publishable = tmp_path / "sib"
    assert _verify_into("repo", publishable).exit_code == 0

    pointer = json.loads((publishable / "current-control.json").read_text(encoding="utf-8"))
    assert pointer["control"]["state"] == "review_publishable", pointer["control"]
    # It binds a plan, so it is checked by the plan-bound path.
    assert "verification_plan" in pointer["artifacts"]

    # A plan-less worktree pointer — the preview shape — is checked by the
    # live-overlay path instead, where the change set is the whole test.
    preview = tmp_path / "preview-reports"
    preview.mkdir()
    # A terminal pointer must bind something; it just must not bind a plan.
    shutil.copy(publishable / "verifier.json", preview / "verifier.json")
    publish_current_control(
        preview,
        operation="preview",
        control=AgentActionRequiredCurrentControl(
            state="agent_action_required",
            reason="A preview reached no release decision.",
        ),
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

    def _live(changed: tuple[str, ...] | None) -> LiveWorkspace:
        return LiveWorkspace(
            root=repo,
            repository=repository_identity(repo),
            head_commit_sha=commit_sha(repo, "HEAD"),
            head_tree_sha=tree_sha(repo, "HEAD"),
            changed_paths=changed,
            resolve_commit=lambda ref: commit_sha(repo, ref),
            resolve_merge_base=lambda base, head: merge_base_sha(repo, base, head),
        )

    refused: dict[str, str] = {}
    for label, reports in (("review_publishable", publishable), ("preview", preview)):
        # A readable, unchanged tree still reads: the refusal below is about the
        # failure to look, not about this fixture.
        assert read_current_control(reports, live=_live(())).pointer is not None, label
        try:
            read_current_control(reports, live=_live(None))
        except CurrentControlUnavailable as exc:
            refused[label] = exc.reason
        else:
            refused[label] = "answered anyway"
    # Both kinds, reported together: each used to answer over a tree that was
    # never read, by a different route through the currency checks.
    assert refused == {
        "review_publishable": "workspace_unverifiable",
        "preview": "workspace_unverifiable",
    }


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
