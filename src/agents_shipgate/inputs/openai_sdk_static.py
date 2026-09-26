from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.domain import (
    AgentBindingObservation,
    AuthInfo,
    LoadedToolSource,
    Tool,
    ToolkitScopeBound,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import (
    list_input_directory,
    load_text_file,
    resolve_input_path,
    stable_tool_id,
)
from agents_shipgate.inputs.config_trace import trace_config_binding
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.inputs.python_imports import (
    NOT_BOUND,
    ImportResolver,
    PythonModule,
    Resolution,
    ScopeIndex,
    _module_bindings,
    local_binding_detail,
    reference_spelling,
)
from agents_shipgate.inputs.python_static import (
    display_path,
    dotted_name,
    function_input_schema,
    function_output_schema,
    function_signature,
    parse_python_file,
)
from agents_shipgate.inputs.sdk_guard_dependencies import (
    guard_module_metadata,
    read_guard_dependency,
)
from agents_shipgate.schemas.coverage_recovery import CoverageRecovery, SourceRecoveryEvidence
from agents_shipgate.schemas.guard_dependencies import GuardDependencyEvidence
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)

SDK_MODULES = frozenset({"agents", "openai_agents"})
DEFAULT_FUNCTION_TOOL_DECORATORS = frozenset(
    {"function_tool", "agents.function_tool", "openai_agents.function_tool"}
)
DEFAULT_AGENT_CONSTRUCTORS = frozenset({"Agent", "agents.Agent", "openai_agents.Agent"})


def load_openai_sdk_static_tools(
    source: ToolSourceConfig, manifest: AgentsShipgateManifest | None, base_dir: Path
) -> LoadedToolSource:
    entrypoint = source.path or (manifest.agent.sdk.entrypoint if manifest is not None and manifest.agent.sdk else None)
    if not entrypoint:
        return LoadedToolSource(
            source_id=source.id,
            source_type="openai_agents_sdk",
            warnings=["OpenAI Agents SDK source has no entrypoint"],
        )
    path = resolve_input_path(base_dir, entrypoint)
    if not path.exists():
        warning = f"OpenAI Agents SDK entrypoint not found: {path}"
        ref = display_path(path, base_dir)
        return LoadedToolSource(
            source_id=source.id,
            source_type="openai_agents_sdk",
            warnings=[warning],
            recovery_evidence=[SourceRecoveryEvidence(
                warning=warning, source_id=source.id, source_type="openai_agents_sdk",
                source_ref=ref, path=ref,
                recovery=CoverageRecovery(kind="input_unavailable", reason="sdk_entrypoint_not_found"),
            )],
        )
    if path.is_dir():
        python_files = [child for child in list_input_directory(path) if child.match("*.py")]
        if not python_files:
            raise InputParseError(f"OpenAI Agents SDK source directory has no Python files: {path}")
        tools: list[Tool] = []
        toolkit_bounds: list[ToolkitScopeBound] = []
        guard_dependencies: list[GuardDependencyEvidence] = []
        for python_file in python_files:
            file_tools, file_bounds, file_guards = _load_python_file(python_file, source, base_dir)
            tools.extend(file_tools)
            toolkit_bounds.extend(file_bounds)
            guard_dependencies.extend(file_guards)
    elif path.suffix.lower() == ".py":
        tools, toolkit_bounds, guard_dependencies = _load_python_file(path, source, base_dir)
        python_files = [path]
    else:
        raise InputParseError(
            f"OpenAI Agents SDK source must be a Python file or directory: {path}"
        )
    (
        binding_warnings,
        binding_observations,
        recovery_evidence,
        imported_tools,
        imported_guards,
    ) = _extract_agent_bindings(tools, python_files, source, base_dir)
    tools = [*tools, *imported_tools]
    guard_dependencies = [*guard_dependencies, *imported_guards]
    return LoadedToolSource(
        source_id=source.id,
        source_type="openai_agents_sdk",
        tools=tools,
        toolkit_bounds=toolkit_bounds,
        binding_observations=binding_observations,
        warnings=[*_toolkit_binding_warnings(toolkit_bounds), *binding_warnings],
        recovery_evidence=recovery_evidence,
        guard_dependencies=guard_dependencies,
    )


def _toolkit_binding_warnings(bounds: list[ToolkitScopeBound]) -> list[str]:
    """Source warnings for toolkit factories whose binding is unreadable.

    An ``unknown`` binding means the constructor's authority-bearing
    argument exists but is not statically traceable — previously that case
    was silently skipped (a fail-open: the factory disappeared from every
    surface). Mirror the ADK dynamic-toolset fix: record a warning so the
    scan routes to review instead of silence. ``config``-bound factories
    stay warning-free — they carry a comparable marker the verify-tier
    config-binding checks own.
    """
    return [
        (
            f"{bound.provider} toolkit constructor {bound.constructor} at "
            f"{bound.source_ref}:{bound.source_line} passes a configuration "
            "that is not statically readable; its effective tool surface "
            "cannot be enumerated or compared. Use a literal configuration "
            "allowlist, bind it from a config file, or declare an explicit "
            "local tool inventory."
        )
        for bound in bounds
        if bound.config_binding == "unknown"
    ]


