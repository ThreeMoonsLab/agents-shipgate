"""n8n adapter — constants, predicates, and leaf helpers.

Internal module. The public surface is re-exported from
``agents_shipgate.inputs.n8n.__init__``; no external code should import
from this module directly.

This module hosts:

- Constants (``N8N_NODE_TYPE_RE``, ``FROM_AI_RE``, ``N8N_SOURCE_TYPES``,
  ``BUILTIN_N8N_PREFIXES``, ``HTTP_METHODS``).
- The two node-graph data classes (``_NodeItem``, ``_Edge``) — shared by
  every sub-module that consumes parsed workflow JSON.
- Pure-leaf string / path / hash / redaction helpers reused across the
  package.
- Node-kind / tool-node-kind / source-type classification helpers.

Anything that takes user-controlled strings goes through the global
privacy redaction layer (``core.privacy``) before landing in artifacts.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from agents_shipgate.cli.discovery.artifacts import SKIP_DIR_PREFIXES, SKIP_DIRS
from agents_shipgate.core.privacy import redact_data as _privacy_redact_data
from agents_shipgate.core.privacy import redact_text as _privacy_redact_text
from agents_shipgate.inputs.common import manifest_relative_path

N8N_NODE_TYPE_RE = re.compile(r"^(@n8n/)?n8n-nodes-")
FROM_AI_RE = re.compile(
    r"\$fromAI\(\s*['\"]([^'\"]+)['\"]"
    r"(?:\s*,\s*['\"]([^'\"]*)['\"])?"
    r"(?:\s*,\s*['\"]([^'\"]+)['\"])?",
)

N8N_SOURCE_TYPES = {
    "n8n_ai_tool",
    "n8n_workflow_tool",
    "n8n_code_tool",
    "n8n_http_tool",
    "n8n_mcp_client_tool",
    "n8n_inventory",
}
BUILTIN_N8N_PREFIXES = (
    "n8n-nodes-base.",
    "n8n-nodes-langchain.",
    "@n8n/n8n-nodes-base.",
    "@n8n/n8n-nodes-langchain.",
)
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


# --- Leaf string / collection helpers ---------------------------------------


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []


def _top_level_string(value: dict[str, Any], keys: set[str]) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


# --- Hash / path helpers ----------------------------------------------------


def _stable_identifier_hash(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _display_path(path: Path, base_dir: Path) -> str:
    return manifest_relative_path(str(path), base_dir)


def _skip_path(path: Path, root: Path) -> bool:
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return True
    return any(
        part in SKIP_DIRS or any(part.startswith(prefix) for prefix in SKIP_DIR_PREFIXES)
        for part in parts
    )


# --- Privacy shims ----------------------------------------------------------


def _redact_text(value: str | None) -> str | None:
    return _privacy_redact_text(value)


def _redact_structured_strings(value: Any) -> Any:
    return _privacy_redact_data(value)


# --- Schema helper ----------------------------------------------------------


def _schema_type(value: str | None) -> str:
    normalized = (value or "string").lower()
    if normalized in {"number", "integer", "boolean", "array", "object", "string"}:
        return normalized
    if normalized in {"json", "any"}:
        return "object"
    return "string"


# --- Predicates that do NOT need _NodeItem ----------------------------------


def _is_runtime_expression(value: str | None) -> bool:
    return bool(value and ("{{" in value or "$json" in value or "$node" in value))


def _is_ingress_type(lower_node_type: str) -> bool:
    return lower_node_type.endswith(
        (
            ".webhook",
            ".chattrigger",
            ".manualtrigger",
            ".formtrigger",
        )
    )


# --- Node graph models ------------------------------------------------------


class _NodeItem:
    def __init__(
        self,
        raw: dict[str, Any],
        index: int,
        node_id: str,
        name: str,
        node_type: str,
        parameters: dict[str, Any],
        credentials: dict[str, Any],
        disabled: bool,
    ) -> None:
        self.raw = raw
        self.index = index
        self.node_id = node_id
        self.name = name
        self.node_type = node_type
        self.parameters = parameters
        self.credentials = credentials
        self.disabled = disabled

    @classmethod
    def from_raw(cls, raw: dict[str, Any], index: int) -> _NodeItem:
        name = _string_or_none(raw.get("name")) or f"node_{index}"
        node_id = _string_or_none(raw.get("id")) or _stable_identifier_hash(f"{name}:{index}")[:16]
        node_type = _string_or_none(raw.get("type")) or "unknown"
        parameters = raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {}
        credentials = raw.get("credentials") if isinstance(raw.get("credentials"), dict) else {}
        return cls(raw, index, node_id, name, node_type, parameters, credentials, raw.get("disabled") is True)


class _Edge:
    def __init__(self, source: str, target: str, kind: str) -> None:
        self.source = source
        self.target = target
        self.kind = kind


# --- Predicates that take _NodeItem -----------------------------------------


def _is_community_tool(item: _NodeItem) -> bool:
    lower = item.node_type.lower()
    if any(lower.startswith(prefix) for prefix in BUILTIN_N8N_PREFIXES):
        return False
    return ".tool" in lower or "tool" in lower


def _is_human_review_node(item: _NodeItem) -> bool:
    compact_type = item.node_type.lower().replace("-", "").replace("_", "")
    return "sendandwait" in compact_type


def _node_sort_key(node_by_name: dict[str, _NodeItem], name: str) -> tuple[int, str]:
    item = node_by_name.get(name)
    return (item.index if item else 999999, name)


# --- HTTP method extractor (used by node-kind classification AND auth/risk) ---


def _http_method(item: _NodeItem) -> str | None:
    for key in ("method", "requestMethod", "httpMethod"):
        value = _string_or_none(item.parameters.get(key))
        if value and value.upper() in HTTP_METHODS:
            return value.upper()
    return None


# --- Node-kind classification -----------------------------------------------


def _node_kind(node_type: str) -> str:
    lower = node_type.lower()
    compact = lower.replace("-", "").replace("_", "")
    if "mcptrigger" in compact:
        return "mcp_server_trigger"
    if "toolmcp" in compact or "mcpclient" in compact:
        return "mcp_client_tool"
    if "toolworkflow" in compact:
        return "workflow_tool"
    if "toolcode" in compact or lower.endswith(".code") or lower.endswith(".function"):
        return "code_tool"
    if (
        "toolhttprequest" in compact
        or "toolhttp" in compact
        or lower.endswith(".httprequest")
    ):
        return "http_tool"
    if lower.endswith(".agent") or "langchain.agent" in lower:
        return "ai_agent"
    if _is_ingress_type(lower):
        return "ingress"
    if ".tool" in lower:
        return "ai_tool"
    return "unknown"


def _tool_node_kind(item: _NodeItem) -> str:
    kind = _node_kind(item.node_type)
    if kind in {
        "mcp_client_tool",
        "workflow_tool",
        "code_tool",
        "http_tool",
    }:
        return kind
    if _top_level_string(
        item.parameters,
        {
            "workflowId",
            "workflow_id",
            "workflowName",
            "workflow",
            "targetWorkflow",
        },
    ):
        return "workflow_tool"
    if any(
        _string_or_none(item.parameters.get(key))
        for key in ("jsCode", "pythonCode", "functionCode", "code")
    ):
        return "code_tool"
    if _http_method(item) and _top_level_string(
        item.parameters,
        {"url", "path", "endpoint"},
    ):
        return "http_tool"
    return kind if kind != "unknown" else "ai_tool"


def _source_type_for_kind(kind: str, exposed_by_mcp: bool) -> str:
    if exposed_by_mcp:
        return "mcp"
    return {
        "workflow_tool": "n8n_workflow_tool",
        "code_tool": "n8n_code_tool",
        "http_tool": "n8n_http_tool",
        "mcp_client_tool": "n8n_mcp_client_tool",
    }.get(kind, "n8n_ai_tool")
