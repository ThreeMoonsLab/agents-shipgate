from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import ToolSourceConfig
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import AuthInfo, LoadedToolSource, Tool
from agents_shipgate.inputs.common import (
    HTTP_METHODS,
    PositionIndex,
    json_pointer_escape,
    load_structured_file_with_positions,
    manifest_relative_path,
    resolve_input_path,
    schema_to_parameters,
    stable_tool_id,
    tool_name_warning,
)

MAX_SCHEMA_RESOLVE_DEPTH = 32
MAX_SCHEMA_RESOLVE_NODES = 5000


def load_openapi_tools(source: ToolSourceConfig, base_dir: Path) -> LoadedToolSource:
    assert source.path is not None
    path = resolve_input_path(base_dir, source.path)
    document, positions = load_structured_file_with_positions(path)
    if not isinstance(document, dict):
        raise InputParseError(f"OpenAPI file must contain an object: {path}")
    if "openapi" not in document:
        raise InputParseError(f"OpenAPI file missing 'openapi' version: {path}")

    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise InputParseError(f"OpenAPI file missing paths object: {path}")

    tools: list[Tool] = []
    warnings: list[str] = []
    seen_names: set[str] = set()
    for api_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            raise InputParseError(f"OpenAPI path item {api_path} must be an object")
        path_parameters = path_item.get("parameters") or []
        for method, operation in path_item.items():
            method_lower = str(method).lower()
            if method_lower not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                raise InputParseError(
                    f"OpenAPI operation {method_lower.upper()} {api_path} must be an object"
                )
            try:
                tool = _operation_to_tool(
                    document=document,
                    source=source,
                    source_ref=source.path,
                    source_path=manifest_relative_path(source.path, base_dir),
                    api_path=str(api_path),
                    method=method_lower,
                    operation=operation,
                    path_parameters=path_parameters,
                    positions=positions,
                )
            except (RecursionError, MemoryError):
                raise
            except Exception as exc:  # noqa: BLE001 - source parse boundary.
                raise InputParseError(
                    "Unable to parse OpenAPI operation "
                    f"{method_lower.upper()} {api_path}: {type(exc).__name__}: {exc}"
                ) from exc
            if tool.name in seen_names:
                warnings.append(f"Duplicate OpenAPI tool name {tool.name!r} in source {source.id!r}")
            seen_names.add(tool.name)
            if warning := tool_name_warning(tool.name):
                warnings.append(warning)
            for ref in _unresolved_refs(tool.input_schema) + _unresolved_refs(tool.output_schema):
                warnings.append(
                    f"Unresolved OpenAPI $ref {ref!r} in tool {tool.name!r}; "
                    "external or missing refs are left as metadata."
                )
            tools.append(tool)

    return LoadedToolSource(
        source_id=source.id,
        source_type="openapi",
        tools=tools,
        warnings=warnings,
    )


def _operation_to_tool(
    *,
    document: dict[str, Any],
    source: ToolSourceConfig,
    source_ref: str | None,
    source_path: str | None,
    api_path: str,
    method: str,
    operation: dict[str, Any],
    path_parameters: list[Any],
    positions: PositionIndex,
) -> Tool:
    operation_id = operation.get("operationId") or _operation_name(method, api_path)
    request_schema = _extract_request_schema(document, operation)
    parameter_schema = _parameters_to_schema(document, path_parameters, operation.get("parameters") or [])
    input_schema = _merge_object_schemas(parameter_schema, request_schema)
    description = "\n".join(
        part for part in [operation.get("summary"), operation.get("description")] if part
    )
    annotations = _extract_annotations(operation)
    annotations["httpMethod"] = method.upper()
    annotations["path"] = api_path
    auth_type, scopes = _extract_security(document, operation)

    pointer = f"/paths/{json_pointer_escape(api_path)}/{method}"
    pos = positions.lookup(pointer)
    source_start_line: int | None = None
    source_start_column: int | None = None
    if pos is not None:
        source_start_line, source_start_column = pos

    # Note: `source_location` intentionally stays None for OpenAPI tools.
    # The legacy `path:line` string participates in `run_id` (see
    # `cli/scan.py:_run_id`), and v0.10 OpenAPI tools never set it.
    # Reviewers get the line via the structured `source_start_line` /
    # `source_pointer` fields; SARIF prefers those over the legacy string.
    return Tool(
        id=stable_tool_id(str(operation_id)),
        name=str(operation_id),
        description=description or None,
        source_type="openapi",
        source_id=source.id,
        source_ref=f"{source_ref}#{pointer}",
        source_path=source_path,
        source_start_line=source_start_line,
        source_start_column=source_start_column,
        source_pointer=pointer,
        input_schema=input_schema,
        output_schema=_extract_response_schema(document, operation),
        parameters=schema_to_parameters(input_schema),
        annotations=annotations,
        auth=AuthInfo(
            type=auth_type,
            scopes=scopes,
            source="openapi",
        ),
        extraction_confidence="high",
        extraction={"method": "openapi", "confidence": "high"},
    )


def _extract_request_schema(
    document: dict[str, Any], operation: dict[str, Any]
) -> dict[str, Any]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return {}
    request_body = _resolve_ref(document, request_body)
    content = request_body.get("content") or {}
    if not isinstance(content, dict):
        return {}
    media = content.get("application/json") or next(iter(content.values()), {})
    if not isinstance(media, dict):
        return {}
    schema = media.get("schema") or {}
    if not isinstance(schema, dict):
        return {}
    return _resolve_schema(document, schema)


