from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_shipgate.checks.baseline_integrity import (
    build_findings as build_integrity_findings,
)
from agents_shipgate.checks.registry import check_catalog, run_checks
from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.config.loader import load_manifest, load_manifest_with_positions
from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    CodexPluginArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
    OpenAIApiArtifacts,
    ValidationArtifacts,
)
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.baseline import (
    apply_baseline,
    baseline_resolved_fingerprints,
    load_baseline,
    verify_baseline,
)
from agents_shipgate.core.baseline_audit import DEFAULT_AUDIT_LOG_PATH
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import (
    Agent,
    LoadedToolSource,
    Tool,
)
from agents_shipgate.core.dynamic_defaults import dynamic_check_defaults
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.findings import (
    annotate_remediation,
    apply_severity_overrides,
    apply_suppressions,
    assign_finding_ids,
    build_report,
    build_reviewer_summary,
    dedupe_findings,
    finding_fingerprint,
    tool_inventory,
)
from agents_shipgate.core.privacy import (
    RedactionStats,
    build_privacy_audit,
    redact_data,
    sanitize_findings,
    sanitize_model,
    sanitize_tools,
)
from agents_shipgate.core.risk_hints import enrich_tools_with_risk_hints
from agents_shipgate.core.severity_overrides import resolve_severity_overrides
from agents_shipgate.inputs.policy_packs import load_policy_packs, run_policy_pack_rules
from agents_shipgate.inputs.protocol import (
    REGISTRY,
    LoadedAdapterResult,
    ToolSourceAdapter,
)
from agents_shipgate.packet.builder import build_packet
from agents_shipgate.packet.html import write_packet_html
from agents_shipgate.packet.json_packet import write_packet_json
from agents_shipgate.packet.markdown import write_packet_markdown
from agents_shipgate.packet.pdf import (
    PdfRendererUnavailable,
    is_pdf_available,
    render_packet_pdf,
)
from agents_shipgate.report.action_surface_diff import (
    action_reference_from_scan_reference,
    attach_action_surface_finding_summary,
    build_action_surface_facts,
    compute_action_surface_diff,
    enrich_action_surface_diff_with_source,
    evaluate_action_surface_policies,
)
from agents_shipgate.report.capability_diff import apply_capability_diff
from agents_shipgate.report.json_report import report_json_payload, write_json_report
from agents_shipgate.report.markdown import write_markdown_report
from agents_shipgate.report.sarif import write_sarif_report
from agents_shipgate.report.tool_surface_diff import (
    ToolSurfaceDiffReference,
    _stable_hash,
    build_tool_surface_facts,
    compute_tool_surface_diff,
    disabled_tool_surface_diff,
    enrich_tool_surface_diff_with_source,
    load_tool_surface_diff_reference,
    reference_from_baseline,
)
from agents_shipgate.schemas.codex_plugin import CodexPluginSurface
from agents_shipgate.schemas.common import parse_severity
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)
from agents_shipgate.schemas.report import (
    BaselineSummary,
    LoadedPolicyPack,
    PolicyAudit,
    ReadinessReport,
)
from agents_shipgate.schemas.surfaces import (
    ActionFact,
    ActionSurfaceFacts,
    ActionSurfaceHashes,
    ToolSurfaceFacts,
)

PACKET_FORMAT_NAMES = {"md", "json", "html", "pdf"}
"""Allowed values for ``--packet-format`` and ``output.packet.formats``."""

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Phase-result dataclasses (v0.19 R-3 architecture review item E3).
#
# ``run_scan`` was a single 619-line function that mixed nine sequential
# concerns: manifest preparation, input loading, tool/agent building, diff
# loading, check execution + severity resolution, output planning, privacy
# sanitization, report building, and file writing. Decomposing into named
# phase helpers — each with an explicit input/output dataclass — makes the
# pipeline visible at the call site and lets the most fragile phase
# (sanitization) be reasoned about in isolation.
#
# Hard contracts preserved (verified by tests/test_scan.py +
# tests/test_patches_model.py + tests/test_source_provenance.py):
#
# - Public ``run_scan`` signature unchanged.
# - ``_run_id`` inputs byte-identical to pre-decomp; the order of
#   operations inside each phase is preserved.
# - Sanitization (Phase 7) runs BEFORE any file is written. Every
#   write-path receives only ``public_*`` values from the
#   ``_SanitizedSurfaces`` bundle, never the raw values.
# - Existing helpers exported for direct test access (``_load_sources``,
#   ``_flatten_and_deduplicate_tools``, ``_run_id``,
#   ``_build_agent``) keep their signatures.
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedManifest:
    """Phase 1 output: manifest after CLI overrides applied."""

    manifest: AgentsShipgateManifest
    manifest_positions: Any
    base_dir: Path


@dataclass(frozen=True)
class _LoadedInputs:
    """Phase 2 output: source loading + artifact extraction + warnings.

    Warning buckets are kept separate so Phase 3 (``_build_tools_and_agent``)
    can interleave ``duplicate_warnings`` from
    ``_flatten_and_deduplicate_tools`` between ``source_only_warnings`` and
    ``artifact_warnings``, preserving the pre-decomp deterministic order:

        source → duplicate → artifact → placeholder → policy_pack → dedup

    Collapsing them into a single ``warnings`` list here (the P3 bug that
    this split fixes) would push duplicate warnings to the end, changing
    ``report.source_warnings`` order for fixtures with both duplicate-tool
    names and artifact/policy-pack warnings.
    """

    loaded_sources: list[LoadedToolSource]
    artifact_bag: ArtifactBag
    policy_packs: Any  # LoadedPolicyPacks
    source_only_warnings: list[str]   # per-source warnings, no dedup yet
    artifact_warnings_list: list[str]      # from _artifact_warnings(artifact_bag)
    placeholder_warnings: list[str]   # from _manifest_placeholder_warnings
    policy_pack_warnings: list[str]   # from policy_packs.warnings
    adk: GoogleAdkArtifacts | None
    langchain: LangChainArtifacts | None
    crewai: CrewAiArtifacts | None
    n8n: N8nArtifacts | None
    api: OpenAIApiArtifacts | None
    anthropic: AnthropicArtifacts | None
    codex_plugin: CodexPluginArtifacts | None
    validation: ValidationArtifacts | None


@dataclass(frozen=True)
class _ToolsAndAgent:
    """Phase 3 output: flattened/deduped/enriched tools + Agent + final warnings."""

    tools: list[Tool]
    agent: Agent
    warnings: list[str]  # deduplicated source warnings


@dataclass(frozen=True)
class _DiffReferences:
    """Phase 4 output: optional baseline + diff_from references."""

    baseline_file: Any  # BaselineFile | None
    baseline_display_path: str | None
    diff_reference: ToolSurfaceDiffReference | None
    diff_reference_error: str | None


@dataclass(frozen=True)
class _ChecksDecision:
    """Phase 5 output: action surface + checks + severity + remediation."""

    action_surface_facts: ActionSurfaceFacts
    action_surface_diff: Any  # ActionSurfaceDiff (internal/semantic)
    findings: list[Any]  # list[Finding]
    legacy_fingerprints: list[str]
    override_resolution: Any  # SeverityOverrideResolution
    loaded_plugins: list[dict[str, str | None]]
    context: ScanContext


@dataclass(frozen=True)
class _OutputPlan:
    """Phase 6 output: file paths + packet format set + privacy stats.

    ``privacy_stats`` is intentionally mutable — the sanitization phase
    accumulates redaction counts into it. The dataclass is ``frozen`` only
    in the sense that the field bindings don't change; the contained
    ``RedactionStats`` mutates in place.
    """

    out_dir: Path
    generated_paths: dict[str, Path]
    packet_format_set: set[str]
    output_surfaces: list[str]
    privacy_stats: RedactionStats
    generated_report_refs: Any


