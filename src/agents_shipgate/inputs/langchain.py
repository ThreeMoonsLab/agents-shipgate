from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import (
    AgentsShipgateManifest,
    ArtifactPathConfig,
    ToolSourceConfig,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import AuthInfo, LangChainArtifacts, LoadedToolSource, Tool
from agents_shipgate.inputs.common import resolve_input_path, stable_tool_id, tool_name_warning
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.python_static import (
    display_path,
    dotted_name,
    first_string_arg,
    function_input_schema,
    function_output_schema,
    function_signature,
    keyword,
    keyword_name,
    keyword_string,
    last_name,
    parse_python_file,
    pydantic_model_schemas,
)

TOOL_DECORATOR_MODULES = {"langchain.tools", "langchain_core.tools"}
STRUCTURED_TOOL_NAMES = {
    "StructuredTool",
    "langchain.tools.StructuredTool",
    "langchain_core.tools.StructuredTool",
}
AGENT_BINDING_CALLS = {"create_agent", "create_react_agent"}


def load_langchain_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> tuple[list[LoadedToolSource], LangChainArtifacts | None]:
    source_refs = [source for source in manifest.tool_sources if source.type == "langchain"]
    config = manifest.langchain
    if not source_refs and (config is None or not config.has_inputs()):
        return [], None

    artifacts = LangChainArtifacts()
    loaded_sources: list[LoadedToolSource] = []
    for source in source_refs:
        try:
            loaded_sources.extend(_load_langchain_source(source, base_dir, artifacts))
        except InputParseError:
            if not source.optional:
                raise
            warning = f"Optional LangChain source {source.id!r} failed to load."
            artifacts.warnings.append(warning)
            loaded_sources.append(
                LoadedToolSource(source_id=source.id, source_type="langchain", warnings=[warning])
            )

    if config:
        for entrypoint in config.python_entrypoints:
            loaded_sources.extend(
                _load_python_ref(
                    entrypoint,
                    base_dir,
                    source_id=f"langchain:{entrypoint.path}",
                    artifacts=artifacts,
                )
            )
        for inventory in config.tool_inventories:
            loaded = _load_inventory_ref(
                inventory,
                base_dir,
                source_id=f"langchain_inventory:{inventory.path}",
                artifacts=artifacts,
            )
            if loaded:
                loaded_sources.append(loaded)

    artifacts.warnings = sorted(dict.fromkeys(artifacts.warnings))
    artifacts.dynamic_tool_surfaces = sorted(
        artifacts.dynamic_tool_surfaces,
        key=lambda item: (str(item.get("source_ref") or ""), int(item.get("line") or 0), str(item.get("reason") or "")),
    )
    return loaded_sources, artifacts


def _load_langchain_source(
    source: ToolSourceConfig,
    base_dir: Path,
    artifacts: LangChainArtifacts,
) -> list[LoadedToolSource]:
    assert source.path is not None
    ref = ArtifactPathConfig(path=source.path, optional=source.optional)
    path = _resolve_existing_path(ref, base_dir)
    if path.is_dir():
        python_files = sorted(path.glob("*.py"))
        if not python_files:
            raise InputParseError(f"LangChain source directory has no Python files: {path}")
        loaded: list[LoadedToolSource] = []
        for python_file in python_files:
            loaded.extend(_load_python_path(python_file, base_dir, source.id, source.path, artifacts))
        return loaded
    if path.suffix.lower() != ".py":
        raise InputParseError(f"LangChain source must be a Python file or directory: {path}")
    return _load_python_path(path, base_dir, source.id, source.path, artifacts)


def _load_python_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    *,
    source_id: str,
    artifacts: LangChainArtifacts,
) -> list[LoadedToolSource]:
    try:
        path = _resolve_existing_path(ref, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional LangChain Python entrypoint {ref.path!r} failed to load.")
        return []
    return _load_python_path(path, base_dir, source_id, ref.path, artifacts)


def _load_python_path(
    path: Path,
    base_dir: Path,
    source_id: str,
    source_ref: str,
    artifacts: LangChainArtifacts,
) -> list[LoadedToolSource]:
    if path.is_dir():
        loaded: list[LoadedToolSource] = []
        for python_file in sorted(path.glob("*.py")):
            loaded.extend(_load_python_path(python_file, base_dir, source_id, source_ref, artifacts))
        return loaded
    tree = parse_python_file(path, label="LangChain")
    display = display_path(path, base_dir)
    artifacts.python_entrypoints.append(display)
    extractor = _LangChainExtractor(tree, source_id, display, artifacts)
    tools, warnings = extractor.extract()
    return [
        LoadedToolSource(
            source_id=source_id,
            source_type="langchain",
            tools=tools,
            warnings=warnings,
        )
    ]


def _load_inventory_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    *,
    source_id: str,
    artifacts: LangChainArtifacts,
) -> LoadedToolSource | None:
    source = ToolSourceConfig(id=source_id, type="mcp", path=ref.path, optional=ref.optional)
    try:
        loaded = load_mcp_tools(source, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional LangChain tool inventory {ref.path!r} failed to load.")
        return None
    artifacts.tool_inventory_files.append(display_path(resolve_input_path(base_dir, ref.path), base_dir))
    loaded.source_type = "langchain_inventory"
    for tool in loaded.tools:
        tool.source_type = "langchain_inventory"
        tool.annotations["langchain_inventory"] = True
        tool.extraction_confidence = "high"
        tool.extraction["confidence"] = "high"
    return loaded


class _LangChainExtractor:
    def __init__(
        self,
        tree: ast.Module,
        source_id: str,
        source_ref: str,
        artifacts: LangChainArtifacts,
    ) -> None:
        self.tree = tree
        self.source_id = source_id
        self.source_ref = source_ref
        self.artifacts = artifacts
        self.schemas = pydantic_model_schemas(tree)
        self.functions = {
            node.name: node
            for node in _ordered_nodes(tree, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.tool_decorators = self._tool_decorator_names()
        self.tool_vars: dict[str, Tool] = {}
        self.list_vars: dict[str, list[str] | None] = {}
        self.warnings: list[str] = []

    def extract(self) -> tuple[list[Tool], list[str]]:
        for node in _ordered_nodes(self.tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._record_decorated_tool(node)
        for node in _ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            self._record_structured_tool(node)
        for node in _ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            self._record_list_assignment(node)
        for call in _ordered_nodes(self.tree, (ast.Call,)):
            self._record_tool_surface(call)
        warnings = sorted(dict.fromkeys(self.warnings))
        self.artifacts.warnings.extend(warnings)
        return _unique_tools(self.tool_vars.values()), warnings

    def _tool_decorator_names(self) -> set[str]:
        names = {"tool", "langchain.tools.tool", "langchain_core.tools.tool"}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module in TOOL_DECORATOR_MODULES:
                for alias in node.names:
                    if alias.name == "tool":
                        names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in TOOL_DECORATOR_MODULES:
                        names.add(f"{alias.asname or alias.name}.tool")
        return names

    def _record_decorated_tool(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        decorator = self._tool_decorator(node)
        if decorator is None:
            return
        tool_name = _decorator_tool_name(decorator) or node.name
        args_schema = self._schema_for_call(decorator, getattr(decorator, "lineno", node.lineno))
        input_schema, parameters = function_input_schema(node, schema=args_schema)
        description = _decorator_description(decorator) or ast.get_docstring(node)
        tool = _function_tool(
            node,
            name=tool_name,
            description=description,
            input_schema=input_schema,
            parameters=parameters,
            source_id=self.source_id,
            source_ref=self.source_ref,
            source_type="langchain_function",
            extraction_method="langchain_tool_decorator_ast",
        )
        self._add_tool(node.name, tool, "function_tools")

    def _tool_decorator(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Call | ast.Name | None:
        for decorator in node.decorator_list:
            name = dotted_name(decorator.func) if isinstance(decorator, ast.Call) else dotted_name(decorator)
            if name in self.tool_decorators:
                return decorator
        return None

    def _record_structured_tool(self, node: ast.Assign | ast.AnnAssign) -> None:
        call = _assignment_call(node)
        if call is None or last_name(call.func) != "from_function":
            return
        owner_name = dotted_name(call.func)
        if not owner_name or not any(owner_name.startswith(name) for name in STRUCTURED_TOOL_NAMES):
            return
        function_name = keyword_name(call, "func") or (dotted_name(call.args[0]) if call.args else None)
        function = self.functions.get(function_name or "")
        if function is None:
            self._dynamic(
                "structured_tool",
                getattr(call, "lineno", 0),
                f"StructuredTool.from_function references unresolved function {function_name!r}",
            )
            return
        tool_name = keyword_string(call, "name") or function.name
        args_schema = self._schema_for_call(call, call.lineno)
        input_schema, parameters = function_input_schema(function, schema=args_schema)
        description = keyword_string(call, "description") or ast.get_docstring(function)
        tool = _function_tool(
            function,
            name=tool_name,
            description=description,
            input_schema=input_schema,
            parameters=parameters,
            source_id=self.source_id,
            source_ref=self.source_ref,
            source_type="langchain_structured_tool",
            extraction_method="langchain_structured_tool_ast",
        )
        target = _assignment_target(node) or tool_name
        self._add_tool(target, tool, "structured_tools")

    def _record_list_assignment(self, node: ast.Assign | ast.AnnAssign) -> None:
        target = _assignment_target(node)
        value = _assignment_value(node)
        if target is None or value is None:
            return
        names = self._resolve_tool_names(value)
        if names is not None:
            self.list_vars[target] = names
        elif isinstance(value, ast.List | ast.Tuple):
            self.list_vars[target] = None

    def _record_tool_surface(self, call: ast.Call) -> None:
        call_kind = last_name(call.func)
        if call_kind in AGENT_BINDING_CALLS:
            tools_expr = keyword(call, "tools")
            if tools_expr is None and len(call.args) > 1:
                tools_expr = call.args[1]
            self._record_binding("agent", call, tools_expr)
        elif call_kind == "ToolNode":
            self._record_binding("tool_node", call, call.args[0] if call.args else None)
        elif isinstance(call.func, ast.Attribute) and call.func.attr == "bind_tools":
            self._record_binding("bind_tools", call, call.args[0] if call.args else keyword(call, "tools"))

    def _record_binding(self, kind: str, call: ast.Call, tools_expr: ast.AST | None) -> None:
        if tools_expr is None:
            return
        names = self._resolve_tool_names(tools_expr)
        if names is None:
            self._dynamic(kind, call.lineno, _dynamic_reason(tools_expr))
            return
        record = {"source_ref": self.source_ref, "line": call.lineno, "tools": names}
        if kind == "tool_node":
            self.artifacts.tool_nodes.append(record)
        else:
            record["kind"] = kind
            self.artifacts.agent_bindings.append(record)

    def _resolve_tool_names(self, node: ast.AST) -> list[str] | None:
        if isinstance(node, ast.Name):
            if node.id in self.list_vars:
                return self.list_vars[node.id]
            if node.id in self.tool_vars:
                return [self.tool_vars[node.id].name]
            return None
        if isinstance(node, ast.List | ast.Tuple):
            names: list[str] = []
            for element in node.elts:
                if not isinstance(element, ast.Name) or element.id not in self.tool_vars:
                    return None
                names.append(self.tool_vars[element.id].name)
            return names
        return None

    def _schema_for_call(self, call: ast.Call | ast.Name, line: int) -> dict[str, Any] | None:
        if not isinstance(call, ast.Call):
            return None
        schema_name = keyword_name(call, "args_schema")
        if not schema_name:
            return None
        schema = self.schemas.get(schema_name)
        if schema is None:
            self._dynamic(
                "args_schema",
                line,
                f"args_schema {schema_name!r} is not defined in the same file",
            )
        return schema

    def _add_tool(self, variable_name: str, tool: Tool, artifact_field: str) -> None:
        if warning := tool_name_warning(tool.name):
            self.warnings.append(warning)
        self.tool_vars[variable_name] = tool
        getattr(self.artifacts, artifact_field).append(
            {"name": tool.name, "source_ref": self.source_ref, "line": _line(tool.source_location)}
        )

    def _dynamic(self, kind: str, line: int, reason: str) -> None:
        surface = {"kind": kind, "source_ref": self.source_ref, "line": line, "reason": reason}
        self.artifacts.dynamic_tool_surfaces.append(surface)
        self.warnings.append(
            f"LangChain {kind} at {self.source_ref}:{line} has dynamic tool surface: {reason}."
        )


def _function_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    name: str,
    description: str | None,
    input_schema: dict[str, Any],
    parameters: list[Any],
    source_id: str,
    source_ref: str,
    source_type: str,
    extraction_method: str,
) -> Tool:
    return Tool(
        id=stable_tool_id(name),
        name=name,
        description=description,
        source_type=source_type,
        source_id=source_id,
        source_ref=source_ref,
        source_location=f"{source_ref}:{node.lineno}",
        input_schema=input_schema,
        output_schema=function_output_schema(node),
        parameters=parameters,
        function_signature=function_signature(name, parameters, node),
        annotations={"framework": "langchain"},
        auth=AuthInfo(source="langchain_static"),
        extraction_confidence="medium",
        extraction={"method": extraction_method, "confidence": "medium"},
    )


def _decorator_tool_name(decorator: ast.Call | ast.Name) -> str | None:
    if isinstance(decorator, ast.Call):
        return first_string_arg(decorator) or keyword_string(decorator, "name")
    return None


def _decorator_description(decorator: ast.Call | ast.Name) -> str | None:
    return keyword_string(decorator, "description") if isinstance(decorator, ast.Call) else None


def _assignment_call(node: ast.Assign | ast.AnnAssign) -> ast.Call | None:
    value = _assignment_value(node)
    return value if isinstance(value, ast.Call) else None


def _assignment_value(node: ast.Assign | ast.AnnAssign) -> ast.AST | None:
    return node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None


def _assignment_target(node: ast.Assign | ast.AnnAssign) -> str | None:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _ordered_nodes(tree: ast.AST, node_types: tuple[type[Any], ...]) -> list[Any]:
    return sorted(
        (node for node in ast.walk(tree) if isinstance(node, node_types)),
        key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)),
    )


def _unique_tools(tools: Any) -> list[Tool]:
    unique: list[Tool] = []
    seen: set[tuple[str, str | None]] = set()
    for tool in tools:
        key = (tool.name, tool.source_location)
        if key in seen:
            continue
        unique.append(tool)
        seen.add(key)
    return unique


def _dynamic_reason(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return f"unresolved tool reference {node.id!r}"
    if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
        return "tool list is built by a comprehension"
    if isinstance(node, ast.Call):
        return "tool list comes from a runtime call"
    if isinstance(node, ast.List | ast.Tuple):
        return "tool list contains unresolved or inline tool expressions"
    return f"unsupported static tool expression {type(node).__name__}"


def _line(location: str | None) -> int | None:
    if not location or ":" not in location:
        return None
    try:
        return int(location.rsplit(":", 1)[1])
    except ValueError:
        return None


def _resolve_existing_path(ref: ArtifactPathConfig, base_dir: Path) -> Path:
    path = resolve_input_path(base_dir, ref.path)
    if not path.exists():
        raise InputParseError(f"Input file not found: {path}")
    return path
