"""§7.4 cross-block consistency invariants for the v0.22 verifier blocks.

These are the contract guarantees that must hold ACROSS the five new
top-level blocks and the existing summary surfaces. They are the heart of
the "one decision engine + byte-stable projections" discipline (Principle
2): every summary/rollup block is derived, so none may disagree with
`release_decision` or with the underlying findings.

The suite drives the REAL scan pipeline (`run_scan`) two ways:

- a plain scan (no verification context), and
- a verify-mode scan with a `VerificationContext` whose changed files
  touch trust roots,

so every block is exercised with realistic, pipeline-produced data rather
than hand-built fixtures.
"""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.verify.capability_review import build_capability_review
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.verification import VerificationContext

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "support_refund_agent" / "shipgate.yaml"

# Changed files that touch several trust roots (manifest, CI gate, agent
# instructions, trigger catalog) so the verify checks fire and the
# capability / protected-surface / human-ack / verifier blocks populate.
_TRUST_ROOT_CHANGED_FILES = [
    "shipgate.yaml",
    ".github/workflows/agents-shipgate.yml",
    "AGENTS.md",
    "docs/triggers.json",
    "src/agent.py",
]


def _scan(tmp_path, *, verification=None):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        verification_context=verification,
    )
    return report


def _verify_scan(tmp_path):
    return _scan(
        tmp_path,
        verification=VerificationContext(changed_files=_TRUST_ROOT_CHANGED_FILES),
    )


def _all_finding_ids(report) -> set[str]:
    ids: set[str] = set()
    for finding in report.findings:
        fid = finding.id or finding.fingerprint
        if fid:
            ids.add(fid)
    return ids


# --- Invariant 1: verdict mirrors release_decision (the one gate) -----------


def test_all_summaries_share_release_decision_verdict(tmp_path):
    report = _verify_scan(tmp_path)
    decision = report.release_decision.decision
    assert report.verifier_summary.verdict == decision
    assert report.agent_summary.verdict == decision
    assert report.reviewer_summary.verdict == decision


def test_verdict_mirrors_decision_on_plain_scan(tmp_path):
    report = _scan(tmp_path)
    assert report.verifier_summary.verdict == report.release_decision.decision


# --- Invariant 2: verifier_summary counts equal the underlying rollups ------


def test_capability_delta_summary_equals_capability_change_lengths(tmp_path):
    report = _verify_scan(tmp_path)
    cap = report.capability_change
    delta = report.verifier_summary.capability_delta_summary
    assert delta.added == len(cap.added)
    assert delta.removed == len(cap.removed)
    assert delta.broadened == len(cap.broadened)
    assert delta.narrowed == len(cap.narrowed)


def test_pr_capability_review_mirrors_canonical_verifier_blocks(tmp_path):
    report = _verify_scan(tmp_path)
    cap = report.capability_change
    vs = report.verifier_summary
    review = build_capability_review(report)

    assert review.added == len(cap.added)
    assert review.modified == len(cap.broadened) + len(cap.narrowed)
    assert review.removed == len(cap.removed)
    assert review.trust_root_touched == vs.protected_surface_touched
    assert review.policy_weakened == vs.policy_weakened
    assert all(change.change_type != "trust_root_touched" for change in review.top_changes)


def test_by_reason_code_sums_to_active_finding_count(tmp_path):
    report = _verify_scan(tmp_path)
    active = [f for f in report.findings if not f.suppressed]
    assert sum(report.verifier_summary.by_reason_code.values()) == len(active)
    assert sum(report.verifier_summary.by_severity.values()) == len(active)


def test_top_reason_codes_are_subset_of_by_reason_code(tmp_path):
    report = _verify_scan(tmp_path)
    vs = report.verifier_summary
    assert len(vs.top_reason_codes) <= 5
    for entry in vs.top_reason_codes:
        assert entry.reason_code in vs.by_reason_code
        assert entry.count == vs.by_reason_code[entry.reason_code]


def test_top_reason_codes_ranked_severity_then_count_then_code(tmp_path):
    report = _verify_scan(tmp_path)
    rank = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    # Recompute the max severity per code from active findings.
    max_sev: dict[str, int] = {}
    for f in report.findings:
        if f.suppressed:
            continue
        max_sev[f.check_id] = max(max_sev.get(f.check_id, -1), rank.get(f.severity, -1))
    keys = [
        (-max_sev.get(t.reason_code, -1), -t.count, t.reason_code)
        for t in report.verifier_summary.top_reason_codes
    ]
    assert keys == sorted(keys), "top_reason_codes not in the contracted rank order"


# --- Invariant 3: protected_surface_changes ↔ active verify findings --------


def test_protected_surface_changes_present_when_trust_root_touched(tmp_path):
    report = _verify_scan(tmp_path)
    assert report.protected_surface_changes, (
        "touching trust roots in verify mode must populate "
        "protected_surface_changes"
    )
    assert report.verifier_summary.protected_surface_touched is True


