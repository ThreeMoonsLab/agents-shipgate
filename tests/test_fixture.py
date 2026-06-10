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
    assert "_anti_patterns" not in names, (
        "anti-patterns directory must not surface as a fixture"
    )
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
    # exit code may be 0 or 20 depending on findings; either is "the
    # fixture ran"
    assert result.exit_code in (0, 20), result.output
    assert (out / "report.json").is_file()


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
    blocker_checks = {
        item["check_id"] for item in report["release_decision"]["blockers"]
    }
    assert "SHIP-VERIFY-CI-GATE-REMOVED" in blocker_checks
