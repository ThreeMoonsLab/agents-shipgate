from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from agents_shipgate.core.domain import (
    AuthInfo,
    LoadedToolSource,
    Tool,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.paths import display_path
from agents_shipgate.inputs.common import list_input_directory, resolve_input_path, stable_tool_id
from agents_shipgate.inputs.list_expressions import ListExpressions, bindings_at, evaluation_site
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.python_imports import (
    ScopeIndex,
    _module_bindings,
    reference_spelling,
)
from agents_shipgate.inputs.python_static import (
    function_output_schema,
    function_signature,
    parse_python_file,
)
from agents_shipgate.schemas.manifest import (
    ArtifactPathConfig,
    ToolInventoryConfig,
    ToolSourceConfig,
)

ExtractorFactory = Callable[[ast.Module, str, str, Any], Any]


def load_python_framework_sources(
    *,
    source_refs: Iterable[ToolSourceConfig],
    config: Any,
    base_dir: Path,
    framework_type: str,
    framework_label: str,
    inventory_source_type: str,
    inventory_annotation: str,
    artifacts: Any,
    extractor_factory: ExtractorFactory,
) -> list[LoadedToolSource]:
    loaded_sources: list[LoadedToolSource] = []
    for source in source_refs:
        try:
            loaded_sources.extend(
                _load_framework_source(
                    source,
                    base_dir,
                    framework_label=framework_label,
                    framework_type=framework_type,
                    artifacts=artifacts,
                    extractor_factory=extractor_factory,
                )
            )
        except InputParseError:
            if not source.optional:
                raise
            warning = f"Optional {framework_label} source {source.id!r} failed to load."
            artifacts.warnings.append(warning)
            loaded_sources.append(
                LoadedToolSource(
                    source_id=source.id,
                    source_type=framework_type,
                    warnings=[warning],
                )
            )

    if config:
        for entrypoint in config.python_entrypoints:
            loaded_sources.extend(
                _load_python_ref(
                    entrypoint,
                    base_dir,
                    source_id=f"{framework_type}:{entrypoint.path}",
                    framework_label=framework_label,
                    framework_type=framework_type,
                    artifacts=artifacts,
                    extractor_factory=extractor_factory,
                )
            )
        for inventory in config.tool_inventories:
            loaded = _load_inventory_ref(
                inventory,
                base_dir,
                source_id=f"{inventory_source_type}:{inventory.path}",
                framework_label=framework_label,
                inventory_source_type=inventory_source_type,
                inventory_annotation=inventory_annotation,
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
    return loaded_sources


def _load_framework_source(
    source: ToolSourceConfig,
    base_dir: Path,
    *,
    framework_label: str,
    framework_type: str,
    artifacts: Any,
    extractor_factory: ExtractorFactory,
) -> list[LoadedToolSource]:
    assert source.path is not None
    ref = ArtifactPathConfig(path=source.path, optional=source.optional)
    path = resolve_existing_path(ref, base_dir)
    if path.is_dir():
        python_files = [child for child in list_input_directory(path) if child.match("*.py")]
        if not python_files:
            raise InputParseError(f"{framework_label} source directory has no Python files: {path}")
        loaded: list[LoadedToolSource] = []
        for python_file in python_files:
            loaded.extend(
                load_python_path(
                    python_file,
                    base_dir,
                    source_id=source.id,
                    framework_label=framework_label,
                    framework_type=framework_type,
                    artifacts=artifacts,
                    extractor_factory=extractor_factory,
                )
            )
        return loaded
    if path.suffix.lower() != ".py":
        raise InputParseError(f"{framework_label} source must be a Python file or directory: {path}")
    return load_python_path(
        path,
        base_dir,
        source_id=source.id,
        framework_label=framework_label,
        framework_type=framework_type,
        artifacts=artifacts,
        extractor_factory=extractor_factory,
    )


def _load_python_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    *,
    source_id: str,
    framework_label: str,
    framework_type: str,
    artifacts: Any,
    extractor_factory: ExtractorFactory,
) -> list[LoadedToolSource]:
    try:
        path = resolve_existing_path(ref, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(
            f"Optional {framework_label} Python entrypoint {ref.path!r} failed to load."
        )
        return []
    return load_python_path(
        path,
        base_dir,
        source_id=source_id,
        framework_label=framework_label,
        framework_type=framework_type,
        artifacts=artifacts,
        extractor_factory=extractor_factory,
    )


def load_python_path(
    path: Path,
    base_dir: Path,
    *,
    source_id: str,
    framework_label: str,
    framework_type: str,
    artifacts: Any,
    extractor_factory: ExtractorFactory,
) -> list[LoadedToolSource]:
    if path.is_dir():
        loaded: list[LoadedToolSource] = []
        for python_file in [child for child in list_input_directory(path) if child.match("*.py")]:
            loaded.extend(
                load_python_path(
                    python_file,
                    base_dir,
                    source_id=source_id,
                    framework_label=framework_label,
                    framework_type=framework_type,
                    artifacts=artifacts,
                    extractor_factory=extractor_factory,
                )
            )
        return loaded
    tree = parse_python_file(path, label=framework_label)
    display = display_path(path, base_dir)
    artifacts.python_entrypoints.append(display)
    extractor = extractor_factory(tree, source_id, display, artifacts)
    tools, warnings = extractor.extract()
    return [
        LoadedToolSource(
            source_id=source_id,
            source_type=framework_type,
            tools=tools,
            warnings=warnings,
        )
    ]


def _load_inventory_ref(
    ref: ToolInventoryConfig,
    base_dir: Path,
    *,
    source_id: str,
    framework_label: str,
    inventory_source_type: str,
    inventory_annotation: str,
    artifacts: Any,
) -> LoadedToolSource | None:
    source = ToolSourceConfig(id=source_id, type="mcp", path=ref.path, optional=ref.optional)
    try:
        loaded = load_mcp_tools(source, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(
            f"Optional {framework_label} tool inventory {ref.path!r} failed to load."
        )
        return None
    artifacts.tool_inventory_files.append(display_path(resolve_input_path(base_dir, ref.path), base_dir))
    tools: list[Tool] = []
    for original in loaded.tools:
        tool = original.model_copy(deep=True)
        tool.source_type = inventory_source_type
        tool.annotations[inventory_annotation] = True
        tool.extraction_confidence = "high"
        tool.extraction["confidence"] = "high"
        tools.append(tool)
    return LoadedToolSource(
        source_id=source_id,
        source_type=inventory_source_type,
        tools=tools,
        warnings=loaded.warnings,
        completes_source_id=ref.source_id,
        is_tool_inventory=True,
    )


def framework_function_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    framework: str,
    auth_source: str,
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
        annotations={"framework": framework},
        auth=AuthInfo(source=auth_source),
        extraction_confidence="medium",
        extraction={"method": extraction_method, "confidence": "medium"},
    )


def assignment_call(node: ast.Assign | ast.AnnAssign) -> ast.Call | None:
    value = assignment_value(node)
    return value if isinstance(value, ast.Call) else None


def assignment_value(node: ast.AST | None) -> ast.AST | None:
    if isinstance(node, ast.Assign | ast.AnnAssign):
        return node.value
    return node


def assignment_target(node: ast.Assign | ast.AnnAssign) -> str | None:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def ordered_nodes(tree: ast.AST, node_types: tuple[type[Any], ...]) -> list[Any]:
    return sorted(
        (node for node in ast.walk(tree) if isinstance(node, node_types)),
        key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)),
    )


