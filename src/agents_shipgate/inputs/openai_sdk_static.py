from __future__ import annotations

import ast
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, NamedTuple

from agents_shipgate.core.domain import (
    AgentBindingObservation,
    AuthInfo,
    CapabilityChangeObservation,
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
    reflective_access,
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
        capability_changes,
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
        capability_changes=capability_changes,
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
    capability_changes: list[CapabilityChangeObservation] = []
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
        # The call an assignment binds to a plain name: that name is the
        # agent's identity, as it always has been. Any other construction —
        # ``return Agent(...)``, ``self.agent = Agent(...)``, an agent inline in
        # a list — is identified by its literal ``name`` (#876). A variable name
        # assigned in more than one function (two builders' local ``agent``) is
        # no identity at all, so those agents take their literal ``name`` too;
        # anywhere else a rename or a move between scopes keeps the identity
        # it had (#876 review).
        assigned: dict[int, str] = {}
        variable_scopes: dict[str, set[int]] = {}
        function_local: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    import_aliases[alias.asname or alias.name] = alias.name
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = _assignment_target(node)
                if target and isinstance(node.value, ast.Call):
                    assigned[id(node.value)] = target
                    enclosing = _enclosing_scope(scopes, node)
                    variable_scopes.setdefault(target, set()).add(id(enclosing))
                    if enclosing is not None:
                        function_local.add(id(node.value))
        shared = {name for name, found in variable_scopes.items() if len(found) > 1}

        def identity_of(
            call: ast.Call,
            assigned: dict[int, str] = assigned,
            function_local: set[int] = function_local,
            shared: set[str] = shared,
        ) -> str | None:
            target, literal = assigned.get(id(call)), _literal_agent_name(call)
            if literal is not None and target in shared and id(call) in function_local:
                return literal
            return target or literal
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
        subclasses = _agent_subclasses(tree, sdk_names)
        values = _AgentValues(tree, scopes, tool_lists.module_bindings, sdk_names, subclasses)
        #: ``list binding -> identities`` of agents constructed with that list.
        list_holders: dict[object, set[str]] = {}
        copies: list[ast.Call] = []

        def unread(reason: str, pointer: str, kind: str, path: str = source_ref) -> None:
            # A construction the reader saw but cannot establish is a named
            # limit on this file, never silence: an unobserved agent must not
            # let the comparison read as complete (#876).
            warnings.append(reason)
            recovery_evidence.append(SourceRecoveryEvidence(
                warning=reason, source_id=source.id, source_type="openai_agents_sdk",
                source_ref=pointer, path=path,
                recovery=CoverageRecovery(kind="unresolved", reason=kind),
            ))

        def unread_agent(
            call: ast.Call,
            reason: str,
            kind: str,
            file_observations: list[AgentBindingObservation],
            source_ref: str = source_ref,
            identity_of: Callable[[ast.Call], str | None] = identity_of,
            unread: Callable[..., None] = unread,
            values: _AgentValues = values,
        ) -> None:
            # An agent whose capabilities the reader cannot read is still an
            # agent: a named limit on *it*, so other agents' rows stand.
            pointer = f"{source_ref}:{call.lineno}"
            identity = identity_of(call)
            if identity is None:
                unread(reason, pointer, kind)
                return
            values.constructed[id(call)] = identity
            warnings.append(reason)
            file_observations.append(
                AgentBindingObservation(
                    agent=identity,
                    source_id=source.id,
                    source=source_ref,
                    source_pointer=pointer,
                    tools_complete=False,
                    handoffs_complete=False,
                    issues=[reason],
                )
            )

        file_observations: list[AgentBindingObservation] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            pointer = f"{source_ref}:{node.lineno}"
            if _copy_shape(node) is not None:
                # Whether the receiver is an agent is known only once every
                # construction in the file has been read.
                copies.append(node)
                continue
            func = node.func.value if isinstance(node.func, ast.Subscript) else node.func
            if isinstance(func, ast.Name) and func.id in subclasses:
                unread_agent(
                    node,
                    f"OpenAI Agents SDK agent at {pointer} is built from the subclass "
                    f"{func.id!r} (line {subclasses[func.id]}), whose constructor this "
                    "reader does not read.",
                    "sdk_agent_subclass_unread",
                    file_observations,
                )
                continue
            if not _denotes_agent(sdk_names, node):
                continue
            call = node
            target = identity_of(call)
            if target is None:
                unread(
                    f"OpenAI Agents SDK agent constructed at {pointer} has no literal "
                    "name, so it cannot be identified; its tools are not read.",
                    pointer,
                    "sdk_agent_identity_unresolved",
                )
                continue
            values.constructed[id(call)] = target
            # The list object this agent holds, when ``tools=`` names one: an
            # in-place change through any agent sharing it reaches this one too.
            shared_tools = _keyword(call, "tools")
            if isinstance(shared_tools, ast.Name):
                found_list = scopes.enclosing_bindings(call, shared_tools.id)
                list_holders.setdefault(
                    id(found_list[0]) if found_list else ("module", shared_tools.id), set()
                ).add(target)
            opaque = _opaque_arguments(call)
            if opaque is not None:
                reason = (
                    f"OpenAI Agents SDK agent {target!r} at {pointer} is constructed with "
                    f"{opaque}, so its tools and handoffs are not read."
                )
                warnings.append(reason)
                file_observations.append(
                    AgentBindingObservation(
                        agent=target, source_id=source.id, source=source_ref,
                        source_pointer=pointer, tools_complete=False,
                        handoffs_complete=False, issues=[reason],
                    )
                )
                continue
            tools_expr = _keyword(call, "tools")
            references = tool_lists.references(tools_expr, call)
            issues: list[str] = []
            tools_complete = True
            names: list[str] = []
            locators: dict[str, str] = {}
            tool_issues: dict[str, str] = {}
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
                        tool_issues.pop(tool.name, None)
                        continue
                    names.append(tool.name)
                    if locator is not None:
                        locators[tool.name] = locator
                        first_location.setdefault(tool.name, tool.source_location or locator)
                    if detail:
                        # Named, never established: code that runs first is
                        # not read (#879 review).
                        reason = (
                            f"OpenAI Agents SDK agent {target!r} at {pointer} binds "
                            f"{tool.name!r} ({tool.source_location}), but {detail}; the "
                            "definition read for it is not established as the one bound."
                        )
                        warnings.append(reason)
                        tool_issues[tool.name] = reason
            handoff_names = tool_lists.names(
                _keyword(call, "handoffs"), call, import_aliases, identity_of, sdk_names
            )
            handoffs_complete = True
            if handoff_names is None:
                reason = f"OpenAI Agents SDK agent {target!r} has dynamic handoffs at {pointer}."
                warnings.append(reason)
                issues.append(reason)
                handoffs_complete = False
                handoff_names = []
            file_observations.append(
                AgentBindingObservation(
                    agent=target,
                    source_id=source.id,
                    source=source_ref,
                    source_pointer=pointer,
                    tool_names=names,
                    tool_locators=locators,
                    tool_issues=tool_issues,
                    handoff_names=handoff_names,
                    tools_complete=tools_complete,
                    handoffs_complete=handoffs_complete,
                    issues=issues,
                )
            )
        for node in copies:
            clone_of = _capability_rebinding_copy(
                node, lambda receiver, node=node, values=values: values.is_agent(receiver, node)
            )
            if clone_of is None:
                continue
            pointer = f"{source_ref}:{node.lineno}"
            unread_agent(
                node,
                f"OpenAI Agents SDK agent copy at {pointer} ({clone_of}) is not read: "
                "it passes its own tools, handoffs or MCP servers.",
                "sdk_agent_clone_unread",
                file_observations,
            )
        # A change to an agent's tools, handoffs or MCP servers after it is
        # constructed — ``agent.tools.append(x)``, ``self.agent.tools = [...]``,
        # ``setattr(agent, "tools", ...)``, a handle ``t = agent.tools`` — is a
        # limit on the agent the changed value is, read in the scope of the
        # change. An agent imported from another file, or one another file's
        # function returns, is a limit on that file's agents. Any other value
        # the reader cannot identify may be any agent, here or elsewhere; one
        # it can prove is not an agent is nothing (#876 review).
        unattributed = False
        first_unattributed = [""]
        #: ``agent -> reason`` of the agents a change here limits, and the
        #: ``(agent, capability)`` pairs already recorded.
        limited: dict[str, str] = {}
        limited_kinds: set[tuple[str, str, bool]] = set()
        #: Other files whose agents a change here already limits.
        homes: set[str] = set()
        module_callees = ModuleCallees(
            tree,
            scopes,
            tool_lists.module_bindings,
            resolver=imports.resolver if module is not None else None,
            module=module,
            registry=imports.callees,
        )

        def agent_home(
            receiver: ast.expr | None,
            site: ast.AST,
            *,
            module: PythonModule | None = module,
            scopes: ScopeIndex = scopes,
            tool_lists: _ToolLists = tool_lists,
            module_callees: ModuleCallees = module_callees,
        ) -> tuple[str, str] | None:
            """``(file, what)`` when the receiver is an agent another file of the
            scope constructs: a name imported from it (``from app.plant import
            plant_agent``), or bound to what its function returns (``support =
            build(...)`` with ``build`` returning ``Agent(...)``)."""

            if module is None:
                return None
            node: ast.AST | None
            statement: ast.AST | None
            if isinstance(receiver, ast.Call):
                # ``apply(build(), ...)``: what another file's builder returns.
                name, local, node = "_", [], None
                statement = ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=receiver)
            elif isinstance(receiver, ast.Name):
                name = receiver.id
                local = scopes.enclosing_bindings(site, name)
                if local:
                    if len(local) != 1:
                        return None
                    node = local[0]
                    statement = scopes.statement_of(node)
                else:
                    found = tool_lists.module_bindings.get(name, [])
                    if len(found) != 1:
                        return None
                    node, statement = found[0].node, found[0].statement
            else:
                return None
            if isinstance(node, ast.alias):
                if not isinstance(statement, ast.Import | ast.ImportFrom):
                    return None
                resolution = (
                    imports.resolver.resolve_local_import(module, statement, node, name)
                    if local
                    else imports.resolver.resolve(module, name)
                )
                home, value = resolution.module, resolution.value
                if (
                    home is None
                    or home.path == module.path
                    or not isinstance(value, ast.Call)
                    or not _denotes_agent(_SdkNames(home.tree), value)
                ):
                    return None
                return home.ref, f"{name!r} ({home.ref}:{value.lineno})"
            if not isinstance(statement, ast.Assign | ast.AnnAssign) or _assignment_target(
                statement
            ) != name or not isinstance(statement.value, ast.Call):
                return None
            callee = module_callees(statement.value)
            home = callee.callees.module if callee is not None else None
            if callee is None or home is None or home.path == module.path:
                return None
            if not _returns_agent(callee.function, _SdkNames(home.tree), callee.callees.scopes):
                return None
            return home.ref, (
                f"the agent {callee.function.name!r} returns ({home.ref}:{callee.function.lineno})"
            )

        def record_change(
            owner: str | bool | None,
            site: ast.AST,
            *,
            receiver: ast.expr | None = None,
            lookup: ast.AST | None = None,
            limited: dict[str, str] = limited,
            limited_kinds: set[tuple[str, str, bool]] = limited_kinds,
            first_unattributed: list[str] = first_unattributed,
            scopes: ScopeIndex = scopes,
            homes: set[str] = homes,
            source_ref: str = source_ref,
            list_holders: dict[Any, set[str]] = list_holders,
            file_observations: list[AgentBindingObservation] = file_observations,
            agent_home: Callable[..., tuple[str, str] | None] = agent_home,
        ) -> None:
            nonlocal unattributed
            # One limit per agent, and one for the file: the first site names it.
            if owner is None:
                return
            pointer = f"{source_ref}:{site.lineno}"  # type: ignore[attr-defined]
            removes, in_place = _change_effect(site)
            if owner is True:
                # ``lookup``: where a container member is written, which is
                # where its name is bound.
                home = agent_home(receiver, lookup or site)
                capability = "*" if lookup is not None else _changed_capability(receiver, site, scopes)
                if home is not None:
                    path, what = home
                    warning = (
                        f"OpenAI Agents SDK tools, handoffs or MCP servers of {what} are "
                        f"changed after construction at {pointer}, which this reader does "
                        f"not follow; the agents constructed in {path} are not established."
                    )
                    if path not in homes:
                        homes.add(path)
                        unread(warning, pointer, "sdk_capability_change_elsewhere", path=path)
                    # Every change, so the comparison knows each list it reaches.
                    capability_changes.append(
                        CapabilityChangeObservation(
                            warning=next(
                                (item.warning for item in capability_changes if item.home == path),
                                warning,
                            ),
                            home=path,
                            capability=capability,
                            in_place=in_place,
                            removes=removes,
                        )
                    )
                    return
                warning = (
                    f"OpenAI Agents SDK tools, handoffs or MCP servers are changed at "
                    f"{pointer} on a value this reader cannot identify; any agent it may "
                    f"be, constructed in {source_ref} or in another file, is not established."
                )
                if not unattributed:
                    unattributed = True
                    unread(warning, pointer, "sdk_capability_change_unattributed")
                    first_unattributed[0] = warning
                capability_changes.append(
                    CapabilityChangeObservation(
                        warning=first_unattributed[0],
                        capability=capability,
                        in_place=in_place,
                        removes=removes,
                    )
                )
                return
            assert isinstance(owner, str)
            # Which list changed: a change to ``handoffs`` does not make what
            # the constructor binds as ``tools`` uncertain (#876 review).
            kind = _changed_capability(receiver, site, scopes)
            # An ``append`` first, then a ``clear``: the second can still undo
            # what the constructor binds (#876 review).
            if (owner, kind, removes) in limited_kinds:
                return
            limited_kinds.add((owner, kind, removes))
            reason = limited.get(owner)
            if reason is None:
                reason = limited[owner] = (
                    f"OpenAI Agents SDK agent {owner!r} has its tools, handoffs or MCP "
                    f"servers changed after construction at {pointer}, which this reader "
                    "does not follow."
                )
                warnings.append(reason)
            # Changed in place (not re-assigned): every agent built from the same
            # list object holds the change too (#876 review).
            reached = {owner}
            if not _reassigns(site):
                for holders in list_holders.values():
                    if owner in holders:
                        reached |= holders
            for observation in file_observations:
                if observation.agent in reached:
                    observation.tools_complete = False
                    observation.handoffs_complete = False
                    if reason not in observation.issues:
                        observation.issues.append(reason)
                    if removes:
                        # An ``append`` cannot undo what the constructor binds.
                        observation.changed_after_construction.setdefault(kind, reason)

        def scope_imported(
            receiver: ast.expr,
            site: ast.AST,
            *,
            module: PythonModule | None = module,
            scopes: ScopeIndex = scopes,
            tool_lists: _ToolLists = tool_lists,
        ) -> bool:
            """Whether a receiver is reached from a name imported from the scope
            (``from app.registry import AGENTS``, ``getattr(registry, "a")``)."""

            if module is None:
                return False
            for root in _roots(_through_loops(receiver, site, scopes, tool_lists.module_bindings)):
                if scopes.enclosing_bindings(site, root):
                    continue
                if not any(
                    isinstance(item.node, ast.alias)
                    for item in tool_lists.module_bindings.get(root, [])
                ):
                    continue
                resolution = imports.resolver.resolve(module, root)
                if resolution.steps and resolution.reason not in _OUTSIDE_THE_SCOPE:
                    return True
            return False

        rewires: dict[tuple[int, int], Callable[[], bool]] = {}
        for receiver, site, strict, via_unresolved in _capability_changes(
            tree, scopes=scopes, callees=module_callees, rewires=rewires
        ):
            owner = values.owner(receiver, site)
            check = rewires.get((id(receiver), id(site)))
            if check is not None:
                # A call that may hand an agent to a function rewriting it:
                # only for an agent the file names or imports, or a builder's
                # result — never a value merely held in some container — and
                # only when the function does rewrite it (#876 review).
                if owner is None:
                    continue
                direct = (
                    isinstance(owner, str)
                    or scope_imported(receiver, site)
                    or agent_home(receiver, site) is not None
                )
                unknown: list[tuple[ast.expr, ast.AST]] = []
                held = values.agent_like(receiver, site, unknown=unknown) if not direct else None
                members = [
                    (member, where)
                    for member, where in unknown
                    if scope_imported(member, where) or agent_home(member, where) is not None
                ]
                if not (direct or held or members) or not check():
                    continue
                if direct:
                    record_change(owner, site, receiver=receiver)
                for item in sorted(held or ()):
                    record_change(item, site, receiver=receiver)
                for member, where in members:
                    record_change(True, site, receiver=member, lookup=where)
                continue
            owners: list[str | bool | None] = [owner]
            if owner is not None and not isinstance(owner, str):
                # A receiver the reader cannot name may still be one of the
                # file's own agents, held in its containers: the change is a
                # limit on those (#876 review).
                unknown: list[tuple[ast.expr, ast.AST]] = []
                held = values.agent_like(receiver, site, unknown=unknown)
                if held or unknown:
                    owners = list(sorted(held or ()))
                    # A member that may be another file's agent is limited
                    # where it comes from, or everywhere (#876 review).
                    for element, where in unknown:
                        record_change(True, site, receiver=element, lookup=where)
                elif (
                    strict
                    and held is None
                    and not scope_imported(receiver, site)
                    and not via_unresolved
                    and agent_home(receiver, site) is None
                ):
                    # Handed on, not changed in view: followed that far only
                    # for the file's agents, one imported from the scope, or a
                    # value handed to the application's own code that is not
                    # read — a parameter, a factory's result, ``self.agent``
                    # may be any agent (#876 review).
                    continue
            for item in owners:
                record_change(item, site, receiver=receiver)
        # One identity constructed twice in a file — ``if premium: return
        # Agent(name="Quote", ...)`` / ``else: return Agent(name="Quote", ...)``
        # — is merged by the binding graph into one agent. Its tools cannot be
        # attributed to either construction, so the agent is incomplete rather
        # than silently the union of both (#876 review). Constructions that
        # bind exactly the same tools and handoffs are the same agent either way.
        sites: dict[str, list[str]] = {}
        shapes: dict[str, set[tuple[object, ...]]] = {}
        for observation in file_observations:
            sites.setdefault(observation.agent, []).append(observation.source_pointer or source_ref)
            shapes.setdefault(observation.agent, set()).add(
                (
                    tuple(observation.tool_names),
                    tuple(sorted(observation.tool_locators.items())),
                    tuple(observation.handoff_names),
                    observation.tools_complete,
                    observation.handoffs_complete,
                    tuple(observation.issues),
                )
            )
        for identity, pointers in sites.items():
            if len(pointers) < 2:
                continue
            if len(shapes[identity]) == 1 and not next(iter(shapes[identity]))[-1]:
                # The same agent either way: one observation, not two that the
                # comparison would read as an ambiguous identity.
                first = next(o for o in file_observations if o.agent == identity)
                file_observations = [
                    o for o in file_observations if o.agent != identity or o is first
                ]
                continue
            reason = (
                f"OpenAI Agents SDK agent {identity!r} is constructed more than once in "
                f"{source_ref} ({', '.join(p.rsplit(':', 1)[-1] for p in pointers)}); "
                "its tools are not attributed to either construction."
            )
            warnings.append(reason)
            for observation in file_observations:
                if observation.agent == identity:
                    observation.tools_complete = False
                    observation.handoffs_complete = False
                    if reason not in observation.issues:
                        observation.issues.append(reason)
        observations.extend(file_observations)
    return (
        list(dict.fromkeys(warnings)),
        observations,
        recovery_evidence,
        imports.new_tools,
        imports.new_guards,
        capability_changes,
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
        #: The callee resolvers of every module a change analysis reached.
        self.callees: dict[Path, ModuleCallees] = {}

    def tool_for(
        self,
        reference: str,
        module: PythonModule | None,
        source_ref: str,
        tool_by_name: dict[str, Tool],
        import_aliases: dict[str, str],
    ) -> tuple[Tool | None, str | None]:
        """The tool ``reference`` binds, or None and why not; see
        :meth:`tool_from_resolution` for a tool that comes with a reason."""

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
        """The tool one import resolution reached, or None and why not.

        A tool comes with a reason too when the resolution carries a caveat:
        code that runs before the name is used is not read, so the binding is
        named and never established (#879 review).
        """

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
        return tool, "; ".join(resolution.caveats) or None


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


def _denotes_agent(sdk_names: _SdkNames, node: ast.AST) -> bool:
    """Whether ``node`` — a call, or a class base — names the SDK's ``Agent``.

    ``Agent[Context](...)`` parameterizes the class before calling it; the
    subscript does not change which class is constructed.
    """
    func = node.func if isinstance(node, ast.Call) else node
    if isinstance(func, ast.Subscript):
        func = func.value
    return sdk_names.denotes(dotted_name(func), node, "Agent", DEFAULT_AGENT_CONSTRUCTORS)


def _literal_agent_name(call: ast.Call) -> str | None:
    """The ``name`` an unassigned ``Agent(...)`` is constructed with."""

    value = _keyword(call, "name")
    if value is None and call.args:
        value = call.args[0]
    return _const_str(value) if isinstance(value, ast.expr) else None


#: Keywords that give an agent copy capabilities of its own.
_CAPABILITY_KEYWORDS = frozenset({"tools", "handoffs", "mcp_servers"})
#: Methods that change a list in place.
_LIST_MUTATORS = frozenset({"append", "extend", "insert", "remove", "pop", "clear"})


#: Spellings of ``replace`` that copy a dataclass with changed fields.
_REPLACE_FUNCTIONS = frozenset({"replace", "dataclasses.replace", "copy.replace"})


def _copy_shape(call: ast.Call) -> tuple[ast.expr, str, bool] | None:
    """``(receiver, kind, explicit)`` for ``x.clone(...)`` / ``replace(x, ...)`` passing capabilities.

    ``explicit``: a ``tools=`` / ``handoffs=`` / ``mcp_servers=`` keyword, as
    opposed to only ``**`` unpacking, which could carry anything or nothing.
    """

    keywords = {keyword.arg for keyword in call.keywords}
    explicit = bool(keywords & _CAPABILITY_KEYWORDS)
    if not explicit and None not in keywords:
        return None
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "clone":
        return func.value, "clone", explicit
    name = dotted_name(func)
    if name in _REPLACE_FUNCTIONS and call.args:
        return call.args[0], "copy.replace" if name == "copy.replace" else "dataclasses.replace", explicit
    return None


def _capability_rebinding_copy(
    call: ast.Call, is_agent: Callable[[ast.expr], bool | None] | None = None
) -> str | None:
    """``x.clone(tools=...)`` or ``replace(x, tools=...)``: a copy with its own capabilities.

    A copy that passes none of them keeps the original's tools, which the
    original's own rows already compare, so it is not a limit (#876 review).
    ``is_agent`` answers for the receiver: a value proven not to be an agent
    (``Settings()``) is never a copy, and one that passes only ``**`` is a copy
    only when the receiver is proven to be one.
    """

    shape = _copy_shape(call)
    if shape is None:
        return None
    receiver, kind, explicit = shape
    verdict = is_agent(receiver) if is_agent is not None else None
    if verdict is False or (not explicit and verdict is not True):
        return None
    return kind


def _capability_changes(
    tree: ast.Module,
    capabilities: frozenset[str] = _CAPABILITY_KEYWORDS,
    *,
    scopes: ScopeIndex | None = None,
    callees: ModuleCallees | None = None,
    rewires: dict[tuple[int, int], Callable[[], bool]] | None = None,
) -> list[tuple[ast.expr, ast.AST, bool, bool]]:
    """``(receiver, site, strict)`` wherever a module may change an object's capability list.

    ``strict`` False — a change in view, whatever the receiver: assigning,
    extending or deleting ``x.tools`` (or its items), a list method on it,
    ``setattr`` / ``delattr`` by name, and a handle on it (``t = x.tools``,
    ``payload["tools"] = x.tools``, ``t = getattr(x, "tools")``) changed
    through it later in its scope.

    ``strict`` True — the list escapes where a change could not be seen: handed
    to a call that is not read-only, put in a container or an attribute,
    returned, unpacked, bound by ``for``/``with``/walrus, or a handle used
    that way. The caller counts it only when the receiver is an agent it can
    name: ``helper(agent.tools)`` on an agent the file builds may change it,
    while ``payload["tools"] = request.tools`` on a request object is a read of
    another library's list (#876 review).
    """

    scopes = scopes or ScopeIndex(tree)
    callees = callees or ModuleCallees(tree, scopes, _module_bindings(tree)[0])
    # A reader is proven by its binding in the module the call is written in.
    reads_at = _bindings_at(callees.scopes, callees.bindings)

    def left_alone(target: tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, ModuleCallees]) -> bool:
        function, name, inner = target
        return _parameter_left_alone(function, name, _bindings_at(inner.scopes, inner.bindings))

    def parameter_of(
        call: ast.Call, position: int | None, keyword: str | None, within: ModuleCallees
    ) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, ModuleCallees] | None:
        """The parameter of the function a call argument binds — one the module
        defines or imports (#876 review) — and the callees of that function's
        own module."""

        target = within(call)
        if target is None:
            return None
        function, inner, skip = target
        positional = [*function.args.posonlyargs, *function.args.args][skip:]
        if position is not None:
            if position >= len(positional):
                return None
            return function, positional[position].arg, inner
        names = {arg.arg for arg in [*positional, *function.args.kwonlyargs]}
        return (function, str(keyword), inner) if keyword in names else None

    def parameter_changed(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        name: str,
        within: ModuleCallees,
        depth: int = 0,
    ) -> bool:
        """Whether a function visibly changes a list it is given: a list method,
        ``+=`` or an item store on the parameter, or handing it to a function
        that does — resolved in the function's own module (#876 review)."""

        if depth > 3:
            return True
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LIST_MUTATORS
                and _handle_spelling(node.func.value) == name
            ):
                return True
            if isinstance(node, ast.AugAssign) and _handle_spelling(node.target) == name:
                return True
            if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
                stores = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
                if any(
                    isinstance(item, ast.Subscript) and _handle_spelling(item.value) == name
                    for item in stores
                ):
                    return True
            if isinstance(node, ast.Call) and changed_by_call(node, name, within, depth + 1):
                return True
        return False

    def changed_by_call(
        call: ast.Call, spelling: str, within: ModuleCallees | None = None, depth: int = 0
    ) -> bool:
        """Whether ``call`` hands the list spelled ``spelling`` to a function that changes it."""

        arguments: list[tuple[int | None, str | None, ast.expr]] = [
            (index, None, arg) for index, arg in enumerate(call.args)
        ] + [(None, item.arg, item.value) for item in call.keywords if item.arg]
        for position, keyword, value in arguments:
            if _handle_spelling(value) != spelling:
                continue
            target = parameter_of(call, position, keyword, within or callees)
            if target is not None and parameter_changed(*target, depth=depth):
                return True
        return False

    def call_reads(call: ast.Call, position: int | None, keyword: str | None) -> bool:
        if _leaves_arguments_alone(call, reads_at):
            return True
        # A function the module defines or imports whose every use of that
        # parameter reads it.
        target = parameter_of(call, position, keyword, callees)
        return target is not None and left_alone(target)

    def read_only(node: ast.expr) -> bool:
        return _read_only_use(node, scopes.parents, call_reads)

    def alias_statement(node: ast.expr) -> ast.Assign | ast.AnnAssign | None:
        """The assignment ``node`` is (possibly through ``or`` / ``if``) the value of."""

        current: ast.AST = node
        parent = scopes.parents.get(current)
        while isinstance(parent, ast.BoolOp) or (
            isinstance(parent, ast.IfExp) and parent.test is not current
        ):
            current, parent = parent, scopes.parents.get(parent)
        if isinstance(parent, ast.Assign | ast.AnnAssign) and parent.value is current:
            return parent
        return None

    def handle_scope(statement: ast.stmt, target: ast.expr) -> ast.AST:
        # ``self.tools = agent.tools`` is reachable from every method.
        root = target
        while isinstance(root, ast.Attribute | ast.Subscript):
            root = root.value
        current = scopes.parents.get(statement)
        function: ast.AST | None = None
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                function = function or current
            elif isinstance(current, ast.ClassDef):
                if isinstance(root, ast.Name) and root.id in {"self", "cls"} and root is not target:
                    return current
                break
            current = scopes.parents.get(current)
        return function or tree

    def changed_handle(statement: ast.Assign | ast.AnnAssign, depth: int = 0) -> bool:
        """Whether the list ``statement`` gives a second name is changed through it.

        Only a change in view counts: a list method, ``+=``, or an item store
        through the handle, or a ``global``/``nonlocal`` declaration of it; an
        alias of it (``other = t``, ``other = t or []``) is followed. Handing it
        on — to a call, a return, a container — is an indirect helper effect,
        which the comparison does not establish, exactly as
        ``helper(agent.tools)`` hands the list itself on (#876 review).
        """

        if depth > 4:
            return True
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        for target in targets:
            if isinstance(target, ast.Tuple | ast.List):
                # Unpacking takes the members, never the list.
                continue
            spelling = _handle_spelling(target)
            if spelling is None:
                return True
            scope = handle_scope(statement, target)
            for node in ast.walk(scope):
                if isinstance(node, ast.Global | ast.Nonlocal) and spelling in node.names:
                    return True
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _LIST_MUTATORS
                    and _handle_spelling(node.func.value) == spelling
                ):
                    return True
                if isinstance(node, ast.AugAssign) and _handle_spelling(node.target) == spelling:
                    return True
                if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
                    stores = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
                    if any(
                        isinstance(item, ast.Subscript) and _handle_spelling(item.value) == spelling
                        for item in stores
                    ):
                        return True
                if isinstance(node, ast.Call) and changed_by_call(node, spelling):
                    # ``add_image(handle)`` with ``add_image`` appending.
                    return True
                if (
                    isinstance(node, ast.Assign | ast.AnnAssign)
                    and node is not statement
                    and node.value is not None
                    and any(_handle_spelling(item) == spelling for item in _aliased(node.value))
                    and changed_handle(node, depth + 1)
                ):
                    return True
        return False

    def unresolved_call(node: ast.expr) -> bool:
        """Whether ``node`` is handed to a call that is not read to leave it
        alone and is not another library's: a method of an inherited class, a
        dispatch table, a ``partial``, a method of an unknown object (#876
        review). ``validate(request.tools)`` or ``client.create(tools=...)``
        with ``client = OpenAI()`` hands it to code outside the scope, which
        this reader never reads — the same boundary as any other library."""

        parent = scopes.parents.get(node)
        keyword: str | None = None
        if isinstance(parent, ast.keyword):
            keyword = parent.arg
            parent = scopes.parents.get(parent)
            if keyword in capabilities and isinstance(parent, ast.Call) and callees.library_read(parent, node):
                # ``client.create(tools=request.tools)`` with ``client =
                # OpenAI()``: another library's API payload.
                return False
        if (
            not isinstance(parent, ast.Call)
            or parent.func is node
            or _leaves_arguments_alone(parent, reads_at)
            or callees.library_read(parent, node)
        ):
            return False
        position = next((index for index, arg in enumerate(parent.args) if arg is node), None)
        target = parameter_of(parent, position, keyword, callees)
        # Unresolved, or resolved to a function that hands it on again out of
        # view (``super().add(lst)``): not established either way.
        return target is None or not left_alone(target)

    def handle_escapes(statement: ast.Assign | ast.AnnAssign, depth: int = 0) -> tuple[bool, bool]:
        """Whether a handle on the list is kept anywhere but a plain name, or used
        other than to read it, and whether that use hands it to a call nothing
        resolves; aliases are followed."""

        if depth > 4:
            return True, False
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                return True, False
            scope = handle_scope(statement, target)
            for node in ast.walk(scope):
                if (
                    not isinstance(node, ast.Name)
                    or node.id != target.id
                    or node is target
                    or not isinstance(node.ctx, ast.Load)
                    or read_only(node)
                ):
                    continue
                alias = alias_statement(node)
                if alias is None:
                    return True, unresolved_call(node)
                if alias is statement:
                    return True, False
                escapes, via = handle_escapes(alias, depth + 1)
                if escapes:
                    return True, via
        return False, False

    def additive_handle_ops(statement: ast.Assign | ast.AnnAssign) -> list[ast.AST] | None:
        """The ``append``/``extend``/``insert``/``+=`` made through a handle,
        when those are all that uses it other than to read it; else None."""

        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            return None
        spelling = targets[0].id
        ops: list[ast.AST] = []
        for node in ast.walk(handle_scope(statement, targets[0])):
            if not (isinstance(node, ast.Name) and node.id == spelling) or node is targets[0]:
                continue
            parent = scopes.parents.get(node)
            grand = scopes.parents.get(parent) if parent is not None else None
            if (
                isinstance(parent, ast.Attribute)
                and parent.attr in {"append", "extend", "insert"}
                and isinstance(grand, ast.Call)
                and grand.func is parent
            ):
                ops.append(grand)
            elif isinstance(parent, ast.AugAssign) and parent.target is node and isinstance(parent.op, ast.Add):
                ops.append(parent)
            elif not (isinstance(node.ctx, ast.Load) and read_only(node)):
                return None
        return ops or None

    def reflective(call: ast.Call, names: frozenset[str]) -> bool:
        return (
            isinstance(call.func, ast.Name)
            and call.func.id in names
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and call.args[1].value in capabilities
        )

    #: ``import svc.app.tools``: ``svc.app.tools`` is that module, not a list.
    modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.asname is None
    }

    def access(node: ast.AST) -> ast.expr | None:
        """The receiver of ``x.tools`` or ``getattr(x, "tools")``."""

        if isinstance(node, ast.Attribute) and node.attr in capabilities:
            spelling = _handle_spelling(node)
            if spelling is not None and any(
                name == spelling or name.startswith(spelling + ".") for name in modules
            ):
                return None
            return node.value
        if isinstance(node, ast.Call) and reflective(node, frozenset({"getattr"})):
            return node.args[0]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"__getattribute__", "__getattr__"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in capabilities
        ):
            # ``agent.__getattribute__("tools")`` is ``agent.tools`` (#876 review).
            return node.func.value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"__getattribute__", "__getattr__"}
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in capabilities
        ):
            # ``object.__getattribute__(agent, "tools")``: the unbound form.
            return node.args[0]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Call)
            and (reference_spelling(node.func.func) or "").rsplit(".", 1)[-1] == "attrgetter"
            and len(node.func.args) == 1
            and isinstance(node.func.args[0], ast.Constant)
            and node.func.args[0].value in capabilities
            and len(node.args) == 1
        ):
            # ``operator.attrgetter("tools")(agent)``.
            return node.args[0]
        return None

    def namespace_of(node: ast.AST) -> ast.expr | None:
        """``x`` of ``vars(x)`` or ``x.__dict__``: its attributes by name."""

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "vars"
            and len(node.args) == 1
        ):
            return node.args[0]
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            return node.value
        return None

    def names_other(key: ast.AST | None) -> bool:
        """Whether a spelled-out attribute name is not a capability."""

        return isinstance(key, ast.Constant) and key.value not in capabilities

    def namespace_read(node: ast.AST) -> bool:
        """Whether ``vars(x)`` / ``x.__dict__`` is only read, or changed where
        its own branch records it: a constant non-capability key read, a
        read-only use (``.items()``, iteration, ``json.dumps``), or the store and
        method forms handled above."""

        parent = scopes.parents.get(node)
        if isinstance(parent, ast.Subscript) and parent.value is node:
            return not isinstance(parent.ctx, ast.Load) or names_other(parent.slice)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            call = scopes.parents.get(parent)
            if parent.attr in {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}:
                return True
            if parent.attr == "get" and isinstance(call, ast.Call) and call.args:
                return names_other(call.args[0])
            if parent.attr in {"items", "values"}:
                # ``for name, value in vars(agent).items(): value.clear()``:
                # the attributes themselves, the lists among them (#876 review).
                return False
        return isinstance(node, ast.expr) and read_only(node)

    def mutated_in_place(node: ast.AST) -> bool:
        """``x.tools`` as the receiver of ``x.tools.append(...)``: that call is the
        change, recorded with what it does."""

        method = scopes.parents.get(node)
        call = scopes.parents.get(method) if method is not None else None
        return (
            isinstance(method, ast.Attribute)
            and method.attr in _LIST_MUTATORS
            and isinstance(call, ast.Call)
            and call.func is method
        )

    rewired: dict[tuple[int, str], bool] = {}
    in_progress: set[tuple[int, str]] = set()
    #: Whether a computation read an answer still in progress (a cycle): its
    #: own answer is then not final, and not kept (#876 review).
    provisional: list[bool] = []

    def rewire_target(
        call: ast.Call, position: int | None, keyword: str | None, within: ModuleCallees
    ) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, ModuleCallees] | None:
        """``parameter_of``, and also a method reached through its class
        (``Patcher.apply(...)``, a static or class method) or ``self``."""

        target = parameter_of(call, position, keyword, within)
        if target is not None or not isinstance(call.func, ast.Attribute):
            return target
        func = call.func
        found: tuple[ast.ClassDef, ModuleCallees] | None = None
        skip = 0
        if isinstance(func.value, ast.Name) and func.value.id in {"self", "cls"}:
            current = within.scopes.parents.get(call)
            while current is not None and not isinstance(current, ast.ClassDef):
                current = within.scopes.parents.get(current)
            if isinstance(current, ast.ClassDef):
                found, skip = (current, within), 1
        else:
            found = within._class(func.value, call)
        if found is None:
            return None
        cls, inner = found
        method = next(
            (
                item
                for item in cls.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == func.attr
            ),
            None,
        )
        if method is None:
            return None
        decorators = {dotted_name(decorator) for decorator in method.decorator_list}
        if "staticmethod" in decorators:
            skip = 0
        elif "classmethod" in decorators:
            skip = 1
        positional = [*method.args.posonlyargs, *method.args.args][skip:]
        if position is not None:
            return (method, positional[position].arg, inner) if position < len(positional) else None
        names = {arg.arg for arg in [*positional, *method.args.kwonlyargs]}
        return (method, str(keyword), inner) if keyword in names else None

    def parameter_rewired(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        name: str,
        within: ModuleCallees,
        depth: int = 0,
    ) -> bool:
        """Whether a function rewrites the attributes of what its parameter
        ``name`` holds — or of a member of it, through a loop or a plain alias
        — by a name computed at run time or through its namespace
        (``setattr(agent, key, value)`` over overrides, ``vars(agent)[k] = v``,
        ``agent.__setattr__``), or sets one of its capabilities by name, or hands
        it to a function that does — resolved in the function's own module
        (#876 review)."""

        key = (id(function), name)
        if key in rewired:
            return rewired[key]
        if key in in_progress:
            if provisional:
                provisional[-1] = True
            return False
        if depth > 8:
            return True
        # ``target = agent``, ``for agent in agents``: the same objects.
        aliases = {name}
        grown = True
        while grown:
            grown = False
            for node in ast.walk(function):
                bound: list[ast.expr] = []
                if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension) and isinstance(node.iter, ast.Name) and node.iter.id in aliases:
                    bound = [node.target]
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                    bound = list(node.targets)
                for item in bound:
                    if isinstance(item, ast.Name) and item.id not in aliases:
                        aliases.add(item.id)
                        grown = True

        def spells(node: ast.AST | None) -> bool:
            return isinstance(node, ast.Name) and node.id in aliases

        def iterated_changed(call: ast.Call) -> bool:
            """``for key, value in vars(agent).items(): value.clear()``: an
            attribute changed in place in the loop. Reading or copying the
            attributes (``repr(value)``, ``setattr(other, key, value)``) is not.
            Read in the function's own module, whichever module calls it."""

            parents = within.scopes.parents
            loop = parents.get(call)
            if not (isinstance(loop, ast.For | ast.AsyncFor | ast.comprehension) and loop.iter is call):
                return not _read_only_use(call, parents, lambda *_: False)
            target = loop.target
            if isinstance(call.func, ast.Attribute) and call.func.attr == "items":
                if not (isinstance(target, ast.Tuple | ast.List) and len(target.elts) == 2):
                    return True
                target = target.elts[1]
            if not isinstance(target, ast.Name):
                return True
            body: list[ast.AST] = (
                [*loop.body, *loop.orelse] if isinstance(loop, ast.For | ast.AsyncFor) else [parents.get(loop) or loop]
            )
            for statement in body:
                for inner in ast.walk(statement):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in _LIST_MUTATORS
                        and isinstance(inner.func.value, ast.Name)
                        and inner.func.value.id == target.id
                    ):
                        return True
                    if isinstance(inner, ast.AugAssign) and isinstance(inner.target, ast.Name) and inner.target.id == target.id:
                        return True
                    if (
                        isinstance(inner, ast.Subscript)
                        and not isinstance(inner.ctx, ast.Load)
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id == target.id
                    ):
                        return True
            return False

        in_progress.add(key)
        provisional.append(False)
        found = False
        try:
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    func = node.func
                    first = node.args[0] if node.args else None
                    second = node.args[1] if len(node.args) > 1 else None
                    if isinstance(func, ast.Name) and func.id in {"setattr", "delattr"} and spells(first) and not names_other(second):
                        found = True
                    elif isinstance(func, ast.Attribute) and func.attr in {"__setattr__", "__delattr__"} and (
                        spells(func.value) or spells(first)
                    ):
                        found = True
                    elif (
                        isinstance(func, ast.Attribute)
                        and func.attr in {"update", "setdefault", "pop", "popitem", "clear"}
                        and spells(namespace_of(func.value))
                    ):
                        found = True
                    elif (
                        isinstance(func, ast.Attribute)
                        and func.attr in {"items", "values"}
                        and spells(namespace_of(func.value))
                        and iterated_changed(node)
                    ):
                        found = True
                    else:
                        arguments = [(index, None, arg) for index, arg in enumerate(node.args)] + [
                            (None, item.arg, item.value) for item in node.keywords if item.arg
                        ]
                        for position, keyword, value in arguments:
                            if spells(value):
                                target = rewire_target(node, position, keyword, within)
                                if target is not None and parameter_rewired(*target, depth=depth + 1):
                                    found = True
                                    break
                elif isinstance(node, ast.Assign | ast.AugAssign | ast.Delete):
                    targets = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
                    found = any(
                        isinstance(item, ast.Subscript)
                        and spells(namespace_of(item.value))
                        and not names_other(item.slice)
                        for item in targets
                    )
                if found:
                    break
        finally:
            in_progress.discard(key)
            tainted = provisional.pop()
        if tainted and not found:
            if provisional:
                provisional[-1] = True
        else:
            rewired[key] = found
        return found

    changes: list[tuple[ast.expr, ast.AST, bool, bool]] = []
    #: ``(argument, call, check)``: a call that may hand an agent to a function
    #: rewriting it; a change only if ``check()`` — resolved last, and only
    #: for an argument the caller counts (#876 review).
    candidates: list[tuple[ast.expr, ast.Call, Callable[[], bool]]] = []
    #: Changes by a name computed at run time or through a namespace: on a
    #: parameter, followed to the module's calls (a list handed on is not —
    #: another library's read of it is the reader's boundary).
    dynamic: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.Delete):
            targets = node.targets if isinstance(node, ast.Assign | ast.Delete) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript):
                    owner = namespace_of(target.value)
                    if owner is not None and not names_other(target.slice):
                        # ``vars(agent)["tools"] = ...``, ``agent.__dict__[k] = ...``:
                        # the list reached by name (#876 review); by a computed
                        # name, as ``setattr`` is.
                        computed = not isinstance(target.slice, ast.Constant)
                        changes.append((owner, node, computed, False))
                        dynamic.add(id(node))
                        continue
                    target = target.value
                if isinstance(target, ast.Attribute | ast.Call) and (receiver := access(target)) is not None:
                    changes.append((receiver, node, False, False))
            value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
            for item in _aliased(value) if value is not None else []:
                receiver = access(item)
                if receiver is None:
                    continue
                assert isinstance(node, ast.Assign | ast.AnnAssign)
                if changed_handle(node):
                    # Through ``t = agent.tools``, only ``t.append(...)``: those
                    # calls are the changes, and none can undo a binding.
                    ops = additive_handle_ops(node)
                    for site in ops or [node]:
                        changes.append((receiver, site, False, False))
                else:
                    escapes, via_unresolved = handle_escapes(node)
                    if escapes:
                        changes.append((receiver, item, True, via_unresolved))
        elif isinstance(node, ast.Call):
            func = node.func
            # ``apply(plant_agent, overrides)`` with ``apply`` — here, imported,
            # a method, or a chain — rewriting its parameter's attributes: a
            # change on the argument, counted for an agent the file can name,
            # imports, or gets from another file's builder (#876 review).
            arguments = [(index, None, arg) for index, arg in enumerate(node.args)] + [
                (None, item.arg, item.value) for item in node.keywords if item.arg
            ]
            for position, keyword, value in arguments:
                # ``apply_all([plant_agent], overrides)``: each member.
                receivers = list(value.elts) if isinstance(value, ast.List | ast.Tuple | ast.Set) else [value]
                for receiver in receivers:
                    if isinstance(receiver, ast.Starred):
                        receiver = receiver.value
                    if not isinstance(receiver, ast.Name | ast.Attribute | ast.Subscript | ast.Call):
                        continue

                    def check(node: ast.Call = node, position: int | None = position, keyword: str | None = keyword) -> bool:
                        target = rewire_target(node, position, keyword, callees)
                        return target is not None and parameter_rewired(*target)

                    candidates.append((receiver, node, check))
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _LIST_MUTATORS
                and (receiver := access(func.value)) is not None
            ):
                # ``agent.tools.append(x)``, ``getattr(agent, "tools").append(x)``.
                changes.append((receiver, node, False, False))
            elif reflective(node, frozenset({"setattr", "delattr"})):
                changes.append((node.args[0], node, False, False))
            elif (
                isinstance(func, ast.Name)
                and func.id in {"setattr", "delattr", "getattr"}
                and len(node.args) >= 2
                and not isinstance(node.args[1], ast.Constant)
                and (func.id != "getattr" or not read_only(node))
            ):
                # ``setattr(agent, key, value)`` over overrides, or
                # ``getattr(agent, cap).clear()``: any capability, by a name
                # computed at run time (#876 review). Counted, as a list handed
                # on is, for an agent the file can name or one imported from
                # the scope: ``setattr(record, field, value)`` on a database
                # row and ``return getattr(module, name)`` are everywhere.
                in_view = func.id == "getattr" and mutated_in_place(node)
                changes.append(
                    (node.args[0], node, not in_view, not in_view and unresolved_call(node))
                )
                dynamic.add(id(node))
            elif (
                isinstance(func, ast.Attribute)
                and func.attr in {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}
                and (owner := namespace_of(func.value)) is not None
            ):
                # ``agent.__dict__.update(overrides)``: names from the data.
                changes.append((owner, node, True, False))
                dynamic.add(id(node))
            elif (
                reflective(node, frozenset({"getattr"}))
                and not read_only(node)
                and alias_statement(node) is None
            ):
                # ``helper(getattr(agent, "tools"))``: out of view, as below.
                changes.append((node.args[0], node, True, unresolved_call(node)))
        if (owner := namespace_of(node)) is not None and not namespace_read(node):
            # ``d = agent.__dict__``, ``operator.setitem(vars(agent), ...)``:
            # its attributes, the lists among them, by any name (#876 review).
            # Counted for an agent the file names or imports: handing
            # ``vars(args)`` to ``.update()`` copies it.
            changes.append((owner, node, True, False))
            dynamic.add(id(node))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {
            "__setattr__",
            "__delattr__",
        }:
            # ``object.__setattr__(agent, "tools", ...)``, ``agent.__setattr__(...)``.
            direct = bool(node.args) and not isinstance(node.args[0], ast.Constant)
            owner = node.args[0] if direct else node.func.value
            key = node.args[1] if direct and len(node.args) > 1 else node.args[0] if node.args and not direct else None
            if not names_other(key):
                changes.append((owner, node, not isinstance(key, ast.Constant), False))
                dynamic.add(id(node))
        if (
            isinstance(node, ast.Name)
            and node.id in {"setattr", "delattr"}
            and isinstance(node.ctx, ast.Load)
            and isinstance(scopes.parents.get(node), ast.Call)
            and scopes.parents[node].func is not node  # type: ignore[attr-defined]
        ):
            # ``functools.partial(setattr, agent)``: every other argument may be
            # the object it is applied to.
            call = scopes.parents[node]
            for arg in [*call.args, *(item.value for item in call.keywords)]:  # type: ignore[attr-defined]
                if arg is not node and isinstance(arg, ast.Name | ast.Attribute | ast.Subscript):
                    changes.append((arg, call, True, False))
                    dynamic.add(id(call))
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and access(node) is not None
            and not read_only(node)
            and alias_statement(node) is None
            and not mutated_in_place(node)
        ):
            # ``helper(agent.tools)``, ``d = {"t": agent.tools}``, ``return
            # agent.tools``, ``(t := agent.tools)``: out of view from here —
            # unless the helper is this module's and changes it in view, which
            # is a change whatever the receiver (#876 review).
            parent = scopes.parents.get(node)
            call = scopes.parents.get(parent) if isinstance(parent, ast.keyword) else parent
            spelling = _handle_spelling(node)
            changed = (
                isinstance(call, ast.Call)
                and spelling is not None
                and changed_by_call(call, spelling)
            )
            changes.append((node.value, node, not changed, not changed and unresolved_call(node)))
    followed = _through_call_sites(tree, scopes, [change for change in changes if id(change[1]) in dynamic])
    # A candidate that is also a change on its own (``setattr(agent, key,
    # value)``: its argument and call) is that change, not a candidate.
    real = {(id(receiver), id(site)) for receiver, site, _, _ in changes}
    for receiver, site, check in candidates:
        if (id(receiver), id(site)) in real:
            continue
        if rewires is None:
            if not check():
                continue
        else:
            rewires[(id(receiver), id(site))] = check
        changes.append((receiver, site, True, False))
    return changes + followed


