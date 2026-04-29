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
from agents_shipgate.core.models import AuthInfo, CrewAiArtifacts, LoadedToolSource, Tool
from agents_shipgate.inputs.common import resolve_input_path, stable_tool_id, tool_name_warning
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.python_static import (
    display_path,
    dotted_name,
    first_string_arg,
    function_input_schema,
    function_output_schema,
    function_parameters,
    function_signature,
    keyword,
    keyword_name,
    keyword_string,
    last_name,
    literal_string,
    parse_python_file,
    pydantic_model_schemas,
)

TOOL_DECORATOR_MODULES = {"crewai.tools"}


def load_crewai_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> tuple[list[LoadedToolSource], CrewAiArtifacts | None]:
    source_refs = [source for source in manifest.tool_sources if source.type == "crewai"]
    config = manifest.crewai
    if not source_refs and (config is None or not config.has_inputs()):
        return [], None

    artifacts = CrewAiArtifacts()
    loaded_sources: list[LoadedToolSource] = []
    for source in source_refs:
        try:
            loaded_sources.extend(_load_crewai_source(source, base_dir, artifacts))
        except InputParseError:
            if not source.optional:
                raise
            warning = f"Optional CrewAI source {source.id!r} failed to load."
            artifacts.warnings.append(warning)
            loaded_sources.append(
                LoadedToolSource(source_id=source.id, source_type="crewai", warnings=[warning])
            )

    if config:
        for entrypoint in config.python_entrypoints:
            loaded_sources.extend(
                _load_python_ref(
                    entrypoint,
                    base_dir,
                    source_id=f"crewai:{entrypoint.path}",
                    artifacts=artifacts,
                )
            )
        for inventory in config.tool_inventories:
            loaded = _load_inventory_ref(
                inventory,
                base_dir,
                source_id=f"crewai_inventory:{inventory.path}",
                artifacts=artifacts,
            )
            if loaded:
                loaded_sources.append(loaded)

    artifacts.warnings = sorted(dict.fromkeys(artifacts.warnings))
    artifacts.dynamic_tool_surfaces = sorted(
        artifacts.dynamic_tool_surfaces,
        key=lambda item: (
            str(item.get("source_ref") or ""),
            int(item.get("line") or 0),
            str(item.get("reason") or ""),
        ),
    )
    return loaded_sources, artifacts


def _load_crewai_source(
    source: ToolSourceConfig,
    base_dir: Path,
    artifacts: CrewAiArtifacts,
) -> list[LoadedToolSource]:
    assert source.path is not None
    ref = ArtifactPathConfig(path=source.path, optional=source.optional)
    path = _resolve_existing_path(ref, base_dir)
    if path.is_dir():
        python_files = sorted(path.glob("*.py"))
        if not python_files:
            raise InputParseError(f"CrewAI source directory has no Python files: {path}")
        loaded: list[LoadedToolSource] = []
        for python_file in python_files:
            loaded.extend(_load_python_path(python_file, base_dir, source.id, source.path, artifacts))
        return loaded
    if path.suffix.lower() != ".py":
        raise InputParseError(f"CrewAI source must be a Python file or directory: {path}")
    return _load_python_path(path, base_dir, source.id, source.path, artifacts)


