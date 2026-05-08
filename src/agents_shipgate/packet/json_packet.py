"""JSON serialization and load for the Release Evidence Packet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_shipgate.core.disclaimers import HITL_RUNTIME_CONTROL_DISCLAIMER
from agents_shipgate.packet.models import EvidencePacket


class PacketSchemaError(ValueError):
    """Raised when ``packet.json`` content does not match the expected
    schema (e.g. wrong ``packet_schema_version``, missing fields).
    """


def serialize_packet_json(packet: EvidencePacket) -> dict[str, Any]:
    """Return the packet as a JSON-ready dict (compatible with
    ``json.dumps``).

    ``generated_at`` is excluded when ``None`` so the default scan
    flow produces byte-identical ``packet.json`` for byte-identical
    inputs (matching the ``run_id`` reproducibility guarantee on the
    main report). Callers that want a timestamp pass it explicitly.
    Other ``None`` fields (e.g. ``ApprovalCoverageRow.source``) stay
    in the JSON so the contract shape is stable.
    """

    payload = packet.model_dump(mode="json")
    if payload.get("generated_at") is None:
        payload.pop("generated_at", None)
    return payload


def write_packet_json(packet: EvidencePacket, path: Path) -> None:
    """Write ``packet.json`` to ``path``. Parent dirs are created."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_packet_json(packet)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_packet_json(payload: dict[str, Any] | str | bytes) -> EvidencePacket:
    """Validate ``payload`` and return an ``EvidencePacket``.

    ``payload`` may be a parsed dict or a raw JSON string/bytes. A
    v0.1 payloads are upgraded with the default v0.2 tool-surface diff
    section, then v0.1/v0.2 payloads are upgraded with the default
    v0.3 HITL provenance fields. Unsupported versions raise
    ``PacketSchemaError`` so callers can downgrade to a clean error
    rather than a noisy validation traceback.
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
    if version == "0.1":
        payload_dict = {
            **payload_dict,
            "packet_schema_version": "0.3",
            "tool_surface_diff": {
                "status": "not_declared",
                "enabled": False,
                "base_kind": "none",
                "summary": {},
                "highlights": [],
                "notes": ["No tool-surface diff was recorded."],
            },
        }
        _upgrade_hitl_v03(payload_dict)
    elif version == "0.2":
        payload_dict = {**payload_dict, "packet_schema_version": "0.3"}
        _upgrade_hitl_v03(payload_dict)
    elif version != "0.3":
        raise PacketSchemaError(
            "unsupported packet_schema_version: "
            f"{version!r}; expected '0.1', '0.2', or '0.3'"
        )

    try:
        return EvidencePacket.model_validate(payload_dict)
    except ValidationError as exc:
        raise PacketSchemaError(f"packet.json failed validation: {exc}") from exc


def _upgrade_hitl_v03(payload: dict[str, Any]) -> None:
    hitl = payload.get("human_in_the_loop")
    if not isinstance(hitl, dict):
        return
    hitl.setdefault("runtime_control_disclaimer", HITL_RUNTIME_CONTROL_DISCLAIMER)
    hitl.setdefault("source_provenance", [])
    hitl.setdefault("provenance_mode", "unavailable")
