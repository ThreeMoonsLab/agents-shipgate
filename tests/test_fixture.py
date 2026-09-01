"""Tests for ``agents-shipgate fixture`` subcommand and the underlying
``agents_shipgate.fixtures`` module."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate import fixtures as fixtures_module
from agents_shipgate.cli.fixture import _report_replay_expectation
from agents_shipgate.cli.main import app
from agents_shipgate.fixtures import (
    fixture_path,
    fixtures_root,
    list_fixtures,
    replay_fixture,
)
from agents_shipgate.schemas.report import Finding, ReadinessReport, ReleaseDecision
from agents_shipgate.schemas.verifier import VerifierArtifact

runner = CliRunner()


def test_fixtures_root_finds_samples_in_editable_install():
    root = fixtures_root()
    assert root.is_dir()
    # The repo's bundled fixtures should be under this root.
    assert (root / "support_refund_agent" / "shipgate.yaml").is_file()


def test_list_fixtures_excludes_anti_patterns_and_dotfiles():
    fixtures = list_fixtures()
    listed_names = [entry["name"] for entry in fixtures]
    names = set(listed_names)
    assert "support_refund_agent" in names
    assert "agent_weakens_gate" in names
    assert "governed_edits_governance" in names
    assert "prompt_change_rides_release" in names
    assert len(listed_names) == len(names), "fixture names must be unique"
    assert "_anti_patterns" not in names, "anti-patterns directory must not surface as a fixture"
    for entry in fixtures:
        assert not entry["name"].startswith("_")
        assert not entry["name"].startswith(".")


def test_fixture_path_returns_existing_directory():
    path = fixture_path("clean_read_only_agent")
    assert (path / "shipgate.yaml").is_file()


def test_fixture_path_raises_for_unknown_fixture():
    from agents_shipgate.fixtures import FixtureNotFoundError

    with pytest.raises(FixtureNotFoundError):
        fixture_path("does-not-exist")


def test_replay_name_cannot_shadow_a_different_bundled_sample(tmp_path: Path):
    collision = tmp_path / "governed_edits_governance"
    collision.mkdir()
    (collision / "shipgate.yaml").write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="governed_edits_governance"):
        fixtures_module._validate_replay_fixture_names(tmp_path)


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
    replay = next(entry for entry in payload if entry["name"] == "governed_edits_governance")
    assert replay["kind"] == "replay"
    assert replay["path"] == "replay:governed_edits_governance"
    assert replay["backing_path"].endswith("/agent_weakens_gate")


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


def test_cli_fixture_copy_labels_replay_as_the_requested_incident(tmp_path: Path):
    target = tmp_path / "copies"
    result = runner.invoke(
        app,
        [
            "fixture",
            "copy",
            "governed_edits_governance",
            "--to",
            str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    copied = target / "governed_edits_governance"
    readme = (copied / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# governed_edits_governance\n")
    assert "reviewed `agent_weakens_gate` sample" in readme
    assert not readme.startswith("# agent_weakens_gate")
    assert (copied / "INCIDENT-FIXTURE.md").is_file()


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


def test_incident_replay_contracts_pin_current_and_desired_outcomes() -> None:
    """Lightweight registry checks complement the installed-wheel replays."""

    gap = replay_fixture("governed_edits_governance")
    assert gap is not None
    assert gap.observed_merge_verdict == "mergeable"
    assert gap.desired_merge_verdict == "human_review_required"
    assert gap.absent_check_ids == ("SHIP-VERIFY-TRUST-ROOT-TOUCHED",)
    assert gap.known_gap is not None and gap.known_gap.endswith("/issues/474")
    assert gap.gap_paths == (".github/agents/release-reviewer.agent.md",)

    routed = replay_fixture("prompt_change_rides_release")
    assert routed is not None
    assert routed.observed_merge_verdict == "human_review_required"
    assert routed.observed_decision == "review_required"
    assert routed.required_check_ids == (
        "SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED",
        "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
    )

    gate_removal = replay_fixture("agent_weakens_gate")
    assert gate_removal is not None
    assert gate_removal.head_files == ((".github/workflows/agents-shipgate.yml", None),)


def _verifier(merge_verdict: str, decision: str) -> VerifierArtifact:
    return VerifierArtifact.model_construct(
        merge_verdict=merge_verdict,
        release_decision=ReleaseDecision.model_construct(decision=decision),
    )


def _report(*findings: tuple[str, dict[str, object]]) -> ReadinessReport:
    return ReadinessReport.model_construct(
        findings=[
            Finding.model_construct(check_id=check_id, evidence=evidence)
            for check_id, evidence in findings
        ]
    )


def test_expected_fail_is_not_resolved_by_an_unrelated_review_route(capsys) -> None:
    """The desired verdict alone cannot prove the named path gap closed."""

    replay = replay_fixture("governed_edits_governance")
    assert replay is not None
    verifier = _verifier("human_review_required", "review_required")
    report = _report(("SHIP-SOME-UNRELATED-REVIEW", {"path": "somewhere-else"}))

    assert _report_replay_expectation(replay, verifier=verifier, report=report) == 20
    captured = capsys.readouterr()
    assert "Fixture expectation diverged" in captured.err
    assert f"Known gap: {replay.known_gap}" in captured.err
    assert "review finding names the gap path" in captured.err
    assert "Expected-fail resolved" not in captured.err


def test_expected_fail_resolution_accepts_a_new_check_that_names_the_gap(capsys) -> None:
    replay = replay_fixture("governed_edits_governance")
    assert replay is not None
    path = replay.gap_paths[0]
    new_check = "SHIP-NEW-GOVERNANCE-PATH-TOUCHED"
    verifier = _verifier("human_review_required", "review_required")
    report = _report((new_check, {"changed_path": path}))

    assert _report_replay_expectation(replay, verifier=verifier, report=report) == 20
    captured = capsys.readouterr()
    assert "Expected-fail resolved" in captured.err
    assert new_check in captured.err
    assert path in captured.err


def test_expected_fail_refuses_to_pass_without_a_report(capsys) -> None:
    replay = replay_fixture("governed_edits_governance")
    assert replay is not None

    assert (
        _report_replay_expectation(
            replay,
            verifier=_verifier("mergeable", "passed"),
            report=None,
        )
        == 20
    )
    assert "refusing to treat missing findings as expected absence" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"known_gap": None}, "must be set together"),
        ({"desired_merge_verdict": None}, "must be set together"),
        ({"absent_check_ids": ()}, "require absent_check_ids and gap_paths"),
        ({"gap_paths": ()}, "require absent_check_ids and gap_paths"),
    ],
)
def test_expected_fail_replay_metadata_invariants(
    changes: dict[str, object],
    message: str,
) -> None:
    replay = replay_fixture("governed_edits_governance")
    assert replay is not None
    with pytest.raises(ValueError, match=message):
        replace(replay, **changes)
