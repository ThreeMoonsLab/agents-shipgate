from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import ToolSourceConfig
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import AuthInfo, LoadedToolSource, Tool
from agents_shipgate.inputs.common import (
    load_structured_file,
    resolve_input_path,
    schema_to_parameters,
    stable_tool_id,
    tool_name_warning,
)


def load_mcp_tools(source: ToolSourceConfig, base_dir: Path) -> LoadedToolSource:
    assert source.path is not None
    path = resolve_input_path(base_dir, source.path)
    source_ref = source.path
    data = load_structured_file(path)
    warnings: list[str] = []

    if isinstance(data, list):
        raw_tools = data
    elif isinstance(data, dict):
        raw_tools = data.get("tools")
        if data.get("wildcard") is True or raw_tools == "*":
            if isinstance(raw_tools, list) and raw_tools:
                raise InputParseError(
                    "MCP source declares wildcard tool exposure and an explicit tools "
                    f"array: {path}. Use wildcard exposure or explicit tools, not both."
                )
            wildcard_warnings = ["MCP source declares wildcard tool exposure"]
            wildcard = Tool(
                id=stable_tool_id(f"{source.id}.*"),
                name=f"{source.id}.*",
                description="Wildcard MCP tool exposure.",
                source_type="mcp",
                source_id=source.id,
                source_ref=source_ref,
                annotations={"wildcard_tools": True},
                extraction_confidence="high",
                extraction={"method": "mcp_json", "confidence": "high"},
            )
            return LoadedToolSource(
                source_id=source.id,
                source_type="mcp",
                tools=[wildcard],
                warnings=wildcard_warnings,
            )
    else:
        raise InputParseError(f"MCP tools file must be an object or array: {path}")

    if not isinstance(raw_tools, list):
        raise InputParseError(f"MCP tools file must contain a tools array: {path}")

    tools: list[Tool] = []
    seen_names: set[str] = set()
    for raw in raw_tools:
        if not isinstance(raw, dict):
            warnings.append("Skipping non-object MCP tool entry")
            continue
        name = raw.get("name")
        if not name:
            warnings.append("Skipping MCP tool without name")
            continue
        name_text = str(name)
        if name_text in seen_names:
            warnings.append(f"Duplicate MCP tool name {name_text!r} in source {source.id!r}")
        seen_names.add(name_text)
        if warning := tool_name_warning(name_text):
            warnings.append(warning)
        input_schema = _first_present(raw, ["inputSchema", "input_schema"]) or {}
        output_schema = _first_present(raw, ["outputSchema", "output_schema"]) or {}
        annotations = raw.get("annotations") or {}
        auth = raw.get("auth") or {}
        tool = Tool(
            id=stable_tool_id(str(name)),
            name=name_text,
            description=raw.get("description"),
            source_type="mcp",
            source_id=source.id,
            source_ref=source_ref,
            input_schema=input_schema if isinstance(input_schema, dict) else {},
            output_schema=output_schema if isinstance(output_schema, dict) else {},
            parameters=schema_to_parameters(input_schema),
            annotations=annotations if isinstance(annotations, dict) else {},
            auth=AuthInfo(
                type=auth.get("type") if isinstance(auth, dict) else None,
                scopes=list(auth.get("scopes") or []) if isinstance(auth, dict) else [],
                credential_mode=auth.get("credential_mode") if isinstance(auth, dict) else None,
                source="mcp",
            ),
            owner=raw.get("owner"),
            extraction_confidence="high",
            extraction={"method": "mcp_json", "confidence": "high"},
        )
        tools.append(tool)

    return LoadedToolSource(
        source_id=source.id,
        source_type="mcp",
        tools=tools,
        warnings=warnings,
    )


def _first_present(raw: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return None
