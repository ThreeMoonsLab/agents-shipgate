"""Pin the v0.22 verifier report blocks (shape + invariants).

v0.22 ships five additive top-level blocks as deterministic projections
with stable empty/default shapes when no evidence is available:

- ``capability_change`` (CapabilityChangeBlock)
- ``protected_surface_changes`` (list[ProtectedSurfaceChange])
- ``effective_policy`` (EffectivePolicy)
- ``human_ack`` (HumanAck)
- ``verifier_summary`` (VerifierSummary)

This file pins:
- the closed enum surfaces of the new models,
- the deterministic default values the builders emit,
- byte-stable serialization (sorted lists),
- the §8 invariant ``verifier_summary.verdict == release_decision.decision``
  (Principle 2 — the verifier summary cannot derive an independent verdict),
- validation of the emitted payload against the committed v0.22 schema.

Schema-level "every emitted report carries the block" coverage lives in
``tests/test_reports.py``; this file pins the *semantics* of the blocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import jsonschema

from agents_shipgate.core.findings import (
    build_capability_change,
    build_human_ack,
    build_protected_surface_changes,
    build_verifier_summary,
)
from agents_shipgate.core.lenses.effective_policy import (
    build_effective_policy_snapshot,
)
from agents_shipgate.schemas.capability_change import (
    CapabilityChangeBlock,
    CapabilityChangeMember,
    CapabilityReleaseImpact,
    EffectivePolicy,
    HumanAck,
    HumanAckEntry,
    ProtectedSurfaceChange,
    VerifierVerdict,
)
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    ReadinessReport,
    ReleaseDecision,
    ReportSummary,
    ToolSurfaceSummary,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CURRENT_SCHEMA_VERSION = ReadinessReport.model_fields[
    "report_schema_version"
].default


# --- Helpers ----------------------------------------------------------------


def _release_decision(decision: str) -> ReleaseDecision:
    return ReleaseDecision(
        decision=decision,
        reason="test",
        blockers=[],
        review_items=[],
        evidence_coverage=EvidenceCoverageDecision(
            level="static",
            human_review_recommended=False,
            source_warning_count=0,
            low_confidence_tool_count=0,
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(
            ci_mode="advisory",
            fail_on=[],
            would_fail_ci=False,
            exit_code=0,
        ),
    )


def _report(decision: str = "passed") -> ReadinessReport:
    return ReadinessReport(
        run_id="run",
        project={},
        agent={},
        environment={},
        summary=ReportSummary(status="ok"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        release_decision=_release_decision(decision),
    )


# --- Enum surface contracts -------------------------------------------------


def test_capability_change_direction_enum_uses_broadened_narrowed():
    """The diff-derived delta groups members by added/removed/broadened/narrowed."""
    member = CapabilityChangeMember(id="i", direction="broadened", tool="t")
    assert member.direction == "broadened"
    block = CapabilityChangeBlock()
    assert set(CapabilityChangeBlock.model_fields) >= {
        "enabled",
        "added",
        "removed",
        "broadened",
        "narrowed",
    }
    assert block.added == [] and block.removed == []
    assert block.broadened == [] and block.narrowed == []


def test_verifier_verdict_enum_matches_release_decision():
    """VerifierSummary.verdict mirrors release_decision.decision's enum."""
    assert set(get_args(VerifierVerdict)) == {
        "blocked",
        "review_required",
        "insufficient_evidence",
        "passed",
    }


def test_capability_release_impact_enum_is_closed():
    assert set(get_args(CapabilityReleaseImpact)) == {
        "none",
        "informational",
        "review_required",
        "blocks_release",
        "insufficient_evidence",
    }


# --- Default builders --------------------------------------------------------


def test_phase_a_blocks_are_empty_defaults():
    report = _report("passed")
    cap = build_capability_change(report)
    assert cap.added == [] and cap.removed == []
    assert cap.broadened == [] and cap.narrowed == []
    # enabled mirrors the (disabled) action-surface diff on a base-less report.
    assert cap.enabled is False

    assert build_protected_surface_changes(report) == []

    # The empty/default snapshot shape (the lens builder fills it from a
    # real manifest; tested separately below).
    pol = EffectivePolicy()
    assert pol.fail_on == [] and pol.waiver_scopes == []
    assert pol.suppressed_check_ids == [] and pol.severity_overrides == {}

    ack = build_human_ack(report)
    # Default: not required, therefore satisfied, with empty lists.
    assert ack.required is False
    assert ack.satisfied is True
    assert ack.acks == [] and ack.outstanding == []


