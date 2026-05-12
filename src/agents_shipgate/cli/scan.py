from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from agents_shipgate.checks.registry import run_checks
from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.baseline import apply_baseline, load_baseline
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.findings import (
    annotate_remediation,
    apply_severity_overrides,
    apply_suppressions,
    assign_finding_ids,
    build_report,
    tool_inventory,
)
from agents_shipgate.core.models import (
    Agent,
    AnthropicArtifacts,
    CodexPluginSurface,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    LoadedToolSource,
    N8nArtifacts,
    OpenAIApiArtifacts,
    ReadinessReport,
    Tool,
    parse_severity,
)
from agents_shipgate.core.risk_hints import enrich_tools_with_risk_hints
from agents_shipgate.inputs.anthropic_api import load_anthropic_artifacts
from agents_shipgate.inputs.codex_plugin import load_codex_plugin_artifacts
from agents_shipgate.inputs.frameworks import load_framework_artifacts
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openai_api import load_openai_api_artifacts
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.inputs.policy_packs import load_policy_packs, run_policy_pack_rules
from agents_shipgate.inputs.validation import load_validation_artifacts
from agents_shipgate.packet.builder import build_packet
from agents_shipgate.packet.html import write_packet_html
from agents_shipgate.packet.json_packet import write_packet_json
from agents_shipgate.packet.markdown import write_packet_markdown
from agents_shipgate.packet.pdf import (
    PdfRendererUnavailable,
    is_pdf_available,
    render_packet_pdf,
)
from agents_shipgate.report.capability_diff import apply_capability_diff
from agents_shipgate.report.json_report import write_json_report
from agents_shipgate.report.markdown import write_markdown_report
from agents_shipgate.report.sarif import write_sarif_report
from agents_shipgate.report.tool_surface_diff import (
    build_tool_surface_facts,
    compute_tool_surface_diff,
    disabled_tool_surface_diff,
    load_tool_surface_diff_reference,
    reference_from_baseline,
)

PACKET_FORMAT_NAMES = {"md", "json", "html", "pdf"}
"""Allowed values for ``--packet-format`` and ``output.packet.formats``."""

