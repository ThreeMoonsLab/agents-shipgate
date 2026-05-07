"""Release Evidence Packet — reviewer-shaped artifact derived from a scan.

The packet condenses a scan into ten fixed sections so a security,
platform, or release reviewer can read a single file end-to-end and make
a release decision.

The packet is built during ``agents-shipgate scan`` (where it has access
to the in-memory manifest and per-source artifacts) and persisted as a
sibling family of files alongside ``report.{md,json}``::

    agents-shipgate-reports/packet.md
    agents-shipgate-reports/packet.json    (schema: docs/packet-schema.v0.3.json)
    agents-shipgate-reports/packet.html
    agents-shipgate-reports/packet.pdf     (only with the [pdf] extras)

The packet **does not** prove prompt robustness, runtime behavior, model
correctness, or adversarial resistance — see §10 of every emitted
packet for the verbatim disclaimers.
"""

from __future__ import annotations

from agents_shipgate.packet.builder import build_packet, build_packet_from_report
from agents_shipgate.packet.html import render_packet_html
from agents_shipgate.packet.json_packet import (
    PacketSchemaError,
    load_packet_json,
    serialize_packet_json,
)
from agents_shipgate.packet.markdown import render_packet_markdown
from agents_shipgate.packet.models import EvidencePacket
from agents_shipgate.packet.pdf import PdfRendererUnavailable, render_packet_pdf

__all__ = [
    "EvidencePacket",
    "PacketSchemaError",
    "PdfRendererUnavailable",
    "build_packet",
    "build_packet_from_report",
    "load_packet_json",
    "render_packet_html",
    "render_packet_markdown",
    "render_packet_pdf",
    "serialize_packet_json",
]
