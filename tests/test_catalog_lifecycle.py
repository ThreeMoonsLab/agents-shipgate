"""The check catalog's lifecycle metadata and its projection IDs (#1047).

Every deprecated ID is marked in machine-readable form, the CLI shows it, and a
baseline naming any deprecated ID is reported as stale. Every ID a review
surface prints resolves through ``explain``, and an ID catalogued only so it
can be explained (a projection) is never treated as a configurable check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.checks import _metadata_loader, registry
from agents_shipgate.checks.plugin_validation import BAD_METADATA, validate_entry_point
from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.baseline import verify_baseline
from agents_shipgate.core.baseline_audit import (
    BaselineAuditEntry,
    append_audit_entry,
    compute_baseline_hash_from_file,
)
from agents_shipgate.core.check_ids import LEGACY_CHECK_ID_ALIASES
from agents_shipgate.core.domain import SemanticIssueKind
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.severity_overrides import resolve_severity_overrides
from agents_shipgate.inputs.coverage import BoundaryCoverageError, build_boundary_matrix
from agents_shipgate.report.pr_projection import PROJECTION_CHECK_IDS, select_pr_items
from agents_shipgate.report.sarif import _semantic_gap_rule_id
from agents_shipgate.schemas.baseline import BaselineFile, BaselineFinding, BaselineProvenance
from agents_shipgate.schemas.checks import CheckMetadata
from agents_shipgate.schemas.manifest.severity_overrides import SeverityOverrideEntry

REPO_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()

DEPRECATED = {
    "SHIP-API-OPERATIONAL-READINESS": "0.4.0",
    "SHIP-CODEX-BOUNDARY-AGENTS-SHIPGATE-REQUIREMENT-REMOVED": "1.1.0",
    "SHIP-CODEX-BOUNDARY-SKILL-COMMAND-CHANGED": "1.1.0",
    "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED": "1.1.0",
}
SEMANTIC_RULE_IDS = {_semantic_gap_rule_id(kind) for kind in get_args(SemanticIssueKind)}
PROJECTIONS = {*PROJECTION_CHECK_IDS, *SEMANTIC_RULE_IDS}
# Projection IDs the code no longer prints. A shipped ID is never removed
# (STABILITY.md § Check catalog metadata), so a retired one stays catalogued
# with lifecycle projection; name it here when its emitter goes away.
RETIRED_PROJECTIONS: frozenset[str] = frozenset()


def _catalog() -> dict[str, CheckMetadata]:
    return {check.id: check for check in registry.check_catalog(plugins_enabled=False)}


def _explain(*args: str):
    return runner.invoke(app, ["explain", *args, "--no-plugins"])


# --- Lifecycle metadata -----------------------------------------------------


def test_every_check_that_no_longer_fires_is_marked_deprecated() -> None:
    catalog = _catalog()
    retired_by_prose = {
        check.id for check in catalog.values() if check.description.startswith("Deprecated")
    }
    assert retired_by_prose == set(DEPRECATED)
    assert {
        check.id: check.deprecated_in
        for check in catalog.values()
        if check.lifecycle == "deprecated"
    } == DEPRECATED
    # A legacy alias and its catalog entry name the same replacements.
    for alias, replacements in LEGACY_CHECK_ID_ALIASES.items():
        assert catalog[alias].replaced_by == list(replacements)


def test_list_checks_json_and_docs_catalog_carry_lifecycle() -> None:
    result = runner.invoke(app, ["list-checks", "--json", "--no-plugins"])
    assert result.exit_code == 0, result.output
    published = json.loads((REPO_ROOT / "docs" / "checks.json").read_text("utf-8"))["checks"]
    for entries in (json.loads(result.output), published):
        by_lifecycle: dict[str, dict[str, str | None]] = {}
        for entry in entries:
            assert {"lifecycle", "deprecated_in", "replaced_by"} <= entry.keys(), entry["id"]
            by_lifecycle.setdefault(entry["lifecycle"], {})[entry["id"]] = entry["deprecated_in"]
        assert by_lifecycle["deprecated"] == DEPRECATED
        assert set(by_lifecycle["projection"]) == PROJECTIONS | RETIRED_PROJECTIONS
        assert "SHIP-POLICY-APPROVAL-MISSING" in by_lifecycle["active"]


def test_list_checks_text_and_explain_show_the_lifecycle() -> None:
    listed = runner.invoke(app, ["list-checks", "--no-plugins"])
    rows = {line.split("\t")[0]: line.split("\t") for line in listed.output.splitlines()}
    assert {len(row) for row in rows.values()} == {6}
    assert rows["SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED"][5] == "deprecated"
    assert rows["SHIP-CAPABILITY-CHANGE"][5] == "projection"
    assert rows["SHIP-POLICY-APPROVAL-MISSING"][5] == "active"

    assert (
        "Lifecycle: deprecated in 0.4.0; replaced by SHIP-API-RETRY-POLICY-MISSING, "
        in _explain("SHIP-API-OPERATIONAL-READINESS").output
    )
    assert (
        "Lifecycle: deprecated in 1.1.0; nothing replaces it."
        in _explain("SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED").output
    )
    assert "Lifecycle: projection." in _explain("SHIP-CAPABILITY-CHANGE").output
    assert json.loads(_explain("SHIP-POLICY-APPROVAL-MISSING", "--json").output)[
        "lifecycle"
    ] == "active"


def _baseline_naming(tmp_path: Path, check_id: str) -> tuple[Path, Path]:
    baseline_path = tmp_path / "baseline.json"
    provenance = BaselineProvenance(
        scanner_version="1.0.0", run_id="run_x", recorded_at="2026-01-01T00:00:00Z"
    )
    finding = BaselineFinding(
        fingerprint="fp_retired",
        check_id=check_id,
        tool_name="t",
        severity="medium",
        title="retired",
        provenance=provenance,
    )
    baseline = BaselineFile(
        created_at="2026-01-01T00:00:00Z", source_report_run_id="run_x", findings=[finding]
    )
    baseline_path.write_text(baseline.model_dump_json(indent=2) + "\n", encoding="utf-8")
    audit_path = tmp_path / "baseline-audit.log"
    append_audit_entry(
        audit_path,
        BaselineAuditEntry(
            timestamp="2026-01-01T00:00:00Z",
            run_id="run_x",
            scanner_version="1.0.0",
            baseline_path=str(baseline_path),
            hash_before=None,
            hash_after=compute_baseline_hash_from_file(baseline_path),
        ),
    )
    return baseline_path, audit_path


@pytest.mark.parametrize("check_id", sorted(DEPRECATED))
def test_a_baseline_naming_any_deprecated_id_is_reported_stale(
    tmp_path: Path, check_id: str
) -> None:
    issues = [
        issue
        for issue in verify_baseline(*_baseline_naming(tmp_path, check_id))
        if issue.kind == "deprecated_check_id"
    ]

    assert len(issues) == 1
    replacements = issues[0].evidence["replacement_check_ids"]
    assert replacements == sorted(_catalog()[check_id].replaced_by)
    assert replacements or "remove the entry" in issues[0].title


@pytest.mark.parametrize("check_id", sorted(DEPRECATED))
def test_a_scan_reports_a_deprecated_baseline_entry_once(tmp_path: Path, check_id: str) -> None:
    """The entry is also unmatched, but one entry is one stale finding."""

    baseline_path, _ = _baseline_naming(tmp_path, check_id)
    report, _ = run_scan(
        config_path=REPO_ROOT / "samples" / "clean_read_only_agent" / "shipgate.yaml",
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="strict",
        baseline_path=baseline_path,
    )

    stale = [f for f in report.findings if f.check_id == "SHIP-BASELINE-ENTRY-STALE"]
    assert [finding.evidence["kind"] for finding in stale] == ["deprecated_check_id"]


@pytest.mark.parametrize("version", ["1.0", ""])
def test_only_a_deprecated_entry_carries_deprecation_fields(version: str) -> None:
    with pytest.raises(ValueError, match="only a deprecated ID"):
        CheckMetadata(
            id="SHIP-X", category="x", default_severity="low", description="x", deprecated_in=version
        )


@pytest.mark.parametrize("replacement", ["SHIP-FAKE-MISSING", "SHIP-FAKE-PROJECTION"])
def test_loader_refuses_a_replacement_that_is_not_an_active_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    monkeypatch.setattr(_metadata_loader, "_resolve_checks_dir", lambda: tmp_path)
    rows = [
        {"id": "SHIP-FAKE-OLD", "default_severity": "low", "description": "old"}
        | {"lifecycle": "deprecated", "replaced_by": [replacement]},
        {"id": "SHIP-FAKE-PROJECTION", "default_severity": "low", "description": "p"}
        | {"lifecycle": "projection"},
    ]
    (tmp_path / "fake.yaml").write_text(yaml.safe_dump({"checks": rows}), encoding="utf-8")

    with pytest.raises(ValueError, match="not an active check"):
        _metadata_loader.load_check_metadata()


# --- Projection IDs -----------------------------------------------------------


def test_the_catalogued_projections_are_exactly_what_the_projections_print() -> None:
    assert {
        check_id for check_id, check in _catalog().items() if check.lifecycle == "projection"
    } == PROJECTIONS | RETIRED_PROJECTIONS
    assert not PROJECTIONS & RETIRED_PROJECTIONS
    assert len(SEMANTIC_RULE_IDS) == len(get_args(SemanticIssueKind))


@pytest.mark.parametrize("check_id", sorted(PROJECTIONS))
def test_every_projection_id_resolves_through_explain(check_id: str) -> None:
    result = _explain(check_id, "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["lifecycle"] == "projection"


def _sample_manifests() -> list[Path]:
    samples = REPO_ROOT / "samples"
    return sorted(
        path
        for path in [*samples.glob("*/shipgate.yaml"), *samples.glob("*/*/shipgate.yaml")]
        if "_anti_patterns" not in path.parts
    )


def _assert_printed_ids_explainable(output_dir: Path) -> set[str]:
    """Every ID the SARIF report and the PR projection print resolves via explain."""

    run = json.loads((output_dir / "report.sarif").read_text("utf-8"))["runs"][0]
    rules = run["tool"]["driver"]["rules"]
    # A policy rule's SARIF id is the reviewer's own policy id; the check that
    # emitted it is in properties.check_id.
    printed = {rule.get("properties", {}).get("check_id") or rule["id"] for rule in rules}
    printed |= {
        result.get("properties", {}).get("check_id") or result["ruleId"]
        for result in run["results"]
    }
    report = json.loads((output_dir / "report.json").read_text("utf-8"))
    verifier_path = output_dir / "verifier.json"
    verifier = json.loads(verifier_path.read_text("utf-8")) if verifier_path.is_file() else None
    printed |= {item.check_id for item in select_pr_items(report, verifier, limit=10_000)}

    unexplained = sorted(check_id for check_id in printed if _explain(check_id).exit_code != 0)
    assert not unexplained, f"explain cannot answer for printed IDs: {unexplained}"
    # Code scanning links every rule, a semantic-gap rule included, to its docs.
    assert not [rule["id"] for rule in rules if not rule.get("helpUri")]
    return printed


@pytest.mark.parametrize(
    "manifest", _sample_manifests(), ids=lambda path: path.parent.relative_to(REPO_ROOT).as_posix()
)
def test_every_id_a_sample_scan_prints_resolves_through_explain(
    tmp_path: Path, manifest: Path
) -> None:
    run_scan(
        config_path=manifest,
        output_dir=tmp_path,
        formats=["json", "sarif"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    _assert_printed_ids_explainable(tmp_path)


@pytest.mark.parametrize(
    "fixture",
    [
        "ai_generated_refund_pr",
        "agent_weakens_gate",
        "governed_edits_governance",
        "prompt_change_rides_release",
    ],
)
def test_every_id_a_verify_fixture_prints_resolves_through_explain(
    tmp_path: Path, fixture: str
) -> None:
    out = tmp_path / "reports"
    result = runner.invoke(app, ["fixture", "run", fixture, "--out", str(out)])
    assert (out / "report.sarif").is_file(), result.output

    printed = _assert_printed_ids_explainable(out)

    if fixture == "ai_generated_refund_pr":
        # Not vacuous: this fixture prints a semantic-gap rule and an
        # acknowledged-override annotation, neither of which is a finding.
        assert printed & SEMANTIC_RULE_IDS
        assert "SHIP-ACTION-EFFECT-OVERRIDE-ACKNOWLEDGED" in printed


def test_a_severity_override_naming_a_projection_id_is_refused() -> None:
    with pytest.raises(ConfigError, match="projection ID"):
        resolve_severity_overrides(
            overrides={"SHIP-CAPABILITY-CHANGE": SeverityOverrideEntry(severity="info")},
            acknowledgements=[],
            catalog=registry.check_catalog(plugins_enabled=False),
        )


def test_an_ignore_entry_naming_a_projection_id_is_a_stale_suppression(tmp_path: Path) -> None:
    sample = REPO_ROOT / "samples" / "clean_read_only_agent"
    work = tmp_path / "sample"
    work.mkdir()
    for path in sample.iterdir():
        if path.is_file():
            (work / path.name).write_bytes(path.read_bytes())
    config = work / "shipgate.yaml"
    manifest = yaml.safe_load(config.read_text("utf-8"))
    manifest.setdefault("checks", {})["ignore"] = [
        {"check_id": "SHIP-CAPABILITY-CHANGE", "reason": "Annotation noise."}
    ]
    config.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    report, _ = run_scan(
        config_path=config, output_dir=tmp_path / "out", formats=["json"], ci_mode="advisory"
    )

    (stale,) = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-MANIFEST-STALE-SUPPRESSION"
        and finding.evidence.get("check_id") == "SHIP-CAPABILITY-CHANGE"
    ]
    assert stale.evidence["issues"] == ["unknown_check_id"]
    assert "projection ID" in stale.title


def test_configurable_ids_exclude_projections_and_keep_deprecated() -> None:
    configurable = registry.configurable_check_ids(registry.CHECK_METADATA)

    assert not configurable & PROJECTIONS
    assert set(DEPRECATED) <= configurable


def test_a_boundary_cell_may_not_publish_a_projection_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A coverage cell's ``raises`` must name a check, not a catalogued label."""

    raised = "SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"  # a real cell's
    catalog = [
        check.model_copy(update={"lifecycle": "projection"}) if check.id == raised else check
        for check in registry.check_catalog(plugins_enabled=False)
    ]
    monkeypatch.setattr(registry, "check_catalog", lambda **_kwargs: catalog)

    with pytest.raises(BoundaryCoverageError, match=f"{raised}.*no registered check owns"):
        build_boundary_matrix()


@pytest.mark.parametrize("lifecycle", ["deprecated", "projection"])
def test_a_plugin_cannot_declare_a_non_active_lifecycle(lifecycle: str) -> None:
    def plugin(context):
        return []

    plugin.AGENTS_SHIPGATE_METADATA = {
        "id": "ACME-CHECK",
        "category": "custom",
        "default_severity": "low",
        "description": "Claims a non-active lifecycle.",
        "lifecycle": lifecycle,
    }

    class EntryPoint:
        name = "acme"
        value = "acme:run"
        dist = None

        @staticmethod
        def load():
            return plugin

    record = validate_entry_point(
        EntryPoint(), builtin_ids=set(), already_registered_plugin_ids=set()
    )

    assert record.info["validation_status"] == BAD_METADATA
    assert record.metadata is None
