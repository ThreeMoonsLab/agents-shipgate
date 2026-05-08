from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import ToolSourceConfig
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import AuthInfo, LoadedToolSource, Tool
from agents_shipgate.inputs.common import (
    load_structured_file_with_positions,
    manifest_relative_path,
    resolve_input_path,
    schema_to_parameters,
    stable_tool_id,
    tool_name_warning,
)


def load_mcp_tools(source: ToolSourceConfig, base_dir: Path) -> LoadedToolSource:
    assert source.path is not None
    path = resolve_input_path(base_dir, source.path)
    source_ref = source.path
    source_path = manifest_relative_path(source.path, base_dir)
    data, positions = load_structured_file_with_positions(path)
    warnings: list[str] = []

    pointer_prefix: str
    if isinstance(data, list):
        raw_tools = data
        pointer_prefix = ""
    elif isinstance(data, dict):
        raw_tools = data.get("tools")
        pointer_prefix = "/tools"
        if data.get("wildcard") is True or raw_tools == "*":
            if isinstance(raw_tools, list) and raw_tools:
                raise InputParseError(
                    "MCP source declares wildcard tool exposure and an explicit tools "
                    f"array: {path}. Use wildcard exposure or explicit tools, not both."
                )
            wildcard_warnings = ["MCP source declares wildcard tool exposure"]
            # Pick the pointer that actually triggered the wildcard
            # branch so reviewers jump to the offending line — `wildcard:
            # true` and `tools: '*'` are different signals on different
            # lines.
            wildcard_pointer = (
                "/wildcard" if data.get("wildcard") is True else "/tools"
            )
            wildcard_pos = positions.lookup(wildcard_pointer)
            wildcard_start_line: int | None = None
            wildcard_start_column: int | None = None
            if wildcard_pos is not None:
                wildcard_start_line, wildcard_start_column = wildcard_pos
            wildcard = Tool(
                id=stable_tool_id(f"{source.id}.*"),
                name=f"{source.id}.*",
                description="Wildcard MCP tool exposure.",
                source_type="mcp",
                source_id=source.id,
                source_ref=source_ref,
                source_path=source_path,
                source_start_line=wildcard_start_line,
                source_start_column=wildcard_start_column,
                source_pointer=wildcard_pointer,
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
    for index, raw in enumerate(raw_tools):
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
        pointer = f"{pointer_prefix}/{index}"
        pos = positions.lookup(pointer)
        source_start_line: int | None = None
        source_start_column: int | None = None
        if pos is not None:
            source_start_line, source_start_column = pos
        # `source_location` stays None: the legacy `path:line` string is
        # part of the `run_id` hash and v0.10 MCP tools never set it.
        # Reviewers get the line through the structured fields below.
        tool = Tool(
            id=stable_tool_id(str(name)),
            name=name_text,
            description=raw.get("description"),
            source_type="mcp",
            source_id=source.id,
            source_ref=source_ref,
            source_path=source_path,
            source_start_line=source_start_line,
            source_start_column=source_start_column,
            source_pointer=pointer,
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