def _through_call_sites(
    tree: ast.Module, scopes: ScopeIndex, changes: list[tuple[ast.expr, ast.AST, bool, bool]]
) -> list[tuple[ast.expr, ast.AST, bool, bool]]:
    """A change by a computed name or through a namespace (``setattr(agent, key,
    value)`` over overrides) on a parameter of a module-level function is one on
    each argument the module's own calls of it pass (``apply(plant_agent,
    ...)``), counted by the same rule: for an agent the file names or imports
    (#876 review)."""

    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    followed: list[tuple[ast.expr, ast.AST, bool, bool]] = []
    if not changes:
        return followed
    calls: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
            calls.setdefault(node.func.id, []).append(node)
    seen: set[tuple[int, str]] = set()
    for receiver, site, strict, _ in changes:
        if not strict or not isinstance(receiver, ast.Name):
            continue
        found = scopes.enclosing_bindings(site, receiver.id)
        if len(found) != 1 or not isinstance(found[0], ast.arg):
            continue
        parameter = found[0]
        arguments = scopes.parents.get(parameter)
        function = scopes.parents.get(arguments) if arguments is not None else None
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef) or functions.get(function.name) is not function:
            continue
        if (id(function), parameter.arg) in seen:
            continue
        seen.add((id(function), parameter.arg))
        positional = [*function.args.posonlyargs, *function.args.args]
        position = next((index for index, item in enumerate(positional) if item is parameter), None)
        for node in calls.get(function.name, []):
            supplied = next((item.value for item in node.keywords if item.arg == parameter.arg), None)
            if supplied is None and position is not None and position < len(node.args):
                supplied = node.args[position]
            if isinstance(supplied, ast.Name | ast.Attribute | ast.Subscript):
                followed.append((supplied, node, True, False))
    return followed


