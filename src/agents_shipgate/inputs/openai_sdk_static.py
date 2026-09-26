from __future__ import annotations

import ast
from pathlib import Path
from typing import ClassVar, Literal

from agents_shipgate.core.domain import (
    AgentBindingObservation,
    AuthInfo,
    LoadedToolSource,
    Tool,
    ToolkitScopeBound,
    UnreadAgentConstruction,
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
    binding_warnings, binding_observations, recovery_evidence, unread = _extract_agent_bindings(
        tools, python_files, source, base_dir
    )
    return LoadedToolSource(
        source_id=source.id,
        source_type="openai_agents_sdk",
        tools=tools,
        toolkit_bounds=toolkit_bounds,
        binding_observations=binding_observations,
        unread_agent_constructions=unread,
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
    list[UnreadAgentConstruction],
]:
    """Extract exact, local-only ``Agent(..., tools=[...])`` wiring.

    This intentionally resolves only literal lists, names bound to literal
    lists, and local/imported function-tool identifiers. Dynamic expressions
    are preserved as partial evidence instead of being guessed. Every other
    construction of the SDK's ``Agent`` is returned as unread (#876).
    """

    warnings: list[str] = []
    observations: list[AgentBindingObservation] = []
    recovery_evidence: list[SourceRecoveryEvidence] = []
    unread: list[UnreadAgentConstruction] = []
    tool_by_name = {tool.name: tool for tool in tools}
    tool_by_name.update(
        {
            symbol: tool
            for tool in tools
            if isinstance((symbol := tool.annotations.get("python_symbol")), str)
        }
    )
    for path in paths:
        tree = parse_python_file(path, label="OpenAI Agents SDK")
        source_ref = display_path(path, base_dir)
        sdk_names = _SdkNames(tree)
        # Keyed by the scope that binds the name, so a builder's ``tools``
        # parameter never resolves to a module list that happens to share it.
        list_vars: dict[tuple[int, str], list[str] | None] = {}
        import_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    import_aliases[alias.asname or alias.name] = alias.name
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = _assignment_target(node)
                value = node.value
                if target and isinstance(value, (ast.List, ast.Tuple)):
                    scope = id(sdk_names.scope_of[id(node)])
                    list_vars[(scope, target)] = _literal_names(value, import_aliases)
        constructions, unread_sites = _agent_constructions(tree, sdk_names)
        unread.extend(
            UnreadAgentConstruction(
                source=source_ref,
                source_pointer=f"{source_ref}:{line}",
                reason=(
                    f"OpenAI Agents SDK agent construction at {source_ref}:{line} is "
                    f"not read ({form}); its agent and tool bindings are not established."
                ),
            )
            for line, form in unread_sites
        )
        for target, call in constructions:
            tools_expr = _keyword(call, "tools")
            names = _resolve_name_list(tools_expr, list_vars, import_aliases, sdk_names)
            if tools_expr is None and _unpacks_arguments(call):
                # `Agent(name="x", **config)` may carry `tools`; an empty
                # list would be a false complete answer.
                names = None
            pointer = f"{source_ref}:{call.lineno}"
            issues: list[str] = []
            tools_complete = True
            if names is None:
                reason = (
                    f"OpenAI Agents SDK agent {target!r} at {pointer} uses a "
                    "dynamic tools expression; its binding graph is incomplete."
                    if tools_expr is not None
                    else f"OpenAI Agents SDK agent {target!r} at {pointer} unpacks "
                    "arguments that may set its tools; its binding graph is incomplete."
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
                names = []
            else:
                for name in names:
                    if tool_by_name.get(name) is None:
                        reason = (
                            f"OpenAI Agents SDK agent {target!r} at {pointer} binds "
                            f"unresolved tool {name!r}."
                        )
                        warnings.append(reason)
                        issues.append(reason)
                        tools_complete = False
                names = [
                    tool_by_name[name].name if name in tool_by_name else name
                    for name in names
                ]
            handoffs_expr = _keyword(call, "handoffs")
            handoff_names = _resolve_name_list(
                handoffs_expr, list_vars, import_aliases, sdk_names
            )
            if handoffs_expr is None and _unpacks_arguments(call):
                handoff_names = None
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
                    handoff_names=handoff_names,
                    tools_complete=tools_complete,
                    handoffs_complete=handoffs_complete,
                    issues=issues,
                )
            )
    return list(dict.fromkeys(warnings)), observations, recovery_evidence, unread


