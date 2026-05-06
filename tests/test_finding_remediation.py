"""Per-finding remediation derivation, agreement, run_id stability, and
unknown-check-id fallback for v0.7.

Per the v0.7 plan §2 (revised v3+):

- When ``Finding.patches`` is non-empty, the four remediation fields
  are derived from the actual patches with the **strict** rule:
  ``autofix_safe=True`` only when EVERY patch is non-manual AND
  high-confidence. Mixed states fall to safe-closed.
- When ``Finding.patches`` is None (scan ran without ``--suggest-patches``),
  fields come from the matching ``CheckMetadata`` entry, with the
  safe-closed fallback for unknown check IDs (policy-pack /
  third-party plugin findings).
- ``docs_url`` always comes from CheckMetadata; the per-finding patches
  do NOT carry per-instance doc URLs.
- ``_run_id`` excludes all four fields plus ``patches`` so toggling
  ``--suggest-patches`` or adding new derived fields can never shift
  the hash.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.findings import (
    _REMEDIATION_FALLBACK,
    annotate_remediation,
)
from agents_shipgate.core.models import CheckMetadata, Finding
from agents_shipgate.core.patches import (
    AppendPointerPatch,
    ManualPatch,
    RemovePointerPatch,
    SetPointerPatch,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SAMPLE_MANIFEST = SAMPLES / "support_refund_agent" / "shipgate.yaml"


def _builtin_lookup() -> dict[str, CheckMetadata]:
    return {check.id: check for check in check_catalog(plugins_enabled=False)}


def _finding(check_id: str, patches=None) -> Finding:
    return Finding(
        check_id=check_id,
        title="t",
        severity="medium",
        category="manifest",
        evidence={},
        confidence="medium",
        recommendation="r",
        patches=patches,
    )


def _high_remove(check_id: str = "SHIP-MANIFEST-STALE-SUPPRESSION") -> RemovePointerPatch:
    return RemovePointerPatch(
        target_file="/tmp/x/shipgate.yaml",
        pointer="/checks/ignore/0",
        target_format="yaml",
        confidence="high",
        rationale="r",
        target_sha256="0" * 64,
    )


def _medium_append() -> AppendPointerPatch:
    return AppendPointerPatch(
        target_file="/tmp/x/shipgate.yaml",
        pointer="/permissions/scopes",
        value="read:x",
        target_format="yaml",
        confidence="medium",
        rationale="r",
        target_sha256="0" * 64,
    )


def _low_set() -> SetPointerPatch:
    return SetPointerPatch(
        target_file="/tmp/x/shipgate.yaml",
        pointer="/foo",
        value="bar",
        target_format="yaml",
        confidence="low",
        rationale="r",
        target_sha256="0" * 64,
    )


# --- Strict derivation rule ------------------------------------------------


def test_all_high_non_manual_yields_autofix_safe_true():
    finding = _finding(
        "SHIP-MANIFEST-STALE-SUPPRESSION",
        patches=[_high_remove(), _high_remove()],
    )
    annotate_remediation([finding], _builtin_lookup())
    assert finding.autofix_safe is True
    assert finding.requires_human_review is False
    assert finding.suggested_patch_kind == "remove_pointer"


def test_mixed_high_and_manual_falls_to_safe_closed():
    """One ManualPatch among the patches → never auto-safe.

    This is the v3 strict rule. The earlier "at least one safe patch"
    rule was unsafe — it would have marked this combination
    auto-fixable while a ManualPatch would still require review.
    """
    finding = _finding(
        "SHIP-MANIFEST-STALE-SUPPRESSION",
        patches=[_high_remove(), ManualPatch(instructions="implement gate")],
    )
    annotate_remediation([finding], _builtin_lookup())
    assert finding.autofix_safe is False
    assert finding.requires_human_review is True
    # First non-manual patch's kind reported.
    assert finding.suggested_patch_kind == "remove_pointer"


def test_mixed_high_and_medium_falls_to_safe_closed():
    """High + medium → mixed-confidence → not auto-safe."""
    finding = _finding(
        "SHIP-AUTH-SCOPE-COVERAGE-MISSING",
        patches=[_high_remove(), _medium_append()],
    )
    annotate_remediation([finding], _builtin_lookup())
    assert finding.autofix_safe is False
    assert finding.requires_human_review is True


def test_low_confidence_alone_is_not_auto_safe():
    finding = _finding(
        "SHIP-AUTH-SCOPE-COVERAGE-MISSING",
        patches=[_low_set()],
    )
    annotate_remediation([finding], _builtin_lookup())
    assert finding.autofix_safe is False
    assert finding.requires_human_review is True
    assert finding.suggested_patch_kind == "set_pointer"


def test_all_manual_yields_manual_kind():
    finding = _finding(
        "SHIP-API-TRACE-APPROVAL-MISSING",
        patches=[ManualPatch(instructions="x"), ManualPatch(instructions="y")],
    )
    annotate_remediation([finding], _builtin_lookup())
    assert finding.suggested_patch_kind == "manual"
    assert finding.autofix_safe is False
    assert finding.requires_human_review is True


def test_empty_patches_list_yields_none_kind_and_safe_closed():
    """An explicit empty patches list means the scan ran with
    ``--suggest-patches`` but the generator emitted nothing for this
    finding. The annotation must NOT fall through to the catalog
    (which could misleadingly report a patch kind the report doesn't
    actually carry); instead it returns the safe-closed shape with
    ``suggested_patch_kind="none"``.

    Regression for v0.7 PR 3 review feedback: the earlier
    ``if finding.patches:`` check treated ``[]`` like ``None``,
    bypassing the empty-list branch in ``_derive_from_patches``."""
    finding = _finding("SHIP-MANIFEST-STALE-SUPPRESSION", patches=[])
    annotate_remediation([finding], _builtin_lookup())
    assert finding.autofix_safe is False
    assert finding.requires_human_review is True
    assert finding.suggested_patch_kind == "none"


# --- CheckMetadata fallback (no patches) -----------------------------------


def test_fields_seed_from_catalog_when_no_patches():
    finding = _finding("SHIP-MANIFEST-STALE-SUPPRESSION", patches=None)
    lookup = _builtin_lookup()
    annotate_remediation([finding], lookup)
    catalog = lookup["SHIP-MANIFEST-STALE-SUPPRESSION"]
    assert finding.autofix_safe is catalog.autofix_safe
    assert finding.requires_human_review is catalog.requires_human_review
    assert finding.suggested_patch_kind == catalog.suggested_patch_kind
    assert finding.docs_url == catalog.docs_url


# --- Unknown check ID fallback (policy-pack / plugin findings) -------------


def test_unknown_check_id_uses_safe_closed_fallback():
    """Findings whose check_id isn't in the catalog (policy packs,
    third-party plugins emitted while plugins are disabled) get the
    safe-closed fallback values, never None for the safety bools."""
    finding = _finding("PACK-CUSTOM-RULE-001", patches=None)
    annotate_remediation([finding], _builtin_lookup())
    assert finding.autofix_safe is _REMEDIATION_FALLBACK["autofix_safe"]
    assert (
        finding.requires_human_review
        is _REMEDIATION_FALLBACK["requires_human_review"]
    )
    assert (
        finding.suggested_patch_kind
        == _REMEDIATION_FALLBACK["suggested_patch_kind"]
    )
    assert finding.docs_url is None


def test_unknown_check_id_with_high_patches_still_derives():
    """Even unknown check IDs derive from patches when present — the
    fallback only applies when patches are absent. A high-confidence
    non-manual patch is auto-safe regardless of catalog presence."""
    finding = _finding("PACK-CUSTOM-RULE-002", patches=[_high_remove()])
    annotate_remediation([finding], _builtin_lookup())
    assert finding.autofix_safe is True
    assert finding.requires_human_review is False
    assert finding.suggested_patch_kind == "remove_pointer"
    # docs_url is None because no catalog entry exists.
    assert finding.docs_url is None


# --- docs_url always from catalog ------------------------------------------


def test_docs_url_is_always_from_catalog_not_patches():
    """Even when patches are present, docs_url comes from CheckMetadata.
    Patches don't carry per-instance documentation URLs."""
    finding = _finding(
        "SHIP-MANIFEST-STALE-SUPPRESSION",
        patches=[_high_remove()],
    )
    lookup = _builtin_lookup()
    annotate_remediation([finding], lookup)
    assert finding.docs_url == lookup["SHIP-MANIFEST-STALE-SUPPRESSION"].docs_url