def _load_python_file(
    path: Path,
    source: ToolSourceConfig,
    base_dir: Path,
) -> tuple[list[Tool], list[ToolkitScopeBound], list[GuardDependencyEvidence]]:
    try:
        source_text = load_text_file(path)
        tree = ast.parse(source_text, filename=str(path))
    except SyntaxError as exc:
        raise InputParseError(f"Unable to parse OpenAI Agents SDK entrypoint {path}: {exc.msg}") from exc
    except InputParseError as exc:
        message = str(exc).replace(
            "OpenAI Agents SDK Python entrypoint",
            "OpenAI Agents SDK entrypoint",
        )
        raise InputParseError(message) from exc
    ref = display_path(path, base_dir)
    sdk_decorators = _function_tool_decorators(tree)
    definitions = [node for node in ast.walk(tree) if _is_function_tool(node, sdk_decorators)]
    tools = [_function_to_tool(node, source, ref, sdk_decorators) for node in definitions]
    source_sha256, source_within_limits = guard_module_metadata(tree, source_text)
    guards = [
        read_guard_dependency(
            tree=tree, source_sha256=source_sha256, source_within_limits=source_within_limits,
            path=path, root=base_dir, tool=tool, definition=node,
        )
        for tool, node in zip(tools, definitions, strict=True)
    ]
    return tools, _detect_toolkit_bounds(tree, ref), guards


def _extract_agent_bindings(
    tools: list[Tool],
    paths: list[Path],
    source: ToolSourceConfig,
    base_dir: Path,
) -> tuple[
    list[str],
    list[AgentBindingObservation],
    list[SourceRecoveryEvidence],
    list[Tool],
    list[GuardDependencyEvidence],
]:
    """Extract exact, local-only ``Agent(..., tools=[...])`` wiring.

    This intentionally resolves only literal lists, names bound to literal
    lists, local function tools, and names or ``module.function`` references
    that repository-local imports lead to a ``@function_tool`` definition
    (#864). Dynamic expressions are preserved as partial evidence instead of
    being guessed, and an import that does not reach a definition inside the
    read scope is named with its reason. The last two return values are the
    function tools those imports reached outside the files this source reads,
    and their guard evidence.
    """

    warnings: list[str] = []
    observations: list[AgentBindingObservation] = []
    recovery_evidence: list[SourceRecoveryEvidence] = []
    tool_by_name = {tool.name: tool for tool in tools}
    tool_by_name.update(
        {
            symbol: tool
            for tool in tools
            if isinstance((symbol := tool.annotations.get("python_symbol")), str)
        }
    )
    imports = _ImportedTools(tools, source, base_dir)
    for path in paths:
        text = load_text_file(path)
        tree = parse_python_file(path, label="OpenAI Agents SDK")
        source_ref = display_path(path, base_dir)
        sdk_names = _SdkNames(tree)
        scopes = ScopeIndex(tree)
        module = imports.resolver.entry(path, tree, text)
        import_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    import_aliases[alias.asname or alias.name] = alias.name
        tool_lists = _ToolLists(
            tree,
            scopes,
            module.bindings if module is not None else _module_bindings(tree)[0],
            sdk_names=sdk_names,
            resolve=(
                (lambda spelling, module=module: imports.resolver.resolve(module, spelling))
                if module is not None
                else None
            ),
        )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            target = _assignment_target(node)
            call = node.value if isinstance(node.value, ast.Call) else None
            if (
                not target
                or call is None
                or not sdk_names.denotes(
                    dotted_name(call.func), call, "Agent", DEFAULT_AGENT_CONSTRUCTORS
                )
            ):
                continue
            tools_expr = _keyword(call, "tools")
            references = tool_lists.references(tools_expr, call)
            pointer = f"{source_ref}:{call.lineno}"
            issues: list[str] = []
            tools_complete = True
            names: list[str] = []
            locators: dict[str, str] = {}
            if references is None:
                reason = (
                    f"OpenAI Agents SDK agent {target!r} at {pointer} uses a "
                    "dynamic tools expression; its binding graph is incomplete."
                )
                warnings.append(reason)
                issues.append(reason)
                literal_concat = _literal_tool_list_concatenation(tools_expr)
                recovery_evidence.append(SourceRecoveryEvidence(
                    warning=reason, source_id=source.id, source_type="openai_agents_sdk",
                    source_ref=pointer, path=source_ref,
                    recovery=CoverageRecovery(
                        kind="reader_limitation" if literal_concat else "unresolved",
                        reason=(
                            "sdk_literal_tool_list_concatenation_unsupported" if literal_concat
                            else "sdk_tools_expression_unresolved"
                        ),
                    ),
                ))
                tools_complete = False
            else:
                # Two different definitions under one tool name: the model
                # sees one name for both, so neither is bound (#879 review).
                duplicated: set[str] = set()
                first_location: dict[str, str] = {}
                for reference, element in references:
                    head = reference.split(".", 1)[0]
                    # Read where the reference is written: a module-level
                    # list's names are the module's, whatever the agent's
                    # enclosing function binds (#879 review).
                    found = scopes.enclosing_bindings(element, head)
                    local = found[0] if found else None
                    statement = scopes.statement_of(local) if local is not None else None
                    if len(found) > 1:
                        tool, detail = None, local_binding_detail(
                            source_ref, head, local, rebound=True
                        )
                    elif (
                        isinstance(local, ast.alias)
                        and module is not None
                        and isinstance(statement, ast.Import | ast.ImportFrom)
                    ):
                        # The builder's own import is the binding its agent
                        # receives: followed like a module-level one.
                        tool, detail = imports.tool_from_resolution(
                            imports.resolver.resolve_local_import(
                                module, statement, local, reference
                            )
                        )
                    elif isinstance(local, ast.FunctionDef | ast.AsyncFunctionDef):
                        # A nested ``@function_tool`` in the building function.
                        tool = imports.by_location.get(f"{source_ref}:{local.lineno}")
                        detail = (
                            None
                            if tool is not None
                            else f"it is the nested function {local.name!r} at "
                            f"{source_ref}:{local.lineno}, which is not decorated with "
                            "the SDK's @function_tool"
                        )
                    elif local is not None:
                        # The function binds the name itself — a local import,
                        # a parameter — so the module's binding is not the one
                        # this agent receives.
                        tool, detail = None, local_binding_detail(source_ref, head, local)
                    else:
                        tool, detail = imports.tool_for(
                            reference, module, source_ref, tool_by_name, import_aliases
                        )
                    if tool is None:
                        reason = (
                            f"OpenAI Agents SDK agent {target!r} at {pointer} binds "
                            f"unresolved tool {reference!r}"
                            + (f": {detail}." if detail else ".")
                        )
                        warnings.append(reason)
                        issues.append(reason)
                        tools_complete = False
                        names.append(import_aliases.get(reference, reference))
                        continue
                    locator = f"{tool.source_ref}#{tool.name}" if tool.source_ref else None
                    if tool.name in duplicated:
                        continue
                    bound = locators.get(tool.name)
                    if bound is not None and locator is not None and bound != locator:
                        reason = (
                            f"OpenAI Agents SDK agent {target!r} at {pointer} binds two "
                            f"different functions named {tool.name!r} "
                            f"({first_location.get(tool.name, bound.split('#', 1)[0])} and "
                            f"{tool.source_location}); the model sees one tool name for "
                            "both, so neither is resolved."
                        )
                        warnings.append(reason)
                        issues.append(reason)
                        tools_complete = False
                        duplicated.add(tool.name)
                        names = [name for name in names if name != tool.name]
                        locators.pop(tool.name, None)
                        continue
                    names.append(tool.name)
                    if locator is not None:
                        locators[tool.name] = locator
                        first_location.setdefault(tool.name, tool.source_location or locator)
            handoff_names = tool_lists.names(_keyword(call, "handoffs"), call, import_aliases)
            handoffs_complete = True
            if handoff_names is None:
                reason = f"OpenAI Agents SDK agent {target!r} has dynamic handoffs at {pointer}."
                warnings.append(reason)
                issues.append(reason)
                handoffs_complete = False
                handoff_names = []
            observations.append(
                AgentBindingObservation(
                    agent=target,
                    source_id=source.id,
                    source=source_ref,
                    source_pointer=pointer,
                    tool_names=names,
                    tool_locators=locators,
                    handoff_names=handoff_names,
                    tools_complete=tools_complete,
                    handoffs_complete=handoffs_complete,
                    issues=issues,
                )
            )
    return (
        list(dict.fromkeys(warnings)),
        observations,
        recovery_evidence,
        imports.new_tools,
        imports.new_guards,
    )


