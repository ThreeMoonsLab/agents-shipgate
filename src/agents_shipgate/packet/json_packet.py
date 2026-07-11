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


def serialize_packet_json(
    packet: EvidencePacket,
    *,
    sanitize_output: bool = True,
) -> dict[str, Any]:
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
    if sanitize_output:
        payload = sanitize_packet_payload(payload)
    _strip_report_only_fields(payload)
    if payload.get("generated_at") is None:
        payload.pop("generated_at", None)
    return payload


def write_packet_json(
    packet: EvidencePacket,
    path: Path,
    *,
    sanitize_output: bool = True,
) -> None:
    """Write ``packet.json`` to ``path``. Parent dirs are created."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_packet_json(packet, sanitize_output=sanitize_output)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_packet_json(payload: dict[str, Any] | str | bytes) -> EvidencePacket:
    """Validate ``payload`` and return an ``EvidencePacket``.

    ``payload`` may be a parsed dict or a raw JSON string/bytes. Older
    payloads are upgraded additively through the current packet shape:
    v0.2 tool-surface diff, v0.3 HITL provenance fields, v0.5
    action-surface diff, v0.6 evidence matrix, v0.7 capability trace
    evidence metadata, and v0.8 semantic coverage. Unsupported versions
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
    legacy_version = (
        version
        if version in {"0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9"}
        else None
    )
    if version == "0.1":
        payload_dict = {
            **payload_dict,
            "packet_schema_version": "0.10",
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
        _upgrade_hitl_v07(payload_dict)
    elif version == "0.2":
        payload_dict = {**payload_dict, "packet_schema_version": "0.10"}
        _upgrade_hitl_v03(payload_dict)
        _upgrade_action_surface_v05(payload_dict)
        _upgrade_evidence_matrix_v06(payload_dict)
        _upgrade_hitl_v07(payload_dict)
    elif version == "0.3":
        payload_dict = {**payload_dict, "packet_schema_version": "0.10"}
        _upgrade_action_surface_v05(payload_dict)
        _upgrade_evidence_matrix_v06(payload_dict)
        _upgrade_hitl_v07(payload_dict)
    elif version == "0.4":
        payload_dict = {**payload_dict, "packet_schema_version": "0.10"}
        _upgrade_action_surface_v05(payload_dict)
        _upgrade_evidence_matrix_v06(payload_dict)
        _upgrade_hitl_v07(payload_dict)
    elif version == "0.5":
        payload_dict = {**payload_dict, "packet_schema_version": "0.10"}
        _upgrade_evidence_matrix_v06(payload_dict)
        _upgrade_hitl_v07(payload_dict)
    elif version == "0.6":
        payload_dict = {**payload_dict, "packet_schema_version": "0.10"}
        _upgrade_hitl_v07(payload_dict)
    elif version == "0.7":
        payload_dict = {**payload_dict, "packet_schema_version": "0.10"}
    elif version == "0.8":
        payload_dict = {**payload_dict, "packet_schema_version": "0.10"}
    elif version == "0.9":
        payload_dict = {**payload_dict, "packet_schema_version": "0.10"}
    elif version != "0.10":
        raise PacketSchemaError(
            "unsupported packet_schema_version: "
            f"{version!r}; expected '0.1', '0.2', '0.3', '0.4', '0.5', "
            "'0.6', '0.7', '0.8', '0.9', or '0.10'"
        )

    if legacy_version is not None:
        _upgrade_semantic_coverage_v08(payload_dict, source_version=legacy_version)

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
    """Compatibility hook kept for tests that import it.

    Packet v0.7 intentionally carries ``ReleaseDecisionItem.capability_refs``
    and ``capability_trace_refs``. There are no report-only fields to strip.
    """

    return None


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


def _upgrade_hitl_v07(payload: dict[str, Any]) -> None:
    hitl = payload.get("human_in_the_loop")
    if not isinstance(hitl, dict):
        return
    hitl.setdefault(
        "capability_trace_summary",
        {
            "source_count": 0,
            "trace_count": 0,
            "matched_trace_count": 0,
            "unmatched_trace_count": 0,
            "approval_trace_count": 0,
            "agent_trace_count": 0,
            "api_trace_count": 0,
            "warning_count": 0,
        },
    )
    hitl.setdefault("capability_trace_refs", [])


def _upgrade_semantic_coverage_v08(
    payload: dict[str, Any],
    *,
    source_version: str,
) -> None:
    """Never reinterpret a legacy ``passed`` packet as a v0.8 pass.

    Packet v0.7 and older predate evidence-backed semantic coverage.  They
    remain readable, but their historical verdict cannot prove the v0.8 pass
    contract.  Downgrade only the unsafe ``passed`` case and attach a
    structured, human-routed regeneration action.
    """

    release = payload.get("release_decision")
    if not isinstance(release, dict) or release.get("decision") != "passed":
        return

    rerun_command = "agents-shipgate scan -c shipgate.yaml --format json"
    reason = (
        f"Packet schema {source_version} predates evidence-backed semantic "
        "coverage; its historical passed verdict is not a v0.8 safety "
        f"statement. Regenerate from the source workspace with `{rerun_command}`."
    )
    release["decision"] = "insufficient_evidence"
    release["verdict"] = "INSUFFICIENT EVIDENCE"
    release["reason"] = reason

    evidence = release.get("evidence_coverage")
    if isinstance(evidence, dict):
        evidence["level"] = "incomplete"
        evidence["human_review_recommended"] = True
        gaps = evidence.setdefault("evidence_gaps", [])
        if isinstance(gaps, list):
            gaps.append(
                {
                    "kind": "incomplete_surface",
                    "subject": f"legacy_packet_schema:{source_version}",
                    "source_type": None,
                    "source_ref": f"packet_schema_version={source_version}",
                    "why": (
                        "The legacy packet has no trustworthy v0.8 semantic coverage assessment."
                    ),
                    "next_action": {
                        "kind": "provide_complete_inventory",
                        "command": rerun_command,
                        "path": "shipgate.yaml",
                        "why": (
                            "Semantic coverage must be recomputed by the current "
                            "static engine from source artifacts."
                        ),
                        "expects": (
                            "A freshly generated packet schema 0.8 whose semantic "
                            "coverage accounts for every in-scope action."
                        ),
                        "accepted_values": [],
                    },
                }
            )
        total_actions = 0
        high_risk_surface = payload.get("high_risk_surface")
        if isinstance(high_risk_surface, dict):
            candidate = high_risk_surface.get("total_tools")
            if isinstance(candidate, int) and candidate >= 0:
                total_actions = candidate
        evidence["semantic_coverage"] = {
            "total_actions": total_actions,
            "pass_eligible_actions": 0,
            "gap_count": 1,
            "review_concern_count": 0,
            "reason_counts": {"legacy_packet_requires_regeneration": 1},
        }

    fail_policy = release.get("fail_policy")
    if isinstance(fail_policy, dict):
        strict = fail_policy.get("ci_mode") == "strict"
        fail_policy["would_fail_ci"] = strict
        fail_policy["exit_code"] = 20 if strict else 0

    not_proven = payload.get("not_proven")
    if isinstance(not_proven, dict):
        residuals = not_proven.setdefault("additional_residuals", [])
        if isinstance(residuals, list):
            residuals.append(reason)
