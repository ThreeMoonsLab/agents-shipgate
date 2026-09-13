"""#590: a host inventory never publishes credential-shaped bytes from a path.

The inventory already redacted configuration values and failed-input text, but
`artifacts[].path`, `grants[].source`, `grants[].path` and the coverage and
drift projections built from them carried the exact path. A directory or file
named with something token-shaped leaked through every one of them.

The sentinel below is synthetic: GitHub's token shape with a counting body, so
no scanner mistakes it for a real credential and every assertion can search for
it verbatim.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_grants import host_audit_inventory, public_host_path

SENTINEL = "ghp_" + "1234567890abcdefghijklmnopqrstuvwxyzAB"
OTHER_SENTINEL = "ghp_" + "ZYXWVUTSRQPONMLKJIHGFEDCBA0987654321ab"
SKILL = f".claude/skills/deploy TOKEN={SENTINEL}/SKILL.md"
WORKFLOW = f".github/workflows/deploy TOKEN={SENTINEL}.yml"
FAILED_MCP = f"pkg/settings TOKEN={SENTINEL}/.mcp.json"
WORKFLOW_TEXT = (
    "on: [push]\npermissions:\n  contents: read\njobs:\n  t:\n"
    "    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write(repo: Path, name: str, value: str | bytes) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def make_repo(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    write(repo, ".gitignore", "agents-shipgate-reports/\n")
    for name, value in files.items():
        write(repo, name, value)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


def redacted(path: str) -> bool:
    """Either redaction marker the shared sanitizers write."""

    return "[REDACTED:" in path or "<redacted>" in path


def invoke(*args: str) -> str:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


SUCCESSFUL = {
    ".claude/settings.json": '{"permissions": {"allow": ["Bash(npm test)"]}}',
    SKILL: "---\nname: deploy\ndescription: Deploy helper.\n---\n\nBody.\n",
    WORKFLOW: WORKFLOW_TEXT,
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path, {**SUCCESSFUL, FAILED_MCP: b'{"mcpServers": {"a": "\xff\xfe"}}'})


# --- the direct reader --------------------------------------------------------


def test_the_reader_publishes_no_sentinel_for_successful_or_failed_sources(repo: Path) -> None:
    inventory = host_audit_inventory(repo)
    rendered = json.dumps(inventory)

    assert SENTINEL not in rendered
    paths = {item["path"] for item in inventory["artifacts"]}
    # Successful and failed sources are both published, redacted, not dropped.
    assert any(path.endswith("/SKILL.md") and redacted(path) for path in paths)
    assert any(path.endswith(".mcp.json") and redacted(path) for path in paths)
    failed = [item for item in inventory["artifacts"] if item["parse_status"] == "failed"]
    assert failed and all(redacted(item["path"]) for item in failed)
    assert all(SENTINEL not in path for item in inventory["host_coverage"] for path in item["sources_observed"])
    assert all(SENTINEL not in issue["source"] for issue in inventory["issues"])


def test_redaction_keeps_the_file_the_path_names(repo: Path) -> None:
    """The skill under a redacted directory is still read as a skill, not an unknown file."""

    inventory = host_audit_inventory(repo)
    skill = next(item for item in inventory["artifacts"] if item["path"].endswith("/SKILL.md"))

    assert skill["parse_status"] == "parsed"
    assert (skill.get("instruction_structure") or {}).get("status") == "structured"


def test_public_host_path_is_component_wise_and_collision_free() -> None:
    assert public_host_path(".claude/settings.json") == ".claude/settings.json"
    first = public_host_path(f"a/TOKEN={SENTINEL}/SKILL.md")
    second = public_host_path(f"a/TOKEN={OTHER_SENTINEL}/SKILL.md")

    assert SENTINEL not in first and OTHER_SENTINEL not in second
    assert first.startswith("a/") and first.endswith("/SKILL.md")
    assert first != second
    assert public_host_path(f"a/TOKEN={SENTINEL}/SKILL.md") == first


def test_two_sources_that_redact_alike_keep_their_own_identity(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {
        ".claude/settings.json": '{"permissions": {"allow": []}}',
        f"pkg/one TOKEN={SENTINEL}/.mcp.json": b"\xff",
        f"pkg/one TOKEN={OTHER_SENTINEL}/.mcp.json": b"\xfe",
        f".claude/skills/a TOKEN={SENTINEL}/SKILL.md": "---\nname: a\ndescription: A.\n---\n\nBody.\n",
        f".claude/skills/a TOKEN={OTHER_SENTINEL}/SKILL.md": "---\nname: b\ndescription: B.\n---\n\nBody.\n",
    })

    inventory = host_audit_inventory(repo)
    rendered = json.dumps(inventory)

    assert SENTINEL not in rendered and OTHER_SENTINEL not in rendered
    for kind in ("mcp", "instructions"):
        items = [item for item in inventory["artifacts"] if item["kind"] == kind and redacted(item["path"])]
        assert len(items) == 2
        assert len({item["path"] for item in items}) == 2
        assert len({item["artifact_id"] for item in items}) == 2
    redacted_issues = [issue for issue in inventory["issues"] if redacted(issue["source"])]
    assert len(redacted_issues) >= 2
    assert len({issue["issue_id"] for issue in redacted_issues}) == len(redacted_issues)
    assert len({issue["source"] for issue in redacted_issues}) == len(redacted_issues)


# --- the public outputs built from it ----------------------------------------


def test_audit_json_and_markdown_carry_no_sentinel(repo: Path) -> None:
    assert SENTINEL not in invoke("audit", "--host", "--workspace", str(repo), "--json")
    assert SENTINEL not in invoke("audit", "--host", "--workspace", str(repo))


def test_baseline_and_drift_carry_no_sentinel(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, SUCCESSFUL)
    invoke("audit", "--host", "--workspace", str(repo), "--save-baseline", "--json")
    saved = (repo / ".agents-shipgate" / "host-grants.json").read_text(encoding="utf-8")
    assert SENTINEL not in saved

    unchanged = json.loads(invoke("audit", "--host", "--workspace", str(repo), "--drift", "--json"))
    assert unchanged["comparison_status"] == "comparable"
    assert unchanged.get("artifact_changes", []) == []

    write(repo, WORKFLOW, WORKFLOW_TEXT.replace("contents: read", "contents: write"))
    write(repo, ".claude/settings.json", '{"permissions": {"allow": ["Bash(*)"]}}')
    drift = invoke("audit", "--host", "--workspace", str(repo), "--drift", "--json")
    payload = json.loads(drift)
    assert SENTINEL not in drift
    assert payload["expansion_signals"]


def test_diff_and_verify_host_comparison_carry_no_sentinel(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, SUCCESSFUL)
    git(repo, "checkout", "-q", "-b", "change")
    write(repo, WORKFLOW, WORKFLOW_TEXT.replace("contents: read", "contents: write"))
    write(repo, ".claude/settings.json", '{"permissions": {"allow": ["Bash(*)"]}}')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "widen")

    diff = json.loads(invoke("diff", "--workspace", str(repo), "--base", "main", "--json"))
    verifier = json.loads(invoke(
        "verify", "--preview", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--json",
    ))
    comparison = verifier["host_comparison"]

    assert diff["comparison_status"] == comparison["comparison_status"] == "comparable"
    assert any(row["after"] == "Bash(*)" for row in comparison["rows"])
    assert SENTINEL not in json.dumps(diff)
    assert SENTINEL not in json.dumps(comparison)


def test_issue_sources_that_would_display_alike_keep_their_own_label_and_id() -> None:
    """Control characters and the length bound are lossy too, so they leave a digest."""

    from agents_shipgate.core.host_grants import _inventory_issue
    from agents_shipgate.core.host_input_failure import MAX_FAILURE_TEXT

    def issue(source: str) -> dict:
        return _inventory_issue(kind="unreadable", host="claude-code", source=source, message="m", blocking=True)

    plain, tabbed = issue("pkg/a b/.mcp.json"), issue("pkg/a\tb/.mcp.json")
    assert plain["source"] == "pkg/a b/.mcp.json"
    assert "\t" not in tabbed["source"]
    assert tabbed["source"] != plain["source"] and tabbed["issue_id"] != plain["issue_id"]

    first = issue("pkg/" + "x" * 600 + "/a/.mcp.json")
    second = issue("pkg/" + "x" * 600 + "/b/.mcp.json")
    assert len(first["source"]) <= MAX_FAILURE_TEXT and len(second["source"]) <= MAX_FAILURE_TEXT
    assert first["source"] != second["source"] and first["issue_id"] != second["issue_id"]