class _ImportedTools:
    """Function tools a source's ``tools=[...]`` lists reach through imports.

    One per source load, so a definition two agent modules import is one tool,
    and a definition this source already read from its own files is that tool
    rather than a second observation of it.
    """

    def __init__(self, tools: list[Tool], source: ToolSourceConfig, base_dir: Path) -> None:
        self.source = source
        self.base_dir = base_dir
        self.resolver = ImportResolver(base_dir)
        self.by_location = {tool.source_location: tool for tool in tools}
        #: ``(source_ref, python_symbol) -> tool``, first seen wins: a lookup
        #: per reference, not a scan of every tool (#879 review).
        self.by_symbol: dict[tuple[str | None, object], Tool] = {}
        for tool in tools:
            self.by_symbol.setdefault((tool.source_ref, tool.annotations.get("python_symbol")), tool)
        self.new_tools: list[Tool] = []
        self.new_guards: list[GuardDependencyEvidence] = []

    def tool_for(
        self,
        reference: str,
        module: PythonModule | None,
        source_ref: str,
        tool_by_name: dict[str, Tool],
        import_aliases: dict[str, str],
    ) -> tuple[Tool | None, str | None]:
        """The tool ``reference`` binds, or None and why not."""

        local = self.by_symbol.get((source_ref, reference))
        if module is None:
            if local is not None:
                return local, None
            # A module outside the read scope: the previous name reading holds.
            return tool_by_name.get(import_aliases.get(reference, reference)), None
        bindings = module.bindings.get(reference, [])
        if (
            local is not None
            and len(bindings) == 1
            and bindings[0].top_level
            and isinstance(bindings[0].node, ast.FunctionDef | ast.AsyncFunctionDef)
            and f"{source_ref}:{bindings[0].node.lineno}" == local.source_location
        ):
            return local, None
        # Anything else the module binds under this name — an import, an
        # assignment, a conditional ``def`` — is what a module-level agent
        # receives, not a same-named function nested elsewhere (#879 review).
        resolution = self.resolver.resolve(module, reference)
        if resolution.reason == NOT_BOUND:
            return None, f"{reference!r} is not bound at module level in {module.ref}"
        return self.tool_from_resolution(resolution)

    def tool_from_resolution(self, resolution: Resolution) -> tuple[Tool | None, str | None]:
        """The tool one import resolution reached, or None and why not."""

        if not resolution.resolved:
            return None, resolution.detail
        node, defining = resolution.definition, resolution.module
        assert node is not None and defining is not None
        location = f"{defining.ref}:{node.lineno}"
        tool = self.by_location.get(location)
        if tool is None:
            sdk_decorators = _function_tool_decorators(defining.tree)
            if not _is_function_tool(node, sdk_decorators):
                return None, (
                    f"it resolves to {node.name!r} at {location}, which is not "
                    "decorated with the SDK's @function_tool"
                )
            tool = _function_to_tool(node, self.source, defining.ref, sdk_decorators)
            # Minted from an import: another source that reads that module
            # observes the same definition, and the catalog keeps one (#879).
            tool.extraction["imported_definition"] = True
            self.by_location[location] = tool
            self.by_symbol.setdefault((tool.source_ref, tool.annotations.get("python_symbol")), tool)
            self.new_tools.append(tool)
            source_sha256, within_limits = guard_module_metadata(
                defining.tree, defining.text
            )
            self.new_guards.append(
                read_guard_dependency(
                    tree=defining.tree,
                    source_sha256=source_sha256,
                    source_within_limits=within_limits,
                    path=defining.path,
                    root=self.base_dir,
                    tool=tool,
                    definition=node,
                )
            )
        evidence = resolution.evidence()
        recorded = tool.extraction.setdefault("import_resolutions", [])
        if evidence not in recorded:
            recorded.append(evidence)
        return tool, None