@dataclass
class _SanitizedSurfaces:
    """Phase 7 output: every ``public_*`` value flowing into report/packet.

    Not frozen — the baseline-integrity branch appends to ``findings``
    in place and refreshes derivative fields. After Phase 7 returns,
    every value here has been passed through ``redact_data`` /
    ``sanitize_*`` exactly once. Phase 8+ (``build_report`` /
    ``build_packet`` / ``_write_*``) MUST NOT re-redact and MUST NOT
    touch any raw (non-``public_*``) value.
    """

    manifest: AgentsShipgateManifest
    manifest_dir: str
    project: Any
    environment: Any
    agent: Agent
    tools: list[Tool]
    findings: list[Any]
    source_warnings: list[str]
    api_artifacts: OpenAIApiArtifacts | None
    anthropic_artifacts: AnthropicArtifacts | None
    validation_artifacts: ValidationArtifacts | None
    api_surface: Any
    anthropic_surface: Any
    frameworks_surface: Any
    codex_plugin_surface: CodexPluginSurface | None
    policy_audit: PolicyAudit
    loaded_policy_packs: list[Any]
    loaded_plugins: Any
    diff_reference: ToolSurfaceDiffReference | None
    action_surface_facts: ActionSurfaceFacts
    action_surface_diff: Any
    tool_surface_facts: Any
    tool_surface_diff: Any
    baseline_summary: Any
    privacy_audit: Any


# -----------------------------------------------------------------------------
# Phase helpers. Each takes explicit kwargs and returns a phase-result
# dataclass. Order of operations inside each helper matches the pre-decomp
# code one-for-one so ``_run_id`` and finding fingerprints stay
# byte-identical.
# -----------------------------------------------------------------------------


def _prepare_scan(
    *,
    config_path: Path,
    ci_mode: str | None,
    fail_on: list[str] | None,
    output_dir: Path | None,
    formats: list[str] | None,
    packet_enabled: bool | None,
    packet_formats: list[str] | None,
    baseline_mode: str,
) -> _ResolvedManifest:
    """Phase 1: load manifest with positions; apply CLI overrides.

    CLI overrides take precedence over manifest values. Raises
    ``ConfigError`` (exit 2) for invalid packet formats or unsupported
    baseline modes — both fail before any source loading happens.
    """
    raw_manifest, manifest_positions = load_manifest_with_positions(config_path)
    manifest = raw_manifest.model_copy(deep=True)
    if ci_mode:
        manifest.ci.mode = ci_mode
    if fail_on is not None:
        manifest.ci.fail_on = [parse_severity(item) for item in fail_on]
    if output_dir:
        manifest.output.directory = str(output_dir)
    if formats:
        manifest.output.formats = formats
    if packet_enabled is not None:
        manifest.output.packet.enabled = packet_enabled
    if packet_formats is not None:
        invalid = [f for f in packet_formats if f not in PACKET_FORMAT_NAMES]
        if invalid:
            raise ConfigError(
                "--packet-format values must be one of "
                f"{sorted(PACKET_FORMAT_NAMES)}; got {invalid}"
            )
        manifest.output.packet.formats = packet_formats
    if baseline_mode != "new-findings":
        raise ConfigError("--baseline-mode supports only new-findings")
    return _ResolvedManifest(
        manifest=manifest,
        manifest_positions=manifest_positions,
        base_dir=config_path.resolve().parent,
    )


def _load_inputs(
    *,
    manifest: AgentsShipgateManifest,
    base_dir: Path,
    config_path: Path,
    policy_pack_paths: list[Path] | None,
    verbose: bool,
) -> _LoadedInputs:
    """Phase 2: dispatch every adapter through ``REGISTRY``, extract
    typed artifacts from the ``ArtifactBag``, aggregate source warnings
    (including CHANGE_ME placeholder warnings from the manifest text),
    load policy packs.
    """
    loaded_sources, artifact_bag = _load_sources(manifest, base_dir, verbose=verbose)
    logger.debug(
        "loaded sources",
        extra={
            "agents_shipgate_source_count": len(loaded_sources),
            "agents_shipgate_sources": [
                {"id": source.source_id, "type": source.source_type, "tools": len(source.tools)}
                for source in loaded_sources
            ],
        },
    )
    # Keep warning buckets separate so Phase 3 can re-assemble them in the
    # pre-decomp order: source → duplicate → artifact → placeholder →
    # policy_pack → dedup. See _LoadedInputs docstring for the P3 rationale.
    source_only_warnings: list[str] = [
        warning for loaded in loaded_sources for warning in loaded.warnings
    ]
    artifact_warnings_list: list[str] = _artifact_warnings(artifact_bag)
    # Unresolved CHANGE_ME placeholders in the manifest mean the run is
    # operating on stub data. Surface them as source warnings so the
    # existing ``source_warning_count > 0`` branch in
    # release_decision.evidence_coverage routes the gate to
    # ``review_required`` and the packet §10 "Not proven" section
    # mentions the placeholder verbatim.
    placeholder_warnings: list[str] = _manifest_placeholder_warnings(config_path)
    policy_packs = load_policy_packs(
        manifest=manifest,
        base_dir=base_dir,
        cli_policy_packs=policy_pack_paths,
    )
    policy_pack_warnings: list[str] = list(policy_packs.warnings)
    return _LoadedInputs(
        loaded_sources=loaded_sources,
        artifact_bag=artifact_bag,
        policy_packs=policy_packs,
        source_only_warnings=source_only_warnings,
        artifact_warnings_list=artifact_warnings_list,
        placeholder_warnings=placeholder_warnings,
        policy_pack_warnings=policy_pack_warnings,
        adk=artifact_bag.get("google_adk", GoogleAdkArtifacts),
        langchain=artifact_bag.get("langchain", LangChainArtifacts),
        crewai=artifact_bag.get("crewai", CrewAiArtifacts),
        n8n=artifact_bag.get("n8n", N8nArtifacts),
        api=artifact_bag.get("openai_api", OpenAIApiArtifacts),
        anthropic=artifact_bag.get("anthropic_api", AnthropicArtifacts),
        codex_plugin=artifact_bag.get("codex_plugin", CodexPluginArtifacts),
        validation=artifact_bag.get("validation", ValidationArtifacts),
    )


def _build_tools_and_agent(
    *,
    manifest: AgentsShipgateManifest,
    inputs: _LoadedInputs,
) -> _ToolsAndAgent:
    """Phase 3: flatten/dedup tools with source priority, enrich with
    manifest-derived risk hints, build the ``Agent`` object, finalize
    the source-warnings list (dedup after appending the duplicate-tool
    warnings from ``_flatten_and_deduplicate_tools``).
    """
    tools, duplicate_warnings = _flatten_and_deduplicate_tools(inputs.loaded_sources)
    # Assemble in pre-decomp order: source → duplicate → artifact →
    # placeholder → policy_pack. Duplicate warnings MUST come immediately
    # after per-source warnings (before artifact / placeholder / policy_pack)
    # so ``report.source_warnings`` is byte-identical to pre-v0.19 output.
    # (P3 fix: _LoadedInputs now carries separate buckets instead of a
    # pre-assembled list so this interleaving is possible.)
    warnings: list[str] = list(inputs.source_only_warnings)
    warnings.extend(duplicate_warnings)
    warnings.extend(inputs.artifact_warnings_list)
    warnings.extend(inputs.placeholder_warnings)
    warnings.extend(inputs.policy_pack_warnings)
    # Some adapters expose the same warnings through both LoadedToolSource
    # and the artifact bag; keep report warning output stable and unique.
    warnings = list(dict.fromkeys(warnings))
    tools = enrich_tools_with_risk_hints(manifest, tools)
    logger.debug(
        "risk hints generated",
        extra={
            "agents_shipgate_tools": [
                {
                    "name": tool.name,
                    "risk_hints": [
                        {"tag": hint.tag, "confidence": hint.confidence, "source": hint.source}
                        for hint in tool.risk_hints
                    ],
                }
                for tool in tools
            ]
        },
    )
    agent = _build_agent(
        manifest, tools, inputs.api, inputs.anthropic, inputs.adk
    )
    return _ToolsAndAgent(tools=tools, agent=agent, warnings=warnings)


