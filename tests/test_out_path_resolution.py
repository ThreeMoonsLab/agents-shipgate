"""One rule for a relative ``--out``, and paths that open from the caller (#818).

Before, ``verify --out <relative>`` resolved against the scanned repository's
Git root, ``scan --out`` against the manifest's directory, and ``audit --host
--out`` against the current directory, and nothing said which. ``verify
--workspace ../repo --out rel`` wrote an untracked ``rel/`` into the scanned
checkout, printed ``rel/verifier.json`` (which does not exist from the caller),
and ``agent control --reports-dir rel`` from the same shell read another
directory. ``audit --host --out <existing directory>`` exited 4 with a
temporary-file ``Is a directory`` error.

The rules pinned here:

* An explicit relative ``--out`` resolves against the current directory for
  ``verify``, ``scan``, ``audit --host`` and ``fixture run``, as
  ``agent control --reports-dir`` already did; an absolute one is used as
  given; an omitted one keeps its default. Each ``--help`` says so.
* Where a relative spelling now lands somewhere 1.0 did not, a stderr note
  names both directories; from the directory 1.0 resolved against, nothing is
  printed.
* Artifact paths printed on stdout open from the caller: relative to the
  current directory when beneath it, absolute otherwise. ``verifier.json`` on
  disk keeps its repository-relative spelling, and from the Git root stdout and
  the file agree byte for byte.
* Every command a run emits names ``--out`` absolutely, and running it from a
  sibling directory writes to the same place.
* ``audit --host --out <directory>`` refuses up front, as ``config_error``,
  naming the file path it expects, and writes nothing — not even the baseline
  ``--save-baseline`` asked for.
* The output-directory refusal (#804) judges the directory a relative spelling
  reaches from the caller, so ``--out .`` from inside a tracked directory is
  refused like its absolute path.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
REPORTS = "agents-shipgate-reports"
ENV = {
    "NO_COLOR": "1",
    "GITHUB_ACTIONS": None,
    "GITHUB_STEP_SUMMARY": None,
    "FORCE_COLOR": None,
    "COLUMNS": "200",
}
AGENT_ENV = {**ENV, "AGENTS_SHIPGATE_AGENT_MODE": "1"}
NOTE = "resolves against the current directory"
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

runner = CliRunner()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _write_settings(path: Path, allow: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"permissions": {"allow": allow}}, indent=2) + "\n", encoding="utf-8"
    )


def _repo(path: Path, *, manifest: bool) -> Path:
    """A repository on `feature` that widens a shell permission `main` granted.

    `sub/` and `docs/` hold tracked files, so a caller can stand inside the
    repository below its root. Only the default reports directory is ignored:
    every other output directory a test names inside it is judged by #804.
    """

    path.mkdir(parents=True)
    if manifest:
        for name in ("shipgate.yaml", "tools.json"):
            shutil.copy(SAMPLE / name, path / name)
    _write_settings(path / ".claude" / "settings.json", ["Bash(git status)"])
    (path / "sub").mkdir()
    (path / "sub" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (path / "docs").mkdir()
    (path / "docs" / "a.txt").write_text("a\n", encoding="utf-8")
    (path / ".gitignore").write_text(f"{REPORTS}/\n", encoding="utf-8")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.test")
    _git(path, "config", "user.name", "Test User")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "base")
    _git(path, "checkout", "-q", "-b", "feature")
    _write_settings(
        path / ".claude" / "settings.json", ["Bash(git status)", "Bash(curl:*)"]
    )
    _git(path, "commit", "-q", "-am", "widen")
    return path


def _callers(tmp_path: Path, repo: Path) -> dict[str, Path]:
    sibling = tmp_path / "sibling"
    sibling.mkdir(exist_ok=True)
    return {"root": repo, "subdirectory": repo / "sub", "outside": sibling}


def _verify(repo: Path, *extra: str, env=ENV):
    return runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--base", "main", "--format", "json", *extra],
        env=env,
    )


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _flat(text: str) -> str:
    """Help text with Rich's box drawing and wrapping taken out."""

    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", _plain(text)).split())