def _literal_tool_list_concatenation(value: ast.AST | None) -> bool:
    """One proven reader limitation, never a claim about deployed wiring.

    Python defines addition of two literal lists, but this reader's name-list
    resolver has no BinOp branch. Calls, unpacking and other expressions do
    not prove a product-owned repair and deliberately remain unresolved.
    """
    return (
        isinstance(value, ast.BinOp)
        and isinstance(value.op, ast.Add)
        and isinstance(value.left, ast.List)
        and isinstance(value.right, ast.List)
        and all(isinstance(item, ast.Name) for item in [*value.left.elts, *value.right.elts])
    )


def _assignment_target(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


#: Methods that change a list in place.
_LIST_MUTATORS = frozenset({"append", "extend", "insert", "remove", "pop", "clear"})
#: Calls that read the values they are given and never change them.
_READ_ONLY_CALLS = frozenset(
    {
        "len", "print", "repr", "str", "bool", "id", "hash", "isinstance", "type",
        "list", "tuple", "set", "frozenset", "sorted", "reversed", "enumerate", "iter",
        "any", "all", "sum", "min", "max", "zip", "map", "filter",
        "copy.copy", "copy.deepcopy", "json.dumps", "pprint", "pprint.pprint",
        "pprint.pformat", "pformat",
    }
)
_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})


def _leaves_arguments_alone(call: ast.Call) -> bool:
    name = dotted_name(call.func)
    if name in _READ_ONLY_CALLS:
        return True
    return isinstance(call.func, ast.Attribute) and call.func.attr in _LOG_METHODS


def _parameter_left_alone(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """Whether ``function`` never changes, re-binds out, or hands on parameter ``name``."""

    def rooted(node: ast.AST) -> bool:
        while isinstance(node, ast.Attribute | ast.Subscript):
            node = node.value
        return isinstance(node, ast.Name) and node.id == name

    for node in ast.walk(function):
        if isinstance(node, ast.Global | ast.Nonlocal) and name in node.names:
            return False
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
            targets = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
            if any(isinstance(t, ast.Subscript | ast.Attribute) and rooted(t) for t in targets):
                return False
            value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
            if isinstance(value, ast.Name) and value.id == name:
                return False
        if isinstance(node, ast.NamedExpr) and isinstance(node.value, ast.Name) and node.value.id == name:
            return False
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in _LIST_MUTATORS
                and rooted(node.func.value)
            ):
                return False
            if not _leaves_arguments_alone(node) and any(
                isinstance(value, ast.Name) and value.id == name
                for value in [*node.args, *(keyword.value for keyword in node.keywords)]
            ):
                return False
    return True


