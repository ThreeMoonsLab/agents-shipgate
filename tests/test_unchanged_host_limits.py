"""#721: an unchanged partial or experimental surface is named, not a veto.

The #660 cold-start population lost 11 of 30 comparisons because one file the
change never touched made the base inventory incomplete. A limit that is
byte-identical on both sides cannot hide a change to itself, so the rest is
compared and the limit is named. Every negative control below is a way the
same rule could hide a real change, and each must still refuse.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.git import blob_path_unchanged
from agents_shipgate.core.host_comparison import UNCHANGED_LIMIT_ISSUE_KINDS
from agents_shipgate.core.host_grants import build_host_grants_baseline, host_audit_inventory

SKILL = ".claude/skills/helper/SKILL.md"
#: `version` is not a documented Claude skill field, so this skill is unresolved.
UNRESOLVED_SKILL = "---\nname: helper\ndescription: A helper.\nversion: 1.0.0\n---\n\nBody.\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def write(repo: Path, name: str, value) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def make_repo(tmp_path: Path, files: dict[str, object]) -> Path:
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
    git(repo, "checkout", "-qb", "change")
    return repo


def diff(repo: Path) -> dict:
    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "main", "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.fixture
def skill_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path, {
        ".claude/settings.json": {"permissions": {"allow": ["Read"]}},
        SKILL: UNRESOLVED_SKILL,
    })


def _widen(repo: Path) -> None:
    write(repo, ".claude/settings.json", {"permissions": {"allow": ["Read", "Bash(*)"]}})


def test_an_unchanged_unresolved_skill_is_named_and_the_change_is_compared(skill_repo: Path) -> None:
    _widen(skill_repo)

    payload = diff(skill_repo)

    assert payload["comparison_status"] == "comparable"
    assert any(row["after"] == "Bash(*)" for row in payload["rows"])
    assert payload["unchanged_limits"]
    assert {limit["source"] for limit in payload["unchanged_limits"]} == {SKILL}
    assert {limit["limit"] for limit in payload["unchanged_limits"]} == {"unsupported"}
    assert all("frontmatter_unknown_fields" in limit["detail"] for limit in payload["unchanged_limits"])


def test_the_text_output_names_the_limit(skill_repo: Path) -> None:
    _widen(skill_repo)

    result = CliRunner().invoke(app, ["diff", "--workspace", str(skill_repo), "--base", "main"])

    assert result.exit_code == 0, result.output
    assert "Not compared: unchanged in this change" in result.output
    assert SKILL in result.output
    assert "Bash(*)" in result.output


def test_a_changed_unresolved_skill_still_refuses(skill_repo: Path) -> None:
    _widen(skill_repo)
    write(skill_repo, SKILL, UNRESOLVED_SKILL.replace("Body.", "A different body."))

    payload = diff(skill_repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["rows"] == [] and payload["unchanged_limits"] == []


def test_an_unresolved_skill_the_change_adds_still_refuses(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".claude/settings.json": {"permissions": {"allow": ["Read"]}}})
    _widen(repo)
    write(repo, SKILL, UNRESOLVED_SKILL)

    payload = diff(repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["unchanged_limits"] == []


def test_a_line_ending_filter_cannot_make_a_changed_file_read_as_unchanged(tmp_path: Path) -> None:
    """With `eol=lf`, `git diff` reports a CRLF rewrite as no change. The bytes
    the reader opens differ, so identity is taken from unfiltered object IDs."""

    repo = make_repo(tmp_path, {
        ".gitattributes": "*.md text eol=lf\n",
        ".claude/settings.json": {"permissions": {"allow": ["Read"]}},
        SKILL: UNRESOLVED_SKILL,
    })
    assert blob_path_unchanged(repo, "main", None, SKILL)
    (repo / SKILL).write_bytes(UNRESOLVED_SKILL.replace("\n", "\r\n").encode())

    assert subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "main", "--", SKILL]).returncode == 0
    assert not blob_path_unchanged(repo, "main", None, SKILL)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_an_unchanged_symlink_still_refuses(tmp_path: Path) -> None:
    """An unchanged link whose target changed would hide that change (#700)."""

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    write(repo, ".claude/settings.json", {"permissions": {"allow": ["Read"]}})
    write(repo, "shared/helper/SKILL.md", "---\nname: helper\ndescription: A helper.\n---\n\nBody.\n")
    (repo / ".claude/skills").mkdir(parents=True)
    os.symlink("../../shared/helper", repo / ".claude/skills/helper")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "change")
    _widen(repo)

    payload = diff(repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["unchanged_limits"] == []


@pytest.fixture
def vscode_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path, {
        ".mcp.json": {"mcpServers": {}},
        ".vscode/mcp.json": {"servers": {"docs": {"command": "docs-server"}}},
    })


def test_an_unchanged_experimental_file_is_named_and_the_change_is_compared(vscode_repo: Path) -> None:
    write(vscode_repo, ".mcp.json", {"mcpServers": {"postgres": {"command": "postgres-server"}}})

    payload = diff(vscode_repo)

    assert payload["comparison_status"] == "comparable"
    assert any(row["after"] == "postgres" for row in payload["rows"])
    assert payload["unchanged_limits"] == [
        {
            "host": "vscode",
            "limit": "experimental_coverage",
            "source": ".vscode/mcp.json",
            "detail": "vscode coverage is experimental; this unchanged source was not compared",
        }
    ]


