"""n8n adapter — Tool extraction from workflow nodes.

Internal module. Builds the normalized ``Tool`` objects emitted by the
n8n adapter for the four tool flavours:

  - ``n8n_ai_tool`` — generic tools attached to an AI Agent node
  - ``n8n_workflow_tool`` — Call Workflow Tool sub-workflow references
  - ``n8n_code_tool`` — Code / Function tools
  - ``n8n_http_tool`` — HTTP Request Tool
  - ``n8n_mcp_client_tool`` — MCP Client Tool

Plus the projected ``mcp`` flavour when a tool is exposed by an MCP
Server Trigger.

The schema extraction layer (``_input_schema`` / ``_output_schema`` /
``_from_ai_parameters``) reads the n8n-specific ``parameters.fields``
list, the ``inputSchema`` / ``outputSchema`` dicts, and the
``$fromAI(name, description, type)`` macro that LangChain n8n nodes use
to declare LLM-callable parameters.

``_workflows.py`` calls back into ``_dynamic`` / ``_node_record`` /
``_execution_control`` here via late imports inside the functions
that need them — keeps the workflows → tools edge directional at
module-load time and lets the workflow record builders live where
their domain is.
"""

from __future__ import annotations

from typing import Any

from agents_shipgate.core.artifact_models import N8nArtifacts
from agents_shipgate.core.domain import Tool
from agents_shipgate.inputs.common import (
    json_pointer_escape,
    schema_to_parameters,
    stable_tool_id,
    tool_name_warning,
)
from agents_shipgate.inputs.n8n._auth_risk import (
    _auth_info,
    _http_path_hint,
    _risk_hints,
)
from agents_shipgate.inputs.n8n._common import (
    FROM_AI_RE,
    _http_method,
    _is_community_tool,
    _is_runtime_expression,
    _NodeItem,
    _redact_structured_strings,
    _redact_text,
    _schema_type,
    _source_type_for_kind,
    _string_or_none,
    _string_values,
    _tool_node_kind,
    _top_level_string,
)


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
    bound_agent_names: list[str] | None = None,
) -> list[Tool]:
    # Late import: workflows.py owns the record builders + dynamic-
    # surface emitter. Loading them at call time keeps the
    # workflows → tools edge one-way at module-load time.
    from agents_shipgate.inputs.n8n._workflows import _dynamic, _node_record

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
        mcp_tools = _mcp_client_tools(
            item,
            source_id=source_id,
            source_path=source_path,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            workflow_error_workflow=workflow_error_workflow,
            artifacts=artifacts,
            warnings=warnings,
        )
        for tool in mcp_tools:
            if not exposed_by_mcp:
                for agent_name in bound_agent_names or []:
                    tool.annotations.setdefault("agent_bindings", []).append(
                        {
                            "agent": agent_name,
                            "source_id": workflow_id,
                            "edge_type": "workflow",
                            "source": source_path,
                            "source_pointer": f"{source_path}#node:{item.node_id}",
                            "complete": True,
                        }
                    )
        return mcp_tools
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
    if not exposed_by_mcp:
        for agent_name in bound_agent_names or []:
            tool.annotations.setdefault("agent_bindings", []).append(
                {
                    "agent": agent_name,
                    "source_id": workflow_id,
                    "edge_type": "workflow",
                    "source": source_path,
                    "source_pointer": f"{source_path}#node:{item.node_id}",
                    "complete": True,
                }
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
    from agents_shipgate.inputs.n8n._workflows import _execution_control

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
    from agents_shipgate.inputs.n8n._workflows import _dynamic, _node_record

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
    elif mode == "unknown" and not artifacts.tool_inventory_files:
        _dynamic(
            artifacts,
            kind="mcp_client_selection_mode_unknown",
            item=item,
            source_path=source_path,
            reason=(
                "MCP Client Tool selection mode is unrecognized; "
                "static tool exposure cannot be proven."
            ),
            warnings=warnings,
        )
    names = selected or [
        f"{_redact_text(item.name) or item.name}.*"
        if mode in {"all", "all_except", "unknown"}
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
    if mode in {"all", "all_except", "unknown"}:
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
    from agents_shipgate.inputs.n8n._workflows import _dynamic

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


# --- Tool name / description / schema ---------------------------------------


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


# --- MCP Client Tool selection-mode helpers ---------------------------------


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
    if selected:
        return "selected"
    return "unknown" if value else "unspecified"


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


# --- Tool-artifact recording ------------------------------------------------


def _record_tool_artifact(
    kind: str,
    tool: Tool,
    item: _NodeItem,
    source_path: str,
    workflow_id: str,
    artifacts: N8nArtifacts,
) -> None:
    from agents_shipgate.inputs.n8n._workflows import _execution_control

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
