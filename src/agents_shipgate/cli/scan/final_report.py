from __future__ import annotations

from typing import Any

from agents_shipgate.core.findings.report_builder import build_report
from agents_shipgate.core.findings.reviewer_summary import build_reviewer_summary
from agents_shipgate.core.findings.verifier_blocks import (
    build_capability_change,
    build_human_ack,
    build_protected_surface_changes,
    build_verifier_summary,
)
from agents_shipgate.core.lenses.capability_intent import apply_capability_diff
from agents_shipgate.core.lenses.effective_policy import (
    accepted_debt_fingerprints,
    build_effective_policy_snapshot,
)
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import ReadinessReport

from .models import _OutputPlan, _SanitizedSurfaces
from .run_identity import _run_id


def _build_final_report(
    *,
    manifest: AgentsShipgateManifest,
    sanitized: _SanitizedSurfaces,
    plan: _OutputPlan,
) -> tuple[ReadinessReport, Any]:
    """Phase 8: hash the run_id, build the ``ReadinessReport`` from the
    fully sanitized surfaces, run capability-diff enrichment, and
    project the JSON payload that packet building consumes.

    The ``_run_id`` inputs are exactly what they were pre-decomp —
    STABILITY contract requires byte-identical hashes for the same
    workspace.
    """
    report = build_report(
        run_id=_run_id(
            manifest,
            sanitized.tools,
            sanitized.findings,
            project=sanitized.project,
            agent_name=sanitized.agent.name,
            environment=sanitized.environment,
            api_surface=sanitized.api_surface,
            anthropic_surface=sanitized.anthropic_surface,
            frameworks=sanitized.frameworks_surface,
            codex_plugin_surface=sanitized.codex_plugin_surface,
            action_surface_facts=sanitized.action_surface_facts,
        ),
        manifest=sanitized.manifest,
        project=sanitized.project,
        manifest_dir=sanitized.manifest_dir,
        agent=sanitized.agent.model_dump(exclude_none=True),
        environment=sanitized.environment,
        tools=sanitized.tools,
        findings=sanitized.findings,
        generated_reports=plan.generated_report_refs,
        ci_mode=sanitized.manifest.ci.mode,
        fail_on=sanitized.manifest.ci.fail_on,
        new_findings_only=sanitized.baseline_summary is not None,
        loaded_policy_packs=sanitized.loaded_policy_packs,
        loaded_plugins=sanitized.loaded_plugins,
        loaded_adapters=sanitized.loaded_adapters,
        source_warnings=sanitized.source_warnings,
        api_surface=sanitized.api_surface,
        anthropic_surface=sanitized.anthropic_surface,
        frameworks=sanitized.frameworks_surface,
        codex_plugin_surface=sanitized.codex_plugin_surface,
        baseline=sanitized.baseline_summary,
        tool_surface_facts=sanitized.tool_surface_facts,
        tool_surface_diff=sanitized.tool_surface_diff,
        action_surface_facts=sanitized.action_surface_facts,
        action_surface_diff=sanitized.action_surface_diff,
        # v0.17 (M1): top-of-report policy audit. Always emitted (may
        # be an empty envelope) so consumers can rely on the field
        # existing in v0.17 reports.
        policy_audit=sanitized.policy_audit,
        privacy_audit=sanitized.privacy_audit,
        heuristics_filter=sanitized.heuristics_filter,
    )
    apply_capability_diff(report, sanitized.tools)
    # v0.20: reviewer_summary is built HERE — after apply_capability_diff
    # has populated misalignments / release_consequence / suggested_scenarios.
    # Building it inside build_report() would project from incomplete state
    # (capability_misalignments would always be 0). Pure projection, no I/O.
    report.reviewer_summary = build_reviewer_summary(
        findings=sanitized.findings,
        report=report,
    )
    # v0.22 (verifier cycle, P2/M3): build the new top-level verifier
    # blocks as deterministic projections. They are reviewer-facing inputs
    # and summaries only; release_decision.decision remains the sole gate.
    # Order matters: the capability delta, protected surfaces, effective
    # policy, and human-ack blocks are built first, then verifier_summary is
    # composed last so it can mirror release_decision.decision and the
    # human_ack flags.
    report.capability_change = build_capability_change(report)
    report.protected_surface_changes = build_protected_surface_changes(report)
    report.effective_policy = build_effective_policy_snapshot(
        sanitized.manifest,
        baseline_fingerprints=accepted_debt_fingerprints(report.findings),
    )
    report.human_ack = build_human_ack(report, sanitized.manifest)
    report.verifier_summary = build_verifier_summary(report)
    public_report_payload = report_json_payload(report)
    return report, public_report_payload