class Callee(NamedTuple):
    """A call's function, the callees of its own module, and how many leading
    positional parameters the call does not supply (a bound method's ``self``)."""

    function: ast.FunctionDef | ast.AsyncFunctionDef
    callees: ModuleCallees
    skip: int = 0


#: Calls that hand on the members of what they are given.
_ITERATION_WRAPPERS = frozenset(
    {"list", "tuple", "set", "frozenset", "sorted", "reversed", "iter", "enumerate", "zip", "filter"}
)


class ModuleCallees:
    """The function a call in one module names — defined there or imported
    (#864), a method of a class it defines or imports, or a helper a function
    imports for itself — with the callees of that function's own module, so a
    helper's inner calls are read where the helper is written (#876 review).

    One per module, shared through ``registry``; resolutions are cached, so a
    module with a thousand calls reads each spelling once.
    """

    def __init__(
        self,
        tree: ast.Module,
        scopes: ScopeIndex,
        bindings: dict[str, list[Any]],
        *,
        resolver: ImportResolver | None = None,
        module: PythonModule | None = None,
        registry: dict[Path, ModuleCallees] | None = None,
    ) -> None:
        self.scopes = scopes
        self.bindings = bindings
        self.resolver = resolver
        self.module = module
        self.registry = registry if registry is not None else {}
        self._cache: dict[str, Callee | None] = {}

    def __call__(self, call: ast.Call) -> Callee | None:
        func = call.func
        if isinstance(func, ast.Attribute):
            method = self._method(func, call)
            if method is not None:
                return method
        spelling = reference_spelling(func)
        if spelling is None:
            return None
        head = spelling.split(".", 1)[0]
        found = self.scopes.enclosing_bindings(call, head)
        if found:
            # ``from app.helpers import add_image`` inside the function that
            # calls it, the usual way to break an import cycle.
            return self._local_import(found, spelling)
        if spelling not in self._cache:
            self._cache[spelling] = self._resolve(spelling)
        return self._cache[spelling]

    def _resolve(self, spelling: str) -> Callee | None:
        found = self.bindings.get(spelling, []) if "." not in spelling else []
        if (
            len(found) == 1
            and found[0].top_level
            and isinstance(found[0].node, ast.FunctionDef | ast.AsyncFunctionDef)
        ):
            return Callee(found[0].node, self)
        if self.resolver is None or self.module is None:
            return None
        resolution = self.resolver.resolve(self.module, spelling)
        if not resolution.resolved or resolution.module is None:
            return None
        assert resolution.definition is not None
        return Callee(resolution.definition, self.for_module(resolution.module))

    def _local_import(self, found: list[ast.AST], spelling: str) -> Callee | None:
        if len(found) != 1 or not isinstance(found[0], ast.alias):
            return None
        if self.resolver is None or self.module is None:
            return None
        statement = self.scopes.statement_of(found[0])
        if not isinstance(statement, ast.Import | ast.ImportFrom):
            return None
        resolution = self.resolver.resolve_local_import(self.module, statement, found[0], spelling)
        if not resolution.resolved or resolution.module is None:
            return None
        assert resolution.definition is not None
        return Callee(resolution.definition, self.for_module(resolution.module))

    def _method(self, func: ast.Attribute, call: ast.Call) -> Callee | None:
        """``H().add(...)``, or ``obj.add(...)`` with ``obj = H()``: the method of a
        class the module defines or imports, ``self`` not supplied."""

        if isinstance(func.value, ast.Name) and func.value.id == "self":
            # A subclass may override it: ``self.register(x)`` in a base class
            # runs whichever ``register`` the instance has (#876 review).
            return None
        found = self._receiver_class(func, call)
        return self._method_of(*found, name=func.attr) if found is not None else None

    def _receiver_class(
        self, func: ast.Attribute, call: ast.Call
    ) -> tuple[ast.ClassDef, ModuleCallees] | None:
        """The class a method call's receiver is an instance of, when the module
        defines or imports it: ``H()``, or ``obj`` with ``obj = H()``."""

        owner = func.value
        maker: ast.expr | None = None
        if isinstance(owner, ast.Call):
            maker = owner.func
        elif isinstance(owner, ast.Name):
            value = self._value_of(owner.id, call)
            if isinstance(value, ast.Call):
                maker = value.func
        return self._class(maker, call) if maker is not None else None

    @staticmethod
    def _method_of(cls: ast.ClassDef, callees: ModuleCallees, name: str) -> Callee | None:
        for item in cls.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name:
                static = any(dotted_name(decorator) == "staticmethod" for decorator in item.decorator_list)
                return Callee(item, callees, 0 if static else 1)
        return None

    def library_read(self, call: ast.Call, handed: ast.AST | None = None, depth: int = 0) -> bool:
        """Whether handing a list to ``call`` is another library's read.

        The function is another library's — reached from a name imported
        from a package the repository holds nowhere (``validate``), or a name
        bound once to an instance of one (``client = OpenAI()``) — and every
        other argument of every call on the way is inert data (#876 review).
        An argument this reader does not know to be data may be the
        application's own callable or object, which the library could call
        with the list; so anything unrecognised is not a read."""

        if depth > 3 or self.resolver is None or self.module is None:
            return False
        calls: list[ast.Call] = []
        root: ast.AST = call
        while isinstance(root, ast.Attribute | ast.Subscript | ast.Call):
            if isinstance(root, ast.Call):
                calls.append(root)
                root = root.func
            elif isinstance(root, ast.Subscript):
                if not self._inert(root.slice, call):
                    return False
                root = root.value
            else:
                root = root.value
        # ``create(model=req.model, tools=req.tools)``: what else it is handed
        # from the object the list came from is that object's data.
        roots = frozenset({owner} if handed is not None and (owner := _root_name(handed)) else ())
        for item in calls:
            for arg in [*item.args, *(keyword.value for keyword in item.keywords)]:
                if arg is not handed and not self._inert(arg, call, roots=roots):
                    return False
        if not isinstance(root, ast.Name):
            return False
        binding = self._binding(root.id, call)
        if binding is None:
            return False
        node, statement = binding
        if isinstance(node, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
            return self._origin(node, statement, root.id, call)[0] == "library"
        if (
            isinstance(statement, ast.Assign | ast.AnnAssign)
            and _assignment_target(statement) == root.id
            and isinstance(statement.value, ast.Call)
        ):
            # ``client = OpenAI()``: an instance of another library's class.
            # Never ``self.client`` or a parameter, whatever it is annotated:
            # a subclass or a caller may put the application's own object
            # there.
            return self.library_read(statement.value, None, depth + 1)
        return False

    def _binding(self, name: str, site: ast.AST) -> tuple[ast.AST, ast.AST | None] | None:
        """The one binding of ``name`` seen from ``site`` and its statement."""

        found = self.scopes.enclosing_bindings(site, name)
        if found:
            return (found[0], self.scopes.statement_of(found[0])) if len(found) == 1 else None
        bindings = self.bindings.get(name, [])
        if len(bindings) != 1 or not bindings[0].top_level:
            return None
        return bindings[0].node, bindings[0].statement

    def _origin(
        self, node: ast.alias, statement: ast.Import | ast.ImportFrom, spelling: str, site: ast.AST
    ) -> tuple[str, Resolution]:
        """Where an imported name's code lives: ``scope``, the ``repository``
        outside it, the ``stdlib``, or another ``library``."""

        assert self.resolver is not None and self.module is not None
        if self.scopes.enclosing_bindings(site, spelling.split(".", 1)[0]):
            resolution = self.resolver.resolve_local_import(self.module, statement, node, spelling)
        else:
            resolution = self.resolver.resolve(self.module, spelling)
        if resolution.reason not in _OUTSIDE_THE_SCOPE:
            return "scope", resolution
        if self.resolver.another_library(statement, node):
            return "library", resolution
        top = (statement.module or "" if isinstance(statement, ast.ImportFrom) else node.name).split(".", 1)[0]
        stdlib = not getattr(statement, "level", 0) and top in sys.stdlib_module_names
        return ("stdlib" if stdlib else "repository"), resolution

    def _inert(
        self,
        expr: ast.AST,
        site: ast.AST,
        depth: int = 0,
        local: frozenset[str] = frozenset(),
        roots: frozenset[str] = frozenset(),
    ) -> bool:
        """Whether an argument is data a library can only read: a literal, a
        container or comprehension of data, a capability list, a read of a
        parameter's attribute (``req.messages``), a name bound once to data, a
        parameter annotated with a builtin data type, another library's or a
        builtin's value, or a builtin or library call on data (#876 review)."""

        if depth > 6:
            return False
        inert = lambda item, names=local: self._inert(item, site, depth + 1, names, roots)  # noqa: E731
        if isinstance(expr, ast.Constant):
            return True
        if isinstance(expr, ast.JoinedStr):
            return all(inert(item) for item in expr.values)
        if isinstance(expr, ast.FormattedValue):
            return inert(expr.value)
        if isinstance(expr, ast.List | ast.Tuple | ast.Set):
            return all(inert(item) for item in expr.elts)
        if isinstance(expr, ast.Dict):
            return all(inert(item) for item in [*(key for key in expr.keys if key is not None), *expr.values])
        if isinstance(expr, ast.Starred):
            return inert(expr.value)
        if isinstance(expr, ast.BinOp):
            return inert(expr.left) and inert(expr.right)
        if isinstance(expr, ast.UnaryOp):
            return inert(expr.operand)
        if isinstance(expr, ast.BoolOp):
            return all(inert(item) for item in expr.values)
        if isinstance(expr, ast.Compare):
            return inert(expr.left) and all(inert(item) for item in expr.comparators)
        if isinstance(expr, ast.IfExp):
            return inert(expr.test) and inert(expr.body) and inert(expr.orelse)
        if isinstance(expr, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            names = set(local)
            for generator in expr.generators:
                if not inert(generator.iter, frozenset(names)):
                    return False
                names |= {node.id for node in ast.walk(generator.target) if isinstance(node, ast.Name)}
                if not all(inert(item, frozenset(names)) for item in generator.ifs):
                    return False
            parts = [expr.key, expr.value] if isinstance(expr, ast.DictComp) else [expr.elt]
            return all(inert(item, frozenset(names)) for item in parts)
        if isinstance(expr, ast.Attribute) and expr.attr in _CENSUS_CAPABILITIES:
            # The list handed on, or another capability list read.
            return True
        if isinstance(expr, ast.Attribute | ast.Subscript):
            if isinstance(expr, ast.Subscript) and not inert(expr.slice):
                return False
            root: ast.AST = expr.value
            while isinstance(root, ast.Attribute | ast.Subscript):
                if isinstance(root, ast.Subscript) and not inert(root.slice):
                    return False
                root = root.value
            if not isinstance(root, ast.Name) or root.id in {"self", "cls"}:
                return False
            binding = self._binding(root.id, site) if root.id not in local else None
            if root.id in local:
                return True
            if binding is not None and isinstance(binding[0], ast.arg):
                # ``req.messages`` beside ``req.tools``: the request's own
                # data. Another parameter's attribute (``svc.attach``) may be
                # a bound method of the application's (#876 review).
                return root.id in roots or _data_annotation(binding[0].annotation)
            return inert(root)
        if isinstance(expr, ast.Name):
            if expr.id in local:
                return True
            binding = self._binding(expr.id, site)
            if binding is None:
                # A builtin (``key=str``), unless the name is bound where
                # this reader cannot tell which binding holds.
                return expr.id in _BUILTIN_NAMES and not self._bound_anywhere(expr.id, site)
            node, statement = binding
            if isinstance(node, ast.arg):
                return _data_annotation(node.annotation)
            if isinstance(node, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
                if self.resolver is None or self.module is None:
                    return False
                origin, resolution = self._origin(node, statement, expr.id, site)
                if origin in {"library", "stdlib"}:
                    # ``os.environ``, never ``sys.modules`` or ``importlib``:
                    # those reach the application's own modules.
                    return _import_top(node, statement) not in _DYNAMIC_MODULES
                return resolution.value is not None and _literal_data(resolution.value)
            if isinstance(statement, ast.Assign | ast.AnnAssign) and statement.value is not None:
                if _assignment_target(statement) != expr.id:
                    return False
                return inert(statement.value)
            return False
        if isinstance(expr, ast.Call):
            if not all(inert(item) for item in [*expr.args, *(keyword.value for keyword in expr.keywords)]):
                return False
            func = expr.func
            if isinstance(func, ast.Name) and func.id in _DATA_BUILTINS and not self._bound_anywhere(func.id, site):
                return True
            root = func
            while isinstance(root, ast.Attribute):
                root = root.value
            if not isinstance(root, ast.Name):
                return False
            binding = self._binding(root.id, site)
            if binding is None:
                return False
            node, statement = binding
            # ``types.Content(role="user", parts=[...])``: another library's
            # value built from data.
            return (
                isinstance(node, ast.alias)
                and isinstance(statement, ast.Import | ast.ImportFrom)
                and self.resolver is not None
                and self.module is not None
                and self._origin(node, statement, root.id, site)[0] in {"library", "stdlib"}
                # ``importlib.import_module("app.x")`` returns the
                # application's own module.
                and _import_top(node, statement) not in _DYNAMIC_MODULES
            )
        return False

    def _bound_anywhere(self, name: str, site: ast.AST) -> bool:
        return bool(self.scopes.enclosing_bindings(site, name)) or name in self.bindings

    def _value_of(self, name: str, site: ast.AST) -> ast.expr | None:
        found = self.scopes.enclosing_bindings(site, name)
        if found:
            statement = self.scopes.statement_of(found[0]) if len(found) == 1 else None
        else:
            bindings = self.bindings.get(name, [])
            statement = bindings[0].statement if len(bindings) == 1 and bindings[0].top_level else None
        if isinstance(statement, ast.Assign | ast.AnnAssign) and _assignment_target(statement) == name:
            return statement.value
        return None

    def _class(self, maker: ast.expr, site: ast.AST, depth: int = 0) -> tuple[ast.ClassDef, ModuleCallees] | None:
        if not isinstance(maker, ast.Name) or depth > 3 or self.scopes.enclosing_bindings(site, maker.id):
            return None
        found = self.bindings.get(maker.id, [])
        if len(found) != 1 or not found[0].top_level:
            return None
        node, statement = found[0].node, found[0].statement
        if isinstance(node, ast.ClassDef):
            return node, self
        if (
            isinstance(node, ast.alias)
            and isinstance(statement, ast.ImportFrom)
            and self.resolver is not None
            and self.module is not None
        ):
            # Follow ``from app.helpers import H`` to the module that defines it.
            try:
                container = self.resolver._from_base(self.module, statement)
                if container.module_path is None:
                    return None
                defining = self.resolver.module(container.module_path)
            except Exception:  # noqa: BLE001 - any stop means "not followed"
                return None
            other = self.for_module(defining)
            return other._class(ast.Name(id=node.name, ctx=ast.Load()), defining.tree, depth + 1)
        return None

    def for_module(self, module: PythonModule) -> ModuleCallees:
        if module is self.module:
            return self
        known = self.registry.get(module.path)
        if known is None:
            known = self.registry[module.path] = ModuleCallees(
                module.tree,
                ScopeIndex(module.tree),
                module.bindings,
                resolver=self.resolver,
                module=module,
                registry=self.registry,
            )
        return known


def _roots(expr: ast.AST) -> set[str]:
    """The names an expression's value is reached from, through attributes,
    items, ``getattr`` and iteration wrappers (``enumerate(AGENTS)``)."""

    if isinstance(expr, ast.Name):
        return {expr.id}
    if isinstance(expr, ast.Attribute | ast.Subscript | ast.Starred):
        return _roots(expr.value)
    if isinstance(expr, ast.BinOp):
        return _roots(expr.left) | _roots(expr.right)
    if isinstance(expr, ast.Call):
        name = dotted_name(expr.func)
        if name == "getattr" and expr.args:
            return _roots(expr.args[0])
        if name in _ITERATION_WRAPPERS:
            return set().union(*(_roots(arg) for arg in expr.args)) if expr.args else set()
        if isinstance(expr.func, ast.Attribute) and expr.func.attr in {"values", "items", "keys", "copy"}:
            return _roots(expr.func.value)
    return set()


def _through_loops(
    receiver: ast.expr, site: ast.AST, scopes: ScopeIndex, bindings: dict[str, list[Any]]
) -> ast.expr:
    """What a loop variable receiver iterates over: ``for ag in AGENTS:
    ag.tools`` is reached from ``AGENTS`` (#876 review)."""

    for _ in range(4):
        if not isinstance(receiver, ast.Name):
            return receiver
        found = scopes.enclosing_bindings(site, receiver.id)
        statements = (
            [scopes.statement_of(item) for item in found]
            if found
            else [item.statement for item in bindings.get(receiver.id, [])]
        )
        if len(statements) != 1 or not isinstance(statements[0], ast.For | ast.AsyncFor):
            return receiver
        receiver, site = statements[0].iter, statements[0]
    return receiver


#: Resolution outcomes that mean the name is not the scope's own code.
_OUTSIDE_THE_SCOPE = frozenset({"module_not_found", "outside_scope"})
#: Builtin names that are values a library may only read (``key=str``).
_BUILTIN_NAMES = frozenset(
    {"str", "int", "float", "bool", "bytes", "dict", "list", "tuple", "set", "frozenset",
     "len", "sorted", "min", "max", "sum", "abs", "repr", "id", "type", "object"}
)
#: Builtin calls that build data from data.
_DATA_BUILTINS = frozenset(
    {"dict", "list", "tuple", "set", "frozenset", "str", "int", "float", "bool", "bytes",
     "len", "sorted", "min", "max", "sum", "abs", "repr", "round", "range", "zip", "enumerate"}
)
#: Parameter annotations that name builtin scalar data.
_DATA_TYPES = frozenset({"str", "int", "float", "bool", "bytes", "None"})
#: Builtin containers: data only when their items are (``list[str]``).
_CONTAINER_TYPES = frozenset({"list", "dict", "tuple", "set", "frozenset"})
#: Standard-library modules whose values can reach the application's own code:
#: a dynamic import, the module table, a frame, a partial.
_DYNAMIC_MODULES = frozenset(
    {"importlib", "pkgutil", "runpy", "sys", "builtins", "inspect", "operator", "functools", "types", "gc", "ctypes"}
)


def _import_top(node: ast.alias, statement: ast.Import | ast.ImportFrom) -> str:
    dotted = (statement.module or "") if isinstance(statement, ast.ImportFrom) else node.name
    return dotted.split(".", 1)[0]


def _data_annotation(annotation: ast.expr | None) -> bool:
    """``str``, ``list[dict[str, str]]``, ``int | None``, ``Optional[str]``:
    builtin data. A bare ``list`` or ``dict`` says nothing of what it holds
    (#876 review)."""

    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant):
        if annotation.value is None:
            return True
        if isinstance(annotation.value, str):
            try:
                parsed = ast.parse(annotation.value, mode="eval").body
            except (SyntaxError, ValueError, RecursionError):
                return False
            return not isinstance(parsed, ast.Constant) and _data_annotation(parsed)
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id in _DATA_TYPES
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _data_annotation(annotation.left) and _data_annotation(annotation.right)
    if isinstance(annotation, ast.Subscript):
        spelling = reference_spelling(annotation.value) or ""
        inner = annotation.slice
        items = list(inner.elts) if isinstance(inner, ast.Tuple) else [inner]
        if spelling.rsplit(".", 1)[-1] in {"Optional", "Union"} or spelling in _CONTAINER_TYPES:
            return all(
                _data_annotation(item) or (isinstance(item, ast.Constant) and item.value is Ellipsis)
                for item in items
            )
    return False


def _literal_data(value: ast.expr) -> bool:
    """A module constant spelled only with literals: ``MODEL = "gpt-4o"``."""

    return all(
        isinstance(node, ast.Constant | ast.List | ast.Tuple | ast.Set | ast.Dict | ast.JoinedStr
                   | ast.FormattedValue | ast.BinOp | ast.UnaryOp | ast.operator | ast.unaryop
                   | ast.expr_context)
        for node in ast.walk(value)
    )


def _change_effect(site: ast.AST) -> tuple[bool, bool]:
    """``(removes, in_place)`` of a capability change: whether it can remove or
    replace a binding the constructor made, and whether it changes the list
    object itself rather than giving the agent a new one (#876 review)."""

    if isinstance(site, ast.Call):
        func = site.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in {"append", "extend", "insert"}:
            return False, True
        if name in {"setattr", "delattr"}:
            return True, False
        return True, True
    if isinstance(site, ast.AugAssign):
        # ``x.tools += [y]`` extends the list in place; ``*=`` can empty it.
        return not isinstance(site.op, ast.Add), True
    return True, not _reassigns(site)


def _changed_capability(receiver: ast.expr | None, site: ast.AST, scopes: ScopeIndex) -> str:
    """The capability list a change reaches — ``tools``, ``handoffs`` or
    ``mcp_servers`` — or ``*`` when it cannot be told (#876 review)."""

    parent = scopes.parents.get(receiver) if receiver is not None else None
    if isinstance(parent, ast.Attribute) and parent.value is receiver:
        return parent.attr if parent.attr in _CAPABILITY_KEYWORDS else "*"
    if (
        isinstance(parent, ast.Call)
        and len(parent.args) >= 2
        and parent.args[0] is receiver
        and isinstance(parent.args[1], ast.Constant)
        and parent.args[1].value in _CAPABILITY_KEYWORDS
    ):
        # ``getattr(agent, "tools")``, ``setattr(agent, "handoffs", ...)``.
        return str(parent.args[1].value)
    return "*"


def _returns_agent(
    function: ast.FunctionDef | ast.AsyncFunctionDef, sdk_names: _SdkNames, scopes: ScopeIndex
) -> bool:
    """Whether every ``return`` of a function gives back an agent it constructs:
    ``return Agent(...)``, or a name bound once in it to one."""

    returns: list[ast.Return] = []
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPE_NODES):
            continue
        if isinstance(node, ast.Return):
            returns.append(node)
        stack.extend(ast.iter_child_nodes(node))
    for node in returns:
        value = node.value
        if isinstance(value, ast.Name):
            found = scopes.enclosing_bindings(node, value.id)
            statement = scopes.statement_of(found[0]) if len(found) == 1 else None
            value = (
                statement.value
                if isinstance(statement, ast.Assign | ast.AnnAssign)
                and _assignment_target(statement) == value.id
                else None
            )
        if not isinstance(value, ast.Call) or not _denotes_agent(sdk_names, value):
            return False
    return bool(returns)


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute | ast.Subscript | ast.Call):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _handle_spelling(node: ast.AST) -> str | None:
    """``t``, ``payload['tools']``, ``self.tools``: a place a list can be kept."""

    if not isinstance(node, ast.Name | ast.Attribute | ast.Subscript):
        return None
    try:
        return ast.unparse(node)
    except (ValueError, AttributeError, RecursionError):
        return None