# --- run_id stability (per finding 2 of v0.7 review) -----------------------


def test_run_id_unchanged_across_suggest_patches_toggle(tmp_path):
    """v0.7's four new derived fields must NOT enter `_run_id`.

    Two scans of the same workspace — one with --suggest-patches and
    one without — must produce the same run_id. If a future contributor
    forgets to add a new derived field to the exclude set, this test
    fails loudly.
    """
    report_no_patches, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path / "no_patches",
        formats=["json"],
        ci_mode="advisory",
    )
    report_with_patches, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path / "with_patches",
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    assert report_no_patches.run_id == report_with_patches.run_id


# --- End-to-end via real scan ---------------------------------------------


def test_scan_without_suggest_patches_still_populates_fields(tmp_path):
    """Per the v0.7 contract: even WITHOUT --suggest-patches every
    active finding has the four remediation fields populated, sourced
    from CheckMetadata."""
    report, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    active = [f for f in report.findings if not f.suppressed]
    assert active, "expected active findings in support_refund_agent"
    for finding in active:
        assert finding.autofix_safe is not None, finding.check_id
        assert finding.requires_human_review is not None, finding.check_id
        assert finding.suggested_patch_kind is not None, finding.check_id
        # docs_url is populated for built-in checks (catalog has it for
        # all 45 entries since PR 2).
        assert finding.docs_url is not None, finding.check_id