def _strings(value) -> list[str]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    return [value] if isinstance(value, str) else []


def _assert_every_emitted_out_is_absolute(payload) -> None:
    for text in _strings(payload):
        if "--out" not in text:
            continue
        try:
            tokens = shlex.split(text)
        except ValueError:
            continue
        for index, token in enumerate(tokens[:-1]):
            if token == "--out":
                assert Path(tokens[index + 1]).is_absolute(), text


def _args_after_executable(command: str, subcommand: str) -> list[str]:
    tokens = shlex.split(command)
    return tokens[tokens.index(subcommand) :]


# -- verify ------------------------------------------------------------------


@pytest.mark.parametrize("caller", ["root", "subdirectory", "outside"])
@pytest.mark.parametrize("spelling", ["relative", "absolute"])
def test_verify_out_is_the_callers_path_and_its_printed_artifacts_open_from_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: str, spelling: str
):
    repo = _repo(tmp_path / "repo", manifest=False)
    cwd = _callers(tmp_path, repo)[caller]
    monkeypatch.chdir(cwd)
    elsewhere = tmp_path / "absolute-out"
    out = "rel-verify" if spelling == "relative" else str(elsewhere)
    expected = (cwd / "rel-verify") if spelling == "relative" else elsewhere

    result = _verify(repo, "--out", out)

    assert result.exit_code == 0, _plain(result.output)
    assert (expected / "verifier.json").is_file()
    assert (expected / "current-control.json").is_file()
    if caller != "root" or spelling == "absolute":
        # 1.0 would have written here.
        assert not (repo / "rel-verify").exists()
    untracked = {line[3:] for line in _git(repo, "status", "--porcelain").splitlines()}
    if spelling == "absolute" or caller == "outside":
        assert untracked == set(), untracked
    else:
        assert untracked == {"rel-verify/" if caller == "root" else "sub/rel-verify/"}

    payload = json.loads(result.stdout)
    assert payload["artifacts"], payload
    for key, value in payload["artifacts"].items():
        # Opened from here, every printed path names the directory written to.
        assert Path(value).resolve().parent == expected.resolve(), (key, value)
    for key in ("verifier_json", "agent_handoff_json", "pr_comment"):
        assert Path(payload["artifacts"][key]).is_file(), payload["artifacts"]
    _assert_every_emitted_out_is_absolute(payload)

    # The switch is announced exactly where the destination moved.
    moved = spelling == "relative" and caller != "root"
    assert (NOTE in result.stderr) is moved, result.stderr
    if moved:
        assert str((cwd / "rel-verify").resolve()) in result.stderr
        assert str((repo / "rel-verify").resolve()) in result.stderr


def test_the_file_keeps_repository_paths_and_the_root_run_prints_the_same_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = _repo(tmp_path / "repo", manifest=False)

    monkeypatch.chdir(repo)
    from_root = _verify(repo)
    assert from_root.exit_code == 0, _plain(from_root.output)
    recorded = json.loads((repo / REPORTS / "verifier.json").read_text(encoding="utf-8"))
    printed = json.loads(from_root.stdout)
    assert printed == recorded
    assert recorded["artifacts"]["verifier_json"] == f"{REPORTS}/verifier.json"
    assert NOTE not in from_root.stderr

    monkeypatch.chdir(_callers(tmp_path, repo)["outside"])
    from_outside = _verify(repo)
    assert from_outside.exit_code == 0, _plain(from_outside.output)
    printed = json.loads(from_outside.stdout)
    recorded = json.loads((repo / REPORTS / "verifier.json").read_text(encoding="utf-8"))
    # The default is still under --workspace, and the file still says so
    # relative to the repository; only stdout is spelled for this caller.
    assert recorded["artifacts"] == _default_artifacts(repo)
    assert printed["artifacts"] == {
        key: str((repo / value).resolve()) for key, value in recorded["artifacts"].items()
    }
    unspelled = ("artifacts", "head_report_json", "base_report_json")
    assert {key: value for key, value in printed.items() if key not in unspelled} == {
        key: value for key, value in recorded.items() if key not in unspelled
    }
    assert NOTE not in from_outside.stderr


