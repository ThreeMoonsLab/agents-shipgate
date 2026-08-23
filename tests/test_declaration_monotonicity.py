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
    """The rule is monotone: only de-escalation is compared."""

    report = _scan(_project(tmp_path, _ESCALATED), tmp_path / "reports")
    coverage = report.release_decision.evidence_coverage

    assert not [
        gap
        for gap in coverage.evidence_gaps
        if gap.kind == "declaration_below_inferred_evidence"
    ]
    assert coverage.semantic_coverage.review_concern_count == 0


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
