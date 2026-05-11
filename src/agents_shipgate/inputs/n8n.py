from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from agents_shipgate.cli.discovery.artifacts import SKIP_DIR_PREFIXES, SKIP_DIRS
from agents_shipgate.config.schema import (
    AgentsShipgateManifest,
    ArtifactPathConfig,
    ToolSourceConfig,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import (
    AuthInfo,
    LoadedToolSource,
    N8nArtifacts,
    Tool,
    ToolRiskHint,
)
from agents_shipgate.inputs.common import (
    json_pointer_escape,
    load_structured_file,
    manifest_relative_path,
    resolve_input_path,
    schema_to_parameters,
    stable_tool_id,
    tool_name_warning,
)
from agents_shipgate.inputs.mcp import load_mcp_tools

N8N_NODE_TYPE_RE = re.compile(r"^(@n8n/)?n8n-nodes-")
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b")),
    (
        "stripe_key",
        re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    ),
    ("slack_token", re.compile(r"\bxox[boprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    ),
    (
        "database_url",
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://"
            r"[^:\s/@]+:[^@\s]+@[^ \t\r\n'\"]+"
        ),
    ),
    (
        "bearer_token",
        re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    ),
)
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


def load_n8n_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> tuple[list[LoadedToolSource], N8nArtifacts | None]:
    config = manifest.n8n
    if config is None or not config.has_inputs():
        return [], None

    artifacts = N8nArtifacts()
    loaded_sources: list[LoadedToolSource] = []
    _load_credential_stubs(config.credential_stubs, base_dir, artifacts)
    _load_structured_refs(
        config.variable_stubs,
        base_dir,
        artifacts.variable_stub_files,
        artifacts.warnings,
        label="n8n variable stub",
    )
    _load_structured_refs(
        config.data_table_schemas,
        base_dir,
        artifacts.data_table_schema_files,
        artifacts.warnings,
        label="n8n data-table schema",
    )
    _load_structured_refs(
        config.execution_samples,
        base_dir,
        artifacts.execution_sample_files,
        artifacts.warnings,
        label="n8n execution sample",
    )
    _load_structured_refs(
        config.eval_sets,
        base_dir,
        artifacts.eval_files,
        artifacts.warnings,
        label="n8n eval set",
    )
    for inventory in config.tool_inventories:
        loaded = _load_inventory_ref(inventory, base_dir, artifacts)
        if loaded:
            loaded_sources.append(loaded)

    for workflow_ref in config.workflows:
        loaded_sources.extend(_load_workflow_ref(workflow_ref, base_dir, artifacts))

    return loaded_sources, artifacts


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
            )
            tools.extend(extracted)

    return tools, list(dict.fromkeys(warnings))


def _tools_from_tool_node(
    item: _NodeItem,
    *,
    source_id: str,
    source_path: str,
    workflow_id: str,
    workflow_name: str,
    workflow_error_workflow: str | None,
    exposed_by_mcp: bool,
    artifacts: N8nArtifacts,
    warnings: list[str],
    node_by_id: dict[str, _NodeItem],
    node_by_name: dict[str, _NodeItem],
    record_node_findings: bool = True,
) -> list[Tool]:
    kind = _tool_node_kind(item)
    if record_node_findings and _is_runtime_expression(_tool_name(item)):
        _dynamic(
            artifacts,
            kind="runtime_tool_name",
            item=item,
            source_path=source_path,
            reason="Tool name uses a runtime expression.",
            warnings=warnings,
        )
    if (
        record_node_findings
        and _is_community_tool(item)
        and not artifacts.tool_inventory_files
    ):
        artifacts.community_tools.append(_node_record(item, source_path, workflow_id))
        _dynamic(
            artifacts,
            kind="community_tool",
            item=item,
            source_path=source_path,
            reason="Community or custom n8n tool node lacks explicit inventory.",
            warnings=warnings,
        )

    if kind == "mcp_client_tool":
        return _mcp_client_tools(
            item,
            source_id=source_id,
            source_path=source_path,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            workflow_error_workflow=workflow_error_workflow,
            artifacts=artifacts,
            warnings=warnings,
        )
    if record_node_findings and kind == "workflow_tool":
        _record_workflow_resolution(
            item,
            source_path,
            artifacts,
            node_by_id,
            node_by_name,
            warnings,
        )
    source_type = _source_type_for_kind(kind, exposed_by_mcp)
    tool = _base_tool(
        item,
        source_id=source_id,
        source_path=source_path,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        workflow_error_workflow=workflow_error_workflow,
        source_type=source_type,
        exposed_by_mcp=exposed_by_mcp,
    )
    if warning := tool_name_warning(tool.name):
        warnings.append(warning)
    _record_tool_artifact(kind, tool, item, source_path, workflow_id, artifacts)
    return [tool]