# --- P4: human-acknowledgement mechanism (§5.4 / §7.2) ----------------------


def _ack_finding(check_id, evidence=None, *, fid="F", severity="high"):
    from agents_shipgate.schemas.report import Finding

    return Finding(
        id=fid,
        check_id=check_id,
        title="t",
        severity=severity,
        category="verify",
        recommendation="r",
        evidence=evidence or {},
    )


class _FakeManifest:
    """Minimal manifest stand-in exposing the declared ``human_ack`` list."""

    def __init__(self, declarations=None):
        self.human_ack = declarations or []


def _decl(surface, owner="alice", reason="approved", expires=None):
    from agents_shipgate.schemas.manifest import HumanAckDeclaration

    return HumanAckDeclaration(
        affected_surface=surface, owner=owner, reason=reason, expires=expires
    )


def test_human_ack_declaration_requires_nonempty_fields():
    import pytest
    from pydantic import ValidationError

    from agents_shipgate.schemas.manifest import HumanAckDeclaration

    with pytest.raises(ValidationError):
        HumanAckDeclaration(affected_surface="policy", owner="", reason="x")


def test_manifest_parses_human_ack_section():
    # Load a real sample manifest and inject a human_ack section via the
    # same validation path, so the test tracks the real manifest shape.
    import pytest
    from pydantic import ValidationError

    from agents_shipgate.config.loader import load_manifest
    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    base = load_manifest(
        REPO_ROOT / "samples" / "simple_openai_api_agent" / "shipgate.yaml"
    )
    data = base.model_dump(exclude_none=True)
    data["human_ack"] = [
        {
            "affected_surface": "policy",
            "owner": "alice",
            "reason": "approved CI downgrade",
            "expires": "2026-12-31",
        }
    ]
    manifest = AgentsShipgateManifest.model_validate(data)
    assert len(manifest.human_ack) == 1
    decl = manifest.human_ack_declarations()[0]
    assert decl.affected_surface == "policy"
    assert decl.expires == "2026-12-31"

    # Unknown field is rejected (STRICT_MODEL_CONFIG).
    bad = dict(data)
    bad["human_ack"] = [
        {"affected_surface": "policy", "owner": "a", "reason": "r", "bogus": 1}
    ]
    with pytest.raises(ValidationError):
        AgentsShipgateManifest.model_validate(bad)


def test_human_ack_required_when_weakening_fires_without_ack():
    report = _report("review_required")
    report.findings = [_ack_finding("SHIP-VERIFY-POLICY-WEAKENED")]
    ack = build_human_ack(report, _FakeManifest())
    assert ack.required is True
    assert ack.satisfied is False
    assert ack.outstanding == ["policy"]


def test_human_ack_satisfied_by_matching_declaration():
    report = _report("review_required")
    report.findings = [_ack_finding("SHIP-VERIFY-CI-GATE-REMOVED", severity="critical")]
    ack = build_human_ack(report, _FakeManifest([_decl("ci_gate")]))
    assert ack.required is True
    assert ack.satisfied is True
    assert ack.outstanding == []
    assert [a.affected_surface for a in ack.acks] == ["ci_gate"]
    assert ack.acks[0].source == "shipgate.yaml#/human_ack"


def test_human_ack_partition_invariant_partial():
    # Two ack-requiring weakenings, only one acknowledged.
    report = _report("review_required")
    report.findings = [
        _ack_finding("SHIP-VERIFY-POLICY-WEAKENED", fid="F1"),
        _ack_finding("SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED", fid="F2"),
    ]
    ack = build_human_ack(report, _FakeManifest([_decl("policy")]))
    assert ack.required is True
    assert ack.satisfied is False
    assert ack.outstanding == ["baseline_or_waiver"]


def test_human_ack_path_level_declaration_satisfies_surface():
    # An ack naming a concrete changed file (not the surface key) still
    # satisfies the surface.
    report = _report("review_required")
    report.findings = [
        _ack_finding(
            "SHIP-VERIFY-CI-GATE-REMOVED",
            {"removed_workflow_files": [".github/workflows/agents-shipgate.yml"]},
            severity="critical",
        )
    ]
    ack = build_human_ack(
        report,
        _FakeManifest([_decl(".github/workflows/agents-shipgate.yml")]),
    )
    assert ack.satisfied is True


