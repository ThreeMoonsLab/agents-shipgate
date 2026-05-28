from __future__ import annotations

import ast
from pathlib import Path
from typing import ClassVar, Literal

from agents_shipgate.core.domain import (
    AuthInfo,
    LoadedToolSource,
    Tool,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import resolve_input_path, stable_tool_id
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.inputs.python_static import (
    display_path,
    dotted_name,
    function_input_schema,
    function_output_schema,
    function_signature,
    parse_python_file,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)

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
    if path.is_dir():
        python_files = sorted(path.glob("*.py"))
        if not python_files:
            raise InputParseError(
                f"OpenAI Agents SDK source directory has no Python files: {path}"
            )
        tools = [
            tool
            for python_file in python_files
            for tool in _load_python_file(python_file, source, base_dir)
        ]
    elif path.suffix.lower() == ".py":
        tools = _load_python_file(path, source, base_dir, source_ref=entrypoint)
    else:
        raise InputParseError(
            f"OpenAI Agents SDK source must be a Python file or directory: {path}"
        )
    return LoadedToolSource(
        source_id=source.id,
        source_type="openai_agents_sdk",
        tools=tools,
    )


def _load_python_file(
    path: Path,
    source: ToolSourceConfig,
    base_dir: Path,
    *,
    source_ref: str | None = None,
) -> list[Tool]:
    try:
        tree = parse_python_file(path, label="OpenAI Agents SDK")
    except InputParseError as exc:
        message = str(exc).replace(
            "OpenAI Agents SDK Python entrypoint",
            "OpenAI Agents SDK entrypoint",
        )
        raise InputParseError(message) from exc
    ref = source_ref or display_path(path, base_dir)
    decorator_names = _function_tool_decorator_names(tree)
    return [
        _function_to_tool(node, source, ref, decorator_names)
        for node in ast.walk(tree)
        if _is_function_tool(node, decorator_names)
    ]


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
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    return dotted_name(decorator)


def _function_to_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: ToolSourceConfig,
    source_ref: str,
    decorator_names: set[str],
) -> Tool:
    tool_name = _tool_name(node, decorator_names)
    input_schema, parameters = function_input_schema(node)
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
        output_schema=function_output_schema(node),
        parameters=parameters,
        function_signature=function_signature(tool_name, parameters, node),
        auth=AuthInfo(source="sdk_static"),
        extraction_confidence="medium",
        extraction={"method": "openai_agents_sdk_ast", "confidence": "medium"},
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


class OpenAISDKAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_openai_sdk_static_tools`."""

    source_type: ClassVar[str] = "openai_agents_sdk"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
    artifact_class: ClassVar[type | None] = None

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        assert source is not None, "per_source adapter requires a source"
        return LoadedAdapterResult(
            tool_sources=[load_openai_sdk_static_tools(source, manifest, base_dir)]
        )
