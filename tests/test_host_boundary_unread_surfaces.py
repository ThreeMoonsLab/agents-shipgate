"""The support page names the host surfaces no adapter reads.

A composite action (#701), a hook-run script (#702), a named reusable-workflow
secret (#693) and a remote MCP server's URL path (#772) can each change what
runs, or what it can reach, with no row. Until a reader exists, the published
boundary has to say so, or advertised coverage would exceed measured behavior.

The behavioral cases pin that silence on the page's own examples. The day a
reader starts producing a row for one of them, its case fails, and the page
entry has to leave in the same change. Each silent fixture also has a control
change the reader does see, so the silence cannot come from a fixture that is
never read at all.

A workflow step's remote action reference was on this list until #771 read it.
Its fixture now pins the row, and the page names the read and its limits.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

PAGE = Path(__file__).resolve().parents[1] / "docs" / "host-boundary-support.md"
runner = CliRunner()


def _section() -> str:
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("### Known unread surfaces")
    end = text.find("\n#", start + 1)
    return " ".join(text[start : end if end != -1 else len(text)].split())


def _bullets() -> str:
    """Only the list of unread surfaces, not the prose that follows it."""

    section = _section()
    return section[: section.index("Review changes to those files")]


def test_composite_actions_are_named_as_unread() -> None:
    section = _bullets()
    assert ".github/actions/<name>/action.yml" in section
    assert "(#701)" in section


def test_hook_run_scripts_are_named_as_unread() -> None:
    section = _bullets()
    assert "script a hook command runs" in section
    assert "(#702)" in section


def test_step_action_references_are_no_longer_named_as_unread() -> None:
    assert "action reference of a workflow step" not in _bullets()
    assert "(#771)" not in _bullets()


def test_the_step_action_read_names_its_limits() -> None:
    section = _section()
    assert "A workflow step's remote action reference is read (#771)" in section
    # Only the remote slice is read: local composites stay #701's.
    assert "A local `./…` reference is not part of this read and stays unread (#701)" in section
    assert "listed as `unresolved` with its reason" in section
    assert "it never marks the row as widening" in section
    assert "a comparison of that changed workflow refuses" in section


def test_named_reusable_workflow_secrets_are_named_as_unread() -> None:
    section = _bullets()
    assert "Named secrets passed to a reusable workflow" in section
    assert "(#693)" in section


def test_mcp_url_paths_are_named_as_unread() -> None:
    section = _bullets()
    assert "path of a remote MCP server's URL" in section
    assert "(#772)" in section


def test_a_hook_row_is_not_described_as_proof_of_loading() -> None:
    section = _section()
    assert "A hook row states its loading basis (#714)" in section
    assert "No row is proof that a hook ran" in section
    # The three published bases and the limit that remains for each.
    assert "Selected by a plugin manifest" in section
    assert "Selected by nothing" in section
    assert "`access: unknown`" in section
    assert "Installation and enablement are never read" in section


def test_link_refusals_are_named_on_the_page() -> None:
    text = " ".join(PAGE.read_text(encoding="utf-8").split())
    assert "refuses the whole comparison" in text
    assert "(#688)" in text


_WORKFLOW_STEP = """on: pull_request
permissions:
  contents: {access}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: {ref}
"""

_REUSABLE_CALL = """on: pull_request
permissions:
  contents: read
jobs:
  build:
    uses: org/shared/.github/workflows/build.yml@v1
    secrets:
{secrets}
"""

_REMOTE_MCP = '{{"mcpServers":{{"remote":{{"type":"http","url":"https://{host}/{path}"}}}}}}\n'

_PINNED = "11bd71901bbe5b1630ceea73d27597364c9af683"

# (case, file, base, silent head, seen head)
_CASES = [
    (
        "local composite action reference (#701)",
        ".github/workflows/ci.yml",
        _WORKFLOW_STEP.format(access="read", ref="./.github/actions/build"),
        _WORKFLOW_STEP.format(access="read", ref="./.github/actions/deploy"),
        _WORKFLOW_STEP.format(access="write", ref="./.github/actions/build"),
    ),
    (
        "named reusable-workflow secret (#693)",
        ".github/workflows/ci.yml",
        _REUSABLE_CALL.format(secrets="      token: ${{ secrets.READ_TOKEN }}"),
        _REUSABLE_CALL.format(secrets="      token: ${{ secrets.ADMIN_TOKEN }}"),
        _REUSABLE_CALL.format(secrets="      inherit_marker: x").replace(
            "    secrets:\n      inherit_marker: x", "    secrets: inherit"
        ),
    ),
    (
        "remote MCP server URL path (#772)",
        ".mcp.json",
        _REMOTE_MCP.format(host="mcp.example.com", path="read"),
        _REMOTE_MCP.format(host="mcp.example.com", path="admin"),
        _REMOTE_MCP.format(host="mcp.other.example", path="read"),
    ),
]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _diff_after(tmp_path: Path, file: str, base: str, head: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    target = repo / file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    target.write_text(head, encoding="utf-8")

    result = runner.invoke(
        app, ["diff", "--workspace", str(repo), "--base", "HEAD", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["comparison_status"] == "comparable", payload
    assert payload["incomparable_reasons"] == [], payload
    assert payload["unchanged_limits"] == [], payload
    return payload


@pytest.mark.parametrize(
    ("case", "file", "base", "silent", "seen"),
    _CASES,
    ids=[case[0] for case in _CASES],
)
def test_each_named_unread_surface_is_still_silent(
    tmp_path: Path, case: str, file: str, base: str, silent: str, seen: str
) -> None:
    payload = _diff_after(tmp_path, file, base, silent)

    assert payload["rows"] == [], (
        f"{case} now produces a row. The reader covers it: remove its entry from "
        "docs/host-boundary-support.md § Known unread surfaces in this change."
    )


@pytest.mark.parametrize(
    ("case", "file", "base", "silent", "seen"),
    _CASES,
    ids=[case[0] for case in _CASES],
)
def test_each_silent_fixture_is_read_when_a_covered_field_changes(
    tmp_path: Path, case: str, file: str, base: str, silent: str, seen: str
) -> None:
    payload = _diff_after(tmp_path, file, base, seen)

    assert len(payload["rows"]) == 1, (case, payload["rows"])


def test_the_formerly_silent_step_reference_fixture_now_produces_its_row(tmp_path: Path) -> None:
    """The page's own #771 example: a pinned SHA moved to `@main`."""

    payload = _diff_after(
        tmp_path,
        ".github/workflows/ci.yml",
        _WORKFLOW_STEP.format(access="read", ref=f"actions/checkout@{_PINNED}"),
        _WORKFLOW_STEP.format(access="read", ref="actions/checkout@main"),
    )

    row, = payload["rows"]
    assert row["subject"] == "github .github/workflows/ci.yml"
    assert f"test/steps[0]: uses actions/checkout@{_PINNED}" in row["before"]
    assert "test/steps[0]: uses actions/checkout@main" in row["after"]
    assert row["direction"] == "changed"
    assert row["expands"] is False
    assert "test/steps[0]" in row["why"]


def test_an_unchanged_step_reference_fixture_stays_quiet(tmp_path: Path) -> None:
    unchanged = _WORKFLOW_STEP.format(access="read", ref=f"actions/checkout@{_PINNED}")
    payload = _diff_after(
        tmp_path, ".github/workflows/ci.yml", unchanged, unchanged + "# a comment\n"
    )

    assert payload["rows"] == []
