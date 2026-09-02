from __future__ import annotations

import logging
from pathlib import Path

from agents_shipgate.checks.baseline_integrity import build_findings as build_integrity_findings
from agents_shipgate.checks.verify_baseline_waiver import baseline_expansion_findings
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
from agents_shipgate.core.domain import Agent, SourceSurfaceOmission
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.findings.identity import assign_finding_ids
from agents_shipgate.core.findings.remediation import annotate_remediation
from agents_shipgate.core.lenses.action_surface import (
    action_reference_from_scan_reference,
    attach_action_surface_finding_summary,
    compute_action_surface_diff,
    enrich_action_surface_diff_with_source,
)
from agents_shipgate.core.lenses.declaration_surface import (
    build_action_declaration_facts,
    build_public_action_declaration_facts,
)
from agents_shipgate.core.lenses.effective_policy import accepted_debt_fingerprints
from agents_shipgate.core.lenses.tool_surface import (
    build_tool_surface_facts,
    compute_tool_surface_diff,
    disabled_tool_surface_diff,
    enrich_tool_surface_diff_with_source,
)
from agents_shipgate.core.privacy import (
    build_privacy_audit,
    redact_data,
    sanitize_findings,
    sanitize_model,
    sanitize_tools,
)
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, BindingSurfaceDiff
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import (
    BaselineSummary,
    CapabilityRuntimeEvidence,
    EvidenceGap,
    LoadedPolicyPack,
    PolicyAudit,
)
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
    configured_manifest_path = (
        decision.context.verification.configured_manifest_path
        if decision.context.verification is not None
        else None
    )
    public_manifest_path = redact_data(
        configured_manifest_path or config_path.name,
        stats=privacy_stats,
        path="manifest_path",
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
    public_tool_catalog = sanitize_tools(tools_and_agent.tool_catalog, stats=privacy_stats)
    # Validate and resolve declaration rows against the raw, one-to-one
    # manifest/tool surface first. Redaction is many-to-one and cannot safely
    # be treated as a second manifest: two valid secret-bearing selectors may
    # deliberately collapse to the same public marker.
    build_action_declaration_facts(
        manifest,
        tools_and_agent.tools,
        manifest_path=configured_manifest_path or config_path.name,
    )
    public_action_declaration_facts = build_public_action_declaration_facts(
        public_manifest,
        public_tools,
        manifest_path=public_manifest_path,
        indeterminate_override_positions=_indeterminate_override_positions(
            manifest,
            public_manifest,
        ),
    )
    public_binding_graph = sanitize_model(
        tools_and_agent.binding_graph,
        AgentBindingGraphAssessment,
        stats=privacy_stats,
        path="binding_surface_facts",
    )
    base_binding = diffs.diff_reference.binding_facts if diffs.diff_reference else None
    verification = decision.context.verification
    # Asked for, by any route: a reference loaded cleanly, a reference was
    # supplied and failed to parse, or verify resolved a base ref and could not
    # produce a report for it. The middle case is the one that was missing —
    # reading only the *successfully loaded* reference meant a malformed
    # `--diff-from` reported "no comparison requested" and went on to assert
    # that an unbound destructive tool predated the change (PR #404 review 2).
    # Whether the bytes parsed is not the same question as whether the caller
    # asked.
    base_comparison_requested = (
        diffs.diff_reference is not None
        or diffs.diff_reference_error is not None
        or bool(verification is not None and verification.base_comparison_unavailable)
    )
    if base_binding is None:
        public_binding_diff = BindingSurfaceDiff(
            enabled=False,
            base_comparison_requested=base_comparison_requested,
            base_report_schema_version=(
                diffs.diff_reference.report_schema_version if diffs.diff_reference else None
            ),
            notes=[
                "Binding diff requires a report_schema_version 0.31 base report."
            ] if diffs.diff_reference is not None else [],
        )
    else:
        current_handoffs = {
            f"{edge.source_agent_id}->{edge.target_agent_id}:{edge.edge_type}"
            for edge in public_binding_graph.handoff_edges
        }
        base_handoffs = {
            f"{edge.source_agent_id}->{edge.target_agent_id}:{edge.edge_type}"
            for edge in base_binding.handoff_edges
        }
        public_binding_diff = BindingSurfaceDiff(
            enabled=True,
            base_comparison_requested=True,
            base_report_schema_version=diffs.diff_reference.report_schema_version,
            added_reachable_tool_ids=sorted(
                set(public_binding_graph.reachable_tool_ids)
                - set(base_binding.reachable_tool_ids)
            ),
            removed_reachable_tool_ids=sorted(
                set(base_binding.reachable_tool_ids)
                - set(public_binding_graph.reachable_tool_ids)
            ),
            # Head-minus-base on the *excluded* partition, so both ways a
            # subject can newly leave the analysed surface are caught: added
            # to a source and left unwired, or wired at base and unwired here.
            # Subtracting the base exclusions is what keeps a long-standing
            # unwired catalog from re-reporting itself on every PR.
            added_unbound_tool_ids=sorted(
                set(public_binding_graph.unbound_tool_ids)
                - set(base_binding.unbound_tool_ids)
            ),
            added_handoffs=sorted(current_handoffs - base_handoffs),
            removed_handoffs=sorted(base_handoffs - current_handoffs),
        )
    public_findings = sanitize_findings(decision.findings, stats=privacy_stats)
    assign_finding_ids(public_findings)
    public_capability_runtime_evidence = sanitize_model(
        decision.context.capability_runtime_evidence,
        CapabilityRuntimeEvidence,
        stats=privacy_stats,
        path="capability_runtime_evidence",
    )
    public_policy_evidence_gaps = [
        sanitize_model(
            gap,
            EvidenceGap,
            stats=privacy_stats,
            path="policy_evidence_gaps[]",
        )
        for gap in decision.context.policy_evidence_gaps
    ]

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
    # Fail-soft action-surface warnings (e.g. OpenAPI action_id collisions)
    # are raised in Phase 5, after the Phase 3 source-warning list is
    # assembled. Append them last so byte-stable output is preserved for
    # the common (no-collision) case where this list is empty.
    public_source_warnings.extend(
        redact_data(
            decision.action_surface_warnings,
            stats=privacy_stats,
            path="source_warnings[]",
        )
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
            inputs.conductor,
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
        public_base_action_surface_facts,
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
        _append_baseline_expansion_findings(
            diffs=diffs,
            decision=decision,
            public_findings=public_findings,
            privacy_stats=privacy_stats,
            plugins_enabled=plugins_enabled,
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
        toolkit_bounds=decision.context.toolkit_bounds,
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
        manifest_path=public_manifest_path,
        project=public_project,
        environment=public_environment,
        agent=public_agent,
        tools=public_tools,
        tool_catalog=public_tool_catalog,
        binding_graph=public_binding_graph,
        binding_surface_diff=public_binding_diff,
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
        base_comparison_requested=bool(
            public_diff_reference is not None
            or (
                decision.context.verification is not None
                and decision.context.verification.base_comparison_unavailable
            )
        ),
        manifest_introduced=bool(
            decision.context.verification is not None
            and decision.context.verification.manifest_introduced
        ),
        configured_gate_introduced=bool(
            decision.context.verification is not None
            and decision.context.verification.configured_gate_introduced
        ),
        action_declaration_facts=public_action_declaration_facts,
        base_action_surface_facts=public_base_action_surface_facts,
        action_surface_facts=public_action_surface_facts,
        action_surface_diff=public_action_surface_diff,
        capability_runtime_evidence=public_capability_runtime_evidence,
        tool_surface_facts=public_tool_surface_facts,
        tool_surface_diff=public_tool_surface_diff,
        baseline_summary=baseline_summary,
        privacy_audit=privacy_audit,
        heuristics_filter=decision.heuristics_filter,
        policy_evidence_gaps=public_policy_evidence_gaps,
        source_omissions=[
            sanitize_model(
                omission,
                SourceSurfaceOmission,
                stats=privacy_stats,
                path="source_omissions[]",
            )
            for loaded in inputs.loaded_sources
            for omission in loaded.omissions
        ],
    )


def _indeterminate_override_positions(
    raw_manifest: AgentsShipgateManifest,
    public_manifest: AgentsShipgateManifest,
) -> frozenset[int]:
    """Rows whose override equality privacy redaction made unknowable.

    The durable projection carries only a fixed sentinel for these rows. It
    must not carry a digest of either raw string: evidence/reason fields often
    contain credentials, and a deterministic raw hash is still secret-derived
    material that enables offline guessing.
    """

    positions: set[int] = set()
    raw_rows = raw_manifest.action_surface.actions
    public_rows = public_manifest.action_surface.actions
    for position, (raw, public) in enumerate(zip(raw_rows, public_rows, strict=True)):
        if raw.override is None or public.override is None:
            continue
        if (
            raw.override.evidence != public.override.evidence
            or raw.override.reason != public.override.reason
        ):
            positions.add(position)
    return frozenset(positions)


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
        public_action_reference.facts if public_action_reference else None,
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
    toolkit_bounds=(),
):
    public_tool_surface_facts = sanitize_model(
        build_tool_surface_facts(
            public_manifest,
            public_tools,
            public_findings,
            public_api_artifacts,
            public_anthropic_artifacts,
            toolkit_bounds,
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


def _append_baseline_expansion_findings(
    *,
    diffs: _DiffReferences,
    decision: _ChecksDecision,
    public_findings: list,
    privacy_stats,
    plugins_enabled: bool | None,
) -> None:
    """Append verify baseline-expansion findings after baseline matching.

    ``SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED`` needs the head accepted-debt
    set, which only exists after ``apply_baseline`` has marked public findings
    with ``baseline_status="matched"``. Keep this as a normal Finding before
    ``build_report`` so the existing release decision engine remains the gate.
    """
    base_policy = (
        getattr(diffs.diff_reference, "effective_policy", None)
        if diffs.diff_reference is not None
        else None
    )
    findings = baseline_expansion_findings(
        decision.context,
        base_policy,
        head_baseline_fingerprints=accepted_debt_fingerprints(public_findings),
    )
    if not findings:
        return

    public_findings.extend(sanitize_findings(findings, stats=privacy_stats))
    assign_finding_ids(public_findings)
    annotate_remediation(
        public_findings,
        _check_metadata_lookup(plugins_enabled=plugins_enabled),
    )


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
