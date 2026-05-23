from __future__ import annotations

import logging
import os
from pathlib import Path

from agents_shipgate.core.privacy import RedactionStats, redact_data
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

from .models import _OutputPlan
from .output_helpers import _planned_generated_paths, _resolve_packet_format_set
from .path_helpers import _relative_display_path

logger = logging.getLogger(__name__)

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
