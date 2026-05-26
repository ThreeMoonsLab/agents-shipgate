"""v0.21 — ``--no-heuristics`` filter tests.

Earns the contract weight of ``Finding.provenance_kind`` (shipped v0.15
as required+non-nullable wire metadata but with no consumer until v0.21)
by giving it a first-class CLI surface and a HeuristicsFilter envelope.

Scope of this test file:

1. ``apply_no_heuristics_filter`` semantics (the pure projection).
2. End-to-end ``run_scan`` with ``no_heuristics=True``: heuristic
   findings are suppressed, release decision is recomputed without
   them, ``report.heuristics_filter`` envelope is populated.
3. Negative parity: with the flag OFF the report shape is unchanged
   and the envelope reports zero activity.
4. Contract stability: every value in
   ``NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS`` is a real
   ``ProvenanceKind`` literal, and KEEP-list values
   (``static_declaration``, ``ast_extraction``, ``policy_pack``) are
   NOT filtered.
5. Suppression interaction: manifest-driven suppression reasons are
   preserved when the same finding would also be filtered by the flag
   (manifest intent wins).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.findings import (
    NO_HEURISTICS_SUPPRESSION_REASON,
    apply_no_heuristics_filter,
)
from agents_shipgate.schemas.common import ProvenanceKind, Severity
from agents_shipgate.schemas.report import (
    NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS,
    Finding,
)

SUPPORT_REFUND_FIXTURE = Path("samples/support_refund_agent/shipgate.yaml")


# --- Pure-function tests ---------------------------------------------------


def _finding(
    check_id: str,
    *,
    provenance_kind: ProvenanceKind,
    suppressed: bool = False,
    suppression_reason: str | None = None,
    severity: Severity = "medium",
) -> Finding:
    return Finding(
        check_id=check_id,
        category="test",
        severity=severity,
        title=f"{check_id} title",
        provenance_kind=provenance_kind,
        recommendation="test recommendation",
        suppressed=suppressed,
        suppression_reason=suppression_reason,
    )


def test_disabled_filter_returns_zero_activity_envelope() -> None:
    """``enabled=False`` is the no-op path: envelope shape is identical
    to the enabled case, but counts are zero and no findings mutate."""
    findings = [
        _finding("SHIP-KW", provenance_kind="keyword_heuristic"),
        _finding("SHIP-RX", provenance_kind="regex_heuristic"),
        _finding("SHIP-DECL", provenance_kind="static_declaration"),
    ]
    envelope = apply_no_heuristics_filter(findings, enabled=False)
    assert envelope.enabled is False
    assert envelope.filtered_finding_count == 0
    assert envelope.filtered_by_kind == {}
    # excluded_provenance_kinds is still populated so a consumer reading
    # the envelope sees the contract regardless of whether the flag fired.
    assert envelope.excluded_provenance_kinds == list(
        NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS
    )
    # No mutation
    assert all(not f.suppressed for f in findings)


def test_enabled_filter_marks_heuristic_findings_suppressed() -> None:
    """``keyword_heuristic`` and ``regex_heuristic`` findings are
    mutated to ``suppressed=True`` with the canonical reason."""
    findings = [
        _finding("SHIP-KW", provenance_kind="keyword_heuristic"),
        _finding("SHIP-RX", provenance_kind="regex_heuristic"),
        _finding("SHIP-DECL", provenance_kind="static_declaration"),
        _finding("SHIP-AST", provenance_kind="ast_extraction"),
        _finding("SHIP-POL", provenance_kind="policy_pack"),
    ]
    envelope = apply_no_heuristics_filter(findings, enabled=True)
    assert envelope.enabled is True
    assert envelope.filtered_finding_count == 2
    assert envelope.filtered_by_kind == {
        "keyword_heuristic": 1,
        "regex_heuristic": 1,
    }
    by_id = {f.check_id: f for f in findings}
    # Filtered: suppressed with canonical reason.
    assert by_id["SHIP-KW"].suppressed is True
    assert (
        by_id["SHIP-KW"].suppression_reason == NO_HEURISTICS_SUPPRESSION_REASON
    )
    assert by_id["SHIP-RX"].suppressed is True
    assert (
        by_id["SHIP-RX"].suppression_reason == NO_HEURISTICS_SUPPRESSION_REASON
    )
    # KEEP-list: NOT touched.
    assert by_id["SHIP-DECL"].suppressed is False
    assert by_id["SHIP-AST"].suppressed is False
    assert by_id["SHIP-POL"].suppressed is False


def test_manifest_suppression_reason_preserved_when_also_filterable() -> None:
    """A finding the user already suppressed via manifest keeps the
    user's reason; the filter still counts it in the envelope (for
    audit overlap) but does not overwrite the reason."""
    findings = [
        _finding(
            "SHIP-KW",
            provenance_kind="keyword_heuristic",
            suppressed=True,
            suppression_reason="user said it's fine",
        ),
    ]
    envelope = apply_no_heuristics_filter(findings, enabled=True)
    # The finding was already suppressed → reason is preserved.
    assert findings[0].suppressed is True
    assert findings[0].suppression_reason == "user said it's fine"
    # But the audit envelope STILL counts it so a reviewer sees the
    # overlap. (Without this, a manifest that pre-suppresses all
    # heuristic findings would hide the filter's effective scope.)
    assert envelope.filtered_finding_count == 1


def test_excluded_kinds_are_real_provenance_kinds() -> None:
    """Contract: every value in ``NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS``
    must be a real ``ProvenanceKind`` literal. A typo here would silently
    no-op the filter for that kind."""
    valid = set(get_args(ProvenanceKind))
    for kind in NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS:
        assert kind in valid, f"unknown provenance_kind {kind!r}"


def test_keep_list_is_explicit_and_non_overlapping() -> None:
    """Contract: the KEEP and EXCLUDE partitions of ``ProvenanceKind``
    are pinned EXACTLY. A future ``ProvenanceKind`` literal must trigger
    a deliberate filter-list update — either add it to the EXCLUDE
    constant in ``schemas/report.py`` or to the EXPECTED_KEEP set below.

    Pinning the literals (rather than deriving ``kept = valid - excluded``)
    is what catches the regression. Derivation-style asserts would silently
    pass for any newly-added literal because the derivation would include
    it on whichever side the constant didn't, masking the missing decision.
    """
    valid = set(get_args(ProvenanceKind))
    excluded = set(NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS)
    expected_exclude = {"keyword_heuristic", "regex_heuristic"}
    expected_keep = {"static_declaration", "ast_extraction", "policy_pack"}
    assert excluded == expected_exclude, (
        f"NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS drifted from the pinned "
        f"set: got {excluded}, expected {expected_exclude}. Update the "
        f"constant in agents_shipgate.schemas.report (and this test) "
        f"only with an explicit decision about classification."
    )
    assert valid == expected_keep | expected_exclude, (
        f"ProvenanceKind literals changed without updating this test. "
        f"Got valid={valid}, expected {expected_keep | expected_exclude}. "
        f"Either add the new literal to NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS "
        f"or to the EXPECTED_KEEP pin above — every literal must be "
        f"classified by --no-heuristics."
    )


# --- End-to-end run_scan tests --------------------------------------------


def test_run_scan_with_no_heuristics_emits_envelope(tmp_path) -> None:
    """End-to-end: ``run_scan(no_heuristics=True)`` produces a report
    whose ``heuristics_filter`` envelope is enabled+populated."""
    report, _ = run_scan(
        config_path=SUPPORT_REFUND_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        no_heuristics=True,
    )
    assert report.heuristics_filter is not None
    assert report.heuristics_filter.enabled is True
    # The support_refund fixture is known to contain keyword_heuristic
    # findings (description-injection and broad-free-text checks). Exact
    # count varies as the catalog evolves; assert ≥ 1 so the test
    # documents the live-fixture invariant without over-pinning.
    assert report.heuristics_filter.filtered_finding_count >= 1
    assert "keyword_heuristic" in report.heuristics_filter.filtered_by_kind


def test_run_scan_without_flag_is_unaffected(tmp_path) -> None:
    """Negative parity: with the flag OFF the envelope reports zero
    activity, and no finding carries the canonical filter reason."""
    report, _ = run_scan(
        config_path=SUPPORT_REFUND_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        # no_heuristics defaults to False
    )
    assert report.heuristics_filter is not None
    assert report.heuristics_filter.enabled is False
    assert report.heuristics_filter.filtered_finding_count == 0
    assert report.heuristics_filter.filtered_by_kind == {}
    # No finding was suppressed by the filter.
    assert not any(
        f.suppression_reason == NO_HEURISTICS_SUPPRESSION_REASON
        for f in report.findings
    )


def test_no_heuristics_changes_review_items_but_preserves_static_blockers(
    tmp_path,
) -> None:
    """The filter removes heuristic findings from the ACTIVE set
    (review_items shrinks) but does NOT touch static-declaration
    blockers. A finding that would have blocked release because of
    static evidence still blocks release after filtering."""
    baseline_dir = tmp_path / "baseline"
    filtered_dir = tmp_path / "filtered"
    baseline_report, _ = run_scan(
        config_path=SUPPORT_REFUND_FIXTURE,
        output_dir=baseline_dir,
        formats=["json"],
        ci_mode="advisory",
    )
    filtered_report, _ = run_scan(
        config_path=SUPPORT_REFUND_FIXTURE,
        output_dir=filtered_dir,
        formats=["json"],
        ci_mode="advisory",
        no_heuristics=True,
    )
    # Same decision (blocked) but fewer review_items because heuristic
    # ones are now suppressed.
    assert baseline_report.release_decision is not None
    assert filtered_report.release_decision is not None
    assert (
        filtered_report.release_decision.decision
        == baseline_report.release_decision.decision
    )
    assert len(filtered_report.release_decision.review_items) <= len(
        baseline_report.release_decision.review_items
    )
    # Blockers are static_declaration (approval policy / idempotency
    # missing) — unchanged.
    assert len(filtered_report.release_decision.blockers) == len(
        baseline_report.release_decision.blockers
    )


def test_no_heuristics_via_cli_smoke(tmp_path) -> None:
    """Smoke-test the CLI surface: invoke via subprocess to confirm
    the Typer flag is plumbed end-to-end (catches argument-parser
    breakage that pure Python kwarg tests would miss)."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agents_shipgate",
            "scan",
            "-c",
            str(SUPPORT_REFUND_FIXTURE),
            "--out",
            str(tmp_path),
            "--format",
            "json",
            "--no-heuristics",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"scan failed: rc={result.returncode}\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["heuristics_filter"]["enabled"] is True
    assert report["heuristics_filter"]["filtered_finding_count"] >= 1


