"""Hunk-row retention in the shared unified-diff parser (#611).

The parser used to decide what a line *was* from how it was spelled, so a
removed ``---`` (rendered ``----``) and a removed ``-- note`` (rendered
``--- note``) matched the file-header prefixes.  The first dropped a
counted row; the second rewrote the file's identity from its own content.

These cases run against real ``git diff`` output wherever the defect is
about what Git actually emits, and against handwritten text where the
point is a malformed input Git would never produce.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agents_shipgate.core.boundary_diff import (
    ResolvedFileText,
    parse_unified_diff,
)
from agents_shipgate.core.instruction_structure import (
    instruction_pair_is_complete,
    unchanged_instruction_structure,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "config", "user.name", "Test")
    return workspace


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"write {name}")


def _rows_are_counted(diff) -> bool:
    """Every hunk retains exactly the rows its header declared."""

    return all(
        hunk.old_count == sum(kind in {" ", "-"} for kind, _ in hunk.lines)
        and hunk.new_count == sum(kind in {" ", "+"} for kind, _ in hunk.lines)
        for hunk in diff.hunks
    )


# --- what Git really emits -------------------------------------------------


@pytest.mark.parametrize(
    "before,after,removed,added",
    [
        # The reported case: a Markdown horizontal rule, rendered "----".
        (
            "Explain results.\n---\nKeep examples concise.\n",
            "Explain results.\nKeep examples concise.\n",
            ["---"],
            [],
        ),
        # The mirror: adding one, and adding a "+++" lookalike.
        (
            "Explain results.\nKeep examples concise.\n",
            "Explain results.\n+++\nKeep examples concise.\n",
            [],
            ["+++"],
        ),
        # Header-shaped content *with a space*, which reached the file-header
        # branch rather than being dropped.
        (
            "Explain results.\n-- note\nKeep examples concise.\n",
            "Explain results.\nKeep examples concise.\n",
            ["-- note"],
            [],
        ),
        (
            "Explain results.\nKeep examples concise.\n",
            "Explain results.\n++ note\nKeep examples concise.\n",
            [],
            ["++ note"],
        ),
        # A line that is only dashes, longer than the header prefix.
        (
            "Explain results.\n-------\nKeep examples concise.\n",
            "Explain results.\nKeep examples concise.\n",
            ["-------"],
            [],
        ),
    ],
)
def test_header_shaped_content_is_retained_as_hunk_data(
    repo: Path, before: str, after: str, removed: list[str], added: list[str]
) -> None:
    _commit(repo, "AGENTS.md", before)
    (repo / "AGENTS.md").write_text(after, encoding="utf-8")

    diff = parse_unified_diff(_git(repo, "diff"))[0]

    assert diff.removed_lines == removed
    assert diff.added_lines == added
    assert _rows_are_counted(diff)
    # Identity comes from the header, never from a content row that looks
    # like one.
    assert diff.old_path == "AGENTS.md"
    assert diff.new_path == "AGENTS.md"


def test_the_reported_rule_removal_reaches_a_complete_comparison(repo: Path) -> None:
    """#611's acceptance: plain guidance either side, so the structural
    comparison must complete rather than refuse an incomplete input."""

    before = "Explain results.\n---\nKeep examples concise.\n"
    after = "Explain results.\nKeep examples concise.\n"
    _commit(repo, "AGENTS.md", before)
    (repo / "AGENTS.md").write_text(after, encoding="utf-8")

    diff = parse_unified_diff(_git(repo, "diff"))[0]
    resolved = ResolvedFileText(before, after, "fixture", None, None)

    assert instruction_pair_is_complete(diff, resolved)
    assert unchanged_instruction_structure(diff, resolved)


def test_multi_file_rows_stay_with_their_own_file(repo: Path) -> None:
    _commit(repo, "AGENTS.md", "One.\n---\nTwo.\n")
    _commit(repo, "CLAUDE.md", "Three.\n-- four\nFive.\n")
    (repo / "AGENTS.md").write_text("One.\nTwo.\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("Three.\nFive.\n", encoding="utf-8")

    files = {f.path: f for f in parse_unified_diff(_git(repo, "diff"))}

    assert set(files) == {"AGENTS.md", "CLAUDE.md"}
    assert files["AGENTS.md"].removed_lines == ["---"]
    assert files["CLAUDE.md"].removed_lines == ["-- four"]
    assert all(_rows_are_counted(f) for f in files.values())


def test_multiple_hunks_in_one_file_each_keep_their_rows(repo: Path) -> None:
    body = "\n".join(["---"] + [f"line {n}" for n in range(1, 30)] + ["---", ""])
    _commit(repo, "AGENTS.md", body)
    (repo / "AGENTS.md").write_text(
        "\n".join([f"line {n}" for n in range(1, 30)] + [""]), encoding="utf-8"
    )

    diff = parse_unified_diff(_git(repo, "diff"))[0]

    assert len(diff.hunks) == 2
    assert diff.removed_lines == ["---", "---"]
    assert _rows_are_counted(diff)


def test_rename_contract_is_unchanged(repo: Path) -> None:
    _commit(repo, "AGENTS.md", "One.\n---\nTwo.\n")
    _git(repo, "mv", "AGENTS.md", "CLAUDE.md")
    (repo / "CLAUDE.md").write_text("One.\nTwo.\n", encoding="utf-8")
    _git(repo, "add", "-A")

    diff = parse_unified_diff(_git(repo, "diff", "--cached", "-M"))[0]

    assert diff.is_rename
    assert (diff.old_path, diff.new_path) == ("AGENTS.md", "CLAUDE.md")
    assert diff.removed_lines == ["---"]
    assert _rows_are_counted(diff)


def test_no_newline_marker_consumes_no_row_budget(repo: Path) -> None:
    _commit(repo, "AGENTS.md", "One.\n---")
    (repo / "AGENTS.md").write_text("One.\n----", encoding="utf-8")

    raw = _git(repo, "diff")
    diff = parse_unified_diff(raw)[0]

    assert "\\ No newline at end of file" in raw
    assert diff.removed_lines == ["---"]
    assert diff.added_lines == ["----"]
    assert _rows_are_counted(diff)
    assert all(kind in {" ", "+", "-"} for kind, _ in diff.hunks[0].lines)


def test_new_and_deleted_files_keep_their_whole_side(repo: Path) -> None:
    _commit(repo, "keep.md", "anchor\n")
    (repo / "AGENTS.md").write_text("---\nprose\n", encoding="utf-8")
    _git(repo, "add", "-A")
    added = parse_unified_diff(_git(repo, "diff", "--cached"))
    added_file = next(f for f in added if f.path == "AGENTS.md")

    assert added_file.is_new
    assert added_file.added_lines == ["---", "prose"]
    assert _rows_are_counted(added_file)

    _git(repo, "commit", "-qm", "add agents")
    _git(repo, "rm", "-q", "AGENTS.md")
    deleted = parse_unified_diff(_git(repo, "diff", "--cached"))[0]

    assert deleted.is_deleted
    assert deleted.removed_lines == ["---", "prose"]
    assert _rows_are_counted(deleted)


# --- malformed input Git would not produce ---------------------------------


def _handwritten(hunk_header: str, *rows: str, path: str = "AGENTS.md") -> str:
    head = (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"{hunk_header}\n"
    )
    return head + "".join(row + "\n" for row in rows)


def test_a_truncated_hunk_is_still_incomplete() -> None:
    """Under-supplied rows must not become a successful comparison."""

    diff = parse_unified_diff(
        _handwritten("@@ -1,3 +1,2 @@", " One.", " Two.")
    )[0]

    assert not _rows_are_counted(diff)
    assert not instruction_pair_is_complete(
        diff, ResolvedFileText("One.\n---\nTwo.\n", "One.\nTwo.\n", "fixture", None, None)
    )


def test_an_over_declared_hunk_does_not_swallow_the_next_file() -> None:
    """A hunk that owes rows still ends at the next file header, so the
    second file is parsed and the short hunk stays refusable."""

    text = _handwritten("@@ -1,9 +1,9 @@", " One.") + _handwritten(
        "@@ -1,1 +1,1 @@", "-Two.", "+Three.", path="CLAUDE.md"
    )

    files = {f.path: f for f in parse_unified_diff(text)}

    assert set(files) == {"AGENTS.md", "CLAUDE.md"}
    assert not _rows_are_counted(files["AGENTS.md"])
    assert files["CLAUDE.md"].removed_lines == ["Two."]
    assert files["CLAUDE.md"].added_lines == ["Three."]
    assert _rows_are_counted(files["CLAUDE.md"])


def test_an_over_declared_hunk_does_not_swallow_the_next_hunk_header() -> None:
    text = _handwritten("@@ -1,9 +1,9 @@", " One.", "@@ -5,1 +5,1 @@", "-Two.", "+Three.")

    diff = parse_unified_diff(text)[0]

    assert len(diff.hunks) == 2
    assert [h.old_start for h in diff.hunks] == [1, 5]
    assert not _rows_are_counted(diff)


def test_a_header_path_that_disagrees_with_the_diff_line_still_refuses() -> None:
    text = (
        "diff --git a/AGENTS.md b/AGENTS.md\n"
        "--- a/OTHER.md\n"
        "+++ b/AGENTS.md\n"
        "@@ -1,1 +1,1 @@\n"
        "-One.\n"
        "+Two.\n"
    )

    diff = parse_unified_diff(text)[0]

    assert diff.old_path == "\0invalid-diff-path"


def test_a_file_header_after_an_exhausted_hunk_is_still_a_header() -> None:
    """Once the budget is spent the parser is outside the hunk again, so a
    second file's headers are read as headers."""

    text = _handwritten("@@ -1,1 +1,1 @@", "-One.", "+Two.") + _handwritten(
        "@@ -1,1 +1,1 @@", "-Three.", "+Four.", path="CLAUDE.md"
    )

    files = {f.path: f for f in parse_unified_diff(text)}

    assert set(files) == {"AGENTS.md", "CLAUDE.md"}
    assert all(_rows_are_counted(f) for f in files.values())