def _base_tool(
    item: _NodeItem,
    *,
    source_id: str,
    source_path: str,
    workflow_id: str,
    workflow_name: str,
    workflow_error_workflow: str | None,
    source_type: str,
    exposed_by_mcp: bool = False,
    selected_mcp_tool: str | None = None,
) -> Tool:
    name = selected_mcp_tool or _tool_name(item)
    fallback_description = f"n8n tool node {_redact_text(item.name) or item.name}."
    description = _redact_text(_tool_description(item) or fallback_description)
    input_schema = _input_schema(item)
    annotations = {
        "framework": "n8n",
        "n8n_node_id": item.node_id,
        "n8n_node_name": _redact_text(item.name) or item.name,
        "n8n_node_type": item.node_type,
        "n8n_workflow_id": workflow_id,
        "n8n_workflow_name": workflow_name,
    }
    if workflow_error_workflow:
        annotations["n8n_error_workflow"] = workflow_error_workflow
    execution_control = _execution_control(item)
    if execution_control:
        annotations["n8n_execution"] = execution_control
        if execution_control.get("retryOnFail") is True:
            annotations["retryPolicy"] = {
                "source": "n8n",
                "retryOnFail": True,
                **(
                    {"maxTries": execution_control["maxTries"]}
                    if "maxTries" in execution_control
                    else {}
                ),
            }
        if execution_control.get("continueOnFail") is True:
            annotations["continueOnFail"] = True
    if selected_mcp_tool:
        annotations["mcp_tool_name"] = selected_mcp_tool
    if exposed_by_mcp:
        annotations["exposed_by"] = "n8n_mcp_server_trigger"
    method = _http_method(item)
    if method:
        annotations["httpMethod"] = method
    path_hint = _http_path_hint(item)
    if path_hint:
        annotations["path"] = path_hint
    return Tool(
        id=stable_tool_id(f"{workflow_id}:{source_type}:{name}"),
        name=str(name),
        description=description,
        source_type=source_type,
        source_id=source_id,
        source_ref=f"{source_path}#node:{item.node_id}",
        source_path=source_path,
        source_pointer=f"/nodes/{json_pointer_escape(item.node_id)}",
        input_schema=input_schema,
        output_schema=_output_schema(item),
        parameters=schema_to_parameters(input_schema),
        annotations=annotations,
        auth=_auth_info(item),
        risk_hints=_risk_hints(item, method=method),
        extraction_confidence="medium",
        extraction={"method": "n8n_workflow_json", "confidence": "medium"},
    )


