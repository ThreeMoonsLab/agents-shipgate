from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from agents_shipgate.checks.registry import run_checks
from agents_shipgate.ci.exit_policy import exit_code_for_report
from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.baseline import apply_baseline, load_baseline
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.findings import (
    apply_severity_overrides,
    apply_suppressions,
    assign_finding_ids,
    build_report,
    tool_inventory,
)
from agents_shipgate.core.models import (
    Agent,
    LoadedToolSource,
    OpenAIApiArtifacts,
    ReadinessReport,
    Tool,
    parse_severity,
)
from agents_shipgate.core.risk_hints import enrich_tools_with_risk_hints
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openai_api import load_openai_api_artifacts
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.report.json_report import write_json_report
from agents_shipgate.report.markdown import write_markdown_report

logger = logging.getLogger(__name__)


def run_scan(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    formats: list[str] | None = None,
    ci_mode: str | None = None,
    fail_on: list[str] | None = None,
    baseline_path: Path | None = None,
    baseline_mode: str = "new-findings",
    deep_import: bool = False,
    plugins_enabled: bool | None = None,
    verbose: bool = False,
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
    if baseline_mode != "new-findings":
        raise ConfigError("--baseline-mode currently supports only new-findings")

    base_dir = config_path.resolve().parent
    loaded_sources = _load_sources(manifest, base_dir, verbose=verbose)
    api_source, api_artifacts = load_openai_api_artifacts(manifest.openai_api, base_dir)
    if api_source:
        loaded_sources.append(api_source)
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
    agent = _build_agent(manifest, tools, api_artifacts)
    context = ScanContext(
        manifest=manifest,
        agent=agent,
        tools=tools,
        config_path=config_path.resolve(),
        api_artifacts=api_artifacts,
    )
    loaded_plugins: list[dict[str, str | None]] = []
    findings = run_checks(
        context,
        plugins_enabled=plugins_enabled,
        loaded_plugins=loaded_plugins,
    )
    assign_finding_ids(findings)
    apply_severity_overrides(findings, manifest.severity_overrides())
    apply_suppressions(findings, manifest.checks.ignore)
    baseline_summary = None
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
    generated_paths = _planned_generated_paths(out_dir, manifest.output.formats)
    report = build_report(
        run_id=_run_id(
            manifest,
            tools,
            findings,
            api_surface=api_artifacts.surface_summary() if api_artifacts else None,
        ),
        manifest=manifest,
        agent=agent.model_dump(exclude_none=True),
        environment=manifest.environment.model_dump(exclude_none=True),
        tools=tools,
        findings=findings,
        generated_reports={
            key: _relative_display_path(path, base_dir)
            for key, path in generated_paths.items()
        },
        loaded_plugins=loaded_plugins,
        source_warnings=warnings,
        api_surface=api_artifacts.surface_summary() if api_artifacts else None,
        baseline=baseline_summary,
    )
    _write_reports(report, generated_paths, manifest.output.formats)
    write_github_step_summary(report)
    return report, exit_code_for_report(
        report,
        manifest.ci.mode,
        fail_on=manifest.ci.fail_on,
        new_findings_only=baseline_summary is not None,
    )


def inspect_sources(*, config_path: Path, verbose: bool = False) -> dict[str, object]:
    manifest = load_manifest(config_path)
    base_dir = config_path.resolve().parent
    loaded_sources = _load_sources(manifest, base_dir, verbose=verbose)
    api_source, api_artifacts = load_openai_api_artifacts(manifest.openai_api, base_dir)
    if api_source:
        loaded_sources.append(api_source)
    tools, duplicate_warnings = _flatten_and_deduplicate_tools(loaded_sources)
    warnings = [warning for loaded in loaded_sources for warning in loaded.warnings]
    warnings.extend(duplicate_warnings)
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
        "baseline": _default_baseline_status(base_dir),
        "warnings": warnings,
    }


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
    by_name: dict[str, Tool] = {}
    warnings: list[str] = []
    for loaded in loaded_sources:
        for tool in loaded.tools:
            existing = by_name.get(tool.name)
            if not existing:
                by_name[tool.name] = tool
                continue
            if _source_priority(tool) > _source_priority(existing):
                by_name[tool.name] = tool
                kept, dropped = tool, existing
            else:
                kept, dropped = existing, tool
            warnings.append(
                "Duplicate tool name "
                f"{tool.name!r}; kept {kept.source_type} source {kept.source_id!r} "
                f"and ignored {dropped.source_type} source {dropped.source_id!r}."
            )
    return list(by_name.values()), warnings


def _source_priority(tool: Tool) -> int:
    return {
        "openai_api": 40,
        "openapi": 30,
        "mcp": 20,
        "sdk_function": 10,
    }.get(tool.source_type, 0)


def _build_agent(
    manifest, tools: list[Tool], api_artifacts: OpenAIApiArtifacts | None = None
) -> Agent:
    sdk = manifest.agent.sdk
    instructions_preview = manifest.agent.instructions_preview
    instruction_source = "config" if instructions_preview else "dynamic_unknown"
    instruction_confidence = "high" if instructions_preview else "medium"
    if not instructions_preview and api_artifacts and api_artifacts.prompt_text:
        instructions_preview = api_artifacts.prompt_text[:500]
        instruction_source = "openai_api_prompt_files"
        instruction_confidence = "high"
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


def _planned_generated_paths(out_dir: Path, formats: list[str]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    if "markdown" in formats:
        paths["markdown"] = out_dir / "report.md"
    if "json" in formats:
        paths["json"] = out_dir / "report.json"
    return paths


def _write_reports(
    report: ReadinessReport, paths: dict[str, Path], formats: list[str]
) -> None:
    if "markdown" in formats and "markdown" in paths:
        write_markdown_report(report, paths["markdown"])
    if "json" in formats and "json" in paths:
        write_json_report(report, paths["json"])


def _relative_display_path(path: Path, base_dir: Path) -> str:
    resolved = path.resolve()
    base = base_dir.resolve()
    rel = os.path.relpath(resolved, base)
    if rel == ".." or rel.startswith(f"..{os.sep}"):
        return str(resolved)
    return rel


def _run_id(
    manifest,
    tools: list[Tool],
    findings,
    api_surface: dict[str, object] | None = None,
) -> str:
    payload = {
        "project": manifest.project.model_dump(mode="json", exclude_none=False),
        "agent_name": manifest.agent.name,
        "environment": manifest.environment.model_dump(mode="json", exclude_none=False),
        "tool_inventory": tool_inventory(tools),
        "findings": [
            finding.model_dump(
                mode="json",
                exclude={"id", "baseline_status"},
                exclude_none=False,
            )
            for finding in findings
        ],
        "api_surface": api_surface,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"agents_shipgate_{digest}"


def _default_baseline_status(base_dir: Path) -> dict[str, object]:
    path = base_dir / ".agents-shipgate" / "baseline.json"
    return {
        "default_path": _relative_display_path(path, base_dir),
        "present": path.exists(),
    }