class _ToolLists:
    """Literal lists that a ``tools=NAME`` / ``handoffs=NAME`` refers to (#879 review).

    The name is read where the agent is constructed, through the scope that
    binds it there — a builder's local ``tools = [...]`` is that builder's,
    never another's; a class body's list is the class body's. It is read only
    when that scope binds it once, to a literal list, and nothing in the file
    changes that binding in place — ``.append`` from a nested function, a
    ``global`` or ``nonlocal`` rebinding, a subscript store. Anything else is a
    dynamic expression, never the last assignment.

    Every change site is indexed once, against the binding it changes, so a
    lookup costs the depth of the scopes and not the size of the file.
    """

    def __init__(
        self,
        tree: ast.Module,
        scopes: ScopeIndex,
        module_bindings: dict[str, list[Any]],
        *,
        sdk_names: _SdkNames | None = None,
        resolve: Callable[[str], Resolution] | None = None,
    ) -> None:
        self.scopes = scopes
        self.module_bindings = module_bindings
        self.sdk_names = sdk_names
        self.resolve = resolve
        self.changed: set[object] = set()
        # Only a name bound to a literal list somewhere can be read as one, so
        # only those are followed into a callee — reading other modules for any
        # call would widen the run's inputs for nothing.
        listed = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign | ast.AnnAssign)
            and isinstance(node.value, ast.List | ast.Tuple)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            roots: list[ast.AST] = []
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LIST_MUTATORS
            ):
                roots = [node.func.value]
            elif isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
                targets = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
                roots = [target for target in targets if isinstance(target, ast.Subscript | ast.Attribute)]
                # ``alias = TOOLS``: a second handle that can change the list
                # where this index cannot see it (#879 review).
                if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(node.value, ast.Name):
                    roots.append(node.value)
            elif isinstance(node, ast.NamedExpr) and isinstance(node.value, ast.Name):
                roots = [node.value]
            elif isinstance(node, ast.Call) and not _leaves_arguments_alone(node):
                # ``register(TOOLS)``: a callee may change the list it is given,
                # unless it is an SDK agent reading its own ``tools=`` or a
                # function this module can read that leaves it alone.
                # An agent, or a copy of one, reads its own ``tools=``.
                agent = (
                    self.sdk_names is not None
                    and self.sdk_names.denotes(
                        dotted_name(node.func), node, "Agent", DEFAULT_AGENT_CONSTRUCTORS
                    )
                ) or (
                    (isinstance(node.func, ast.Attribute) and node.func.attr == "clone")
                    or dotted_name(node.func) in {"replace", "dataclasses.replace", "copy.replace"}
                )
                roots = [
                    arg
                    for position, arg in enumerate(node.args)
                    if isinstance(arg, ast.Name)
                    and arg.id in listed
                    and not self._callee_leaves_alone(node, position, None)
                ]
                roots.extend(
                    keyword.value
                    for keyword in node.keywords
                    if isinstance(keyword.value, ast.Name)
                    and keyword.value.id in listed
                    and not (agent and keyword.arg in {"tools", "handoffs", "mcp_servers"})
                    and not self._callee_leaves_alone(node, None, keyword.arg)
                )
            elif isinstance(node, ast.Global):
                self.changed.update(("module", name) for name in node.names)
            elif isinstance(node, ast.Nonlocal):
                for name in node.names:
                    found = scopes.enclosing_bindings(node, name)
                    if found:
                        self.changed.add(id(found[0]))
            for root in roots:
                while isinstance(root, ast.Attribute | ast.Subscript):
                    root = root.value
                if isinstance(root, ast.Name):
                    found = scopes.enclosing_bindings(root, root.id)
                    self.changed.add(id(found[0]) if found else ("module", root.id))

    def _callee_leaves_alone(
        self, call: ast.Call, position: int | None, keyword: str | None
    ) -> bool:
        """Whether the function ``call`` names never changes the argument it passes."""

        spelling = reference_spelling(call.func)
        if spelling is None or self.resolve is None:
            return False
        resolution = self.resolve(spelling)
        function = resolution.definition if resolution.resolved else None
        if function is None:
            return False
        positional = [*function.args.posonlyargs, *function.args.args]
        if position is not None:
            if position >= len(positional):
                return False
            parameter = positional[position].arg
        elif keyword in {arg.arg for arg in [*positional, *function.args.kwonlyargs]}:
            parameter = str(keyword)
        else:
            return False
        return _parameter_left_alone(function, parameter)

    def _literal(self, name: str, node: ast.AST) -> ast.List | ast.Tuple | None | bool:
        """The one literal list ``name`` holds at ``node``.

        False: not a list variable — a function, an import — so the name is a
        reference. None: bound in a way the reader cannot read as one list.
        """

        found = self.scopes.enclosing_bindings(node, name)
        if found:
            if len(found) != 1:
                return None
            local = found[0]
            if isinstance(local, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.alias):
                return False
            statement = self.scopes.statement_of(local)
            if (
                isinstance(local, ast.Name)
                and isinstance(statement, ast.Assign | ast.AnnAssign)
                and _assignment_target(statement) == name
                and isinstance(statement.value, ast.List | ast.Tuple)
                and id(local) not in self.changed
            ):
                return statement.value
            return None
        bindings = self.module_bindings.get(name, [])
        if all(
            isinstance(item.node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.alias)
            for item in bindings
        ):
            return False
        if len(bindings) != 1 or not bindings[0].top_level:
            return None
        statement = bindings[0].statement
        if (
            isinstance(statement, ast.Assign | ast.AnnAssign)
            and _assignment_target(statement) == name
            and isinstance(statement.value, ast.List | ast.Tuple)
            and ("module", name) not in self.changed
        ):
            return statement.value
        return None

    def _elements(self, value: ast.AST | None, node: ast.AST) -> list[ast.expr] | None:
        if value is None:
            return []
        if isinstance(value, ast.List | ast.Tuple):
            literal: ast.List | ast.Tuple | None | bool = value
        elif isinstance(value, ast.Name):
            literal = self._literal(value.id, node)
            if literal is False:
                return [value]
        else:
            return None
        if not isinstance(literal, ast.List | ast.Tuple) or any(
            isinstance(item, ast.Starred) for item in literal.elts
        ):
            return None
        return list(literal.elts)

    def references(
        self, value: ast.AST | None, node: ast.AST
    ) -> list[tuple[str, ast.expr]] | None:
        """``(spelling, element)`` per listed tool; None when not a readable list."""

        elements = self._elements(value, node)
        if elements is None:
            return None
        references: list[tuple[str, ast.expr]] = []
        for item in elements:
            spelling = reference_spelling(item)
            if spelling is None:
                return None
            references.append((spelling, item))
        return references

    def names(
        self, value: ast.AST | None, node: ast.AST, aliases: dict[str, str]
    ) -> list[str] | None:
        """Handoff names: plain names only, read through ``from`` import aliases."""

        elements = self._elements(value, node)
        if elements is None or not all(isinstance(item, ast.Name) for item in elements):
            return None
        return [aliases.get(item.id, item.id) for item in elements if isinstance(item, ast.Name)]


_SCOPE_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