def _default_artifacts(repo: Path) -> dict[str, str]:
    names = {
        "verifier_json": "verifier.json",
        "verify_run_json": "verify-run.json",
        "agent_handoff_json": "agent-handoff.json",
        "pr_comment": "pr-comment.md",
    }
    return {key: f"{REPORTS}/{name}" for key, name in names.items()}


def test_emitted_verification_commands_replay_from_a_sibling_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The reproduction a run hands out writes where that run wrote."""

    repo = _repo(tmp_path / "repo", manifest=True)
    callers = _callers(tmp_path, repo)
    monkeypatch.chdir(callers["subdirectory"])
    first = runner.invoke(
        app,
        [
            "verify", "--workspace", str(repo), "--config", "shipgate.yaml",
            "--base", "main", "--ci-mode", "advisory", "--format", "json",
            "--out", "rel",
        ],
        env=ENV,
    )
    assert first.exit_code in (0, 1), _plain(first.output)
    reports = (repo / "sub" / "rel").resolve()
    assert (reports / "report.json").is_file()
    payload = json.loads(first.stdout)
    _assert_every_emitted_out_is_absolute(payload)
    command = payload["fix_task"]["verification_command"]
    assert f"--out {shlex.quote(str(reports))}" in command, command
    for value in payload["artifacts"].values():
        assert Path(value).is_file(), value
        assert not Path(value).is_absolute(), value  # beneath the caller: relative

    (reports / "report.json").unlink()
    monkeypatch.chdir(callers["outside"])
    replay = runner.invoke(app, _args_after_executable(command, "verify"), env=ENV)

    assert replay.exit_code == first.exit_code, _plain(replay.output)
    assert list(callers["outside"].iterdir()) == []
    assert not (repo / "rel").exists()
    assert (reports / "report.json").is_file()
    replayed = json.loads(replay.stdout)
    assert replayed["control"]["state"] == payload["control"]["state"]
    assert replayed["fix_task"]["verification_command"] == command
    for value in replayed["artifacts"].values():
        assert Path(value).is_absolute() and Path(value).is_file(), value
        assert Path(value).parent == reports, value


def test_preview_commands_name_an_absolute_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = _repo(tmp_path / "repo", manifest=True)
    monkeypatch.chdir(_callers(tmp_path, repo)["outside"])

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--preview", "--base", "main",
         "--out", "preview-out", "--format", "json"],
        env=ENV,
    )

    assert result.exit_code == 0, _plain(result.output)
    payload = json.loads(result.stdout)
    assert (Path("preview-out") / "verifier.json").is_file()
    emitted = [text for text in _strings(payload) if " --out " in text]
    assert emitted, "the preview should route to a verify command carrying --out"
    _assert_every_emitted_out_is_absolute(payload)


@pytest.mark.parametrize("spelling", [".", "../docs"])
def test_a_relative_out_reaching_a_tracked_directory_is_refused_from_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
):
    """#804 judges the directory the caller's spelling reaches, however spelled."""

    repo = _repo(tmp_path / "repo", manifest=False)
    monkeypatch.chdir(repo / ("docs" if spelling == "." else "sub"))

    result = _verify(repo, "--out", spelling, env=AGENT_ENV)

    assert result.exit_code == 2, _plain(result.output)
    assert "holds tracked repository files (docs/a.txt)" in _plain(result.output)
    assert sorted(path.name for path in (repo / "docs").iterdir()) == ["a.txt"]


