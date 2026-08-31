"""P0 exact-outcome canaries for policy-evidence provenance.

These 48 cases lock the three boundaries that prevent heuristic evidence from
being promoted into an authoritative policy result: claim classification,
predicate support, and release-gate contribution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from agents_shipgate.ci.release_decision import build_release_decision
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.adopter_text import internal_vocabulary
from agents_shipgate.core.domain import EvidenceBasis, SemanticClaim
from agents_shipgate.core.evidence_actions import evidence_gap_headline
from agents_shipgate.core.policy_evidence import (
    conjunction_status,
    disjunction_status,
    finding_support,
    negated_disjunction_status,
    policy_evidence_gap,
    predicate_evidence,
)
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment
from agents_shipgate.schemas.common import Confidence, ProvenanceKind
from agents_shipgate.schemas.report import (
    Finding,
    FindingSupport,
    PolicyMatchStatus,
    ReadinessReport,
    ReportSummary,
    ToolSurfaceSummary,
)


@dataclass(frozen=True)
class ClaimCanary:
    name: str
    basis: EvidenceBasis
    confidence: Confidence
    expected_provenance: ProvenanceKind
    policy_eligible: bool


CLAIM_CANARIES = [
    ClaimCanary("reviewed_high", "reviewed_declaration", "high", "static_declaration", True),
    ClaimCanary("protocol_high", "protocol_structure", "high", "static_declaration", True),
    ClaimCanary("provider_high", "typed_provider_fact", "high", "ast_extraction", True),
    ClaimCanary("scope_high", "structural_scope", "high", "static_declaration", True),
    ClaimCanary("keyword_high", "inferred_keyword", "high", "keyword_heuristic", False),
    ClaimCanary("regex_high", "inferred_regex", "high", "regex_heuristic", False),
    ClaimCanary("default_high", "protocol_default", "high", "static_declaration", False),
    ClaimCanary("unknown_high", "unknown", "high", "policy_pack", False),
    ClaimCanary("reviewed_medium", "reviewed_declaration", "medium", "static_declaration", False),
    ClaimCanary("protocol_medium", "protocol_structure", "medium", "static_declaration", False),
    ClaimCanary("provider_medium", "typed_provider_fact", "medium", "ast_extraction", False),
    ClaimCanary("scope_medium", "structural_scope", "medium", "static_declaration", False),
    ClaimCanary("keyword_medium", "inferred_keyword", "medium", "keyword_heuristic", False),
    ClaimCanary("regex_medium", "inferred_regex", "medium", "regex_heuristic", False),
    ClaimCanary("default_medium", "protocol_default", "medium", "static_declaration", False),
    ClaimCanary("unknown_medium", "unknown", "medium", "policy_pack", False),
]


@pytest.mark.parametrize("case", CLAIM_CANARIES, ids=lambda case: case.name)
def test_claim_basis_canary(case: ClaimCanary) -> None:
    claim = SemanticClaim(
        dimension="effect",
        value="write",
        confidence=case.confidence,
        provenance_kind="policy_pack",
        basis=case.basis,
        source="p0-canary",
    )

    assert claim.provenance_kind == case.expected_provenance
    assert claim.policy_eligible is case.policy_eligible
    assert claim.claim_id is not None and claim.claim_id.startswith("clm_")


@dataclass(frozen=True)
class SupportCanary:
    name: str
    reducer: Literal["conjunction", "disjunction", "negated_disjunction"]
    statuses: tuple[PolicyMatchStatus, ...]
    confidences: tuple[Confidence, ...]
    eligibility: tuple[bool, ...]
    requested_confidence: Confidence
    expected_status: PolicyMatchStatus
    expected_confidence: Confidence
    expected_eligible: bool


SUPPORT_CANARIES = [
    SupportCanary("all_authoritative", "conjunction", ("matched",), ("high",), (True,), "high", "matched", "high", True),
    SupportCanary("medium_cannot_authorize", "conjunction", ("matched",), ("medium",), (False,), "high", "matched", "medium", False),
    SupportCanary("rule_low_caps_evidence", "conjunction", ("matched",), ("high",), (True,), "low", "matched", "low", True),
    SupportCanary("heuristic_cannot_authorize", "conjunction", ("matched",), ("high",), (False,), "high", "matched", "high", False),
    SupportCanary("indeterminate_conjunction", "conjunction", ("matched", "indeterminate"), ("high", "low"), (True, False), "high", "indeterminate", "low", False),
    SupportCanary("negative_conjunction", "conjunction", ("matched", "not_matched"), ("high", "high"), (True, True), "high", "not_matched", "high", False),
    SupportCanary("conflicting_conjunction", "conjunction", ("matched", "conflicting"), ("high", "high"), (True, False), "high", "conflicting", "high", False),
    SupportCanary("negative_beats_unknown", "conjunction", ("not_matched", "indeterminate"), ("high", "low"), (True, False), "high", "not_matched", "low", False),
    SupportCanary("positive_disjunction", "disjunction", ("not_matched", "matched"), ("high", "high"), (True, True), "high", "matched", "high", False),
    SupportCanary("positive_beats_conflict", "disjunction", ("conflicting", "matched"), ("low", "high"), (False, True), "high", "matched", "low", False),
    SupportCanary("conflict_beats_unknown", "disjunction", ("indeterminate", "conflicting"), ("low", "high"), (False, False), "high", "conflicting", "low", False),
    SupportCanary("unknown_disjunction", "disjunction", ("not_matched", "indeterminate"), ("high", "low"), (True, False), "high", "indeterminate", "low", False),
    SupportCanary("negative_disjunction", "disjunction", ("not_matched", "not_matched"), ("high", "high"), (True, True), "high", "not_matched", "high", False),
    SupportCanary("none_of_positive", "negated_disjunction", ("matched",), ("high",), (True,), "high", "not_matched", "high", False),
    SupportCanary("none_of_negative", "negated_disjunction", ("not_matched",), ("high",), (True,), "high", "matched", "high", False),
    SupportCanary("none_of_unknown", "negated_disjunction", ("indeterminate",), ("low",), (False,), "high", "indeterminate", "low", False),
]


@pytest.mark.parametrize("case", SUPPORT_CANARIES, ids=lambda case: case.name)
def test_policy_support_canary(case: SupportCanary) -> None:
    reducers = {
        "conjunction": conjunction_status,
        "disjunction": disjunction_status,
        "negated_disjunction": negated_disjunction_status,
    }
    predicates = [
        predicate_evidence(
            f"predicate_{index}",
            status,
            confidence=case.confidences[index],
            evidence_bases=[
                "protocol_structure" if case.eligibility[index] else "inferred_keyword"
            ],
            policy_eligible=case.eligibility[index],
        )
        for index, status in enumerate(case.statuses)
    ]
    support = finding_support(
        predicates,
        requested_confidence=case.requested_confidence,
        status=reducers[case.reducer](case.statuses),
    )

    assert support.status == case.expected_status
    assert support.confidence == case.expected_confidence
    assert support.policy_eligible is case.expected_eligible
    assert support.blocking_eligible is case.expected_eligible


@dataclass(frozen=True)
class ReleaseCanary:
    name: str
    support_status: PolicyMatchStatus
    support_eligible: bool
    severity: Literal["critical", "high", "medium", "low"]
    gap_status: PolicyMatchStatus | None
    ci_mode: Literal["advisory", "strict"]
    fail_on: tuple[Literal["critical", "high", "medium", "low"], ...]
    expected_decision: Literal[
        "blocked", "review_required", "insufficient_evidence", "passed"
    ]
    expected_exit: int
    expected_contribution: str


RELEASE_CANARIES = [
    ReleaseCanary("eligible_critical_advisory", "matched", True, "critical", None, "advisory", (), "blocked", 0, "severity_block_new"),
    ReleaseCanary("eligible_critical_strict", "matched", True, "critical", None, "strict", (), "blocked", 20, "severity_block_new"),
    ReleaseCanary("eligible_high_review", "matched", True, "high", None, "advisory", (), "review_required", 0, "review_required"),
    ReleaseCanary("eligible_high_fail_on", "matched", True, "high", None, "strict", ("high",), "blocked", 20, "severity_block_new"),
    ReleaseCanary("eligible_medium_review", "matched", True, "medium", None, "advisory", (), "review_required", 0, "review_required"),
    ReleaseCanary("eligible_low_pass", "matched", True, "low", None, "advisory", (), "passed", 0, "sub_threshold"),
    ReleaseCanary("heuristic_critical_ie", "indeterminate", False, "critical", "indeterminate", "advisory", (), "insufficient_evidence", 0, "unsupported_evidence"),
    ReleaseCanary("heuristic_critical_strict_ie", "indeterminate", False, "critical", "indeterminate", "strict", (), "insufficient_evidence", 20, "unsupported_evidence"),
    ReleaseCanary("heuristic_high_ie", "indeterminate", False, "high", "indeterminate", "advisory", (), "insufficient_evidence", 0, "unsupported_evidence"),
    ReleaseCanary("heuristic_medium_ie", "indeterminate", False, "medium", "indeterminate", "advisory", (), "insufficient_evidence", 0, "unsupported_evidence"),
    ReleaseCanary("heuristic_low_ie", "indeterminate", False, "low", "indeterminate", "advisory", (), "insufficient_evidence", 0, "unsupported_evidence"),
    ReleaseCanary("conflicting_critical_ie", "conflicting", False, "critical", "conflicting", "strict", (), "insufficient_evidence", 20, "unsupported_evidence"),
    ReleaseCanary("unknown_high_ie", "indeterminate", False, "high", "indeterminate", "strict", ("high",), "insufficient_evidence", 20, "unsupported_evidence"),
    ReleaseCanary("unsupported_without_gap_cannot_block", "matched", False, "critical", None, "strict", (), "passed", 0, "unsupported_evidence"),
    ReleaseCanary("unsupported_rule_block_cannot_block", "matched", False, "high", None, "strict", ("high",), "passed", 0, "unsupported_evidence"),
    ReleaseCanary("eligible_low_strict_threshold", "matched", True, "low", None, "strict", ("low",), "blocked", 20, "severity_block_new"),
]


def _support(case: ReleaseCanary) -> FindingSupport:
    return finding_support(
        [
            predicate_evidence(
                "capability.effect",
                case.support_status,
                observed="financial_write",
                confidence="high",
                evidence_bases=[
                    "protocol_structure" if case.support_eligible else "inferred_keyword"
                ],
                policy_eligible=case.support_eligible,
            )
        ],
        status=case.support_status,
    )


@pytest.mark.parametrize("case", RELEASE_CANARIES, ids=lambda case: case.name)
def test_release_contribution_canary(case: ReleaseCanary) -> None:
    support = _support(case)
    gap = (
        policy_evidence_gap(
            status=case.gap_status,
            subject="wire_funds",
            policy_id="p0-canary",
            source_ref="tools.json#/wire_funds",
            support=support,
            manifest_path="policy_packs[0].rules[0].match",
        )
        if case.gap_status is not None
        else None
    )
    finding = Finding(
        id=f"finding-{case.name}",
        fingerprint=f"fp-{case.name}",
        check_id="SHIP-POLICY-PACK-VIOLATION",
        title="P0 policy evidence canary",
        severity=case.severity,
        category="policy_pack",
        recommendation="Provide authoritative evidence.",
        provenance_kind="policy_pack",
        # Most cases exercise severity routing. One adversarial case sets the
        # rule's block bit on unsupported evidence and proves it is ignored.
        blocks_release=case.name == "unsupported_rule_block_cannot_block",
        support=support,
    )
    report = ReadinessReport(
        run_id="p0-policy-canary",
        project={"name": "p0-policy-canary"},
        agent={"name": "p0-policy-canary"},
        environment={"target": "test"},
        summary=ReportSummary(
            status="warnings_detected",
            critical_count=int(case.severity == "critical"),
            high_count=int(case.severity == "high"),
            medium_count=int(case.severity == "medium"),
            human_review_recommended=False,
            evidence_coverage="static",
        ),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        binding_surface_facts=AgentBindingGraphAssessment(
            root_agent_id="p0-policy-canary",
            status="declared",
            pass_eligible=True,
        ),
        findings=[finding],
        policy_evidence_gaps=[] if gap is None else [gap],
    )

    decision = build_release_decision(
        report=report,
        tools=[],
        ci_mode=case.ci_mode,
        fail_on=list(case.fail_on) or None,
        new_findings_only=False,
    )

    assert decision.decision == case.expected_decision
    assert decision.fail_policy.exit_code == case.expected_exit
    assert decision.contribution_rules[0].rule == case.expected_contribution


def test_policy_evidence_canary_catalog_has_exact_planned_shape() -> None:
    assert len(CLAIM_CANARIES) == 16
    assert len(SUPPORT_CANARIES) == 16
    assert len(RELEASE_CANARIES) == 16
    names = [
        *(case.name for case in CLAIM_CANARIES),
        *(case.name for case in SUPPORT_CANARIES),
        *(case.name for case in RELEASE_CANARIES),
    ]
    assert len(names) == 48
    assert len(names) == len(set(names))


def test_weaker_write_declaration_with_inferred_financial_effect_is_ie(
    tmp_path: Path,
) -> None:
    """P0: a discarded heuristic escalation must never become a clean pass."""

    (tmp_path / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "create_refund",
                        "description": "Create a refund for a customer payment",
                        "auth": {
                            "type": "oauth2",
                            "mode": "scoped",
                            "scopes": ["refunds:write"],
                        },
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "amount": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 10000,
                                }
                            },
                            "required": ["amount"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        """
