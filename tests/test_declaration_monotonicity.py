"""#409 — a declaration weaker than inferred evidence is never silent.

The trust anchor is "a human said so outranks the scanner guessed", and that is
right. What was missing is that nothing told the human, or their reviewer, when
what they said is contradicted by what the scanner observed. Declaring
``effect: read`` on a tool this scanner itself tagged ``external_write`` closed
the very gap that evidence raised and made the action pass-eligible with zero
findings.

These tests pin the whole rule end to end, in both directions: escalation past
the evidence stays silent, agreement stays silent, a de-escalation is owed an
answer, and an acknowledged override is accepted but reported.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents_shipgate.cli.scan import run_scan

_AGENT_SOURCE = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def send_email(to: str, body: str) -> dict:
    """Send an email."""
    return {"status": "sent"}


root_agent = LlmAgent(
    name="closer_agent",
    instruction="Route approvals.",
    tools=[FunctionTool(func=send_email)],
)
'''

_MANIFEST = """
version: "0.1"
project:
  name: declaration-monotonicity
agent:
  name: closer-agent
  declared_purpose:
    - route approval mail
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
"""


def _project(root: Path, declaration: str) -> Path:
    project = root / "project"
    project.mkdir(exist_ok=True)
    (project / "agent.py").write_text(_AGENT_SOURCE, encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        _MANIFEST + "action_surface:\n  actions:\n" + declaration,
        encoding="utf-8",
    )
    return project / "shipgate.yaml"


def _scan(config: Path, out: Path):
    report, _ = run_scan(
        config_path=config,
        output_dir=out,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    return report


_WEAK = """    - tool: send_email
      source_id: adk_agent
      effect: read
      authority:
        mode: none
"""
_ACKNOWLEDGED = """    - tool: send_email
      source_id: adk_agent
      effect: read
      override:
        evidence: agent.py returns a canned status; no mail client is constructed
        reason: the name matches the comms heuristic but the body sends nothing
      authority:
        mode: none
"""
_HONEST = """    - tool: send_email
      source_id: adk_agent
      effect: external_communication
      authority:
        mode: none
"""
_ESCALATED = """    - tool: send_email
      source_id: adk_agent
      effect: destructive
      risk_tags: [external_write]
      authority:
        mode: none
"""

#: Escalation *across* categories. `destructive` outranks the inferred
#: `external_communication` on risk but obliges rollback rather than the audit
#: log communicating outward requires, so it does not account for it — and the
#: row says which controls are missing rather than telling the reviewer to
#: raise an effect that is already higher.
_ESCALATED_ACROSS_CATEGORIES = """    - tool: send_email
      source_id: adk_agent
      effect: destructive
      authority:
        mode: none
"""


def test_declaration_below_inferred_evidence_is_reported(tmp_path: Path) -> None:
    """The #409 reproduction: `read` on a tool tagged `external_write`."""

    report = _scan(_project(tmp_path, _WEAK), tmp_path / "reports")
    coverage = report.release_decision.evidence_coverage
    rows = [
        gap
        for gap in coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    ]

    assert len(rows) == 1
    row = rows[0]
    # The row names the subject, what was declared, and what was inferred —
    # a reviewer must not have to join two artifacts to see the conflict.
    assert "send_email" in row.subject
    assert "'read'" in row.why
    assert "'external_communication'" in row.why
    assert row.next_action.kind == "resolve_semantic_conflict"
    # The instruction names the value. The short headline every human surface
    # renders is the per-kind phrase, not this row's `why`, so an instruction
    # that only pointed back at the row named the effect nowhere the user
    # could see it — and `accepted_values` is the vocabulary for exactly the
    # field `expects` names.
    assert "Raise action_surface.actions[].effect to 'external_communication'" in (
        row.next_action.expects
    )
    assert "external_communication" in row.next_action.accepted_values
    assert row.next_action.declaration_template is not None
    assert set(row.next_action.declaration_template["override"]) == {"evidence", "reason"}
    # A human assertion, never a machine-applicable patch.
    assert row.next_action.suggested_patch_kind == "manual"
    assert row.next_action.auto_apply is False
    assert row.next_action.requires_human_review is True

    action = report.action_surface_facts.actions[0]
    assert action.semantic_assessment is not None
    assert action.semantic_assessment.pass_eligible is False
    # The declaration still wins: heuristics do not drive the verdict (#357).
    assert action.semantic_assessment.effect.status == "declared"
    assert report.release_decision.decision != "passed"


