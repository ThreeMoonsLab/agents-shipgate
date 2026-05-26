from __future__ import annotations

import logging
from pathlib import Path

from agents_shipgate.checks.baseline_integrity import build_findings as build_integrity_findings
from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    OpenAIApiArtifacts,
    ValidationArtifacts,
)
from agents_shipgate.core.baseline import (
    apply_baseline,
    baseline_resolved_fingerprints,
    verify_baseline,
)
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.findings.identity import assign_finding_ids
from agents_shipgate.core.findings.remediation import annotate_remediation
from agents_shipgate.core.privacy import (
    build_privacy_audit,
    redact_data,
    sanitize_findings,
    sanitize_model,
    sanitize_tools,
)
from agents_shipgate.report.action_surface_diff import (
    action_reference_from_scan_reference,
    attach_action_surface_finding_summary,
    compute_action_surface_diff,
    enrich_action_surface_diff_with_source,
)
from agents_shipgate.report.tool_surface_diff import (
    build_tool_surface_facts,
    compute_tool_surface_diff,
    disabled_tool_surface_diff,
    enrich_tool_surface_diff_with_source,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import BaselineSummary, LoadedPolicyPack, PolicyAudit
from agents_shipgate.schemas.surfaces import ToolSurfaceFacts

from .models import (
    _ChecksDecision,
    _DiffReferences,
    _LoadedInputs,
    _OutputPlan,
    _SanitizedSurfaces,
    _ToolsAndAgent,
)
from .patching import _check_metadata_lookup
from .path_helpers import _resolve_audit_log_path
from .source_loading import _tool_source_index
from .surface_redaction import (
    _build_public_action_surface_facts,
    _frameworks_surface,
    _sanitize_codex_plugin_surface,
    _sanitize_diff_reference,
)

logger = logging.getLogger(__name__)


def _sanitize_for_output(
    *,
    manifest: AgentsShipgateManifest,
    config_path: Path,
    baseline_path: Path | None,
    inputs: _LoadedInputs,
    tools_and_agent: _ToolsAndAgent,
    diffs: _DiffReferences,
    decision: _ChecksDecision,
    plan: _OutputPlan,
    plugins_enabled: bool | None,
) -> _SanitizedSurfaces:
    """Phase 7: privacy redaction of every value that flows into a
    report or packet — STABILITY contract: runs BEFORE any file is
    written. Also: assign public finding IDs (redacted-evidence
    fingerprints), apply baseline (with legacy-fingerprint
    compatibility), run baseline-integrity checks, build public
    tool/action surface facts + diffs (enriched with provenance from
    the *public* tool source index, never from the raw one), build the
    final privacy audit envelope.

    Returns a single ``_SanitizedSurfaces`` bundle. Nothing in later
    phases re-redacts, and ``build_report`` / ``build_packet`` see only
    these values.
    """
    privacy_stats = plan.privacy_stats

    public_manifest = sanitize_model(
        manifest, AgentsShipgateManifest, stats=privacy_stats, path="manifest"
    )
    public_manifest_dir = redact_data(
        str(config_path.resolve().parent),
        stats=privacy_stats,
        path="manifest_dir",
    )
    public_api_artifacts = (
        sanitize_model(
            inputs.api,
            OpenAIApiArtifacts,
            stats=privacy_stats,
            path="api_artifacts",
        )
        if inputs.api
        else None
    )
    public_anthropic_artifacts = (
        sanitize_model(
            inputs.anthropic,
            AnthropicArtifacts,
            stats=privacy_stats,
            path="anthropic_artifacts",
        )
        if inputs.anthropic
        else None
    )
    public_validation_artifacts = (
        sanitize_model(
            inputs.validation,
            ValidationArtifacts,
            stats=privacy_stats,
            path="validation_artifacts",
        )
        if inputs.validation
        else None
    )
    public_tools = sanitize_tools(tools_and_agent.tools, stats=privacy_stats)
    public_findings = sanitize_findings(decision.findings, stats=privacy_stats)
    assign_finding_ids(public_findings)

    public_project = redact_data(
        public_manifest.project.model_dump(exclude_none=True),
        stats=privacy_stats,
        path="project",
    )
    public_agent = sanitize_model(
        tools_and_agent.agent, Agent, stats=privacy_stats, path="agent"
    )
    public_environment = redact_data(
        public_manifest.environment.model_dump(exclude_none=True),
        stats=privacy_stats,
        path="environment",
    )
    public_source_warnings = redact_data(
        tools_and_agent.warnings,
        stats=privacy_stats,
        path="source_warnings[]",
    )
    public_api_surface = redact_data(
        public_api_artifacts.surface_summary() if public_api_artifacts else None,
        stats=privacy_stats,
        path="api_surface",
    )
    public_anthropic_surface = redact_data(
        public_anthropic_artifacts.surface_summary()
        if public_anthropic_artifacts
        else None,
        stats=privacy_stats,
        path="anthropic_surface",
    )
    public_frameworks_surface = redact_data(
        _frameworks_surface(
            inputs.adk,
            inputs.langchain,
            inputs.crewai,
            inputs.n8n,
        ),
        stats=privacy_stats,
        path="frameworks",
    )
    public_codex_plugin_surface = _sanitize_codex_plugin_surface(
        inputs.codex_plugin.surface_summary() if inputs.codex_plugin else None,
        stats=privacy_stats,
    )
    public_policy_audit = sanitize_model(
        decision.override_resolution.audit,
        PolicyAudit,
        stats=privacy_stats,
        path="policy_audit",
    )
    public_loaded_policy_packs = [
        sanitize_model(
            pack,
            LoadedPolicyPack,
            stats=privacy_stats,
            path="loaded_policy_packs[]",
        )
        for pack in inputs.policy_packs.loaded
    ]
    public_loaded_plugins = redact_data(
        decision.loaded_plugins,
        stats=privacy_stats,
        path="loaded_plugins[]",
    )
    # v0.20: third-party adapter provenance. Same redaction shape as
    # loaded_plugins[] — entry-point ``value`` strings and distribution
    # metadata are first-party and don't carry secrets, but the audit
    # envelope flows through redact_data for forward-compat with future
    # adapter-emitted fields.
    public_loaded_adapters = redact_data(
        inputs.loaded_adapters,
        stats=privacy_stats,
        path="loaded_adapters[]",
    )

    (
        public_diff_reference,
        public_action_surface_facts,
        public_action_surface_diff,
    ) = _public_action_surfaces(
        public_manifest=public_manifest,
        public_agent_id=public_agent.id,
        public_tools=public_tools,
        diffs=diffs,
        decision=decision,
        privacy_stats=privacy_stats,
    )

    baseline_summary = None
    if diffs.baseline_file and diffs.baseline_display_path:
        baseline_summary = apply_baseline(
            public_findings,
            diffs.baseline_file,
            display_path=diffs.baseline_display_path,
            legacy_fingerprints=decision.legacy_fingerprints,
        )
        baseline_summary = sanitize_model(
            baseline_summary,
            BaselineSummary,
            stats=privacy_stats,
            path="baseline",
        )
        _append_baseline_integrity_findings(
            manifest=manifest,
            baseline_path=baseline_path,
            baseline_file=diffs.baseline_file,
            decision=decision,
            public_findings=public_findings,
            public_source_warnings=public_source_warnings,
            privacy_stats=privacy_stats,
            plugins_enabled=plugins_enabled,
        )
    attach_action_surface_finding_summary(public_action_surface_diff, public_findings)

    public_tool_surface_facts, public_tool_surface_diff = _public_tool_surfaces(
        public_manifest=public_manifest,
        public_tools=public_tools,
        public_findings=public_findings,
        public_api_artifacts=public_api_artifacts,
        public_anthropic_artifacts=public_anthropic_artifacts,
        public_diff_reference=public_diff_reference,
        diffs=diffs,
        privacy_stats=privacy_stats,
    )
    privacy_audit = build_privacy_audit(
        privacy_stats,
        output_surfaces=plan.output_surfaces,
        notes=[
            "Default-on best-effort pattern/key redaction ran before public artifacts were written.",
            "Redaction audit paths contain counts and secret kinds only; raw values and raw hashes are not emitted.",
            *(
                [
                    "Baseline matching accepts legacy pre-v0.18 raw secret fingerprints for compatibility; re-save reviewed baselines to migrate to redacted public fingerprints."
                ]
                if diffs.baseline_file and privacy_stats.occurrence_count
                else []
            ),
        ],
    )
    return _SanitizedSurfaces(
        manifest=public_manifest,
        manifest_dir=public_manifest_dir,
        project=public_project,
        environment=public_environment,
        agent=public_agent,
        tools=public_tools,
        findings=public_findings,
        source_warnings=public_source_warnings,
        api_artifacts=public_api_artifacts,
        anthropic_artifacts=public_anthropic_artifacts,
        validation_artifacts=public_validation_artifacts,
        api_surface=public_api_surface,
        anthropic_surface=public_anthropic_surface,
        frameworks_surface=public_frameworks_surface,
        codex_plugin_surface=public_codex_plugin_surface,
        policy_audit=public_policy_audit,
        loaded_policy_packs=public_loaded_policy_packs,
        loaded_plugins=public_loaded_plugins,
        loaded_adapters=public_loaded_adapters,
        diff_reference=public_diff_reference,
        action_surface_facts=public_action_surface_facts,
        action_surface_diff=public_action_surface_diff,
        tool_surface_facts=public_tool_surface_facts,
        tool_surface_diff=public_tool_surface_diff,
        baseline_summary=baseline_summary,
        privacy_audit=privacy_audit,
        heuristics_filter=decision.heuristics_filter,
    )


def _public_action_surfaces(
    *,
    public_manifest: AgentsShipgateManifest,
    public_agent_id: str,
    public_tools: list,
    diffs: _DiffReferences,
    decision: _ChecksDecision,
    privacy_stats,
):
    public_diff_reference = _sanitize_diff_reference(
        diffs.diff_reference,
        stats=privacy_stats,
    )
    public_action_surface_facts = _build_public_action_surface_facts(
        raw_facts=decision.action_surface_facts,
        manifest=public_manifest,
        agent_id=public_agent_id,
        tools=public_tools,
        stats=privacy_stats,
    )
    public_action_reference = action_reference_from_scan_reference(public_diff_reference)
    public_action_surface_diff = compute_action_surface_diff(
        public_action_surface_facts,
        public_action_reference.facts if public_action_reference else None,
        reference=public_action_reference,
    )
    if diffs.diff_reference_error:
        public_action_surface_diff.enabled = False
        public_action_surface_diff.notes = redact_data(
            [diffs.diff_reference_error],
            stats=privacy_stats,
            path="action_surface_diff.notes",
        )
    # v0.19 reviewer-grade provenance: enrich the PUBLIC action-surface
    # diff rows from ``public_tools`` (already sanitized) so the
    # rendered ``report.json`` and packet §3B carry tool source
    # citations on every reason field.
    enrich_action_surface_diff_with_source(
        public_action_surface_diff, _tool_source_index(public_tools)
    )
    return (
        public_diff_reference,
        public_action_surface_facts,
        public_action_surface_diff,
    )


def _public_tool_surfaces(
    *,
    public_manifest: AgentsShipgateManifest,
    public_tools: list,
    public_findings: list,
    public_api_artifacts: OpenAIApiArtifacts | None,
    public_anthropic_artifacts: AnthropicArtifacts | None,
    public_diff_reference,
    diffs: _DiffReferences,
    privacy_stats,
):
    public_tool_surface_facts = sanitize_model(
        build_tool_surface_facts(
            public_manifest,
            public_tools,
            public_findings,
            public_api_artifacts,
            public_anthropic_artifacts,
        ),
        ToolSurfaceFacts,
        stats=privacy_stats,
        path="tool_surface_facts",
    )
    if diffs.diff_reference_error:
        public_tool_surface_diff = disabled_tool_surface_diff(
            redact_data(
                diffs.diff_reference_error,
                stats=privacy_stats,
                path="tool_surface_diff.notes",
            )
        )
    else:
        public_tool_surface_diff = compute_tool_surface_diff(
            public_tool_surface_facts,
            public_diff_reference.facts if public_diff_reference else None,
            public_findings,
            reference=public_diff_reference,
        )
    # v0.19 reviewer-grade provenance: enrich tool-surface diff
    # controls (and any other reason-bearing rows) with the public
    # tool path:line citation so the rendered report.json and packet
    # §3A carry source info on every change-row reason.
    enrich_tool_surface_diff_with_source(
        public_tool_surface_diff, _tool_source_index(public_tools)
    )
    return public_tool_surface_facts, public_tool_surface_diff


def _append_baseline_integrity_findings(
    *,
    manifest: AgentsShipgateManifest,
    baseline_path: Path | None,
    baseline_file,
    decision: _ChecksDecision,
    public_findings: list,
    public_source_warnings: list[str],
    privacy_stats,
    plugins_enabled: bool | None,
) -> None:
    """Append public baseline-integrity findings after baseline matching.

    Runs after public finding fingerprints are assigned so integrity output
    does not depend on raw secret-bearing finding IDs. Mutates
    ``public_findings`` and ``public_source_warnings`` in place, matching the
    original Phase 7 ordering.
    """
    integrity_mode = manifest.baseline.integrity_mode
    if integrity_mode == "off" or baseline_path is None:
        return

    audit_log_path = _resolve_audit_log_path(manifest, baseline_path)
    try:
        static_issues = verify_baseline(baseline_path, audit_log_path)
    except InputParseError as exc:
        logger.warning(
            "baseline integrity verification failed",
            extra={
                "agents_shipgate_baseline_path": str(baseline_path),
                "agents_shipgate_error": str(exc),
            },
        )
        static_issues = []
        warning = f"Baseline integrity check skipped: {exc}"
        public_source_warnings.append(
            redact_data(
                warning,
                stats=privacy_stats,
                path="source_warnings[]",
            )
        )
    stale_issues = baseline_resolved_fingerprints(
        public_findings,
        baseline_file,
        legacy_fingerprints=decision.legacy_fingerprints,
    )
    baseline_privacy_hint = None
    if stale_issues and privacy_stats.occurrence_count:
        baseline_privacy_hint = (
            "If these stale baseline entries appeared immediately after "
            "upgrading to report schema v0.18, review and regenerate the "
            "baseline. Secret-bearing public fingerprints are now computed "
            "from redacted evidence."
        )
        for issue in stale_issues:
            issue.evidence["v0_18_privacy_migration_hint"] = baseline_privacy_hint
    integrity_findings = build_integrity_findings(
        static_issues + stale_issues,
        context=decision.context,
        integrity_mode=integrity_mode,
    )
    if baseline_privacy_hint:
        for finding in integrity_findings:
            if finding.check_id == "SHIP-BASELINE-ENTRY-STALE":
                finding.recommendation = f"{finding.recommendation} {baseline_privacy_hint}"
    if not integrity_findings:
        return

    public_findings.extend(sanitize_findings(integrity_findings, stats=privacy_stats))
    assign_finding_ids(public_findings)
    annotate_remediation(
        public_findings,
        _check_metadata_lookup(plugins_enabled=plugins_enabled),
    )