def test_human_ack_medium_reviews_do_not_require_ack():
    # Agent-instructions / trigger-drift are review-only; they do not
    # require a formal acknowledgement.
    report = _report("review_required")
    report.findings = [
        _ack_finding("SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED", severity="medium"),
        _ack_finding("SHIP-VERIFY-TRIGGER-CATALOG-DRIFT", severity="medium"),
    ]
    ack = build_human_ack(report, _FakeManifest())
    assert ack.required is False
    assert ack.satisfied is True


def test_human_ack_surface_is_itself_a_trust_root():
    # Editing the human_ack section lives in shipgate.yaml, which the Tier A
    # classifier already treats as a trust root — so a coding agent cannot
    # add its own acknowledgement without tripping TRUST-ROOT-TOUCHED.
    from agents_shipgate.checks.verify import run as verify_run

    ctx = _trust_root_context(["shipgate.yaml"])
    findings = verify_run(ctx)
    assert any(f.check_id == "SHIP-VERIFY-TRUST-ROOT-TOUCHED" for f in findings)


def _trust_root_context(changed_files):
    from pathlib import Path

    from agents_shipgate.config.loader import load_manifest
    from agents_shipgate.core.context import ScanContext
    from agents_shipgate.core.domain import Agent
    from agents_shipgate.schemas.verification import VerificationContext

    manifest = load_manifest(
        REPO_ROOT / "samples" / "support_refund_agent" / "shipgate.yaml"
    )
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        verification=VerificationContext(changed_files=changed_files),
    )


# --- P1: real diff-derived capability classification ------------------------


def _diffed_report(*, action_surface_diff=None, tool_surface_diff=None, findings=None):
    report = _report("review_required")
    if action_surface_diff is not None:
        report.action_surface_diff = action_surface_diff
    if tool_surface_diff is not None:
        report.tool_surface_diff = tool_surface_diff
    if findings is not None:
        report.findings = findings
    return report


def test_capability_change_classifies_surface_diffs():
    from agents_shipgate.schemas.surfaces import (
        ActionSurfaceChange,
        ActionSurfaceDiff,
        ToolSurfaceControlChange,
        ToolSurfaceDiff,
        ToolSurfaceScopeChange,
        ToolSurfaceToolChange,
    )

    report = _diffed_report(
        action_surface_diff=ActionSurfaceDiff(
            enabled=True,
            added=[
                ActionSurfaceChange(
                    type="ACTION_ADDED",
                    action_id="stripe.create_refund",
                    tool_name="stripe",
                    reason="action added",
                )
            ],
            modified=[
                ActionSurfaceChange(
                    type="APPROVAL_REMOVED",
                    action_id="stripe.charge",
                    tool_name="stripe",
                    reason="approval removed",
                )
            ],
        ),
        tool_surface_diff=ToolSurfaceDiff(
            enabled=True,
            tools=[ToolSurfaceToolChange(kind="removed", name="email")],
            scopes=[
                ToolSurfaceScopeChange(
                    kind="added",
                    scope="refunds:write",
                    scope_kind="tool_required",
                    tool_names=["stripe"],
                )
            ],
            controls=[
                ToolSurfaceControlChange(
                    kind="removed", control="approval_policy", tool="stripe"
                )
            ],
        ),
    )
    block = build_capability_change(report)
    assert block.enabled is True
    assert [m.action for m in block.added] == ["stripe.create_refund"]
    assert [m.tool for m in block.removed] == ["email"]
    # APPROVAL_REMOVED action mod + scope added + control removed all widen.
    broadened = {(m.subject_kind, m.tool, m.action, m.scope) for m in block.broadened}
    assert ("action", "stripe", "stripe.charge", None) in broadened
    assert ("scope", "stripe", None, "refunds:write") in broadened
    assert ("policy", "stripe", "approval_policy", None) in broadened
    assert block.narrowed == []


