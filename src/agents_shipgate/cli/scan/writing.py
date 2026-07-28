from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.ci.release_decision import (
    SUGGESTED_DECLARATIONS_FILENAME,
    SUGGESTED_INVENTORY_FILENAME,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.privacy import sanitize_packet
from agents_shipgate.packet.builder import build_packet
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import ReadinessReport

from .declarations import scaffold_for_report
from .models import _OutputPlan, _SanitizedSurfaces
from .output_helpers import _write_packet, _write_reports


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
    public_report = ReadinessReport.model_validate(public_report_payload)
    _write_reports(
        public_report,
        plan.generated_paths,
        manifest.output.formats,
        sanitized_payload=public_report_payload,
    )
    _write_suggested_inventory(
        sanitized_tools=sanitized.tools,
        generated_paths=plan.generated_paths,
    )
    _write_suggested_declarations(
        report=public_report,
        generated_paths=plan.generated_paths,
    )
    if manifest.output.packet.enabled and plan.packet_format_set:
        assert report.release_decision is not None
        packet = build_packet(
            manifest=manifest,
            agent=public_report.agent,
            project=public_report.project,
            environment=public_report.environment,
            run_id=public_report.run_id,
            tools=sanitized.tools,
            findings=sanitized.findings,
            release_decision=public_report.release_decision,
            api_artifacts=sanitized.api_artifacts,
            anthropic_artifacts=sanitized.anthropic_artifacts,
            source_warnings=sanitized.source_warnings,
            validation_artifacts=sanitized.validation_artifacts,
            tool_surface_diff=public_report.tool_surface_diff,
            action_surface_diff=public_report.action_surface_diff,
            report_payload=public_report_payload,
            capability_runtime_evidence=public_report.capability_runtime_evidence,
            generated_at=packet_generated_at,
            config_ref=config_path.resolve().name,
        )
        _write_packet(
            sanitize_packet(packet),
            plan.generated_paths,
            plan.packet_format_set,
        )


def _write_suggested_declarations(
    *,
    report: ReadinessReport,
    generated_paths: dict[str, Path],
) -> None:
    """Advisory scaffold for the gaps only a human declaration can close.

    Written next to report.json whenever any evidence gap carries a
    ``declaration_template``. A pure projection of the decision engine's own
    output: it asserts nothing, decides nothing, and every human-owned value
    stays ``<REVIEW_REQUIRED>``. Consumes the sanitized public report so no
    redacted content reaches the scaffold.
    """

    anchor = generated_paths.get("json") or next(iter(generated_paths.values()), None)
    if anchor is None:
        return
    scaffold = scaffold_for_report(report)
    if scaffold is None:
        return
    out_path = anchor.parent / SUGGESTED_DECLARATIONS_FILENAME
    out_path.write_text(scaffold, encoding="utf-8")


def _write_suggested_inventory(
    *,
    sanitized_tools: list[Tool],
    generated_paths: dict[str, Path],
) -> None:
    """v0.26: advisory tool-inventory skeleton for low-confidence sources.

    Written next to report.json whenever low-confidence tools exist, in
    the MCP-export shape every ``tool_inventories`` manifest key loads
    (``load_mcp_tools`` ignores the top-level ``note``). The
    ``evidence_gaps`` rows in ``release_decision.evidence_coverage``
    reference this file by name. Consumes only sanitized tools so no
    redacted content can leak into the skeleton. Deterministic: sorted
    by (name, source_type), stable JSON.
    """
    anchor = generated_paths.get("json") or next(
        iter(generated_paths.values()), None
    )
    if anchor is None:
        return
    low_confidence = sorted(
        (tool for tool in sanitized_tools if tool.extraction_confidence != "high"),
        key=lambda tool: (tool.name, tool.source_type),
    )
    if not low_confidence:
        return
    entries = []
    for tool in low_confidence:
        entry: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description
            or "TODO: describe what this tool does and what it can touch.",
        }
        if tool.input_schema:
            entry["input_schema"] = tool.input_schema
        entries.append(entry)
    payload = {
        "note": (
            "Skeleton tool inventory generated by agents-shipgate scan for "
            "sources static extraction could not fully enumerate. Review "
            "and complete each entry, save the file in your repo, and "
            "reference it from the matching tool_inventories key in "
            "shipgate.yaml (see "
            "release_decision.evidence_coverage.evidence_gaps)."
        ),
        "tools": entries,
    }
    out_path = anchor.parent / SUGGESTED_INVENTORY_FILENAME
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