version: "0.1"
project: {name: inferred-effect-escalation}
agent:
  name: refund-agent
  declared_purpose: [process reviewed refunds]
environment: {target: local}
permissions:
  scopes: [refunds:write]
tool_sources:
  - id: refunds
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: create_refund, source_id: refunds}]
      handoffs: []
      reason: reviewed test binding
action_surface:
  actions:
    - tool: create_refund
      source_id: refunds
      effect: write
      scopes: [refunds:write]
      authority:
        mode: scoped
        auth_type: oauth2
""",
        encoding="utf-8",
    )

    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="strict",
        packet_enabled=False,
    )

    assert exit_code == 20
    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    assert report.findings == []
    action = report.action_surface_facts.actions[0]
    assert action.semantic_assessment is not None
    # #409: the declaration is `write` and a heuristic reads `financial_write`
    # off the name, so the discarded escalation is now on the record instead of
    # being carried only by the policy-evidence gap below. The scope claim
    # `refunds:write` corroborates the declared value and the row says so, but
    # corroboration is not an exemption — this resolver already refuses to pass
    # on that scope alone.
    assert action.semantic_assessment.pass_eligible is False
    assert "declaration_below_inferred_evidence" in {
        issue.kind for issue in action.semantic_assessment.effect.issues
    }
    gaps = [gap for gap in report.policy_evidence_gaps if gap.kind == "mixed_policy_evidence"]
    assert len(gaps) == 1
    assert gaps[0].kind == "mixed_policy_evidence"
    assert "builtin-effect-control-applicability" not in gaps[0].why
    assert "a reviewed declaration" in gaps[0].why
    assert "keyword inference" in gaps[0].why
    assert "Confirm the reviewed declaration" in gaps[0].why
    assert internal_vocabulary(gaps[0].why) == ()
    assert internal_vocabulary(evidence_gap_headline(gaps[0])) == ()
    assert gaps[0].next_action.kind == "review_policy_evidence"


def test_empty_finding_support_is_fail_closed() -> None:
    support = finding_support([])

    assert support.status == "indeterminate"
    assert support.policy_eligible is False
    assert support.blocking_eligible is False
    assert support.predicates[0].evidence_bases == ["unknown"]
