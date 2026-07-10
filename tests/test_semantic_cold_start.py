from __future__ import annotations

from pathlib import Path

from agents_shipgate.cli.scan import run_scan

FIXTURE = Path("tests/fixtures/semantic_cold_start/shipgate.yaml")


def test_unaugmented_framework_cold_start_is_measured_as_insufficient_evidence(
    tmp_path: Path,
) -> None:
    """Pin the migration impact before samples gain declarations/inventories.

    This fixture intentionally has neither ``action_surface`` declarations nor
    a framework inventory.  It is not an accuracy corpus; it is a committed
    cold-start regression proving that AST discovery alone cannot silently
    retain a pre-0.16 ``passed`` verdict.
    """

    report, exit_code = run_scan(
        config_path=FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert exit_code == 0
    assert report.release_decision.decision == "insufficient_evidence"
    coverage = report.release_decision.evidence_coverage.semantic_coverage
    assert coverage.total_actions == 1
    assert coverage.pass_eligible_actions == 0
    assert coverage.gap_count >= 1
    gap_kinds = {
        gap.kind for gap in report.release_decision.evidence_coverage.evidence_gaps
    }
    assert "incomplete_surface" in gap_kinds
    assert "missing_authority_evidence" in gap_kinds