def _mcp_client_tools(
    item: _NodeItem,
    *,
    source_id: str,
    source_path: str,
    workflow_id: str,
    workflow_name: str,
    workflow_error_workflow: str | None,
    artifacts: N8nArtifacts,
    warnings: list[str],
) -> list[Tool]:
    mode = _selection_mode(item.parameters)
    selected = _selected_mcp_tools(item.parameters)
    artifacts.mcp_client_tools.append(
        {
            **_node_record(item, source_path, workflow_id),
            "selection_mode": mode,
            "selected_tool_count": len(selected),
        }
    )
    if mode in {"all", "all_except"} and not artifacts.tool_inventory_files:
        _dynamic(
            artifacts,
            kind="mcp_client_wildcard",
            item=item,
            source_path=source_path,
            reason="MCP Client Tool exposes All or All Except without a local inventory.",
            warnings=warnings,
        )
    names = selected or [
        f"{_redact_text(item.name) or item.name}.*"
        if mode in {"all", "all_except"}
        else _tool_name(item)
    ]
    tools = [
        _base_tool(
            item,
            source_id=source_id,
            source_path=source_path,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            workflow_error_workflow=workflow_error_workflow,
            source_type="n8n_mcp_client_tool",
            selected_mcp_tool=name,
        )
        for name in names
    ]
    if mode in {"all", "all_except"}:
        for tool in tools:
            tool.annotations["wildcard_tools"] = True
            tool.annotations["tool_selection_mode"] = mode
    return tools


def _record_workflow_resolution(
    item: _NodeItem,
    source_path: str,
    artifacts: N8nArtifacts,
    node_by_id: dict[str, _NodeItem],
    node_by_name: dict[str, _NodeItem],
    warnings: list[str],
) -> None:
    target = _top_level_string(
        item.parameters,
        {
            "workflowId",
            "workflow_id",
            "workflowName",
            "workflow",
            "targetWorkflow",
        },
    )
    if target and not _is_runtime_expression(target):
        if target in node_by_id or target in node_by_name:
            return
        # A DB workflow id can be valid at runtime but is not reviewable from
        # local files unless an explicit inventory/sub-workflow is present.
        _dynamic(
            artifacts,
            kind="unresolved_workflow",
            item=item,
            source_path=source_path,
            reason="Call Workflow Tool references a workflow id/name not resolved locally.",
            warnings=warnings,
        )
    elif target and _is_runtime_expression(target):
        _dynamic(
            artifacts,
            kind="unresolved_workflow",
            item=item,
            source_path=source_path,
            reason="Call Workflow Tool target uses a runtime expression.",
            warnings=warnings,
        )


