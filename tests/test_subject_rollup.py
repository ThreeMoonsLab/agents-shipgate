"""#364: one fact about four tools reads as one group, not seventeen rows.

The reported shape: a scan of four money-moving tools produced seventeen
findings across five check families, the human summary showed three of them,
and all three were the *same* check on sibling tools — so idempotency, scopes,
owners and guardrails were never mentioned.  Separately, the recommendation on
the finding a reader would open first told them to declare a control the same
finding's ``evidence.missing`` says they had already declared.

Two properties are pinned here, and one non-property:

* the human surfaces group by subject and every check family that fired on a
  shown subject is visible;
* a built-in control recommendation names the missing controls and nothing
  else, which is enforced by construction (one ``missing`` list builds the
  evidence, the sentence, and the predicate row) and checked here through a
  real scan;
* ``report.json`` does **not** group.  ``findings[]`` stays the flat
  per-finding record, and the rollup adds no field to it.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents_shipgate.ci.release_decision import _to_item
from agents_shipgate.cli._helpers import (
    _CLI_ROW_LIMIT,
    _CLI_SUBJECT_LIMIT,
    _print_cli_summary,
)
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.verify.orchestrator import _derive_verifier_control
from agents_shipgate.core.action_semantics import (
    BUILTIN_EFFECT_OBLIGATIONS,
    missing_control_recommendation,
)
from agents_shipgate.core.findings.subject_rollup import (
    AGENT_WIDE_SUBJECT,
    SubjectGroup,
    finding_line,
    group_summary,
    missing_controls,
    roll_up_findings,
    rollup_detail,
    rollup_headline,
    source_suffix,
    top_findings_block,
)
from agents_shipgate.core.findings.summaries import (
    summarize_findings,
    summarize_tool_surface,
)
from agents_shipgate.core.surface_exclusions import derived_id_kind
from agents_shipgate.report.markdown import (
    _MARKDOWN_SUBJECT_LIMIT,
    _safe_markdown_text,
    render_markdown_report,
)
from agents_shipgate.report.pr_comment import _COMMENT_SUBJECT_LIMIT, render_pr_comment
from agents_shipgate.report.pr_comment import _escape as _pr_escape
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    Finding,
    ReadinessReport,
    ReleaseDecision,
)
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierCapabilityReview,
    VerifierDiffStatus,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

#: The controls a built-in check can name, and how each is spelled to a reader.
#: Every path in :data:`BUILTIN_EFFECT_OBLIGATIONS` has to appear here or the
#: conservation assertion below cannot tell "not named" from "spelled another
#: way", which is how a drifting sentence would pass as a clean one.
CONTROL_PHRASES = {
    "approval.required": "approval.required",
    "confirmation.required": "confirmation policy",
    "safeguards.audit_log": "safeguards.audit_log",
    "safeguards.idempotency": "safeguards.idempotency",
    "safeguards.rollback": "safeguards.rollback",
}

BUILTIN_CONTROL_CHECKS = {
    "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
    "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
    "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
}


AGENT_PY = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def spraay_batch_eth(recipients: list, amount: float) -> dict:
    """Send the same amount of ETH to every recipient in a batch."""
    return {"status": "sent"}


def spraay_batch_eth_variable(recipients: list, amounts: list) -> dict:
    """Send per-recipient ETH amounts in one batch transfer."""
    return {"status": "sent"}


def spraay_batch_token(token: str, recipients: list, amount: float) -> dict:
    """Transfer the same token amount to every recipient in a batch."""
    return {"status": "sent"}


def spraay_batch_token_variable(token: str, recipients: list, amounts: list) -> dict:
    """Transfer per-recipient token amounts in one batch transfer."""
    return {"status": "sent"}


root_agent = LlmAgent(
    name="spraay_agent",
    instruction="Distribute funds to recipients.",
    tools=[
        FunctionTool(func=spraay_batch_eth),
        FunctionTool(func=spraay_batch_eth_variable),
        FunctionTool(func=spraay_batch_token),
        FunctionTool(func=spraay_batch_token_variable),
    ],
)
'''

SPRAAY_TOOLS = (
    "spraay_batch_eth",
    "spraay_batch_eth_variable",
    "spraay_batch_token",
    "spraay_batch_token_variable",
)


def _manifest(*, approval_declared: bool) -> str:
    """The reported repository, reduced.

    ``approval_declared`` is the whole point of the drift half: with approval
    already declared, ``evidence.missing`` holds two of the three financial
    write controls, and the sentence used to name all three anyway.
    """

    approval = "      approval:\n        required: true\n" if approval_declared else ""
    actions = "".join(
        f"    - tool: {name}\n"
        "      effect: financial_write\n"
        "      authority:\n"
        "        mode: none\n" + approval
        for name in SPRAAY_TOOLS
    )
    return (
        'version: "0.1"\n'
        "\n"
        "project:\n"
        "  name: spraay-agent\n"
        "\n"
        "agent:\n"
        "  name: spraay-agent\n"
        "  declared_purpose:\n"
        "    - distribute funds to many recipients\n"
        "\n"
        "environment:\n"
        "  target: production_like\n"
        "\n"
        "tool_sources:\n"
        "  - id: spraay\n"
        "    type: google_adk\n"
        "    path: agent.py\n"
        "\n"
        "action_surface:\n"
        "  actions:\n" + actions
    )


def _pr_comment(report) -> str:
    """The findings-style PR comment for a scanned report.

    Assembled here rather than mocked: the comment is a surface a reviewer
    reads, and the point of the assertion it serves is that it renders the
    same groups the other two do.
    """

    review = VerifierCapabilityReview()
    control = _derive_verifier_control(
        execution="succeeded",
        merge_verdict="blocked",
        release_decision=report.release_decision,
        fix_task=None,
        capability_review=review,
        headline="h",
        first_next_action_override=None,
        base_status="not_requested",
        base_ref=None,
        diff_status=VerifierDiffStatus(completeness="complete"),
    )
    verifier = VerifierArtifact(
        workspace=".",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 rule matched."},
        execution="succeeded",
        head_status="succeeded",
        release_decision=report.release_decision,
        decision=report.release_decision.decision,
        merge_verdict="blocked",
        applicability="verified",
        headline="h",
        control=control,
        capability_review=review,
        artifacts={"report_json": "agents-shipgate-reports/report.json"},
    )
    return render_pr_comment(verifier, report=report, style="findings")


def _block_after(lines: list[str], header: str) -> list[str]:
    """The summary block only — the console prints other bulleted lists."""

    body = lines[lines.index(header) + 1 :]
    end = body.index("") if "" in body else len(body)
    return body[:end]


def _scan(tmp_path: Path, *, approval_declared: bool = True):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "agent.py").write_text(AGENT_PY, encoding="utf-8")
    (workspace / "shipgate.yaml").write_text(
        _manifest(approval_declared=approval_declared), encoding="utf-8"
    )
    out = tmp_path / "out"
    report, exit_code = run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=out,
        formats=["json", "markdown"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    return report, out


def _report_with(findings: list[Finding], *, catalog: list[dict], agent: dict):
    """A report shaped enough for the projection, with no scan behind it.

    The scanned repro above cannot produce a nameless catalog row or an agent
    named like a derived id, and those are exactly the shapes that put an
    unreadable string in a heading.
    """

    return ReadinessReport(
        run_id="run_test",
        project={"name": "p"},
        agent=agent,
        environment={"target": "production_like"},
        summary=summarize_findings(findings, []),
        tool_surface=summarize_tool_surface([]),
        findings=findings,
        tool_catalog=catalog,
    )


def _decision(*, blockers: list[Finding], review_items: list[Finding]) -> ReleaseDecision:
    """A decision that names the given findings, built the way scans build it."""

    return ReleaseDecision(
        decision="blocked" if blockers else "review_required",
        reason="fixture",
        blockers=[_to_item(finding) for finding in blockers],
        review_items=[_to_item(finding) for finding in review_items],
        evidence_coverage=EvidenceCoverageDecision(
            level="static",
            human_review_recommended=False,
            source_warning_count=0,
            low_confidence_tool_count=0,
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(
            ci_mode="advisory", would_fail_ci=False, exit_code=0
        ),
    )


# --------------------------------------------------------------------------
# The reported repro
# --------------------------------------------------------------------------


def test_repro_stdout_shows_one_group_per_tool_plus_the_aggregate(tmp_path, capsys):
    """Four tool groups and one aggregate, where seventeen flat rows were.

    The acceptance criterion from the issue, asserted on the console because
    the console is where it was reported.  ``blocks_release`` stays visible per
    group: grouping is a change of axis, not a softening of the verdict.
    """

    report, _ = _scan(tmp_path)
    _print_cli_summary(report, "advisory", 0)
    lines = capsys.readouterr().out.splitlines()

    header = next(line for line in lines if line.startswith("Top findings"))
    assert "5 subjects" in header

    block = _block_after(lines, header)
    groups = [line for line in block if line.startswith("- ")]
    subjects = [line[2:].split(" — ")[0] for line in groups]
    for name in SPRAAY_TOOLS:
        assert any(subject.startswith(name) for subject in subjects), subjects
    assert any(AGENT_WIDE_SUBJECT in subject for subject in subjects)
    assert len(subjects) == 5

    blocking = [line for line in groups if "BLOCKS RELEASE" in line]
    assert len(blocking) == 4, groups


def test_every_check_family_that_fired_is_named_somewhere_in_the_summary(tmp_path, capsys):
    """The defect was not "too many rows" — it was a hidden check family.

    Three rows of one check on three sibling tools spent the whole budget
    saying one thing.  With the subject as the group key, every family that
    fired on a shown subject has a row of its own.
    """

    report, _ = _scan(tmp_path)
    _print_cli_summary(report, "advisory", 0)
    printed = capsys.readouterr().out

    families = {
        finding.check_id
        for finding in report.findings
        if not finding.suppressed and finding.severity in {"critical", "high"}
    }
    assert len(families) > 1
    for check_id in families:
        assert check_id in printed, check_id


def test_report_json_findings_stay_flat_and_ungrouped(tmp_path):
    """Scope guard. The rollup is presentation; automation reads the record.

    Nothing about grouping may reach ``report.json`` — not a group key, not a
    subject label, not a reordering that merges siblings.  ``findings[]``
    stays one row per finding, in the order the pipeline produced them.
    """

    report, out = _scan(tmp_path)
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))

    assert len(payload["findings"]) == len(report.findings)
    assert [row["check_id"] for row in payload["findings"]] == [
        finding.check_id for finding in report.findings
    ]
    for row in payload["findings"]:
        assert "subject" not in row
        assert "group" not in row


# --------------------------------------------------------------------------
# Recommendation conservation
# --------------------------------------------------------------------------


def _named_controls(text: str) -> set[str]:
    return {path for path, phrase in CONTROL_PHRASES.items() if phrase in text}


def test_a_declared_control_is_not_recommended_again(tmp_path):
    """The drift the issue quoted, on the manifest that produced it.

    ``approval.required`` is declared, so it is absent from
    ``evidence.missing`` — and the sentence must be absent too.  Asserting the
    negative alone would pass on an empty sentence, so the two controls that
    *are* missing are asserted present in the same breath.
    """

    report, _ = _scan(tmp_path, approval_declared=True)
    findings = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
    ]
    assert len(findings) == len(SPRAAY_TOOLS)
    for finding in findings:
        assert missing_controls(finding) == [
            "safeguards.audit_log",
            "safeguards.idempotency",
        ]
        assert "approval.required" not in finding.recommendation
        assert "safeguards.audit_log" in finding.recommendation
        assert "safeguards.idempotency" in finding.recommendation


def test_undeclared_controls_are_all_recommended(tmp_path):
    """The other direction: nothing declared, so nothing may be dropped.

    A renderer that derives from ``missing`` could satisfy the test above by
    saying less every time.  This pins the full set on the same manifest with
    the approval taken out.
    """

    report, _ = _scan(tmp_path, approval_declared=False)
    findings = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
    ]
    assert findings
    for finding in findings:
        assert set(missing_controls(finding)) == BUILTIN_EFFECT_OBLIGATIONS[
            "financial_write"
        ]
        assert _named_controls(finding.recommendation) == set(
            missing_controls(finding)
        )


def test_no_shipped_sample_recommends_a_control_it_says_is_present():
    """The acceptance criterion, swept over every sample that ships.

    A guard scoped to the one manifest that reported the bug passes vacuously
    for every other shape, which is the failure mode this repo keeps
    rediscovering — so the sweep counts what it checked and fails if that
    count is zero.  Reading the committed goldens rather than rescanning keeps
    it fast and makes a regression visible in review as a golden diff.
    """

    expected_reports = sorted(SAMPLES.glob("*/expected/report.json"))
    assert expected_reports, "no sample goldens found to sweep"
    checked = 0
    for path in expected_reports:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("findings", []):
            if row["check_id"] not in BUILTIN_CONTROL_CHECKS:
                continue
            checked += 1
            missing = set(row.get("evidence", {}).get("missing") or [])
            assert missing, f"{path.parent.parent.name}: {row['check_id']} with no missing set"
            assert _named_controls(row["recommendation"]) == missing, (
                f"{path.parent.parent.name}: {row['check_id']}"
            )
    assert checked, "no shipped sample emits a built-in control finding"


def test_the_phrase_table_covers_every_control_the_builtins_oblige():
    """A conservation check on the check itself.

    ``_named_controls`` can only find a control it knows how to spell, so an
    obligation this table has never heard of would make every assertion above
    pass by finding nothing.
    """

    obliged = set().union(*BUILTIN_EFFECT_OBLIGATIONS.values())
    assert obliged <= set(CONTROL_PHRASES)


def test_the_sentence_is_built_from_the_missing_list_alone():
    """Unit-level: the renderer names its argument, in a fixed reading order.

    The order is not the caller's: two branches collect the same set in
    different orders (audit log before confirmation in one, after it in the
    other), and one set of missing controls has to have one sentence.
    """

    assert missing_control_recommendation(
        ["financial_write"], ["safeguards.audit_log", "safeguards.idempotency"]
    ) == (
        "Declare safeguards.audit_log and safeguards.idempotency "
        "for this financial write action."
    )
    forward = missing_control_recommendation(
        ["external_communication"], ["safeguards.audit_log", "confirmation.required"]
    )
    backward = missing_control_recommendation(
        ["external_communication"], ["confirmation.required", "safeguards.audit_log"]
    )
    assert forward == backward
    assert forward == (
        "Declare confirmation policy and safeguards.audit_log "
        "for this external communication action."
    )


def test_an_unknown_control_path_is_still_named():
    """Failing open here would put the sentence back in the business of
    disagreeing with the evidence — silently, and only for new controls."""

    sentence = missing_control_recommendation(
        ["financial_write"], ["safeguards.audit_log", "safeguards.time_lock"]
    )
    assert "safeguards.time_lock" in sentence


# --------------------------------------------------------------------------
# The projection itself
# --------------------------------------------------------------------------


def test_grouping_conserves_the_findings_it_selects(tmp_path):
    """No finding is invented, duplicated, or dropped by the grouping step."""

    report, _ = _scan(tmp_path)
    groups = roll_up_findings(report)
    grouped = [finding for group in groups for finding in group.findings]
    assert len(grouped) == len({id(finding) for finding in grouped})

    decision = report.release_decision
    named = {item.id for item in [*decision.blockers, *decision.review_items]}
    expected = [
        finding
        for finding in report.findings
        if not finding.suppressed
        and (finding.severity in {"critical", "high"} or finding.id in named)
    ]
    assert {id(finding) for finding in grouped} == {id(finding) for finding in expected}


def test_a_suppressed_finding_never_reaches_a_group(tmp_path):
    report, _ = _scan(tmp_path)
    target = next(
        finding for finding in report.findings if finding.severity == "critical"
    )
    target.suppressed = True
    grouped = [
        finding for group in roll_up_findings(report) for finding in group.findings
    ]
    assert not any(finding is target for finding in grouped)


def test_blocking_subjects_sort_ahead_of_review_subjects(tmp_path):
    """Urgency is what orders the list now that severity is an attribute."""

    groups = roll_up_findings(_scan(tmp_path)[0])
    blocking = [index for index, group in enumerate(groups) if group.blocks_release]
    review = [index for index, group in enumerate(groups) if not group.blocks_release]
    assert blocking and review
    assert max(blocking) < min(review)


def test_no_subject_is_a_derived_identifier(tmp_path):
    """#329's rule, one subject kind later.

    A group heading is the most adopter-facing string this projection emits.
    A ``tool_v2_…`` or ``agent_v1:…`` in it names something that appears in no
    file the reader has.
    """

    for group in roll_up_findings(_scan(tmp_path)[0]):
        assert derived_id_kind(group.subject) is None, group.subject


def test_two_findings_about_one_tool_share_one_spelling(tmp_path):
    """The label is resolved once per group, from the catalog index.

    Rendering it per finding is how a subject acquires two spellings — the
    reason ``catalog_label_index`` exists at all.
    """

    groups = roll_up_findings(_scan(tmp_path)[0])
    tool_groups = [group for group in groups if group.kind == "tool"]
    assert len(tool_groups) == len({group.subject for group in tool_groups})
    assert all(group.count > 1 for group in tool_groups)


def test_all_three_human_surfaces_read_the_same_groups(tmp_path):
    """One projection, or the surfaces drift back apart.

    Each renders its own escaping and its own budget; none of them recomputes
    which findings belong to which subject.  All three are exercised, because
    a test that names three and checks two is how the third one drifts.
    """

    report, _out = _scan(tmp_path)
    groups = roll_up_findings(report)
    assert len(groups) > 1

    console = "\n".join(
        top_findings_block(
            groups, group_limit=_CLI_SUBJECT_LIMIT, row_limit=_CLI_ROW_LIMIT
        )
    )
    surfaces = (
        (console, _CLI_SUBJECT_LIMIT, str),
        (render_markdown_report(report), _MARKDOWN_SUBJECT_LIMIT, _safe_markdown_text),
        (_pr_comment(report), _COMMENT_SUBJECT_LIMIT, _pr_escape),
    )
    # The budgets differ on purpose — a PR comment is not a report — so what
    # has to agree is *which* subjects each surface shows, in what order: the
    # first N of one projection, never a different N.
    for rendered, limit, escape in surfaces:
        for group in groups[:limit]:
            assert escape(group.subject) in rendered, group.subject
        for group in groups[limit:]:
            assert escape(group.subject) not in rendered, group.subject


def test_truncation_says_how_much_it_hid(tmp_path):
    """A summary that drops rows without saying so is how the flat list came
    to look complete while showing three of seventeen."""

    groups = roll_up_findings(_scan(tmp_path)[0])
    block = "\n".join(top_findings_block(groups, group_limit=1, row_limit=1))
    assert f"… and {len(groups) - 1} more subjects" in block
    assert "more finding" in block


def test_singular_counts_read_as_singular():
    """``1 finding across 1 subject``, not ``1 finding(s)``.

    Built from a real ``Finding`` rather than a stub: a stub that happens to
    carry the fields today's renderer reads stops exercising the renderer the
    moment it reads one more.
    """

    group = SubjectGroup(
        subject="only_tool",
        kind="tool",
        findings=(
            Finding(
                check_id="SHIP-EXAMPLE",
                title="one thing",
                severity="high",
                category="example",
                recommendation="do the thing",
            ),
        ),
        blocking=(False,),
    )
    block = "\n".join(
        top_findings_block([group], group_limit=9, row_limit=9, heading=None)
    )
    assert "1 finding across 1 subject" == rollup_headline([group])
    assert "findings" not in block
    assert "subjects" not in block


def test_a_row_repeats_the_location_only_when_it_differs_from_the_heading():
    """The suffix is hoisted when every row shares it and kept when they do
    not — the case ``samples/conductor_agent`` is the live example of."""

    def _finding(pointer: str) -> Finding:
        return Finding(
            check_id="SHIP-EXAMPLE",
            title="one thing",
            severity="high",
            category="example",
            recommendation="do the thing",
            source=SourceReference(
                type="conductor_workflow", path="workflows/a.json", pointer=pointer
            ),
        )

    same = SubjectGroup(
        subject="subject",
        kind="agent",
        findings=(_finding("/tasks/1"), _finding("/tasks/1")),
        blocking=(False, False),
        location=" (at workflows/a.json#/tasks/1)",
    )
    lines = top_findings_block([same], group_limit=9, row_limit=9, heading=None)
    assert lines[0].endswith(" — review (2 high)")
    assert "workflows/a.json" in lines[0]
    assert not any("workflows/a.json" in line for line in lines[1:])

    differing = SubjectGroup(
        subject="subject",
        kind="agent",
        findings=(_finding("/tasks/1"), _finding("/tasks/4")),
        blocking=(False, False),
    )
    lines = top_findings_block([differing], group_limit=9, row_limit=9, heading=None)
    assert "workflows/a.json" not in lines[0]
    assert "#/tasks/1)" in lines[1]
    assert "#/tasks/4)" in lines[2]


def test_a_zero_width_character_in_a_path_stays_visible():
    """A path names something the reader will open, so it is escaped rather
    than folded: ``a\u200b.json`` must not render as ``a.json``."""

    finding = Finding(
        check_id="SHIP-EXAMPLE",
        title="one thing",
        severity="high",
        category="example",
        recommendation="do the thing",
        source=SourceReference(type="file", path="a\u200b.json"),
    )
    assert "\u200b" not in source_suffix(finding)
    assert "U+200B" in source_suffix(finding)


def test_the_row_says_what_is_missing_when_the_check_knows(tmp_path):
    """``missing: a, b`` is the decision; the title only restates the heading."""

    report, _ = _scan(tmp_path)
    finding = next(
        row
        for row in report.findings
        if row.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
    )
    assert rollup_detail(finding) == (
        "missing: safeguards.audit_log, safeguards.idempotency"
    )
    assert "(blocks release)" in finding_line(finding, blocks_release=True)
    assert "(blocks release)" not in finding_line(finding, blocks_release=False)


def test_missing_controls_reads_both_evidence_shapes():
    """Built-in checks write strings; action-policy checks write rows."""

    class _Finding:
        def __init__(self, evidence):
            self.evidence = evidence

    assert missing_controls(_Finding({"missing": ["a", " b "]})) == ["a", "b"]
    assert missing_controls(
        _Finding({"missing": [{"path": "a", "expected": True}]})
    ) == ["a"]
    assert missing_controls(_Finding({"missing": "a"})) == []
    assert missing_controls(_Finding({})) == []


def test_group_summary_names_the_status_before_the_severities(tmp_path):
    groups = roll_up_findings(_scan(tmp_path)[0])
    blocking = next(group for group in groups if group.blocks_release)
    review = next(group for group in groups if not group.blocks_release)
    assert group_summary(blocking).startswith("BLOCKS RELEASE")
    assert group_summary(review).startswith("review")
    assert "critical" in group_summary(blocking)


def test_a_nameless_catalog_row_never_becomes_a_group_heading():
    """``catalog_subject`` falls back to the tool id, which is right where it
    joins two surfaces by value and wrong in a heading (#329)."""

    finding = Finding(
        check_id="SHIP-EXAMPLE",
        title="one thing",
        severity="high",
        category="example",
        recommendation="do the thing",
        tool_id="tool_v2_deadbeefdeadbeef",
        tool_name="",
        agent_id="agent:p/a",
    )
    report = _report_with(
        [finding],
        catalog=[{"tool_id": "tool_v2_deadbeefdeadbeef", "name": "", "provider": "mcp"}],
        agent={"id": "agent:p/a", "name": "a"},
    )
    (group,) = roll_up_findings(report)
    assert "tool_v2_deadbeefdeadbeef" not in group.subject
    assert group.subject == f"a ({AGENT_WIDE_SUBJECT})"


def test_an_agent_named_like_a_derived_id_falls_back_to_the_generic_subject():
    """A name is adopter-controlled, so it can look like anything — including
    like the identity model.  A digest is unreadable either way."""

    finding = Finding(
        check_id="SHIP-EXAMPLE",
        title="one thing",
        severity="high",
        category="example",
        recommendation="do the thing",
        agent_id="agent:p/agent_v1:deadbeefdeadbeef",
    )
    report = _report_with(
        [finding],
        catalog=[],
        agent={"id": "agent:p/agent_v1:deadbeefdeadbeef", "name": "agent_v1:deadbeefdeadbeef"},
    )
    (group,) = roll_up_findings(report)
    assert group.subject == AGENT_WIDE_SUBJECT
    assert derived_id_kind(group.subject) is None


def test_two_tools_with_one_name_stay_two_subjects():
    """Grouping is on identity, not on the label — a name repeated across
    sources would otherwise merge two tools into one heading."""

    def _finding(tool_id: str) -> Finding:
        return Finding(
            check_id="SHIP-EXAMPLE",
            title="one thing",
            severity="high",
            category="example",
            recommendation="do the thing",
            tool_id=tool_id,
            tool_name="search",
        )

    report = _report_with(
        [_finding("tool_v2_aaaa"), _finding("tool_v2_bbbb")],
        catalog=[
            {"tool_id": "tool_v2_aaaa", "name": "search", "provider": "openapi"},
            {"tool_id": "tool_v2_bbbb", "name": "search", "provider": "mcp"},
        ],
        agent={"id": "agent:p/a", "name": "a"},
    )
    groups = roll_up_findings(report)
    assert len(groups) == 2
    assert {group.subject for group in groups} == {"search [openapi]", "search [mcp]"}


def test_accepted_debt_is_not_reported_as_blocking():
    """`finding.blocks_release` and "the decision blocks on this" are two
    claims, and a baseline separates them.

    A policy finding whose debt a baseline has accepted keeps
    ``blocks_release=True`` and is filed by `ci.release_decision` as a *review
    item*. Reading the flag would print BLOCKS RELEASE two lines under a
    verdict that accepted it.
    """

    accepted = Finding(
        id="fp_accepted",
        fingerprint="fp_accepted",
        check_id="SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
        title="t lacks controls",
        severity="critical",
        category="action_surface",
        recommendation="declare them",
        tool_id="tool_v2_aaaa",
        tool_name="t",
        blocks_release=True,
        baseline_status="matched",
    )
    report = _report_with(
        [accepted],
        catalog=[{"tool_id": "tool_v2_aaaa", "name": "t", "provider": "mcp"}],
        agent={"id": "agent:p/a", "name": "a"},
    )
    report.release_decision = _decision(blockers=[], review_items=[accepted])

    (group,) = roll_up_findings(report)
    assert not group.blocks_release
    assert group_summary(group).startswith("review")
    assert "(blocks release)" not in finding_line(accepted, blocks_release=False)


def test_a_shared_title_does_not_borrow_another_findings_verdict():
    """Two findings can share a check id and a title — ``conductor_agent``
    ships exactly that pair — so the check-id-and-title fallback is applied
    only to a decision item that carries no id and no fingerprint."""

    def _finding(finding_id: str, pointer: str) -> Finding:
        return Finding(
            id=finding_id,
            fingerprint=finding_id,
            check_id="SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
            title="Conductor tool surface cannot be statically enumerated",
            severity="high",
            category="conductor",
            recommendation="bind it literally",
            agent_id="agent:p/a",
            source=SourceReference(
                type="conductor_workflow", path="workflows/a.json", pointer=pointer
            ),
        )

    blocking, quiet = _finding("fp_one", "/tasks/1"), _finding("fp_two", "/tasks/4")
    report = _report_with(
        [blocking, quiet],
        catalog=[],
        agent={"id": "agent:p/a", "name": "a"},
    )
    report.release_decision = _decision(blockers=[blocking], review_items=[quiet])

    (group,) = roll_up_findings(report)
    assert dict(zip([f.id for f in group.findings], group.blocking, strict=True)) == {
        "fp_one": True,
        "fp_two": False,
    }


def test_an_item_with_no_id_still_matches_by_check_and_title():
    """The fallback exists for a report predating id assignment, and removing
    the collision must not remove the fallback with it."""

    finding = Finding(
        check_id="SHIP-EXAMPLE",
        title="one thing",
        severity="medium",
        category="example",
        recommendation="do the thing",
        agent_id="agent:p/a",
    )
    report = _report_with(
        [finding], catalog=[], agent={"id": "agent:p/a", "name": "a"}
    )
    report.release_decision = _decision(blockers=[finding], review_items=[])
    report.release_decision.blockers[0].id = None
    report.release_decision.blockers[0].fingerprint = None

    (group,) = roll_up_findings(report)
    # A medium is selected only because the decision names it, and it is
    # marked blocking through the same fallback.
    assert group.findings == (finding,)
    assert group.blocks_release


def test_a_shared_fingerprint_does_not_borrow_another_findings_verdict():
    """A fingerprint is not an identity.

    It hashes check id, tool id and evidence, so two findings can share one —
    which is precisely why ``assign_finding_ids`` appends a discriminator when
    they do, and ``_to_item`` copies both values onto the decision item.
    Consulting the fingerprint for an item that carries an id marks the
    collision partner as blocking too.
    """

    def _finding(finding_id: str) -> Finding:
        return Finding(
            id=finding_id,
            fingerprint="fp_shared",
            check_id="SHIP-EXAMPLE",
            title=f"about {finding_id}",
            severity="high",
            category="example",
            recommendation="do the thing",
            agent_id="agent:p/a",
        )

    blocking, quiet = _finding("fp_shared_a"), _finding("fp_shared_b")
    report = _report_with(
        [blocking, quiet], catalog=[], agent={"id": "agent:p/a", "name": "a"}
    )
    report.release_decision = _decision(blockers=[blocking], review_items=[quiet])

    (group,) = roll_up_findings(report)
    assert dict(zip([f.id for f in group.findings], group.blocking, strict=True)) == {
        "fp_shared_a": True,
        "fp_shared_b": False,
    }


def test_an_item_with_only_a_fingerprint_still_matches():
    """Dropping the collision must not drop the tier.

    A decision item that carries a fingerprint and no id has nothing more
    precise to offer, so the fingerprint is the right key for it.
    """

    finding = Finding(
        fingerprint="fp_only",
        check_id="SHIP-EXAMPLE",
        title="one thing",
        severity="medium",
        category="example",
        recommendation="do the thing",
        agent_id="agent:p/a",
    )
    report = _report_with(
        [finding], catalog=[], agent={"id": "agent:p/a", "name": "a"}
    )
    report.release_decision = _decision(blockers=[finding], review_items=[])
    report.release_decision.blockers[0].id = None

    (group,) = roll_up_findings(report)
    assert group.findings == (finding,)
    assert group.blocks_release


def test_an_empty_missing_list_never_renders_a_hole():
    """Unreachable through the checks — every branch is inside ``if missing:``
    — so it is a wiring mistake, and the useful answer to one is the sentence
    that was correct before #364, not ``Declare  for this … action.``"""

    sentence = missing_control_recommendation(["financial_write"], [])
    assert "Declare  " not in sentence
    assert _named_controls(sentence) == BUILTIN_EFFECT_OBLIGATIONS["financial_write"]


def test_a_finding_carrying_only_a_name_joins_that_tools_group():
    """``checks.baseline_integrity`` emits ``tool_id=None`` with a name.

    Keyed on the name, that finding opened a *second* heading beside the
    tool's own — ``create_refund`` and ``create_refund [stripe]`` — which is
    the second-spelling failure ``catalog_label_index`` exists to prevent,
    arrived at from the other side.
    """

    scoped = Finding(
        check_id="SHIP-AUTH-MISSING-SCOPE",
        title="create_refund lacks declared auth scopes",
        severity="high",
        category="auth",
        recommendation="declare them",
        tool_id="tool_v2_aaaa",
        tool_name="create_refund",
        agent_id="agent:p/a",
    )
    unkeyed = Finding(
        check_id="SHIP-BASELINE-INTEGRITY-MISMATCH",
        title="create_refund baseline entry does not match",
        severity="high",
        category="baseline",
        recommendation="re-save the baseline",
        tool_id=None,
        tool_name="create_refund",
        agent_id="agent:p/a",
    )
    report = _report_with(
        [scoped, unkeyed],
        catalog=[{"tool_id": "tool_v2_aaaa", "name": "create_refund", "provider": "stripe"}],
        agent={"id": "agent:p/a", "name": "a"},
    )

    (group,) = roll_up_findings(report)
    assert group.subject == "create_refund [stripe]"
    assert {finding.check_id for finding in group.findings} == {
        "SHIP-AUTH-MISSING-SCOPE",
        "SHIP-BASELINE-INTEGRITY-MISMATCH",
    }


def test_one_tool_listed_twice_is_not_an_ambiguous_name():
    """Ambiguity is two *tools* answering to a name, not two rows.

    Marking a repeated catalog row ambiguous would push a resolvable finding
    back into its own heading for no reason.
    """

    unkeyed = Finding(
        check_id="SHIP-BASELINE-INTEGRITY-MISMATCH",
        title="create_refund baseline entry does not match",
        severity="high",
        category="baseline",
        recommendation="re-save the baseline",
        tool_id=None,
        tool_name="create_refund",
        agent_id="agent:p/a",
    )
    row = {"tool_id": "tool_v2_aaaa", "name": "create_refund", "provider": "stripe"}
    report = _report_with(
        [unkeyed], catalog=[row, dict(row)], agent={"id": "agent:p/a", "name": "a"}
    )

    (group,) = roll_up_findings(report)
    assert group.subject == "create_refund [stripe]"


def test_an_ambiguous_name_is_left_unresolved_rather_than_guessed():
    """Two tools can share a name across sources.

    Picking one would file a finding under a tool it is not about, which is
    worse than a heading the reader has to reconcile.
    """

    unkeyed = Finding(
        check_id="SHIP-BASELINE-INTEGRITY-MISMATCH",
        title="create_refund baseline entry does not match",
        severity="high",
        category="baseline",
        recommendation="re-save the baseline",
        tool_id=None,
        tool_name="create_refund",
        agent_id="agent:p/a",
    )
    report = _report_with(
        [unkeyed],
        catalog=[
            {"tool_id": "tool_v2_aaaa", "name": "create_refund", "provider": "stripe"},
            {"tool_id": "tool_v2_bbbb", "name": "create_refund", "provider": "adyen"},
        ],
        agent={"id": "agent:p/a", "name": "a"},
    )

    (group,) = roll_up_findings(report)
    assert group.subject == "create_refund"


def test_the_report_never_prints_the_missing_list_twice(tmp_path):
    """``report.md`` annotates a row with its recommendation — except where
    the row already *is* the missing list the recommendation is derived from.

    The reason the compact surfaces skip the sentence is that it repeats the
    row in different words; that reason does not stop applying inside a
    report.
    """

    report, out = _scan(tmp_path)
    markdown = (out / "report.md").read_text(encoding="utf-8")
    section = markdown.split("## Top Findings")[1].split("## Finding Provenance")[0]

    assert "missing: safeguards.audit\\_log, safeguards.idempotency" in section
    assert "Declare safeguards.audit\\_log and safeguards.idempotency" not in section
    # A row with no missing list keeps its sentence.
    assert "Declare an owner for each high-risk production tool" in section