def _load_diff_references(
    *,
    baseline_path: Path | None,
    diff_from_path: Path | None,
    base_dir: Path,
) -> _DiffReferences:
    """Phase 4: load optional baseline JSON + tool-surface diff reference.

    ``--diff-from`` wins over baseline-derived reference when both are
    supplied. ``InputParseError`` from either path is caught and returned
    as a string so the downstream diff is rendered as ``enabled=False``
    with a reviewer-visible note rather than aborting the scan.
    """
    baseline_file = load_baseline(baseline_path) if baseline_path else None
    baseline_display_path = (
        _relative_display_path(baseline_path, base_dir) if baseline_path else None
    )
    diff_reference: ToolSurfaceDiffReference | None = None
    diff_reference_error: str | None = None
    try:
        if diff_from_path:
            diff_reference = load_tool_surface_diff_reference(
                diff_from_path,
                display_path=_relative_display_path(diff_from_path, base_dir),
            )
        elif baseline_file:
            diff_reference = reference_from_baseline(
                baseline_file,
                display_path=baseline_display_path,
            )
    except InputParseError as exc:
        diff_reference_error = str(exc)
    return _DiffReferences(
        baseline_file=baseline_file,
        baseline_display_path=baseline_display_path,
        diff_reference=diff_reference,
        diff_reference_error=diff_reference_error,
    )


def _run_checks_and_decide(
    *,
    manifest: AgentsShipgateManifest,
    manifest_positions: Any,
    config_path: Path,
    tools_and_agent: _ToolsAndAgent,
    inputs: _LoadedInputs,
    diffs: _DiffReferences,
    plugins_enabled: bool | None,
    suggest_patches: bool,
) -> _ChecksDecision:
    """Phase 5: build internal action-surface facts, run all checks
    (built-in + plugin + policy-pack + action-surface policies),
    resolve severity overrides via the dynamic-default aggregator,
    apply suppressions + optional patches, annotate remediation
    metadata, snapshot ``legacy_fingerprints`` for pre-v0.18 baseline
    compatibility.

    The INTERNAL ``action_surface_diff`` returned here is semantic
    only — provenance enrichment happens later on the PUBLIC diff
    derived from sanitized tools. Mutating ``reason`` here would leak
    ``path:line`` into ``Finding.evidence``, churning fingerprints.
    """
    action_surface_facts = build_action_surface_facts(
        manifest,
        agent_id=tools_and_agent.agent.id,
        tools=tools_and_agent.tools,
    )
    action_reference = action_reference_from_scan_reference(diffs.diff_reference)
    action_surface_diff = compute_action_surface_diff(
        action_surface_facts,
        action_reference.facts if action_reference else None,
        reference=action_reference,
    )
    if diffs.diff_reference_error:
        action_surface_diff.enabled = False
        action_surface_diff.notes = [diffs.diff_reference_error]
    context = ScanContext(
        manifest=manifest,
        agent=tools_and_agent.agent,
        tools=tools_and_agent.tools,
        config_path=config_path.resolve(),
        framework_artifacts=inputs.artifact_bag,
        action_surface_facts=action_surface_facts,
        manifest_positions=manifest_positions,
    )
    loaded_plugins: list[dict[str, str | None]] = []
    findings = run_checks(
        context,
        plugins_enabled=plugins_enabled,
        loaded_plugins=loaded_plugins,
        extra_known_check_ids={
            resolved.rule.id for resolved in inputs.policy_packs.rules
        },
    )
    findings.extend(run_policy_pack_rules(context, inputs.policy_packs))
    findings.extend(
        evaluate_action_surface_policies(
            manifest,
            action_surface_facts,
            action_surface_diff,
            agent_id=tools_and_agent.agent.id,
            tools=tools_and_agent.tools,
        )
    )
    findings = dedupe_findings(findings)
    # v0.17 (M1) + v0.18 (PR #1): centralized aggregator covers every
    # catalog check with ``dynamic_default=True``. See
    # ``core/dynamic_defaults.py`` and ``severity_overrides.py`` for the
    # tier-crossing / floor-enforcement contract.
    catalog = check_catalog(plugins_enabled=plugins_enabled)
    effective_dynamic_defaults = dynamic_check_defaults(
        manifest, inputs.policy_packs, catalog=catalog
    )
    override_resolution = resolve_severity_overrides(
        overrides=manifest.severity_override_entries(),
        acknowledgements=manifest.acknowledge_overrides(),
        catalog=catalog,
        extra_known_check_defaults=effective_dynamic_defaults,
    )
    apply_severity_overrides(findings, override_resolution.override_by_check_id)
    apply_suppressions(findings, manifest.checks.ignore)
    if suggest_patches:
        _attach_patches(
            findings,
            manifest,
            config_path,
            plugins_enabled=plugins_enabled,
        )
    # v0.7: annotate every finding (regardless of --suggest-patches) with
    # the four remediation fields. When patches are present they're
    # derived from those; otherwise the per-check CheckMetadata seeds
    # the values.
    annotate_remediation(
        findings,
        _check_metadata_lookup(plugins_enabled=plugins_enabled),
    )
    legacy_fingerprints = [finding_fingerprint(finding) for finding in findings]
    logger.debug(
        "checks completed",
        extra={
            "agents_shipgate_finding_count": len(findings),
            "agents_shipgate_suppressed_count": sum(
                1 for finding in findings if finding.suppressed
            ),
        },
    )
    return _ChecksDecision(
        action_surface_facts=action_surface_facts,
        action_surface_diff=action_surface_diff,
        findings=findings,
        legacy_fingerprints=legacy_fingerprints,
        override_resolution=override_resolution,
        loaded_plugins=loaded_plugins,
        context=context,
    )


