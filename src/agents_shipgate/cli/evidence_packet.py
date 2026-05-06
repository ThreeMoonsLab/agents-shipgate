"""``agents-shipgate evidence-packet`` — re-render a packet from a
previously-built ``packet.json`` into additional formats.

Does **not** rebuild the packet from a scan report. The packet is
authored during ``scan`` (where the in-memory manifest and per-source
artifacts are available); this command's job is purely to re-emit the
already-built JSON in markdown, html, or pdf form. Useful for:

- regenerating ``packet.pdf`` after installing the ``[pdf]`` extras
- rendering a CI-archived ``packet.json`` outside the source workspace

Exit codes:

- 0 — render(s) completed successfully.
- 2 — ``--from`` payload missing, malformed, or wrong schema version.
- 4 — internal error.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from agents_shipgate.packet import (
    EvidencePacket,
    PacketSchemaError,
    PdfRendererUnavailable,
    load_packet_json,
    render_packet_pdf,
    serialize_packet_json,
)
from agents_shipgate.packet.html import write_packet_html
from agents_shipgate.packet.markdown import write_packet_markdown

_DEFAULT_FORMATS = "md,html"
_VALID_FORMATS = {"md", "html", "pdf"}


def evidence_packet(
    from_path: Path = typer.Option(
        ...,
        "--from",
        help="Path to an existing packet.json (built by `agents-shipgate scan`).",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory. Defaults to the directory of --from.",
    ),
    formats: str = typer.Option(
        _DEFAULT_FORMATS,
        "--format",
        help=(
            "Comma-separated render targets: md,html,pdf. "
            "Default: md,html. JSON is the input and is not re-emitted."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Echo the loaded packet.json content to stdout.",
    ),
) -> None:
    """Re-render a packet from packet.json."""

    try:
        payload = from_path.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Cannot read packet at {from_path}: {exc}", err=True)
        raise typer.Exit(2) from exc

    try:
        packet = load_packet_json(payload)
    except PacketSchemaError as exc:
        typer.echo(f"Invalid packet.json: {exc}", err=True)
        raise typer.Exit(2) from exc

    if json_output:
        typer.echo(json.dumps(serialize_packet_json(packet), indent=2, sort_keys=True))
        return

    requested = _parse_formats(formats)
    out_dir = (out or from_path.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if "md" in requested:
        md_path = out_dir / "packet.md"
        write_packet_markdown(packet, md_path)
        written.append(md_path)
    if "html" in requested:
        html_path = out_dir / "packet.html"
        write_packet_html(packet, html_path)
        written.append(html_path)
    if "pdf" in requested:
        pdf_path = out_dir / "packet.pdf"
        try:
            render_packet_pdf(packet, pdf_path)
        except PdfRendererUnavailable as exc:
            typer.echo(f"packet.pdf skipped: {exc}", err=True)
        else:
            written.append(pdf_path)

    if not written:
        typer.echo(
            "No outputs written. Pass at least one of md,html,pdf in --format.",
            err=True,
        )
        raise typer.Exit(2)
    for path in written:
        typer.echo(f"Wrote {path}")


def _parse_formats(value: str) -> set[str]:
    parts = {item.strip() for item in value.split(",") if item.strip()}
    invalid = parts - _VALID_FORMATS
    if invalid:
        typer.echo(
            f"Unsupported --format value(s): {sorted(invalid)}; "
            "expected a subset of md,html,pdf",
            err=True,
        )
        raise typer.Exit(2)
    if not parts:
        typer.echo("--format must contain at least one of md,html,pdf", err=True)
        raise typer.Exit(2)
    return parts


__all__ = ["evidence_packet", "EvidencePacket"]
