"""#409 — a declaration weaker than the evidence must not be accepted in silence.

The fail-open these cover: declaring ``effect: read`` on a tool Shipgate itself
tagged ``external_write`` closed the ``inferred_effect_only`` gap that the very
same heuristic had raised, made the action pass-eligible, and produced zero
findings. The contradiction check existed; its filter admitted only
policy-eligible claims, so an inferred claim could not challenge a declaration
even though it was sitting in the same assessment.

The rule is monotone, so every test here comes in both directions: a
declaration at or above its evidence stays silent, and only de-escalation is
recorded.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents_shipgate.ci.release_decision import build_release_decision
from agents_shipgate.core.domain import AuthInfo, Tool, ToolRiskHint
from agents_shipgate.core.semantic_assessment import (
    assess_tool_semantics,
    attach_semantic_assessments,
)
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment
from agents_shipgate.schemas.manifest import ActionDeclarationConfig
from agents_shipgate.schemas.report import (
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)

TOOL_ID = "google_adk:smart_closer:send_email"


def _send_email(**updates: object) -> Tool:
    """The #409 reproduction subject: a name heuristic says it talks outward."""

    values: dict[str, object] = {
        "id": TOOL_ID,
        "name": "send_email",
        "source_type": "google_adk",
        "source_id": "smart_closer",
        "provider": "smart_closer",
        "source_pointer": "agent.py",
        "extraction_confidence": "high",
        "extraction": {"surface": "enumerated"},
        "risk_hints": [
            ToolRiskHint(
                tag="external_write",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
                evidence={"matched": "send"},
            ),
            ToolRiskHint(
                tag="customer_communication",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
                evidence={"matched": "email"},
            ),
        ],
    }
    values.update(updates)
    return Tool.model_validate(values)


def _declaration(**updates: object) -> ActionDeclarationConfig:
    payload: dict[str, object] = {
        "tool": "send_email",
        "effect": "read",
        "authority": {"mode": "none"},
    }
    payload.update(updates)
    return ActionDeclarationConfig.model_validate(payload)


def _effect_issues(tool: Tool, declaration: ActionDeclarationConfig | None):
    assessment = assess_tool_semantics(tool, declaration)
    return assessment, {issue.kind for issue in assessment.effect.issues}


# --------------------------------------------------------------------------
# The resolver
# --------------------------------------------------------------------------


def test_a_declaration_below_inferred_evidence_is_recorded() -> None:
    """The reproduction from #409, now answered instead of accepted."""

    assessment, kinds = _effect_issues(_send_email(), _declaration())

    assert "declaration_below_inferred_evidence" in kinds
    assert assessment.pass_eligible is False
    # The declaration still stands as the operative effect: a human outranks a
    # heuristic. What changed is that the disagreement is on the record.
    assert assessment.effect.status == "declared"
    assert assessment.conservative_effect == "external_communication"

    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )
    assert "'read'" in issue.message
    assert "external_communication" in issue.message
    assert "risk_hint:name" in issue.message
    assert issue.source_pointer == "action_surface.actions[tool='send_email'].effect"


def test_a_declaration_matching_the_evidence_stays_silent() -> None:
    """Negative control: agreement is not a contradiction."""

    _, kinds = _effect_issues(
        _send_email(), _declaration(effect="external_communication")
    )

    assert "declaration_below_inferred_evidence" not in kinds


@pytest.mark.parametrize("effect", ["financial_write", "destructive"])
def test_an_escalating_declaration_stays_silent(effect: str) -> None:
    """The rule is monotone: only de-escalation is reported."""

    assessment, kinds = _effect_issues(_send_email(), _declaration(effect=effect))

    assert "declaration_below_inferred_evidence" not in kinds
    assert assessment.pass_eligible is True


def test_a_tool_with_no_inferred_evidence_stays_silent() -> None:
    """Nothing observed above the declaration means nothing to override."""

    assessment, kinds = _effect_issues(
        _send_email(name="lookup_order", risk_hints=[]), _declaration(tool="lookup_order")
    )

    assert kinds == set()
    assert assessment.pass_eligible is True