def _plan_outputs(
    *,
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> _OutputPlan:
    """Phase 6: resolve output dir + planned file paths + packet format
    set (filtering PDF if weasyprint is missing). Initialize the
    ``RedactionStats`` accumulator and the already-redacted
    ``generated_reports`` map needed by ``build_report`` downstream.
    """
    out_dir = (base_dir / manifest.output.directory).resolve()
    packet_cfg = manifest.output.packet
    packet_format_set, packet_pdf_skipped = _resolve_packet_format_set(packet_cfg)
    if packet_pdf_skipped:
        # PDF availability is an *output renderer* concern, not a source
        # loader concern. Routing it through `warnings` would inflate
        # `evidence_coverage.source_warning_count` and add a noise
        # residual to the packet's §10, telling reviewers to rerun the
        # scan after fixing source warnings even when no source loader
        # had a problem. Log it instead — same channel as runtime
        # WeasyPrint failures in `_write_packet`.
        logger.warning(
            "packet.pdf requested but weasyprint is not installed; "
            "install with `pipx install 'agents-shipgate[pdf]'` to "
            "enable. Skipping PDF for this run."
        )
    generated_paths = _planned_generated_paths(
        out_dir,
        manifest.output.formats,
        packet_enabled=packet_cfg.enabled,
        packet_formats=packet_format_set,
    )
    privacy_stats = RedactionStats()
    generated_report_refs = redact_data(
        {
            key: _relative_display_path(path, base_dir)
            for key, path in generated_paths.items()
        },
        stats=privacy_stats,
        path="generated_reports",
    )
    output_surfaces = list(generated_paths)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        output_surfaces.append("github_step_summary")
    return _OutputPlan(
        out_dir=out_dir,
        generated_paths=generated_paths,
        packet_format_set=packet_format_set,
        output_surfaces=output_surfaces,
        privacy_stats=privacy_stats,
        generated_report_refs=generated_report_refs,
    )


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

    public_diff_reference = _sanitize_diff_reference(
        diffs.diff_reference,
        stats=privacy_stats,
    )
    public_action_surface_facts = _build_public_action_surface_facts(
        raw_facts=decision.action_surface_facts,
        manifest=public_manifest,
        agent_id=public_agent.id,
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
        # v0.5 baseline-integrity (M2). Run this after public finding
        # fingerprints are assigned so integrity output does not depend on
        # raw secret-bearing finding IDs.
        integrity_mode = manifest.baseline.integrity_mode
        if integrity_mode != "off" and baseline_path is not None:
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
                diffs.baseline_file,
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
                    issue.evidence["v0_18_privacy_migration_hint"] = (
                        baseline_privacy_hint
                    )
            integrity_findings = build_integrity_findings(
                static_issues + stale_issues,
                context=decision.context,
                integrity_mode=integrity_mode,
            )
            if baseline_privacy_hint:
                for finding in integrity_findings:
                    if finding.check_id == "SHIP-BASELINE-ENTRY-STALE":
                        finding.recommendation = (
                            f"{finding.recommendation} {baseline_privacy_hint}"
                        )
            if integrity_findings:
                public_findings.extend(
                    sanitize_findings(integrity_findings, stats=privacy_stats)
                )
                assign_finding_ids(public_findings)
                annotate_remediation(
                    public_findings,
                    _check_metadata_lookup(plugins_enabled=plugins_enabled),
                )
    attach_action_surface_finding_summary(public_action_surface_diff, public_findings)

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
        diff_reference=public_diff_reference,
        action_surface_facts=public_action_surface_facts,
        action_surface_diff=public_action_surface_diff,
        tool_surface_facts=public_tool_surface_facts,
        tool_surface_diff=public_tool_surface_diff,
        baseline_summary=baseline_summary,
        privacy_audit=privacy_audit,
    )


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
    public_report_payload = report_json_payload(report)
    return report, public_report_payload


def _write_outputs(
    *,
    report: ReadinessReport,
    public_report_payload: Any,
    sanitized: _SanitizedSurfaces,
    plan: _OutputPlan,
    manifest: AgentsShipgateManifest,
    config_path: Path,
    packet_generated_at: str | None,
) -> None:
    """Phase 9: write report (md/json/sarif) + packet (md/json/html/pdf).

    Both writes consume only sanitized values; the raw manifest is
    passed to ``build_packet`` for non-output internal use (packet
    builder reads manifest defaults like ``output.packet.formats`` but
    never serializes raw manifest content into the packet).
    """
    _write_reports(report, plan.generated_paths, manifest.output.formats)
    if manifest.output.packet.enabled and plan.packet_format_set:
        assert report.release_decision is not None
        packet = build_packet(
            manifest=manifest,
            agent=report.agent,
            project=report.project,
            environment=report.environment,
            run_id=report.run_id,
            tools=sanitized.tools,
            findings=sanitized.findings,
            release_decision=report.release_decision,
            api_artifacts=sanitized.api_artifacts,
            anthropic_artifacts=sanitized.anthropic_artifacts,
            source_warnings=sanitized.source_warnings,
            validation_artifacts=sanitized.validation_artifacts,
            tool_surface_diff=report.tool_surface_diff,
            action_surface_diff=report.action_surface_diff,
            report_payload=public_report_payload,
            generated_at=packet_generated_at,
            config_ref=config_path.resolve().name,
        )
        _write_packet(packet, plan.generated_paths, plan.packet_format_set)


# -----------------------------------------------------------------------------
# Public entry point.
# -----------------------------------------------------------------------------


def run_scan(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    formats: list[str] | None = None,
    ci_mode: str | None = None,
    fail_on: list[str] | None = None,
    baseline_path: Path | None = None,
    diff_from_path: Path | None = None,
    baseline_mode: str = "new-findings",
    deep_import: bool = False,
    policy_pack_paths: list[Path] | None = None,
    plugins_enabled: bool | None = None,
    verbose: bool = False,
    suggest_patches: bool = False,
    packet_enabled: bool | None = None,
    packet_formats: list[str] | None = None,
    packet_generated_at: str | None = None,
) -> tuple[ReadinessReport, int]:
    """Run a full scan pipeline. Returns ``(report, exit_code)``.

    Orchestrates nine sequential phases (see the phase helpers above).
    Public signature, exit-code contract, and ``_run_id`` hash inputs
    are stable across the v0.19 R-3 decomposition refactor.
    """
    if deep_import:
        raise ConfigError("Deep import is intentionally deferred and is not supported.")

    resolved = _prepare_scan(
        config_path=config_path,
        ci_mode=ci_mode,
        fail_on=fail_on,
        output_dir=output_dir,
        formats=formats,
        packet_enabled=packet_enabled,
        packet_formats=packet_formats,
        baseline_mode=baseline_mode,
    )
    inputs = _load_inputs(
        manifest=resolved.manifest,
        base_dir=resolved.base_dir,
        config_path=config_path,
        policy_pack_paths=policy_pack_paths,
        verbose=verbose,
    )
    tools_and_agent = _build_tools_and_agent(
        manifest=resolved.manifest,
        inputs=inputs,
    )
    diffs = _load_diff_references(
        baseline_path=baseline_path,
        diff_from_path=diff_from_path,
        base_dir=resolved.base_dir,
    )
    decision = _run_checks_and_decide(
        manifest=resolved.manifest,
        manifest_positions=resolved.manifest_positions,
        config_path=config_path,
        tools_and_agent=tools_and_agent,
        inputs=inputs,
        diffs=diffs,
        plugins_enabled=plugins_enabled,
        suggest_patches=suggest_patches,
    )
    plan = _plan_outputs(
        manifest=resolved.manifest,
        base_dir=resolved.base_dir,
    )
    sanitized = _sanitize_for_output(
        manifest=resolved.manifest,
        config_path=config_path,
        baseline_path=baseline_path,
        inputs=inputs,
        tools_and_agent=tools_and_agent,
        diffs=diffs,
        decision=decision,
        plan=plan,
        plugins_enabled=plugins_enabled,
    )
    report, public_report_payload = _build_final_report(
        manifest=resolved.manifest,
        sanitized=sanitized,
        plan=plan,
    )
    _write_outputs(
        report=report,
        public_report_payload=public_report_payload,
        sanitized=sanitized,
        plan=plan,
        manifest=resolved.manifest,
        config_path=config_path,
        packet_generated_at=packet_generated_at,
    )
    write_github_step_summary(report)
    assert report.release_decision is not None  # build_report always populates it
    return report, report.release_decision.fail_policy.exit_code




def inspect_sources(*, config_path: Path, verbose: bool = False) -> dict[str, object]:
    manifest = load_manifest(config_path)
    base_dir = config_path.resolve().parent
    unresolved_sources = _resolve_source_paths(manifest, base_dir, config_path)
    if unresolved_sources:
        # Drop unresolved-required sources from the manifest before loading
        # so doctor returns a structured payload with `unresolved_sources`
        # instead of raising InputParseError. scan() does not use this path
        # — its `_load_sources` call is unchanged and still raises.
        unresolved_ids = {entry["id"] for entry in unresolved_sources}
        manifest = manifest.model_copy(
            update={
                "tool_sources": [
                    src for src in manifest.tool_sources
                    if src.id not in unresolved_ids
                ]
            }
        )
    loaded_sources, artifact_bag = _load_sources(manifest, base_dir, verbose=verbose)
    adk_artifacts = artifact_bag.get("google_adk", GoogleAdkArtifacts)
    langchain_artifacts = artifact_bag.get("langchain", LangChainArtifacts)
    crewai_artifacts = artifact_bag.get("crewai", CrewAiArtifacts)
    n8n_artifacts = artifact_bag.get("n8n", N8nArtifacts)
    api_artifacts = artifact_bag.get("openai_api", OpenAIApiArtifacts)
    anthropic_artifacts = artifact_bag.get("anthropic_api", AnthropicArtifacts)
    codex_plugin_artifacts = artifact_bag.get("codex_plugin", CodexPluginArtifacts)
    tools, duplicate_warnings = _flatten_and_deduplicate_tools(loaded_sources)
    warnings = [warning for loaded in loaded_sources for warning in loaded.warnings]
    warnings.extend(duplicate_warnings)
    warnings.extend(_artifact_warnings(artifact_bag))
    policy_packs = load_policy_packs(manifest, base_dir)
    warnings.extend(policy_packs.warnings)
    # Some adapters expose the same warnings through both LoadedToolSource
    # and the artifact bag; keep doctor warning output stable and unique.
    warnings = list(dict.fromkeys(warnings))
    payload = {
        "project": manifest.project.name,
        "agent": manifest.agent.name,
        "config": str(config_path),
        "total_tools": len(tools),
        "sources": [
            {
                "id": source.source_id,
                "type": source.source_type,
                "tool_count": len(source.tools),
                "sample_tool": source.tools[0].name if source.tools else None,
                "warnings": source.warnings,
            }
            for source in loaded_sources
        ],
        "api_surface": api_artifacts.surface_summary() if api_artifacts else None,
        "anthropic_surface": (
            anthropic_artifacts.surface_summary() if anthropic_artifacts else None
        ),
        "frameworks": _frameworks_surface(
            adk_artifacts,
            langchain_artifacts,
            crewai_artifacts,
            n8n_artifacts,
        ),
        "codex_plugin_surface": (
            codex_plugin_artifacts.surface_summary().model_dump(mode="json")
            if codex_plugin_artifacts
            else None
        ),
        "policy_packs": [pack.model_dump(mode="json") for pack in policy_packs.loaded],
        "baseline": _default_baseline_status(base_dir),
        "warnings": warnings,
        "unresolved_sources": unresolved_sources,
        "manifest_summary": {
            "environment_target": manifest.environment.target,
            "has_permissions": bool(
                manifest.permissions.scopes or manifest.permissions.credential_mode
            ),
            "has_policies": bool(
                manifest.policies.require_approval_for_tools
                or manifest.policies.require_confirmation_for_tools
                or manifest.policies.require_idempotency_for_tools
            ),
            "scope_count": len(manifest.permissions.scopes),
        },
    }
    return redact_data(payload, stats=RedactionStats(), path="$")


def _resolve_source_paths(
    manifest, base_dir: Path, config_path: Path
) -> list[dict[str, object]]:
    """Return required tool_sources whose declared path is unusable.

    Two failure modes are flagged so doctor can surface them as a
    ``SHIP-DIAG-MISSING-SOURCE-FILE`` diagnostic instead of crashing in
    a downstream loader:

    - ``reason="missing"`` — the file does not exist.
    - ``reason="outside_manifest_dir"`` — the file exists but escapes the
      manifest's containment boundary (loaders mirror this check and
      would raise ``InputParseError``).

    Optional sources are not reported here — the existing
    ``_load_sources`` flow handles them with a warning. Returned entries
    carry the source id, the declared path string, the 1-indexed line
    number in the manifest text where the path appears (best-effort),
    and the failure reason.
    """
    unresolved: list[dict[str, object]] = []
    try:
        manifest_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        manifest_text = ""
    text_lines = manifest_text.splitlines()
    base_resolved = base_dir.resolve()
    for source in manifest.tool_sources:
        if source.optional:
            continue
        if source.path is None:
            continue
        raw_path = Path(source.path)
        candidate = (
            raw_path if raw_path.is_absolute() else base_resolved / raw_path
        ).resolve()
        if not candidate.exists():
            reason = "missing"
        else:
            try:
                candidate.relative_to(base_resolved)
            except ValueError:
                reason = "outside_manifest_dir"
            else:
                continue
        line_no: int | None = None
        needle = f"path: {source.path}"
        for index, line in enumerate(text_lines, start=1):
            if needle in line:
                line_no = index
                break
        unresolved.append(
            {
                "id": source.id,
                "declared_path": source.path,
                "line": line_no,
                "reason": reason,
            }
        )
    return unresolved


def _load_sources(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
    *,
    verbose: bool,
) -> tuple[list[LoadedToolSource], ArtifactBag]:
    """Dispatch every adapter through ``REGISTRY``.

    Returns ``(loaded_sources, artifact_bag)``. ``artifact_bag`` is a
    typed ``ArtifactBag`` with per-scan adapter artifacts keyed by
    ``source_type``. Per-source adapters (mcp, openapi,
    openai_agents_sdk) never populate artifacts.

    Ordering is deterministic and matches the legacy run_scan order:

      1. per-source loaders in tool_sources declared order
      2. per-scan adapters in REGISTRY iteration order:
         google_adk → langchain → crewai → n8n → openai_api
         → anthropic_api → codex_plugin → validation

    Per-scan adapters are invoked unconditionally in pass 2, in
    canonical order — NOT in tool_sources declared order. This matches
    today's run_scan exactly: framework loaders fire once per scan in
    fixed order, and the manifest-only loaders (openai_api,
    anthropic_api) and codex_plugin trail them.
    Per-scan source types appearing in tool_sources are ignored by
    pass 1 — they would be redundant; framework loaders already iterate
    every matching entry internally via the manifest.
    """
    per_source_loaded: list[LoadedToolSource] = []
    per_scan_loaded: list[LoadedToolSource] = []
    bag = ArtifactBag()

    # Pass 1 — per-source adapters only, in tool_sources declared
    # order. Per-scan source types (langchain, crewai, etc.) are
    # skipped here; pass 2 invokes them in canonical REGISTRY order
    # regardless of where they appear in tool_sources. This protects
    # the dedup tie-break in _flatten_and_deduplicate_tools from
    # changing based on user-facing tool_sources ordering.
    for source in manifest.tool_sources:
        adapter = REGISTRY.require(source.type)
        if adapter.scope != "per_source":
            continue
        result = _invoke_per_source_adapter(
            adapter, source, base_dir, manifest, verbose=verbose
        )
        _absorb(result, source.type, per_source_loaded, bag, adapter)

    # Pass 2 — every per-scan adapter fires once, in REGISTRY order.
    # Covers framework adapters (always check their manifest section
    # internally and may emit zero LoadedToolSource entries when not
    # configured) and manifest-only adapters (openai_api,
    # anthropic_api, n8n).
    for adapter in REGISTRY.per_scan_adapters():
        result = adapter.load(None, base_dir, manifest)
        _absorb(result, adapter.source_type, per_scan_loaded, bag, adapter)

    return per_source_loaded + per_scan_loaded, bag


def _tool_source_index(
    tools: list[Tool],
) -> dict[str, tuple[str | None, int | None]]:
    """Build a tool-name → ``(source_path, source_start_line)`` map for
    surface-diff enrichment.

    Used by ``enrich_action_surface_diff_with_source`` and
    ``enrich_tool_surface_diff_with_source`` to append
    ``(source: path:line)`` to change-row ``reason`` strings, and by
    the packet builder to suffix §3A / §3B highlights. Empty when the
    tool list is empty so callers can rely on a boolean test.
    """
    return {
        tool.name: (tool.source_path, tool.source_start_line)
        for tool in tools
    }


def _artifact_warnings(artifact_bag: ArtifactBag) -> list[str]:
    warnings: list[str] = []
    for artifact in artifact_bag.raw().values():
        artifact_warnings = getattr(artifact, "warnings", None)
        if isinstance(artifact_warnings, list):
            warnings.extend(str(warning) for warning in artifact_warnings)
    return warnings


def _manifest_placeholder_warnings(config_path: Path) -> list[str]:
    """Return source-warning strings for each ``CHANGE_ME`` placeholder
    surviving in the manifest text.

    Doctor already surfaces these as ``SHIP-DIAG-CHANGE-ME-PLACEHOLDERS``
    diagnostics; the same fact also needs to flow into the scan so the
    existing ``source_warning_count > 0 → review_required`` branch in
    release_decision.evidence_coverage trips. Read failures (missing
    file, non-UTF8 content) yield no warnings — the manifest loader runs
    immediately before and will have already raised a structured error
    in that case.
    """
    try:
        manifest_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    placeholders = collect_placeholders(manifest_text)
    name = config_path.name
    return [
        f"{name}:{entry['line']} — CHANGE_ME placeholder at "
        f"{entry.get('path', '<root>')!r}; replace before treating this "
        "report as evidence."
        for entry in placeholders
    ]


def _absorb(
    result: LoadedAdapterResult,
    source_type: str,
    sink: list[LoadedToolSource],
    bag: ArtifactBag,
    adapter: ToolSourceAdapter,
) -> None:
    sink.extend(result.tool_sources)
    if result.artifact is not None:
        if adapter.artifact_class is not None and not isinstance(
            result.artifact, adapter.artifact_class
        ):
            raise TypeError(
                f"Adapter {adapter.source_type!r} declared "
                f"artifact_class={adapter.artifact_class.__name__} but "
                f"returned {type(result.artifact).__name__}"
            )
        bag.set(source_type, result.artifact)
    if result.warnings:
        sink.append(
            LoadedToolSource(
                source_id=f"adapter:{source_type}",
                source_type=source_type,
                warnings=list(result.warnings),
            )
        )


def _invoke_per_source_adapter(
    adapter: ToolSourceAdapter,
    source: ToolSourceConfig,
    base_dir: Path,
    manifest: AgentsShipgateManifest,
    *,
    verbose: bool,
) -> LoadedAdapterResult:
    try:
        return adapter.load(source, base_dir, manifest)
    except InputParseError:
        if source.optional:
            warning = f"Optional source {source.id} failed to load"
            if verbose:
                warning = (
                    f"{warning}; continuing because the source is marked optional"
                )
            return LoadedAdapterResult(
                tool_sources=[
                    LoadedToolSource(
                        source_id=source.id,
                        source_type=source.type,
                        warnings=[warning],
                    )
                ],
            )
        raise


def _flatten_and_deduplicate_tools(
    loaded_sources: list[LoadedToolSource],
) -> tuple[list[Tool], list[str]]:
    by_id: dict[str, Tool] = {}
    warnings: list[str] = []
    for loaded in loaded_sources:
        for tool in loaded.tools:
            existing = by_id.get(tool.id)
            if not existing:
                by_id[tool.id] = tool
                continue
            if _source_priority(tool) > _source_priority(existing):
                kept, dropped = tool, existing
            else:
                kept, dropped = existing, tool
            by_id[tool.id] = _merge_duplicate_tool_metadata(kept, dropped)
            warnings.append(
                "Duplicate tool name "
                f"{tool.name!r}; kept {kept.source_type} source {kept.source_id!r} "
                f"and merged metadata from {dropped.source_type} source {dropped.source_id!r}."
            )
    return list(by_id.values()), warnings


def _source_priority(tool: Tool) -> int:
    # Anthropic and OpenAI artifacts are equally authoritative; on duplicate
    # tool names across them the first-loaded entry wins (OpenAI is loaded
    # first in run_scan), and a `Duplicate tool name` warning surfaces.
    return {
        "openai_api": 40,
        "anthropic_api": 40,
        "openapi": 30,
        "google_adk_inventory": 25,
        "langchain_inventory": 25,
        "crewai_inventory": 25,
        "codex_plugin_mcp_inventory": 25,
        "n8n_inventory": 25,
        "mcp": 20,
        "google_adk_function": 10,
        "langchain_function": 10,
        "langchain_structured_tool": 10,
        "crewai_function": 10,
        "crewai_class_tool": 10,
        "n8n_ai_tool": 10,
        "n8n_workflow_tool": 10,
        "n8n_code_tool": 10,
        "n8n_http_tool": 10,
        "n8n_mcp_client_tool": 10,
        "sdk_function": 10,
        "google_adk_config": 5,
        "crewai_prebuilt_tool": 5,
    }.get(tool.source_type, 0)


def _merge_duplicate_tool_metadata(kept: Tool, dropped: Tool) -> Tool:
    merged = kept.model_copy(deep=True)
    merged.annotations = {**dropped.annotations, **merged.annotations}
    seen_hints = {_risk_hint_key(hint) for hint in merged.risk_hints}
    for hint in dropped.risk_hints:
        key = _risk_hint_key(hint)
        if key in seen_hints:
            continue
        merged.risk_hints.append(hint.model_copy(deep=True))
        seen_hints.add(key)
    merged.auth = merged.auth.model_copy(deep=True)
    merged.auth.scopes = _merge_string_values(merged.auth.scopes, dropped.auth.scopes)
    if not merged.auth.type:
        merged.auth.type = dropped.auth.type
    if not merged.auth.credential_mode:
        merged.auth.credential_mode = dropped.auth.credential_mode
    if not merged.auth.source and dropped.auth.source:
        merged.auth.source = dropped.auth.source
    if merged.owner is None:
        merged.owner = dropped.owner
    return merged


def _risk_hint_key(hint) -> tuple[str, str, str, str]:
    evidence = json.dumps(hint.evidence, sort_keys=True, default=str)
    return hint.tag, hint.source, hint.confidence, evidence


def _merge_string_values(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*primary, *secondary]:
        if value not in merged:
            merged.append(value)
    return merged


def _build_agent(
    manifest,
    tools: list[Tool],
    api_artifacts: OpenAIApiArtifacts | None = None,
    anthropic_artifacts: AnthropicArtifacts | None = None,
    adk_artifacts: GoogleAdkArtifacts | None = None,
) -> Agent:
    sdk = manifest.agent.sdk
    instructions_preview = manifest.agent.instructions_preview
    instruction_source = "config" if instructions_preview else "dynamic_unknown"
    instruction_confidence = "high" if instructions_preview else "medium"
    if not instructions_preview and api_artifacts and api_artifacts.prompt_text:
        instructions_preview = api_artifacts.prompt_text[:500]
        instruction_source = "openai_api_prompt_files"
        instruction_confidence = "high"
    if (
        not instructions_preview
        and anthropic_artifacts
        and anthropic_artifacts.prompt_text
    ):
        instructions_preview = anthropic_artifacts.prompt_text[:500]
        instruction_source = "anthropic_prompt_files"
        instruction_confidence = "high"
    if not instructions_preview and adk_artifacts:
        adk_instruction = _first_adk_instruction_preview(adk_artifacts)
        if adk_instruction:
            instructions_preview = adk_instruction[:500]
            instruction_source = "google_adk_static"
            instruction_confidence = "medium"
    return Agent(
        id=f"agent:{manifest.project.name}/{manifest.agent.name}",
        name=manifest.agent.name,
        source=sdk.model_dump(exclude_none=True) if sdk else {"source": "manifest"},
        instructions={
            "value_preview": instructions_preview,
            "source": instruction_source,
            "confidence": instruction_confidence,
        },
        declared_purpose=manifest.agent.declared_purpose,
        prohibited_actions=manifest.agent.prohibited_actions,
        tools=[tool.name for tool in tools],
        guardrails={
            "input": "unknown",
            "output": "unknown",
            "tool": "unknown",
            "source": "unknown",
        },
        extraction={
            "method": "config_assisted",
            "confidence": "medium",
            "missing_fields": ["runtime_traces"],
            "dynamic_fields": [],
        },
    )


def _first_adk_instruction_preview(adk_artifacts: GoogleAdkArtifacts) -> str | None:
    for agent in adk_artifacts.agents:
        value = agent.get("instruction_preview")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _planned_generated_paths(
    out_dir: Path,
    formats: list[str],
    *,
    packet_enabled: bool = False,
    packet_formats: set[str] | None = None,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    if "markdown" in formats:
        paths["markdown"] = out_dir / "report.md"
    if "json" in formats:
        paths["json"] = out_dir / "report.json"
    if "sarif" in formats:
        paths["sarif"] = out_dir / "report.sarif"
    if packet_enabled and packet_formats:
        if "md" in packet_formats:
            paths["packet_md"] = out_dir / "packet.md"
        if "json" in packet_formats:
            paths["packet_json"] = out_dir / "packet.json"
        if "html" in packet_formats:
            paths["packet_html"] = out_dir / "packet.html"
        if "pdf" in packet_formats:
            paths["packet_pdf"] = out_dir / "packet.pdf"
    return paths


def _write_reports(
    report: ReadinessReport, paths: dict[str, Path], formats: list[str]
) -> None:
    if "markdown" in formats and "markdown" in paths:
        write_markdown_report(report, paths["markdown"])
    if "json" in formats and "json" in paths:
        write_json_report(report, paths["json"])
    if "sarif" in formats and "sarif" in paths:
        write_sarif_report(report, paths["sarif"])


def _write_packet(packet, paths: dict[str, Path], packet_formats: set[str]) -> None:
    if "md" in packet_formats and "packet_md" in paths:
        write_packet_markdown(packet, paths["packet_md"])
    if "json" in packet_formats and "packet_json" in paths:
        write_packet_json(packet, paths["packet_json"])
    if "html" in packet_formats and "packet_html" in paths:
        write_packet_html(packet, paths["packet_html"])
    if "pdf" in packet_formats and "packet_pdf" in paths:
        try:
            render_packet_pdf(packet, paths["packet_pdf"])
        except PdfRendererUnavailable as exc:
            logger.warning("packet.pdf skipped: %s", exc)


def _resolve_packet_format_set(packet_cfg) -> tuple[set[str], bool]:
    """Resolve the writeable packet formats after probing weasyprint.

    Returns ``(formats, pdf_skipped)``: ``formats`` is the set of
    format names that should actually be emitted; ``pdf_skipped`` is
    ``True`` iff the user requested PDF but weasyprint is unavailable
    on this install (so the caller can record a single warning).
    """

    requested = {fmt for fmt in packet_cfg.formats if fmt in PACKET_FORMAT_NAMES}
    if not packet_cfg.enabled:
        return set(), False
    if "pdf" in requested and not is_pdf_available():
        return requested - {"pdf"}, True
    return requested, False


def _relative_display_path(path: Path, base_dir: Path) -> str:
    resolved = path.resolve()
    base = base_dir.resolve()
    rel = os.path.relpath(resolved, base)
    if rel == ".." or rel.startswith(f"..{os.sep}"):
        return str(resolved)
    return rel


def _resolve_audit_log_path(
    manifest: AgentsShipgateManifest,
    baseline_path: Path,
) -> Path:
    """Resolve the baseline audit log path.

    Resolution order:
    1. ``manifest.baseline.audit_log`` if set (relative paths resolved
       against the baseline file's directory).
    2. Otherwise ``<baseline_path.parent>/baseline-audit.log`` —
       co-located with the baseline JSON. This matches the default that
       ``write_baseline`` uses, so save/verify see the same file
       without configuration.
    """
    override = manifest.baseline.audit_log
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = baseline_path.parent / candidate
        return candidate
    return baseline_path.parent / DEFAULT_AUDIT_LOG_PATH.name


def _check_metadata_lookup(
    *, plugins_enabled: bool | None
) -> dict:
    """Build a {check_id: CheckMetadata} lookup honoring the scan's
    actual plugin setting. Used by ``annotate_remediation`` so the
    serialized report's per-finding remediation fields reflect the
    catalog the scan was run against.

    Avoids the late-stage plugin-loading hazard: by passing the lookup
    *into* annotation, we never call ``check_catalog()`` at write time
    where ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1`` could re-load plugins
    even for ``--no-plugins`` scans.
    """
    from agents_shipgate.checks.registry import check_catalog

    return {
        check.id: check
        for check in check_catalog(plugins_enabled=plugins_enabled)
    }


def _attach_patches(
    findings: list,
    manifest,
    config_path: Path,
    *,
    plugins_enabled: bool | None,
) -> None:
    """Attach Patch objects to unsuppressed findings (per v0.6 plan §3).

    Suppressed findings are intentionally skipped — apply-patches must
    not mutate entries the user marked ignored.

    Coverage rule: every active finding gets ≥ 1 patch (non-manual when
    a generator exists, ManualPatch otherwise). Findings without
    --suggest-patches keep ``patches=None`` (per C4) and are filtered
    out of the JSON by ``report_json_payload``.

    Per the v0.7 PR 3 review: ``plugins_enabled`` is forwarded into
    ``check_catalog`` so the recommendation lookup honors the scan's
    explicit ``--no-plugins`` flag even when ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1``
    is set in the environment. Without this, the patch-attachment path
    would load third-party plugin entry points before
    ``annotate_remediation`` ran with its plugin-safe lookup.
    """
    from agents_shipgate.checks.patches import (
        PatchContext,
        generate_patches_for_finding,
    )
    from agents_shipgate.checks.registry import check_catalog

    recommendation_lookup = {
        check.id: check.recommendation
        for check in check_catalog(plugins_enabled=plugins_enabled)
        if check.recommendation
    }
    context = PatchContext(
        manifest=manifest,
        manifest_path=config_path,
        recommendation_lookup=recommendation_lookup,
    )
    for finding in findings:
        if finding.suppressed:
            continue
        finding.patches = generate_patches_for_finding(context, finding)


def _run_id(
    manifest,
    tools: list[Tool],
    findings,
    project: dict[str, object] | None = None,
    agent_name: str | None = None,
    environment: dict[str, object] | None = None,
    api_surface: dict[str, object] | None = None,
    anthropic_surface: dict[str, object] | None = None,
    frameworks: dict[str, object] | None = None,
    codex_plugin_surface: CodexPluginSurface | None = None,
    action_surface_facts: ActionSurfaceFacts | None = None,
) -> str:
    payload = {
        "project": project
        if project is not None
        else manifest.project.model_dump(mode="json", exclude_none=False),
        "agent_name": agent_name if agent_name is not None else manifest.agent.name,
        "environment": environment
        if environment is not None
        else manifest.environment.model_dump(mode="json", exclude_none=False),
        "tool_inventory": tool_inventory(tools),
        "findings": [
            finding.model_dump(
                mode="json",
                # Exclude derived-enrichment fields (per C11 + v0.7
                # review finding 2): patches and the four remediation
                # fields are computed AFTER the input surface is
                # known, so they MUST NOT enter the run_id hash. Two
                # scans of the same workspace must produce the same
                # run_id whether `--suggest-patches` is set or not, and
                # whether v0.7 metadata is present or not.
                exclude={
                    "id": True,
                    "baseline_status": True,
                    "patches": True,
                    "autofix_safe": True,
                    "requires_human_review": True,
                    "suggested_patch_kind": True,
                    "docs_url": True,
                    "blocks_release": True,
                    # v0.12 derived enrichment: same exclusion rule as
                    # the v0.7 remediation fields above. agent_action is
                    # a deterministic projection of those fields, so
                    # excluding them already implies it should be
                    # excluded — but make it explicit so a future
                    # contributor doesn't have to trace the projection.
                    "agent_action": True,
                    # v0.11 provenance fields are excluded so YAML line
                    # drift cannot churn run_id; the legacy
                    # type/ref/location strings stay in the hash so
                    # existing run_ids remain stable.
                    "source": {
                        "path": True,
                        "start_line": True,
                        "end_line": True,
                        "start_column": True,
                        "pointer": True,
                    },
                    # v0.19 reviewer-grade provenance: the secondary
                    # manifest pointer ``policy_evidence_source`` is
                    # excluded in its entirety. The whole field is
                    # additive (older scans never emitted it) and
                    # YAML line drift on the manifest must not churn
                    # run_id — same rationale as the v0.11 exclusion
                    # above.
                    "policy_evidence_source": True,
                },
                exclude_none=False,
            )
            for finding in findings
        ],
        "api_surface": api_surface,
        "anthropic_surface": anthropic_surface,
        "frameworks": frameworks or {},
        "codex_plugin_surface": (
            codex_plugin_surface.model_dump(mode="json") if codex_plugin_surface else None
        ),
        "action_surface_facts": (
            action_surface_facts.model_dump(mode="json")
            if action_surface_facts is not None
            else None
        ),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"agents_shipgate_{digest}"


def _frameworks_surface(
    adk_artifacts: GoogleAdkArtifacts | None,
    langchain_artifacts: LangChainArtifacts | None = None,
    crewai_artifacts: CrewAiArtifacts | None = None,
    n8n_artifacts: N8nArtifacts | None = None,
) -> dict[str, object]:
    surface: dict[str, object] = {}
    if adk_artifacts:
        surface["google_adk"] = adk_artifacts.surface_summary()
    if langchain_artifacts:
        surface["langchain"] = langchain_artifacts.surface_summary()
    if crewai_artifacts:
        surface["crewai"] = crewai_artifacts.surface_summary()
    if n8n_artifacts:
        surface["n8n"] = n8n_artifacts.surface_summary()
    return surface


def _build_public_action_surface_facts(
    *,
    raw_facts: ActionSurfaceFacts,
    manifest: AgentsShipgateManifest,
    agent_id: str,
    tools: list[Tool],
    stats: RedactionStats,
) -> ActionSurfaceFacts:
    try:
        return sanitize_model(
            build_action_surface_facts(
                manifest,
                agent_id=agent_id,
                tools=tools,
            ),
            ActionSurfaceFacts,
            stats=stats,
            path="action_surface_facts",
        )
    except ConfigError:
        logger.debug(
            "redacted action surface collapsed distinct raw action ids; "
            "falling back to a sanitized raw snapshot with public-only "
            "ordinal disambiguators"
        )
        return _sanitize_existing_action_surface_facts(
            raw_facts,
            stats=stats,
            path="action_surface_facts",
        )


def _sanitize_existing_action_surface_facts(
    facts: ActionSurfaceFacts,
    *,
    stats: RedactionStats,
    path: str,
) -> ActionSurfaceFacts:
    public_facts = sanitize_model(
        facts,
        ActionSurfaceFacts,
        stats=stats,
        path=path,
    )
    _disambiguate_public_action_ids(public_facts)
    return public_facts


def _disambiguate_public_action_ids(facts: ActionSurfaceFacts) -> None:
    seen: dict[str, int] = {}
    for action in facts.actions:
        count = seen.get(action.action_id, 0) + 1
        seen[action.action_id] = count
        if count > 1:
            action.action_id = f"{action.action_id}#{count}"
        _refresh_public_action_hashes(action)


def _refresh_public_action_hashes(action: ActionFact) -> None:
    schema_hash = _stable_hash(
        {
            "input_fields": action.input_fields,
            "required_input_fields": action.required_input_fields,
        }
    )
    policy_hash = _stable_hash(
        {
            "approval": action.approval_policy.model_dump(mode="json"),
            "safeguards": action.safeguards.model_dump(mode="json"),
            "evidence": action.evidence.model_dump(mode="json"),
        }
    )
    risk_hash = _stable_hash(
        {
            "effect": action.effect,
            "risk_tags": action.risk_tags,
            "required_scopes": action.required_scopes,
        }
    )
    action.input_schema_hash = schema_hash
    action.hashes = ActionSurfaceHashes(
        identity_hash=_stable_hash(action.action_id),
        schema_hash=schema_hash,
        policy_hash=policy_hash,
        risk_hash=risk_hash,
    )


def _sanitize_codex_plugin_surface(
    surface: CodexPluginSurface | None,
    *,
    stats: RedactionStats,
) -> CodexPluginSurface | None:
    if surface is None:
        return None
    return sanitize_model(
        surface,
        CodexPluginSurface,
        stats=stats,
        path="codex_plugin_surface",
    )


def _sanitize_diff_reference(
    reference: ToolSurfaceDiffReference | None,
    *,
    stats: RedactionStats,
) -> ToolSurfaceDiffReference | None:
    if reference is None:
        return None
    facts = (
        sanitize_model(
            reference.facts,
            ToolSurfaceFacts,
            stats=stats,
            path="tool_surface_diff.base.facts",
        )
        if reference.facts is not None
        else None
    )
    action_facts = (
        _sanitize_existing_action_surface_facts(
            reference.action_facts,
            stats=stats,
            path="action_surface_diff.base.facts",
        )
        if reference.action_facts is not None
        else None
    )
    findings = (
        [
            item.__class__.model_validate(
                redact_data(
                    item.model_dump(mode="python"),
                    stats=stats,
                    path="tool_surface_diff.base.findings[]",
                )
            )
            for item in reference.findings
        ]
        if reference.findings is not None
        else None
    )
    return ToolSurfaceDiffReference(
        kind=reference.kind,
        facts=facts,
        path=redact_data(reference.path, stats=stats, path="tool_surface_diff.base.path"),
        report_schema_version=reference.report_schema_version,
        baseline_schema_version=reference.baseline_schema_version,
        action_facts=action_facts,
        findings=findings,
        notes=tuple(
            redact_data(
                list(reference.notes),
                stats=stats,
                path="tool_surface_diff.base.notes[]",
            )
        ),
        action_notes=tuple(
            redact_data(
                list(reference.action_notes),
                stats=stats,
                path="action_surface_diff.base.notes[]",
            )
        ),
    )


def _default_baseline_status(base_dir: Path) -> dict[str, object]:
    path = base_dir / ".agents-shipgate" / "baseline.json"
    return {
        "default_path": _relative_display_path(path, base_dir),
        "present": path.exists(),
    }
