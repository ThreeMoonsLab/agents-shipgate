"""#662: every maintained entry route names the same host-capability change.

Scripted engineering tests. Each case drives a documented command against an
isolated fixture repository, parses the real machine output, and compares what
it names. They establish that the installation, instructions and machine output
agree. They are not evidence that a real Claude Code, Codex or Cursor session
chooses to run these commands unprompted; that is the provider experiment #662
keeps separate.

The fixture is the widening shape the original study used: a wildcard shell
rule, a wildcard fetch rule, a new MCP server and `contents: write`, with a
denial removed.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.discovery.agent_instructions.renderers import render_agents_md
from agents_shipgate.cli.main import app
from agents_shipgate.mcp_server.server import shipgate_check

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
WORKFLOW = (
    "on: [push]\npermissions:\n  contents: read\njobs:\n  test:\n"
    "    runs-on: ubuntu-latest\n    steps:\n      - run: echo fixture\n"
)
#: What every route must name for the widening fixture, as (subject file, value).
WIDENING = {
    (".claude/settings.json", "Bash(*)"),
    (".claude/settings.json", "WebFetch(*)"),
    (".claude/settings.json", "Bash(rm *)"),
    (".mcp.json", "postgres"),
    (".github/workflows/ci.yml", "contents: write"),
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "host"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write(root, ".gitignore", "agents-shipgate-reports/\n")
    _write(root, ".claude/settings.json", {"permissions": {"allow": [], "deny": ["Bash(rm *)"]}})
    _write(root, ".mcp.json", {"mcpServers": {}})
    _write(root, ".github/workflows/ci.yml", WORKFLOW)
    _commit(root, "base")
    _git(root, "checkout", "-q", "-b", "change")
    return root


def _widen(repo: Path) -> None:
    _write(repo, ".claude/settings.json", {"permissions": {"allow": ["Bash(*)", "WebFetch(*)"], "deny": []}})
    _write(repo, ".mcp.json", {"mcpServers": {"postgres": {"command": "fixture-postgres"}}})
    _write(repo, ".github/workflows/ci.yml", WORKFLOW.replace("contents: read", "contents: write"))
    _commit(repo, "widen")


def _named(rows: list[dict]) -> set[tuple[str, str]]:
    """(file, value) pairs a route names, with `check`'s argument redaction undone for matching."""

    named = set()
    for row in rows:
        file = str(row["subject"]).split(" ", 1)[1]
        value = row["after"] if row["after"] != "—" else row["before"]
        if value == "Bash(<redacted-arguments>)":
            value = "Bash(rm *)"
        if file.startswith(".github/workflows/"):
            # A workflow row renders its whole authority ("write, test: contents:
            # write, on: push"); what must be named is the widened scope.
            value = "contents: write" if "contents: write" in value else value
        named.add((file, value))
    return named


def _invoke_json(args: list[str]) -> dict:
    result = CliRunner().invoke(app, args)
    return json.loads(result.output)


def _diff(repo: Path, base: str = "main") -> dict:
    return _invoke_json(["diff", "--workspace", str(repo), "--base", base, "--json"])


def _check(repo: Path, base: str = "main", head: str = "HEAD") -> dict:
    return _invoke_json(
        ["check", "--workspace", str(repo), "--base", base, "--head", head, "--format", "agent-boundary-json"]
    )


def _verify(repo: Path, base: str = "main", head: str = "HEAD") -> dict:
    return _invoke_json(
        ["verify", "--preview", "--workspace", str(repo), "--base", base, "--head", head, "--json"]
    )["host_comparison"]


def _mcp(repo: Path, base: str = "main") -> dict:
    return shipgate_check(workspace=str(repo), diff_text=_git(repo, "diff", base, "HEAD") + "\n")


# --- 1-4: the same change, named by every route ------------------------------


def test_1_diff_names_the_widening(repo: Path) -> None:
    _widen(repo)
    payload = _diff(repo)

    assert payload["comparison_status"] == "comparable"
    assert _named(payload["rows"]) == WIDENING


def test_2_check_names_the_same_change(repo: Path) -> None:
    _widen(repo)
    payload = _check(repo)

    assert payload["comparison_status"] == "comparable"
    assert _named(payload["rows"]) == WIDENING