def test_a_protocol_default_is_not_an_observation() -> None:
    """An MCP tool that says nothing about itself is not contradicting anyone.

    The protocol default exists precisely because the surface is silent, and a
    declaration is the answer to that silence. Treating it as evidence would
    make every MCP declaration a permanent override.
    """

    tool = Tool.model_validate(
        {
            "id": "mcp:orders:process_order",
            "name": "process_order",
            "source_type": "mcp",
            "source_id": "orders",
            "source_pointer": "/tools/0",
            "extraction_confidence": "high",
            "extraction": {"method": "mcp_json", "confidence": "high"},
            "auth": AuthInfo(source="mcp", mode="none", explicit=True),
        }
    )
    assessment, kinds = _effect_issues(
        tool, ActionDeclarationConfig.model_validate({"tool": "process_order", "effect": "read"})
    )

    assert kinds == set()
    assert assessment.pass_eligible is True


def test_a_policy_eligible_contradiction_keeps_its_own_verdict() -> None:
    """Regression guard: the high-confidence path is untouched by #409.

    A structural write scope still resolves to ``conflicting`` with
    ``conflicting_effect_evidence``, and must not be re-labelled as the softer
    override finding.
    """

    tool = Tool.model_validate(
        {
            "id": "mcp:orders:process_order",
            "name": "process_order",
            "source_type": "mcp",
            "source_id": "orders",
            "source_pointer": "/tools/0",
            "extraction_confidence": "high",
            "extraction": {"method": "mcp_json", "confidence": "high"},
            "auth": AuthInfo(
                type="oauth2",
                scopes=["orders:write"],
                source="mcp",
                mode="scoped",
                explicit=True,
            ),
        }
    )
    assessment, kinds = _effect_issues(
        tool,
        ActionDeclarationConfig.model_validate(
            {
                "tool": "process_order",
                "effect": "read",
                "scopes": ["orders:write"],
                "authority": {"mode": "scoped", "auth_type": "oauth2"},
            }
        ),
    )

    assert "conflicting_effect_evidence" in kinds
    assert "declaration_below_inferred_evidence" not in kinds
    assert assessment.effect.status == "conflicting"


# --------------------------------------------------------------------------
# The override
# --------------------------------------------------------------------------


def test_an_exact_override_is_accepted() -> None:
    assessment, kinds = _effect_issues(
        _send_email(),
        _declaration(
            override={
                "evidence": ["external_communication"],
                "reason": "Renders a preview; delivery happens in send_email_now.",
            }
        ),
    )

    assert kinds == set()
    assert assessment.pass_eligible is True
    # The override changes who answers for the effect, never what the union of
    # evidence says the action can do.
    assert assessment.conservative_effect == "external_communication"


def test_an_override_cannot_pre_acknowledge_absent_evidence() -> None:
    """The fail-open one layer up: acknowledge everything once, stay silent forever.

    Containment would accept this. Exact match is what keeps an override tied
    to the evidence that justified it.
    """

    assessment, kinds = _effect_issues(
        _send_email(),
        _declaration(
            override={
                "evidence": ["external_communication", "destructive"],
                "reason": "covering my future self",
            }
        ),
    )

    assert "declaration_below_inferred_evidence" in kinds
    assert assessment.pass_eligible is False
    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )
    assert "destructive" in issue.message


def test_an_override_that_misses_one_observation_reopens_the_question() -> None:
    """New evidence re-opens a settled override, naming only what is new."""

    tool = _send_email(
        risk_hints=[
            ToolRiskHint(
                tag="external_write",
                source="name",
                confidence="medium",
                basis="inferred_keyword",
            ),
            ToolRiskHint(
                tag="financial_action",
                source="body",
                confidence="medium",
                basis="inferred_keyword",
            ),
        ]
    )
    assessment, kinds = _effect_issues(
        tool,
        _declaration(
            override={"evidence": ["external_communication"], "reason": "preview only"}
        ),
    )

    assert "declaration_below_inferred_evidence" in kinds
    assert assessment.pass_eligible is False
    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )
    assert "financial_write" in issue.message
    assert "risk_hint:body" in issue.message


def test_an_override_whose_evidence_disappeared_reopens_the_question() -> None:
    """Drift in the other direction: a stale override is not a silent one."""

    declaration = _declaration(
        override={"evidence": ["external_communication"], "reason": "preview only"}
    )
    tool = _send_email(risk_hints=[])
    assessment, kinds = _effect_issues(tool, declaration)

    assert "declaration_below_inferred_evidence" in kinds
    assert assessment.pass_eligible is False
    # The remedy must be true of *this* state. "Raise the declared effect" is
    # advice about evidence that no longer exists.
    finding = _effect_override_findings_for(
        attach_semantic_assessments([tool], {TOOL_ID: declaration})[0], declaration
    )[0]
    assert "no longer observes" in finding.title
    assert "Remove action_surface.actions[].override" in finding.recommendation
    assert finding.evidence["stale_evidence"] == ["external_communication"]