def test_a_relative_out_from_inside_the_trust_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = _repo(tmp_path / "repo", manifest=False)
    monkeypatch.chdir(repo / ".claude")

    result = _verify(repo, "--out", ".", env=AGENT_ENV)

    assert result.exit_code == 2, _plain(result.output)
    assert "holds tracked repository files (.claude/settings.json)" in _plain(result.output)
    assert sorted(path.name for path in (repo / ".claude").iterdir()) == ["settings.json"]


# -- audit --host ------------------------------------------------------------


def _audit(repo: Path, *extra: str, env=ENV):
    return runner.invoke(
        app, ["audit", "--host", "--workspace", str(repo), "--json", *extra], env=env
    )


@pytest.mark.parametrize("caller", ["root", "subdirectory", "outside"])
@pytest.mark.parametrize("spelling", ["relative", "absolute"])
def test_audit_out_is_a_file_path_relative_to_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: str, spelling: str
):
    repo = _repo(tmp_path / "repo", manifest=False)
    cwd = _callers(tmp_path, repo)[caller]
    monkeypatch.chdir(cwd)
    absolute = tmp_path / "audit-out" / "grants.json"
    out = "grants.json" if spelling == "relative" else str(absolute)
    expected = cwd / "grants.json" if spelling == "relative" else absolute

    result = _audit(repo, "--out", out)

    assert result.exit_code == 0, _plain(result.output)
    assert json.loads(expected.read_text(encoding="utf-8")) == json.loads(result.stdout)
    if caller != "root":
        assert not (repo / "grants.json").exists()


@pytest.mark.parametrize("mode", [[], ["--save-baseline"], ["--drift"]])
def test_audit_out_naming_a_directory_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: list[str]
):
    repo = _repo(tmp_path / "repo", manifest=False)
    callers = _callers(tmp_path, repo)
    monkeypatch.chdir(callers["outside"])
    (callers["outside"] / "existing-dir").mkdir()
    expected = (callers["outside"] / "existing-dir" / "host-grants.json").resolve()

    result = _audit(repo, *mode, "--out", "existing-dir", env=AGENT_ENV)

    text = _plain(result.output)
    assert result.exit_code == 2, text
    assert "Is a directory" not in text and ".tmp" not in text, text
    assert "--out existing-dir is a directory" in text, text
    assert str(expected) in text, text
    assert list((callers["outside"] / "existing-dir").iterdir()) == []
    assert not (repo / ".agents-shipgate").exists()
    line = json.loads(
        [row for row in text.splitlines() if row.startswith('{"error"')][-1]
    )
    assert line["error"] == "config_error"
    assert line["exit_code"] == 2
    action = line["next_actions"][0]
    assert action["kind"] == "command", action
    assert str(expected) in action["why"], action
    _assert_every_emitted_out_is_absolute(action["command"])
    assert f"--out {shlex.quote(str(expected))}" in action["command"], action

    if mode:
        return
    # The recovery is the same request with only --out changed, and it works
    # from anywhere.
    monkeypatch.chdir(tmp_path)
    replay = runner.invoke(app, _args_after_executable(action["command"], "audit"), env=ENV)
    assert replay.exit_code == 0, _plain(replay.output)
    assert json.loads(expected.read_text(encoding="utf-8")) == json.loads(replay.stdout)


def test_audit_directory_refusal_never_routes_over_an_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = _repo(tmp_path / "repo", manifest=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "existing-dir").mkdir()
    (tmp_path / "existing-dir" / "host-grants.json").write_text("mine\n", encoding="utf-8")

    result = _audit(repo, "--out", "existing-dir", env=AGENT_ENV)

    assert result.exit_code == 2, _plain(result.output)
    line = json.loads(
        [row for row in _plain(result.output).splitlines() if row.startswith('{"error"')][-1]
    )
    assert line["next_actions"][0]["kind"] == "review", line
    assert line["next_actions"][0]["command"] is None, line
    assert (tmp_path / "existing-dir" / "host-grants.json").read_text() == "mine\n"


