"""``agents-shipgate evidence-packet`` — render a packet from an
existing scan artifact (``packet.json`` or ``report.json``).

Two input modes:

- **``packet.json``** (preferred): re-renders the existing packet into
  md/html/pdf. The full-fidelity path — preserves §4/§5/§6/§8 declared
  coverage from the original scan.
- **``report.json``**: rebuilds a degraded packet from the report.
  Useful when only the CI-archived report is on hand (the manifest is
  no longer available). §10 carries an explicit note that declared
  coverage is incomplete; reviewers are pointed at re-running scan for
  full fidelity.

Either way, this command does not invoke checks, scan code, or call
out to a model. Pure JSON in, files out.

Exit codes:

- 0 — render(s) completed successfully.
- 2 — ``--from`` payload missing, malformed, or unrecognised schema.
- 4 — internal error.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from pydantic import ValidationError

from agents_shipgate.core.models import ReadinessReport
from agents_shipgate.packet import (
    EvidencePacket,
    PacketSchemaError,
    PdfRendererUnavailable,
    build_packet_from_report,
    load_packet_json,
    render_packet_pdf,
    serialize_packet_json,
)
from agents_shipgate.packet.html import write_packet_html
from agents_shipgate.packet.json_packet import write_packet_json
from agents_shipgate.packet.markdown import write_packet_markdown

_DEFAULT_FORMATS = "md,json,html"
_VALID_FORMATS = {"md", "json", "html", "pdf"}


def evidence_packet(
    from_path: Path = typer.Option(
        ...,
        "--from",
        help=(
            "Path to packet.json (preferred) or report.json. report.json "
            "produces a degraded packet — see §10 of the output."
        ),
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
            "Comma-separated render targets: md,json,html,pdf. "
            "Default: md,json,html. ``json`` writes packet.json — useful "
            "when the input is report.json (rebuild) or after upgrading "
            "the packet schema version. PDF requires the [pdf] extras."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Echo the resolved packet.json content to stdout.",
    ),
) -> None:
    """Render a packet from packet.json or report.json."""

    try:
        payload = from_path.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Cannot read input at {from_path}: {exc}", err=True)
        raise typer.Exit(2) from exc

    try:
        packet = _load_packet_or_report(payload)
    except PacketSchemaError as exc:
        typer.echo(f"Invalid input: {exc}", err=True)
        raise typer.Exit(2) from exc

    if json_output:
        typer.echo(json.dumps(serialize_packet_json(packet), indent=2, sort_keys=True))
        return

    requested = _parse_formats(formats)
    out_dir = (out or from_path.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    pdf_gracefully_skipped = False
    if "md" in requested:
        md_path = out_dir / "packet.md"
        write_packet_markdown(packet, md_path)
        written.append(md_path)
    if "json" in requested:
        json_path = out_dir / "packet.json"
        write_packet_json(packet, json_path)
        written.append(json_path)
    if "html" in requested:
        html_path = out_dir / "packet.html"
        write_packet_html(packet, html_path)
        written.append(html_path)
    if "pdf" in requested:
        pdf_path = out_dir / "packet.pdf"
        try:
            render_packet_pdf(packet, pdf_path)
        except PdfRendererUnavailable as exc:
            # PDF is opt-in via the [pdf] extras; missing WeasyPrint is
            # a documented graceful-skip case, not an error. The scan
            # path treats this the same way.
            typer.echo(f"packet.pdf skipped: {exc}", err=True)
            pdf_gracefully_skipped = True
        else:
            written.append(pdf_path)

    if not written:
        if pdf_gracefully_skipped:
            # User requested only ``pdf`` and WeasyPrint is unavailable.
            # The scan path stays at exit 0 in this case; mirror it here
            # so CI artifacts pinned to ``--format pdf`` don't fail when
            # the renderer is missing.
            return
        typer.echo(
            "No outputs written. Pass at least one of md,json,html,pdf in --format.",
            err=True,
        )
        raise typer.Exit(2)
    for path in written:
        typer.echo(f"Wrote {path}")


def _parse_formats(value: str) -> set[str]:
    parts = {item.strip() for item in value.split(",") if item.strip()}
    invalid = parts - _VALID_FORMATS
    expected = ",".join(sorted(_VALID_FORMATS))
    if invalid:
        typer.echo(
            f"Unsupported --format value(s): {sorted(invalid)}; "
            f"expected a subset of {expected}",
            err=True,
        )
        raise typer.Exit(2)
    if not parts:
        typer.echo(
            f"--format must contain at least one of {expected}", err=True
        )
        raise typer.Exit(2)
    return parts


def _load_packet_or_report(payload: str) -> EvidencePacket:
    """Detect whether ``payload`` is a ``packet.json`` or a
    ``report.json`` and dispatch to the matching loader.

    Raises ``PacketSchemaError`` for unrecognised payloads so the CLI
    surfaces a single consistent exit code (2) regardless of which
    branch failed.
    """

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PacketSchemaError(f"input is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise PacketSchemaError("input must be a JSON object")

    if "packet_schema_version" in parsed:
        return load_packet_json(parsed)

    if "report_schema_version" in parsed:
        try:
            report = ReadinessReport.model_validate(parsed)
        except ValidationError as exc:
            raise PacketSchemaError(
                f"report.json failed schema validation: {exc}"
            ) from exc
        try:
            return build_packet_from_report(report)
        except ValueError as exc:
            raise PacketSchemaError(str(exc)) from exc

    raise PacketSchemaError(
        "input does not look like packet.json or report.json — expected a "
        "JSON object with either 'packet_schema_version' or "
        "'report_schema_version' at the top level"
    )


__all__ = ["evidence_packet", "EvidencePacket"]