@pytest.mark.parametrize(
    "override, message",
    [
        ({"evidence": [], "reason": "x"}, "at least 1 item"),
        ({"evidence": ["write"], "reason": "   "}, "override.reason"),
        (
            {"evidence": ["write", "write"], "reason": "x"},
            "must not repeat an effect",
        ),
    ],
)
def test_override_shape_is_validated(override: dict, message: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        ActionDeclarationConfig.model_validate(
            {"tool": "send_email", "effect": "read", "override": override}
        )
    assert message in str(excinfo.value)


def test_an_override_without_a_declared_effect_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ActionDeclarationConfig.model_validate(
            {
                "tool": "send_email",
                "override": {"evidence": ["write"], "reason": "x"},
            }
        )
    assert "requires effect" in str(excinfo.value)


def test_an_override_naming_its_own_declared_effect_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ActionDeclarationConfig.model_validate(
            {
                "tool": "send_email",
                "effect": "read",
                "override": {"evidence": ["read"], "reason": "x"},
            }
        )
    assert "must not repeat the declared effect" in str(excinfo.value)


# --------------------------------------------------------------------------
# The projections: the gap a coding agent works, the finding a human reads
# --------------------------------------------------------------------------


def _report_and_decision(declaration: ActionDeclarationConfig):
    tools = attach_semantic_assessments([_send_email()], {TOOL_ID: declaration})
    report = ReadinessReport(
        run_id="run-1",
        project={},
        agent={},
        environment={},
        summary=ReportSummary(status="review_required"),
        tool_surface=ToolSurfaceSummary(total_tools=1, high_risk_tools=0),
        binding_surface_facts=AgentBindingGraphAssessment(
            root_agent_id="agent",
            status="structural",
            pass_eligible=True,
            reachable_tool_ids=[TOOL_ID],
        ),
    )
    decision = build_release_decision(
        report=report,
        tools=tools,
        ci_mode="advisory",
        fail_on=None,
        new_findings_only=False,
    )
    return tools, decision


def test_the_gap_scaffolds_both_ways_out() -> None:
    _, decision = _report_and_decision(_declaration())
    gaps = [
        gap
        for gap in decision.evidence_coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    ]

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.subject_id == TOOL_ID
    assert gap.next_action.path == "shipgate.yaml#action_surface.actions[tool='send_email']"
    template = gap.next_action.declaration_template
    assert template is not None
    # The declared effect is echoed back, not blanked: the reviewer already
    # answered that question, and the override list is filled in completely
    # because an override is only accepted when it names exactly this set.
    assert template["effect"] == "read"
    assert template["override"]["evidence"] == ["external_communication"]
    assert template["override"]["reason"] == "<REVIEW_REQUIRED>"


def test_the_scaffolded_override_is_a_valid_declaration() -> None:
    """The template must be pasteable, not merely printable."""

    _, decision = _report_and_decision(_declaration())
    gap = next(
        gap
        for gap in decision.evidence_coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    )
    template = dict(gap.next_action.declaration_template or {})
    template["override"] = dict(template["override"])
    template["override"]["reason"] = "Renders a preview only."

    declaration = ActionDeclarationConfig.model_validate(template)
    _, kinds = _effect_issues(_send_email(), declaration)

    assert kinds == set()


def test_a_stale_override_gap_does_not_scaffold_a_blank_to_fill() -> None:
    """The two states that reach this gap need opposite advice.

    When the evidence an override answers is gone there is no block to paste,
    only one to remove, and a template offering a blank would send the
    reviewer the wrong way.
    """

    tools = attach_semantic_assessments(
        [_send_email(risk_hints=[])],
        {
            TOOL_ID: _declaration(
                override={"evidence": ["external_communication"], "reason": "preview only"}
            )
        },
    )
    report = ReadinessReport(
        run_id="run-1",
        project={},
        agent={},
        environment={},
        summary=ReportSummary(status="review_required"),
        tool_surface=ToolSurfaceSummary(total_tools=1, high_risk_tools=0),
        binding_surface_facts=AgentBindingGraphAssessment(
            root_agent_id="agent",
            status="structural",
            pass_eligible=True,
            reachable_tool_ids=[TOOL_ID],
        ),
    )
    decision = build_release_decision(
        report=report,
        tools=tools,
        ci_mode="advisory",
        fail_on=None,
        new_findings_only=False,
    )
    gap = next(
        gap
        for gap in decision.evidence_coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    )

    assert gap.next_action.declaration_template is None
    assert "Remove the action_surface.actions[].override" in gap.next_action.expects


def test_an_unacknowledged_declaration_is_not_pass_eligible() -> None:
    _, decision = _report_and_decision(_declaration())

    assert decision.evidence_coverage.semantic_coverage.pass_eligible_actions == 0
    assert decision.decision == "insufficient_evidence"


def test_an_acknowledged_override_is_pass_eligible_but_never_silent() -> None:
    _, decision = _report_and_decision(
        _declaration(
            override={"evidence": ["external_communication"], "reason": "preview only"}
        )
    )

    assert decision.evidence_coverage.semantic_coverage.pass_eligible_actions == 1
    assert not [
        gap
        for gap in decision.evidence_coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    ]


def test_the_pr_comment_names_every_override_row() -> None:
    """A reviewer decides whether to trust the manifest on this surface.

    The finding is ``medium``, and the PR comment's top-findings list shows
    only critical and high, so without an explicit row the one place the
    disagreement matters most would never mention it.
    """

    from agents_shipgate.report.pr_comment import _effect_override_lines

    tools = attach_semantic_assessments([_send_email()], {TOOL_ID: _declaration()})
    report = ReadinessReport(
        run_id="run-1",
        project={},
        agent={},
        environment={},
        summary=ReportSummary(status="review_required"),
        tool_surface=ToolSurfaceSummary(total_tools=1, high_risk_tools=0),
    )
    report.findings = _effect_override_findings_for(tools[0], _declaration())
    lines = _effect_override_lines(report)

    assert lines[0] == "- Declaration overrides (1):"
    assert "declares `read`" in lines[1]
    assert "evidence says `external_communication`" in lines[1]
    assert "risk" in lines[1]
    assert "NOT acknowledged" in lines[1]


def test_the_pr_comment_row_carries_the_reviewed_reason() -> None:
    from agents_shipgate.report.pr_comment import _effect_override_lines

    declaration = _declaration(
        override={"evidence": ["external_communication"], "reason": "preview only"}
    )
    tools = attach_semantic_assessments([_send_email()], {TOOL_ID: declaration})
    report = ReadinessReport(
        run_id="run-1",
        project={},
        agent={},
        environment={},
        summary=ReportSummary(status="review_required"),
        tool_surface=ToolSurfaceSummary(total_tools=1, high_risk_tools=0),
    )
    report.findings = _effect_override_findings_for(tools[0], declaration)
    lines = _effect_override_lines(report)

    assert "— acknowledged — preview only" in lines[1]


def _effect_override_findings_for(tool, declaration):
    """The lens findings for one already-assessed tool."""

    from agents_shipgate.core.lenses.action_surface import (
        build_action_surface_facts,
        evaluate_action_surface_policies,
    )
    from agents_shipgate.schemas.manifest import AgentsShipgateManifest
    from agents_shipgate.schemas.surfaces import ActionSurfaceDiff

    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "repro"},
            "agent": {"name": "agent", "declared_purpose": ["x"]},
            "environment": {"target": "local"},
            "tool_sources": [{"id": "smart_closer", "type": "mcp", "path": "tools.json"}],
            "action_surface": {"actions": [declaration.model_dump(exclude_none=True)]},
        }
    )
    facts = build_action_surface_facts(manifest, agent_id="agent", tools=[tool])
    diff = ActionSurfaceDiff(enabled=False)
    findings = evaluate_action_surface_policies(
        manifest,
        facts,
        diff,
        agent_id="agent",
        tools=[tool],
    )
    return [
        finding
        for finding in findings
        if finding.check_id == "SHIP-ACTION-EFFECT-OVERRIDES-EVIDENCE"
    ]


