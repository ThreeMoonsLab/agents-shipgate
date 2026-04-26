from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import AgentsShipgateManifest, ToolSourceConfig
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import AuthInfo, LoadedToolSource, Tool, ToolParameter
from agents_shipgate.inputs.common import resolve_input_path, stable_tool_id

DEFAULT_FUNCTION_TOOL_DECORATORS = frozenset(
    {"function_tool", "agents.function_tool", "openai_agents.function_tool"}
)


def load_openai_sdk_static_tools(
    source: ToolSourceConfig, manifest: AgentsShipgateManifest, base_dir: Path
) -> LoadedToolSource:
    entrypoint = source.path or (manifest.agent.sdk.entrypoint if manifest.agent.sdk else None)
    if not entrypoint:
        return LoadedToolSource(
            source_id=source.id,
            source_type="openai_agents_sdk",
            warnings=["OpenAI Agents SDK source has no entrypoint"],
        )
    path = resolve_input_path(base_dir, entrypoint)
    if not path.exists():
        return LoadedToolSource(
            source_id=source.id,
            source_type="openai_agents_sdk",
            warnings=[f"OpenAI Agents SDK entrypoint not found: {path}"],
        )
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise InputParseError(
            f"Unable to parse OpenAI Agents SDK entrypoint {path}: {exc.msg}"
        ) from exc
    decorator_names = _function_tool_decorator_names(tree)
    tools = [
        _function_to_tool(node, source, entrypoint, decorator_names)
        for node in ast.walk(tree)
        if _is_function_tool(node, decorator_names)
    ]
    return LoadedToolSource(
        source_id=source.id,
        source_type="openai_agents_sdk",
        tools=tools,
    )


def _function_tool_decorator_names(tree: ast.Module) -> set[str]:
    names = set(DEFAULT_FUNCTION_TOOL_DECORATORS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"agents", "openai_agents"}:
            for alias in node.names:
                if alias.name == "function_tool":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"agents", "openai_agents"}:
                    names.add(f"{alias.asname or alias.name}.function_tool")
    return names


def _is_function_tool(node: ast.AST, decorator_names: set[str]) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for decorator in node.decorator_list:
        name = _decorator_name(decorator)
        if name in decorator_names:
            return True
    return False


def _decorator_name(decorator: ast.AST) -> str | None:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        prefix = _decorator_name(decorator.value)
        return f"{prefix}.{decorator.attr}" if prefix else decorator.attr
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    return None


def _function_to_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: ToolSourceConfig,
    source_ref: str,
    decorator_names: set[str],
) -> Tool:
    tool_name = _tool_name(node, decorator_names)
    parameters = _parameters(node)
    return_type = _annotation_to_string(node.returns)
    signature = f"{tool_name}({', '.join(param.name for param in parameters)})"
    if return_type:
        signature = f"{signature} -> {return_type}"
    properties: dict[str, Any] = {
        param.name: {"type": _json_schema_type(param.type)}
        for param in parameters
    }
    input_schema = {
        "type": "object",
        "properties": properties,
        "required": [param.name for param in parameters if param.required],
    }
    description = _description(node, decorator_names) or ast.get_docstring(node)
    return Tool(
        id=stable_tool_id(tool_name),
        name=tool_name,
        description=description,
        source_type="sdk_function",
        source_id=source.id,
        source_ref=source_ref,
        source_location=f"{source_ref}:{node.lineno}",
        input_schema=input_schema,
        output_schema={"type": _json_schema_type(return_type)} if return_type else {},
        parameters=parameters,
        function_signature=signature,
        auth=AuthInfo(source="sdk_static"),
        extraction_confidence="medium",
        extraction={"method": "openai_agents_sdk_ast", "confidence": "medium"},
    )


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ToolParameter]:
    parameters: list[ToolParameter] = []
    positional_args = [*node.args.posonlyargs, *node.args.args]
    positional_defaults: list[ast.expr | None] = [
        None for _ in range(len(positional_args) - len(node.args.defaults))
    ]
    positional_defaults.extend(node.args.defaults)
    for arg, default in zip(positional_args, positional_defaults, strict=True):
        if arg.arg in {"self", "ctx", "context"}:
            continue
        parameters.append(_parameter(arg, required=default is None))
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        if arg.arg in {"self", "ctx", "context"}:
            continue
        parameters.append(_parameter(arg, required=default is None))
    return parameters


def _parameter(arg: ast.arg, *, required: bool) -> ToolParameter:
    return ToolParameter(
        name=arg.arg,
        type=_annotation_to_string(arg.annotation),
        required=required,
    )


def _tool_name(
    node: ast.FunctionDef | ast.AsyncFunctionDef, decorator_names: set[str]
) -> str:
    return _decorator_kwarg_string(node, decorator_names, "name_override") or node.name


def _description(
    node: ast.FunctionDef | ast.AsyncFunctionDef, decorator_names: set[str]
) -> str | None:
    return _decorator_kwarg_string(node, decorator_names, "description_override")


def _decorator_kwarg_string(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator_names: set[str],
    kwarg_name: str,
) -> str | None:
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        if not call or _decorator_name(call.func) not in decorator_names:
            continue
        for keyword in call.keywords:
            if keyword.arg != kwarg_name or not isinstance(keyword.value, ast.Constant):
                continue
            value = keyword.value.value
            if isinstance(value, str) and value:
                return value
    return None


def _annotation_to_string(annotation: ast.AST | None) -> str | None:
    if annotation is None:
        return None
    return ast.unparse(annotation)


def _json_schema_type(annotation: str | None) -> str:
    if annotation in {"int", "float"}:
        return "number"
    if annotation == "bool":
        return "boolean"
    if annotation in {"list", "List"} or (annotation or "").startswith("list["):
        return "array"
    if annotation in {"dict", "Dict"} or (annotation or "").startswith("dict["):
        return "object"
    return "string"