class _SdkNames:
    """Which spellings, where they are used, denote the SDK's own symbols.

    ``livekit.agents`` also exports ``Agent`` and ``function_tool``, so the
    spelling alone proves nothing. A spelling's head is resolved where Python
    resolves it: the nearest enclosing scope that binds it, with class bodies
    skipped from code nested inside them and ``global``/``nonlocal`` obeyed,
    so an import in a sibling function never decides this use. If that scope
    imports the head, every import there must come from the absolute
    ``agents``/``openai_agents`` package; if it binds the head only otherwise
    (a parameter, an assignment, a local ``def``), it is not the SDK's. A head
    no scope binds keeps the default spellings' terminal-name reading, unless
    a wildcard from another module could supply it.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.module = tree
        self.scope_of: dict[int, ast.AST] = {}
        self.parent: dict[int, ast.AST | None] = {id(tree): None}
        self.bindings: dict[int, dict[str, list[str | None]]] = {}
        self.declared: dict[int, dict[str, str]] = {}
        self.foreign_wildcard = False
        # Decorators, defaults, class bases and a comprehension's first
        # iterable are evaluated in the scope that encloses their owner.
        enclosing: dict[int, ast.AST] = {}
        stack: list[tuple[ast.AST, ast.AST]] = [(tree, tree)]
        while stack:
            node, scope = stack.pop()
            scope = enclosing.get(id(node), scope)
            self.scope_of[id(node)] = scope
            inner = scope
            if isinstance(node, _SCOPE_NODES):
                self.parent[id(node)] = scope
                inner = node
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self._bind(scope, node.name)
                if isinstance(node, ast.ClassDef):
                    outer = [*node.decorator_list, *node.bases, *node.keywords]
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    outer = [
                        *getattr(node, "decorator_list", []),
                        *node.args.defaults,
                        *(default for default in node.args.kw_defaults if default),
                    ]
                else:
                    outer = [node.generators[0].iter]
                enclosing.update((id(item), scope) for item in outer)
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                for alias in node.names:
                    if alias.name == "*":
                        self.foreign_wildcard |= not _is_sdk_path(module)
                    else:
                        path = f"{module}.{alias.name}" if node.module else module + alias.name
                        self._bind(scope, alias.asname or alias.name, path)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    head = alias.name.split(".", 1)[0]
                    bound, path = (alias.asname, alias.name) if alias.asname else (head, head)
                    self._bind(scope, bound, path)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                self._bind(scope, node.id)
            elif isinstance(node, ast.arg):
                self._bind(scope, node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                self._bind(scope, node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                kind = "global" if isinstance(node, ast.Global) else "nonlocal"
                self.declared.setdefault(id(scope), {}).update(dict.fromkeys(node.names, kind))
            stack.extend((child, inner) for child in ast.iter_child_nodes(node))

    def _bind(self, scope: ast.AST, name: str, path: str | None = None) -> None:
        self.bindings.setdefault(id(scope), {}).setdefault(name, []).append(path)

    def _resolve(self, name: str, node: ast.AST) -> list[str | None] | None:
        scope: ast.AST | None = self.scope_of.get(id(node), self.module)
        start = scope
        while scope is not None:
            declared = self.declared.get(id(scope), {}).get(name)
            if declared == "global":
                return self.bindings.get(id(self.module), {}).get(name)
            if declared is None and (scope is start or not isinstance(scope, ast.ClassDef)):
                bound = self.bindings.get(id(scope), {}).get(name)
                if bound is not None:
                    return bound
            scope = self.parent.get(id(scope))
        return None

    def denotes(
        self, spelling: str | None, node: ast.AST, symbol: str, defaults: frozenset[str]
    ) -> bool:
        if not spelling:
            return False
        head, _, rest = spelling.partition(".")
        bound = self._resolve(head, node)
        if bound is None:
            return spelling in defaults and not self.foreign_wildcard
        paths = [path for path in bound if path is not None]
        return bool(paths) and all(
            _is_sdk_path(path) and (f"{path}.{rest}" if rest else path).rsplit(".", 1)[-1] == symbol
            for path in paths
        )


def _is_sdk_path(path: str) -> bool:
    return not path.startswith(".") and path.split(".", 1)[0] in SDK_MODULES


def _function_tool_decorators(tree: ast.Module) -> set[int]:
    """The decorator nodes that are the SDK's ``function_tool``, by identity.

    Decided per node, not per spelling: the same ``@function_tool`` may be the
    SDK's in one function and LiveKit's in its sibling.
    """
    names = _SdkNames(tree)
    return {
        id(decorator)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if names.denotes(
            _decorator_name(decorator), decorator, "function_tool", DEFAULT_FUNCTION_TOOL_DECORATORS
        )
    }


def _is_function_tool(node: ast.AST, sdk_decorators: set[int]) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return any(id(decorator) in sdk_decorators for decorator in node.decorator_list)


def _decorator_name(decorator: ast.AST) -> str | None:
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    return dotted_name(decorator)


def _function_to_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: ToolSourceConfig,
    source_ref: str,
    sdk_decorators: set[int],
) -> Tool:
    tool_name = _tool_name(node, sdk_decorators)
    input_schema, parameters = function_input_schema(node)
    description = _description(node, sdk_decorators) or ast.get_docstring(node)
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
        annotations={"python_symbol": node.name},
        auth=AuthInfo(source="sdk_static"),
        extraction_confidence="medium",
        extraction={"method": "openai_agents_sdk_ast", "confidence": "medium"},
    )


def _tool_name(node: ast.FunctionDef | ast.AsyncFunctionDef, sdk_decorators: set[int]) -> str:
    return _decorator_kwarg_string(node, sdk_decorators, "name_override") or node.name


def _description(
    node: ast.FunctionDef | ast.AsyncFunctionDef, sdk_decorators: set[int]
) -> str | None:
    return _decorator_kwarg_string(node, sdk_decorators, "description_override")


def _decorator_kwarg_string(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    sdk_decorators: set[int],
    kwarg_name: str,
) -> str | None:
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        if not call or id(call) not in sdk_decorators:
            continue
        for keyword in call.keywords:
            if keyword.arg != kwarg_name or not isinstance(keyword.value, ast.Constant):
                continue
            value = keyword.value.value
            if isinstance(value, str) and value:
                return value
    return None


# ---------------------------------------------------------------------------
# Agent-toolkit scope bounds
# ---------------------------------------------------------------------------
#
# Some agent toolkits expose their tools through a runtime factory
# (``*toolkit.get_tools()``) the static extractor cannot enumerate, but the
# *constructor* takes a statically-parseable permission allowlist. We capture
# that allowlist so the verifier can diff it base-vs-head and catch a silent
# broadening (the allowlist removed or widened) even when the individual tools
# stay opaque. See ``core.domain.ToolkitScopeBound`` and the
# ``SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED`` check.
#
# Known agent-toolkit constructors, grouped by the top-level module they are
# imported from. A symbol only counts when it is actually imported from the
# provider's package (resolved through this file's import aliases below), so an
# unrelated local symbol that happens to share the name is NOT matched, and an
# aliased import (``import StripeAgentToolkit as SAT``) IS. Both the class
# constructor and the async factory map to the same provider so a base→head
# switch between them still matches.
_TOOLKIT_MODULES: dict[str, dict[str, str]] = {
    "stripe_agent_toolkit": {
        "StripeAgentToolkit": "stripe",
        "create_stripe_agent_toolkit": "stripe",
    },
}


def _detect_toolkit_bounds(tree: ast.Module, ref: str) -> list[ToolkitScopeBound]:
    name_map, module_aliases = _toolkit_alias_maps(tree)
    if not name_map and not module_aliases:
        return []
    bounds: list[ToolkitScopeBound] = []
    captured: set[int] = set()
    # Assignments first so we can attach the Python binding name, which keys
    # the bound per-instance (see core.toolkit_scope.policy_key_for).
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        call = _unwrap_toolkit_call(value)
        if call is None:
            continue
        bound = _bound_from_toolkit_call(
            call, tree, ref, _first_assign_name(targets), name_map, module_aliases
        )
        if bound is not None:
            bounds.append(bound)
            captured.add(id(call))
    # Bare/inline toolkit constructions not bound to a name.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in captured:
            continue
        bound = _bound_from_toolkit_call(node, tree, ref, None, name_map, module_aliases)
        if bound is not None:
            bounds.append(bound)
    return bounds


def _toolkit_alias_maps(
    tree: ast.Module,
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Resolve local names to known toolkit constructors via the file's imports.

    Returns ``(name_map, module_aliases)``:

    - ``name_map``: local call name → ``(provider, symbol)`` for
      ``from <toolkit_module> import <Symbol> [as <local>]`` (handles aliases).
    - ``module_aliases``: local module alias → top-level toolkit package for
      ``import <toolkit_module>[.sub] [as <alias>]``, so a later
      ``alias.Symbol(...)`` attribute call resolves.

    Only imports whose top-level package is a known toolkit module contribute,
    so an unrelated symbol that merely shares a constructor name is ignored.
    """
    name_map: dict[str, tuple[str, str]] = {}
    module_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            symbols = _toolkit_symbols_for_module(node.module)
            if symbols is None:
                continue
            for alias in node.names:
                provider = symbols.get(alias.name)
                if provider is not None:
                    name_map[alias.asname or alias.name] = (provider, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in _TOOLKIT_MODULES:
                    module_aliases[alias.asname or alias.name] = alias.name.split(".", 1)[0]
    return name_map, module_aliases


def _toolkit_symbols_for_module(module: str | None) -> dict[str, str] | None:
    if not module:
        return None
    return _TOOLKIT_MODULES.get(module.split(".", 1)[0])


def _resolve_toolkit_call(
    call: ast.Call,
    name_map: dict[str, tuple[str, str]],
    module_aliases: dict[str, str],
) -> tuple[str, str] | None:
    """Resolve a call node to ``(provider, constructor)`` via the import maps.

    Matches a bare ``Name`` against ``name_map`` (covers plain and aliased
    ``from`` imports) and an ``Attribute`` against ``module_aliases`` or a
    fully-qualified ``stripe_agent_toolkit…`` path. Returns ``None`` for calls
    that do not resolve to a known toolkit constructor.
    """
    func = call.func
    if isinstance(func, ast.Name):
        return name_map.get(func.id)
    if isinstance(func, ast.Attribute):
        base = dotted_name(func.value)
        if not base:
            return None
        base_top = base.split(".", 1)[0]
        top = module_aliases.get(base_top) or (base_top if base_top in _TOOLKIT_MODULES else None)
        if top is not None:
            provider = _TOOLKIT_MODULES.get(top, {}).get(func.attr)
            if provider is not None:
                return (provider, func.attr)
    return None


def _unwrap_toolkit_call(value: ast.expr) -> ast.Call | None:
    if isinstance(value, ast.Await):
        value = value.value
    return value if isinstance(value, ast.Call) else None


def _first_assign_name(targets: list[ast.expr]) -> str | None:
    for target in targets:
        if isinstance(target, ast.Name):
            return target.id
        if isinstance(target, ast.Attribute):
            return target.attr
    return None


def _bound_from_toolkit_call(
    call: ast.Call,
    tree: ast.Module,
    ref: str,
    binding: str | None,
    name_map: dict[str, tuple[str, str]],
    module_aliases: dict[str, str],
) -> ToolkitScopeBound | None:
    resolved = _resolve_toolkit_call(call, name_map, module_aliases)
    if resolved is None:
        return None
    provider, constructor = resolved
    parser = _CONFIG_PARSERS.get(provider)
    parsed = parser(call) if parser is not None else (False, [])
    if isinstance(parsed, tuple):
        bounded, scopes = parsed
        return ToolkitScopeBound(
            provider=provider,
            constructor=constructor,
            bounded=bounded,
            scopes=sorted(scopes),
            binding=binding,
            source_ref=ref,
            source_line=getattr(call, "lineno", None),
            config_binding="literal" if bounded else "absent",
        )
    # The authority-bearing argument is present but not a literal. Trace it
    # conservatively (docs/engineering/config-bound-capability-detection.md):
    # a name bound from a config read becomes a ``config``-bound marker the
    # verify diff can compare base-vs-head; anything else is ``unknown`` — a
    # marker + source warning (never silence, mirroring the ADK dynamic-
    # toolset fail-open fix) that no removal check ever fires on.
    trace = trace_config_binding(
        parsed, tree=tree, line=getattr(call, "lineno", 0)
    )
    return ToolkitScopeBound(
        provider=provider,
        constructor=constructor,
        bounded=False,
        scopes=[],
        binding=binding,
        source_ref=ref,
        source_line=getattr(call, "lineno", None),
        config_binding=trace.binding,
        config_path=trace.config_path,
    )


def _parse_stripe_configuration(
    call: ast.Call,
) -> tuple[bool, list[str]] | ast.expr:
    """Read the Stripe ``configuration={"actions": {...}}`` allowlist.

    Returns ``(bounded, scopes)``:

    - No ``configuration`` keyword, OR a ``configuration`` dict with no
      ``actions`` key → ``(False, [])``. The Stripe toolkit mounts its FULL
      surface when ``actions`` is absent, so this is *unbounded*, not "bounded
      to no tools" — otherwise a base ``actions={…}`` → head
      ``configuration={"context": …}`` migration would read as a narrowing and
      escape the gate.
    - ``configuration`` (or its ``actions``) present but not a dict literal →
      the offending *expression* (the authority-bearing argument), which the
      caller traces for a config binding instead of silently skipping.
    - ``actions`` is a dict literal → ``(True, scopes)`` with each truthy
      ``resource:verb`` flattened. An explicit empty ``actions={}`` is
      ``(True, [])`` — explicitly bounded to no tools.
    """
    config = _keyword_value(call, "configuration")
    if config is None:
        return (False, [])
    if not isinstance(config, ast.Dict):
        return config
    actions = _dict_get(config, "actions")
    if actions is None:
        return (False, [])
    if not isinstance(actions, ast.Dict):
        return actions
    scopes: list[str] = []
    for resource_key, resource_value in zip(actions.keys, actions.values, strict=False):
        resource = _const_str(resource_key)
        if resource is None:
            continue
        if isinstance(resource_value, ast.Dict):
            for verb_key, verb_value in zip(
                resource_value.keys, resource_value.values, strict=False
            ):
                verb = _const_str(verb_key)
                if verb is not None and _is_truthy_const(verb_value):
                    scopes.append(f"{resource}:{verb}")
        elif _is_truthy_const(resource_value):
            scopes.append(f"{resource}:*")
    return (True, scopes)


# Per-provider configuration parsers. Provider detection (imports) is kept
# separate from config-shape parsing so a future provider with a different
# allowlist shape only adds a parser here.
_CONFIG_PARSERS = {"stripe": _parse_stripe_configuration}


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _dict_get(node: ast.Dict, name: str) -> ast.expr | None:
    for key, value in zip(node.keys, node.values, strict=False):
        if _const_str(key) == name:
            return value
    return None


def _const_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_truthy_const(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value) is True


class OpenAISDKAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_openai_sdk_static_tools`."""

    source_type: ClassVar[str] = "openai_agents_sdk"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
    artifact_class: ClassVar[type | None] = None

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="openai_agents_sdk",
        label="OpenAI Agents SDK (Python)",
        reads=(
            "Python modules parsed with `ast` and never imported."
        ),
        cells=(
            BoundaryCell(
                shape="export_artifact",
                status="not_applicable",
                reads=(
                    "This input declares no reviewed inventory of its own; a "
                    "committed export is configured as its own `mcp` or `openapi` "
                    "source."
                ),
            ),
            BoundaryCell(
                shape="literal_registration",
                status="extracted",
                reads=(
                    "A module-level function decorated with `@function_tool`, read "
                    "for its name, docstring, and annotated parameters — including "
                    "one an agent's `tools=[...]` reaches through a "
                    "repository-local import inside the read directory."
                ),
                emits=("sdk_function",),
                ceiling="medium",
            ),
            BoundaryCell(
                shape="factory",
                status="not_extracted",
                reads=(
                    "A recognised agent-toolkit constructor records a "
                    "statically-parsed least-privilege scope bound and a warning. "
                    "Naming the actions it returns would mean running it."
                ),
                raises=("SHIP-SCOPE-TOOLKIT-UNBOUNDED",),
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="not_extracted",
                reads=(
                    "`tools=<expression>` that is not a literal list of readable "
                    "names records a binding warning; whatever the expression would "
                    "have produced never enters the catalog."
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
        assert source is not None, "per_source adapter requires a source"
        return LoadedAdapterResult(
            tool_sources=[load_openai_sdk_static_tools(source, manifest, base_dir)]
        )