def test_the_report_still_carries_the_contradicting_evidence(tmp_path: Path) -> None:
    """The evidence was always in the artifact; nothing joined it (#409)."""

    report = _scan(_project(tmp_path, _WEAK), tmp_path / "reports")
    action = report.action_surface_facts.actions[0]

    assert "external_communication" in action.risk_tags
    assert action.effect == "external_communication"


def test_acknowledged_override_is_accepted_and_reported(tmp_path: Path) -> None:
    """Accepted, pass-eligible — and never silent."""

    report = _scan(_project(tmp_path, _ACKNOWLEDGED), tmp_path / "reports")
    coverage = report.release_decision.evidence_coverage

    assert not [
        gap
        for gap in coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    ]
    semantic = coverage.semantic_coverage
    assert semantic.reason_counts.get("acknowledged_effect_override") == 1
    assert semantic.review_concern_count == 1

    action = report.action_surface_facts.actions[0]
    assert action.semantic_assessment is not None
    assert action.semantic_assessment.pass_eligible is True

    override_claims = [
        claim
        for claim in action.semantic_assessment.effect.claims
        if claim.source == "action_surface_declaration_override"
    ]
    assert len(override_claims) == 1
    assert override_claims[0].evidence["overridden_effect"] == "external_communication"

    # An acknowledged override can never reach `passed`: the reviewer has to
    # see the exception, which is the entire point of accepting it.
    assert report.release_decision.decision != "passed"

    # …and it must actually *land* on the review route. Policy applicability
    # asks the same question the acknowledgement answers, so leaving the
    # acknowledged claim unresolved there traded one gap for another and kept
    # the verdict at `insufficient_evidence` — the reviewer followed this row's
    # own instruction and got a differently-named dead end (review 1).
    assert report.release_decision.decision == "review_required"
    assert coverage.policy_gap_count == 0
    assert report.policy_evidence_gaps == []
    assert coverage.evidence_gaps == []


def test_each_acknowledged_override_is_projected_for_the_reviewer(
    tmp_path: Path,
) -> None:
    """A count is not a review surface (#409, review 2).

    The reviewer reads exceptions, not every action, so each one has to name the
    action, both readings, the hint source, and the human's evidence and reason.
    """

    report = _scan(_project(tmp_path, _ACKNOWLEDGED), tmp_path / "reports")
    rows = report.release_decision.evidence_coverage.semantic_coverage.acknowledged_overrides

    assert len(rows) == 1
    row = rows[0]
    assert "send_email" in row.subject
    assert row.subject_id
    assert row.declared_effect == "read"
    assert row.inferred_effect == "external_communication"
    assert row.inferred_sources
    assert "no mail client is constructed" in row.evidence
    assert "the body sends nothing" in row.reason
    assert row.manifest_path.endswith("].override")

    # …and the PR comment carries the same row.
    from agents_shipgate.report.pr_projection import select_pr_items

    items = select_pr_items(json.loads(report.model_dump_json()))
    override_items = [
        item
        for item in items
        if item.check_id == "SHIP-ACTION-EFFECT-OVERRIDE-ACKNOWLEDGED"
    ]
    assert len(override_items) == 1
    item = override_items[0]
    assert "send_email" in item.title
    assert "'read'" in item.message
    assert "'external_communication'" in item.message
    assert "no mail client is constructed" in item.message
    assert item.merge_impact == "review_required"


def test_the_packet_renders_each_override_not_just_the_count(tmp_path: Path) -> None:
    from agents_shipgate.packet.builder import build_packet_from_report
    from agents_shipgate.packet.html import render_packet_html
    from agents_shipgate.packet.markdown import render_packet_markdown

    report = _scan(_project(tmp_path, _ACKNOWLEDGED), tmp_path / "reports")
    packet = build_packet_from_report(report)

    for rendered in (render_packet_markdown(packet), render_packet_html(packet)):
        assert "Acknowledged override:" in rendered
        # Markdown escapes the underscore in the tool name, so match on the
        # parts that survive both renderers.
        assert "send" in rendered and "email" in rendered
        assert "external_communication" in rendered or "external\\_communication" in rendered
        assert "no mail client is constructed" in rendered


def test_declaration_matching_the_evidence_is_silent(tmp_path: Path) -> None:
    """Negative control: agreement raises nothing."""

    report = _scan(_project(tmp_path, _HONEST), tmp_path / "reports")
    coverage = report.release_decision.evidence_coverage

    assert not [
        gap
        for gap in coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    ]
    assert coverage.semantic_coverage.reason_counts.get("acknowledged_effect_override") is None