def _agent_constructions(
    tree: ast.Module, sdk_names: _SdkNames
) -> tuple[list[tuple[str, ast.Call]], list[tuple[int, str]]]:
    """Partition every construction of the SDK's ``Agent`` in one module.

    Returns the constructions the reader establishes, with the identity each
    is keyed by, and ``(line, form)`` for every other one. Two forms are
    established: ``name = Agent(...)``, keyed by the bound name, and
    ``return Agent(name="...", ...)`` in a function or method, keyed by its
    literal ``name=`` — the identity ``_still_named_at`` and the ADK reader
    already use. Everything else the module visibly constructs as an agent is
    unread, never silently absent (#876): an agent passed inline, a
    parameterized ``Agent[Ctx](...)``, a clone of an agent this module
    assigns, and a class deriving from ``Agent``, whose instances the reader
    does not follow.
    """

    def is_agent(expression: ast.AST, node: ast.AST) -> bool:
        return sdk_names.denotes(
            dotted_name(expression), node, "Agent", DEFAULT_AGENT_CONSTRUCTORS
        )

    established: list[tuple[str, ast.Call]] = []
    unread: list[tuple[int, str]] = []
    claimed: set[int] = set()
    assigned: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            call, target = node.value, _assignment_target(node)
            if target and isinstance(call, ast.Call) and is_agent(call.func, call):
                established.append((target, call))
                claimed.add(id(call))
                assigned.add((id(sdk_names.scope_of[id(node)]), target))
        elif isinstance(node, ast.Return):
            call = node.value
            if isinstance(call, ast.Call) and is_agent(call.func, call):
                claimed.add(id(call))
                name = _keyword(call, "name")
                if isinstance(name, ast.Constant) and isinstance(name.value, str) and name.value:
                    established.append((name.value, call))
                else:
                    unread.append((call.lineno, "returned without a literal name="))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                expression = base.value if isinstance(base, ast.Subscript) else base
                if is_agent(expression, expression):
                    unread.append(
                        (
                            node.lineno,
                            f"class {node.name!r} derives from Agent; its instances "
                            "are not followed",
                        )
                    )
                    break
        if not isinstance(node, ast.Call) or id(node) in claimed:
            continue
        func = node.func
        if isinstance(func, ast.Subscript) and is_agent(func.value, func.value):
            unread.append((node.lineno, "a parameterized Agent[...]"))
        elif is_agent(func, node):
            unread.append((node.lineno, "not assigned to a single name or returned"))
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "clone"
            and isinstance(func.value, ast.Name)
            and (scope := sdk_names.binding_scope(func.value.id, func.value)) is not None
            and (id(scope), func.value.id) in assigned
        ):
            unread.append((node.lineno, f"a clone of agent {func.value.id!r}"))
    return established, sorted(set(unread))


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


def _unpacks_arguments(call: ast.Call) -> bool:
    """``*args`` or ``**kwargs`` can supply any keyword the call does not spell."""

    return any(isinstance(arg, ast.Starred) for arg in call.args) or any(
        keyword.arg is None for keyword in call.keywords
    )


def _assignment_target(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _literal_names(
    value: ast.List | ast.Tuple, aliases: dict[str, str]
) -> list[str] | None:
    names: list[str] = []
    for item in value.elts:
        if not isinstance(item, ast.Name):
            return None
        names.append(aliases.get(item.id, item.id))
    return names


def _resolve_name_list(
    value: ast.AST | None,
    list_vars: dict[tuple[int, str], list[str] | None],
    aliases: dict[str, str],
    sdk_names: _SdkNames,
) -> list[str] | None:
    if value is None:
        return []
    if isinstance(value, (ast.List, ast.Tuple)):
        return _literal_names(value, aliases)
    if isinstance(value, ast.Name):
        scope = sdk_names.binding_scope(value.id, value)
        if scope is not None and (id(scope), value.id) in list_vars:
            return list_vars[(id(scope), value.id)]
        if scope is not None and (id(scope), value.id) in sdk_names.parameters:
            # The caller supplies it; its value is not this module's to read.
            return None
        return [aliases.get(value.id, value.id)]
    return None


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
        #: ``(scope, name)`` for each function or lambda parameter.
        self.parameters: set[tuple[int, str]] = set()
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
                self.parameters.add((id(scope), node.arg))
            elif isinstance(node, ast.ExceptHandler) and node.name:
                self._bind(scope, node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                kind = "global" if isinstance(node, ast.Global) else "nonlocal"
                self.declared.setdefault(id(scope), {}).update(dict.fromkeys(node.names, kind))
            stack.extend((child, inner) for child in ast.iter_child_nodes(node))

    def _bind(self, scope: ast.AST, name: str, path: str | None = None) -> None:
        self.bindings.setdefault(id(scope), {}).setdefault(name, []).append(path)

    def binding_scope(self, name: str, node: ast.AST) -> ast.AST | None:
        """The scope whose binding of ``name`` a use at ``node`` reads."""

        scope: ast.AST | None = self.scope_of.get(id(node), self.module)
        start = scope
        while scope is not None:
            declared = self.declared.get(id(scope), {}).get(name)
            if declared == "global":
                return self.module if name in self.bindings.get(id(self.module), {}) else None
            if declared is None and (scope is start or not isinstance(scope, ast.ClassDef)):
                if name in self.bindings.get(id(scope), {}):
                    return scope
            scope = self.parent.get(id(scope))
        return None

    def _resolve(self, name: str, node: ast.AST) -> list[str | None] | None:
        scope = self.binding_scope(name, node)
        return None if scope is None else self.bindings[id(scope)][name]

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
                    "for its name, docstring, and annotated parameters."
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