def test_protected_surface_related_finding_ids_resolve(tmp_path):
    report = _verify_scan(tmp_path)
    all_ids = _all_finding_ids(report)
    for row in report.protected_surface_changes:
        for fid in row.related_finding_ids:
            assert fid in all_ids, (
                f"protected_surface_changes row {row.path!r} references "
                f"finding id {fid!r} that is not in findings[]"
            )


def test_every_active_trust_root_touched_finding_has_a_surface_row(tmp_path):
    report = _verify_scan(tmp_path)
    touched_paths = {
        f.evidence.get("changed_file")
        for f in report.findings
        if not f.suppressed and f.check_id == "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
    }
    row_paths = {row.path for row in report.protected_surface_changes}
    assert touched_paths <= row_paths, (
        "every active TRUST-ROOT-TOUCHED finding must have a "
        "protected_surface_changes row"
    )


def test_plain_scan_has_no_protected_surface_changes(tmp_path):
    report = _scan(tmp_path)
    assert report.protected_surface_changes == []
    assert report.verifier_summary.protected_surface_touched is False


# --- Invariant 4: capability_change finding refs resolve --------------------


def test_capability_change_related_finding_ids_resolve(tmp_path):
    report = _verify_scan(tmp_path)
    all_ids = _all_finding_ids(report)
    for direction in ("added", "removed", "broadened", "narrowed"):
        for member in getattr(report.capability_change, direction):
            for fid in member.related_finding_ids:
                assert fid in all_ids


# --- Invariant 5: human_ack partition + summary mirror ----------------------


def test_human_ack_summary_flags_mirror_block(tmp_path):
    report = _verify_scan(tmp_path)
    ack = report.human_ack
    vs = report.verifier_summary
    assert vs.human_ack_required == ack.required
    assert vs.human_ack_satisfied == ack.satisfied


def test_human_ack_required_implies_outstanding_unless_satisfied(tmp_path):
    report = _verify_scan(tmp_path)
    ack = report.human_ack
    # satisfied iff no outstanding surfaces.
    assert ack.satisfied == (not ack.outstanding)
    if ack.required and not ack.satisfied:
        assert ack.outstanding, "required + unsatisfied must list outstanding surfaces"


def test_policy_weakened_flag_mirrors_finding(tmp_path):
    """The flag answers "was the gate weakened", across both policy reason codes.

    ``SHIP-VERIFY-POLICY-BASE-ABSENT`` is the no-base fail-safe: an unprovable
    direction still counts as weakened, and only a git-proven first adoption
    (``kind: manifest_introduced``) clears the flag.
    """

    report = _verify_scan(tmp_path)
    has_weakened = any(
        not f.suppressed
        and (
            f.check_id == "SHIP-VERIFY-POLICY-WEAKENED"
            or (
                f.check_id == "SHIP-VERIFY-POLICY-BASE-ABSENT"
                and (f.evidence or {}).get("kind") != "manifest_introduced"
            )
        )
        for f in report.findings
    )
    assert report.verifier_summary.policy_weakened == has_weakened


# --- Invariant 6: no summary block introduces a finding-independent gate ----


def test_summary_blocks_do_not_change_the_gate(tmp_path):
    # The verifier blocks are projections; removing them must not alter the
    # decision. Assert the decision is fully determined by findings — the
    # release_decision blockers all trace to real findings.
    report = _verify_scan(tmp_path)
    all_ids = _all_finding_ids(report)
    for blocker in report.release_decision.blockers:
        bid = blocker.id or blocker.fingerprint
        assert bid in all_ids
    # verifier_summary cannot escalate past the decision.
    assert report.verifier_summary.verdict == report.release_decision.decision


# --- Invariant 7: byte-stable determinism across the new blocks -------------


def test_new_blocks_are_byte_stable_across_identical_scans(tmp_path):
    r1 = _verify_scan(tmp_path / "a")
    r2 = _verify_scan(tmp_path / "b")
    p1 = report_json_payload(r1)
    p2 = report_json_payload(r2)
    for key in (
        "capability_change",
        "protected_surface_changes",
        "effective_policy",
        "human_ack",
        "verifier_summary",
    ):
        assert p1[key] == p2[key], f"{key} is not byte-stable across identical scans"


# --- Invariant 8: effective_policy mirrors the manifest ---------------------


def test_effective_policy_mirrors_manifest(tmp_path):
    from agents_shipgate.config.loader import load_manifest

    report = _scan(tmp_path)
    manifest = load_manifest(SAMPLE)
    ep = report.effective_policy
    assert ep.ci_mode == manifest.ci.mode
    assert ep.suppressed_check_ids == sorted(
        {s.check_id for s in manifest.checks.ignore}
    )
    assert ep.baseline_integrity_mode == manifest.baseline.integrity_mode