def _aliased(value: ast.expr) -> list[ast.expr]:
    """The expressions an assigned value may be, itself: ``t``, ``t or []``, ``t if c else u``."""

    if isinstance(value, ast.BoolOp):
        return [item for operand in value.values for item in _aliased(operand)]
    if isinstance(value, ast.IfExp):
        return [*_aliased(value.body), *_aliased(value.orelse)]
    if isinstance(value, ast.NamedExpr):
        return [value.target, *_aliased(value.value)]
    return [value]


#: Constructors whose result is never an agent.
_NON_AGENT_CONSTRUCTORS = frozenset(
    {
        "dict",
        "list",
        "set",
        "tuple",
        "object",
        "SimpleNamespace",
        "types.SimpleNamespace",
        "defaultdict",
        "collections.defaultdict",
        "OrderedDict",
        "collections.OrderedDict",
    }
)
_NON_AGENT_LITERALS = (
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Set,
    ast.Dict,
    ast.JoinedStr,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


class _AgentValues:
    """What a name holds where it is used, as far as agents go (#876 review)."""

    def __init__(
        self,
        tree: ast.Module,
        scopes: ScopeIndex,
        module_bindings: dict[str, list[Any]],
        sdk_names: _SdkNames,
        subclasses: dict[str, int],
    ) -> None:
        self.scopes = scopes
        self.module_bindings = module_bindings
        self.sdk_names = sdk_names
        self.subclasses = subclasses
        #: ``id(call) -> identity`` of every agent construction the reader read.
        self.constructed: dict[int, str] = {}
        self._class_attributes: dict[int, dict[str, list[ast.expr]]] = {}
        self.class_nodes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        self._appended: dict[str, list[ast.expr]] | None = None
        self.classes = set(self.class_nodes)
        self.functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }

    def value_of(self, name: str, site: ast.AST) -> ast.expr | None:
        """The value ``name`` is bound to once, seen from ``site``; else None."""

        found = self.scopes.enclosing_bindings(site, name)
        if found:
            if len(found) != 1 or not isinstance(found[0], ast.Name):
                return None
            statement = self.scopes.statement_of(found[0])
        else:
            bindings = self.module_bindings.get(name, [])
            if (
                len(bindings) != 1
                or not bindings[0].top_level
                or not isinstance(bindings[0].node, ast.Name)
            ):
                return None
            statement = bindings[0].statement
        if isinstance(statement, ast.Assign | ast.AnnAssign) and _assignment_target(statement) == name:
            return statement.value
        return None

    def is_agent(self, expr: ast.expr, site: ast.AST) -> bool | None:
        """True: an agent; False: provably not one; None: unknown."""

        if isinstance(expr, ast.Name):
            value = self.value_of(expr.id, site)
            if value is None:
                return None
            expr = value
        if isinstance(expr, _NON_AGENT_LITERALS):
            return False
        if isinstance(expr, ast.Call):
            if id(expr) in self.constructed or _denotes_agent(self.sdk_names, expr):
                return True
            if self.returned_agent(expr) is not None:
                return True
            if self._not_an_agent(expr):
                return False
        return None

    def agent_like(
        self,
        receiver: ast.expr,
        site: ast.AST,
        depth: int = 0,
        unknown: list[tuple[ast.expr, ast.AST]] | None = None,
    ) -> set[str] | None:
        """The file's agents a receiver ``owner`` cannot name may be, or None.

        An item of a container that holds one (``AGENTS[0]``), a loop variable
        over such a container, or a class attribute bound to one (#876
        review). The set names the agents it can be, empty when they are not
        named; another library's ``request`` is none of these (None).
        ``unknown`` collects the members that may be agents the file does not
        construct — ``plant_agent`` imported beside its own ``manager`` — which
        the caller limits where they come from (#876 review).
        """

        if depth > 3:
            return None
        if isinstance(receiver, ast.Subscript):
            return self._agents_in(receiver.value, site, depth + 1, unknown)
        if isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name):
            holder = self.value_of(receiver.value.id, site)
            cls = self.class_nodes.get(receiver.value.id)
            if holder is None and cls is not None:
                found = [
                    item.value
                    for item in cls.body
                    if isinstance(item, ast.Assign | ast.AnnAssign)
                    and _assignment_target(item) == receiver.attr
                    and item.value is not None
                    and self.is_agent(item.value, item) is True
                ]
                return {name for item in found if (name := self._identity(item, site))} if found else None
            return None
        if isinstance(receiver, ast.Name):
            loops = [
                self.scopes.statement_of(item)
                for item in self.scopes.enclosing_bindings(site, receiver.id)
                if isinstance(item, ast.Name)
            ]
            if not self.scopes.enclosing_bindings(site, receiver.id):
                # A loop at module level (#876 review).
                loops = [item.statement for item in self.module_bindings.get(receiver.id, [])]
            held: set[str] | None = None
            for loop in loops:
                if isinstance(loop, ast.For | ast.AsyncFor):
                    agents = self._agents_in(loop.iter, loop, depth + 1, unknown)
                    if agents is not None:
                        held = (held or set()) | agents
            return held
        return None

    def _agents_in(
        self,
        container: ast.expr,
        site: ast.AST,
        depth: int,
        unknown: list[tuple[ast.expr, ast.AST]] | None = None,
    ) -> set[str] | None:
        """The agents a container holds, at any depth: its literal items, what is
        put into it (``append``, an item store, ``setdefault``, ``update``), a
        comprehension over one, ``+`` of two, a slice, an iteration wrapper
        (``sorted``, ``enumerate``, ``zip``, ``list(REG.values())``), a loop
        variable over one, or a module function's returned list (#876 review)."""

        if depth > 6:
            return None
        found: set[str] | None = None

        def merge(inner: set[str] | None) -> None:
            nonlocal found
            if inner is not None:
                found = (found or set()) | inner

        def add(item: ast.expr, where: ast.AST) -> None:
            nonlocal found
            verdict = self.is_agent(item, where)
            if verdict is True:
                found = found or set()
                identity = self._identity(item, where)
                if identity is not None:
                    found.add(identity)
                return
            inner = (
                self._agents_in(item, where, depth + 1, unknown)
                if isinstance(item, ast.List | ast.Tuple | ast.Set | ast.Dict | ast.Name | ast.Call)
                else None
            )
            merge(inner)
            if verdict is None and inner is None and unknown is not None and self._may_be_agent(item, where):
                # Not a container of the file's agents, and not proven to be no
                # agent: it may be one another file constructs.
                unknown.append((item, where))

        def part(expr: ast.expr, where: ast.AST) -> None:
            # A container within: ``OTHERS`` in ``[manager] + OTHERS`` may hold
            # another file's agents even when it holds none of this one's.
            inner = self._agents_in(expr, where, depth + 1, unknown)
            merge(inner)
            if inner is None and unknown is not None and self._may_be_agent(expr, where):
                unknown.append((expr, where))

        if isinstance(container, ast.Name):
            for node in self._appends.get(container.id, []):
                add(node, node)
            value = self.value_of(container.id, site)
            if value is None:
                # A loop variable: the members of what the loop iterates.
                loops = [
                    self.scopes.statement_of(item)
                    for item in self.scopes.enclosing_bindings(site, container.id)
                    if isinstance(item, ast.Name)
                ] or [item.statement for item in self.module_bindings.get(container.id, [])]
                for loop in loops:
                    if isinstance(loop, ast.For | ast.AsyncFor):
                        merge(self._agents_in(loop.iter, loop, depth + 1, unknown))
                return found
            container = value
        if isinstance(container, ast.List | ast.Tuple | ast.Set):
            for item in container.elts:
                add(item.value if isinstance(item, ast.Starred) else item, site)
        elif isinstance(container, ast.Dict):
            for item in container.values:
                add(item, site)
        elif isinstance(container, ast.BinOp):
            part(container.left, site)
            part(container.right, site)
        elif isinstance(container, ast.Subscript):
            # ``AGENTS[1:]``, or ``GROUPS["x"]`` of a container of containers.
            part(container.value, site)
        elif isinstance(container, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            for generator in container.generators:
                part(generator.iter, site)
        elif isinstance(container, ast.Call):
            name = dotted_name(container.func)
            if isinstance(container.func, ast.Attribute) and container.func.attr in {"values", "items", "copy"}:
                # ``REG.values()`` / ``REG.items()`` of a dict of agents.
                part(container.func.value, site)
            elif name in _ITERATION_WRAPPERS:
                for arg in container.args:
                    part(arg, site)
            elif isinstance(container.func, ast.Name) and container.func.id in self.functions:
                for node in ast.walk(self.functions[container.func.id]):
                    if isinstance(node, ast.Return) and node.value is not None:
                        merge(self._agents_in(node.value, node, depth + 1, unknown))
        return found

    def _may_be_agent(self, expr: ast.expr, site: ast.AST) -> bool:
        """Whether a container member may be an agent the file does not
        construct: not a function, class or lambda, a logger, a builtin's
        result or a literal (#876 review)."""

        if isinstance(expr, ast.Name):
            found = self.scopes.enclosing_bindings(site, expr.id)
            nodes = found or [item.node for item in self.module_bindings.get(expr.id, [])]
            if nodes and all(isinstance(node, _SCOPE_NODES) for node in nodes):
                return False
            value = self.value_of(expr.id, site)
            if value is not None:
                expr = value
        if isinstance(expr, ast.Lambda | ast.Constant | ast.JoinedStr) or self.is_agent(expr, site) is False:
            return False
        if isinstance(expr, ast.Call):
            spelling = reference_spelling(expr.func) or ""
            if spelling.rsplit(".", 1)[-1] in _LOGGER_FACTORIES or (
                isinstance(expr.func, ast.Name)
                and expr.func.id in _NON_AGENT_BUILTINS
                and not self.scopes.enclosing_bindings(site, expr.func.id)
                and not self.module_bindings.get(expr.func.id)
            ):
                return False
        return isinstance(expr, ast.Name | ast.Attribute | ast.Subscript | ast.Call)

    @property
    def _appends(self) -> dict[str, list[ast.expr]]:
        """``name -> items`` put into a module container: ``append`` /
        ``extend`` / ``insert``, ``x[k] = item``, ``setdefault(k, item)``,
        ``update({k: item})``."""

        if self._appended is None:
            self._appended = {}
            for node in self.scopes.parents:
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                ):
                    method, target = node.func.attr, node.func.value.id
                    items: list[ast.expr] = []
                    if method in {"append", "insert"} and node.args:
                        items = [node.args[-1]]
                    elif method == "extend" and node.args:
                        arg = node.args[0]
                        items = list(arg.elts) if isinstance(arg, ast.List | ast.Tuple) else [arg]
                    elif method == "setdefault" and len(node.args) > 1:
                        items = [node.args[1]]
                    elif method == "update":
                        for arg in node.args:
                            items += [item for item in arg.values if item is not None] if isinstance(arg, ast.Dict) else [arg]
                        items += [keyword.value for keyword in node.keywords]
                    if items:
                        self._appended.setdefault(target, []).extend(items)
                elif isinstance(node, ast.Assign):
                    for target_node in node.targets:
                        if isinstance(target_node, ast.Subscript) and isinstance(target_node.value, ast.Name):
                            self._appended.setdefault(target_node.value.id, []).append(node.value)
        return self._appended

    def _identity(self, item: ast.expr, site: ast.AST) -> str | None:
        value = self.value_of(item.id, site) if isinstance(item, ast.Name) else item
        if isinstance(value, ast.Call):
            return self.constructed.get(id(value)) or self.returned_agent(value)
        return None

    def _not_an_agent(self, call: ast.Call) -> bool:
        if isinstance(call.func, ast.Call):
            # ``type("P", (), {})()``: a class made on the spot, from no base
            # that could be an agent. Any other call of a call is unknown.
            maker = call.func
            return (
                dotted_name(maker.func) == "type"
                and len(maker.args) >= 2
                and isinstance(maker.args[1], ast.Tuple)
                and not maker.args[1].elts
            )
        name = dotted_name(call.func)
        if name in _NON_AGENT_CONSTRUCTORS:
            return True
        return name in self.classes and name not in self.subclasses

    def returned_agent(self, call: ast.Call) -> str | None:
        """The one observed agent a module-level function's every ``return`` gives back."""

        name = dotted_name(call.func)
        function = self.functions.get(name) if name else None
        if function is None:
            return None
        identities: set[str] = set()
        stack: list[ast.AST] = list(function.body)
        while stack:
            node = stack.pop()
            if isinstance(node, _SCOPE_NODES):
                continue
            if isinstance(node, ast.Return):
                value = node.value
                if isinstance(value, ast.Name):
                    value = self.value_of(value.id, node)
                if not isinstance(value, ast.Call) or id(value) not in self.constructed:
                    return None
                identities.add(self.constructed[id(value)])
            stack.extend(ast.iter_child_nodes(node))
        return identities.pop() if len(identities) == 1 else None

    def owner(self, receiver: ast.expr, site: ast.AST) -> str | bool | None:
        """The agent a capability change reaches: its identity, None, or True (unknown)."""

        if isinstance(receiver, ast.Name) and receiver.id in {"self", "cls"}:
            # The instance's own list: an agent only when the class is an
            # agent subclass, whose instances are already a named limit.
            return None
        if (
            isinstance(receiver, ast.Attribute)
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id in {"self", "cls"}
        ):
            assigned = self._self_attribute_values(receiver.attr, site)
            identities = {self.constructed.get(id(value)) for value in assigned}
            if assigned and None not in identities and len(identities) == 1:
                return identities.pop()
            return True
        if isinstance(receiver, ast.Name):
            value = self.value_of(receiver.id, site)
            if value is None:
                return True
            if isinstance(value, ast.Call):
                if id(value) in self.constructed:
                    return self.constructed[id(value)]
                returned = self.returned_agent(value)
                if returned is not None:
                    return returned
            return None if self.is_agent(value, site) is False else True
        return True

    def _self_attribute_values(self, attr: str, site: ast.AST) -> list[ast.expr]:
        current = self.scopes.parents.get(site)
        while current is not None and not isinstance(current, ast.ClassDef):
            current = self.scopes.parents.get(current)
        if current is None:
            return []
        values = self._class_attributes.get(id(current))
        if values is None:
            # One walk per class, not one per change site (#876 review).
            values = {}
            for node in ast.walk(current):
                if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id in {"self", "cls"}
                        ):
                            values.setdefault(target.attr, []).append(node.value)
            self._class_attributes[id(current)] = values
        return values.get(attr, [])


