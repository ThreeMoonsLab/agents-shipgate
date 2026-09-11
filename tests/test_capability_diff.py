"""#651: `shipgate diff` — what this change does to the agent's authority.

The engine already decided this. `audit --host --drift` compares two host
inventories and names every typed grant change; it just needed a baseline
someone had committed in advance and reachable from the branch. `diff`
supplies the other side from Git instead, so the same comparator answers
without a manifest, a committed baseline, or a second checkout.

The cases that matter most here are the ones proving the rows are
*projected* rather than invented — a second opinion about the same change
is the defect this command must not become.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import (
    ABSENT,
    capability_diff_rows,
)

runner = CliRunner()

BASE_SETTINGS = '{"permissions": {"allow": ["Bash(pytest *)", "Read(**)"]}}'
WIDE_SETTINGS = '{"permissions": {"allow": ["Bash(*)", "Read(**)", "WebFetch(*)"]}}'
BASE_MCP = '{"mcpServers": {"fs": {"command": "npx", "args": ["server-filesystem"]}}}'
WIDE_MCP = (
    '{"mcpServers": {"fs": {"command": "npx", "args": ["server-filesystem"]}, '
    '"postgres": {"command": "npx", "args": ["server-postgres"], '
    '"env": {"DATABASE_URL": "postgres://x"}}}}'
)
BASE_WORKFLOW = (
    "name: ci\non: [pull_request]\npermissions:\n  contents: read\n"
    "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n"
)
WIDE_WORKFLOW = (
    "name: ci\non: [pull_request_target]\npermissions:\n  contents: write\n"
    "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    (workspace / ".claude").mkdir(parents=True)
    (workspace / ".github" / "workflows").mkdir(parents=True)
    (workspace / ".claude" / "settings.json").write_text(BASE_SETTINGS, encoding="utf-8")
    (workspace / ".mcp.json").write_text(BASE_MCP, encoding="utf-8")
    (workspace / ".github" / "workflows" / "ci.yml").write_text(
        BASE_WORKFLOW, encoding="utf-8"
    )
    (workspace / "README.md").write_text("# app\n", encoding="utf-8")
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "config", "user.name", "Test")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    return workspace


def _widen(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feat/widen")
    (repo / ".claude" / "settings.json").write_text(WIDE_SETTINGS, encoding="utf-8")
    (repo / ".mcp.json").write_text(WIDE_MCP, encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        WIDE_WORKFLOW, encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "widen the agent's authority")


def _diff(repo: Path, *extra: str):
    return runner.invoke(app, ["diff", "--workspace", str(repo), *extra])


def _rows(repo: Path) -> list[dict]:
    result = _diff(repo, "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["rows"]


# --- the reviewer's question -----------------------------------------------


def test_the_three_widenings_are_named_without_a_manifest_or_baseline(
    repo: Path,
) -> None:
    _widen(repo)

    rows = _rows(repo)
    afters = {row["after"] for row in rows}

    assert "Bash(*)" in afters
    assert "WebFetch(*)" in afters
    assert "postgres" in afters
    assert any("pull_request_target" in row["after"] for row in rows)
    assert not (repo / "shipgate.yaml").exists()
    assert not (repo / ".agents-shipgate").exists()


def test_each_row_answers_what_why_and_how_severe(repo: Path) -> None:
    _widen(repo)

    for row in _rows(repo):
        assert row["subject"], row
        assert row["before"] and row["after"], row
        assert row["direction"] in {"added", "removed", "widened", "changed"}, row
        assert row["why"] and len(row["why"]) > 10, row
        assert row["severity"] in {"critical", "high", "medium", "low"}, row


def test_the_most_severe_change_is_read_first(repo: Path) -> None:
    _widen(repo)

    severities = [row["severity"] for row in _rows(repo)]

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert severities == sorted(severities, key=lambda value: order[value])


def test_a_workflow_row_names_both_sides_of_its_authority(repo: Path) -> None:
    """A workflow has no single name, so rendering its `kind` on both sides
    produced "workflow → workflow" — a row that says nothing."""

    _widen(repo)

    workflow = next(row for row in _rows(repo) if "workflows" in row["subject"])

    assert workflow["before"] != workflow["after"]
    assert "read" in workflow["before"]
    assert "write" in workflow["after"]
    assert "pull_request_target" in workflow["after"]


def test_a_benign_change_produces_no_rows(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "docs")
    (repo / "README.md").write_text("# app\n\nmore docs\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs only")

    result = _diff(repo)

    assert result.exit_code == 0
    assert "No change to what the agent may do." in result.output
    assert _rows(repo) == []


def test_uncommitted_work_counts(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feat/uncommitted")
    (repo / ".claude" / "settings.json").write_text(WIDE_SETTINGS, encoding="utf-8")

    assert any(row["after"] == "Bash(*)" for row in _rows(repo))


# --- projection, not a second opinion --------------------------------------


def test_every_row_field_is_read_from_the_payload() -> None:
    """Severity and widening are the engine's words. If this module ever
    computes either, the two surfaces can disagree about one change — the
    defect the distribution registry exists to prevent."""

    payload = {
        "comparison_status": "comparable",
        "expansion_signals": ["wildcard_allow_added: claude-code:Bash(*)"],
        "changes": [
            {
                "baseline": None,
                "current": {
                    "host": "claude-code",
                    "source": ".claude/settings.json",
                    "kind": "permission_rule",
                    "rule": "Bash(*)",
                    "access": "admin",
                    "risk": "critical",
                    "wildcard": True,
                    "disposition": "allow",
                },
            },
            {
                "baseline": None,
                "current": {
                    "host": "claude-code",
                    "source": ".claude/settings.json",
                    "kind": "permission_rule",
                    "rule": "Read(docs/**)",
                    "access": "read",
                    # A severity the module would never choose for a wildcard
                    # read: it must still come out as the engine said.
                    "risk": "low",
                    "wildcard": True,
                    "disposition": "allow",
                },
            },
        ],
    }

    rows = {row.after: row for row in capability_diff_rows(payload)}

    assert rows["Bash(*)"].severity == "critical"
    assert rows["Read(docs/**)"].severity == "low"
    # Only the signalled row is an expansion, though both are wildcard adds.
    assert rows["Bash(*)"].expands is True
    assert rows["Read(docs/**)"].expands is False


def test_widened_requires_the_engine_to_have_said_so() -> None:
    """A change on both sides is `changed` until the engine calls it an
    expansion. Guessing narrowing needs the pattern lattice in #657."""

    change = {
        "baseline": {
            "host": "claude-code", "source": ".claude/settings.json",
            "kind": "permission_rule", "rule": "Bash(npm *)", "risk": "high",
        },
        "current": {
            "host": "claude-code", "source": ".claude/settings.json",
            "kind": "permission_rule", "rule": "Bash(npm test:*)", "risk": "high",
        },
    }

    quiet = capability_diff_rows(
        {"comparison_status": "comparable", "expansion_signals": [], "changes": [change]}
    )
    signalled = capability_diff_rows(
        {
            "comparison_status": "comparable",
            "expansion_signals": ["wildcard_allow_added: claude-code:Bash(npm test:*)"],
            "changes": [change],
        }
    )

    assert quiet[0].direction == "changed"
    assert quiet[0].expands is False
    assert signalled[0].direction == "widened"


