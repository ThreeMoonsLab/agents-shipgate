"""JSON serialization and load for the Release Evidence Packet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_shipgate.packet.models import EvidencePacket


class PacketSchemaError(ValueError):
    """Raised when ``packet.json`` content does not match the expected
    v0.1 schema (e.g. wrong ``packet_schema_version``, missing fields).
    """


def serialize_packet_json(packet: EvidencePacket) -> dict[str, Any]:
    """Return the packet as a JSON-ready dict (compatible with
    ``json.dumps``)."""

    return packet.model_dump(mode="json")


def write_packet_json(packet: EvidencePacket, path: Path) -> None:
    """Write ``packet.json`` to ``path``. Parent dirs are created."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_packet_json(packet)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_packet_json(payload: dict[str, Any] | str | bytes) -> EvidencePacket:
    """Validate ``payload`` and return an ``EvidencePacket``.

    ``payload`` may be a parsed dict or a raw JSON string/bytes. A
    mismatched ``packet_schema_version`` (anything other than ``"0.1"``)
    raises ``PacketSchemaError`` so callers can downgrade to a clean
    error rather than a noisy validation traceback.
    """

    if isinstance(payload, (str, bytes)):
        try:
            payload_dict = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PacketSchemaError(f"packet.json is not valid JSON: {exc}") from exc
    else:
        payload_dict = payload

    if not isinstance(payload_dict, dict):
        raise PacketSchemaError("packet.json must be a JSON object")

    version = payload_dict.get("packet_schema_version")
    if version != "0.1":
        raise PacketSchemaError(
            f"unsupported packet_schema_version: {version!r}; expected '0.1'"
        )

    try:
        return EvidencePacket.model_validate(payload_dict)
    except ValidationError as exc:
        raise PacketSchemaError(f"packet.json failed validation: {exc}") from exc