def test_a_changed_experimental_file_still_refuses(vscode_repo: Path) -> None:
    write(vscode_repo, ".mcp.json", {"mcpServers": {"postgres": {"command": "postgres-server"}}})
    write(vscode_repo, ".vscode/mcp.json", {"servers": {"docs": {"command": "other-server"}}})

    payload = diff(vscode_repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["unchanged_limits"] == []


def test_verify_publishes_the_same_limits_as_diff(skill_repo: Path) -> None:
    _widen(skill_repo)
    git(skill_repo, "add", "-A")
    git(skill_repo, "commit", "-qm", "widen")

    result = CliRunner().invoke(app, [
        "verify", "--workspace", str(skill_repo), "--base", "main", "--head", "HEAD", "--format", "text",
    ])

    assert result.exit_code == 0, result.output
    comparison = json.loads((skill_repo / "agents-shipgate-reports/verifier.json").read_text())["host_comparison"]
    assert comparison["comparison_status"] == "comparable"
    assert any(row["after"] == "Bash(*)" for row in comparison["rows"])
    assert {limit["source"] for limit in comparison["unchanged_limits"]} == {SKILL}
    assert "Not compared: unchanged in this change" in result.output


def test_identity_is_refused_wherever_it_cannot_be_proven(skill_repo: Path) -> None:
    assert blob_path_unchanged(skill_repo, "main", None, SKILL)
    assert blob_path_unchanged(skill_repo, "main", "main", SKILL)
    for path in ("", "missing.json", ".claude/skills", "../outside", "/etc/passwd", ".claude\\settings.json"):
        assert not blob_path_unchanged(skill_repo, "main", None, path), path
    assert not blob_path_unchanged(skill_repo, "no-such-ref", None, SKILL)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_parent_is_not_the_same_path(skill_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "helper").mkdir(parents=True)
    (outside / "helper/SKILL.md").write_text(UNRESOLVED_SKILL, encoding="utf-8")
    subprocess.run(["rm", "-rf", str(skill_repo / ".claude/skills")], check=True)
    os.symlink(outside, skill_repo / ".claude/skills")

    assert not blob_path_unchanged(skill_repo, "main", None, SKILL)


def test_the_audit_baseline_still_refuses_an_incomplete_inventory(skill_repo: Path) -> None:
    """A saved baseline acknowledges evidence; only the two-sided comparison changed."""

    with pytest.raises(ValueError, match="cannot acknowledge missing evidence"):
        build_host_grants_baseline(host_audit_inventory(skill_repo))


def test_only_per_source_limits_can_be_unchanged_limits() -> None:
    assert UNCHANGED_LIMIT_ISSUE_KINDS == {"unsupported", "parse_failed"}
    assert "unreadable" not in UNCHANGED_LIMIT_ISSUE_KINDS


def test_a_committed_head_that_changed_the_file_is_not_the_same_blob(skill_repo: Path) -> None:
    """The commit route compares object IDs too, not just that both sides have the path."""

    write(skill_repo, SKILL, UNRESOLVED_SKILL.replace("Body.", "Edited body."))
    git(skill_repo, "add", "-A")
    git(skill_repo, "commit", "-qm", "edit skill")

    assert not blob_path_unchanged(skill_repo, "main", "HEAD", SKILL)
    assert blob_path_unchanged(skill_repo, "main", "main", SKILL)


def test_an_issue_only_one_side_carries_refuses_even_when_its_source_is_identical() -> None:
    """Defence in depth: a limit must be shared, not merely unchanged in bytes."""

    from agents_shipgate.core.host_comparison import unchanged_limits

    issue = {"kind": "unsupported", "host": "claude-code", "source": SKILL, "message": "m", "blocking": True}
    extra = {**issue, "source": ".claude/skills/other/SKILL.md"}
    coverage = [{"host": "claude-code", "status": "partial", "sources_observed": [SKILL]}]
    before = {"issues": [issue], "host_coverage": coverage}
    after = {"issues": [issue, extra], "host_coverage": coverage}

    assert unchanged_limits(before, after, lambda source: True) is None
    assert unchanged_limits(before, before, lambda source: True) == [
        {"host": "claude-code", "limit": "unsupported", "source": SKILL, "detail": "m"}
    ]


def _committed_widening(repo: Path) -> None:
    _widen(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "widen")


def test_a_v0_18_verifier_reads_as_current_without_gaining_limits(skill_repo: Path) -> None:
    """0.18 compared only complete inventories, so it can never claim a limit."""

    from agents_shipgate.schemas.verifier import VerifierArtifact

    _committed_widening(skill_repo)
    result = CliRunner().invoke(app, [
        "verify", "--preview", "--workspace", str(skill_repo), "--base", "main", "--head", "HEAD", "--json",
    ])
    payload = json.loads(result.output)
    assert payload["verifier_schema_version"] == "0.19"
    assert payload["host_comparison"]["unchanged_limits"]

    legacy = json.loads(json.dumps(payload))
    legacy["verifier_schema_version"] = "0.18"
    with pytest.raises(ValueError, match="unchanged comparison limits"):
        VerifierArtifact.model_validate(legacy)
    legacy["host_comparison"].pop("unchanged_limits")
    read = VerifierArtifact.model_validate(legacy)
    assert read.verifier_schema_version == "0.19"
    assert read.host_comparison is not None and read.host_comparison.unchanged_limits == []


def test_check_refuses_rather_than_show_rows_without_their_limits(skill_repo: Path) -> None:
    """`shipgate.agent_boundary_result/v3` has no field for a limit yet."""

    _committed_widening(skill_repo)
    result = CliRunner().invoke(app, [
        "check", "--workspace", str(skill_repo), "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json",
    ])

    payload = json.loads(result.output)
    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == ["unchanged_limits_not_representable"]
    assert payload["rows"] == []
