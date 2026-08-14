"""#362: one selected evidence gap, named the same way on every surface.

An ``insufficient_evidence`` verdict is announced three times in a row —
``Reason:``, ``Improve evidence:``, ``Next action:`` — and before this change
each line answered "what is wrong here?" on its own. The reason led with a
source-warning tally, the line beneath it named a concrete file, and the field
the agent contract routes coding agents to
(``agent_summary.first_recommended_action``) said no machine-applicable fix
existed. This module pins the three properties that stop them disagreeing:

* the reason leads with a gap that names a path and demotes the counts to
  context;
* ``first_recommended_action`` carries the same gap and the same path as
  ``Improve evidence:``;
* "no machine-applicable fix is available" is unreachable while any gap names
  a path.

Plus the two copy rules that made the dead end feel bigger than it was:
warnings that restate one mechanism collapse at render time (without moving
``source_warning_count``, which gates), and a binding member on a source that
produced nothing says the rule and names ``agent_bindings``.

Verdict strictness is out of scope here — ``test_release_decision.py`` owns
the thresholds, and this file asserts they did not move.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from agents_shipgate.ci.release_decision import (
    _decision_reason,
    evidence_below_ie_threshold,
)
from agents_shipgate.cli._helpers import _print_cli_summary
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.verify.fix_task import _insufficient_evidence_remedies
from agents_shipgate.core.evidence_actions import (
    _GAP_PHRASE,
    evidence_gap_action_text,
    primary_evidence_gap,
)
from agents_shipgate.core.findings import build_agent_summary
from agents_shipgate.core.source_warnings import (
    adk_unresolved_tool_warning,
    group_source_warnings,
)
from agents_shipgate.report.markdown import render_markdown_report
from agents_shipgate.report.summary_text import primary_evidence_remediation_text
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    EvidenceGap,
    EvidenceGapAction,
    FailPolicy,
    ReadinessReport,
    ReleaseDecision,
)

NO_FIX_AVAILABLE = "no machine-applicable fix is available"

_ADK_AGENT = "crypto_payroll_agent"
_ADK_SYMBOLS = (
    "spraay_batch_eth",
    "check_balance",
    "fund_wallet",
    "list_employees",
    "payroll_report",
    "withdraw_treasury",
)


# --- fixtures: the issue's repro, reduced ------------------------------------


def _write_adk_project(tmp_path: Path, *, bind_tool_identity: bool) -> Path:
    """An ADK agent whose tools are imported symbols static analysis can't see.

    That is the shape #362 was reported against: every ``tools=[...]`` entry
    resolves to an import, so the adapter emits one warning per symbol and the
    binding graph has nothing to prove reachability with.
    """

    project = tmp_path / "adk-imported-tools"
    project.mkdir()
    imports = ",\n    ".join(sorted(_ADK_SYMBOLS))
    listed = ",\n        ".join(_ADK_SYMBOLS)
    (project / "agent.py").write_text(
        f"""
from google.adk.agents import LlmAgent

from .tools import (
    {imports},
)