# -- scan and fixture run ----------------------------------------------------


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "workspace" / "project"
    project.mkdir(parents=True)
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copy(SAMPLE / name, project / name)
    return project


@pytest.mark.parametrize("caller", ["manifest directory", "parent", "outside"])
@pytest.mark.parametrize("spelling", ["relative", "absolute"])
def test_scan_out_is_the_callers_path_and_its_printed_reports_open_from_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: str, spelling: str
):
    project = _project(tmp_path)
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    cwd = {"manifest directory": project, "parent": project.parent, "outside": sibling}[caller]
    monkeypatch.chdir(cwd)
    absolute = tmp_path / "scan-out"
    out = "rel-scan" if spelling == "relative" else str(absolute)
    expected = cwd / "rel-scan" if spelling == "relative" else absolute

    result = runner.invoke(
        app,
        ["scan", "-c", str(project / "shipgate.yaml"), "--out", out,
         "--format", "markdown,json", "--no-packet"],
        env=ENV,
    )

    assert result.exit_code == 0, _plain(result.output)
    assert (expected / "report.json").is_file()
    if caller != "manifest directory":
        assert not (project / "rel-scan").exists()
    printed = [
        line[2:] for line in _plain(result.stdout).splitlines() if line.startswith("- ")
        and line.rstrip().endswith(("report.md", "report.json"))
    ]
    assert len(printed) == 2, result.stdout
    for value in printed:
        assert Path(value).is_file(), value
    moved = spelling == "relative" and caller != "manifest directory"
    assert (NOTE in result.stderr) is moved, result.stderr


def test_multi_manifest_scan_puts_each_project_beneath_the_callers_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "workspace"
    for name in ("alpha", "beta"):
        (workspace / name).mkdir(parents=True)
        for source in ("shipgate.yaml", "tools.json"):
            shutil.copy(SAMPLE / source, workspace / name / source)
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)

    result = runner.invoke(
        app,
        ["scan", "--workspace", str(workspace), "--out", "rel-multi",
         "--format", "json", "--no-packet"],
        env=ENV,
    )

    assert result.exit_code == 0, _plain(result.output)
    written = sorted(path.parent for path in (caller / "rel-multi").rglob("report.json"))
    assert len(written) == 2, written
    assert all(path.parent == caller / "rel-multi" for path in written), written
    assert not list(workspace.rglob("rel-multi"))
    assert NOTE in result.stderr and "each manifest's directory" in result.stderr


def test_fixture_run_keeps_a_relative_out_beside_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The copy is removed when --out is given; the reports must not be in it."""

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["fixture", "run", "clean_read_only_agent", "--out", "fixture-out"], env=ENV
    )

    assert result.exit_code == 0, _plain(result.output)
    assert (tmp_path / "fixture-out" / "report.json").is_file()
    reported = [
        line.split(":", 1)[1].strip()
        for line in _plain(result.stdout).splitlines()
        if line.startswith("Reports:")
    ]
    assert reported and (Path(reported[0]) / "report.json").is_file(), result.stdout


# -- help --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "kind", "default"),
    [
        (["verify"], "Output directory (not a file)", "Default: agents-shipgate-reports under --workspace"),
        (["scan"], "Output directory (not a file)", "Default: output.directory, relative to the manifest"),
        (["audit"], "a file path, not a directory", "Default: no file is written"),
        (["fixture", "run"], "Output directory (not a file)", "Defaults to a temp location"),
    ],
)
def test_help_states_the_base_the_kind_and_the_default(
    command: list[str], kind: str, default: str
):
    result = runner.invoke(app, [*command, "--help"], env=ENV)

    assert result.exit_code == 0, result.output
    text = _flat(result.output)
    assert NOTE in text, text
    assert kind in text, text
    assert default in text, text