def _load_python_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    *,
    source_id: str,
    artifacts: CrewAiArtifacts,
) -> list[LoadedToolSource]:
    try:
        path = _resolve_existing_path(ref, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional CrewAI Python entrypoint {ref.path!r} failed to load.")
        return []
    return _load_python_path(path, base_dir, source_id, ref.path, artifacts)


def _load_python_path(
    path: Path,
    base_dir: Path,
    source_id: str,
    source_ref: str,
    artifacts: CrewAiArtifacts,
) -> list[LoadedToolSource]:
    if path.is_dir():
        loaded: list[LoadedToolSource] = []
        for python_file in sorted(path.glob("*.py")):
            loaded.extend(_load_python_path(python_file, base_dir, source_id, source_ref, artifacts))
        return loaded
    tree = parse_python_file(path, label="CrewAI")
    display = display_path(path, base_dir)
    artifacts.python_entrypoints.append(display)
    extractor = _CrewAiExtractor(tree, source_id, display, artifacts)
    tools, warnings = extractor.extract()
    return [
        LoadedToolSource(
            source_id=source_id,
            source_type="crewai",
            tools=tools,
            warnings=warnings,
        )
    ]


def _load_inventory_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    *,
    source_id: str,
    artifacts: CrewAiArtifacts,
) -> LoadedToolSource | None:
    source = ToolSourceConfig(id=source_id, type="mcp", path=ref.path, optional=ref.optional)
    try:
        loaded = load_mcp_tools(source, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional CrewAI tool inventory {ref.path!r} failed to load.")
        return None
    artifacts.tool_inventory_files.append(display_path(resolve_input_path(base_dir, ref.path), base_dir))
    loaded.source_type = "crewai_inventory"
    for tool in loaded.tools:
        tool.source_type = "crewai_inventory"
        tool.annotations["crewai_inventory"] = True
        tool.extraction_confidence = "high"
        tool.extraction["confidence"] = "high"
    return loaded


class _CrewAiExtractor:
    def __init__(
        self,
        tree: ast.Module,
        source_id: str,
        source_ref: str,
        artifacts: CrewAiArtifacts,
    ) -> None:
        self.tree = tree
        self.source_id = source_id
        self.source_ref = source_ref
        self.artifacts = artifacts
        self.schemas = pydantic_model_schemas(tree)
        self.tool_decorators = self._tool_decorator_names()
        self.prebuilt_names = self._prebuilt_tool_names()
        self.tool_vars: dict[str, Tool] = {}
        self.list_vars: dict[str, list[str] | None] = {}
        self.agent_vars: set[str] = set()
        self.warnings: list[str] = []

    def extract(self) -> tuple[list[Tool], list[str]]:
        for node in _ordered_nodes(self.tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._record_decorated_tool(node)
        for node in _ordered_nodes(self.tree, (ast.ClassDef,)):
            self._record_class_tool(node)
        for node in _ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            self._record_prebuilt_tool(node)
        for node in _ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            self._record_list_assignment(node)
        for node in _ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            self._record_agent_assignment(node)
        for call in _ordered_nodes(self.tree, (ast.Call,)):
            self._record_agent_or_crew(call)
        warnings = sorted(dict.fromkeys(self.warnings))
        self.artifacts.warnings.extend(warnings)
        return _unique_tools(self.tool_vars.values()), warnings

    def _tool_decorator_names(self) -> set[str]:
        names = {"tool", "crewai.tools.tool"}
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

    def _prebuilt_tool_names(self) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module == "crewai_tools":
                for alias in node.names:
                    if alias.name.endswith("Tool"):
                        names.add(alias.asname or alias.name)
        return names

    def _record_decorated_tool(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        decorator = self._tool_decorator(node)
        if decorator is None:
            return
        tool_name = _decorator_tool_name(decorator) or node.name
        input_schema, parameters = function_input_schema(
            node,
            schema=self._schema_for_call(decorator, getattr(decorator, "lineno", node.lineno)),
        )
        description = ast.get_docstring(node)
        tool = _function_tool(
            node,
            name=tool_name,
            description=description,
            input_schema=input_schema,
            parameters=parameters,
            source_id=self.source_id,
            source_ref=self.source_ref,
            source_type="crewai_function",
            extraction_method="crewai_tool_decorator_ast",
        )
        self._add_tool(node.name, tool, "function_tools")

    def _tool_decorator(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Call | ast.Name | None:
        for decorator in node.decorator_list:
            name = dotted_name(decorator.func) if isinstance(decorator, ast.Call) else dotted_name(decorator)
            if name in self.tool_decorators:
                return decorator
        return None

    def _record_class_tool(self, node: ast.ClassDef) -> None:
        if not any(last_name(base) == "BaseTool" for base in node.bases):
            return
        name = _class_string_attr(node, "name") or node.name
        description = _class_string_attr(node, "description") or ast.get_docstring(node)
        schema_name = _class_name_attr(node, "args_schema")
        schema = self.schemas.get(schema_name or "")
        if schema_name and schema is None:
            self._dynamic(
                "args_schema",
                node.lineno,
                f"args_schema {schema_name!r} is not defined in the same file",
            )
        run_method = _class_method(node, "_run") or _class_method(node, "run")
        if run_method:
            input_schema, parameters = function_input_schema(run_method, schema=schema)
            output_schema = function_output_schema(run_method)
            signature = function_signature(name, parameters, run_method)
        else:
            input_schema = schema or {"type": "object", "properties": {}, "required": []}
            parameters = function_parameters(node) if isinstance(node, ast.FunctionDef) else []
            output_schema = {}
            signature = f"{name}()"
        tool = Tool(
            id=stable_tool_id(name),
            name=name,
            description=description,
            source_type="crewai_class_tool",
            source_id=self.source_id,
            source_ref=self.source_ref,
            source_location=f"{self.source_ref}:{node.lineno}",
            input_schema=input_schema,
            output_schema=output_schema,
            parameters=parameters,
            function_signature=signature,
            annotations={"framework": "crewai"},
            auth=AuthInfo(source="crewai_static"),
            extraction_confidence="medium",
            extraction={"method": "crewai_base_tool_ast", "confidence": "medium"},
        )
        self._add_tool(node.name, tool, "class_tools")

    def _record_prebuilt_tool(self, node: ast.Assign | ast.AnnAssign) -> None:
        target = _assignment_target(node)
        call = _assignment_call(node)
        if target is None or call is None:
            return
        class_name = last_name(call.func)
        if class_name in self.tool_vars and self.tool_vars[class_name].source_type == "crewai_class_tool":
            self.tool_vars[target] = self.tool_vars[class_name]
            return
        if not self._is_prebuilt_tool_call(call):
            return
        name = last_name(call.func) or target
        tool = self._prebuilt_tool(name, call.lineno)
        self._add_tool(target, tool, "prebuilt_tools")
        self.warnings.append(
            f"CrewAI prebuilt tool {name!r} at {self.source_ref}:{call.lineno} "
            "was recorded as low-confidence metadata; provide an explicit inventory "
            "for full review."
        )

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

    def _record_agent_assignment(self, node: ast.Assign | ast.AnnAssign) -> None:
        target = _assignment_target(node)
        call = _assignment_call(node)
        if target is None or call is None or last_name(call.func) != "Agent":
            return
        self.agent_vars.add(target)

    def _record_agent_or_crew(self, call: ast.Call) -> None:
        call_kind = last_name(call.func)
        if call_kind == "Agent":
            tools_expr = keyword(call, "tools")
            names = self._resolve_tool_names(tools_expr) if tools_expr is not None else []
            record = {"source_ref": self.source_ref, "line": call.lineno, "tools": names or []}
            self.artifacts.agents.append(record)
            if tools_expr is not None and names is None:
                self._dynamic("agent", call.lineno, _dynamic_reason(tools_expr))
        elif call_kind == "Crew":
            agents_expr = keyword(call, "agents")
            agents = _resolve_names(agents_expr) if agents_expr is not None else []
            self.artifacts.crews.append(
                {"source_ref": self.source_ref, "line": call.lineno, "agents": agents or []}
            )

    def _resolve_tool_names(self, node: ast.AST | None) -> list[str] | None:
        if node is None:
            return []
        if isinstance(node, ast.Name):
            if node.id in self.list_vars:
                return self.list_vars[node.id]
            if node.id in self.tool_vars:
                return [self.tool_vars[node.id].name]
            return None
        if isinstance(node, ast.List | ast.Tuple):
            names: list[str] = []
            for element in node.elts:
                if isinstance(element, ast.Name) and element.id in self.tool_vars:
                    names.append(self.tool_vars[element.id].name)
                    continue
                if isinstance(element, ast.Call) and self._is_prebuilt_tool_call(element):
                    name = last_name(element.func) or "CrewAI prebuilt tool"
                    inline_name = f"{name}@{element.lineno}"
                    tool = self._prebuilt_tool(name, element.lineno)
                    self.tool_vars[inline_name] = tool
                    self.artifacts.prebuilt_tools.append(
                        {"name": tool.name, "source_ref": self.source_ref, "line": element.lineno}
                    )
                    self.warnings.append(
                        f"CrewAI prebuilt tool {name!r} at {self.source_ref}:{element.lineno} "
                        "was recorded as low-confidence metadata; provide an explicit "
                        "inventory for full review."
                    )
                    names.append(name)
                    continue
                return None
            return names
        return None

    def _is_prebuilt_tool_call(self, call: ast.Call) -> bool:
        name = dotted_name(call.func) or ""
        return (
            name in self.prebuilt_names
            or name.startswith("crewai_tools.")
            or last_name(call.func) in self.prebuilt_names
        )

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

    def _prebuilt_tool(self, name: str, line: int) -> Tool:
        return Tool(
            id=stable_tool_id(name),
            name=name,
            description=(
                f"CrewAI prebuilt tool {name}. Static reference only; provide an "
                "explicit inventory for full metadata."
            ),
            source_type="crewai_prebuilt_tool",
            source_id=self.source_id,
            source_ref=self.source_ref,
            source_location=f"{self.source_ref}:{line}",
            annotations={"framework": "crewai", "prebuilt_tool": True},
            auth=AuthInfo(source="crewai_static"),
            extraction_confidence="low",
            extraction={"method": "crewai_prebuilt_tool_reference", "confidence": "low"},
        )

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
            f"CrewAI {kind} at {self.source_ref}:{line} has dynamic tool surface: {reason}."
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
        annotations={"framework": "crewai"},
        auth=AuthInfo(source="crewai_static"),
        extraction_confidence="medium",
        extraction={"method": extraction_method, "confidence": "medium"},
    )


def _decorator_tool_name(decorator: ast.Call | ast.Name) -> str | None:
    if isinstance(decorator, ast.Call):
        return first_string_arg(decorator) or keyword_string(decorator, "name")
    return None


def _class_string_attr(node: ast.ClassDef, attr_name: str) -> str | None:
    for statement in node.body:
        target = _simple_assignment_name(statement)
        if target != attr_name:
            continue
        if value := literal_string(_assignment_value(statement)):
            return value
    return None


def _class_name_attr(node: ast.ClassDef, attr_name: str) -> str | None:
    for statement in node.body:
        target = _simple_assignment_name(statement)
        if target == attr_name:
            return dotted_name(_assignment_value(statement))
    return None


def _class_method(node: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for statement in node.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef) and statement.name == name:
            return statement
    return None


def _simple_assignment_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _assignment_call(node: ast.Assign | ast.AnnAssign) -> ast.Call | None:
    value = _assignment_value(node)
    return value if isinstance(value, ast.Call) else None


def _assignment_value(node: ast.AST | None) -> ast.AST | None:
    if isinstance(node, ast.Assign | ast.AnnAssign):
        return node.value
    return node


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


def _resolve_names(node: ast.AST | None) -> list[str] | None:
    if isinstance(node, ast.List | ast.Tuple):
        names: list[str] = []
        for element in node.elts:
            if not isinstance(element, ast.Name):
                return None
            names.append(element.id)
        return names
    if isinstance(node, ast.Name):
        return [node.id]
    return None


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