def test_3_verify_pr_route_names_the_same_change(repo: Path) -> None:
    _widen(repo)
    comparison = _verify(repo)

    assert comparison["comparison_status"] == "comparable"
    assert _named(comparison["rows"]) == WIDENING


def test_4_mcp_check_names_the_same_change(repo: Path) -> None:
    _widen(repo)
    payload = _mcp(repo)

    assert payload["comparison_status"] == "comparable"
    assert _named(payload["rows"]) == WIDENING


# --- 5: the human rendering carries the same rows ----------------------------


def test_5_diff_text_shows_every_widening_value(repo: Path) -> None:
    _widen(repo)
    text = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "main"]).output

    for _file, value in WIDENING:
        assert value in text, f"text output does not name {value!r}"


# --- 6-9: controls that must not read as safety ------------------------------


def test_6_covered_no_change_is_zero_rows_on_every_route(repo: Path) -> None:
    for name, payload in {
        "diff": _diff(repo, base="HEAD"),
        "check": _check(repo, base="HEAD", head="HEAD"),
        "verify": _verify(repo, base="HEAD", head="HEAD"),
    }.items():
        assert payload["comparison_status"] == "comparable", name
        assert payload["rows"] == [], name


def test_7_a_narrowing_is_named_and_never_marked_as_expanding(repo: Path) -> None:
    _widen(repo)
    _git(repo, "branch", "-f", "widened", "HEAD")
    _write(repo, ".claude/settings.json", {"permissions": {"allow": ["WebFetch(*)"], "deny": []}})
    _commit(repo, "narrow")

    payload = _diff(repo, base="widened")

    assert payload["comparison_status"] == "comparable"
    assert [(row["direction"], row["before"], row["expands"]) for row in payload["rows"]] == [
        ("removed", "Bash(*)", False)
    ]


def test_8_malformed_input_is_incomparable_on_every_route(repo: Path) -> None:
    _widen(repo)
    _write(repo, ".mcp.json", "{broken")
    _commit(repo, "break")

    for name, payload in {"diff": _diff(repo), "check": _check(repo), "verify": _verify(repo)}.items():
        assert payload["comparison_status"] == "incomparable", name
        assert payload["rows"] == [], name
        assert payload["incomparable_reasons"], name


def test_9_a_refused_boundary_link_names_its_refusal(repo: Path) -> None:
    _widen(repo)
    _write(repo, "AGENTS.md", "# agents\n")
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    _commit(repo, "link instructions")

    payload = _diff(repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["rows"] == []
    assert payload["incomparable_reasons"]


# --- 10-12: repeat use and authority -----------------------------------------


def test_10_a_second_change_is_compared_not_remembered(repo: Path) -> None:
    _write(repo, ".claude/settings.json", {"permissions": {"allow": ["WebFetch(*)"], "deny": ["Bash(rm *)"]}})
    _commit(repo, "first change")
    first = _check(repo)
    _write(repo, ".mcp.json", {"mcpServers": {"postgres": {"command": "fixture-postgres"}}})
    _commit(repo, "second change")
    second = _check(repo)

    assert _named(first["rows"]) == {(".claude/settings.json", "WebFetch(*)")}
    assert _named(second["rows"]) == {(".claude/settings.json", "WebFetch(*)"), (".mcp.json", "postgres")}


def test_11_a_named_widening_grants_no_authority(repo: Path) -> None:
    _widen(repo)
    payload = _check(repo)

    assert payload["rows"]
    control = payload["control"]
    assert control["state"] != "complete"
    assert control["permissions"]["merge"] is False
    assert control["permissions"]["report_complete"] is False


def test_12_the_documented_command_runs_as_printed(repo: Path) -> None:
    """The generated AGENTS.md block's `shipgate diff` line, executed as written."""

    _widen(repo)
    line = next(
        line.strip() for line in render_agents_md().splitlines() if line.strip().startswith("shipgate diff")
    )
    argv = shlex.split(line)
    assert argv[:2] == ["shipgate", "diff"]
    args = [*argv[1:], "--base", "main", "--json"]
    args[args.index(".")] = str(repo)

    payload = _invoke_json(args)

    assert payload["comparison_status"] == "comparable"
    assert _named(payload["rows"]) == WIDENING
