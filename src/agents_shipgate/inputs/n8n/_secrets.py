"""n8n adapter — secret scanning of workflow JSON and node parameters.

Internal module. Scans workflow ``parameters``, ``notes``, ``pinData``,
and ``staticData`` against the global ``SECRET_PATTERNS`` and records
each match into ``artifacts.secret_exposures`` with a JSON-pointer
locator. Evidence is deliberately redacted: only ``source_ref``,
``parameter_pointer``, and ``secret_kind`` are stored — never the
matched secret value.

The redaction policy is enforced by ``core.privacy``; this module is
the call-site that wires per-node and per-workflow scans into the n8n
adapter's pipeline.
"""

from __future__ import annotations

from typing import Any

from agents_shipgate.core.artifact_models import N8nArtifacts
from agents_shipgate.core.privacy import SECRET_PATTERNS
from agents_shipgate.inputs.common import json_pointer_escape
from agents_shipgate.inputs.n8n._common import _NodeItem, _redact_text


def _scan_node_secrets(
    item: _NodeItem,
    source_path: str,
    workflow_id: str,
    artifacts: N8nArtifacts,
) -> None:
    for pointer, value in _secret_values(
        item.parameters,
        prefix=f"/nodes/{json_pointer_escape(item.node_id)}/parameters",
    ):
        _record_secret_matches(
            value,
            pointer=pointer,
            source_ref=f"{source_path}#node:{item.node_id}",
            source_path=source_path,
            workflow_id=workflow_id,
            artifacts=artifacts,
            node_id=item.node_id,
        )
    if "notes" in item.raw:
        for pointer, value in _secret_values(
            item.raw["notes"],
            prefix=f"/nodes/{json_pointer_escape(item.node_id)}/notes",
        ):
            _record_secret_matches(
                value,
                pointer=pointer,
                source_ref=f"{source_path}#node:{item.node_id}",
                source_path=source_path,
                workflow_id=workflow_id,
                artifacts=artifacts,
                node_id=item.node_id,
            )


def _scan_workflow_secrets(
    workflow: dict[str, Any],
    source_path: str,
    workflow_id: str,
    artifacts: N8nArtifacts,
) -> None:
    for key in ("pinData", "staticData"):
        if key not in workflow:
            continue
        for pointer, value in _secret_values(workflow[key], prefix=f"/{key}"):
            _record_secret_matches(
                value,
                pointer=pointer,
                source_ref=f"{source_path}#{pointer}",
                source_path=source_path,
                workflow_id=workflow_id,
                artifacts=artifacts,
            )


def _record_secret_matches(
    value: str,
    *,
    pointer: str,
    source_ref: str,
    source_path: str,
    workflow_id: str,
    artifacts: N8nArtifacts,
    node_id: str | None = None,
) -> None:
    for kind, pattern in SECRET_PATTERNS:
        for _match in pattern.finditer(value):
            exposure = {
                "source_ref": source_ref,
                "source_path": source_path,
                "workflow_id": workflow_id,
                "parameter_pointer": pointer,
                "source_pointer": pointer,
                "secret_kind": kind,
            }
            if node_id is not None:
                exposure["node_id"] = node_id
            artifacts.secret_exposures.append(exposure)


def _secret_values(value: Any, *, prefix: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        found.append((prefix, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            pointer_key = _redact_text(str(key)) or str(key)
            found.extend(
                _secret_values(
                    item,
                    prefix=f"{prefix}/{json_pointer_escape(pointer_key)}",
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_secret_values(item, prefix=f"{prefix}/{index}"))
    return found
