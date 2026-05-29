"""Tier B SHIP-VERIFY-* trust-root weakening checks (roadmap §5.1/§5.3).

Covers the five semantic-weakening checks added in M3:

- SHIP-VERIFY-POLICY-WEAKENED
- SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED
- SHIP-VERIFY-CI-GATE-REMOVED
- SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED
- SHIP-VERIFY-TRIGGER-CATALOG-DRIFT

Plus the normalized effective-policy snapshot they compare against. Each
check must: (a) emit nothing without a VerificationContext, (b) compare
base-vs-head via the snapshot (not a text diff), (c) fail safe to
review_required when direction can't be proven, and (d) emit ordinary
category-"verify" findings that flow through the one decision engine.
"""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.checks import (
    verify_agent_instructions,
    verify_baseline_waiver,
    verify_ci_gate,
    verify_policy,
    verify_trigger_drift,
)
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.lenses.effective_policy import (
    build_effective_policy_snapshot,
)
from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference
from agents_shipgate.schemas.capability_change import EffectivePolicy
from agents_shipgate.schemas.verification import VerificationContext

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")


def _manifest():
    return load_manifest(SAMPLE)


def _context(
    *,
    changed_files: list[str] | None = None,
    verification: bool = True,
    base_policy: EffectivePolicy | None = None,
    manifest=None,
    config_path: str = "shipgate.yaml",
) -> ScanContext:
    vc = (
        VerificationContext(changed_files=changed_files or [])
        if verification
        else None
    )
    diff_reference = (
        ToolSurfaceDiffReference(kind="report", facts=None, effective_policy=base_policy)
        if base_policy is not None
        else None
    )
    return ScanContext(
        manifest=manifest or _manifest(),
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path(config_path),
        verification=vc,
        diff_reference=diff_reference,
    )


# --- effective-policy snapshot ---------------------------------------------


def test_snapshot_is_deterministic_and_normalized():
    manifest = _manifest()
    a = build_effective_policy_snapshot(manifest)
    b = build_effective_policy_snapshot(manifest)
    assert a.model_dump() == b.model_dump()
    # fail_on is tier-sorted, lists are sorted+unique.
    assert a.fail_on == sorted(set(a.fail_on), key=_rank)
    assert a.suppressed_check_ids == sorted(set(a.suppressed_check_ids))


def _rank(sev):
    from agents_shipgate.schemas.capability_change import SEVERITY_RANK

    return (SEVERITY_RANK.get(sev, -1), sev)


# --- SHIP-VERIFY-POLICY-WEAKENED -------------------------------------------


def test_policy_weakened_no_verification_emits_nothing():
    assert verify_policy.run(_context(verification=False)) == []


def test_policy_weakened_ci_mode_strict_to_advisory():
    base = EffectivePolicy(ci_mode="strict", fail_on=["high", "critical"])
    ctx = _context(base_policy=base)
    # Head manifest (support_refund) is advisory by default -> weakened.
    findings = verify_policy.run(ctx)
    kinds = {f.evidence.get("kind") for f in findings}
    assert "ci_mode_weakened" in kinds
    assert all(f.check_id == "SHIP-VERIFY-POLICY-WEAKENED" for f in findings)
    assert all(f.category == "verify" for f in findings)
    assert all(not f.blocks_release for f in findings)


def test_policy_weakened_fail_on_loosened():
    head = build_effective_policy_snapshot(_manifest())
    # Base required an extra severity that head no longer fails on.
    base = EffectivePolicy(
        ci_mode=head.ci_mode,
        fail_on=sorted(set(head.fail_on) | {"critical", "high", "medium"}),
    )
    findings = verify_policy.run(_context(base_policy=base))
    kinds = {f.evidence.get("kind") for f in findings}
    assert "fail_on_loosened" in kinds