logger = logging.getLogger(__name__)


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
    if deep_import:
        raise ConfigError("Deep import is intentionally deferred and is not supported.")

    manifest = load_manifest(config_path).model_copy(deep=True)
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

    base_dir = config_path.resolve().parent
    loaded_sources = _load_sources(manifest, base_dir, verbose=verbose)
    framework_result = load_framework_artifacts(manifest, base_dir)
    adk_artifacts = framework_result.adk_artifacts
    langchain_artifacts = framework_result.langchain_artifacts
    crewai_artifacts = framework_result.crewai_artifacts
    n8n_artifacts = framework_result.n8n_artifacts
    loaded_sources.extend(framework_result.loaded_sources)
    api_source, api_artifacts = load_openai_api_artifacts(manifest.openai_api, base_dir)
    if api_source:
        loaded_sources.append(api_source)
    anthropic_source, anthropic_artifacts = load_anthropic_artifacts(
        manifest.anthropic, base_dir
    )
    if anthropic_source:
        loaded_sources.append(anthropic_source)
    codex_plugin_sources, codex_plugin_artifacts = load_codex_plugin_artifacts(
        manifest, base_dir
    )
    loaded_sources.extend(codex_plugin_sources)
    validation_artifacts = load_validation_artifacts(manifest.validation, base_dir)
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
    tools, duplicate_warnings = _flatten_and_deduplicate_tools(loaded_sources)
    warnings = [warning for loaded in loaded_sources for warning in loaded.warnings]
    warnings.extend(duplicate_warnings)
    if adk_artifacts:
        warnings.extend(adk_artifacts.warnings)
    if langchain_artifacts:
        warnings.extend(langchain_artifacts.warnings)
    if crewai_artifacts:
        warnings.extend(crewai_artifacts.warnings)
    if codex_plugin_artifacts:
        warnings.extend(codex_plugin_artifacts.warnings)
    if n8n_artifacts:
        warnings.extend(n8n_artifacts.warnings)
    if validation_artifacts:
        warnings.extend(validation_artifacts.warnings)
    policy_packs = load_policy_packs(
        manifest,
        base_dir,
        cli_policy_packs=policy_pack_paths,
    )
    warnings.extend(policy_packs.warnings)
    warnings = list(dict.fromkeys(warnings))
    tools = enrich_tools_with_risk_hints(manifest, tools)
    logger.debug(
        "risk hints generated",
        extra={
            "agents_shipgate_tools": [
                {
                    "name": tool.name,
                    "risk_hints": [
                        {
                            "tag": hint.tag,
                            "confidence": hint.confidence,
                            "source": hint.source,
                        }
                        for hint in tool.risk_hints
                    ],
                }
                for tool in tools
            ]
        },
    )
    agent = _build_agent(
        manifest,
        tools,
        api_artifacts,
        anthropic_artifacts,
        adk_artifacts,
    )
    context = ScanContext(
        manifest=manifest,
        agent=agent,
        tools=tools,
        config_path=config_path.resolve(),
        api_artifacts=api_artifacts,
        anthropic_artifacts=anthropic_artifacts,
        adk_artifacts=adk_artifacts,
        langchain_artifacts=langchain_artifacts,
        crewai_artifacts=crewai_artifacts,
        codex_plugin_artifacts=codex_plugin_artifacts,
        n8n_artifacts=n8n_artifacts,
        validation_artifacts=validation_artifacts,
    )
    loaded_plugins: list[dict[str, str | None]] = []
    findings = run_checks(
        context,
        plugins_enabled=plugins_enabled,
        loaded_plugins=loaded_plugins,
        extra_known_check_ids={resolved.rule.id for resolved in policy_packs.rules},
    )
    findings.extend(run_policy_pack_rules(context, policy_packs))
    assign_finding_ids(findings)
    apply_severity_overrides(findings, manifest.severity_overrides())
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
    # the values. Built with the scan's actual plugin setting so
    # serialization never re-loads plugins.
    annotate_remediation(
        findings,
        _check_metadata_lookup(plugins_enabled=plugins_enabled),
    )
    baseline_summary = None
    baseline_file = None
    if baseline_path:
        baseline_file = load_baseline(baseline_path)
        baseline_summary = apply_baseline(
            findings,
            baseline_file,
            display_path=_relative_display_path(baseline_path, base_dir),
        )
    logger.debug(
        "checks completed",
        extra={
            "agents_shipgate_finding_count": len(findings),
            "agents_shipgate_suppressed_count": sum(
                1 for finding in findings if finding.suppressed
            ),
        },
    )

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
    anthropic_surface = (
        anthropic_artifacts.surface_summary() if anthropic_artifacts else None
    )
    frameworks_surface = _frameworks_surface(
        adk_artifacts,
        langchain_artifacts,
        crewai_artifacts,
        n8n_artifacts,
    )
    tool_surface_facts = build_tool_surface_facts(
        manifest,
        tools,
        findings,
        api_artifacts,
        anthropic_artifacts,
    )
    try:
        if diff_from_path:
            diff_reference = load_tool_surface_diff_reference(
                diff_from_path,
                display_path=_relative_display_path(diff_from_path, base_dir),
            )
        elif baseline_file:
            diff_reference = reference_from_baseline(
                baseline_file,
                display_path=baseline_summary.path if baseline_summary else None,
            )
        else:
            diff_reference = None
        tool_surface_diff = compute_tool_surface_diff(
            tool_surface_facts,
            diff_reference.facts if diff_reference else None,
            findings,
            reference=diff_reference,
        )
    except InputParseError as exc:
        tool_surface_diff = disabled_tool_surface_diff(str(exc))
    report = build_report(
        run_id=_run_id(
            manifest,
            tools,
            findings,
            api_surface=api_artifacts.surface_summary() if api_artifacts else None,
            anthropic_surface=anthropic_surface,
            frameworks=frameworks_surface,
            codex_plugin_surface=(
                codex_plugin_artifacts.surface_summary()
                if codex_plugin_artifacts
                else None
            ),
        ),
        manifest=manifest,
        manifest_dir=str(config_path.resolve().parent),
        agent=agent.model_dump(exclude_none=True),
        environment=manifest.environment.model_dump(exclude_none=True),
        tools=tools,
        findings=findings,
        generated_reports={
            key: _relative_display_path(path, base_dir)
            for key, path in generated_paths.items()
        },
        ci_mode=manifest.ci.mode,
        fail_on=manifest.ci.fail_on,
        new_findings_only=baseline_summary is not None,
        loaded_policy_packs=policy_packs.loaded,
        loaded_plugins=loaded_plugins,
        source_warnings=warnings,
        api_surface=api_artifacts.surface_summary() if api_artifacts else None,
        anthropic_surface=anthropic_surface,
        frameworks=frameworks_surface,
        codex_plugin_surface=(
            codex_plugin_artifacts.surface_summary() if codex_plugin_artifacts else None
        ),
        baseline=baseline_summary,
        tool_surface_facts=tool_surface_facts,
        tool_surface_diff=tool_surface_diff,
    )
    apply_capability_diff(report, tools)
    _write_reports(report, generated_paths, manifest.output.formats)
    if packet_cfg.enabled and packet_format_set:
        assert report.release_decision is not None
        packet = build_packet(
            manifest=manifest,
            agent=report.agent,
            project=report.project,
            environment=report.environment,
            run_id=report.run_id,
            tools=tools,
            findings=findings,
            release_decision=report.release_decision,
            api_artifacts=api_artifacts,
            anthropic_artifacts=anthropic_artifacts,
            source_warnings=warnings,
            validation_artifacts=validation_artifacts,
            tool_surface_diff=report.tool_surface_diff,
            generated_at=packet_generated_at,
            config_ref=config_path.resolve().name,
        )
        _write_packet(packet, generated_paths, packet_format_set)
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
    loaded_sources = _load_sources(manifest, base_dir, verbose=verbose)
    framework_result = load_framework_artifacts(manifest, base_dir)
    adk_artifacts = framework_result.adk_artifacts
    langchain_artifacts = framework_result.langchain_artifacts
    crewai_artifacts = framework_result.crewai_artifacts
    n8n_artifacts = framework_result.n8n_artifacts
    loaded_sources.extend(framework_result.loaded_sources)
    api_source, api_artifacts = load_openai_api_artifacts(manifest.openai_api, base_dir)
    if api_source:
        loaded_sources.append(api_source)
    anthropic_source, anthropic_artifacts = load_anthropic_artifacts(
        manifest.anthropic, base_dir
    )
    if anthropic_source:
        loaded_sources.append(anthropic_source)
    codex_plugin_sources, codex_plugin_artifacts = load_codex_plugin_artifacts(
        manifest, base_dir
    )
    loaded_sources.extend(codex_plugin_sources)
    validation_artifacts = load_validation_artifacts(manifest.validation, base_dir)
    tools, duplicate_warnings = _flatten_and_deduplicate_tools(loaded_sources)
    warnings = [warning for loaded in loaded_sources for warning in loaded.warnings]
    warnings.extend(duplicate_warnings)
    if adk_artifacts:
        warnings.extend(adk_artifacts.warnings)
    if langchain_artifacts:
        warnings.extend(langchain_artifacts.warnings)
    if crewai_artifacts:
        warnings.extend(crewai_artifacts.warnings)
    if codex_plugin_artifacts:
        warnings.extend(codex_plugin_artifacts.warnings)
    if n8n_artifacts:
        warnings.extend(n8n_artifacts.warnings)
    if validation_artifacts:
        warnings.extend(validation_artifacts.warnings)
    policy_packs = load_policy_packs(manifest, base_dir)
    warnings.extend(policy_packs.warnings)
    warnings = list(dict.fromkeys(warnings))
    return {
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


def _load_sources(manifest, base_dir: Path, *, verbose: bool) -> list[LoadedToolSource]:
    loaded: list[LoadedToolSource] = []
    for source in manifest.tool_sources:
        try:
            if source.type == "mcp":
                loaded.append(load_mcp_tools(source, base_dir))
            elif source.type == "openapi":
                loaded.append(load_openapi_tools(source, base_dir))
            elif source.type == "openai_agents_sdk":
                loaded.append(load_openai_sdk_static_tools(source, manifest, base_dir))
            elif source.type == "google_adk":
                # Google ADK sources are loaded with framework artifacts below.
                continue
            elif source.type in {"langchain", "crewai"}:
                # Framework sources are loaded with framework artifacts below.
                continue
            elif source.type == "codex_plugin":
                # Codex plugin packages are loaded with plugin artifacts above.
                continue
        except InputParseError:
            if source.optional:
                warning = f"Optional source {source.id} failed to load"
                if verbose:
                    warning = (
                        f"{warning}; continuing because the source is marked optional"
                    )
                loaded.append(
                    LoadedToolSource(
                        source_id=source.id,
                        source_type=source.type,
                        warnings=[warning],
                    )
                )
                continue
            raise
    return loaded


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
    api_surface: dict[str, object] | None = None,
    anthropic_surface: dict[str, object] | None = None,
    frameworks: dict[str, object] | None = None,
    codex_plugin_surface: CodexPluginSurface | None = None,
) -> str:
    payload = {
        "project": manifest.project.model_dump(mode="json", exclude_none=False),
        "agent_name": manifest.agent.name,
        "environment": manifest.environment.model_dump(mode="json", exclude_none=False),
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


def _default_baseline_status(base_dir: Path) -> dict[str, object]:
    path = base_dir / ".agents-shipgate" / "baseline.json"
    return {
        "default_path": _relative_display_path(path, base_dir),
        "present": path.exists(),
    }
