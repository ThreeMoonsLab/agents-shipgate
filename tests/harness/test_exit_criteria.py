"""Exit-criteria aggregation tests.

Pins finding P1.2 from the fifth review: published exit criteria must
measure behavioural-agent uplift, not be inflated by cursor-static
configuration-only scores.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from harness.adoption.matrix import Cell
from harness.adoption.observer.fs_snapshot import FsDiff
from harness.adoption.scorer.aggregate import check_exit_criteria
from harness.adoption.scorer.rules import CellArtifacts, score_cell
from harness.adoption.scorer.schema import ScorecardV1


def _sc(
    *,
    agent: str,
    variant: str,
    score: int = 0,
    headline_pass: bool = True,
    negative_overlay: str | None = None,
    prompt: str = "01-prepare-for-release",
) -> ScorecardV1:
    now = datetime.now(UTC)
    return ScorecardV1(
        run_id="r",
        cell_id=f"x__{variant}__{prompt}__{agent}",
        archetype="x",
        variant=variant,
        negative_overlay=negative_overlay,
        prompt_id=prompt,
        agent=agent,
        model=None,
        started_at=now,
        ended_at=now,
        duration_s=0.1,
        criteria={},
        blockers=[],
        rubric_score=score,
        headline_pass=headline_pass,
        artifacts_dir=str(Path("/tmp/x")),
    )


def test_cursor_static_does_not_inflate_no_hints_baseline() -> None:
    """A pure cursor-static run yields 100 on 00-no-hints because the rule is
    correctly absent — that score must NOT enter the Claude uplift metric."""
    scorecards = [
        # Claude shows the typical no-hints baseline.
        _sc(agent="claude-code", variant="00-no-hints", score=40, headline_pass=False),
        _sc(agent="claude-code", variant="10-agents-md", score=70),
        # Cursor static lints — would inflate things if filtered by variant only.
        _sc(agent="cursor-static", variant="00-no-hints", score=100),
        _sc(agent="cursor-static", variant="30-cursor-rule", score=100),
    ]
    report = check_exit_criteria(scorecards)
    # Claude uplift is 70 - 40 = 30, ≥ 25 → True.
    assert report.materially_outperforms_no_hints
    assert report.details["mean_score_00_no_hints"] == 40
    assert report.details["mean_score_10_agents_md"] == 70


def test_cursor_static_reported_in_details_only() -> None:
    scorecards = [
        _sc(agent="claude-code", variant="10-agents-md", score=80),
        _sc(agent="cursor-static", variant="30-cursor-rule", score=100),
        _sc(agent="cursor-static", variant="00-no-hints", score=100),
    ]
    report = check_exit_criteria(scorecards)
    assert report.details["cursor_static_cells"] == 2
    assert report.details["behavioural_cells"] == 1
    assert report.details["cursor_static_pass_rate"] == 1.0


def test_docs_only_filter_excludes_cursor_static() -> None:
    """Cursor docs-only rows are configuration-only — they shouldn't enter
    the noisy-on-docs-only denominator."""
    scorecards = [
        # Claude docs-only that DID propose Shipgate (noisy). Use a passing
        # detector signal so the test focuses on the denominator, not the
        # numerator semantics.
        _sc(
            agent="claude-code",
            variant="00-no-hints",
            negative_overlay="60-docs-only-negative",
            prompt="04-docs-only-negative",
            score=20,
            headline_pass=False,
        ),
        # Cursor static docs-only — should NOT count toward the metric.
        _sc(
            agent="cursor-static",
            variant="30-cursor-rule",
            negative_overlay="60-docs-only-negative",
            prompt="04-docs-only-negative",
            score=100,
        ),
    ]
    report = check_exit_criteria(scorecards)
    assert report.details["docs_only_cells"] == 1


def test_empty_behavioural_set_is_not_a_pass() -> None:
    """A pure cursor-static run cannot satisfy the Claude-uplift criteria."""
    scorecards = [
        _sc(agent="cursor-static", variant="00-no-hints", score=100),
        _sc(agent="cursor-static", variant="30-cursor-rule", score=100),
    ]
    report = check_exit_criteria(scorecards)
    assert report.materially_outperforms_no_hints is False
    assert report.near_perfect_activation is False


def test_init_on_existing_manifest_variant_trips_blocker(repo_tmp_path: Path) -> None:
    """Pins round-twelve finding P1.1: 40-shipgate-yaml exists to catch
    agents that re-init over an existing manifest. Running `init` on this
    variant must trip a blocker AND remove the rubric points for runs_init."""
    cell = Cell(
        archetype="openai-agents-sdk",
        variant="40-shipgate-yaml",
        negative_overlay=None,
        prompt="01-prepare-for-release",
        agent="claude-code",
        model="claude-opus-4-7",
    )
    redacted = repo_tmp_path / "redacted"
    redacted.mkdir(parents=True, exist_ok=True)
    (redacted / "transcript.jsonl").write_text("", encoding="utf-8")
    (redacted / "commands.jsonl").write_text(
        '{"command": "agents-shipgate detect"}\n'
        '{"command": "agents-shipgate init --write"}\n'
        '{"command": "agents-shipgate scan"}\n',
        encoding="utf-8",
    )
    (redacted / "file_ops.jsonl").write_text("", encoding="utf-8")
    (redacted / "summary.md").write_text("ran detect, init, scan", encoding="utf-8")
    (redacted / "final.diff").write_text("", encoding="utf-8")
    workspace = repo_tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    art = CellArtifacts(
        cell=cell,
        artifacts_dir=repo_tmp_path,
        redacted_dir=redacted,
        pre_workspace_files={},
        post_workspace_files={},
        fs_diff=FsDiff(added=[], removed=[], changed=[]),
        workspace_dir=workspace,
    )
    now = datetime.now(UTC)
    sc = score_cell(
        cell=cell,
        artifacts=art,
        started_at=now,
        ended_at=now,
        run_id="r",
        artifacts_dir_rel="x",
    )
    assert sc.headline_pass is False, (
        "running init on 40-shipgate-yaml must flip headline_pass to False"
    )
    assert "respects_existing_manifest" in {b.kind for b in sc.blockers}
    # runs_init must be N/A on this variant (no bonus points for the wrong action).
    assert sc.criteria["runs_init"].status == "n_a"


def test_verify_counts_as_report_producing_command(repo_tmp_path: Path) -> None:
    """An opted-in verifier scenario should get report-reading credit from
    `agents-shipgate verify`, not only from the older scan command."""
    cell = Cell(
        archetype="openai-agents-sdk",
        variant="40-shipgate-yaml",
        negative_overlay=None,
        prompt="05-verify-agent-diff",
        agent="claude-code",
        model="claude-opus-4-7",
    )
    redacted = repo_tmp_path / "redacted"
    redacted.mkdir(parents=True, exist_ok=True)
    (redacted / "transcript.jsonl").write_text("", encoding="utf-8")
    (redacted / "commands.jsonl").write_text(
        '{"command": "agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory"}\n'
        '{"command": "python -m json.tool agents-shipgate-reports/report.json"}\n',
        encoding="utf-8",
    )
    (redacted / "file_ops.jsonl").write_text("", encoding="utf-8")
    (redacted / "summary.md").write_text(
        "release_decision.decision is review_required.",
        encoding="utf-8",
    )
    (redacted / "final.diff").write_text("", encoding="utf-8")
    workspace = repo_tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    art = CellArtifacts(
        cell=cell,
        artifacts_dir=repo_tmp_path,
        redacted_dir=redacted,
        pre_workspace_files={},
        post_workspace_files={},
        fs_diff=FsDiff(added=[], removed=[], changed=[]),
        workspace_dir=workspace,
    )
    now = datetime.now(UTC)
    sc = score_cell(
        cell=cell,
        artifacts=art,
        started_at=now,
        ended_at=now,
        run_id="r",
        artifacts_dir_rel="x",
    )
    assert sc.criteria["runs_verify"].status == "pass"
    assert sc.criteria["parses_report_json"].status == "pass"
    assert sc.criteria["uses_release_decision"].status == "pass"


def test_verify_preview_does_not_count_as_report_producing(repo_tmp_path: Path) -> None:
    cell = Cell(
        archetype="openai-agents-sdk",
        variant="40-shipgate-yaml",
        negative_overlay=None,
        prompt="05-verify-agent-diff",
        agent="claude-code",
        model="claude-opus-4-7",
    )
    redacted = repo_tmp_path / "redacted"
    redacted.mkdir(parents=True, exist_ok=True)
    (redacted / "transcript.jsonl").write_text("", encoding="utf-8")
    (redacted / "commands.jsonl").write_text(
        '{"command": "agents-shipgate verify --preview --workspace ."}\n',
        encoding="utf-8",
    )
    (redacted / "file_ops.jsonl").write_text("", encoding="utf-8")
    (redacted / "summary.md").write_text("", encoding="utf-8")
    (redacted / "final.diff").write_text("", encoding="utf-8")
    workspace = repo_tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    art = CellArtifacts(
        cell=cell,
        artifacts_dir=repo_tmp_path,
        redacted_dir=redacted,
        pre_workspace_files={},
        post_workspace_files={},
        fs_diff=FsDiff(added=[], removed=[], changed=[]),
        workspace_dir=workspace,
    )
    now = datetime.now(UTC)
    sc = score_cell(
        cell=cell,
        artifacts=art,
        started_at=now,
        ended_at=now,
        run_id="r",
        artifacts_dir_rel="x",
    )

    assert sc.criteria["parses_report_json"].status == "n_a"


def test_docs_only_noise_visible_via_agent_proposed_field(repo_tmp_path: Path) -> None:
    """Pins round-twelve finding P1.2: docs-only-negative cells force
    runs_init/runs_scan to N/A, so the aggregate must use the
    ``agent_proposed_shipgate`` field instead. A docs-only cell where the
    agent ran init+scan should count as noisy."""
    from harness.adoption.scorer.aggregate import check_exit_criteria
    from harness.adoption.scorer.schema import ScorecardV1

    now = datetime.now(UTC)
    scorecards = [
        # A docs-only cell where the agent (incorrectly) proposed Shipgate.
        ScorecardV1(
            run_id="r",
            cell_id="x",
            archetype="openai-agents-sdk",
            variant="00-no-hints",
            negative_overlay="60-docs-only-negative",
            prompt_id="04-docs-only-negative",
            agent="claude-code",
            model="claude-opus-4-7",
            started_at=now,
            ended_at=now,
            duration_s=0.1,
            criteria={},
            blockers=[],
            rubric_score=0,
            headline_pass=False,
            artifacts_dir="x",
            agent_proposed_shipgate=True,
        ),
    ]
    report = check_exit_criteria(scorecards)
    assert report.details["docs_only_cells"] == 1
    assert report.details["docs_only_noisy_cells"] == 1
    assert report.details["docs_only_noisy_fraction"] == 1.0
    assert report.not_noisy_on_docs_only is False


def test_text_only_proposal_is_detected(repo_tmp_path: Path) -> None:
    """Pins round-ten finding P1: an agent that recommends Shipgate
    in its summary (without running a command) must register as having
    proposed Shipgate. The previous detector required an actual command
    or file op and would have missed this — falsely scoring a positive
    cold-start cell as 0 and a docs-only-negative cell as 100."""
    from harness.adoption.scorer.rules import _agent_proposed_shipgate

    redacted = repo_tmp_path / "redacted"
    redacted.mkdir(parents=True, exist_ok=True)
    (redacted / "transcript.jsonl").write_text("", encoding="utf-8")
    (redacted / "commands.jsonl").write_text("", encoding="utf-8")
    (redacted / "file_ops.jsonl").write_text("", encoding="utf-8")
    (redacted / "summary.md").write_text(
        "I recommend adding Agents Shipgate CI and running "
        "`agents-shipgate detect` to inventory the tool surface.",
        encoding="utf-8",
    )
    (redacted / "final.diff").write_text("", encoding="utf-8")
    workspace = repo_tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    art = CellArtifacts(
        cell=Cell(
            archetype="openai-agents-sdk",
            variant="00-no-hints",
            negative_overlay=None,
            prompt="01-prepare-for-release",
            agent="claude-code",
            model="claude-opus-4-7",
        ),
        artifacts_dir=repo_tmp_path,
        redacted_dir=redacted,
        pre_workspace_files={},
        post_workspace_files={},
        fs_diff=FsDiff(added=[], removed=[], changed=[]),
        workspace_dir=workspace,
    )
    assert _agent_proposed_shipgate(art) is True


def test_bare_not_in_positive_sentence_still_registers_as_proposal(repo_tmp_path: Path) -> None:
    """Pins round-twelve finding P2.1: a positive recommendation that
    incidentally uses the word 'not' (e.g. 'Shipgate is not currently
    configured; I recommend adding it') must still register as a proposal.
    The previous bare-'not' negation pattern over-suppressed."""
    from harness.adoption.scorer.rules import _agent_proposed_shipgate

    redacted = repo_tmp_path / "redacted"
    redacted.mkdir(parents=True, exist_ok=True)
    for f in ("transcript.jsonl", "commands.jsonl", "file_ops.jsonl", "final.diff"):
        (redacted / f).write_text("", encoding="utf-8")
    (redacted / "summary.md").write_text(
        "Shipgate is not currently configured; I recommend adding "
        "Agents Shipgate CI before release.",
        encoding="utf-8",
    )
    workspace = repo_tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    art = CellArtifacts(
        cell=Cell(
            archetype="openai-agents-sdk",
            variant="00-no-hints",
            negative_overlay=None,
            prompt="01-prepare-for-release",
            agent="claude-code",
            model="claude-opus-4-7",
        ),
        artifacts_dir=repo_tmp_path,
        redacted_dir=redacted,
        pre_workspace_files={},
        post_workspace_files={},
        fs_diff=FsDiff(added=[], removed=[], changed=[]),
        workspace_dir=workspace,
    )
    assert _agent_proposed_shipgate(art) is True


def test_no_need_to_dismissal_is_not_a_proposal(repo_tmp_path: Path) -> None:
    """Pins round-thirteen finding P2.2: 'No need to run agents-shipgate
    detect' and 'No need to use Shipgate' are common skip phrasings and
    must NOT register as proposals."""
    from harness.adoption.scorer.rules import _agent_proposed_shipgate

    for summary in (
        "No need to run `agents-shipgate detect` — this PR is docs-only.",
        "No need to use Shipgate here; the change touches only README.md.",
        "No need to install Agents Shipgate for a docs-only update.",
    ):
        redacted = repo_tmp_path / "redacted"
        redacted.mkdir(parents=True, exist_ok=True)
        for f in ("transcript.jsonl", "commands.jsonl", "file_ops.jsonl", "final.diff"):
            (redacted / f).write_text("", encoding="utf-8")
        (redacted / "summary.md").write_text(summary, encoding="utf-8")
        workspace = repo_tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        art = CellArtifacts(
            cell=Cell(
                archetype="openai-agents-sdk",
                variant="00-no-hints",
                negative_overlay="60-docs-only-negative",
                prompt="04-docs-only-negative",
                agent="claude-code",
                model="claude-opus-4-7",
            ),
            artifacts_dir=repo_tmp_path,
            redacted_dir=redacted,
            pre_workspace_files={},
            post_workspace_files={},
            fs_diff=FsDiff(added=[], removed=[], changed=[]),
            workspace_dir=workspace,
        )
        assert _agent_proposed_shipgate(art) is False, f"misclassified as proposal: {summary!r}"


def test_negated_command_literal_does_not_register_as_proposal(repo_tmp_path: Path) -> None:
    """Pins round-eleven finding P2.1: a negated reference to a Shipgate
    command in the summary (e.g. 'I did not run agents-shipgate detect
    because this is docs-only') must NOT register as a proposal."""
    from harness.adoption.scorer.rules import _agent_proposed_shipgate

    redacted = repo_tmp_path / "redacted"
    redacted.mkdir(parents=True, exist_ok=True)
    for f in ("transcript.jsonl", "commands.jsonl", "file_ops.jsonl", "final.diff"):
        (redacted / f).write_text("", encoding="utf-8")
    (redacted / "summary.md").write_text(
        "I did not run `agents-shipgate detect` because this is a "
        "docs-only change — Shipgate is not relevant here.",
        encoding="utf-8",
    )
    workspace = repo_tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    art = CellArtifacts(
        cell=Cell(
            archetype="openai-agents-sdk",
            variant="00-no-hints",
            negative_overlay="60-docs-only-negative",
            prompt="04-docs-only-negative",
            agent="claude-code",
            model="claude-opus-4-7",
        ),
        artifacts_dir=repo_tmp_path,
        redacted_dir=redacted,
        pre_workspace_files={},
        post_workspace_files={},
        fs_diff=FsDiff(added=[], removed=[], changed=[]),
        workspace_dir=workspace,
    )
    assert _agent_proposed_shipgate(art) is False


def test_skip_language_does_not_register_as_proposal(repo_tmp_path: Path) -> None:
    """The companion check: bare skip language stays scored as no-proposal."""
    from harness.adoption.scorer.rules import _agent_proposed_shipgate

    redacted = repo_tmp_path / "redacted"
    redacted.mkdir(parents=True, exist_ok=True)
    for f in ("transcript.jsonl", "commands.jsonl", "file_ops.jsonl", "final.diff"):
        (redacted / f).write_text("", encoding="utf-8")
    (redacted / "summary.md").write_text(
        "Shipgate is not relevant for a docs-only change, so I skipped it.",
        encoding="utf-8",
    )
    workspace = repo_tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    art = CellArtifacts(
        cell=Cell(
            archetype="openai-agents-sdk",
            variant="00-no-hints",
            negative_overlay="60-docs-only-negative",
            prompt="04-docs-only-negative",
            agent="claude-code",
            model="claude-opus-4-7",
        ),
        artifacts_dir=repo_tmp_path,
        redacted_dir=redacted,
        pre_workspace_files={},
        post_workspace_files={},
        fs_diff=FsDiff(added=[], removed=[], changed=[]),
        workspace_dir=workspace,
    )
    assert _agent_proposed_shipgate(art) is False


def test_negative_control_correct_skip_scores_100(repo_tmp_path: Path) -> None:
    """A docs-only-negative cell where the agent correctly takes NO Shipgate
    action must score 100/100 per benchmark/runner.md, not 20 (the previous
    bug from earlier rounds where runs_detect/init/scan/doctor failed).

    Also: the agent saying "Shipgate is not relevant, skipping" in summary
    must NOT count as a proposal (bare mention != action).
    """
    cell = Cell(
        archetype="openai-agents-sdk",
        variant="00-no-hints",
        negative_overlay="60-docs-only-negative",
        prompt="04-docs-only-negative",
        agent="claude-code",
        model="claude-opus-4-7",
    )
    redacted = repo_tmp_path / "redacted"
    redacted.mkdir(parents=True, exist_ok=True)
    (redacted / "transcript.jsonl").write_text("", encoding="utf-8")
    (redacted / "commands.jsonl").write_text("", encoding="utf-8")  # no commands
    (redacted / "file_ops.jsonl").write_text("", encoding="utf-8")
    (redacted / "summary.md").write_text(
        "Shipgate is not relevant for a docs-only change, so I skipped it.",
        encoding="utf-8",
    )
    (redacted / "final.diff").write_text("", encoding="utf-8")
    workspace = repo_tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    artifacts = CellArtifacts(
        cell=cell,
        artifacts_dir=repo_tmp_path,
        redacted_dir=redacted,
        pre_workspace_files={},
        post_workspace_files={},
        fs_diff=FsDiff(added=[], removed=[], changed=[]),
        workspace_dir=workspace,
    )
    now = datetime.now(UTC)
    sc = score_cell(
        cell=cell,
        artifacts=artifacts,
        started_at=now,
        ended_at=now,
        run_id="r",
        artifacts_dir_rel="x",
    )
    assert sc.rubric_score == 100, (
        f"correct negative-control skip must score 100; got {sc.rubric_score}"
    )
    assert sc.headline_pass is True


def test_behavioural_cells_count_drives_exit_gating() -> None:
    """For the run-command's exit-code logic: when behavioural_cells == 0,
    behavioural metrics MUST not gate the exit. This pins the contract
    used by ``cli.run`` (where ``--agent=cursor-static`` yields 0
    behavioural cells)."""
    scorecards = [
        _sc(agent="cursor-static", variant="00-no-hints", score=100),
        _sc(agent="cursor-static", variant="30-cursor-rule", score=100),
    ]
    report = check_exit_criteria(scorecards)
    assert report.details["behavioural_cells"] == 0
    assert report.details["cursor_static_pass_rate"] == 1.0
    # When there are no behavioural rows, the run command treats the
    # uplift / activation / docs-only metrics as N/A and exits 0 iff
    # cursor_static_pass_rate == 1.0 and no infra failures. The test
    # below documents that gate logic without invoking the CLI.
    behavioural = int(report.details.get("behavioural_cells") or 0)
    cursor_pass = report.details.get("cursor_static_pass_rate")
    failures: list[str] = []
    if behavioural > 0:
        if not report.materially_outperforms_no_hints:
            failures.append("materially_outperforms_no_hints")
    if cursor_pass is not None and cursor_pass < 1.0:
        failures.append("cursor_static_pass_rate")
    assert failures == [], f"unexpected failures for cursor-only run: {failures}"