#: Capability attributes of either supported framework's agents.
_CENSUS_CAPABILITIES = frozenset({"tools", "handoffs", "mcp_servers", "sub_agents"})


@dataclass(frozen=True)
class ModuleCensus:
    """What a module no reader reads as an SDK source could do to an agent."""

    #: Lines of ``x.clone(tools=...)`` / ``replace(x, tools=...)`` copies.
    copies: list[int]
    #: ``(line, constructor)`` where an object this module can reach from the
    #: scope has its capability lists changed. ``constructor`` is the
    #: ``(module tail, class)`` a receiver was built from by an import, so the
    #: comparison can drop one that is plainly not an agent.
    changes: list[tuple[int, tuple[str, str] | None]]
    #: SDK ``Agent`` subclasses the module defines, by name, with their line.
    subclasses: dict[str, int]
    #: Every identifier the module's code spells (names, attributes, imports),
    #: so a use of a subclass is found in code, never in a string or comment.
    identifiers: frozenset[str] = frozenset()
    #: ``(module tail, name)`` for each ``from … import name``; ``*`` for a
    #: wildcard. With ``modules``, how a use names another module's class.
    imported: frozenset[tuple[str, str]] = frozenset()
    #: Tails of the modules imported whole (``import app.core``, ``from app import core``).
    modules: frozenset[str] = frozenset()
    #: Every class the module defines at top level.
    classes: frozenset[str] = frozenset()

    def uses(self, name: str, defining: str) -> bool:
        """Whether this module's code uses ``name`` from the module at ``defining``."""

        tail = _module_tail(defining)
        if (tail, name) in self.imported:
            return True
        return name in self.identifiers and (tail in self.modules or (tail, "*") in self.imported)

    def reexports(self, name: str, defining: str) -> bool:
        """Whether ``name`` from ``defining`` is in this module's namespace for others."""

        tail = _module_tail(defining)
        return (tail, name) in self.imported or (tail, "*") in self.imported