def _load_inventory_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    artifacts: N8nArtifacts,
) -> LoadedToolSource | None:
    source = ToolSourceConfig(
        id=f"n8n_inventory:{ref.path}",
        type="mcp",
        path=ref.path,
        optional=ref.optional,
    )
    try:
        loaded = load_mcp_tools(source, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional n8n tool inventory {ref.path!r} failed to load.")
        return None
    artifacts.tool_inventory_files.append(
        _display_path(resolve_input_path(base_dir, ref.path), base_dir)
    )
    for tool in loaded.tools:
        tool.source_type = "n8n_inventory"
        tool.annotations["n8n_inventory"] = True
    return LoadedToolSource(
        source_id=loaded.source_id,
        source_type="n8n_inventory",
        tools=loaded.tools,
        warnings=loaded.warnings,
    )


def _load_credential_stubs(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    artifacts: N8nArtifacts,
) -> None:
    for path in _artifact_paths(refs, base_dir, artifacts.warnings, label="n8n credential stub"):
        data = load_structured_file(path)
        _append_unique(artifacts.credential_stub_files, _display_path(path, base_dir))
        for entry in _credential_entries(data):
            artifacts.credential_stubs.append(entry)


def _load_structured_refs(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    target: list[str],
    warnings: list[str],
    *,
    label: str,
) -> None:
    for path in _artifact_paths(refs, base_dir, warnings, label=label):
        load_structured_file(path)
        _append_unique(target, _display_path(path, base_dir))


def _artifact_paths(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    warnings: list[str],
    *,
    label: str,
) -> list[Path]:
    paths: list[Path] = []
    for ref in refs:
        try:
            path = resolve_input_path(base_dir, ref.path)
        except InputParseError:
            if not ref.optional:
                raise
            warnings.append(f"Optional {label} {ref.path!r} failed to load.")
            continue
        if not path.exists():
            if not ref.optional:
                raise InputParseError(f"Input file not found: {path}")
            warnings.append(f"Optional {label} {ref.path!r} failed to load.")
            continue
        if path.is_dir():
            paths.extend(
                sorted(
                    (
                        item
                        for item in path.rglob("*")
                        if item.is_file()
                        and item.suffix.lower() in {".json", ".yaml", ".yml"}
                        and not _skip_path(item, path)
                    ),
                    key=lambda item: _display_path(item, base_dir),
                )
            )
        else:
            paths.append(path)
    return paths


def _credential_entries(data: Any) -> list[dict[str, Any]]:
    raw_entries: list[Any]
    if isinstance(data, list):
        raw_entries = data
    elif isinstance(data, dict) and isinstance(data.get("credentials"), list):
        raw_entries = data["credentials"]
    elif isinstance(data, dict):
        raw_entries = [data]
    else:
        raw_entries = []
    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        name = _string_or_none(raw.get("name"))
        scopes = raw.get("scopes") or raw.get("oauthScopes") or []
        entries.append(
            {
                "id": _string_or_none(raw.get("id")),
                "type": _string_or_none(raw.get("type")),
                "name_present": bool(name),
                "scopes": [str(scope) for scope in scopes] if isinstance(scopes, list) else [],
            }
        )
    return entries


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


def _skip_path(path: Path, root: Path) -> bool:
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return True
    return any(
        part in SKIP_DIRS or any(part.startswith(prefix) for prefix in SKIP_DIR_PREFIXES)
        for part in parts
    )


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


def _tool_name(item: _NodeItem) -> str:
    for key in ("toolName", "name", "descriptionType"):
        value = _string_or_none(item.parameters.get(key))
        if value and key != "descriptionType":
            return _redact_text(value)
    return _redact_text(item.name) or item.name


def _tool_description(item: _NodeItem) -> str | None:
    for key in (
        "description",
        "toolDescription",
        "tool_description",
        "textDescription",
    ):
        value = _string_or_none(item.parameters.get(key))
        if value:
            return value
    return None


def _input_schema(item: _NodeItem) -> dict[str, Any]:
    from_ai = _from_ai_parameters(item.parameters)
    if from_ai:
        return {
            "type": "object",
            "properties": {
                param["name"]: {
                    "type": param["type"],
                    **({"description": param["description"]} if param["description"] else {}),
                }
                for param in from_ai
            },
            "required": [param["name"] for param in from_ai],
        }
    if isinstance(item.parameters.get("inputSchema"), dict):
        return _redact_structured_strings(item.parameters["inputSchema"])
    fields = item.parameters.get("fields") or item.parameters.get("workflowInputs")
    if isinstance(fields, list):
        properties: dict[str, Any] = {}
        required: list[str] = []
        for raw in fields:
            if not isinstance(raw, dict):
                continue
            name = _redact_text(_string_or_none(raw.get("name")))
            if not name:
                continue
            properties[name] = {
                "type": _schema_type(_string_or_none(raw.get("type"))),
                **(
                    {"description": _redact_text(str(raw["description"]))}
                    if raw.get("description")
                    else {}
                ),
            }
            if raw.get("required") is True:
                required.append(name)
        if properties:
            return {"type": "object", "properties": properties, "required": required}
    return {"type": "object", "properties": {}, "required": []}


def _output_schema(item: _NodeItem) -> dict[str, Any]:
    if _tool_node_kind(item) == "code_tool":
        return {}
    if isinstance(item.parameters.get("outputSchema"), dict):
        return _redact_structured_strings(item.parameters["outputSchema"])
    return {}


def _from_ai_parameters(value: Any) -> list[dict[str, str | None]]:
    params: dict[str, dict[str, str | None]] = {}
    for text in _string_values(value):
        for match in FROM_AI_RE.finditer(text):
            name = _redact_text(match.group(1)) or match.group(1)
            description = _redact_text(match.group(2)) if match.group(2) else None
            raw_type = match.group(3)
            params[name] = {
                "name": name,
                "description": description,
                "type": _schema_type(raw_type),
            }
    return [params[name] for name in sorted(params)]


def _schema_type(value: str | None) -> str:
    normalized = (value or "string").lower()
    if normalized in {"number", "integer", "boolean", "array", "object", "string"}:
        return normalized
    if normalized in {"json", "any"}:
        return "object"
    return "string"


def _auth_info(item: _NodeItem) -> AuthInfo:
    refs = _credential_refs(item)
    credential_type = refs[0]["type"] if refs else None
    scopes: list[str] = []
    for ref in refs:
        if ref.get("type"):
            scopes.append(f"n8n:{ref['type']}")
    return AuthInfo(
        type=credential_type,
        scopes=scopes,
        credential_mode="n8n_credential" if refs else None,
        source="n8n_credentials",
    )


def _credential_refs(item: _NodeItem) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for key, raw in item.credentials.items():
        if isinstance(raw, dict):
            refs.append(
                {
                    "type": _string_or_none(raw.get("type")) or str(key),
                    "id": _string_or_none(raw.get("id")),
                    "name_present": bool(_string_or_none(raw.get("name"))),
                }
            )
        elif isinstance(raw, str):
            refs.append({"type": str(key), "id": raw, "name_present": False})
    return refs


def _record_credentials(
    item: _NodeItem,
    source_path: str,
    workflow_id: str,
    artifacts: N8nArtifacts,
) -> None:
    for ref in _credential_refs(item):
        artifacts.credential_refs.append(
            {
                **ref,
                "source_ref": f"{source_path}#node:{item.node_id}",
                "node_id": item.node_id,
                "node_type": item.node_type,
                "workflow_id": workflow_id,
            }
        )


def _risk_hints(item: _NodeItem, *, method: str | None) -> list[ToolRiskHint]:
    hints: list[ToolRiskHint] = []
    kind = _tool_node_kind(item)
    if kind == "code_tool":
        _add_hint(hints, "code_execution", "high", {"node_type": item.node_type})
    if method and method not in {"GET", "HEAD", "OPTIONS"}:
        _add_hint(hints, "external_write", "medium", {"method": method})
    for ref in _credential_refs(item):
        credential_type = str(ref.get("type") or "").lower()
        if any(token in credential_type for token in ("stripe", "paypal", "billing")):
            _add_hint(hints, "financial_action", "medium", {"credential_type": ref.get("type")})
        if any(
            token in credential_type
            for token in ("gmail", "mail", "slack", "twilio", "sms", "discord")
        ):
            _add_hint(
                hints,
                "customer_communication",
                "medium",
                {"credential_type": ref.get("type")},
            )
        if any(
            token in credential_type
            for token in ("aws", "azure", "gcp", "kubernetes", "github")
        ):
            _add_hint(
                hints,
                "infrastructure_change",
                "medium",
                {"credential_type": ref.get("type")},
            )
        if any(
            token in credential_type
            for token in ("postgres", "mysql", "database", "sheets", "notion")
        ):
            _add_hint(
                hints,
                "sensitive_data_access",
                "medium",
                {"credential_type": ref.get("type")},
            )
    return hints


def _add_hint(
    hints: list[ToolRiskHint],
    tag: str,
    confidence: str,
    evidence: dict[str, Any],
) -> None:
    if any(hint.tag == tag and hint.confidence == confidence for hint in hints):
        return
    hints.append(
        ToolRiskHint(
            tag=tag,
            source="n8n_static",
            confidence=confidence,
            evidence=evidence,
        )
    )


def _http_method(item: _NodeItem) -> str | None:
    for key in ("method", "requestMethod", "httpMethod"):
        value = _string_or_none(item.parameters.get(key))
        if value and value.upper() in HTTP_METHODS:
            return value.upper()
    return None


def _http_path_hint(item: _NodeItem) -> str | None:
    value = _top_level_string(item.parameters, {"url", "path", "endpoint"})
    if not value:
        return None
    if "://" in value:
        value = value.split("://", 1)[1].split("/", 1)[-1]
    return _redact_text(value[:200])


def _record_tool_artifact(
    kind: str,
    tool: Tool,
    item: _NodeItem,
    source_path: str,
    workflow_id: str,
    artifacts: N8nArtifacts,
) -> None:
    record = {
        "name": tool.name,
        "source_ref": tool.source_ref,
        "node_id": item.node_id,
        "node_type": item.node_type,
        "workflow_id": workflow_id,
    }
    execution_control = _execution_control(item)
    if execution_control:
        record["execution"] = execution_control
    artifacts.tools.append(record)
    if kind == "workflow_tool":
        artifacts.workflow_tools.append(record)
    elif kind == "code_tool":
        artifacts.code_tools.append(record)
    elif kind == "http_tool":
        artifacts.http_tools.append(record)
    if tool.source_type == "mcp":
        artifacts.mcp_server_exposed_tools.append(
            {
                "source_ref": source_path,
                "node_id": item.node_id,
                "exposed_tool": tool.name,
            }
        )


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


def _redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = value
    for kind, pattern in SECRET_PATTERNS:
        redacted = pattern.sub(
            lambda _match, secret_kind=kind: f"[REDACTED:{secret_kind}]",
            redacted,
        )
    return redacted


def _redact_structured_strings(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value) or value
    if isinstance(value, dict):
        return {
            (_redact_text(str(key)) or str(key)): _redact_structured_strings(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_structured_strings(item) for item in value]
    return value


def _selection_mode(parameters: dict[str, Any]) -> str:
    value = _top_level_string(
        parameters,
        {"toolSelection", "toolsToInclude", "toolSelectionMode"},
    )
    normalized = (value or "").lower().replace(" ", "_").replace("-", "_")
    if normalized in {"all", "all_tools", "alltools"}:
        return "all"
    if normalized in {"all_except", "allexcept"}:
        return "all_except"
    if normalized in {"selected", "selected_tools", "specific"}:
        return "selected"
    selected = _selected_mcp_tools(parameters)
    return "selected" if selected else "unknown"


def _is_unfiltered_mode(parameters: dict[str, Any]) -> bool:
    return _selection_mode(parameters) in {"all", "all_except"}


def _selected_mcp_tools(parameters: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("tools", "toolNames", "selectedTools", "includeTools", "toolName"):
        raw = parameters.get(key)
        if isinstance(raw, str):
            if raw.strip():
                values.append(raw.strip())
        elif isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
        elif isinstance(raw, dict):
            values.extend(_named_values(raw))
    return sorted(dict.fromkeys(_redact_text(value) or value for value in values))


def _named_values(value: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for raw in value.values():
        if isinstance(raw, str) and raw.strip():
            names.append(raw.strip())
        elif isinstance(raw, dict):
            name = _string_or_none(raw.get("name") or raw.get("toolName"))
            if name:
                names.append(name)
    return names


def _top_level_string(value: dict[str, Any], keys: set[str]) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


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


def _is_runtime_expression(value: str | None) -> bool:
    return bool(value and ("{{" in value or "$json" in value or "$node" in value))


def _is_community_tool(item: _NodeItem) -> bool:
    lower = item.node_type.lower()
    if any(lower.startswith(prefix) for prefix in BUILTIN_N8N_PREFIXES):
        return False
    return ".tool" in lower or "tool" in lower


def _is_ingress_type(lower_node_type: str) -> bool:
    return lower_node_type.endswith(
        (
            ".webhook",
            ".chattrigger",
            ".manualtrigger",
            ".formtrigger",
        )
    )


def _is_human_review_node(item: _NodeItem) -> bool:
    compact_type = item.node_type.lower().replace("-", "").replace("_", "")
    return "sendandwait" in compact_type


def _node_sort_key(node_by_name: dict[str, _NodeItem], name: str) -> tuple[int, str]:
    item = node_by_name.get(name)
    return (item.index if item else 999999, name)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _stable_identifier_hash(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _display_path(path: Path, base_dir: Path) -> str:
    return manifest_relative_path(str(path), base_dir)