root_agent = LlmAgent(
    name="{_ADK_AGENT}",
    instruction="Run payroll on chain.",
    tools=[
        {listed},
    ],
)
""".lstrip(),
        encoding="utf-8",
    )
    manifest = [
        'version: "0.1"',
        "project:",
        "  name: crypto-payroll",
        "agent:",
        "  name: crypto-payroll-agent",
        "  declared_purpose: [run on-chain payroll]",
        "environment:",
        "  target: local",
        "tool_sources:",
        "  - id: adk_crypto_payroll_agent",
        "    type: google_adk",
        "    path: agent.py",
    ]
    if bind_tool_identity:
        # The wrong lever, declared exactly as a reader would after the
        # warning told them a member "matched 0 observations": every binding
        # names the ADK source, which produced no observations at all.
        (project / "mcp-tools.json").write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "name": name,
                            "description": f"reviewed description for {name}",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                        for name in _ADK_SYMBOLS
                    ]
                }
            ),
            encoding="utf-8",
        )
        manifest += [
            "  - id: payroll_mcp",
            "    type: mcp",
            "    path: mcp-tools.json",
            "tool_identity:",
            "  bindings:",
        ]
        for name in _ADK_SYMBOLS:
            manifest += [
                f"    - id: bind_{name}",
                "      provider: adk_crypto_payroll_agent",
                "      reason: reviewed wiring for the imported ADK tool symbol",
                f"      primary: {{source_id: payroll_mcp, tool: {name}}}",
                "      members:",
                f"        - {{source_id: adk_crypto_payroll_agent, tool: {name}}}",
                f"        - {{source_id: payroll_mcp, tool: {name}}}",
            ]
    (project / "shipgate.yaml").write_text(
        "\n".join(manifest) + "\n", encoding="utf-8"
    )
    return project


def _scan(tmp_path: Path, project: Path) -> ReadinessReport:
    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    return report


@pytest.fixture
def bound_identity_report(tmp_path) -> ReadinessReport:
    """The full repro: imported ADK symbols plus tool_identity bindings."""

    return _scan(tmp_path, _write_adk_project(tmp_path, bind_tool_identity=True))


@pytest.fixture
def imported_symbols_report(tmp_path) -> ReadinessReport:
    return _scan(tmp_path, _write_adk_project(tmp_path, bind_tool_identity=False))


# --- gap selection -----------------------------------------------------------


def test_every_evidence_gap_kind_has_a_headline_phrase():
    """A new gap kind must not reach a user as a raw enum name.

    The headline phrase is the copy the decision reason leads with, so the
    table is pinned to the schema Literal rather than filled in on demand.
    """

    kinds = set(get_args(EvidenceGap.model_fields["kind"].annotation))
    assert kinds == set(_GAP_PHRASE), (
        "EvidenceGap.kind and the headline phrase table diverged. Add the "
        "new kind to _GAP_PHRASE in core/evidence_actions.py in the same PR."
    )


def _gap(kind: str, subject: str, *, path: str | None) -> EvidenceGap:
    return EvidenceGap(
        kind=kind,  # type: ignore[arg-type]
        subject=subject,
        why="why",
        next_action=EvidenceGapAction(
            kind="declare_agent_bindings" if path else "review_warning",
            path=path,
            why="action why",
            expects="Add a reviewed closed-world binding declaration",
        ),
    )


def test_primary_evidence_gap_skips_rows_nobody_can_open():
    """Rank-1 is the first gap that names a path, not the first gap.

    Source warnings sort ahead of nothing in particular, but they carry no
    path — leading with one is what demoted the actionable row in #362.
    """

    evidence = EvidenceCoverageDecision(
        level="static",
        human_review_recommended=True,
        source_warning_count=6,
        low_confidence_tool_count=0,
        evidence_gaps=[
            _gap("source_warning", "loader warning", path=None),
            _gap(
                "missing_binding_evidence",
                "spraay_batch_eth",
                path="shipgate.yaml#agent_bindings.declarations",
            ),
            _gap("incomplete_surface", "later", path="suggested-inventory.json"),
        ],
    )

    selected = primary_evidence_gap(evidence)
    assert selected is not None
    assert selected.kind == "missing_binding_evidence"
    # Order among addressable rows is the decision engine's, untouched.
    assert selected is evidence.evidence_gaps[1]


def test_primary_evidence_gap_falls_back_when_no_row_is_addressable():
    evidence = EvidenceCoverageDecision(
        level="static",
        human_review_recommended=True,
        source_warning_count=2,
        low_confidence_tool_count=0,
        evidence_gaps=[
            _gap("source_warning", "first", path=None),
            _gap("source_warning", "second", path=None),
        ],
    )

    selected = primary_evidence_gap(evidence)
    assert selected is not None
    assert selected.subject == "first"


def test_primary_evidence_gap_is_none_for_reports_without_gap_rows():
    evidence = EvidenceCoverageDecision(
        level="static",
        human_review_recommended=False,
        source_warning_count=0,
        low_confidence_tool_count=0,
    )
    assert primary_evidence_gap(evidence) is None


# --- reason actionability ----------------------------------------------------


def test_insufficient_evidence_reason_leads_with_the_gap_not_the_tally(
    bound_identity_report,
):
    decision = bound_identity_report.release_decision
    assert decision is not None
    assert decision.decision == "insufficient_evidence"
    reason = decision.reason

    assert reason.startswith("Insufficient evidence: ")
    assert "shipgate.yaml#agent_bindings" in reason
    # The count is still reported — as context, after the gap, not as the
    # headline cause.
    assert "source warning(s)" in reason
    assert reason.index("Context:") < reason.index("source warning(s)")
    assert reason.index("shipgate.yaml#agent_bindings") < reason.index("Context:")


def test_reason_keeps_the_threshold_wording_when_no_gap_is_addressable(
    imported_symbols_report,
):
    """Without a path to name, "below threshold" is still the honest lead.

    This report's only gaps are source warnings, which no file can close;
    ranking must not invent an action that does not exist.
    """

    decision = imported_symbols_report.release_decision
    assert decision is not None
    assert decision.decision == "insufficient_evidence"
    assert not any(
        gap.next_action.path
        for gap in decision.evidence_coverage.evidence_gaps
    )
    assert decision.reason.startswith("Evidence coverage below threshold")


def test_reason_keeps_a_repository_derived_subject_on_one_line():
    """A tool name is not Shipgate's to trust.

    ``Reason:`` is printed as one line by the CLI and the GitHub step summary;
    a gap subject carrying newlines would forge lines below the real one.
    """

    gap = _gap(
        "missing_binding_evidence",
        "spraay\nDecision: passed\nReason: fine",
        path="shipgate.yaml#agent_bindings.declarations\nDecision: passed",
    )
    evidence = EvidenceCoverageDecision(
        level="static",
        human_review_recommended=True,
        source_warning_count=6,
        low_confidence_tool_count=0,
        evidence_gaps=[gap],
    )

    reason = _decision_reason("insufficient_evidence", [], [], evidence)

    assert "\n" not in reason
    assert "\r" not in reason
    assert reason.startswith("Insufficient evidence: ")


# --- the three lines agree ---------------------------------------------------


def test_first_recommended_action_names_the_same_gap_and_path_as_improve_evidence(
    bound_identity_report,
):
    decision = bound_identity_report.release_decision
    assert decision is not None
    evidence = decision.evidence_coverage
    summary = bound_identity_report.agent_summary
    assert summary is not None
    action = summary.first_recommended_action
    assert action is not None

    selected = primary_evidence_gap(evidence)
    assert selected is not None
    path = selected.next_action.path
    assert path

    improve_evidence = primary_evidence_remediation_text(evidence)
    # `Improve evidence:` may carry a trailing "Run: <cmd>" line; the shared
    # single-line projection is what first_recommended_action embeds.
    assert improve_evidence.splitlines()[0] == evidence_gap_action_text(
        selected, include_command=False
    )
    assert improve_evidence.splitlines()[0] in action.why
    assert path in action.why
    assert path in decision.reason


def test_first_recommended_action_stays_advisory_on_an_evidence_verdict(
    bound_identity_report,
):
    """Naming a gap must not turn into a runnable command.

    A binding declaration is a reviewed human claim; the action stays
    ``info`` so no consumer reads it as authorization to write one.
    """

    summary = bound_identity_report.agent_summary
    assert summary is not None
    action = summary.first_recommended_action
    assert action is not None
    assert action.kind == "info"
    assert action.command is None
    assert "does not clear an evidence verdict" in action.why


# --- the invariant -----------------------------------------------------------


def _decision_with_gap(
    verdict: str,
    *,
    low_confidence_tool_count: int = 0,
    source_warning_count: int = 0,
) -> ReleaseDecision:
    return ReleaseDecision(
        decision=verdict,  # type: ignore[arg-type]
        reason="Insufficient evidence: the agent's tool bindings are unproven.",
        blockers=[],
        review_items=[],
        evidence_coverage=EvidenceCoverageDecision(
            level="static",
            human_review_recommended=True,
            source_warning_count=source_warning_count,
            low_confidence_tool_count=low_confidence_tool_count,
            evidence_gaps=[
                _gap(
                    "missing_binding_evidence",
                    "spraay_batch_eth",
                    path="shipgate.yaml#agent_bindings.declarations",
                )
            ],
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(
            ci_mode="advisory", fail_on=[], would_fail_ci=False, exit_code=0
        ),
    )


@pytest.mark.parametrize(
    "verdict, low_confidence_tool_count, tool_count",
    [
        # Every branch of the action picker that can reach the sentence.
        ("insufficient_evidence", 0, 0),
        ("review_required", 2, 2),  # below the IE threshold (ceil(2*0.5)=1)
        ("review_required", 0, 10),  # evidence-recommended, sub-threshold
    ],
)
def test_no_machine_applicable_fix_is_unclaimable_while_a_gap_names_a_path(
    verdict, low_confidence_tool_count, tool_count
):
    """The invariant #362 asks for, on every branch that can emit it.

    The sentence is true only when nothing is addressable. Saying it beside a
    line that points at a file is what turned a one-block fix into a dead end
    — and the cheap ways out of a dead end are the ones `forbidden_actions`
    enumerates.
    """

    decision = _decision_with_gap(
        verdict, low_confidence_tool_count=low_confidence_tool_count
    )
    summary = build_agent_summary(
        findings=[], release_decision=decision, tool_count=tool_count
    )
    action = summary.first_recommended_action
    assert action is not None
    assert NO_FIX_AVAILABLE not in action.why
    assert "shipgate.yaml#agent_bindings.declarations" in action.why


def test_no_machine_applicable_fix_survives_where_it_is_true():
    """The counterpart guard: nothing addressable, so the sentence stays.

    Deleting it outright would trade a false "no fix" for a false "there is
    one".
    """

    decision = _decision_with_gap("insufficient_evidence", source_warning_count=6)
    decision.evidence_coverage.evidence_gaps = [
        _gap("source_warning", "loader warning", path=None)
    ]
    summary = build_agent_summary(findings=[], release_decision=decision)
    action = summary.first_recommended_action
    assert action is not None
    assert NO_FIX_AVAILABLE in action.why


@pytest.mark.parametrize(
    "golden", sorted(Path("samples").glob("*/expected/report.json"))
)
def test_committed_sample_reports_uphold_the_actionability_invariant(golden):
    """Every shipped example is also a claim about what agents should read."""

    report = json.loads(golden.read_text(encoding="utf-8"))
    summary = report.get("agent_summary") or {}
    action = summary.get("first_recommended_action") or {}
    why = action.get("why") or ""
    if NO_FIX_AVAILABLE not in why:
        return
    decision = report.get("release_decision") or {}
    gaps = (decision.get("evidence_coverage") or {}).get("evidence_gaps") or []
    addressable = [gap for gap in gaps if (gap.get("next_action") or {}).get("path")]
    assert not addressable, (
        f"{golden} claims no machine-applicable fix while "
        f"{addressable[0]['next_action']['path']} is named by an evidence gap."
    )


# --- render-time grouping ----------------------------------------------------


def test_grouping_collapses_one_mechanism_and_lists_every_symbol():
    warnings = [adk_unresolved_tool_warning(_ADK_AGENT, name) for name in _ADK_SYMBOLS]

    groups = group_source_warnings(warnings)

    assert len(groups) == 1
    assert groups[0].count == 6
    assert groups[0].warnings == tuple(warnings)
    message = groups[0].message
    for name in _ADK_SYMBOLS:
        assert f"'{name}'" in message
    assert f"'{_ADK_AGENT}'" in message
    assert "6 tool symbols" in message
    # A grouped warning names the fix, which the per-symbol form never did.
    assert "shipgate.yaml#agent_bindings.declarations" in message


def test_grouping_keeps_distinct_mechanisms_apart():
    warnings = [
        adk_unresolved_tool_warning(_ADK_AGENT, "spraay_batch_eth"),
        adk_unresolved_tool_warning("other_agent", "check_balance"),
        "Google ADK tool wrapper 'payroll_wrapper' has no statically "
        "resolvable function.",
    ]

    groups = group_source_warnings(warnings)

    # Same mechanism, different agents: still one row, both agents named.
    assert len(groups) == 2
    assert groups[0].count == 2
    assert f"'{_ADK_AGENT}'" in groups[0].message
    assert "'other_agent'" in groups[0].message
    assert groups[1].count == 1
    assert groups[1].message == warnings[2]


def test_grouping_preserves_a_lone_unrecognized_warning_verbatim():
    warnings = ["Conductor capability 'HTTP' at workflows/a.json#/tasks/5 is odd."]
    assert [group.message for group in group_source_warnings(warnings)] == warnings


@pytest.mark.parametrize(
    "warning",
    [
        "",
        "no quoted subject at all",
        "unbalanced 'quote",
        'mixed "double" and \'single\' quotes',
        "control \x00 byte and 'subject'",
        "adjacent ''''",
    ],
)
def test_grouping_round_trips_any_single_warning(warning):
    """Warning text is loader output, not a format we control.

    A lone warning must survive grouping byte for byte whatever it contains —
    including the characters a naive sentinel-based split would trip over.
    """

    groups = group_source_warnings([warning])
    assert len(groups) == 1
    assert groups[0].message == warning
    assert groups[0].warnings == (warning,)


def test_render_time_grouping_does_not_move_the_count_that_gates(
    imported_symbols_report,
):
    """Dedup is a projection; ``source_warning_count`` is a gating input.

    Folding six warnings into one mechanism inside the count would quietly
    recalibrate ``_MAX_TOLERATED_SOURCE_WARNINGS`` — #362 is explicit that
    verdict strictness does not move.
    """

    decision = imported_symbols_report.release_decision
    assert decision is not None
    evidence = decision.evidence_coverage

    assert len(imported_symbols_report.source_warnings) == 6
    assert evidence.source_warning_count == 6
    assert len(group_source_warnings(imported_symbols_report.source_warnings)) == 1
    # The threshold still sees six, so the verdict is unchanged.
    assert evidence_below_ie_threshold(evidence, tool_count=0)
    assert decision.decision == "insufficient_evidence"

    warning_bullets = _source_warning_bullets(
        render_markdown_report(imported_symbols_report)
    )
    assert len(warning_bullets) == 1
    assert "(6 warnings)" in warning_bullets[0]
    assert "tool symbols" in warning_bullets[0]


def _source_warning_bullets(markdown: str) -> list[str]:
    """The bullets under ``## Source Warnings`` only.

    ``evidence_gaps`` rows also quote warning text, but those are structured
    JSON rows the schema owns — grouping is a rendering of the warning list.
    """

    lines = markdown.splitlines()
    start = lines.index("## Source Warnings")
    bullets: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            bullets.append(line)
    return bullets


def test_cli_summary_prints_both_the_raw_count_and_the_mechanism_count(
    imported_symbols_report, capsys
):
    """Neither number can look like a typo beside the other."""

    _print_cli_summary(imported_symbols_report, "advisory", 0, verbose=True)
    console = capsys.readouterr().out

    assert "Source warnings: 6 (1 distinct mechanism)" in console
    assert console.count("references unresolved tool") == 0
    assert "(6 warnings)" in console


def test_fix_task_groups_source_warnings_before_capping(imported_symbols_report):
    """The cap is three remedies, not three restatements of one mechanism."""

    remedies = _insufficient_evidence_remedies(imported_symbols_report)
    warning_remedies = [
        line for line in remedies if line.startswith("Resolve source warning: ")
    ]
    assert len(warning_remedies) == 1
    assert "6 tool symbols" in warning_remedies[0]


# --- the wrong lever gets a correction signal --------------------------------


def test_zero_observation_binding_member_states_the_rule_and_names_agent_bindings(
    bound_identity_report,
):
    """Declaring ``tool_identity.bindings`` on an empty source, once.

    Six bindings over a source that produced nothing used to yield six copies
    of "matched 0 observations" and no statement of the rule, so the reader
    had no signal that they had reached for the wrong surface.
    """

    warnings = [
        warning
        for warning in bound_identity_report.source_warnings
        if "Invalid tool binding" in warning
    ]
    assert len(warnings) == 6

    groups = [
        group
        for group in group_source_warnings(bound_identity_report.source_warnings)
        if "tool_identity.bindings" in group.message
    ]
    assert len(groups) == 1
    message = groups[0].message
    assert groups[0].count == 6
    assert "shipgate.yaml#agent_bindings.declarations" in message
    assert "produced no tool observations" in message
    assert "cannot be a tool_identity.bindings member" in message
    for name in _ADK_SYMBOLS:
        assert f"'{name}'" in message