def test_capability_change_control_added_narrows():
    from agents_shipgate.schemas.surfaces import (
        ToolSurfaceControlChange,
        ToolSurfaceDiff,
        ToolSurfaceScopeChange,
    )

    report = _diffed_report(
        tool_surface_diff=ToolSurfaceDiff(
            enabled=True,
            controls=[
                ToolSurfaceControlChange(
                    kind="added", control="approval_policy", tool="stripe"
                )
            ],
            scopes=[
                ToolSurfaceScopeChange(
                    kind="removed",
                    scope="refunds:write",
                    scope_kind="tool_required",
                    tool_names=["stripe"],
                )
            ],
        )
    )
    block = build_capability_change(report)
    narrowed = {(m.subject_kind, m.tool, m.action, m.scope) for m in block.narrowed}
    assert ("policy", "stripe", "approval_policy", None) in narrowed
    assert ("scope", "stripe", None, "refunds:write") in narrowed
    assert block.broadened == []


def test_capability_change_release_impact_reflects_blocking_finding():
    from agents_shipgate.schemas.report import Finding
    from agents_shipgate.schemas.surfaces import (
        ActionSurfaceChange,
        ActionSurfaceDiff,
    )

    report = _diffed_report(
        action_surface_diff=ActionSurfaceDiff(
            enabled=True,
            added=[
                ActionSurfaceChange(
                    type="ACTION_ADDED",
                    action_id="stripe.create_refund",
                    tool_name="stripe",
                    reason="action added",
                )
            ],
        ),
        findings=[
            Finding(
                id="F1",
                check_id="SHIP-ACTION-APPROVAL-REMOVED",
                title="t",
                severity="critical",
                category="action_surface",
                tool_name="stripe",
                recommendation="r",
                blocks_release=True,
            )
        ],
    )
    block = build_capability_change(report)
    assert len(block.added) == 1
    member = block.added[0]
    assert member.release_impact == "blocks_release"
    assert member.related_finding_ids == ["F1"]


def test_capability_change_deterministic_sorted_and_stable():
    from agents_shipgate.schemas.surfaces import (
        ActionSurfaceChange,
        ActionSurfaceDiff,
    )

    def _mk(order):
        return _diffed_report(
            action_surface_diff=ActionSurfaceDiff(
                enabled=True,
                added=[
                    ActionSurfaceChange(
                        type="ACTION_ADDED",
                        action_id=aid,
                        tool_name=aid.split(".")[0],
                        reason="added",
                    )
                    for aid in order
                ],
            )
        )

    block = build_capability_change(_mk(["z.act", "a.act"]))
    block_rev = build_capability_change(_mk(["a.act", "z.act"]))
    # Sorted by identity tuple -> a.act before z.act, regardless of input order.
    assert [m.action for m in block.added] == ["a.act", "z.act"]
    assert block.model_dump() == block_rev.model_dump()


def test_human_ack_default_is_not_required_and_satisfied():
    ack = HumanAck()
    assert ack.required is False
    assert ack.satisfied is True


# --- §8 invariant: verifier_summary.verdict == release_decision.decision ----


def test_verifier_summary_verdict_mirrors_release_decision():
    for decision in (
        "blocked",
        "review_required",
        "insufficient_evidence",
        "passed",
    ):
        report = _report(decision)
        report.protected_surface_changes = build_protected_surface_changes(report)
        report.human_ack = build_human_ack(report)
        vs = build_verifier_summary(report)
        assert vs.verdict == decision, (
            "verifier_summary.verdict must mirror release_decision.decision "
            "(Principle 2 — one decision engine)"
        )


def test_verifier_summary_falls_back_to_passed_without_release_decision():
    report = _report("passed")
    report.release_decision = None
    report.human_ack = build_human_ack(report)
    vs = build_verifier_summary(report)
    assert vs.verdict == "passed"


def test_verifier_summary_default_counts_are_zero():
    report = _report("passed")
    report.human_ack = build_human_ack(report)
    vs = build_verifier_summary(report)
    assert vs.capability_delta_summary.added == 0
    assert vs.capability_delta_summary.removed == 0
    assert vs.capability_delta_summary.broadened == 0
    assert vs.capability_delta_summary.narrowed == 0
    assert vs.by_severity == {} and vs.by_reason_code == {}
    assert vs.top_reason_codes == []
    assert vs.protected_surface_touched is False
    assert vs.policy_weakened is False
    assert vs.human_ack_required is False
    assert vs.human_ack_satisfied is True


# --- P5: verifier_summary composition ---------------------------------------


def _summary_report(findings):
    r = _report("blocked")
    r.findings = findings
    return r


