from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.artifact_models import LangChainArtifacts
from agents_shipgate.core.domain import (
    LoadedToolSource,
    Tool,
)
from agents_shipgate.inputs._python_framework import (
    assignment_call,
    assignment_target,
    assignment_value,
    dynamic_reason,
    framework_function_tool,
    load_python_framework_sources,
    ordered_nodes,
    source_line,
    unique_tools,
)
from agents_shipgate.inputs.common import tool_name_warning
from agents_shipgate.inputs.config_trace import trace_config_binding
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.inputs.python_static import (
    dotted_name,
    first_string_arg,
    function_input_schema,
    keyword,
    keyword_name,
    keyword_string,
    last_name,
    pydantic_model_schemas,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
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
    loaded_sources = load_python_framework_sources(
        source_refs=source_refs,
        config=config,
        base_dir=base_dir,
        framework_type="langchain",
        framework_label="LangChain",
        inventory_source_type="langchain_inventory",
        inventory_annotation="langchain_inventory",
        artifacts=artifacts,
        extractor_factory=_LangChainExtractor,
    )
    return loaded_sources, artifacts


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
            for node in ordered_nodes(tree, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.tool_decorators = self._tool_decorator_names()
        self.tool_vars: dict[str, Tool] = {}
        self.discovered_tools: list[Tool] = []
        self.list_vars: dict[str, list[str] | None] = {}
        self.call_targets = {
            id(call): assignment_target(node)
            for node in ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign))
            if (call := assignment_call(node)) is not None
        }
        self.warnings: list[str] = []

    def extract(self) -> tuple[list[Tool], list[str]]:
        for node in ordered_nodes(self.tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._record_decorated_tool(node)
        for node in ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            self._record_structured_tool(node)
        for node in ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            self._record_list_assignment(node)
        for call in ordered_nodes(self.tree, (ast.Call,)):
            self._record_tool_surface(call)
        warnings = sorted(dict.fromkeys(self.warnings))
        self.artifacts.warnings.extend(warnings)
        return unique_tools(self.discovered_tools), warnings

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
        call = assignment_call(node)
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
        target = assignment_target(node) or tool_name
        self._add_tool(target, tool, "structured_tools")

    def _record_list_assignment(self, node: ast.Assign | ast.AnnAssign) -> None:
        target = assignment_target(node)
        value = assignment_value(node)
        if target is None or value is None:
            return
        names = self._resolve_tool_names(value)
        if names is not None:
            self.list_vars[target] = names
        elif isinstance(value, ast.List | ast.Tuple):
            self.list_vars[target] = None

    def _record_tool_surface(self, call: ast.Call) -> None:
        call_kind = last_name(call.func)
        agent_name = self.call_targets.get(id(call)) or "root"
        if call_kind in AGENT_BINDING_CALLS:
            tools_expr = keyword(call, "tools")
            if tools_expr is None and len(call.args) > 1:
                tools_expr = call.args[1]
            self._record_binding("agent", call, tools_expr, agent_name)
        elif call_kind == "ToolNode":
            self._record_binding("tool_node", call, call.args[0] if call.args else None, agent_name)
        elif isinstance(call.func, ast.Attribute) and call.func.attr == "bind_tools":
            self._record_binding("bind_tools", call, call.args[0] if call.args else keyword(call, "tools"), agent_name)

    def _record_binding(
        self,
        kind: str,
        call: ast.Call,
        tools_expr: ast.AST | None,
        agent_name: str,
    ) -> None:
        if tools_expr is None:
            return
        names = self._resolve_tool_names(tools_expr)
        if names is None:
            self._dynamic(kind, call.lineno, dynamic_reason(tools_expr), expr=tools_expr)
            return
        record = {
            "agent": agent_name,
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "line": call.lineno,
            "tools": names,
        }
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
        existing = self.tool_vars.get(variable_name)
        if existing and existing.source_location != tool.source_location:
            self._dynamic(
                "tool_shadowing",
                source_line(tool.source_location) or 0,
                (
                    f"tool variable {variable_name!r} is reassigned from "
                    f"{existing.name!r} to {tool.name!r}"
                ),
            )
        self.tool_vars[variable_name] = tool
        self.discovered_tools.append(tool)
        getattr(self.artifacts, artifact_field).append(
            {"name": tool.name, "source_ref": self.source_ref, "line": source_line(tool.source_location)}
        )

    def _dynamic(
        self, kind: str, line: int, reason: str, *, expr: ast.AST | None = None
    ) -> None:
        surface: dict[str, Any] = {
            "kind": kind,
            "source_ref": self.source_ref,
            "line": line,
            "reason": reason,
        }
        # Config-bound capability detection: when the dynamic tools
        # expression is statically traceable to a config read, record the
        # binding on the surface fact. Purely additive — an untraceable
        # expression leaves the fact byte-identical to the legacy shape.
        if isinstance(expr, ast.expr):
            trace = trace_config_binding(expr, tree=self.tree, line=line)
            if trace.binding == "config":
                surface["config_binding"] = "config"
                if trace.config_path:
                    surface["config_path"] = trace.config_path
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
    return framework_function_tool(
        node,
        framework="langchain",
        auth_source="langchain_static",
        name=name,
        description=description,
        input_schema=input_schema,
        parameters=parameters,
        source_id=source_id,
        source_ref=source_ref,
        source_type=source_type,
        extraction_method=extraction_method,
    )


def _decorator_tool_name(decorator: ast.Call | ast.Name) -> str | None:
    if isinstance(decorator, ast.Call):
        return first_string_arg(decorator) or keyword_string(decorator, "name")
    return None


def _decorator_description(decorator: ast.Call | ast.Name) -> str | None:
    return keyword_string(decorator, "description") if isinstance(decorator, ast.Call) else None


class LangChainAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_langchain_artifacts`.

    Framework-scoped. The dispatcher invokes ``load()`` once per scan
    when either a ``tool_sources`` entry of type ``langchain`` or the
    top-level ``manifest.langchain`` section is configured.
    """

    source_type: ClassVar[str] = "langchain"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = LangChainArtifacts

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="langchain",
        label="LangChain / LangGraph",
        reads=(
            "LangChain and LangGraph Python modules parsed with `ast`, plus any "
            "reviewed inventory `langchain.tool_inventories[]` declares."
        ),
        manifest_section="langchain",
        cells=(
            BoundaryCell(
                shape="export_artifact",
                status="extracted",
                reads=(
                    "A reviewed tool inventory in MCP export form. It is read as a "
                    "published contract, which is why it is the only LangChain route "
                    "that reaches `high`."
                ),
                emits=("langchain_inventory",),
                ceiling="high",
            ),
            BoundaryCell(
                shape="literal_registration",
                status="extracted",
                reads=(
                    "An `@tool`-decorated function, or a `StructuredTool` built with "
                    "a literal name, description, and an `args_schema` defined in "
                    "the same file."
                ),
                emits=("langchain_function", "langchain_structured_tool"),
                ceiling="medium",
            ),
            BoundaryCell(
                shape="factory",
                status="not_extracted",
                reads=(
                    "A tool produced by a call the parser cannot resolve — a "
                    "factory, a loaded toolkit — records a dynamic tool surface and "
                    "adds nothing to the catalog."
                ),
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="not_extracted",
                reads=(
                    "A tools list built by a comprehension, a config read, or a "
                    "rebound variable records a dynamic tool surface, with the "
                    "config path when the read is statically traceable."
                ),
            ),
        ),
    )

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        loaded_sources, artifacts = load_langchain_artifacts(manifest, base_dir)
        return LoadedAdapterResult(tool_sources=loaded_sources, artifact=artifacts)