def _module_tail(path: str) -> str:
    posix = PurePosixPath(path)
    return posix.parent.name if posix.name == "__init__.py" else posix.stem


def census_module(
    tree: ast.Module,
    text: str,
    local_modules: frozenset[str] = frozenset(),
    *,
    read_as_sdk: bool = False,
    read_as_adk: bool = False,
    resolver: ImportResolver | None = None,
    module: PythonModule | None = None,
    callee_registry: dict[Path, ModuleCallees] | None = None,
) -> ModuleCensus:
    """The capability-changing constructs of one module, for the comparison (#876 review).

    A copy of a value not proven to be something else, a change to the
    capability list of an object this module imports from the scope
    (``agent.quote_agent.tools.append(...)``) or of any value it hands to the
    application's own code that is not read, and SDK ``Agent`` subclasses. A
    list only handed on in a module that imports nothing from the scope is
    left out: it is almost always another library's ``.tools``. A
    module read as an SDK source is read for its copies and changes by the
    reader itself, so only its subclasses and identifiers are collected here.

    One walk collects what every module needs; the rest runs only where that
    walk found something to read.
    """

    names: set[str] = set()
    imported: set[tuple[str, str]] = set()
    modules: set[str] = set()
    imports_scope = False
    touches_capabilities = False
    #: Names this module binds by importing from the scope.
    scope_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
            # ``self.tools`` is the object's own list, which the census never
            # attributes to an agent: it need not wake the heavy passes.
            touches_capabilities = touches_capabilities or (
                node.attr in _CENSUS_CAPABILITIES
                and not (isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"})
            )
        elif isinstance(node, ast.ImportFrom):
            # Only an import from the scope can name the scope's classes: a
            # vendor module of the same stem is not ``app/core.py``.
            local = bool(node.level) or (node.module or "").split(".", 1)[0] in local_modules
            imports_scope = imports_scope or local
            tail = (node.module or "").rsplit(".", 1)[-1]
            for alias in node.names:
                names.update(part for part in (alias.name, alias.asname) if part)
                if local:
                    if tail:
                        imported.add((tail, alias.name))
                    modules.add(alias.name)
                    scope_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.update(part for part in (alias.name.rsplit(".", 1)[-1], alias.asname) if part)
                if alias.name.split(".", 1)[0] in local_modules:
                    imports_scope = True
                    modules.add(alias.name.rsplit(".", 1)[-1])
                    scope_names.add(alias.asname or alias.name.split(".", 1)[0])
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "delattr", "getattr"}
        ):
            touches_capabilities = True
    identifiers_of = {
        "identifiers": frozenset(names),
        "imported": frozenset(imported),
        "modules": frozenset(modules),
        "classes": frozenset(node.name for node in tree.body if isinstance(node, ast.ClassDef)),
    }
    subclasses: dict[str, int] = {}
    sdk_names: _SdkNames | None = None
    if "Agent" in text:
        sdk_names = _SdkNames(tree)
        subclasses = _agent_subclasses(tree, sdk_names)
    wants_copies = not read_as_sdk and ("clone(" in text or "replace(" in text)
    # A module that imports nothing from the scope cannot reach its agents; its
    # ``.tools`` are another library's. One that does can reach them through
    # any value — an alias, a loop, a parameter, a call's result — so only a
    # value proven not to be an agent is left out (#876 review).
    # A Google ADK source constructs its own agents, and its reader does not
    # follow a change to them after construction, so it is read like one.
    # ``apply(plant_agent, overrides)`` with ``plant_agent`` imported: a
    # helper may rewrite it (#876 review).
    hands_scope_names = imports_scope and any(
        isinstance(node, ast.Call)
        and any(
            _root_name(item) in scope_names
            for arg in [*node.args, *(keyword.value for keyword in node.keywords)]
            for item in (arg.elts if isinstance(arg, ast.List | ast.Tuple | ast.Set) else [arg])
        )
        for node in ast.walk(tree)
    )
    wants_changes = (
        not read_as_sdk
        and (touches_capabilities or hands_scope_names)
        and (imports_scope or read_as_adk)
    )
    if not wants_copies and not wants_changes:
        return ModuleCensus([], [], subclasses, **identifiers_of)
    scopes = ScopeIndex(tree)
    bindings = _module_bindings(tree)[0]
    copies: list[int] = []
    if wants_copies:
        values = _AgentValues(tree, scopes, bindings, sdk_names or _SdkNames(tree), subclasses)
        copies = sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _capability_rebinding_copy(
                node, lambda receiver, node=node: values.is_agent(receiver, node)
            )
            is not None
        )

    changes: list[tuple[int, tuple[str, str] | None]] = []
    if wants_changes:
        values = _AgentValues(tree, scopes, bindings, sdk_names or _SdkNames(tree), subclasses)

        def constructor(receiver: ast.expr, site: ast.AST) -> tuple[str, str] | None:
            value = (
                values.value_of(receiver.id, site) if isinstance(receiver, ast.Name) else None
            )
            if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
                return None
            for item in bindings.get(value.func.id, []):
                statement = item.statement
                if isinstance(statement, ast.ImportFrom) and isinstance(item.node, ast.alias):
                    return ((statement.module or "").rsplit(".", 1)[-1], item.node.name)
            return None

        rewires: dict[tuple[int, int], Callable[[], bool]] = {}
        changes = sorted(
            {
                (site.lineno, constructor(receiver, site))  # type: ignore[attr-defined]
                for receiver, site, strict, via_unresolved in _capability_changes(
                    tree,
                    _CENSUS_CAPABILITIES,
                    scopes=scopes,
                    callees=ModuleCallees(
                        tree,
                        scopes,
                        bindings,
                        resolver=resolver if module is not None else None,
                        module=module,
                        registry=callee_registry,
                    ),
                    rewires=rewires,
                )
                if (owner := values.owner(receiver, site)) is not None
                # Handed on: counted for an agent the module builds, one it
                # imports from the scope, or any value handed to the
                # application's own code that is not read (#876 review).
                and (
                    not strict
                    or isinstance(owner, str)
                    or via_unresolved
                    or bool(_roots(_through_loops(receiver, site, scopes, bindings)) & scope_names)
                )
                and ((id(receiver), id(site)) not in rewires or rewires[(id(receiver), id(site))]())
            },
            key=lambda item: item[0],
        )
    return ModuleCensus(copies, changes, subclasses, **identifiers_of)