def test_verifier_summary_counts_active_findings_by_severity_and_code():
    from agents_shipgate.schemas.report import Finding

    report = _summary_report(
        [
            Finding(id="F1", check_id="SHIP-A", title="t", severity="high",
                    category="x", recommendation="r"),
            Finding(id="F2", check_id="SHIP-A", title="t", severity="high",
                    category="x", recommendation="r"),
            Finding(id="F3", check_id="SHIP-B", title="t", severity="medium",
                    category="x", recommendation="r"),
            Finding(id="F4", check_id="SHIP-C", title="t", severity="low",
                    category="x", recommendation="r", suppressed=True),
        ]
    )
    vs = build_verifier_summary(report)
    # Suppressed F4 excluded from both maps.
    assert vs.by_severity == {"high": 2, "medium": 1}
    assert vs.by_reason_code == {"SHIP-A": 2, "SHIP-B": 1}


def test_verifier_summary_top_reason_codes_ranked_severity_then_count():
    from agents_shipgate.schemas.report import Finding

    report = _summary_report(
        [
            # high, count 3
            *[
                Finding(id=f"H{i}", check_id="SHIP-HIGH", title="t",
                        severity="high", category="x", recommendation="r")
                for i in range(3)
            ],
            # critical, count 1 -> outranks high despite lower count
            Finding(id="C1", check_id="SHIP-CRIT", title="t", severity="critical",
                    category="x", recommendation="r"),
            # medium, count 2
            Finding(id="M1", check_id="SHIP-MED", title="t", severity="medium",
                    category="x", recommendation="r"),
            Finding(id="M2", check_id="SHIP-MED", title="t", severity="medium",
                    category="x", recommendation="r"),
        ]
    )
    vs = build_verifier_summary(report)
    assert [(t.reason_code, t.count) for t in vs.top_reason_codes] == [
        ("SHIP-CRIT", 1),  # critical first
        ("SHIP-HIGH", 3),  # then high
        ("SHIP-MED", 2),   # then medium
    ]


def test_verifier_summary_top_reason_codes_capped_at_five():
    from agents_shipgate.schemas.report import Finding

    # Eight distinct high-severity codes; only the top 5 surface (ties
    # broken by code asc). by_reason_code still carries all eight.
    findings = [
        Finding(id=f"F{i}", check_id=f"SHIP-{chr(65 + i)}", title="t",
                severity="high", category="x", recommendation="r")
        for i in range(8)
    ]
    vs = build_verifier_summary(_summary_report(findings))
    assert len(vs.top_reason_codes) == 5
    assert [t.reason_code for t in vs.top_reason_codes] == [
        "SHIP-A", "SHIP-B", "SHIP-C", "SHIP-D", "SHIP-E",
    ]
    assert len(vs.by_reason_code) == 8


def test_verifier_summary_delta_counts_match_capability_block():
    report = _report("review_required")
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[CapabilityChangeMember(id="a", direction="added", tool="t1")],
        broadened=[
            CapabilityChangeMember(id="b", direction="broadened", tool="t2"),
            CapabilityChangeMember(id="c", direction="broadened", tool="t3"),
        ],
    )
    vs = build_verifier_summary(report)
    assert vs.capability_delta_summary.added == 1
    assert vs.capability_delta_summary.broadened == 2
    assert vs.capability_delta_summary.removed == 0
    assert vs.capability_delta_summary.narrowed == 0


def test_verifier_summary_policy_weakened_flag_tracks_finding():
    from agents_shipgate.schemas.report import Finding

    report = _summary_report(
        [
            Finding(id="F1", check_id="SHIP-VERIFY-POLICY-WEAKENED", title="t",
                    severity="high", category="verify", recommendation="r"),
        ]
    )
    assert build_verifier_summary(report).policy_weakened is True

    report2 = _summary_report([])
    assert build_verifier_summary(report2).policy_weakened is False


def test_verifier_summary_is_byte_stable():
    from agents_shipgate.schemas.report import Finding

    report = _summary_report(
        [
            Finding(id="F1", check_id="SHIP-B", title="t", severity="medium",
                    category="x", recommendation="r"),
            Finding(id="F2", check_id="SHIP-A", title="t", severity="high",
                    category="x", recommendation="r"),
        ]
    )
    assert (
        build_verifier_summary(report).model_dump()
        == build_verifier_summary(report).model_dump()
    )


# --- Determinism ------------------------------------------------------------


