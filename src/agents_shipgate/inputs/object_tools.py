"""Tools an agent binds as objects, identified by what they are (#910).

Some of the capabilities a reviewer most needs to see are not decorated
functions but objects: a remote MCP server with credentials, another agent
exposed as a tool, a hosted tool the framework provides. The readers used to
name each as "unresolved" or "assigned a value, not a function definition".
This module identifies them, for ``diff --application`` only, as bindings with
a stable identity:

* an MCP server or toolset — Google ADK ``McpToolset`` / ``MCPToolset`` and the
  OpenAI Agents SDK's ``MCPServerStdio`` / ``MCPServerSse`` /
  ``MCPServerStreamableHttp`` — by its transport, the URL's host or the
  program's file name, the names of the credentials it sends and its tool
  filter. Never the URL's path or query, a header's value or a command's
  arguments: those are digested, never printed;
* an agent exposed as a tool — the SDK's ``agent.as_tool(...)``, ADK's
  ``AgentTool(agent=...)`` — by the agent it wraps and the tool's name;
* a hosted or built-in tool — the SDK's ``WebSearchTool``, ``FileSearchTool``,
  ``CodeInterpreterTool``, ``ComputerTool``; ADK's ``google_search``,
  ``built_in_code_execution`` and ``load_memory`` — by its documented name;
* a function wrapped as a value — the SDK's ``function_tool(f)`` — as the
  function it wraps, which the SDK reader then reads as any function tool.

Identity is the import, never the spelling: a name counts only when its one
binding where it is used is an absolute import of the framework's package
that no file in the read scope or the repository provides, and nothing in the
scope stores into it. A same-named local function, a shadowed import or a
reassigned symbol is not one. A repository helper that returns one of these
from a single unconditional ``return`` is followed, up to :data:`MAX_DEPTH`
calls, with its parameters bound to the caller's arguments; any other helper
stays what the reader already names it. Nothing is imported or run.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agents_shipgate.inputs.list_expressions import evaluation_site
from agents_shipgate.inputs.python_imports import (
    MODULE_NOT_FOUND,
    ImportResolver,
    PythonModule,
    Resolution,
    ScopeIndex,
    reference_spelling,
)
from agents_shipgate.inputs.tool_reach import (
    ValueSite,
    is_absent,
    object_command,
    object_credentials,
    object_digest,
    object_hosts,
    object_named,
    object_record,
    object_strings,
    read_object_values,
    scope_library_patches,
    scope_mutations,
)

SDK = "openai_agents_sdk"
ADK = "google_adk"

#: Helper calls followed to the object one returns.
MAX_DEPTH = 8

_SDK_ROOTS = frozenset({"agents", "openai_agents"})
SDK_HOSTED_TOOLS = frozenset({"WebSearchTool", "FileSearchTool", "CodeInterpreterTool", "ComputerTool"})
SDK_MCP_SERVERS = {
    "MCPServerStdio": "stdio",
    "MCPServerSse": "sse",
    "MCPServerStreamableHttp": "streamable_http",
}
#: ADK's built-in tools, by the absolute import that provides each.
ADK_BUILT_INS = {
    "google.adk.tools.google_search": "google_search",
    "google.adk.tools.google_search_tool.google_search": "google_search",
    "google.adk.tools.load_memory": "load_memory",
    "google.adk.tools.load_memory_tool.load_memory": "load_memory",
    "google.adk.tools.load_memory_tool.load_memory_tool": "load_memory",
    "google.adk.tools.built_in_code_execution": "built_in_code_execution",
    "google.adk.tools.built_in_code_execution_tool.built_in_code_execution": "built_in_code_execution",
}
ADK_AGENT_TOOLS = frozenset({"google.adk.tools.AgentTool", "google.adk.tools.agent_tool.AgentTool"})
ADK_MCP_TOOLSETS = frozenset(
    f"{module}.{name}"
    for module in ("google.adk.tools", "google.adk.tools.mcp_tool", "google.adk.tools.mcp_tool.mcp_toolset")
    for name in ("McpToolset", "MCPToolset")
)
#: ADK connection parameters, by class name, under ``google.adk`` or ``mcp``.
ADK_CONNECTIONS = {
    "StreamableHTTPConnectionParams": "streamable_http",
    "StreamableHTTPServerParams": "streamable_http",
    "SseConnectionParams": "sse",
    "SseServerParams": "sse",
    "StdioConnectionParams": "stdio",
    "StdioServerParameters": "stdio",
}
#: Keywords that describe an agent tool to the model, not what it can do.
_DESCRIPTIVE = frozenset({"tool_description", "description"})

_READING: ContextVar[Callable[[str], bool] | None] = ContextVar("object_bindings", default=None)


@contextmanager
def reading_object_bindings(is_test: Callable[[str], bool]) -> Iterator[None]:
    """Read object bindings in the readers called inside this block.

    Only ``diff --application`` reads them; ``scan`` keeps what it reads, so no
    catalog, check or report moves. ``is_test`` is the test-path rule the
    comparison uses, so a patch in a test file is not one in the application.
    """

    token = _READING.set(is_test)
    try:
        yield
    finally:
        _READING.reset(token)


def object_bindings_rule() -> Callable[[str], bool] | None:
    """The test-path rule when object bindings are read, else None."""

    return _READING.get()


@dataclass(frozen=True)
class ObjectBinding:
    """One tool bound as an object, with the identity a row compares."""

    #: The binding's name in the agent's tool list: the tool's own name, or
    #: the variable or helper call an MCP server comes from. None for an
    #: anonymous server, which the reader numbers by its place in the list.
    name: str | None
    identity: dict[str, Any]
    #: ``path:line`` of the construction (or the import, for a built-in).
    location: str
    #: What of the identity is not read: an open question, never a guess.
    unread: tuple[str, ...] = ()
    #: Evidence beside the identity, never compared (a wrapped agent's file).
    evidence: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "location": self.location,
            "unread": list(self.unread),
            **({"evidence": self.evidence} if self.evidence else {}),
        }

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class WrappedFunction:
    """``function_tool(f)``: the function the SDK wraps as a tool."""

    module: PythonModule
    definition: ast.FunctionDef | ast.AsyncFunctionDef
    name_override: str | None
    location: str
    resolution: Resolution | None = None
    #: A digest of the wrapper's other arguments as written (``needs_approval=``,
    #: ``strict_mode=``): part of the tool, as a decorator's are; None when none.
    options_sha256: str | None = None


@dataclass(frozen=True)
class Unread:
    """It is one of these objects, but what it is cannot be established."""

    reason: str


Found = ObjectBinding | WrappedFunction | Unread


@dataclass(frozen=True)
class _Bound:
    kind: str  # "param" | "local" | "module"
    node: ast.AST
    statement: ast.stmt | None
    site: ValueSite


class ObjectTools:
    """Identifies object bindings for one framework reader, inside one scope."""

    def __init__(self, framework: str, resolver: ImportResolver, is_test: Callable[[str], bool]) -> None:
        self.framework = framework
        self.resolver = resolver
        self.is_test = is_test
        self._scopes: dict[int, ScopeIndex] = {}
        self._sdk_names: dict[int, Any] = {}
        self._library: dict[str, str] | None = None
        self._mutated: dict[str, str] | None = None

    # -- entry -------------------------------------------------------------

    def recognize(self, expr: ast.expr, module: PythonModule) -> Found | None:
        """What ``expr``, written in ``module``, binds, when it is an object.

        None: not an object binding this module identifies; the reader keeps
        what it already says about it.
        """

        try:
            return self._recognize(expr, ValueSite(module, self._function_of(module, expr)), None, 0)
        except RecursionError:
            return None

    # -- names -------------------------------------------------------------

    def _scopes_of(self, module: PythonModule) -> ScopeIndex:
        scopes = self._scopes.get(id(module.tree))
        if scopes is None:
            scopes = self._scopes[id(module.tree)] = ScopeIndex(module.tree)
        return scopes

    def _function_of(
        self, module: PythonModule, node: ast.AST
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        scopes = self._scopes_of(module)
        current = scopes.parents.get(evaluation_site(scopes, node))
        while current is not None and not isinstance(current, ast.Module):
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current
            current = scopes.parents.get(current)
        return None

    def _binding(self, site: ValueSite, node: ast.AST, name: str) -> _Bound | None:
        """The one binding of ``name`` where ``node`` reads it, or None."""

        scopes = self._scopes_of(site.module)
        local = scopes.enclosing_bindings(evaluation_site(scopes, node), name)
        if local:
            if len(local) != 1:
                return None
            bound = local[0]
            # The function that binds it: a closure's name is its enclosing
            # function's, whose parameters this read does not hold.
            owner = scopes.parents.get(bound)
            while owner is not None and not isinstance(
                owner, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.Module
            ):
                owner = scopes.parents.get(owner)
            if not isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef):
                return None
            where = site if owner is site.function else ValueSite(site.module, owner)
            if isinstance(bound, ast.arg):
                return _Bound("param", bound, None, where)
            return _Bound("local", bound, scopes.statement_of(bound), where)
        bindings = site.module.bindings.get(name, [])
        if site.module.star_import or len(bindings) != 1 or not bindings[0].top_level:
            return None
        return _Bound("module", bindings[0].node, bindings[0].statement, ValueSite(site.module))

    def _argument(self, site: ValueSite, name: str) -> tuple[ast.expr, ValueSite] | None:
        """What the call that entered ``site.function`` passes as ``name``."""

        function, call, caller = site.function, site.call, site.caller
        if function is None or call is None or caller is None:
            return None
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
            keyword.arg is None for keyword in call.keywords
        ):
            return None
        arguments = function.args
        positional = [*arguments.posonlyargs, *arguments.args]
        for keyword in call.keywords:
            if keyword.arg == name:
                return keyword.value, caller
        for index, param in enumerate(positional):
            if param.arg == name and index < len(call.args):
                return call.args[index], caller
        defaults = dict(
            zip(
                [param.arg for param in positional[len(positional) - len(arguments.defaults):]],
                arguments.defaults,
                strict=True,
            )
        )
        defaults.update(
            {
                param.arg: default
                for param, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
                if default is not None
            }
        )
        if name in defaults:
            # Evaluated where the function is defined: its module, for the
            # module-level helpers this follows.
            return defaults[name], ValueSite(site.module)
        return None

    def _changed(self, bound: _Bound, name: str) -> str | None:
        """Where the object bound to ``name`` is changed after it is built."""

        module = bound.site.module
        if bound.kind == "module":
            for key, line in module.attribute_patches.items():
                if key.startswith(name + "."):
                    return f"{module.ref}:{line}"
            # A module's object can be changed from any of its functions.
            scope: ast.AST = module.tree
        elif bound.site.function is not None:
            scope = bound.site.function
        else:
            return None
        for item in ast.walk(scope):
            target = None
            if isinstance(item, ast.Attribute) and isinstance(item.ctx, ast.Store | ast.Del):
                target = item.value
            elif isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id in {"setattr", "delattr"}:
                target = item.args[0] if item.args else None
            elif (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and isinstance(item.func.value, ast.Attribute | ast.Subscript)
            ):
                # ``toolset.tool_filter.append(...)``: a method changing what
                # the object holds. A method of the object itself
                # (``server.connect()``) is how it is used, not a change.
                target = item.func.value
                while isinstance(target, ast.Attribute | ast.Subscript):
                    target = target.value
            elif isinstance(item, ast.Nonlocal | ast.Global) and bound.kind == "local" and name in item.names:
                # A nested function rebinding the name.
                return f"{module.ref}:{item.lineno}"
            if isinstance(target, ast.Name) and target.id == name:
                return f"{module.ref}:{item.lineno}"
        return None

    # -- import identity ----------------------------------------------------

    def _framework_path(self, expr: ast.AST, site: ValueSite) -> str | None:
        """The absolute import ``expr`` names, when it is a third-party package's.

        The head's one binding where it is used is an absolute import; the
        resolver finds no file in the read scope for it, the repository holds
        none, and nothing in the scope stores into it.
        """

        spelling = reference_spelling(expr)
        if spelling is None:
            return None
        head, _, rest = spelling.partition(".")
        bound = self._binding(site, expr, head)
        if bound is None or bound.kind == "param" or not isinstance(bound.node, ast.alias):
            return None
        alias, statement = bound.node, bound.statement
        if isinstance(statement, ast.ImportFrom):
            if statement.level or not statement.module:
                return None
            path = f"{statement.module}.{alias.name}" + (f".{rest}" if rest else "")
        elif isinstance(statement, ast.Import):
            path = f"{alias.name}.{rest}" if alias.asname else spelling
            if not alias.asname and not (spelling == alias.name or spelling.startswith(alias.name + ".")):
                return None
        else:
            return None
        package, _, last = path.rpartition(".")
        if not package or not self._known(path):
            return None
        module = bound.site.module
        if bound.kind == "local":
            resolution = self.resolver.resolve_local_import(module, statement, alias, spelling)
        else:
            resolution = self.resolver.resolve(module, spelling)
        if resolution.reason != MODULE_NOT_FOUND:
            return None
        if self.resolver.repository_holds(package, [last]) or self._patched(path, head, module, rest):
            return None
        return path

    def _known(self, path: str) -> bool:
        """Whether ``path`` is one of the framework objects this module reads."""

        root, short = path.split(".", 1)[0], path.rsplit(".", 1)[-1]
        if self.framework == ADK:
            return (
                path in ADK_BUILT_INS
                or path in ADK_AGENT_TOOLS
                or path in ADK_MCP_TOOLSETS
                or (root in {"google", "mcp"} and short in ADK_CONNECTIONS)
                or (path.startswith("google.adk.") and short.endswith("Agent"))
            )
        return root in _SDK_ROOTS and (
            short in SDK_HOSTED_TOOLS
            or short in SDK_MCP_SERVERS
            or short in {"function_tool", "create_static_tool_filter"}
        )

    def _patched(self, path: str, head: str, module: PythonModule, rest: str) -> bool:
        """Whether the scope stores into the imported object or its module."""

        if any(key == head or key.startswith(head + ".") for key in module.attribute_patches):
            return True
        if self._library is None:
            root = self.resolver.scope_root
            self._library = scope_library_patches(root, self.is_test)
            self._mutated = scope_mutations(root, self.is_test)[0]
        last = path.rsplit(".", 1)[-1]
        if self._mutated and last in self._mutated:
            return True
        package = path.rsplit(".", 1)[0]
        for key in self._library:
            stored = key.split(":", 1)[-1].lstrip("^")
            if stored == path or stored.startswith(path + ".") or stored in {f"{package}.*", "*"}:
                return True
        return False

    # -- recognition -------------------------------------------------------

    def _recognize(self, expr: ast.expr, site: ValueSite, hint: str | None, depth: int) -> Found | None:
        if depth > MAX_DEPTH:
            return None
        if isinstance(expr, ast.Call):
            return self._call(expr, site, hint, depth)
        spelling = reference_spelling(expr)
        if spelling is None:
            return None
        head, _, rest = spelling.partition(".")
        bound = self._binding(site, expr, head)
        if bound is None:
            return None
        if bound.kind == "param":
            argument = self._argument(bound.site, head)
            if argument is None or rest:
                return None
            return self._recognize(argument[0], argument[1], hint, depth + 1)
        node, statement = bound.node, bound.statement
        if isinstance(node, ast.alias):
            if self.framework == ADK:
                path = self._framework_path(expr, site)
                if path in ADK_BUILT_INS:
                    tool = ADK_BUILT_INS[path]
                    assert statement is not None
                    return ObjectBinding(
                        name=tool,
                        identity={"kind": "built_in_tool", "tool": tool},
                        location=f"{bound.site.module.ref}:{statement.lineno}",
                        evidence={"import": path},
                    )
                if path is not None:
                    return None
            return self._through_import(expr, spelling, bound, hint, depth)
        if rest:
            return None
        if isinstance(statement, ast.Assign | ast.AnnAssign) and statement.value is not None:
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if len(targets) != 1 or targets[0] is not node:
                return None
            found = self._recognize(statement.value, bound.site, hint or head, depth + 1)
            changed = self._changed(bound, head) if isinstance(found, ObjectBinding) else None
            if changed is not None:
                return Unread(f"`{head}` is changed after it is built, at {changed}")
            return found
        if isinstance(statement, ast.With | ast.AsyncWith):
            for item in statement.items:
                if item.optional_vars is node:
                    return self._recognize(item.context_expr, bound.site, hint or head, depth + 1)
        return None

    def _through_import(
        self, expr: ast.expr, spelling: str, bound: _Bound, hint: str | None, depth: int
    ) -> Found | None:
        """A name a repository-local import binds to an object built there."""

        module = bound.site.module
        if bound.kind == "local":
            assert isinstance(bound.statement, ast.Import | ast.ImportFrom)
            assert isinstance(bound.node, ast.alias)
            resolution = self.resolver.resolve_local_import(module, bound.statement, bound.node, spelling)
        else:
            resolution = self.resolver.resolve(module, spelling)
        if resolution.caveats or resolution.module is None or resolution.value is None:
            return None
        name = next(
            (step["name"] for step in reversed(resolution.steps) if step.get("binding") == "value"),
            None,
        )
        found = self._recognize(resolution.value, ValueSite(resolution.module), hint or name, depth + 1)
        if isinstance(found, ObjectBinding) and name is not None:
            # Changed after it is built, in the module that builds it.
            changed = self._changed(_Bound("module", resolution.value, None, ValueSite(resolution.module)), name)
            if changed is not None:
                return Unread(f"`{name}` is changed after it is built, at {changed}")
        return found

    def _call(self, call: ast.Call, site: ValueSite, hint: str | None, depth: int) -> Found | None:
        func = call.func
        path = self._framework_path(func, site)
        if path is not None:
            root, short = path.split(".", 1)[0], path.rsplit(".", 1)[-1]
            if self.framework == ADK:
                if path in ADK_MCP_TOOLSETS:
                    # ``MCPToolset`` is ADK's older spelling of the same class.
                    return self._adk_mcp(call, site, hint, "McpToolset", depth)
                if path in ADK_AGENT_TOOLS:
                    return self._adk_agent_tool(call, site, depth)
            elif root in _SDK_ROOTS:
                if short in SDK_HOSTED_TOOLS:
                    return self._sdk_hosted(call, site, short)
                if short in SDK_MCP_SERVERS:
                    return self._sdk_mcp(call, site, hint, short, depth)
                if short == "function_tool":
                    return self._wrapped(call, site, depth)
            return None
        if self.framework == SDK and isinstance(func, ast.Attribute) and func.attr == "as_tool":
            return self._as_tool(call, site, depth)
        helper = self._helper(func, site)
        if helper is None:
            return None
        module, function = helper
        spelling = reference_spelling(func)
        return self._recognize(
            _single_return(function),  # type: ignore[arg-type]
            ValueSite(module, function, call, site),
            hint or f"{spelling}()",
            depth + 1,
        )

    def _helper(
        self, func: ast.expr, site: ValueSite
    ) -> tuple[PythonModule, ast.FunctionDef | ast.AsyncFunctionDef] | None:
        """The repository function a call runs, when it returns one value plainly."""

        spelling = reference_spelling(func)
        if spelling is None:
            return None
        head, _, rest = spelling.partition(".")
        bound = self._binding(site, func, head)
        if bound is None or bound.kind == "param":
            return None
        node = bound.node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not rest:
            found: tuple[PythonModule, ast.FunctionDef | ast.AsyncFunctionDef] = (bound.site.module, node)
        elif isinstance(node, ast.alias):
            module = bound.site.module
            if bound.kind == "local":
                assert isinstance(bound.statement, ast.Import | ast.ImportFrom)
                resolution = self.resolver.resolve_local_import(module, bound.statement, node, spelling)
            else:
                resolution = self.resolver.resolve(module, spelling)
            if resolution.caveats or resolution.definition is None or resolution.module is None:
                return None
            found = (resolution.module, resolution.definition)
        else:
            return None
        return found if _single_return(found[1]) is not None else None

    def _construction(
        self, expr: ast.expr | None, site: ValueSite, depth: int
    ) -> tuple[ast.Call, ValueSite] | None:
        """The call a value is built by, followed through names and parameters."""

        for _ in range(MAX_DEPTH):
            if expr is None:
                return None
            if isinstance(expr, ast.Call):
                return expr, site
            if not isinstance(expr, ast.Name):
                return None
            bound = self._binding(site, expr, expr.id)
            if bound is None:
                return None
            if bound.kind == "param":
                argument = self._argument(bound.site, expr.id)
                if argument is None:
                    return None
                expr, site = argument
                continue
            statement = bound.statement
            if isinstance(bound.node, ast.alias):
                spelling = expr.id
                module = bound.site.module
                if bound.kind == "local":
                    assert isinstance(statement, ast.Import | ast.ImportFrom)
                    resolution = self.resolver.resolve_local_import(module, statement, bound.node, spelling)
                else:
                    resolution = self.resolver.resolve(module, spelling)
                if resolution.caveats or resolution.module is None or resolution.value is None:
                    return None
                name = next(
                    (step["name"] for step in reversed(resolution.steps) if step.get("binding") == "value"),
                    None,
                )
                defining = ValueSite(resolution.module)
                if name is None or self._changed(_Bound("module", resolution.value, None, defining), name):
                    return None
                expr, site = resolution.value, defining
                continue
            if (
                isinstance(statement, ast.Assign | ast.AnnAssign)
                and statement.value is not None
                and (statement.targets if isinstance(statement, ast.Assign) else [statement.target]) == [bound.node]
                and self._changed(bound, expr.id) is None
            ):
                expr, site = statement.value, bound.site
                continue
            return None
        return None

    # -- the kinds ----------------------------------------------------------

    def _values(self, items: list[tuple[ValueSite, ast.AST | None]]) -> list[Any]:
        return read_object_values(self.resolver, items, is_test=self.is_test)

    def _sdk_hosted(self, call: ast.Call, site: ValueSite, short: str) -> Found:
        named = [(f"#{index}", arg) for index, arg in enumerate(call.args)] + [
            (keyword.arg or "**", keyword.value) for keyword in call.keywords
        ]
        values = self._values([(site, node) for _, node in named])
        unread = tuple(
            f"its argument {name} is not a value this read names"
            for (name, _), value in zip(named, values, strict=True)
            if name == "**" or not object_named(value)
        )
        return ObjectBinding(
            name=short,
            identity={
                "kind": "hosted_tool",
                "tool": short,
                "arguments": sorted(name for name, _ in named),
                "configuration_sha256": object_digest(
                    *[value for value in values], *[name for name, _ in named]
                )
                if named
                else None,
            },
            location=f"{site.module.ref}:{call.lineno}",
            unread=unread,
        )

    def _sdk_mcp(self, call: ast.Call, site: ValueSite, hint: str | None, short: str, depth: int) -> Found:
        params = _keyword(call, "params", 0)
        name_expr = _keyword(call, "name", None)
        name = self._literal(name_expr, site, depth) if name_expr is not None else None
        if name_expr is not None and name is None:
            return Unread(f"the MCP server built at {site.module.ref}:{call.lineno} has a name that is not a literal")
        filter_expr = _keyword(call, "tool_filter", None)
        transport = SDK_MCP_SERVERS[short]
        (params_value,) = self._values([(site, params)])
        record = object_record(params_value)
        unread: list[str] = []
        url = headers = command = args = env = None
        if record is None:
            unread.append("its params are not a dict this read names")
        else:
            if any(key == "**" for key, _ in record.fields):
                unread.append("its params are spread from a value this read does not name")
            url, headers = record.get("url"), record.get("headers")
            command, args, env = record.get("command"), record.get("args"), record.get("env")
        tool_filter = self._sdk_filter(filter_expr, site, depth, unread)
        return self._mcp(
            hint=name or hint,
            short=short,
            transport=transport,
            site=site,
            call=call,
            values={"url": url, "headers": headers, "command": command, "args": args, "env": env},
            tool_filter=tool_filter,
            unread=unread,
        )

    def _sdk_filter(self, expr: ast.expr | None, site: ValueSite, depth: int, unread: list[str]) -> Any:
        if expr is None:
            return None
        built = self._construction(expr, site, depth)
        if built is not None:
            call, call_site = built
            path = self._framework_path(call.func, call_site)
            if path is not None and path.split(".", 1)[0] in _SDK_ROOTS and path.endswith(".create_static_tool_filter"):
                allowed, blocked = self._values(
                    [
                        (call_site, _keyword(call, "allowed_tool_names", None)),
                        (call_site, _keyword(call, "blocked_tool_names", None)),
                    ]
                )
                allowed_names, allowed_unread = object_strings(allowed)
                blocked_names, blocked_unread = object_strings(blocked)
                if allowed_unread or blocked_unread:
                    unread.append("its tool filter is not read")
                return {"allowed": allowed_names, "blocked": blocked_names}
        (value,) = self._values([(site, expr)])
        if is_absent(value):
            return None
        record = object_record(value)
        if record is not None and {key for key, _ in record.fields} <= {"allowed_tool_names", "blocked_tool_names"}:
            allowed_names, allowed_unread = object_strings(record.get("allowed_tool_names"))
            blocked_names, blocked_unread = object_strings(record.get("blocked_tool_names"))
            if not (allowed_unread or blocked_unread):
                return {"allowed": allowed_names, "blocked": blocked_names}
        unread.append("its tool filter is not read")
        return None

    def _adk_mcp(self, call: ast.Call, site: ValueSite, hint: str | None, short: str, depth: int) -> Found:
        unread: list[str] = []
        values: dict[str, tuple[ValueSite, ast.expr | None]] = {}
        transport: str | None = None
        built = self._construction(_keyword(call, "connection_params", 0), site, depth)
        if built is None:
            unread.append("its connection is not read")
        else:
            connection, connection_site = built
            path = self._framework_path(connection.func, connection_site)
            kind = path.rsplit(".", 1)[-1] if path and path.split(".", 1)[0] in {"google", "mcp"} else None
            transport = ADK_CONNECTIONS.get(kind or "")
            if transport is None:
                unread.append("its connection is not one this read identifies")
            elif transport != "stdio":
                values["url"] = (connection_site, _keyword(connection, "url", None))
                values["headers"] = (connection_site, _keyword(connection, "headers", None))
            else:
                server, server_site = connection, connection_site
                if kind == "StdioConnectionParams":
                    nested = self._construction(_keyword(connection, "server_params", 0), connection_site, depth)
                    if nested is None:
                        server = None
                    else:
                        server, server_site = nested
                        nested_path = self._framework_path(server.func, server_site) or ""
                        if nested_path.rsplit(".", 1)[-1] != "StdioServerParameters":
                            server = None
                if server is None:
                    unread.append("its server parameters are not read")
                else:
                    for key in ("command", "args", "env"):
                        values[key] = (server_site, _keyword(server, key, None))
        keys = list(values)
        read = self._values([values[key] for key in keys] + [(site, _keyword(call, "tool_filter", None))])
        found = dict(zip(keys, read[:-1], strict=True))
        names, filter_unread = object_strings(read[-1])
        if filter_unread:
            unread.append("its tool filter is not read")
        return self._mcp(
            hint=hint,
            short=short,
            transport=transport,
            site=site,
            call=call,
            values=found,
            tool_filter=names,
            unread=unread,
        )

    def _mcp(
        self,
        *,
        hint: str | None,
        short: str,
        transport: str | None,
        site: ValueSite,
        call: ast.Call,
        values: dict[str, Any],
        tool_filter: Any,
        unread: list[str],
    ) -> ObjectBinding:
        url, headers = values.get("url"), values.get("headers")
        command, args, env = values.get("command"), values.get("args"), values.get("env")
        hosts: list[str] | None = None
        programs: list[str] | None = None
        if transport in {"sse", "streamable_http"}:
            hosts, host_unread = object_hosts(url)
            if host_unread:
                unread.append("its URL's host is not read")
        if transport == "stdio":
            programs, command_unread = object_command(command)
            if command_unread:
                unread.append("its command is not read")
        credentials, credentials_unread = object_credentials(headers=headers, url=url, env=env)
        if credentials_unread:
            unread.append("its headers or environment are not read")
        return ObjectBinding(
            name=hint,
            identity={
                "kind": "mcp_server",
                "class": short,
                "transport": transport,
                "host": hosts or None,
                "command": programs or None,
                "credential_sources": sorted(credentials, key=lambda item: json.dumps(item, sort_keys=True)),
                "tool_filter": tool_filter,
                "endpoint_sha256": object_digest(url, command, args),
            },
            location=f"{site.module.ref}:{call.lineno}",
            unread=tuple(dict.fromkeys(unread)),
        )

    def _as_tool(self, call: ast.Call, site: ValueSite, depth: int) -> Found:
        assert isinstance(call.func, ast.Attribute)
        where = f"{site.module.ref}:{call.lineno}"
        agent = self._sdk_agent(call.func.value, site, depth)
        if agent is None:
            return Unread(f"the agent `.as_tool(...)` wraps at {where} is not one this reader identifies")
        tool_expr = _keyword(call, "tool_name", 0)
        tool_name = self._literal(tool_expr, site, depth) if tool_expr is not None else None
        if tool_name is None:
            return Unread(f"the agent tool built at {where} has no literal tool_name")
        return self._agent_tool(call, site, agent, tool_name, skip={"tool_name"}, positional=2)

    def _adk_agent_tool(self, call: ast.Call, site: ValueSite, depth: int) -> Found:
        where = f"{site.module.ref}:{call.lineno}"
        built = self._construction(_keyword(call, "agent", 0), site, depth)
        name: str | None = None
        source: str | None = None
        agent_class = "Agent"
        if built is not None:
            agent_call, agent_site = built
            path = self._framework_path(agent_call.func, agent_site) or ""
            if path.startswith("google.adk.") and path.endswith("Agent"):
                name = _literal_keyword(agent_call, "name")
                source = agent_site.module.ref
                # ``RemoteA2aAgent`` is another service; ``LlmAgent`` runs here.
                short = path.rsplit(".", 1)[-1]
                agent_class = "Agent" if short in {"Agent", "LlmAgent"} else short
        if name is None:
            return Unread(f"the agent `AgentTool(...)` wraps at {where} is not one with a literal name")
        found = self._agent_tool(call, site, (name, source), name, skip={"agent"}, positional=1)
        if agent_class != "Agent":
            found.identity["agent_class"] = agent_class
        return found

    def _agent_tool(
        self,
        call: ast.Call,
        site: ValueSite,
        agent: tuple[str, str | None],
        tool_name: str,
        *,
        skip: set[str],
        positional: int,
    ) -> ObjectBinding:
        options = [
            keyword
            for keyword in call.keywords
            if keyword.arg not in skip and keyword.arg not in _DESCRIPTIVE
        ]
        extra = call.args[positional:]
        dumped = [
            [keyword.arg, ast.dump(keyword.value, include_attributes=False)] for keyword in options
        ] + [["#", ast.dump(arg, include_attributes=False)] for arg in extra]
        return ObjectBinding(
            name=tool_name,
            identity={
                "kind": "agent_tool",
                "agent": agent[0],
                "tool": tool_name,
                "options": sorted(keyword.arg or "**" for keyword in options) + ["#"] * len(extra),
                "options_sha256": hashlib.sha256(json.dumps(sorted(dumped)).encode()).hexdigest()
                if dumped
                else None,
            },
            location=f"{site.module.ref}:{call.lineno}",
            evidence={"agent_source": agent[1]} if agent[1] else {},
        )

    def _sdk_agent(self, expr: ast.expr, site: ValueSite, depth: int) -> tuple[str, str | None] | None:
        """The identity of the SDK agent ``expr`` is: the name its construction
        is assigned to, as the SDK reader names a module's agent."""

        for _ in range(MAX_DEPTH):
            if not isinstance(expr, ast.Name):
                return None
            bound = self._binding(site, expr, expr.id)
            if bound is None:
                return None
            if bound.kind == "param":
                argument = self._argument(bound.site, expr.id)
                if argument is None:
                    return None
                expr, site = argument
                continue
            statement = bound.statement
            if isinstance(bound.node, ast.alias):
                module = bound.site.module
                if bound.kind == "local":
                    assert isinstance(statement, ast.Import | ast.ImportFrom)
                    resolution = self.resolver.resolve_local_import(module, statement, bound.node, expr.id)
                else:
                    resolution = self.resolver.resolve(module, expr.id)
                name = next(
                    (step["name"] for step in reversed(resolution.steps) if step.get("binding") == "value"),
                    None,
                )
                if (
                    resolution.caveats
                    or resolution.module is None
                    or not isinstance(resolution.value, ast.Call)
                    or name is None
                    or not self._is_sdk_agent(resolution.value, resolution.module)
                ):
                    return None
                return name, resolution.module.ref
            if (
                isinstance(statement, ast.Assign | ast.AnnAssign)
                and isinstance(statement.value, ast.Call)
                and (statement.targets if isinstance(statement, ast.Assign) else [statement.target]) == [bound.node]
                and self._is_sdk_agent(statement.value, bound.site.module)
            ):
                return expr.id, bound.site.module.ref
            return None
        return None

    def _is_sdk_agent(self, call: ast.Call, module: PythonModule) -> bool:
        from agents_shipgate.inputs.openai_sdk_static import _denotes_agent, _SdkNames

        names = self._sdk_names.get(id(module.tree))
        if names is None:
            names = self._sdk_names[id(module.tree)] = _SdkNames(module.tree)
        return _denotes_agent(names, call)

    def _wrapped(self, call: ast.Call, site: ValueSite, depth: int) -> Found | None:
        """``function_tool(f)``: the function ``f`` is, read as a tool."""

        target = _keyword(call, "func", 0)
        where = f"{site.module.ref}:{call.lineno}"
        override_expr = _keyword(call, "name_override", None)
        override = self._literal(override_expr, site, depth) if override_expr is not None else None
        if override_expr is not None and override is None:
            return Unread(f"the function tool built at {where} has a name_override that is not a literal")
        if not isinstance(target, ast.Name | ast.Attribute):
            return None
        spelling = reference_spelling(target)
        assert spelling is not None
        head, _, rest = spelling.partition(".")
        bound = self._binding(site, target, head)
        if bound is None or bound.kind == "param":
            return None
        definition: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        module = bound.site.module
        resolution: Resolution | None = None
        if isinstance(bound.node, ast.FunctionDef | ast.AsyncFunctionDef) and not rest:
            definition = bound.node
        elif isinstance(bound.node, ast.alias):
            if bound.kind == "local":
                assert isinstance(bound.statement, ast.Import | ast.ImportFrom)
                resolution = self.resolver.resolve_local_import(module, bound.statement, bound.node, spelling)
            else:
                resolution = self.resolver.resolve(module, spelling)
            if resolution.caveats or resolution.definition is None or resolution.module is None:
                return None
            definition, module = resolution.definition, resolution.module
        if definition is None:
            return None
        if definition.decorator_list:
            return Unread(f"the function tool built at {where} wraps a decorated function")
        options = sorted(
            [keyword.arg or "**", ast.dump(keyword.value, include_attributes=False)]
            for keyword in call.keywords
            if keyword.arg not in {"func", "name_override", "description_override"}
        ) + [["#", ast.dump(arg, include_attributes=False)] for arg in call.args[1:]]
        digest = hashlib.sha256(json.dumps(options).encode()).hexdigest() if options else None
        return WrappedFunction(module, definition, override, where, resolution, digest)

    def _literal(self, expr: ast.expr | None, site: ValueSite, depth: int) -> str | None:
        """A string literal, written out or passed in by a caller."""

        for _ in range(MAX_DEPTH):
            if isinstance(expr, ast.Constant):
                return expr.value if isinstance(expr.value, str) and expr.value else None
            if not isinstance(expr, ast.Name):
                return None
            bound = self._binding(site, expr, expr.id)
            if bound is None or bound.kind != "param":
                return None
            argument = self._argument(bound.site, expr.id)
            if argument is None:
                return None
            expr, site = argument
        return None


