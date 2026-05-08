"""End-to-end v0.7 remediation metadata round-trip.

Per the v0.7 plan §3 final-polish verification: agents reading
``agents-shipgate list-checks --json`` and ``report.json`` should both
get populated remediation metadata for every check, and the JSON
contracts on both endpoints should validate against the current schema.

Specifically:

1. ``list-checks --json`` carries non-None ``docs_url``,
   ``autofix_safe``, ``requires_human_review``, ``suggested_patch_kind``
   for every built-in check.
2. ``report.json`` carries the same four fields populated for every
   active finding from a real scan against
   ``samples/support_refund_agent``, both with and without
   ``--suggest-patches``.
3. The ``report.json`` validates against the current report schema.
4. The catalog-vs-Finding contract holds in practice: stale-manifest
   findings whose actual emitted patch is non-manual + high-confidence
   carry ``autofix_safe: True`` even though the catalog stays
   conservative (``autofix_safe: False``).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from jsonschema import validate
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "samples"
SAMPLE_MANIFEST = SAMPLES / "support_refund_agent" / "shipgate.yaml"
REPORT_SCHEMA_V07 = REPO_ROOT / "docs" / "report-schema.v0.7.json"
REPORT_SCHEMA_V08 = REPO_ROOT / "docs" / "report-schema.v0.8.json"
REPORT_SCHEMA_V10 = REPO_ROOT / "docs" / "report-schema.v0.10.json"

REQUIRED_REMEDIATION_KEYS = (
    "autofix_safe",
    "requires_human_review",
    "suggested_patch_kind",
    "docs_url",
)


# --- list-checks --json end-to-end -----------------------------------------


def test_list_checks_json_carries_remediation_metadata_for_every_check():
    runner = CliRunner()
    result = runner.invoke(app, ["list-checks", "--json"])
    assert result.exit_code == 0, result.output
    catalog = json.loads(result.output)
    assert catalog, "empty catalog from list-checks --json"
    for entry in catalog:
        for key in REQUIRED_REMEDIATION_KEYS:
            assert key in entry, (
                f"{entry['id']} missing remediation key {key!r} in "
                "list-checks --json"
            )
            assert entry[key] is not None, (
                f"{entry['id']}.{key} is None in list-checks --json — "
                "all four fields must be populated for every catalog entry"
            )


def test_explain_json_carries_remediation_metadata():
    """`explain <id> --json` is the per-check programmatic surface
    agents use when they want full context on one finding ID. It must
    surface the same remediation fields as the catalog."""
    runner = CliRunner()
    result = runner.invoke(
        app, ["explain", "SHIP-MANIFEST-STALE-SUPPRESSION", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    for key in REQUIRED_REMEDIATION_KEYS:
        assert key in payload, f"explain --json missing {key!r}"
        assert payload[key] is not None, f"explain --json {key!r} is None"


# --- report.json end-to-end ------------------------------------------------


def test_report_json_populates_metadata_without_suggest_patches(tmp_path):
    """Per the v0.7 contract: even WITHOUT --suggest-patches every
    active finding has the four remediation fields populated, sourced
    from CheckMetadata. Agents reading the JSON contract get
    remediation policy without opting into patches."""
    report, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    active = [f for f in payload["findings"] if not f.get("suppressed")]
    assert active, "expected active findings in support_refund_agent"
    for finding in active:
        for key in REQUIRED_REMEDIATION_KEYS:
            assert key in finding, (
                f"{finding['check_id']} missing {key!r} in report.json "
                "(scan ran without --suggest-patches)"
            )
            assert finding[key] is not None, (
                f"{finding['check_id']}.{key} is None — built-in checks "
                "should always populate from CheckMetadata"
            )


def test_report_json_populates_metadata_with_suggest_patches(tmp_path):
    """Same contract under --suggest-patches: every active finding has
    populated remediation fields. Patch-bearing findings derive them
    from the actual emitted patches (per the strict rule); ManualPatch
    findings inherit safe-closed values."""
    report, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    active = [f for f in payload["findings"] if not f.get("suppressed")]
    assert active
    for finding in active:
        for key in REQUIRED_REMEDIATION_KEYS:
            assert key in finding
            assert finding[key] is not None


# --- v0.7 schema validation ------------------------------------------------


def test_report_json_validates_against_v10_schema_with_patches(tmp_path):
    """v0.7 contract: every active finding has the four remediation
    fields populated. v0.10 adds tool-surface diff fields on top.
    Validate against the current v0.10 schema (the v0.7 file stays
    frozen — see test_reports.py::test_v07_schema_file_is_frozen)."""
    report, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    schema = json.loads(REPORT_SCHEMA_V10.read_text(encoding="utf-8"))
    validate(instance=payload, schema=schema)


def test_report_schema_version_is_v10_in_emitted_report(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["report_schema_version"] == "0.10"


# --- Catalog-vs-Finding contract holds in practice -------------------------


def test_stale_finding_with_unique_match_is_per_finding_autofix_safe(tmp_path):
    """The catalog-vs-Finding contract: stale-manifest checks at the
    catalog level are conservative (`autofix_safe: false`). But a
    finding whose generator emitted a unique high-confidence
    `remove_pointer` patch should carry `autofix_safe: true` at the
    Finding level — that's exactly what derivation enables agents to
    act on.
    """
    workspace = tmp_path / "ws"
    shutil.copytree(SAMPLES / "support_refund_agent", workspace)
    manifest_path = workspace / "shipgate.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    data.setdefault("checks", {})["ignore"] = [
        {
            "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
            "tool": "nonexistent_tool_unique",
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
    payload = json.loads(
        (workspace / "agents-shipgate-reports" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    stale = [
        f
        for f in payload["findings"]
        if f["check_id"] == "SHIP-MANIFEST-STALE-SUPPRESSION" and not f.get("suppressed")
    ]
    assert stale
    finding = stale[0]
    # Per-Finding: derived from actual high-confidence remove_pointer.
    assert finding["autofix_safe"] is True
    assert finding["requires_human_review"] is False
    assert finding["suggested_patch_kind"] == "remove_pointer"

    # Catalog (via list-checks): still conservative.
    runner = CliRunner()
    catalog_result = runner.invoke(app, ["list-checks", "--json"])
    catalog = json.loads(catalog_result.output)
    by_id = {entry["id"]: entry for entry in catalog}
    catalog_entry = by_id["SHIP-MANIFEST-STALE-SUPPRESSION"]
    assert catalog_entry["autofix_safe"] is False, (
        "catalog-level autofix_safe must stay conservative — generator "
        "can fall back to ManualPatch on duplicates"
    )


# --- v0.10 release version sanity check ------------------------------------


def test_package_version_is_v010():
    """Final-polish guard: catches the case where the schema bumped to
    v0.10 but the package version was left behind. Both move together."""
    import agents_shipgate

    assert agents_shipgate.__version__ == "0.10.0", (
        f"package version is {agents_shipgate.__version__!r}; "
        "expected 0.10.0 for the v0.10 release"
    )