def test_capability_change_members_sort_deterministically():
    """Members serialize in a stable identity order regardless of input order."""
    members = [
        CapabilityChangeMember(
            id="z", direction="added", tool="b", subject_kind="tool"
        ),
        CapabilityChangeMember(
            id="a", direction="added", tool="a", subject_kind="tool"
        ),
        CapabilityChangeMember(
            id="m", direction="added", tool="a", subject_kind="action"
        ),
    ]
    block = CapabilityChangeBlock(enabled=True, added=list(members))
    block_rev = CapabilityChangeBlock(enabled=True, added=list(reversed(members)))
    assert block.model_dump() == block_rev.model_dump()


def test_member_risk_tags_and_finding_ids_are_sorted():
    member = CapabilityChangeMember(
        id="i",
        direction="added",
        tool="t",
        risk_tags=["z", "a", "m"],
        related_finding_ids=["f2", "f1"],
    )
    assert member.risk_tags == ["a", "m", "z"]
    assert member.related_finding_ids == ["f1", "f2"]


def test_human_ack_entries_sorted_by_surface():
    ack = HumanAck(
        required=True,
        satisfied=False,
        acks=[
            HumanAckEntry(owner="bob", reason="r", affected_surface="s2"),
            HumanAckEntry(owner="amy", reason="r", affected_surface="s1"),
        ],
        outstanding=["z", "a"],
    )
    assert [a.affected_surface for a in ack.acks] == ["s1", "s2"]
    assert ack.outstanding == ["a", "z"]


def test_protected_surface_change_finding_ids_sorted():
    psc = ProtectedSurfaceChange(
        path="shipgate.yaml",
        kind="manifest",
        related_finding_ids=["b", "a"],
    )
    assert psc.related_finding_ids == ["a", "b"]


# --- P3: protected_surface_changes rollup -----------------------------------


def _verify_finding(check_id, evidence, *, fid, severity="medium", suppressed=False):
    from agents_shipgate.schemas.report import Finding

    return Finding(
        id=fid,
        check_id=check_id,
        title="t",
        severity=severity,
        category="verify",
        recommendation="r",
        evidence=evidence,
        suppressed=suppressed,
    )


def _report_with_findings(findings):
    r = _report("review_required")
    r.findings = findings
    return r


def test_protected_surface_rollup_from_trust_root_touched():
    report = _report_with_findings(
        [
            _verify_finding(
                "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
                {
                    "changed_file": "shipgate.yaml",
                    "trust_root_class": "manifest",
                    "matched_glob": "**/shipgate.yaml",
                },
                fid="F1",
            )
        ]
    )
    rows = build_protected_surface_changes(report)
    assert len(rows) == 1
    assert rows[0].kind == "manifest"
    assert rows[0].path == "shipgate.yaml"
    assert rows[0].glob == "**/shipgate.yaml"
    assert rows[0].related_finding_ids == ["F1"]


def test_protected_surface_rollup_dedups_and_merges_finding_ids():
    # Same file flagged by Tier A touched AND a Tier B weakening -> one row,
    # both finding ids merged.
    report = _report_with_findings(
        [
            _verify_finding(
                "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
                {
                    "changed_file": ".github/workflows/agents-shipgate.yml",
                    "trust_root_class": "ci_gate",
                    "matched_glob": "**/.github/workflows/agents-shipgate.yml",
                },
                fid="F1",
            ),
            _verify_finding(
                "SHIP-VERIFY-CI-GATE-REMOVED",
                {
                    "kind": "ci_gate_removed",
                    "removed_workflow_files": [
                        ".github/workflows/agents-shipgate.yml"
                    ],
                },
                fid="F2",
                severity="critical",
            ),
        ]
    )
    rows = build_protected_surface_changes(report)
    assert len(rows) == 1
    assert rows[0].kind == "ci_gate"
    assert rows[0].related_finding_ids == ["F1", "F2"]


def test_protected_surface_rollup_skips_semantic_only_weakening():
    # A POLICY-WEAKENED finding with no changed-file evidence (e.g. a CI
    # mode downgrade) has no specific path -> no protected-surface row.
    report = _report_with_findings(
        [
            _verify_finding(
                "SHIP-VERIFY-POLICY-WEAKENED",
                {
                    "kind": "ci_mode_weakened",
                    "base_ci_mode": "strict",
                    "head_ci_mode": "advisory",
                },
                fid="F1",
                severity="high",
            )
        ]
    )
    assert build_protected_surface_changes(report) == []