def test_the_pr_comment_shows_a_stale_override_in_the_state_it_is_in() -> None:
    """A stale override carries no inferred effect, so "evidence says X" lies.

    Rendered through the shared shape it printed a literal ``?``: a reviewer
    was told a declaration disagrees with an unnamed observation, and the
    actual state — an override to delete — appeared nowhere on the comment.
    """

    from agents_shipgate.report.pr_comment import _effect_override_lines

    declaration = _declaration(
        override={"evidence": ["external_communication"], "reason": "preview only"}
    )
    tool = attach_semantic_assessments([_send_email(risk_hints=[])], {TOOL_ID: declaration})[0]
    report = _report_carrying(_effect_override_findings_for(tool, declaration))
    row = _effect_override_lines(report)[1]

    assert "?" not in row
    assert "which this scan did not observe" in row
    assert "external_communication" in row
    assert "NOT acknowledged" in row


def test_the_pr_comment_shows_unanswered_overrides_before_answered_ones() -> None:
    """The row cap must not hide the only row that needs an answer.

    Every one of these shares a check id and a severity, so in report order
    they sort by tool name — and a repository whose unanswered override is on
    a late-alphabet tool would see three acknowledged rows and a "+1 more".
    """

    from agents_shipgate.report.pr_comment import _MAX_OVERRIDE_ROWS, _effect_override_lines

    findings = []
    for name in ("alpha_send", "beta_send", "gamma_send"):
        declaration = _declaration(
            tool=name,
            override={"evidence": ["external_communication"], "reason": "reviewed"},
        )
        tool = attach_semantic_assessments(
            [_send_email(id=f"tool:{name}", name=name)], {f"tool:{name}": declaration}
        )[0]
        findings.extend(_effect_override_findings_for(tool, declaration))
    unanswered = _declaration(tool="zeta_send")
    tool = attach_semantic_assessments(
        [_send_email(id="tool:zeta_send", name="zeta_send")], {"tool:zeta_send": unanswered}
    )[0]
    findings.extend(_effect_override_findings_for(tool, unanswered))

    lines = _effect_override_lines(_report_carrying(findings))

    assert lines[0] == "- Declaration overrides (4):"
    assert "zeta_send" in lines[1]
    assert "NOT acknowledged" in lines[1]
    assert lines[-1] == f"  - (+{4 - _MAX_OVERRIDE_ROWS} more — see report.json findings)"