def test_scan_with_suggest_patches_derives_from_actual_patches(tmp_path):
    """Inject a stale suppression to trigger a real high-confidence
    remove_pointer patch; verify the per-finding fields reflect the
    actual emission."""
    workspace = tmp_path / "ws"
    shutil.copytree(SAMPLES / "support_refund_agent", workspace)
    manifest_path = workspace / "shipgate.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    data.setdefault("checks", {})["ignore"] = [
        {
            "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
            "tool": "nonexistent_tool",
            "reason": "stale fixture",
        }
    ]
    manifest_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    report, _ = run_scan(
        config_path=manifest_path,
        output_dir=workspace / "agents-shipgate-reports",
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    stale_findings = [
        f
        for f in report.findings
        if f.check_id == "SHIP-MANIFEST-STALE-SUPPRESSION" and not f.suppressed
    ]
    assert stale_findings, "expected SHIP-MANIFEST-STALE-SUPPRESSION finding"
    finding = stale_findings[0]
    # Derived from actual patches (high-confidence remove_pointer).
    assert finding.autofix_safe is True
    assert finding.requires_human_review is False
    assert finding.suggested_patch_kind == "remove_pointer"


def test_report_json_payload_carries_new_fields(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)
    active = [f for f in payload["findings"] if not f.get("suppressed")]
    assert active
    for finding in active:
        for key in (
            "autofix_safe",
            "requires_human_review",
            "suggested_patch_kind",
            "docs_url",
        ):
            assert key in finding, f"{finding['check_id']} missing {key} in JSON"


def test_report_schema_version_is_v09(tmp_path):
    """Schema version moved from 0.8 to 0.9 per the additive contract
    in STABILITY.md. Old reports validate against their respective
    schema files, but new scans emit 0.9 with the capability/intent diff."""
    report, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["report_schema_version"] == "0.9"
    assert "release_decision" in payload
    assert "misalignments" in payload