def test_protected_surface_rollup_ignores_non_verify_and_suppressed():
    from agents_shipgate.schemas.report import Finding

    report = _report_with_findings(
        [
            Finding(
                id="F1",
                check_id="SHIP-DOC-MISSING-DESCRIPTION",
                title="t",
                severity="medium",
                category="documentation",
                recommendation="r",
                evidence={"changed_file": "x"},
            ),
            _verify_finding(
                "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
                {"changed_file": "AGENTS.md", "trust_root_class": "agent_instructions"},
                fid="F2",
                suppressed=True,
            ),
        ]
    )
    # Non-verify finding is ignored; suppressed verify finding is excluded.
    assert build_protected_surface_changes(report) == []


def test_protected_surface_rollup_sorted_deterministically():
    report = _report_with_findings(
        [
            _verify_finding(
                "SHIP-VERIFY-TRIGGER-CATALOG-DRIFT",
                {"changed_trigger_files": ["docs/triggers.json"]},
                fid="F2",
            ),
            _verify_finding(
                "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED",
                {"changed_instruction_files": ["AGENTS.md", "CLAUDE.md"]},
                fid="F1",
            ),
        ]
    )
    rows = build_protected_surface_changes(report)
    # Sorted by (kind, path): agent_instructions/AGENTS.md, .../CLAUDE.md,
    # then trigger_catalog/docs/triggers.json.
    assert [(r.kind, r.path) for r in rows] == [
        ("agent_instructions", "AGENTS.md"),
        ("agent_instructions", "CLAUDE.md"),
        ("trigger_catalog", "docs/triggers.json"),
    ]
    # Deterministic across rebuilds.
    assert [r.model_dump() for r in rows] == [
        r.model_dump() for r in build_protected_surface_changes(report)
    ]


def test_effective_policy_fail_on_sorted_by_tier_rank():
    # fail_on is a severity set sorted by tier rank (info..critical), NOT
    # alphabetically, so a base-vs-head subset/superset comparison is
    # order-independent.
    pol = EffectivePolicy(
        fail_on=["high", "critical", "medium"], waiver_scopes=["z", "a"]
    )
    assert pol.fail_on == ["medium", "high", "critical"]
    assert pol.waiver_scopes == ["a", "z"]


def test_effective_policy_snapshot_from_manifest():
    # The lens builder projects the manifest into the normalized snapshot.
    from agents_shipgate.config.loader import load_manifest

    sample = REPO_ROOT / "samples" / "support_refund_agent" / "shipgate.yaml"
    manifest = load_manifest(sample)
    snap = build_effective_policy_snapshot(manifest)
    assert snap.ci_mode == manifest.ci.mode
    assert snap.fail_on == _sorted_severity_list(manifest.ci.fail_on)
    assert snap.suppressed_check_ids == sorted(
        {s.check_id for s in manifest.checks.ignore}
    )
    # Determinism: a second build is byte-identical.
    assert snap.model_dump() == build_effective_policy_snapshot(manifest).model_dump()


def _sorted_severity_list(values):
    from agents_shipgate.schemas.capability_change import SEVERITY_RANK

    return sorted(set(values or []), key=lambda s: (SEVERITY_RANK.get(s, -1), s))


# --- Schema validation ------------------------------------------------------


def test_report_with_default_blocks_validates_against_v022_schema(tmp_path):
    # Validate a *real* emitted report.json: the real pipeline populates
    # every required top-level block (reviewer_summary, agent_summary,
    # policy/privacy audits, release_consequence, and the five v0.22
    # verifier blocks), so this is the true end-to-end schema guarantee.
    # The hand-built _report() helper only covers the new blocks, so it
    # cannot satisfy the full closed schema.
    from agents_shipgate.cli.scan import run_scan

    sample = REPO_ROOT / "samples" / "support_refund_agent" / "shipgate.yaml"
    run_scan(
        config_path=sample,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    for key in (
        "capability_change",
        "protected_surface_changes",
        "effective_policy",
        "human_ack",
        "verifier_summary",
    ):
        assert key in payload, f"emitted report must carry {key}"

    schema_path = (
        REPO_ROOT / "docs" / f"report-schema.v{CURRENT_SCHEMA_VERSION}.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload, schema=schema)
