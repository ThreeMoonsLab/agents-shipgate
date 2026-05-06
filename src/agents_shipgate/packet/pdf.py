"""PDF renderer for the Release Evidence Packet.

WeasyPrint is an optional dependency installed via the ``[pdf]``
extras. The import is lazy so vanilla installs never pay the cost (or
trip on missing system libraries). Callers catch
``PdfRendererUnavailable`` and downgrade to a one-line warning when
the dep is missing.
"""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.packet.html import render_packet_html
from agents_shipgate.packet.models import EvidencePacket


class PdfRendererUnavailable(RuntimeError):
    """WeasyPrint (or its system deps) cannot be imported. The CLI
    catches this and emits a single warning line; the scan continues
    and returns the same exit code it would have without ``pdf`` in
    ``--packet-format``.
    """


def render_packet_pdf(packet: EvidencePacket, out_path: Path) -> Path:
    """Render the packet as a PDF and write it to ``out_path``.

    Returns the resolved output path. Raises
    ``PdfRendererUnavailable`` if WeasyPrint is not installed.
    """

    try:
        from weasyprint import HTML  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 - import errors include OSError on missing libs
        raise PdfRendererUnavailable(
            "weasyprint is not installed; run "
            "`pipx install 'agents-shipgate[pdf]'` to enable PDF output"
        ) from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_str = render_packet_html(packet)
    HTML(string=html_str).write_pdf(str(out_path))
    return out_path


def is_pdf_available() -> bool:
    """Probe whether the PDF renderer is importable on this install.

    Used by ``cli/scan.py`` during the path-planning phase so
    ``report.generated_reports`` only references files that will
    actually be written.
    """

    try:
        import weasyprint  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True
