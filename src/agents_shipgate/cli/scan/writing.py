from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_shipgate.packet.builder import build_packet
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import ReadinessReport

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
            capability_runtime_evidence=report.capability_runtime_evidence,
            generated_at=packet_generated_at,
            config_ref=config_path.resolve().name,
        )
        _write_packet(packet, plan.generated_paths, plan.packet_format_set)