def test_the_pr_comment_caps_a_reviewed_reason() -> None:
    """Reason text is unbounded manifest input on a size-budgeted surface.

    The comment enforces its budget by truncating prose from the end, so an
    essay here evicts the trigger, base-diff, and artifact lines below it.
    """

    from agents_shipgate.report.pr_comment import (
        _MAX_OVERRIDE_REASON_CHARS,
        _effect_override_lines,
    )

    declaration = _declaration(
        override={"evidence": ["external_communication"], "reason": "x" * 4000}
    )
    tool = attach_semantic_assessments([_send_email()], {TOOL_ID: declaration})[0]
    row = _effect_override_lines(_report_carrying(_effect_override_findings_for(tool, declaration)))[1]

    assert len(row) < _MAX_OVERRIDE_REASON_CHARS + 200
    # Escaped, because the ellipsis lands in prose rather than a code span.
    assert row.endswith("\\.\\.\\.")


def test_an_override_cannot_answer_high_confidence_source_evidence() -> None:
    """An override answers inferred evidence and nothing else.

    Where the source itself proves the higher effect the declaration is wrong
    rather than exceptional, so the override is inert — and a written input
    that changes nothing has to be told it changed nothing.
    """

    tool = Tool.model_validate(
        {
            "id": "mcp:orders:process_order",
            "name": "process_order",
            "source_type": "mcp",
            "source_id": "orders",
            "source_pointer": "/tools/0",
            "extraction_confidence": "high",
            "extraction": {"method": "mcp_json", "confidence": "high"},
            "auth": AuthInfo(
                type="oauth2",
                scopes=["orders:write"],
                source="mcp",
                mode="scoped",
                explicit=True,
            ),
        }
    )
    payload = {
        "tool": "process_order",
        "effect": "read",
        "scopes": ["orders:write"],
        "authority": {"mode": "scoped", "auth_type": "oauth2"},
    }
    without = assess_tool_semantics(tool, ActionDeclarationConfig.model_validate(payload))
    with_override = assess_tool_semantics(
        tool,
        ActionDeclarationConfig.model_validate(
            {**payload, "override": {"evidence": ["write"], "reason": "reviewed"}}
        ),
    )

    # The verdict is identical either way: an override never opens this door.
    assert with_override.effect.status == without.effect.status == "conflicting"
    assert with_override.pass_eligible is False
    message = next(
        issue.message
        for issue in with_override.effect.issues
        if issue.kind == "conflicting_effect_evidence"
    )
    assert "override does not apply" in message
    assert "override" not in next(
        issue.message
        for issue in without.effect.issues
        if issue.kind == "conflicting_effect_evidence"
    )


def _report_carrying(findings):
    report = ReadinessReport(
        run_id="run-1",
        project={},
        agent={},
        environment={},
        summary=ReportSummary(status="review_required"),
        tool_surface=ToolSurfaceSummary(total_tools=1, high_risk_tools=0),
    )
    report.findings = findings
    return report