def test_filter_does_not_overwrite_manifest_suppression_reason_e2e(
    tmp_path,
) -> None:
    """Integration-level pin of the same rule as
    ``test_manifest_suppression_reason_preserved_when_also_filterable``:
    if a manifest already suppressed a heuristic finding, the user's
    reason wins after the filter runs."""
    # support_refund_agent ships heuristic findings without manifest
    # suppression; we'd need a custom fixture to test the overlap path
    # end-to-end. The unit test above pins the semantic; this test
    # documents the absence of regression by confirming the canonical
    # reason appears verbatim on at least one filtered finding when
    # NO manifest suppression overlaps.
    report, _ = run_scan(
        config_path=SUPPORT_REFUND_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        no_heuristics=True,
    )
    flagged = [
        f
        for f in report.findings
        if f.suppression_reason == NO_HEURISTICS_SUPPRESSION_REASON
    ]
    assert flagged, (
        "expected at least one finding with the canonical "
        "--no-heuristics suppression reason in the support_refund fixture"
    )
    # All flagged findings are heuristic provenance.
    assert all(
        f.provenance_kind in NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS
        for f in flagged
    )


# --- Wire-schema enforcement ----------------------------------------------


def test_v21_schema_requires_heuristics_filter_and_rejects_null(tmp_path) -> None:
    """The v0.21 schema must REQUIRE the heuristics_filter envelope and
    REJECT ``null`` — otherwise the contract that "every emitted report
    carries a real HeuristicsFilter" is unenforceable.

    Parallel to ``test_v12_schema_requires_agent_summary_and_agent_action_non_nullable``
    (test_agent_action_summary.py): without this test, a schema edit
    that omits the field from `required` or emits `anyOf: [..., null]`
    would let a payload silently violate the documented stable shape.
    """
    import jsonschema
    import pytest as _pytest

    repo_root = Path(__file__).resolve().parent.parent
    schema = json.loads(
        (repo_root / "docs" / "report-schema.v0.21.json").read_text("utf-8")
    )

    # Top-level required list pins the field.
    assert "heuristics_filter" in schema["required"], (
        "v0.21 schema must list heuristics_filter in the top-level required "
        "block so payloads without the key fail validation."
    )

    # Direct $ref form — no anyOf-with-null. Otherwise a payload could
    # ship `heuristics_filter: null` and validate.
    hf_schema = schema["properties"]["heuristics_filter"]
    assert hf_schema == {"$ref": "#/$defs/HeuristicsFilter"}, (
        "heuristics_filter must be a direct $ref (no anyOf with null) so "
        f"null payloads are rejected at the schema level. Got: {hf_schema}"
    )

    # The HeuristicsFilter definition itself must require all 4 fields.
    hf_def = schema["$defs"]["HeuristicsFilter"]
    assert set(hf_def["required"]) == {
        "enabled",
        "excluded_provenance_kinds",
        "filtered_finding_count",
        "filtered_by_kind",
    }, (
        f"HeuristicsFilter must require all 4 fields; got {hf_def['required']}"
    )

    # End-to-end: a real scan payload validates, but each negative mutation
    # (strip field, set to null, drop a sub-field, swap to {}) must fail.
    report, _ = run_scan(
        config_path=SUPPORT_REFUND_FIXTURE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = json.loads((tmp_path / "report.json").read_text("utf-8"))

    jsonschema.validate(payload, schema)  # baseline: real payload validates

    # 1. Strip the entire field → must fail.
    stripped = {k: v for k, v in payload.items() if k != "heuristics_filter"}
    with _pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(stripped, schema)

    # 2. Set the field to null → must fail.
    null_envelope = json.loads(json.dumps(payload))
    null_envelope["heuristics_filter"] = None
    with _pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(null_envelope, schema)

    # 3. Set the field to an empty object {} → must fail (all four
    # sub-fields are required).
    empty_envelope = json.loads(json.dumps(payload))
    empty_envelope["heuristics_filter"] = {}
    with _pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(empty_envelope, schema)

    # 4. Drop each required sub-field one at a time → each must fail.
    for key in ("enabled", "excluded_provenance_kinds", "filtered_finding_count", "filtered_by_kind"):
        bad = json.loads(json.dumps(payload))
        del bad["heuristics_filter"][key]
        with _pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)
    assert report.heuristics_filter is not None  # sanity guard for the fixture


