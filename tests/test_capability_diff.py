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
import shlex
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
    assert "No static host-grant changes detected." in result.output
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
            "disposition": "allow", "wildcard": True,
        },
    }

    quiet = capability_diff_rows(
        {"comparison_status": "comparable", "expansion_signals": [], "changes": [change]}
    )
    signalled = capability_diff_rows(
        {
            "comparison_status": "comparable",
            "expansion_signals": ["wildcard_allow_changed: claude-code:Bash(npm test:*)"],
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


@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("agent_mode", ["0", "1"])
def test_a_shallow_base_this_clone_does_not_have_names_a_recovery(
    repo: Path, tmp_path: Path, depth: int, json_output: bool, agent_mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovery is for a base the clone genuinely lacks.

    It used to fire on *any* shallow checkout. The base tree is read through
    a scoped archive that packs the tree and walks no ancestry (#686), so a
    base the clone already holds is readable at `--depth 1`; sending that
    caller to `git fetch --unshallow` is a repair for a problem they do not
    have. `--base HEAD` — which this case used to pass — is now answerable
    and is covered below.
    """
    # Hosted CI enables Rich color; the recovery must still be one copyable line.
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "60")
    for index in range(2):
        (repo / "README.md").write_text(f"# revision {index}\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-qm", f"history {index}")
    clone = tmp_path / "shallow checkout"
    _git(tmp_path, "clone", "--quiet", "--depth", str(depth), repo.as_uri(), str(clone))
    (clone / ".claude" / "settings.json").write_text(WIDE_SETTINGS, encoding="utf-8")
    # A commit this shallow clone does not have: the graft's parent is the
    # first thing past the boundary, and naming it by the graft is exact.
    absent = _git_out(clone, "rev-parse", (clone / ".git" / "shallow").read_text().split()[0])
    args = ["diff", "--workspace", str(clone), "--base", f"{absent}~1"]
    if json_output:
        args.append("--json")

    result = runner.invoke(app, args, env={"AGENTS_SHIPGATE_AGENT_MODE": agent_mode})

    assert result.exit_code == 2, result.output
    assert "This checkout is shallow" in result.output
    assert "fetch-depth: 0" in result.output
    assert "Traceback" not in result.output
    recovery = ["git", "-C", str(clone), "fetch", "--unshallow"]
    errors = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    if agent_mode == "1":
        assert len(errors) == 1
        assert errors[0]["error"] == "config_error"
        action = errors[0]["next_actions"][0]
        assert shlex.split(action["command"]) == recovery
        recovery = shlex.split(action["command"])
    else:
        assert errors == []
        assert any(shlex.join(recovery) in line for line in result.stderr.splitlines())
    # Follow the published action from outside the checkout. This must restore
    # the actual comparison, without init, policy edits or a wrapper fetch.
    subprocess.run(recovery, cwd=tmp_path, check=True, capture_output=True)
    recovered = _diff(clone, "--base", f"{absent}~1", "--json")
    assert recovered.exit_code == 0, recovered.output
    assert any(row["after"] == "Bash(*)" for row in json.loads(recovered.output)["rows"])


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("depth", [1, 2])
def test_a_shallow_clone_compares_against_a_base_it_already_holds(
    repo: Path, tmp_path: Path, depth: int
) -> None:
    """`actions/checkout` defaults to `fetch-depth: 1`.

    Refusing every shallow checkout sent that default to `git fetch
    --unshallow` for a comparison the tool can answer from objects it holds.
    Measured on five public repositories: the scoped archive read all five
    at `--depth 3` in 0.5-2.1 s while the command refused all five.
    """

    for index in range(3):
        (repo / "README.md").write_text(f"# revision {index}\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-qm", f"history {index}")
    clone = tmp_path / "shallow checkout"
    _git(tmp_path, "clone", "--quiet", "--depth", str(depth), repo.as_uri(), str(clone))
    (clone / ".claude" / "settings.json").write_text(WIDE_SETTINGS, encoding="utf-8")

    result = _diff(clone, "--base", "HEAD", "--json")

    assert result.exit_code == 0, result.output
    assert "This checkout is shallow" not in result.output
    assert any(row["after"] == "Bash(*)" for row in json.loads(result.output)["rows"])


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


@pytest.mark.parametrize("disposition", ["deny", "ask"])
def test_removing_a_restriction_preserves_the_engine_expansion(disposition: str) -> None:
    from agents_shipgate.core.host_grants import host_grant_expansion_signals

    changes = [{"baseline": {"host": "claude-code", "source": ".claude/settings.json",
                "kind": "permission_rule", "disposition": disposition,
                "rule": "Bash(*)", "risk": "low"}, "current": None}]
    rows = capability_diff_rows({"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)})
    assert rows[0].expands is True
    assert rows[0].direction == "removed"
    assert "removes a" in rows[0].why


def test_hook_expansion_is_bound_by_host_and_source() -> None:
    from agents_shipgate.core.host_grants import host_grant_expansion_signals

    changes = [{"baseline": None, "current": {"host": "claude-code", "source": ".claude/settings.json",
                "kind": "hook", "name": "PreToolUse", "risk": "high"}}]
    rows = capability_diff_rows({"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)})
    assert rows[0].expands is True


def test_missing_default_ref_requires_an_explicit_comparison(repo: Path) -> None:
    _git(repo, "remote", "add", "origin", "https://example.invalid/unfetched.git")
    result = _diff(repo)
    assert result.exit_code == 2
    assert "No base ref could be detected" in result.output


def test_subdirectory_compares_the_same_repository_on_both_sides(repo: Path) -> None:
    nested = repo / "nested"
    nested.mkdir()
    payload = json.loads(_diff(nested, "--base", "HEAD", "--json").output)
    assert payload["workspace"] == str(repo.resolve())
    assert payload["rows"] == []


def test_incomplete_base_returns_an_explicit_incomparable_result(repo: Path) -> None:
    (repo / ".mcp.json").write_text("{bad json", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "incomplete base")
    (repo / ".mcp.json").write_text(BASE_MCP, encoding="utf-8")
    result = _diff(repo, "--base", "HEAD", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == ["base_inventory_incomplete"]
    assert payload["rows"] == []


@pytest.mark.parametrize("base", ["", "--upload-pack=evil"])
def test_invalid_base_cannot_fall_back_or_become_an_option(repo: Path, base: str) -> None:
    result = _diff(repo, "--base", base)
    assert result.exit_code == 2
    assert "Base ref must be non-empty" in result.output


@pytest.mark.parametrize("json_output", [False, True])
def test_shallow_merge_cannot_select_an_older_visible_common_ancestor(
    repo: Path, tmp_path: Path, json_output: bool,
) -> None:
    # A -> B -> L -> H, H also has parent A; R has parent B. A shallow
    # graft at L hides B along H's first parent, but H's second exposes A.
    a = _git_out(repo, "rev-parse", "HEAD")
    (repo / ".claude" / "settings.json").write_text(WIDE_SETTINGS)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "B")
    b = _git_out(repo, "rev-parse", "HEAD")
    tree = _git_out(repo, "rev-parse", "HEAD^{tree}")
    left = _git_out(repo, "commit-tree", tree, "-p", b, "-m", "L")
    head = _git_out(repo, "commit-tree", tree, "-p", left, "-p", a, "-m", "H")
    right = _git_out(repo, "commit-tree", tree, "-p", b, "-m", "R")
    _git(repo, "update-ref", "refs/heads/head", head)
    _git(repo, "update-ref", "refs/heads/base", right)
    assert _git_out(repo, "merge-base", head, right) == b
    clone = tmp_path / "partial history"
    _git(tmp_path, "clone", "--quiet", "--depth", "2", "--branch", "head", repo.as_uri(), str(clone))
    _git(clone, "fetch", "--quiet", "--depth", "3", "origin", "base")
    assert _git_out(clone, "merge-base", "HEAD", "FETCH_HEAD") == a
    # Objects can be present through R even though L's parent edge is cut.
    assert _git_out(clone, "cat-file", "-t", b) == "commit"
    from agents_shipgate.cli.verify.host_comparison import compare_host_refs
    with pytest.raises(ValueError, match="Shallow history"):
        compare_host_refs(workspace=clone, base="FETCH_HEAD", head="HEAD",
                          auto_base=False, config_relative=Path("shipgate.yaml"))
    # A visible ancestor remains the exact base even with another graft.
    visible = _diff(clone, "--base", left, "--json")
    assert visible.exit_code == 0, visible.output
    assert json.loads(visible.output)["rows"] == []
    result = _diff(clone, "--base", "FETCH_HEAD", *(["--json"] if json_output else []))
    assert result.exit_code == 2, result.output
    assert "fetch" in result.output and "--unshallow" in result.output
    _git(clone, "fetch", "--quiet", "--unshallow", "origin")
    result = _diff(clone, "--base", right, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["rows"] == []