def test_policy_weakened_severity_override_lowered():
    head = build_effective_policy_snapshot(_manifest())
    base = EffectivePolicy(
        ci_mode=head.ci_mode,
        fail_on=head.fail_on,
        severity_overrides={"SHIP-DOC-MISSING-DESCRIPTION": "critical"},
    )
    findings = verify_policy.run(_context(base_policy=base))
    kinds = {f.evidence.get("kind") for f in findings}
    assert "severity_override_lowered" in kinds
    lowered = [
        f for f in findings if f.evidence.get("kind") == "severity_override_lowered"
    ]
    assert lowered[0].evidence["base_severity"] == "critical"
    assert lowered[0].evidence["head_severity"] == "medium"


def test_policy_weakened_new_downgrade_override_uses_catalog_default():
    from agents_shipgate.schemas.manifest import SeverityOverrideEntry

    manifest = _manifest()
    manifest.checks.severity_overrides["SHIP-AUTH-MANIFEST-BROAD-SCOPE"] = (
        SeverityOverrideEntry(severity="medium")
    )
    head = build_effective_policy_snapshot(manifest)
    base = EffectivePolicy(ci_mode=head.ci_mode, fail_on=head.fail_on)
    findings = verify_policy.run(_context(base_policy=base, manifest=manifest))
    lowered = [
        f for f in findings if f.evidence.get("kind") == "severity_override_lowered"
    ]
    assert lowered
    assert lowered[0].evidence["target_check_id"] == "SHIP-AUTH-MANIFEST-BROAD-SCOPE"
    assert lowered[0].evidence["base_severity"] == "high"
    assert lowered[0].evidence["head_severity"] == "medium"


def test_policy_weakened_strengthening_emits_nothing():
    # Base was WEAKER (advisory + smaller fail_on); the head sample manifest
    # is same-or-stronger, so no weakening kind should fire.
    base = EffectivePolicy(ci_mode="advisory", fail_on=[])
    findings = verify_policy.run(_context(base_policy=base))
    # No weakening kinds (ci same, fail_on grew or equal).
    kinds = {f.evidence.get("kind") for f in findings}
    assert "ci_mode_weakened" not in kinds
    assert "fail_on_loosened" not in kinds


def test_policy_weakened_no_base_but_policy_touched_fails_safe():
    # No base snapshot, but the PR changed shipgate.yaml -> review-required.
    findings = verify_policy.run(
        _context(base_policy=None, changed_files=["shipgate.yaml"])
    )
    assert len(findings) == 1
    assert findings[0].evidence["kind"] == "base_snapshot_unavailable"
    assert findings[0].severity == "medium"


def test_policy_weakened_no_base_no_policy_touch_emits_nothing():
    findings = verify_policy.run(
        _context(base_policy=None, changed_files=["README.md"])
    )
    assert findings == []


# --- SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED -------------------------------


def test_waiver_suppression_expanded():
    head = build_effective_policy_snapshot(_manifest())
    # Base had FEWER suppressions than head. Inject a head suppression by
    # comparing against an empty base.
    base = EffectivePolicy(
        ci_mode=head.ci_mode,
        fail_on=head.fail_on,
        suppressed_check_ids=[],
        waiver_scopes=[],
    )
    if not head.suppressed_check_ids:
        # The sample has no suppressions; simulate one by swapping base/head
        # roles via a synthetic head with a suppression.
        base = EffectivePolicy(suppressed_check_ids=[], waiver_scopes=[])
        ctx = _context(base_policy=base)
        # Patch the manifest's ignore list through a fresh snapshot.
        from agents_shipgate.schemas.manifest import SuppressionConfig

        ctx.manifest.checks.ignore.append(
            SuppressionConfig(check_id="SHIP-DOC-MISSING-DESCRIPTION", reason="x")
        )
        findings = verify_baseline_waiver.run(ctx)
        kinds = {f.evidence.get("kind") for f in findings}
        assert "suppression_expanded" in kinds
        assert any(
            "SHIP-DOC-MISSING-DESCRIPTION" in f.evidence.get("added_check_ids", [])
            for f in findings
        )


def test_waiver_no_base_emits_nothing():
    # Touching baseline files without a base is covered by TRUST-ROOT-TOUCHED;
    # the expansion check needs the base and emits nothing without it.
    findings = verify_baseline_waiver.run(
        _context(base_policy=None, changed_files=[".agents-shipgate/baseline.json"])
    )
    assert findings == []