def _keyword(call: ast.Call, name: str, position: int | None) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if position is not None and position < len(call.args) and not any(
        isinstance(arg, ast.Starred) for arg in call.args[: position + 1]
    ):
        return call.args[position]
    return None


def _literal_keyword(call: ast.Call, name: str) -> str | None:
    value = _keyword(call, name, None)
    if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
        return value.value
    return None


def _single_return(function: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.expr | None:
    """The value of a function's one unconditional ``return``, when that is
    all it returns: undecorated, not ``async``, no ``yield``, and the
    ``return`` is the body's last statement."""

    if function.decorator_list or isinstance(function, ast.AsyncFunctionDef):
        return None
    returns: list[ast.Return] = []
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Yield | ast.YieldFrom):
            return None
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        if isinstance(node, ast.Return):
            returns.append(node)
        stack.extend(ast.iter_child_nodes(node))
    if len(returns) != 1 or not function.body or function.body[-1] is not returns[0]:
        return None
    return returns[0].value


def object_display(payload: dict[str, Any]) -> str:
    """One line naming what an object binding is, for the text output."""

    identity = payload["identity"]
    kind = identity.get("kind")
    if kind == "mcp_server":
        parts = [f"MCP server ({identity.get('class')}"]
        parts[0] += f", {identity['transport']})" if identity.get("transport") else ")"
        if identity.get("host"):
            parts.append("host " + " or ".join(identity["host"]))
        if identity.get("command"):
            parts.append("runs " + " or ".join(identity["command"]))
        if identity.get("tool_filter") is not None:
            parts.append("tool filter " + json.dumps(identity["tool_filter"], sort_keys=True))
        parts.append(f"endpoint digest {str(identity.get('endpoint_sha256'))[:12]}")
        return "; ".join(parts)
    if kind == "agent_tool":
        return f"agent as tool: wraps {identity.get('agent_class', 'agent')} {identity.get('agent')}" + (
            f" (options {', '.join(identity['options'])})" if identity.get("options") else ""
        )
    if kind == "hosted_tool":
        return f"hosted tool {identity.get('tool')}" + (
            f" (arguments {', '.join(identity['arguments'])})" if identity.get("arguments") else ""
        )
    if kind == "built_in_tool":
        return f"built-in tool {identity.get('tool')}"
    return str(kind)


__all__ = [
    "ADK",
    "SDK",
    "Found",
    "ObjectBinding",
    "ObjectTools",
    "Unread",
    "WrappedFunction",
    "object_bindings_rule",
    "object_display",
    "reading_object_bindings",
]