def _reassigns(site: ast.AST) -> bool:
    """Whether a capability change gives the agent a new list rather than changing its own."""

    if isinstance(site, ast.Assign | ast.AnnAssign):
        targets = site.targets if isinstance(site, ast.Assign) else [site.target]
        return all(isinstance(target, ast.Attribute) for target in targets)
    return isinstance(site, ast.Call) and isinstance(site.func, ast.Name) and site.func.id in {
        "setattr",
        "delattr",
    }


def _enclosing_scope(scopes: ScopeIndex, node: ast.AST) -> ast.AST | None:
    """The function or class body ``node`` is in; None at module level."""

    current = scopes.parents.get(node)
    while current is not None and not isinstance(current, ast.Module):
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            return current
        current = scopes.parents.get(current)
    return None


def _enclosing_function(scopes: ScopeIndex, node: ast.AST) -> ast.AST | None:
    current = scopes.parents.get(node)
    while current is not None and not isinstance(current, ast.Module | ast.ClassDef):
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            return current
        current = scopes.parents.get(current)
    return None


def _agent_subclasses(tree: ast.Module, sdk_names: _SdkNames) -> dict[str, int]:
    """Classes in this module that subclass the SDK's ``Agent``, transitively.

    ``Dyn = type("Dyn", (Agent,), {})`` makes one too (#876 review).
    """

    classes: list[tuple[str, list[ast.expr], int]] = [
        (node.name, list(node.bases), node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and dotted_name(node.value.func) == "type"
            and len(node.value.args) >= 2
            and isinstance(node.value.args[1], ast.Tuple)
        ):
            classes.append((node.targets[0].id, list(node.value.args[1].elts), node.lineno))
    found: dict[str, int] = {}
    changed = True
    while changed:
        changed = False
        for name, bases, line in classes:
            if name in found:
                continue
            for base in bases:
                inner = base.value if isinstance(base, ast.Subscript) else base
                if _denotes_agent(sdk_names, base) or (
                    isinstance(inner, ast.Name) and inner.id in found
                ):
                    found[name] = line
                    changed = True
                    break
    return found


def _opaque_arguments(call: ast.Call) -> str | None:
    """Arguments that can carry ``tools`` or ``handoffs`` the reader cannot see."""

    if any(keyword.arg is None for keyword in call.keywords):
        return "keyword unpacking (**)"
    if len(call.args) > 1 or any(isinstance(arg, ast.Starred) for arg in call.args):
        return "positional arguments after its name"
    return None


def _assignment_target(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


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


#: ``(name, site) -> [(binding node, its statement)]``, empty when unbound.
BindingsAt = Callable[[str, ast.AST], list[tuple[ast.AST, ast.AST | None]]]


def _bindings_at(scopes: ScopeIndex, module_bindings: dict[str, list[Any]]) -> BindingsAt:
    def found(name: str, site: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
        local = scopes.enclosing_bindings(site, name)
        if local:
            return [(item, scopes.statement_of(item)) for item in local]
        module = [(item.node, item.statement) for item in module_bindings.get(name, [])]
        # ``from helpers import *`` may bind any name: none is proven unbound.
        return module or ([(site, None)] if scopes.star_import else [])

    return found


def _leaves_arguments_alone(call: ast.Call, bindings_at: BindingsAt) -> bool:
    """Whether ``call`` is a builtin, a standard-library reader or a logging
    method, which only read what they are handed.

    The spelling proves nothing alone: a ``print`` imported from the
    application's helpers, or an ``.info()`` on an object of its own, may
    change the list (#879 review). A bare name is the builtin only when nothing
    binds it, or the standard-library reader when it is imported from that
    module; ``json.dumps`` only when ``json`` is the standard library's; a
    logging method only on ``logging`` or a logger ``getLogger()`` returned.
    """

    name = dotted_name(call.func)
    if name in _READ_ONLY_CALLS:
        head, _, rest = name.partition(".")
        found = bindings_at(head, call)
        if not found:
            return not rest
        if len(found) != 1:
            return False
        node, statement = found[0]
        if not isinstance(node, ast.alias):
            return False
        if isinstance(statement, ast.ImportFrom):
            # ``from pprint import pprint``.
            return not rest and not statement.level and f"{statement.module}.{node.name}" in _READ_ONLY_CALLS
        # ``import json`` then ``json.dumps``.
        return bool(rest) and isinstance(statement, ast.Import) and node.name == head and node.asname is None
    if (
        name in {"getattr", "hasattr"}
        and not bindings_at(name, call)
        and len(call.args) in {2, 3}
        and not call.keywords
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
        and not call.args[1].value.startswith("__")
        and call.args[1].value not in _LIST_MUTATORS | {"sort", "reverse"}
        and all(isinstance(item, ast.Constant) for item in call.args[2:])
    ):
        # ``getattr(config.tools, "enabled", True)``: reads a plain attribute.
        return True
    if not (isinstance(call.func, ast.Attribute) and call.func.attr in _LOG_METHODS):
        return False
    receiver = call.func.value
    if not isinstance(receiver, ast.Name):
        return False
    found = bindings_at(receiver.id, call)
    if len(found) != 1:
        return False
    node, statement = found[0]
    if isinstance(node, ast.alias):
        # ``logging.info(...)``.
        return isinstance(statement, ast.Import) and node.name == "logging" and receiver.id == "logging"
    value = getattr(statement, "value", None)
    # ``logger = logging.getLogger(__name__)``, a ``LoggerAdapter`` over one,
    # or a child logger (#876 review).
    return (
        isinstance(statement, ast.Assign | ast.AnnAssign)
        and isinstance(value, ast.Call)
        and (reference_spelling(value.func) or "").rsplit(".", 1)[-1] in _LOGGER_FACTORIES
    )


#: Builtins whose result, as a container member, is no agent. Not the data
#: builtins above (``_BUILTIN_NAMES``), which a library read trusts.
_NON_AGENT_BUILTINS = frozenset(
    {"abs", "all", "any", "bool", "bytes", "callable", "chr", "dict", "divmod", "enumerate",
     "filter", "float", "format", "frozenset", "hash", "hex", "id", "int", "isinstance",
     "issubclass", "iter", "len", "list", "map", "max", "min", "oct", "open", "ord", "pow",
     "print", "range", "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum",
     "tuple", "zip"}
)

#: Calls whose result is a logger.
_LOGGER_FACTORIES = frozenset({"getLogger", "get_logger", "getChild", "LoggerAdapter"})


def _parameter_left_alone(
    function: ast.FunctionDef | ast.AsyncFunctionDef, name: str, bindings_at: BindingsAt
) -> bool:
    """Whether every use of parameter ``name`` in ``function`` only reads it.

    The same test as a module list's own uses, one level deep: handing it on
    to any call but a read-only builtin or logging method is not a read.
    ``bindings_at`` answers for the function's own module.
    """

    parents = {child: node for node in ast.walk(function) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(function):
        if isinstance(node, ast.Global | ast.Nonlocal) and name in node.names:
            return False
        if isinstance(node, ast.Name) and node.id == name:
            if not isinstance(node.ctx, ast.Load):
                return False
            if not _read_only_use(
                node, parents, lambda call, *_: _leaves_arguments_alone(call, bindings_at)
            ):
                return False
    return True


def _read_only_use(
    node: ast.expr,
    parents: dict[ast.AST, ast.AST],
    call_reads: Callable[[ast.Call, int | None, str | None], bool],
) -> bool:
    """Whether this load of a list can only read it, never change or hand it on."""

    parent = parents.get(node)
    if isinstance(parent, ast.keyword):
        call = parents.get(parent)
        return isinstance(call, ast.Call) and call_reads(call, None, parent.arg)
    if isinstance(parent, ast.Call):
        for position, arg in enumerate(parent.args):
            if arg is node:
                return call_reads(parent, position, None)
        return False
    if isinstance(parent, ast.For | ast.AsyncFor | ast.comprehension):
        return parent.iter is node
    if isinstance(parent, ast.Subscript):
        return parent.value is node and isinstance(parent.ctx, ast.Load)
    if isinstance(parent, ast.BoolOp) or (
        isinstance(parent, ast.IfExp) and parent.test is not node
    ):
        # ``TOOLS or [x]`` may be the list itself: its own use decides.
        return _read_only_use(parent, parents, call_reads)
    if isinstance(parent, ast.If | ast.While | ast.IfExp | ast.Assert):
        return parent.test is node
    if isinstance(parent, ast.UnaryOp):
        return isinstance(parent.op, ast.Not)
    if isinstance(parent, ast.Starred):
        # ``[*TOOLS, x]`` or ``f(*TOOLS)`` spreads the members; the list itself
        # goes nowhere.
        return parent.value is node
    if isinstance(parent, ast.Compare | ast.FormattedValue | ast.Expr | ast.BinOp):
        # A comparison, a string, a bare expression, or ``TOOLS + [x]`` (a new list).
        return True
    if isinstance(parent, ast.Attribute) and parent.value is node:
        grand = parents.get(parent)
        return (
            parent.attr in {"count", "index", "copy", "get", "keys", "values", "items"}
            and isinstance(grand, ast.Call)
            and grand.func is parent
        )
    return False


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
        # Only a name bound to a literal list somewhere can be read as one.
        listed = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign | ast.AnnAssign)
            and isinstance(node.value, ast.List | ast.Tuple)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        # Every use of such a name must be a read that cannot change the list:
        # iterated, indexed, compared, tested, handed to a read-only builtin, to
        # an agent's own ``tools=``, or to a function that treats its parameter
        # the same way. Any other use — a method call, ``+=``, a second name, a
        # tuple, a return, ``*args`` — may change it (#879 review).
        # ``globals()["TOOLS"]``, ``vars()`` and ``sys.modules[__name__]``
        # reach a module list without spelling its name (#879 review).
        if reflective_access(tree) is not None:
            self.changed.update(("module", name) for name in listed)
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                self.changed.update(("module", name) for name in node.names)
            elif isinstance(node, ast.Nonlocal):
                for name in node.names:
                    found = scopes.enclosing_bindings(node, name)
                    if found:
                        self.changed.add(id(found[0]))
            elif isinstance(node, ast.Name) and node.id in listed:
                parent = scopes.parents.get(node)
                if isinstance(node.ctx, ast.Load):
                    unchanged = _read_only_use(node, scopes.parents, self._call_reads)
                else:
                    unchanged = isinstance(node.ctx, ast.Store) and not isinstance(
                        parent, ast.AugAssign
                    )
                if not unchanged:
                    found = scopes.enclosing_bindings(node, node.id)
                    self.changed.add(id(found[0]) if found else ("module", node.id))

    def _call_reads(self, call: ast.Call, position: int | None, keyword: str | None) -> bool:
        """Whether ``call`` only reads the list it is passed at ``position``/``keyword``."""

        if _leaves_arguments_alone(call, _bindings_at(self.scopes, self.module_bindings)):
            return True
        if keyword in {"tools", "handoffs", "mcp_servers"} and (
            (
                self.sdk_names is not None
                and self.sdk_names.denotes(
                    dotted_name(call.func), call, "Agent", DEFAULT_AGENT_CONSTRUCTORS
                )
            )
            or (isinstance(call.func, ast.Attribute) and call.func.attr == "clone")
            or dotted_name(call.func) in {"replace", "dataclasses.replace", "copy.replace"}
        ):
            # An agent, or a copy of one, reads its own ``tools=``.
            return True
        return self._callee_leaves_alone(call, position, keyword)

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
        defining = resolution.module
        assert defining is not None
        return _parameter_left_alone(
            function, parameter, _bindings_at(ScopeIndex(defining.tree), defining.bindings)
        )

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
        self,
        value: ast.AST | None,
        node: ast.AST,
        aliases: dict[str, str],
        identity_of: Callable[[ast.Call], str | None] | None = None,
        sdk_names: _SdkNames | None = None,
    ) -> list[str] | None:
        """Handoff names: plain names only, read through ``from`` import aliases.

        A name a function binds to an agent it constructs is that agent's
        identity, which is its literal ``name`` (#876 review).
        """

        elements = self._elements(value, node)
        if elements is None or not all(isinstance(item, ast.Name) for item in elements):
            return None
        names: list[str] = []
        for item in elements:
            assert isinstance(item, ast.Name)
            found = self.scopes.enclosing_bindings(item, item.id)
            statement = self.scopes.statement_of(found[0]) if len(found) == 1 else None
            value_node = getattr(statement, "value", None)
            if (
                identity_of is not None
                and sdk_names is not None
                and isinstance(found[0] if found else None, ast.Name)
                and isinstance(value_node, ast.Call)
                and _denotes_agent(sdk_names, value_node)
            ):
                identity = identity_of(value_node)
                if identity is not None:
                    names.append(identity)
                    continue
            names.append(aliases.get(item.id, item.id))
        return names


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
