"""JSON serialization and load for the Release Evidence Packet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_shipgate.core.disclaimers import HITL_RUNTIME_CONTROL_DISCLAIMER
from agents_shipgate.core.privacy import sanitize_packet_payload
from agents_shipgate.packet.evidence_matrix import unavailable_evidence_matrix
from agents_shipgate.schemas.packet import EvidencePacket


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

    payload = sanitize_packet_payload(packet.model_dump(mode="json"))
    _strip_report_only_fields(payload)
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

    ``payload`` may be a parsed dict or a raw JSON string/bytes. Older
    payloads are upgraded additively through the current packet shape:
    v0.2 tool-surface diff, v0.3 HITL provenance fields, v0.5
    action-surface diff, v0.6 evidence matrix (PR #104), and v0.6
    ``ReleaseDecisionItem.{source, policy_evidence_source}`` (PR #103,
    no field synthesis needed because v0.5-emitted packets never
    carried the optional fields). Unsupported versions
    raise ``PacketSchemaError`` so callers can downgrade to a clean
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
    if version == "0.1":
        payload_dict = {
            **payload_dict,
            "packet_schema_version": "0.6",
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
        _upgrade_action_surface_v05(payload_dict)
        _upgrade_evidence_matrix_v06(payload_dict)
    elif version == "0.2":
        payload_dict = {**payload_dict, "packet_schema_version": "0.6"}
        _upgrade_hitl_v03(payload_dict)
        _upgrade_action_surface_v05(payload_dict)
        _upgrade_evidence_matrix_v06(payload_dict)
    elif version == "0.3":
        payload_dict = {**payload_dict, "packet_schema_version": "0.6"}
        _upgrade_action_surface_v05(payload_dict)
        _upgrade_evidence_matrix_v06(payload_dict)
    elif version == "0.4":
        payload_dict = {**payload_dict, "packet_schema_version": "0.6"}
        _upgrade_action_surface_v05(payload_dict)
        _upgrade_evidence_matrix_v06(payload_dict)
    elif version == "0.5":
        payload_dict = {**payload_dict, "packet_schema_version": "0.6"}
        _upgrade_evidence_matrix_v06(payload_dict)
    elif version != "0.6":
        raise PacketSchemaError(
            "unsupported packet_schema_version: "
            f"{version!r}; expected '0.1', '0.2', '0.3', '0.4', '0.5', or '0.6'"
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


def _upgrade_action_surface_v05(payload: dict[str, Any]) -> None:
    payload.setdefault(
        "action_surface_diff",
        {
            "status": "not_declared",
            "enabled": False,
            "base_kind": "none",
            "summary": {},
            "highlights": [],
            "blocking_reasons": [],
            "notes": ["No action-surface diff was recorded."],
        },
    )


def _strip_report_only_fields(value: Any) -> None:
    """Remove report-only additive fields before packet serialization.

    ``EvidencePacket`` reuses ``ReleaseDecisionItem`` from the report schema.
    v0.24 report items carry ``capability_refs``; packet v0.6 deliberately
    remains unchanged, so the packet serializer drops that report-only key from
    the packet sections that carry release-decision items. Do not strip by key
    globally: report-era ``capability_refs`` also appears on other public report
    models, and future packet sections may embed those models unchanged.
    """

    if not isinstance(value, dict):
        return

    _strip_release_item_lists(value.get("release_decision"), ("blockers", "review_items"))
    evidence_matrix = value.get("evidence_matrix")
    if isinstance(evidence_matrix, dict):
        rows = evidence_matrix.get("rows")
        if isinstance(rows, list):
            for row in rows:
                _strip_release_item_lists(
                    row,
                    ("blocking_findings", "review_items"),
                )
    _strip_release_item_lists(
        value.get("capability_intent"),
        ("divergence_findings",),
    )
    _strip_release_item_lists(value.get("approval_coverage"), ("gap_findings",))
    _strip_release_item_lists(value.get("idempotency_risk"), ("gap_findings",))
    _strip_release_item_lists(value.get("scope_coverage"), ("gap_findings",))
    _strip_release_item_lists(value.get("human_in_the_loop"), ("trace_findings",))


def _strip_release_item_lists(value: Any, fields: tuple[str, ...]) -> None:
    if not isinstance(value, dict):
        return
    for field in fields:
        items = value.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                item.pop("capability_refs", None)


def _upgrade_evidence_matrix_v06(payload: dict[str, Any]) -> None:
    payload.setdefault(
        "evidence_matrix",
        unavailable_evidence_matrix().model_dump(mode="json"),
    )
