"""Git pathnames with spaces in the shared unified-diff parser (#581).

Git C-quotes a pathname only when it holds a control character, a double
quote, a backslash or, with ``core.quotePath``, a non-ASCII byte. It never
quotes a space. The parser split every unquoted ``diff --git`` header at its
first space, so a change touching ``docs/new scope/notes.md`` became a record
whose paths were the ``\\0invalid-diff-path`` sentinel. ``check`` published the
sentinel as a changed file and asked for review of it, and a scoped manifest
under a spaced directory crashed ``verify`` in ``os.lstat`` on the embedded NUL.

These cases run against real ``git diff`` output wherever the defect is about
what Git emits, with both ``core.quotePath`` settings because ``check`` and
``verify`` diff with it off. Handwritten text is used only for records Git
would never produce.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.boundary_diff import parse_unified_diff
from agents_shipgate.core.trust_roots import is_configured_manifest

SENTINEL = "\0invalid-diff-path"
runner = CliRunner()

_WINDOWS = sys.platform == "win32"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _init(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "core.autocrlf", "false")
    return repo


def _write(repo: Path, name: str, data: str | bytes) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def _commit(repo: Path, message: str, *, executable: tuple[str, ...] = ()) -> None:
    _git(repo, "add", "-A")
    # After staging: ``git add`` re-reads the worktree mode, which would undo
    # an index-only executable bit set before it.
    for name in executable:
        _git(repo, "update-index", "--chmod=+x", name)
    _git(repo, "commit", "-qm", message)


def _pairs(diff_text: str) -> set[tuple[str | None, str | None]]:
    return {(item.old_path, item.new_path) for item in parse_unified_diff(diff_text)}


@pytest.fixture
def spaced_history(tmp_path: Path) -> Path:
    """One commit pair holding every record shape Git emits for a spaced name."""

    repo = _init(tmp_path / "repo")
    _write(repo, "docs/new scope/notes.md", "one\n")
    _write(repo, "a b/c", "c\n")
    _write(repo, "mode me.sh", "echo\n")
    _write(repo, "bin file.dat", b"\x00\x01")
    _write(repo, "rename me.md", "r\n" * 20)
    _write(repo, "ünï.md", "u\n" * 20)
    _write(repo, "gone file.md", "g\n")
    if not _WINDOWS:
        _write(repo, "trail ", "t\n")
        _write(repo, 'quo"te.md', "q\n")
    _commit(repo, "base")

    _write(repo, "docs/new scope/notes.md", "one\ntwo\n")
    _write(repo, "a b/c", "c\nc2\n")
    _write(repo, "bin file.dat", b"\x00\x02")
    _git(repo, "mv", "rename me.md", "renamed to.md")
    _git(repo, "mv", "ünï.md", "plain name.md")
    _git(repo, "rm", "-q", "gone file.md")
    _write(repo, "brand new.md", "n\n")
    if not _WINDOWS:
        _write(repo, "trail ", "t\nt2\n")
        _write(repo, 'quo"te.md', "q\nq2\n")
    _commit(repo, "head", executable=("mode me.sh",))
    return repo


@pytest.mark.parametrize("quote_path", ["true", "false"])
def test_every_record_git_emits_for_a_spaced_name_keeps_its_paths(
    spaced_history: Path, quote_path: str
) -> None:
    diff_text = _git(
        spaced_history,
        "-c",
        f"core.quotePath={quote_path}",
        "diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--find-renames=50%",
        "HEAD~1",
        "HEAD",
    )

    expected = {
        ("docs/new scope/notes.md", "docs/new scope/notes.md"),
        ("a b/c", "a b/c"),
        ("mode me.sh", "mode me.sh"),
        ("bin file.dat", "bin file.dat"),
        ("rename me.md", "renamed to.md"),
        ("ünï.md", "plain name.md"),
        ("gone file.md", None),
        (None, "brand new.md"),
    }
    if not _WINDOWS:
        expected |= {("trail ", "trail "), ('quo"te.md', 'quo"te.md')}

    assert "old mode 100644" in diff_text
    assert _pairs(diff_text) == expected
    renames = {
        (item.old_path, item.new_path)
        for item in parse_unified_diff(diff_text)
        if item.is_rename
    }
    assert renames == {
        ("rename me.md", "renamed to.md"),
        ("ünï.md", "plain name.md"),
    }


def test_a_copy_between_spaced_names_is_named_by_its_copy_lines(tmp_path: Path) -> None:
    repo = _init(tmp_path / "repo")
    _write(repo, "copy src.md", "".join(f"{n}\n" for n in range(40)))
    _commit(repo, "base")
    _write(repo, "copy dst.md", "".join(f"{n}\n" for n in range(41)))
    _commit(repo, "head")

    diff_text = _git(
        repo, "diff", "-C", "--find-copies-harder", "HEAD~1", "HEAD"
    )

    assert "copy from copy src.md" in diff_text
    (record,) = parse_unified_diff(diff_text)
    assert (record.old_path, record.new_path) == ("copy src.md", "copy dst.md")
    assert record.added_lines == ["40"]


def test_a_header_whose_halves_differ_with_no_rename_lines_is_refused() -> None:
    text = (
        "diff --git a/one two b/three four\n"
        "--- a/one two\t\n"
        "+++ b/three four\t\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )

    (record,) = parse_unified_diff(text)

    assert (record.old_path, record.new_path) == (SENTINEL, SENTINEL)


def test_a_diff_line_that_disagrees_with_the_rename_lines_is_refused() -> None:
    text = (
        "diff --git a/one two b/three four\n"
        "similarity index 90%\n"
        "rename from one two\n"
        "rename to three four\n"
        "--- a/other name\t\n"
        "+++ b/three four\t\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )

    (record,) = parse_unified_diff(text)

    assert record.old_path == SENTINEL
    assert record.new_path == "three four"


def test_a_rename_line_that_disagrees_with_a_resolved_header_is_refused() -> None:
    text = (
        "diff --git a/new scope/x.md b/new scope/x.md\n"
        "rename from new scope/y.md\n"
        "rename to new scope/x.md\n"
    )

    (record,) = parse_unified_diff(text)

    assert record.old_path == SENTINEL


def test_a_timestamp_after_a_tab_is_not_part_of_the_name() -> None:
    text = (
        "diff --git a/new scope/x.md b/new scope/x.md\n"
        "--- a/new scope/x.md\t2026-09-14 10:00:00\n"
        "+++ b/new scope/x.md\t2026-09-14 10:00:01\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )

    (record,) = parse_unified_diff(text)

    assert (record.old_path, record.new_path) == ("new scope/x.md", "new scope/x.md")


def test_a_refused_record_is_not_the_configured_manifest(tmp_path: Path) -> None:
    (tmp_path / "shipgate.yaml").write_text("version: '0.1'\n", encoding="utf-8")

    assert (
        is_configured_manifest("shipgate.yaml", SENTINEL, workspace=tmp_path) is False
    )


_ADK_AGENT = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def lookup_case(case_id: str) -> dict:
    """Look up a case."""
    return {"case": case_id}


root_agent = LlmAgent(
    name="closer_agent",
    instruction="Route approvals.",
    tools=[FunctionTool(func=lookup_case)],
)
'''

_MANIFEST = """version: "0.1"
project:
  name: spaced-scope