def unique_tools(tools: Iterable[Tool]) -> list[Tool]:
    unique: list[Tool] = []
    seen: set[tuple[str, str | None]] = set()
    for tool in tools:
        key = (tool.name, tool.source_location)
        if key in seen:
            continue
        unique.append(tool)
        seen.add(key)
    return unique


def local_tool_lists(
    tree: ast.Module,
    source_ref: str,
    reads: Callable[[ast.Call, str | None, str | None], bool],
) -> ListExpressions:
    scopes = ScopeIndex(tree)
    bindings, has_wildcard = _module_bindings(tree)
    lookup = bindings_at(scopes, bindings)

    def reads_tools(call: ast.Call, keyword: str | None) -> bool:
        spelling = reference_spelling(call.func)
        qualified = None
        if spelling and not has_wildcard:
            head, _, attribute = spelling.partition(".")
            found = lookup(head, call)
            if len(found) == 1:
                node, statement = found[0]
                if isinstance(node, ast.alias) and statement in tree.body and statement.lineno < call.lineno:
                    if isinstance(statement, ast.ImportFrom) and statement.level == 0 and statement.module:
                        qualified = f"{statement.module}.{node.name}"
                    elif isinstance(statement, ast.Import):
                        qualified = node.name if node.asname else head
                    if qualified and attribute:
                        qualified = f"{qualified}.{attribute}"
        return reads(call, keyword, qualified)

    return ListExpressions(
        ref=source_ref,
        tree=tree,
        scopes=scopes,
        bindings=bindings,
        module=None,
        resolver=None,
        agent_reads=reads_tools,
    )


def framework_tool_binding_problem(lists: ListExpressions, name: ast.Name, declaration: ast.stmt) -> str | None:
    view = lists.entry
    site = evaluation_site(view.scopes, name)
    if view.scopes.enclosing_bindings(site, name.id):
        return f"tool reference {name.id!r} is shadowed in its local scope"
    bindings = view.bindings.get(name.id, ())
    if (
        not bindings
        or bindings[-1].statement is not declaration
        or not all(binding.top_level for binding in bindings)
        or (len(bindings) > 1 and name.lineno <= declaration.lineno)
    ):
        return f"tool reference {name.id!r} does not resolve to its recorded module binding"
    if any(line > declaration.lineno for line in view.star_lines):
        return f"tool reference {name.id!r} may be rebound by a wildcard import"
    return None


def framework_tool_members(
    lists: ListExpressions, expr: ast.AST
) -> tuple[tuple[ast.expr, ...], str | None]:
    # Preserve the adapters' existing coverage boundary for runtime calls
    # and top-level expressions other than a literal list or a list name.
    if not isinstance(expr, ast.Name | ast.List | ast.Tuple):
        return (), dynamic_reason(expr)
    resolution = lists.resolve(expr)
    if not resolution.complete:
        return (), ". ".join(dict.fromkeys(part.reason for part in resolution.unresolved))
    if any(part.conditions for part in resolution.members):
        return (), "tool list membership depends on a runtime condition"
    return tuple(part.expr for part in resolution.members), None


def dynamic_reason(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return f"unresolved tool reference {node.id!r}"
    if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
        return "tool list is built by a comprehension"
    if isinstance(node, ast.Call):
        return "tool list comes from a runtime call"
    if isinstance(node, ast.List | ast.Tuple):
        return "tool list contains unresolved or inline tool expressions"
    return f"unsupported static tool expression {type(node).__name__}"


def source_line(location: str | None) -> int | None:
    if not location or ":" not in location:
        return None
    try:
        return int(location.rsplit(":", 1)[1])
    except ValueError:
        return None


def resolve_existing_path(ref: ArtifactPathConfig, base_dir: Path) -> Path:
    path = resolve_input_path(base_dir, ref.path)
    if not path.exists():
        raise InputParseError(f"Input file not found: {path}")
    return path
