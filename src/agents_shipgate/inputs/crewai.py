from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.artifact_models import CrewAiArtifacts
from agents_shipgate.core.domain import (
    AuthInfo,
    LoadedToolSource,
    Tool,
)
from agents_shipgate.inputs._python_framework import (
    assignment_call,
    assignment_target,
    assignment_value,
    dynamic_reason,
    framework_function_tool,
    framework_tool_binding_problem,
    framework_tool_members,
    load_python_framework_sources,
    local_tool_lists,
    ordered_nodes,
    source_line,
    unique_tools,
)
from agents_shipgate.inputs.common import stable_tool_id, tool_name_warning
from agents_shipgate.inputs.config_trace import trace_config_binding
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.list_expressions import bindings_at
from agents_shipgate.inputs.openai_sdk_static import _capability_changes
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.inputs.python_static import (
    dotted_name,
    field_default_string,
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
    pydantic_model_schemas,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
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
    loaded_sources = load_python_framework_sources(
        source_refs=source_refs,
        config=config,
        base_dir=base_dir,
        framework_type="crewai",
        framework_label="CrewAI",
        inventory_source_type="crewai_inventory",
        inventory_annotation="crewai_inventory",
        artifacts=artifacts,
        extractor_factory=_CrewAiExtractor,
    )
    return loaded_sources, artifacts


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
        self.tool_declarations: dict[str, ast.stmt] = {}
        self.discovered_tools: list[Tool] = []
        self.tool_lists = local_tool_lists(tree, source_ref, _reads_tools)
        self.inline_tools: dict[ast.Call, str] = {}
        self.call_targets = {
            id(call): assignment_target(node)
            for node in ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign))
            if (call := assignment_call(node)) is not None
        }
        self.warnings: list[str] = []

    def extract(self) -> tuple[list[Tool], list[str]]:
        for node in ordered_nodes(self.tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._record_decorated_tool(node)
        for node in ordered_nodes(self.tree, (ast.ClassDef,)):
            self._record_class_tool(node)
        for node in ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            self._record_prebuilt_tool(node)
        for node in ordered_nodes(self.tree, (ast.Assign, ast.AnnAssign)):
            value = assignment_value(node)
            if assignment_target(node) is not None and isinstance(value, ast.List | ast.Tuple):
                for element in value.elts:
                    if isinstance(element, ast.Call) and self._is_prebuilt_tool_call(element):
                        self._record_inline_tool(element)
        for call in ordered_nodes(self.tree, (ast.Call,)):
            self._record_agent_or_crew(call)
        self._record_agent_tool_changes()
        warnings = sorted(dict.fromkeys(self.warnings))
        self.artifacts.warnings.extend(warnings)
        return unique_tools(self.discovered_tools), warnings

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
        self._add_tool(node.name, tool, "function_tools", node)

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
        self._add_tool(node.name, tool, "class_tools", node)

    def _record_prebuilt_tool(self, node: ast.Assign | ast.AnnAssign) -> None:
        target = assignment_target(node)
        call = assignment_call(node)
        if target is None or call is None:
            return
        class_name = last_name(call.func)
        if class_name in self.tool_vars and self.tool_vars[class_name].source_type == "crewai_class_tool":
            if isinstance(call.func, ast.Name) and not framework_tool_binding_problem(
                self.tool_lists, call.func, self.tool_declarations[class_name],
            ):
                self.tool_vars[target] = self.tool_vars[class_name]
                self.tool_declarations[target] = node
            return
        if not self._is_prebuilt_tool_call(call):
            return
        name = last_name(call.func) or target
        tool = self._prebuilt_tool(name, call.lineno)
        self._add_tool(target, tool, "prebuilt_tools", node)
        self.warnings.append(
            f"CrewAI prebuilt tool {name!r} at {self.source_ref}:{call.lineno} "
            "was recorded as low-confidence metadata; provide an explicit inventory "
            "for full review."
        )

    def _record_agent_or_crew(self, call: ast.Call) -> None:
        call_kind = last_name(call.func)
        if call_kind == "Agent":
            tools_expr = keyword(call, "tools")
            names, reason = self._resolve_tool_names(tools_expr)
            record = {
                "name": self.call_targets.get(id(call)) or "root",
                "source_id": self.source_id,
                "source_ref": self.source_ref,
                "line": call.lineno,
                "tools": names,
            }
            self.artifacts.agents.append(record)
            if reason is not None:
                self._dynamic("agent", call.lineno, reason, expr=tools_expr)
        elif call_kind == "Crew":
            agents_expr = keyword(call, "agents")
            agents = _resolve_names(agents_expr) if agents_expr is not None else []
            self.artifacts.crews.append(
                {
                    "name": self.call_targets.get(id(call)) or "crew",
                    "source_id": self.source_id,
                    "source_ref": self.source_ref,
                    "line": call.lineno,
                    "agents": agents or [],
                }
            )

    def _record_agent_tool_changes(self) -> None:
        view = self.tool_lists.entry
        lookup = bindings_at(view.scopes, view.bindings)
        for receiver, site in _capability_changes(self.tree, frozenset({"tools"}), scopes=view.scopes):
            candidates = [receiver]
            if isinstance(receiver, ast.Name):
                candidates = [assignment_value(statement) for _, statement in lookup(receiver.id, receiver)]
            if any(isinstance(value, ast.Call) and last_name(value.func) == "Agent" for value in candidates):
                self._dynamic("agent", site.lineno, "the agent's tools may change after construction")

    def _resolve_tool_names(self, expr: ast.AST | None) -> tuple[list[str], str | None]:
        if expr is None:
            return [], None
        if isinstance(expr, (ast.List, ast.Tuple)):
            for element in expr.elts:
                if isinstance(element, ast.Call) and self._is_prebuilt_tool_call(element):
                    self._record_inline_tool(element)
        members, reason = framework_tool_members(self.tool_lists, expr)
        if reason is not None:
            return [], reason
        names = []
        for element in members:
            if isinstance(element, ast.Name) and element.id in self.tool_vars:
                if problem := framework_tool_binding_problem(self.tool_lists, element, self.tool_declarations[element.id]):
                    return [], problem
                names.append(self.tool_vars[element.id].name)
            elif isinstance(element, ast.Call) and self._is_prebuilt_tool_call(element):
                names.append(self._record_inline_tool(element))
            else:
                return [], dynamic_reason(expr)
        return names, None

    def _record_inline_tool(self, element: ast.Call) -> str:
        if element in self.inline_tools:
            return self.inline_tools[element]
        name = last_name(element.func) or "CrewAI prebuilt tool"
        tool = self._prebuilt_tool(name, element.lineno)
        self.inline_tools[element] = name
        self.discovered_tools.append(tool)
        self.artifacts.prebuilt_tools.append(
            {"name": tool.name, "source_ref": self.source_ref, "line": element.lineno}
        )
        self.warnings.append(
            f"CrewAI prebuilt tool {name!r} at {self.source_ref}:{element.lineno} "
            "was recorded as low-confidence metadata; provide an explicit "
            "inventory for full review."
        )
        return name

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

    def _add_tool(self, variable_name: str, tool: Tool, artifact_field: str, declaration: ast.stmt) -> None:
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
        self.tool_declarations[variable_name] = declaration
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
    return framework_function_tool(
        node,
        framework="crewai",
        auth_source="crewai_static",
        name=name,
        description=description,
        input_schema=input_schema,
        parameters=parameters,
        source_id=source_id,
        source_ref=source_ref,
        source_type=source_type,
        extraction_method=extraction_method,
    )


def _reads_tools(call: ast.Call, keyword_name: str | None, qualified: str | None) -> bool:
    return keyword_name == "tools" and (
        last_name(call.func) == "Agent" or qualified in {"crewai.Task", "crewai.task.Task"}
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
        value_node = assignment_value(statement)
        if value := literal_string(value_node):
            return value
        if value := field_default_string(value_node):
            return value
    return None


def _class_name_attr(node: ast.ClassDef, attr_name: str) -> str | None:
    for statement in node.body:
        target = _simple_assignment_name(statement)
        if target == attr_name:
            return dotted_name(assignment_value(statement))
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


class CrewAIAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_crewai_artifacts`.

    Framework-scoped. The dispatcher invokes ``load()`` once per scan
    when either a ``tool_sources`` entry of type ``crewai`` or the
    top-level ``manifest.crewai`` section is configured.
    """

    source_type: ClassVar[str] = "crewai"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = CrewAiArtifacts

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="crewai",
        label="CrewAI",
        reads=(
            "CrewAI Python modules parsed with `ast`, plus any reviewed inventory "
            "`crewai.tool_inventories[]` declares."
        ),
        manifest_section="crewai",
        cells=(
            BoundaryCell(
                shape="export_artifact",
                variant="reviewed inventory",
                status="extracted",
                reads=(
                    "A reviewed tool inventory in MCP export form, read as a "
                    "published contract."
                ),
                emits=("crewai_inventory",),
                ceiling="high",
            ),
            BoundaryCell(
                shape="export_artifact",
                variant="wildcard inventory",
                status="extracted",
                reads=(
                    "An inventory that declares `wildcard: true` instead of "
                    "listing tools. It is a reviewed file and still names "
                    "nothing, so it loads at `high` and proves no surface — a "
                    "reviewed statement that says nothing is not evidence."
                ),
                emits=("crewai_inventory",),
                ceiling="high",
                surface_flags=("wildcard_tools",),
            ),
            BoundaryCell(
                shape="literal_registration",
                status="extracted",
                reads=(
                    "An `@tool`-decorated function, or a `BaseTool` subclass whose "
                    "name, description, and `args_schema` are literals in the same "
                    "file."
                ),
                emits=("crewai_function", "crewai_class_tool"),
                ceiling="medium",
            ),
            BoundaryCell(
                shape="factory",
                status="extracted",
                reads=(
                    "A `crewai_tools` prebuilt constructor such as `FileReadTool()`. "
                    "The name is read; the schema lives in the installed package, "
                    "not in the repository, so the action is recorded as "
                    "low-confidence metadata."
                ),
                emits=("crewai_prebuilt_tool",),
                ceiling="low",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="not_extracted",
                reads=(
                    "A tool list the reader cannot resolve, a rebound member, "
                    "or a list handed to code that may change it records a dynamic "
                    "tool surface. Replacing or mutating an agent's tools also "
                    "leaves its bindings incomplete."
                ),
                raises=("SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",),
            ),
        ),
    )

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        loaded_sources, artifacts = load_crewai_artifacts(manifest, base_dir)
        return LoadedAdapterResult(tool_sources=loaded_sources, artifact=artifacts)