def test_conservative_escalation_is_silent(tmp_path: Path) -> None:
    """The rule is monotone: only an unaccounted-for observation is compared.

    Escalating past the inference costs nothing as long as the declaration
    still carries what the observation obliges. Here `risk_tags` keeps the
    external-communication controls applied, so nothing is left unanswered.
    """

    report = _scan(_project(tmp_path, _ESCALATED), tmp_path / "reports")
    coverage = report.release_decision.evidence_coverage

    assert not [
        gap
        for gap in coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    ]
    assert coverage.semantic_coverage.review_concern_count == 0


def test_escalating_across_categories_is_not_a_free_pass(tmp_path: Path) -> None:
    """Rank is a total order; obligations are not.

    `destructive` outranks `external_communication` and requires approval,
    rollback, and confirmation — but no audit log, which is what communicating
    outward requires. Comparing rank alone let this action report pass-eligible
    with no gap while its external-write obligation went unapplied.
    """

    report = _scan(
        _project(tmp_path, _ESCALATED_ACROSS_CATEGORIES), tmp_path / "reports"
    )
    coverage = report.release_decision.evidence_coverage
    gap = next(
        gap
        for gap in coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    )

    assert "does not carry the controls required by" in gap.why
    # "Raise the effect" would ask for a *lower* assessment here, so the row
    # publishes the route that keeps the declared effect and makes the missing
    # category's controls apply — and the template carries it, filled in.
    assert "risk_tags: [external_communication]" in gap.next_action.expects
    assert "Raise action_surface.actions[].effect" not in gap.next_action.expects
    assert gap.next_action.declaration_template["risk_tags"] == ["external_communication"]
    assert gap.next_action.accepted_values == ["external_communication"]
    assert coverage.semantic_coverage.pass_eligible_actions == 0


def test_the_scaffold_offers_the_override_block(tmp_path: Path) -> None:
    """The file the reviewer is told to edit says what a legal answer is."""

    out = tmp_path / "reports"
    _scan(_project(tmp_path, _WEAK), out)
    scaffold = (out / "suggested-declarations.yaml").read_text(encoding="utf-8")

    assert "closes: declaration_below_inferred_evidence" in scaffold
    assert "override:" in scaffold
    assert "evidence: <REVIEW_REQUIRED>" in scaffold
    assert "reason: <REVIEW_REQUIRED>" in scaffold


def _coverage(**reason_counts: int):
    from agents_shipgate.schemas.report import (
        EvidenceCoverageDecision,
        SemanticCoverageDecision,
    )

    return EvidenceCoverageDecision(
        level="static",
        human_review_recommended=False,
        source_warning_count=0,
        low_confidence_tool_count=0,
        semantic_coverage=SemanticCoverageDecision(
            total_actions=3,
            pass_eligible_actions=3,
            gap_count=0,
            review_concern_count=sum(reason_counts.values()),
            reason_counts=dict(reason_counts),
        ),
    )


def test_review_reason_names_the_concern_it_actually_found() -> None:
    """Two concerns share the review tier; naming the wrong one is worse than
    naming neither. An acknowledged effect override has nothing to do with
    authority mode."""

    from agents_shipgate.ci.release_decision import _decision_reason

    override_only = _decision_reason(
        "review_required", [], [], _coverage(acknowledged_effect_override=2)
    )
    assert "2 acknowledged declarations sit below inferred effect evidence" in override_only
    assert "authority" not in override_only

    authority_only = _decision_reason(
        "review_required", [], [], _coverage(unscoped_authority=1)
    )
    assert "1 action uses known unscoped or ambient authority" in authority_only
    assert "unscoped or ambient authority" in authority_only
    assert "override" not in authority_only

    both = _decision_reason(
        "review_required",
        [],
        [],
        _coverage(ambient_authority=1, acknowledged_effect_override=1),
    )
    assert "unscoped or ambient authority" in both
    assert "1 acknowledged declaration sits below inferred effect evidence" in both


def test_the_instruction_and_the_reason_name_the_same_effect(tmp_path: Path) -> None:
    """One comparator, so the row cannot tell a reviewer two different things.

    ``expects`` is built in the release-decision projection and ``why`` is built
    in the resolver. Deriving the value twice is the recurring defect class in
    this codebase, so both read
    ``semantic_assessment.claims_above_declared_effect``.
    """

    report = _scan(_project(tmp_path, _WEAK), tmp_path / "reports")
    row = next(
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    )

    named_in_reason = [
        effect for effect in row.next_action.accepted_values if f"'{effect}'" in row.why
    ]
    # `read` is the declared value and also appears quoted, so compare the set.
    assert set(named_in_reason) == {"read", "external_communication"}
    assert "'external_communication'" in row.next_action.expects
    assert "'read'" not in row.next_action.expects
