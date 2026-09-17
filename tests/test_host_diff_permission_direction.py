"""#816: every route reads the direction of three ordinary permission edits alike.

Each case is the issue's reproduction: a real two-commit repository, one
Claude Code settings edit, and the documented commands run against it. What is
pinned is what a reviewer is shown: the `diff` summary and its ⚠ rows, the
`--json` rows, `check`'s decision, violations and rows, `verify`'s
`host_comparison`, and drift's `expansion_signals` against a baseline saved at
the base commit.

* `colon`: `Bash(npm:*)` -> `Bash(npm test:*)`. Claude Code documents a trailing
  `:*` as another spelling of a trailing ` *`
  (https://code.claude.com/docs/en/permissions#wildcard-patterns), so this is
  the same narrowing as `space`, and must read the same.
* `moved`: `Bash(git log *)` moves from `deny` to `allow` in the edit that
  narrows `Bash(git status *)`. The move is a widening; the narrowing is not.
* `mcp`: an allow for one MCP tool, which the same page ("MCP") distinguishes
  from `mcp__<server>` and `mcp__<server>__*`, the whole-server grants.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
SETTINGS = ".claude/settings.json"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _invoke(args: list[str]) -> str:
    return CliRunner().invoke(app, args).output


def _repository(tmp_path: Path, base: dict, head: dict) -> Path:
    """A base commit with a saved host-grants baseline, then the edit."""

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n.agents-shipgate/\n", encoding="utf-8")
    (repo / SETTINGS).write_text(json.dumps(base), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _invoke(["audit", "--host", "--workspace", str(repo), "--save-baseline", "--json"])
    _git(repo, "checkout", "-q", "-b", "change")
    (repo / SETTINGS).write_text(json.dumps(head), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    return repo


def _routes(repo: Path) -> dict:
    workspace = ["--workspace", str(repo), "--base", "main"]
    text = _invoke(["diff", *workspace])
    diff = json.loads(_invoke(["diff", *workspace, "--json"]))
    check = json.loads(
        _invoke(["check", "--agent", "claude-code", *workspace, "--format", "agent-boundary-json"])
    )
    verify = json.loads(_invoke(["verify", "--preview", *workspace, "--head", "HEAD", "--json"]))
    drift = json.loads(_invoke(["audit", "--host", "--workspace", str(repo), "--drift", "--json"]))
    return {
        "text": text,
        "diff": diff,
        "check": check,
        "verify": verify["host_comparison"],
        "drift": drift,
    }


def _marked(rows: list[dict]) -> list[tuple[str, str, bool]]:
    """Every row as (direction, value, expands), in a route-independent order."""

    return sorted(
        (row["direction"], row["after"] if row["after"] != "—" else row["before"], row["expands"])
        for row in rows
    )


def _shape(rows: list[dict]) -> list[tuple[str, bool]]:
    """`check` redacts permission arguments, so it is compared by shape."""

    return sorted((row["direction"], row["expands"]) for row in rows)


#: (base settings, head settings, rows diff and verify show, ⚠ count,
#: check decision, check violations, drift expansion signals)
CASES = {
    "colon": (
        {"permissions": {"allow": ["Bash(npm:*)"]}},
        {"permissions": {"allow": ["Bash(npm test:*)"]}},
        [("added", "Bash(npm test:*)", False), ("removed", "Bash(npm:*)", False)],
        0,
        "allow",
        [],
        [],
    ),
    "colon_widened": (
        {"permissions": {"allow": ["Bash(npm test:*)"]}},
        {"permissions": {"allow": ["Bash(npm:*)"]}},
        [("added", "Bash(npm:*)", True), ("removed", "Bash(npm test:*)", False)],
        1,
        "require_review",
        ["SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED"],
        [
            "allow_rule_added: claude-code:Bash(npm:*)",
            "permission_widened: claude-code:Bash(npm test:*) -> Bash(npm:*)",
        ],
    ),
    # The space spelling and a mixed pair answer as they did before #816.
    "space": (
        {"permissions": {"allow": ["Bash(npm *)"]}},
        {"permissions": {"allow": ["Bash(npm test *)"]}},
        [("added", "Bash(npm test *)", False), ("removed", "Bash(npm *)", False)],
        0,
        "allow",
        [],
        [],
    ),
    "mixed": (
        {"permissions": {"allow": ["Bash(npm *)"]}},
        {"permissions": {"allow": ["Bash(npm test:*)"]}},
        [("added", "Bash(npm test:*)", False), ("removed", "Bash(npm *)", False)],
        0,
        "allow",
        [],
        [],
    ),
    "moved": (
        {"permissions": {"allow": ["Bash(git status *)"], "deny": ["Bash(git log *)"]}},
        {"permissions": {"allow": ["Bash(git status --short *)", "Bash(git log *)"]}},
        [
            ("added", "Bash(git log *)", True),
            ("added", "Bash(git status --short *)", False),
            ("removed", "Bash(git log *)", True),
            ("removed", "Bash(git status *)", False),
        ],
        2,
        "require_review",
        [
            "SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED",
            "SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED",
        ],
        [
            "allow_rule_added: claude-code:Bash(git log *)",
            "deny_rule_removed: claude-code:Bash(git log *)",
        ],
    ),
    "mcp": (
        {"permissions": {"allow": ["Read"]}},
        {"permissions": {"allow": ["Read", "mcp__github__get_issue"]}},
        [("added", "mcp__github__get_issue", True)],
        1,
        "require_review",
        ["SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED"],
        ["allow_rule_added: claude-code:mcp__github__get_issue"],
    ),
    "mcp_wildcard_narrowed": (
        {"permissions": {"allow": ["mcp__github__*"]}},
        {"permissions": {"allow": ["mcp__github__get_issue"]}},
        [("added", "mcp__github__get_issue", False), ("removed", "mcp__github__*", False)],
        0,
        "allow",
        [],
        [],
    ),
    # `mcp__github` is every tool of the server, as `mcp__github__*` is.
    "mcp_server_narrowed": (
        {"permissions": {"allow": ["mcp__github"]}},
        {"permissions": {"allow": ["mcp__github__get_issue"]}},
        [("added", "mcp__github__get_issue", False), ("removed", "mcp__github", False)],
        0,
        "allow",
        [],
        [],
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_every_route_reads_the_same_direction(tmp_path: Path, name: str) -> None:
    base, head, rows, warnings, decision, violations, signals = CASES[name]
    routes = _routes(_repository(tmp_path, base, head))

    assert routes["diff"]["comparison_status"] == "comparable"
    assert _marked(routes["diff"]["rows"]) == rows
    assert _marked(routes["verify"]["rows"]) == rows
    assert _shape(routes["check"]["rows"]) == sorted((direction, expands) for direction, _, expands in rows)
    assert routes["check"]["decision"] == decision
    assert sorted({item["check_id"] for item in routes["check"]["violations"]}) == violations
    assert routes["drift"]["comparison_status"] == "comparable"
    assert routes["drift"]["expansion_signals"] == signals

    summary = f"{len(rows)} change(s)" + (
        f", {warnings} widening what the agent may do (⚠)." if warnings else "."
    )
    assert summary in routes["text"], routes["text"]
    assert routes["text"].count("⚠ ") == warnings, routes["text"]


def test_one_mcp_tool_is_not_worded_as_every_target(tmp_path: Path) -> None:
    base, head, *_ = CASES["mcp"]
    routes = _routes(_repository(tmp_path, base, head))

    [row] = routes["diff"]["rows"]
    assert (row["severity"], row["why"]) == ("medium", "runs without a prompt")
    assert "matches every target of this kind" not in routes["text"]
    assert "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW" not in json.dumps(routes["check"])


def test_a_whole_server_mcp_grant_still_blocks(tmp_path: Path) -> None:
    routes = _routes(
        _repository(
            tmp_path,
            {"permissions": {"allow": ["Read"]}},
            {"permissions": {"allow": ["Read", "mcp__github__*"]}},
        )
    )

    assert routes["check"]["decision"] == "block"
    assert "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW" in {
        item["check_id"] for item in routes["check"]["violations"]
    }
    [row] = routes["diff"]["rows"]
    assert row["why"] == "matches every target of this kind, without a prompt"
    assert routes["drift"]["expansion_signals"] == ["wildcard_allow_added: claude-code:mcp__github__*"]
