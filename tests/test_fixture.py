"""Tests for ``agents-shipgate fixture`` subcommand and the underlying
``agents_shipgate.fixtures`` module."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.fixtures import fixture_path, fixtures_root, list_fixtures

runner = CliRunner()


def test_fixtures_root_finds_samples_in_editable_install():
    root = fixtures_root()
    assert root.is_dir()
    # The repo's bundled fixtures should be under this root.
    assert (root / "support_refund_agent" / "shipgate.yaml").is_file()


def test_list_fixtures_excludes_anti_patterns_and_dotfiles():
    fixtures = list_fixtures()
    names = {entry["name"] for entry in fixtures}
    assert "support_refund_agent" in names
    assert "governed_edits_governance" in names
    assert "capability_change_rides_release" in names
    assert "_anti_patterns" not in names, "anti-patterns directory must not surface as a fixture"
    for entry in fixtures:
        assert not entry["name"].startswith("_")
        assert not entry["name"].startswith(".")


def test_fixture_path_returns_existing_directory():
    path = fixture_path("clean_read_only_agent")
    assert (path / "shipgate.yaml").is_file()


def test_fixture_path_raises_for_unknown_fixture():
    import pytest

    from agents_shipgate.fixtures import FixtureNotFoundError

    with pytest.raises(FixtureNotFoundError):
        fixture_path("does-not-exist")


def test_cli_fixture_list_text():
    result = runner.invoke(app, ["fixture", "list"])
    assert result.exit_code == 0
    assert "support_refund_agent" in result.output


def test_cli_fixture_list_json():
    result = runner.invoke(app, ["fixture", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    names = {entry["name"] for entry in payload}
    assert "support_refund_agent" in names


def test_cli_fixture_run(tmp_path: Path):
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "fixture",
            "run",
            "clean_read_only_agent",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out / "report.json").is_file()
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert payload["release_decision"]["decision"] == "passed"
    semantic = payload["release_decision"]["evidence_coverage"]["semantic_coverage"]
    assert semantic == {
        "total_actions": 1,
        "pass_eligible_actions": 1,
        "gap_count": 0,
        "review_concern_count": 0,
        "reason_counts": {},
        "acknowledged_overrides": [],
        # Nothing was ever asked: ``readOnlyHint`` establishes the effect and
        # the MCP auth block establishes the authority, so the manifest's
        # declarations restate what the scan already proved.
        "declaration_questions": {
            "total": 0,
            "answered": 0,
            "open": 0,
            "open_by_dimension": {},
            "open_questions": [],
        },
    }


def test_cli_fixture_run_ai_generated_refund_pr_writes_verifier_artifacts(tmp_path: Path):
    out = tmp_path / "verify-out"
    result = runner.invoke(
        app,
        [
            "fixture",
            "run",
            "ai_generated_refund_pr",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Mode: verify" in result.output
    assert "Merge verdict: blocked" in result.output
    assert (out / "verifier.json").is_file()
    assert (out / "report.json").is_file()
    assert (out / "pr-comment.md").is_file()
    payload = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    assert payload["merge_verdict"] == "blocked"
    assert payload["can_merge_without_human"] is False
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    blocker_checks = {item["check_id"] for item in report["release_decision"]["blockers"]}
    assert "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING" in blocker_checks
    semantic = report["release_decision"]["evidence_coverage"]["semantic_coverage"]
    assert semantic["gap_count"] == 0
    assert semantic["pass_eligible_actions"] == semantic["total_actions"]


def test_cli_fixture_copy(tmp_path: Path):
    target = tmp_path / "copies"
    result = runner.invoke(
        app,
        [
            "fixture",
            "copy",
            "clean_read_only_agent",
            "--to",
            str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (target / "clean_read_only_agent" / "shipgate.yaml").is_file()


def test_cli_fixture_unknown_returns_2():
    result = runner.invoke(app, ["fixture", "run", "this-fixture-does-not-exist"])
    assert result.exit_code == 2


def test_cli_fixture_run_agent_weakens_gate_blocks_on_gate_removal(tmp_path: Path):
    """The trust-root demo: head deletes the Shipgate CI workflow and the
    verifier blocks via the suppression-immune gate-removal checks."""
    out = tmp_path / "verify-out"
    result = runner.invoke(
        app,
        [
            "fixture",
            "run",
            "agent_weakens_gate",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Mode: verify" in result.output
    assert "Merge verdict: blocked" in result.output
    payload = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    assert payload["merge_verdict"] == "blocked"
    assert payload["can_merge_without_human"] is False
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    blocker_checks = {item["check_id"] for item in report["release_decision"]["blockers"]}
    assert "SHIP-VERIFY-CI-GATE-REMOVED" in blocker_checks


def test_cli_fixture_run_governed_edits_governance_names_expected_gap(
    tmp_path: Path,
) -> None:
    """The unshipped .github/agents path is an explicit expected-fail.

    This intentionally pins the current gap. Once #474 ships the path-level
    governance surface, the replay command exits 20 and this test must be
    converted to assert the human-review verdict instead of silently passing.
    """

    out = tmp_path / "verify-out"
    result = runner.invoke(
        app,
        [
            "fixture",
            "run",
            "governed_edits_governance",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Fixture expectation: expected-fail" in result.output
    assert "Expected verdict: human_review_required" in result.output
    assert "Observed verdict: mergeable" in result.output
    assert "issues/474" in result.output

    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    assert verifier["changed_files"] == [".github/agents/release-reviewer.agent.md"]
    assert verifier["merge_verdict"] == "mergeable"
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["release_decision"]["decision"] == "passed"
    checks = {finding["check_id"] for finding in report["findings"]}
    assert "SHIP-VERIFY-TRUST-ROOT-TOUCHED" not in checks


def test_cli_fixture_run_capability_change_rides_release_routes_review(
    tmp_path: Path,
) -> None:
    """Routine release noise must not hide a changed prompt trust root."""

    out = tmp_path / "verify-out"
    result = runner.invoke(
        app,
        [
            "fixture",
            "run",
            "capability_change_rides_release",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Merge verdict: human_review_required" in result.output
    assert "Decision: review_required" in result.output
    assert "Fixture expectation: confirmed" in result.output

    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    assert verifier["changed_files"] == [
        "CHANGELOG.md",
        "package.json",
        "prompts/release.md",
    ]
    assert verifier["merge_verdict"] == "human_review_required"
    assert verifier["can_merge_without_human"] is False

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["release_decision"]["decision"] == "review_required"
    protected = report["protected_surface_changes"]
    assert [item["path"] for item in protected] == ["prompts/release.md"]
    checks = {finding["check_id"] for finding in report["findings"]}
    assert "SHIP-VERIFY-TRUST-ROOT-TOUCHED" in checks