def _extract_response_schema(
    document: dict[str, Any], operation: dict[str, Any]
) -> dict[str, Any]:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return {}
    response = responses.get("200") or responses.get("201") or next(iter(responses.values()), {})
    if not isinstance(response, dict):
        return {}
    response = _resolve_ref(document, response)
    content = response.get("content") or {}
    if not isinstance(content, dict):
        return {}
    media = content.get("application/json") or next(iter(content.values()), {})
    if not isinstance(media, dict):
        return {}
    schema = media.get("schema") or {}
    return _resolve_schema(document, schema) if isinstance(schema, dict) else {}


def _parameters_to_schema(
    document: dict[str, Any], path_parameters: list[Any], operation_parameters: list[Any]
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for raw in [*path_parameters, *operation_parameters]:
        if not isinstance(raw, dict):
            continue
        parameter = _resolve_ref(document, raw)
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        if not name:
            continue
        schema = parameter.get("schema") or {}
        if isinstance(schema, dict):
            properties[str(name)] = _resolve_schema(document, schema)
        else:
            properties[str(name)] = {}
        if parameter.get("required") is True:
            required.append(str(name))
    if not properties:
        return {}
    return {"type": "object", "properties": properties, "required": required}


def _merge_object_schemas(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if not left:
        return right or {}
    if not right:
        return left
    merged = {"type": "object", "properties": {}, "required": []}
    for schema in [left, right]:
        if schema.get("type") == "object" or "properties" in schema:
            merged["properties"].update(schema.get("properties") or {})
            merged["required"].extend(schema.get("required") or [])
        else:
            merged["properties"]["body"] = schema
            merged["required"].append("body")
    merged["required"] = sorted(set(merged["required"]))
    return merged


def _extract_security(document: dict[str, Any], operation: dict[str, Any]) -> tuple[str | None, list[str]]:
    security = operation.get("security", document.get("security") or [])
    schemes = ((document.get("components") or {}).get("securitySchemes") or {})
    scopes: list[str] = []
    auth_type: str | None = None
    if isinstance(security, list):
        for entry in security:
            if not isinstance(entry, dict):
                continue
            for scheme_name, scheme_scopes in entry.items():
                scheme = schemes.get(scheme_name) if isinstance(schemes, dict) else None
                if isinstance(scheme, dict):
                    auth_type = auth_type or scheme.get("type")
                if isinstance(scheme_scopes, list):
                    scopes.extend(str(scope) for scope in scheme_scopes)
    return auth_type, sorted(set(scopes))


def _extract_annotations(operation: dict[str, Any]) -> dict[str, Any]:
    annotations: dict[str, Any] = {}
    for source_key, output_key in {
        "x-readOnlyHint": "readOnlyHint",
        "x-destructiveHint": "destructiveHint",
        "x-idempotentHint": "idempotentHint",
        "x-openWorldHint": "openWorldHint",
        "x-retryPolicy": "retryPolicy",
    }.items():
        if source_key in operation:
            annotations[output_key] = operation[source_key]
    agents_shipgate = operation.get("x-agents-shipgate")
    if isinstance(agents_shipgate, dict):
        annotations.update(agents_shipgate)
    return annotations


def _resolve_schema(
    document: dict[str, Any],
    schema: dict[str, Any],
    seen_refs: set[str] | None = None,
    depth: int = 0,
    budget: list[int] | None = None,
) -> dict[str, Any]:
    if depth > MAX_SCHEMA_RESOLVE_DEPTH:
        return {"x-agents-shipgate-resolution-truncated": "max_depth"}
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > MAX_SCHEMA_RESOLVE_NODES:
        return {"x-agents-shipgate-resolution-truncated": "max_nodes"}
    seen_refs = set(seen_refs or set())
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref in seen_refs:
            return {"$ref": ref, "x-agents-shipgate-recursive-ref": True}
        seen_refs.add(ref)
    resolved = _resolve_ref(document, schema)
    if not isinstance(resolved, dict):
        return {}
    resolved = deepcopy(resolved)
    if "properties" in resolved and isinstance(resolved["properties"], dict):
        resolved["properties"] = {
            name: _resolve_schema(document, prop, seen_refs.copy(), depth + 1, budget) if isinstance(prop, dict) else prop
            for name, prop in resolved["properties"].items()
        }
    if "items" in resolved and isinstance(resolved["items"], dict):
        resolved["items"] = _resolve_schema(document, resolved["items"], seen_refs.copy(), depth + 1, budget)
    for combinator in ("oneOf", "anyOf", "allOf"):
        if combinator in resolved and isinstance(resolved[combinator], list):
            resolved[combinator] = [
                _resolve_schema(document, item, seen_refs.copy(), depth + 1, budget) if isinstance(item, dict) else item
                for item in resolved[combinator]
            ]
    return resolved


def _resolve_ref(document: dict[str, Any], value: dict[str, Any]) -> Any:
    ref = value.get("$ref")
    if not isinstance(ref, str):
        return value
    if not ref.startswith("#/"):
        return value
    current: Any = document
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return value
        current = current[part]
    return current


def _unresolved_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            refs.append(ref)
        for child in value.values():
            refs.extend(_unresolved_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_unresolved_refs(child))
    return refs


def _operation_name(method: str, path: str) -> str:
    safe_path = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    safe_path = safe_path.replace("-", "_") or "root"
    return f"{method}_{safe_path}"
