"""Latency budget + structural regression tests for the large sample.

This sample (``samples/large_multi_framework_agent``) ships ~65 tools across
six tool sources to exercise the pipeline at realistic scale:

* OpenAPI × 2 (payments + fulfillment),
* MCP × 2 (CRM + internal warehouse),
* OpenAI Agents SDK × 1 plus its reviewed MCP-shaped inventory × 1.

Two concerns motivate the tests in this file:

1. **Latency.** The release gate lives on the CI critical path. The full
   pipeline (loaders → checks → release decision → markdown/json/packet
   render → privacy redaction → file writes) must stay fast even with a
   real-shape input set. A regression that pushes a single scan past a few
   seconds will be felt by every adopting team.

2. **Structural shape.** Without a committed golden md/json (see the
   sample README for why), we still want a tripwire that catches a
   regression which silently changes the release decision, drops half the
   findings, or stops emitting key rule families. These assertions are
   intentionally loose bands — they pin behavior, not exact counts.

Budget rationale: a typical scan on this sample completes in 1–3 seconds on
a 2024 laptop. The budget is set to **10.0 seconds** to absorb CI variance
(GitHub-hosted runners, parallel-test contention, cold filesystem caches)
without false-failing. If the typical time exceeds half the budget, the
sample has grown or the pipeline has regressed — investigate before
loosening the budget.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.schemas.report import ReadinessReport

SAMPLE_DIR = Path("samples/large_multi_framework_agent")
SAMPLE_MANIFEST = SAMPLE_DIR / "shipgate.yaml"

# Latency budget for the full scan pipeline on this sample. See module
# docstring for the rationale. If this number ever needs to grow, please
# document WHY in the PR (new check family, new lens, intentional adapter
# work) rather than bumping it silently.
LATENCY_BUDGET_SECONDS = 10.0

# Structural bands. Loose enough to tolerate realistic finding-set evolution
# (a new check landing, a slightly tightened risk classifier) without
# false-failing, tight enough to catch a regression that drops half the
# pipeline.
MIN_LOADED_TOOLS = 50
MAX_LOADED_TOOLS = 100
MIN_TOTAL_FINDINGS = 40
MAX_TOTAL_FINDINGS = 200
MIN_CRITICAL_FINDINGS = 3   # at minimum, the financial-action approval gaps
MIN_BLOCKERS = 3
MIN_REVIEW_ITEMS = 20
# Six tool sources are declared; the reviewed inventory intentionally outranks
# the five duplicate SDK tools while the merge receipts prove the AST adapter
# still contributed metadata.
EXPECTED_SOURCE_COUNT = 6


@pytest.fixture(scope="module")
def scanned_sample(tmp_path_factory: pytest.TempPathFactory) -> ReadinessReport:
    """Run the large sample once per module and reuse the report.

    Running per-test would multiply the latency cost without adding signal;
    the latency test still runs the scan a second time so it measures a
    cold-cache pass rather than a fixture-cached one.
    """
    out_dir = tmp_path_factory.mktemp("large_sample_module")
    report, _ = run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=out_dir,
        formats=["markdown", "json"],
        ci_mode="advisory",
        # Packet rendering adds md/json/html generation cost; keep it on so
        # structural assertions cover the full default output set. The
        # latency test below also runs with the packet enabled.
        packet_enabled=True,
    )
    return report


# --- latency ---------------------------------------------------------------


def test_scan_completes_within_latency_budget(tmp_path: Path) -> None:
    """Scan must finish under :data:`LATENCY_BUDGET_SECONDS`.

    Runs an independent scan (not the module-scoped fixture) so the timing
    measurement isn't biased by warmed import caches. Uses wall-clock time
    (``time.perf_counter``) because that's what an adopter's CI run sees.
    """
    start = time.perf_counter()
    run_scan(
        config_path=SAMPLE_MANIFEST,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=True,
    )
    elapsed = time.perf_counter() - start
    assert elapsed < LATENCY_BUDGET_SECONDS, (
        f"Large-sample scan took {elapsed:.2f}s, exceeding budget of "
        f"{LATENCY_BUDGET_SECONDS:.1f}s. Investigate before bumping the budget: "
        "a new check family, a new lens, or an adapter regression are the usual "
        "causes. See tests/test_large_sample.py docstring for context."
    )


# --- structural shape ------------------------------------------------------


def test_all_six_tool_sources_load(scanned_sample: ReadinessReport) -> None:
    """Every declared source contributes tools or deterministic merge receipts.

    Catches the "n8n/codex_plugin/whatever adapter accidentally lost its
    artifact wiring" class of regression at the loader level. Matches on
    ``source_ref`` prefix because the SDK adapter doesn't populate
    ``source_path`` (it carries the file in ``source_ref``); per-adapter
    inventory-shape differences are normal and not a regression by
    themselves.
    """
    # Collapse OpenAPI refs (``specs/x.yaml#/paths/...``) to their file-path
    # prefix. The reviewed inventory outranks duplicate SDK facts in the final
    # inventory, so the SDK contribution is asserted through merge receipts.
    seen_files: set[str] = set()
    for entry in scanned_sample.tool_inventory:
        ref = entry.get("source_ref") or ""
        if "#" in ref:
            ref = ref.split("#", 1)[0]
        if ref:
            seen_files.add(ref)
    expected = {
        "specs/payments.openapi.yaml",
        "specs/fulfillment.openapi.yaml",
        "mcp/crm-tools.json",
        "mcp/internal-tools.json",
        "inventories/ops-sdk-tools.json",
    }
    assert expected.issubset(seen_files), (
        f"Expected all inventory-owning sources to contribute tools; missing: "
        f"{sorted(expected - seen_files)}. Found: {sorted(seen_files)}."
    )

    # Also pin the per-adapter cohort: at least one tool from each loader
    # surface type. This catches a different regression — a source still
    # loading but emitting tools with the wrong source_type tag.
    source_types = {entry.get("source_type") for entry in scanned_sample.tool_inventory}
    assert {"openapi", "mcp"}.issubset(source_types), (
        f"Expected source_types to include openapi + mcp; "
        f"got {sorted(source_types)}."
    )
    sdk_bindings = [
        row
        for row in scanned_sample.tool_inventory
        if row.get("provider") == "ops_sdk"
        and len(row.get("observation_ids", [])) == 2
    ]
    assert len(sdk_bindings) == 5


def test_tool_inventory_is_in_expected_band(scanned_sample: ReadinessReport) -> None:
    n = len(scanned_sample.tool_inventory)
    assert MIN_LOADED_TOOLS <= n <= MAX_LOADED_TOOLS, (
        f"Expected {MIN_LOADED_TOOLS}–{MAX_LOADED_TOOLS} loaded tools; got "
        f"{n}. A change outside that band signals either a sample edit or an "
        "adapter regression."
    )


def test_findings_count_is_in_expected_band(scanned_sample: ReadinessReport) -> None:
    n = len(scanned_sample.findings)
    assert MIN_TOTAL_FINDINGS <= n <= MAX_TOTAL_FINDINGS, (
        f"Expected {MIN_TOTAL_FINDINGS}–{MAX_TOTAL_FINDINGS} findings; got "
        f"{n}. A change outside that band suggests a check family was added, "
        "dropped, or its trigger condition shifted."
    )


def test_release_decision_is_blocked(scanned_sample: ReadinessReport) -> None:
    """The sample is deliberately authored with critical approval gaps."""
    decision = scanned_sample.release_decision
    assert decision is not None
    assert decision.decision == "blocked", (
        f"Expected decision='blocked'; got {decision.decision!r}. If the "
        "sample was edited to be cleaner, update this test and document the "
        "intent."
    )
    assert len(decision.blockers) >= MIN_BLOCKERS
    assert len(decision.review_items) >= MIN_REVIEW_ITEMS


def test_at_least_one_critical_approval_gap_fires(
    scanned_sample: ReadinessReport,
) -> None:
    """Anchor the headline check we authored the sample around."""
    critical_approval_findings = [
        f
        for f in scanned_sample.findings
        if f.severity == "critical"
        and f.check_id == "SHIP-POLICY-APPROVAL-MISSING"
        and not f.suppressed
    ]
    assert len(critical_approval_findings) >= MIN_CRITICAL_FINDINGS, (
        "Expected the financial-action tools without declared approval to "
        "fire SHIP-POLICY-APPROVAL-MISSING at critical severity. Got "
        f"{len(critical_approval_findings)} critical approval findings."
    )


def test_scope_coverage_check_fires(scanned_sample: ReadinessReport) -> None:
    """The manifest deliberately omits several admin scopes."""
    scope_findings = [
        f
        for f in scanned_sample.findings
        if f.check_id == "SHIP-AUTH-SCOPE-COVERAGE-MISSING" and not f.suppressed
    ]
    assert scope_findings, (
        "Expected SHIP-AUTH-SCOPE-COVERAGE-MISSING to fire for the admin "
        "scopes the manifest omits."
    )


def test_policy_audit_records_severity_override(
    scanned_sample: ReadinessReport,
) -> None:
    """The manifest declares one severity override (SHIP-DOC-INJECTION-RISK)."""
    audit = scanned_sample.policy_audit
    assert audit is not None
    overrides = audit.severity_overrides_applied
    assert any(
        entry.check_id == "SHIP-DOC-INJECTION-RISK" for entry in overrides
    ), (
        "Expected the declared severity_override for SHIP-DOC-INJECTION-RISK "
        "to appear in policy_audit.severity_overrides_applied."
    )


def test_contribution_rules_are_exhaustive_over_findings(
    scanned_sample: ReadinessReport,
) -> None:
    """Every finding produces exactly one contribution rule.

    This is a structural invariant of the release-decision engine and a
    regression here would silently break the audit envelope. Worth pinning
    on a real-scale sample so the assertion runs against ~80+ findings, not
    just the small samples' handful.
    """
    decision = scanned_sample.release_decision
    assert decision is not None
    assert len(decision.contribution_rules) == len(scanned_sample.findings), (
        f"Expected exactly one contribution rule per finding "
        f"({len(scanned_sample.findings)}); got "
        f"{len(decision.contribution_rules)}."
    )


def test_privacy_audit_envelope_is_emitted(scanned_sample: ReadinessReport) -> None:
    """Default-on redaction must emit the privacy audit envelope."""
    audit = scanned_sample.privacy_audit
    assert audit is not None
    assert audit.enabled is True
    assert audit.rules_version  # non-empty
    assert audit.sensitive_field_inventory_version  # non-empty


def test_reviewer_summary_block_is_populated(
    scanned_sample: ReadinessReport,
) -> None:
    """The v0.20 reviewer summary is a deterministic projection; it must
    appear on every emitted scan."""
    summary = scanned_sample.reviewer_summary
    assert summary is not None
    assert summary.verdict == "blocked"
    assert summary.headline  # non-empty


def test_heuristics_filter_envelope_is_emitted(
    scanned_sample: ReadinessReport,
) -> None:
    """The v0.21 heuristics filter envelope is always present.

    ``enabled`` is False because we didn't pass ``--no-heuristics``; the
    envelope shape is still pinned.
    """
    hf = scanned_sample.heuristics_filter
    assert hf is not None
    assert hf.enabled is False
    assert hf.filtered_finding_count == 0
