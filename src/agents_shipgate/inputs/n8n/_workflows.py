"""n8n adapter — workflow shape detection + extraction + record builders.

Internal module. Owns:

- ``_load_workflow_ref`` (per-workflow-source loader; called by the
  top-level orchestrator in ``_adapter.py``)
- ``_workflow_*`` shape predicates and meta-extractors (``_workflow_id``,
  ``_workflow_tags``, ``_workflow_error_workflow``,
  ``_is_workflow_object``, ``_has_workflow_shape``,
  ``_has_first_party_node``)
- ``_extract_workflow`` — the per-workflow processor that walks every
  node, classifies it, scans for secrets, records credentials/ingress,
  and delegates tool extraction to ``_tools.py``.
- Connection-graph helpers (``_connection_edges``, ``_duplicate_names``)
- Record builders (``_node_record``, ``_execution_control``,
  ``_ingress_record``, ``_dynamic``) — produce the per-node dicts that
  land on ``N8nArtifacts``.

``_extract_workflow`` is the inversion point of the n8n pipeline: it
fans out into ``_secrets`` (per-node and per-workflow secret scanning),
``_auth_risk`` (credential records), and ``_tools`` (Tool extraction).
The tools module calls back into ``_dynamic`` / ``_node_record`` /
``_execution_control`` via late imports — that asymmetry is
intentional: workflows owns "what is a node, what is a record"; tools
owns "what is a Tool emitted from a node."
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_shipgate.core.artifact_models import N8nArtifacts
from agents_shipgate.core.domain import LoadedToolSource, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import (
    json_pointer_escape,
    load_structured_file,
    resolve_input_path,
)
from agents_shipgate.inputs.n8n._auth_risk import _record_credentials
from agents_shipgate.inputs.n8n._common import (
    N8N_NODE_TYPE_RE,
    _append_unique,
    _display_path,
    _Edge,
    _http_method,
    _is_human_review_node,
    _node_kind,
    _node_sort_key,
    _NodeItem,
    _redact_text,
    _stable_identifier_hash,
    _string_or_none,
    _top_level_string,
)
from agents_shipgate.inputs.n8n._secrets import (
    _scan_node_secrets,
    _scan_workflow_secrets,
)
from agents_shipgate.inputs.n8n._tools import (
    _is_unfiltered_mode,
    _tools_from_tool_node,
)
from agents_shipgate.schemas.manifest import ArtifactPathConfig


def _load_workflow_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    artifacts: N8nArtifacts,
) -> list[LoadedToolSource]:
    try:
        path = resolve_input_path(base_dir, ref.path)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional n8n workflow source {ref.path!r} failed to load.")
        return []
    if not path.exists():
        if not ref.optional:
            raise InputParseError(f"Input file not found: {path}")
        artifacts.warnings.append(f"Optional n8n workflow source {ref.path!r} failed to load.")
        return []

    workflow_paths = _workflow_paths(path, base_dir)
    loaded_sources: list[LoadedToolSource] = []
    explicit_file = path.is_file()
    for workflow_path in workflow_paths:
        display_path = _display_path(workflow_path, base_dir)
        data = load_structured_file(workflow_path)
        workflows = _workflow_objects(data)
        if not workflows:
            community_hint = (
                isinstance(data, dict)
                and _has_workflow_shape(data)
                and not _has_first_party_node(data)
            )
            if community_hint:
                message = (
                    f"n8n-like workflow JSON has no first-party node types and no "
                    f"versionId marker: {display_path}. Check whether community node "
                    "prefixes should be registered or export metadata is missing."
                )
                if explicit_file:
                    raise InputParseError(message)
                artifacts.warnings.append(message)
            if explicit_file:
                raise InputParseError(
                    f"n8n workflow source is not workflow-shaped JSON: {workflow_path}"
                )
            continue
        for index, workflow in enumerate(workflows):
            source_id = (
                f"n8n:{display_path}"
                if len(workflows) == 1
                else f"n8n:{display_path}:{index}"
            )
            tools, warnings = _extract_workflow(
                workflow,
                source_id=source_id,
                source_path=display_path,
                artifacts=artifacts,
            )
            loaded_sources.append(
                LoadedToolSource(
                    source_id=source_id,
                    source_type="n8n",
                    tools=tools,
                    warnings=warnings,
                )
            )
    return loaded_sources


def _workflow_paths(path: Path, base_dir: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise InputParseError(f"n8n workflow source must be a file or directory: {path}")
    return sorted(
        (candidate for candidate in path.rglob("*.json") if candidate.is_file()),
        key=lambda item: _display_path(item, base_dir),
    )


def _workflow_objects(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [data] if _is_workflow_object(data) else []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict) and _is_workflow_object(item)]
    return []


def _is_workflow_object(data: dict[str, Any]) -> bool:
    if not _has_workflow_shape(data):
        return False
    return _has_first_party_node(data) or bool(_string_or_none(data.get("versionId")))


def _has_workflow_shape(data: dict[str, Any]) -> bool:
    nodes = data.get("nodes")
    connections = data.get("connections")
    return (
        isinstance(nodes, list)
        and bool(nodes)
        and all(isinstance(node, dict) for node in nodes)
        and isinstance(connections, dict)
    )


def _has_first_party_node(data: dict[str, Any]) -> bool:
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return False
    return any(
        isinstance(node, dict)
        and isinstance(node.get("type"), str)
        and N8N_NODE_TYPE_RE.match(node["type"])
        for node in nodes
    )


def _workflow_id(workflow: dict[str, Any], source_id: str) -> str:
    source_key = source_id.removeprefix("n8n:")
    raw_id = _string_or_none(workflow.get("id"))
    if raw_id:
        return f"{source_key}#{raw_id}"
    return f"{source_key}#generated:{_stable_identifier_hash(source_id)[:12]}"


def _workflow_tags(workflow: dict[str, Any]) -> list[str]:
    tags = workflow.get("tags")
    if not isinstance(tags, list):
        return []
    values: list[str] = []
    for raw in tags:
        if isinstance(raw, str):
            value = raw
        elif isinstance(raw, dict):
            value = _string_or_none(raw.get("name")) or _string_or_none(raw.get("id"))
        else:
            value = None
        if value:
            _append_unique(values, _redact_text(value) or value)
    return values


def _workflow_error_workflow(workflow: dict[str, Any]) -> str | None:
    settings = workflow.get("settings")
    if not isinstance(settings, dict):
        return None
    value = _top_level_string(
        settings,
        {"errorWorkflow", "errorWorkflowId", "errorWorkflowName"},
    )
    return _redact_text(value) if value else None


def _extract_workflow(
    workflow: dict[str, Any],
    *,
    source_id: str,
    source_path: str,
    artifacts: N8nArtifacts,
) -> tuple[list[Tool], list[str]]:
    warnings: list[str] = []
    _append_unique(artifacts.workflow_files, source_path)
    workflow_id = _workflow_id(workflow, source_id)
    workflow_name = (
        _redact_text(_string_or_none(workflow.get("name")))
        or _redact_text(Path(source_path).stem)
        or Path(source_path).stem
    )
    nodes = [node for node in workflow.get("nodes") or [] if isinstance(node, dict)]
    node_items = [_NodeItem.from_raw(node, index) for index, node in enumerate(nodes)]
    if not _has_first_party_node(workflow):
        message = (
            f"n8n workflow {source_path} has no first-party node types; "
            "treating it as a community-node workflow because versionId is present."
        )
        warnings.append(message)
        artifacts.warnings.append(message)
    duplicate_names = _duplicate_names(node_items)
    for name in duplicate_names:
        message = (
            f"n8n workflow {source_path} has duplicate node name "
            f"{_redact_text(name)!r}; connection resolution uses the last matching node."
        )
        warnings.append(message)
        artifacts.warnings.append(message)
    _scan_workflow_secrets(workflow, source_path, workflow_id, artifacts)
    workflow_active = workflow.get("active") is not False
    workflow_tags = _workflow_tags(workflow)
    workflow_error = _workflow_error_workflow(workflow)
    disabled_names = {item.name for item in node_items if item.disabled}
    active_node_items = [item for item in node_items if not item.disabled]
    node_by_name = {item.name: item for item in active_node_items if item.name}
    node_by_id = {item.node_id: item for item in active_node_items if item.node_id}
    edges = [
        edge
        for edge in _connection_edges(workflow.get("connections") or {})
        if edge.source not in disabled_names and edge.target not in disabled_names
    ]
    tool_edges = [edge for edge in edges if edge.kind == "ai_tool"]
    tool_sources = {edge.source for edge in tool_edges}
    mcp_targets = {
        item.name
        for item in active_node_items
        if item.name and _node_kind(item.node_type) == "mcp_server_trigger"
    }
    ai_agent_names = {
        item.name
        for item in active_node_items
        if item.name and _node_kind(item.node_type) == "ai_agent"
    }
    human_review_names = {
        item.name for item in active_node_items if item.name and _is_human_review_node(item)
    }

    artifacts.workflows.append(
        {
            "id": workflow_id,
            "name": workflow_name,
            "source_ref": source_path,
            "active": workflow_active,
            **({"tags": workflow_tags} if workflow_tags else {}),
            **({"errorWorkflow": workflow_error} if workflow_error else {}),
            "node_count": len(node_items),
            "tool_connection_count": len(tool_edges),
        }
    )
    for item in node_items:
        _scan_node_secrets(item, source_path, workflow_id, artifacts)
    if not workflow_active:
        message = (
            f"n8n workflow {source_path} is inactive; skipping live tool and "
            "ingress normalization."
        )
        warnings.append(message)
        artifacts.warnings.append(message)
        return [], list(dict.fromkeys(warnings))

    for item in node_items:
        if item.disabled:
            continue
        kind = _node_kind(item.node_type)
        if kind == "ai_agent":
            artifacts.ai_agents.append(_node_record(item, source_path, workflow_id))
        elif kind == "mcp_server_trigger":
            artifacts.mcp_server_triggers.append(
                _node_record(item, source_path, workflow_id)
            )
            if _is_unfiltered_mode(item.parameters) and not artifacts.tool_inventory_files:
                _dynamic(
                    artifacts,
                    kind="mcp_server_wildcard",
                    item=item,
                    source_path=source_path,
                    reason="MCP Server Trigger exposes a wildcard or all-tools surface.",
                    warnings=warnings,
                )
        elif kind == "ingress":
            artifacts.ingress.append(_ingress_record(item, source_path, workflow_id))
        if item.name in human_review_names:
            artifacts.human_review_nodes.append(_node_record(item, source_path, workflow_id))
        _record_credentials(item, source_path, workflow_id, artifacts)

    tools: list[Tool] = []
    for source_name in sorted(tool_sources, key=lambda name: _node_sort_key(node_by_name, name)):
        item = node_by_name.get(source_name)
        if item is None:
            continue
        targets = [edge.target for edge in tool_edges if edge.source == source_name]
        exposure_modes = []
        if any(target not in mcp_targets for target in targets):
            exposure_modes.append(False)
        if any(target in mcp_targets for target in targets):
            exposure_modes.append(True)
        for index, exposed_by_mcp in enumerate(exposure_modes):
            extracted = _tools_from_tool_node(
                item,
                source_id=source_id,
                source_path=source_path,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                workflow_error_workflow=workflow_error,
                exposed_by_mcp=exposed_by_mcp,
                artifacts=artifacts,
                warnings=warnings,
                node_by_id=node_by_id,
                node_by_name=node_by_name,
                record_node_findings=index == 0,
                bound_agent_names=sorted(set(targets) & ai_agent_names),
            )
            tools.extend(extracted)

    return tools, list(dict.fromkeys(warnings))


# --- Connection graph -------------------------------------------------------


def _connection_edges(connections: dict[str, Any]) -> list[_Edge]:
    edges: list[_Edge] = []
    for source, outputs in connections.items():
        if not isinstance(outputs, dict):
            continue
        for output_kind, output_groups in outputs.items():
            if not isinstance(output_groups, list):
                continue
            for group in output_groups:
                if not isinstance(group, list):
                    continue
                for raw in group:
                    if not isinstance(raw, dict):
                        continue
                    target = _string_or_none(raw.get("node"))
                    if not target:
                        continue
                    kind = _string_or_none(raw.get("type")) or str(output_kind)
                    edges.append(_Edge(str(source), target, kind))
    return edges


def _duplicate_names(nodes: list[_NodeItem]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in nodes:
        if item.name in seen:
            duplicates.add(item.name)
        seen.add(item.name)
    return sorted(duplicates)


# --- Record builders --------------------------------------------------------


def _node_record(item: _NodeItem, source_path: str, workflow_id: str) -> dict[str, Any]:
    record = {
        "name": _redact_text(item.name) or item.name,
        "node_id": item.node_id,
        "node_type": item.node_type,
        "source_ref": f"{source_path}#node:{item.node_id}",
        "source_path": source_path,
        "source_pointer": f"/nodes/{json_pointer_escape(item.node_id)}",
        "workflow_id": workflow_id,
    }
    execution_control = _execution_control(item)
    if execution_control:
        record["execution"] = execution_control
    return record


def _execution_control(item: _NodeItem) -> dict[str, Any]:
    control: dict[str, Any] = {}
    for key in ("retryOnFail", "continueOnFail"):
        value = item.raw.get(key)
        if isinstance(value, bool):
            control[key] = value
    max_tries = item.raw.get("maxTries")
    if isinstance(max_tries, int):
        control["maxTries"] = max_tries
    elif isinstance(max_tries, str) and max_tries.strip().isdigit():
        control["maxTries"] = int(max_tries.strip())
    return control


def _ingress_record(item: _NodeItem, source_path: str, workflow_id: str) -> dict[str, Any]:
    auth_value = _top_level_string(
        item.parameters,
        {"authentication", "authType", "authorization"},
    )
    public_path = _top_level_string(item.parameters, {"path", "webhookPath"})
    http_method = _http_method(item)
    return {
        **_node_record(item, source_path, workflow_id),
        "auth_present": bool(auth_value),
        "public_path_present": bool(public_path),
        **({"httpMethod": http_method} if http_method else {}),
    }


def _dynamic(
    artifacts: N8nArtifacts,
    *,
    kind: str,
    item: _NodeItem,
    source_path: str,
    reason: str,
    warnings: list[str] | None = None,
) -> None:
    surface = {
        "kind": kind,
        "source_ref": f"{source_path}#node:{item.node_id}",
        "source_path": source_path,
        "source_pointer": f"/nodes/{json_pointer_escape(item.node_id)}",
        "node_id": item.node_id,
        "node_type": item.node_type,
        "reason": reason,
    }
    artifacts.dynamic_tool_surfaces.append(surface)
    message = (
        f"n8n {kind} at {source_path}#node:{item.node_id} "
        f"has dynamic tool surface: {reason}"
    )
    artifacts.warnings.append(message)
    if warnings is not None:
        warnings.append(message)