def test_waiver_no_expansion_emits_nothing():
    head = build_effective_policy_snapshot(_manifest())
    base = EffectivePolicy(
        ci_mode=head.ci_mode,
        fail_on=head.fail_on,
        suppressed_check_ids=head.suppressed_check_ids,
        waiver_scopes=head.waiver_scopes,
        baseline_fingerprints=[],
    )
    findings = verify_baseline_waiver.run(_context(base_policy=base))
    assert findings == []


def test_baseline_expansion_compares_after_baseline_matching(tmp_path):
    from agents_shipgate.cli.scan import run_scan
    from agents_shipgate.core.baseline import write_baseline

    base_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    baseline_path = tmp_path / "baseline.json"
    write_baseline(
        base_report,
        baseline_path,
        audit_log_path=tmp_path / "baseline-audit.log",
    )

    head_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "head",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
        diff_from_path=tmp_path / "base" / "report.json",
        verification_context=VerificationContext(
            changed_files=[".agents-shipgate/baseline.json"]
        ),
        packet_enabled=False,
    )

    baseline_findings = [
        f
        for f in head_report.findings
        if f.check_id == "SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED"
        and f.evidence.get("kind") == "baseline_expanded"
    ]
    assert baseline_findings
    assert set(baseline_findings[0].evidence["added_fingerprints"]) == {
        f.fingerprint for f in head_report.findings if f.baseline_status == "matched"
    }


# --- SHIP-VERIFY-CI-GATE-REMOVED -------------------------------------------


def test_ci_gate_removed_when_workflow_deleted(tmp_path):
    # config_path's parent is the workspace; the workflow file does NOT
    # exist there, so a changed-files entry for it reads as a deletion.
    cfg = tmp_path / "shipgate.yaml"
    ctx = _context(
        changed_files=[".github/workflows/agents-shipgate.yml"],
        config_path=str(cfg),
    )
    findings = verify_ci_gate.run(ctx)
    assert len(findings) == 1
    assert findings[0].check_id == "SHIP-VERIFY-CI-GATE-REMOVED"
    assert findings[0].severity == "critical"
    assert findings[0].evidence["kind"] == "ci_gate_removed"


def test_ci_gate_present_emits_nothing(tmp_path):
    workflow = tmp_path / ".github" / "workflows" / "agents-shipgate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: shipgate\n", encoding="utf-8")
    ctx = _context(
        changed_files=[".github/workflows/agents-shipgate.yml"],
        config_path=str(tmp_path / "shipgate.yaml"),
    )
    # File exists (modified, not removed) -> no finding.
    assert verify_ci_gate.run(ctx) == []


def test_ci_gate_no_verification_emits_nothing():
    assert verify_ci_gate.run(_context(verification=False)) == []


# --- SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED -------------------------------


def test_agent_instructions_weakened_on_change():
    findings = verify_agent_instructions.run(_context(changed_files=["AGENTS.md"]))
    assert len(findings) == 1
    assert findings[0].check_id == "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED"
    assert findings[0].severity == "medium"


def test_agent_instructions_unrelated_file_emits_nothing():
    assert verify_agent_instructions.run(_context(changed_files=["src/app.py"])) == []


def test_agent_instructions_no_verification_emits_nothing():
    assert verify_agent_instructions.run(_context(verification=False)) == []


# --- SHIP-VERIFY-TRIGGER-CATALOG-DRIFT -------------------------------------


def test_trigger_catalog_drift_on_triggers_json():
    findings = verify_trigger_drift.run(
        _context(changed_files=["docs/triggers.json"])
    )
    assert len(findings) == 1
    assert findings[0].check_id == "SHIP-VERIFY-TRIGGER-CATALOG-DRIFT"


def test_trigger_catalog_unrelated_emits_nothing():
    assert verify_trigger_drift.run(_context(changed_files=["docs/README.md"])) == []


def test_trigger_catalog_no_verification_emits_nothing():
    assert verify_trigger_drift.run(_context(verification=False)) == []
