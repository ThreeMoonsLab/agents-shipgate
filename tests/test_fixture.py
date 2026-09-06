"""Tests for ``agents-shipgate fixture`` subcommand and the underlying
``agents_shipgate.fixtures`` module."""

from __future__ import annotations

import json
import re
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

REPO_ROOT = Path(__file__).resolve().parent.parent

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
        "declaration_review": {
            "enabled": False,
            "base_comparison_requested": False,
            "base_kind": "none",
            "changed_count": 0,
            "summary": {
                "evidence_consistent": 0,
                "unverified": 0,
                "acknowledged_override": 0,
            },
            "rows": [],
            "notes": [
                "No trustworthy base declaration snapshot was available; declaration review disabled."
            ],
        },
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


#: The human entry path (#498). Both files quote this fixture's PR comment, and
#: both are registered distribution surfaces in `docs/distribution-surfaces.md`.
_ENTRY_PATH_SURFACES = ("README.md", "docs/quickstart.md")

#: A fenced or quoted block, and the prose immediately before it. A block is
#: read as a quotation of the PR comment when that prose names the artifact.
_QUOTED_BLOCK = re.compile(r"(?P<lead>(?:[^\n]*\n){0,4})```[a-z]*\n(?P<body>.*?)\n```", re.DOTALL)
_BLOCKQUOTE = re.compile(r"(?P<lead>(?:[^\n]*\n){0,4})(?P<body>(?:^>[^\n]*\n)+)", re.M)


def _unescaped(text: str) -> str:
    """Markdown escaping removed and whitespace collapsed.

    The comment writes ``high\\-risk`` because it renders into a Markdown
    surface; a document quoting it as ``high-risk`` is quoting the same content,
    and where a paragraph wraps is not part of the claim either.
    """

    return re.sub(r"\s+", " ", re.sub(r"\\([^A-Za-z0-9\s])", r"\1", text)).strip()


def _logical_lines(body: str) -> list[str]:
    """One entry per rendered line, with hard-wrapped continuations rejoined.

    A quoted excerpt wraps the comment's long lines to fit the page and skips
    the rows it is not making a point about. Neither is a difference in what it
    claims, so the comparison is per rendered line: a bullet or heading starts
    one, anything else continues the previous.
    """

    lines: list[str] = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "#", "|")) or not lines:
            lines.append(stripped)
        else:
            lines[-1] = f"{lines[-1]} {stripped}"
    return [_unescaped(line) for line in lines]


def test_the_entry_path_quotes_lines_the_pr_comment_actually_renders(tmp_path: Path):
    """README and quickstart quote this fixture's comment; the engine writes it.

    The README used to show a `### Agents Shipgate result: block` heading, an
    `Impact | Change | Subject | Why` table and a numbered *Required before
    merge* list, and said the fixture wrote that "verbatim". No code path
    rendered any of it, and `block` is not a value `merge_verdict` or
    `release_decision.decision` ever takes. It was the flagship example on the
    surface a stranger reads first, and nothing compared it to the artifact it
    named (#498).
    """

    out = tmp_path / "verify-out"
    result = runner.invoke(app, ["fixture", "run", "ai_generated_refund_pr", "--out", str(out)])
    assert result.exit_code == 0, result.output
    rendered = _unescaped((out / "pr-comment.md").read_text(encoding="utf-8"))

    checked = 0
    for relpath in _ENTRY_PATH_SURFACES:
        text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        for pattern in (_QUOTED_BLOCK, _BLOCKQUOTE):
            for match in pattern.finditer(text):
                if "pr-comment.md" not in match.group("lead"):
                    continue
                body = match.group("body").replace("\n>", "\n").lstrip(">")
                for logical in _logical_lines(body):
                    # `…` is the abridgement marker: each side of one is a
                    # claim, what it elides is not. An excerpt also *skips*
                    # rows, so each line is checked on its own rather than the
                    # block being required to appear contiguously.
                    for segment in logical.split("…"):
                        segment = segment.strip(" ;-")
                        if len(segment) < 12:
                            continue
                        checked += 1
                        assert segment in rendered, (
                            f"{relpath} shows this as part of `pr-comment.md`, "
                            f"and the fixture's comment does not contain it:\n"
                            f"  {segment!r}\n"
                            "Quote what the engine renders, or stop calling the "
                            "block a PR comment."
                        )
    assert checked >= 3, (
        f"only {checked} quoted PR-comment segments found across "
        f"{list(_ENTRY_PATH_SURFACES)}; this guard is checking nothing. Either "
        "the entry path stopped showing the comment, or the block it shows is "
        "no longer introduced by prose naming `pr-comment.md`."
    )


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
