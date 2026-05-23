from __future__ import annotations

import logging
from pathlib import Path

from agents_shipgate.packet.html import write_packet_html
from agents_shipgate.packet.json_packet import write_packet_json
from agents_shipgate.packet.markdown import write_packet_markdown
from agents_shipgate.packet.pdf import (
    PdfRendererUnavailable,
    is_pdf_available,
    render_packet_pdf,
)
from agents_shipgate.report.json_report import write_json_report
from agents_shipgate.report.markdown import write_markdown_report
from agents_shipgate.report.sarif import write_sarif_report
from agents_shipgate.schemas.report import ReadinessReport

PACKET_FORMAT_NAMES = {"md", "json", "html", "pdf"}
"""Allowed values for ``--packet-format`` and ``output.packet.formats``."""

logger = logging.getLogger(__name__)

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