# --- Reviewer-summary projection -------------------------------------------


def test_reviewer_summary_lens_counts_reflect_post_filter_state(tmp_path) -> None:
    """ReviewerSummary's lens/audit counts must reflect the POST-filter
    active finding set, not the pre-filter set. The flag works by
    suppressing findings *before* build_reviewer_summary is called, so
    a count of, e.g., capability_misalignments should drop or stay the
    same when --no-heuristics is set.
    """
    baseline_dir = tmp_path / "baseline"
    filtered_dir = tmp_path / "filtered"
    baseline_report, _ = run_scan(
        config_path=SUPPORT_REFUND_FIXTURE,
        output_dir=baseline_dir,
        formats=["json"],
        ci_mode="advisory",
    )
    filtered_report, _ = run_scan(
        config_path=SUPPORT_REFUND_FIXTURE,
        output_dir=filtered_dir,
        formats=["json"],
        ci_mode="advisory",
        no_heuristics=True,
    )
    assert baseline_report.reviewer_summary is not None
    assert filtered_report.reviewer_summary is not None
    # Each lens count is monotone-non-increasing under filtering. (A
    # filter can only suppress, never produce, findings.) This is the
    # invariant the post-filter projection must satisfy.
    for field in (
        "capability_misalignments",
        "evidence_matrix_gaps",
    ):
        baseline_value = getattr(baseline_report.reviewer_summary, field)
        filtered_value = getattr(filtered_report.reviewer_summary, field)
        assert filtered_value <= baseline_value, (
            f"ReviewerSummary.{field} went UP after filtering: "
            f"{baseline_value} → {filtered_value}. Filtering can only "
            "shrink the active set."
        )