agent:
  name: closer-agent
  declared_purpose:
    - look up cases
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
"""


def _check(repo: Path, *extra: str) -> dict:
    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(repo),
            "--base",
            "HEAD~1",
            "--head",
            "HEAD",
            "--format",
            "agent-boundary-json",
            *extra,
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.mark.parametrize("with_manifest", [False, True])
def test_check_reads_a_changed_path_with_a_space(tmp_path: Path, with_manifest: bool) -> None:
    repo = _init(tmp_path / "repo")
    _write(repo, "README.md", "base\n")
    if with_manifest:
        _write(repo, "agent.py", _ADK_AGENT)
        _write(repo, "shipgate.yaml", _MANIFEST)
    _commit(repo, "base")
    _write(repo, "docs/new scope/notes.md", "notes\n")
    _commit(repo, "docs")

    extra = ("--config", "shipgate.yaml") if with_manifest else ()
    payload = _check(repo, *extra)

    assert payload["changed_files"] == ["docs/new scope/notes.md"]
    assert payload["decision"] == "allow", payload


def _verify_scoped_manifest(tmp_path: Path, scope: str) -> tuple[int, dict]:
    repo = _init(tmp_path / scope.replace(" ", "_") / "repo")
    _write(repo, "README.md", "base\n")
    (repo / ".gitignore").write_text("sg-out/\n.agents-shipgate/\n", encoding="utf-8")
    _commit(repo, "base")
    _write(repo, f"agents/{scope}/agent.py", _ADK_AGENT)
    _write(repo, f"agents/{scope}/shipgate.yaml", _MANIFEST)
    _commit(repo, "add a scoped manifest")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            f"agents/{scope}/shipgate.yaml",
            "--base",
            "HEAD~1",
            "--head",
            "HEAD",
            "--ci-mode",
            "advisory",
            "--format",
            "control",
            "--out",
            str(repo / "sg-out"),
        ],
    )
    assert "embedded null" not in result.output, result.output
    return result.exit_code, json.loads(result.output[result.output.index("{") :])


def test_verify_reaches_the_same_review_for_a_scoped_manifest_under_a_spaced_directory(
    tmp_path: Path,
) -> None:
    """The issue's own reproduction: a PR adds ``agents/new scope/shipgate.yaml``."""

    control_exit, control = _verify_scoped_manifest(tmp_path, "new-scope")
    spaced_exit, spaced = _verify_scoped_manifest(tmp_path, "new scope")

    assert control_exit in (0, 1), control
    assert spaced_exit == control_exit
    for field in ("decision", "control_state"):
        assert spaced[field] == control[field], (field, spaced, control)
    assert spaced["next_action"]["kind"] == control["next_action"]["kind"]