def test_a_removal_is_never_reported_as_an_expansion() -> None:
    rows = capability_diff_rows(
        {
            "comparison_status": "comparable",
            "expansion_signals": ["wildcard_allow_added: claude-code:Bash(*)"],
            "changes": [
                {
                    "baseline": {
                        "host": "claude-code", "source": ".claude/settings.json",
                        "kind": "permission_rule", "rule": "Bash(*)", "risk": "critical",
                    },
                    "current": None,
                }
            ],
        }
    )

    assert rows[0].direction == "removed"
    assert rows[0].expands is False
    assert rows[0].after == ABSENT


# --- refusals --------------------------------------------------------------


def test_an_absent_base_ref_is_refused_by_name(repo: Path) -> None:
    result = _diff(repo, "--base", "no-such-ref")

    assert result.exit_code == 2
    assert "not available locally" in result.output
    assert "never fetches" in result.output


def test_an_unrelated_history_is_refused_rather_than_compared(
    repo: Path, tmp_path: Path
) -> None:
    _git(repo, "checkout", "-q", "--orphan", "unrelated")
    _git(repo, "commit", "-qm", "orphan", "--allow-empty")

    result = _diff(repo, "--base", "main")

    assert result.exit_code == 2
    assert "No merge base" in result.output


def test_the_output_states_what_it_did_not_establish(repo: Path) -> None:
    """A capability diff is configuration, not behaviour, and says so."""

    _widen(repo)

    output = _diff(repo).output

    assert "Static configuration only" in output
    assert "No verdict is implied" in output
    assert _diff(repo, "--json").output.count('"static_analysis_only": true') == 1
