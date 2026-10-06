"""Static members of an agent's tools, handoffs or sub-agents expression (#909).

Both application readers used to read a ``tools=`` argument only when it was a
literal list or a name bound to one. An agent whose list is built by an
expression -- ``[*BASE, *([handoff] if wanted else [])]``, ``base + (extra or
[])``, ``[t for t in TOOLS if keep(t)]`` -- read as "uses a dynamic tools
expression", and none of its bindings were compared, even when every member
was a plain tool the reader understands.

This module resolves such an expression to the elements it can hold, without
importing or running anything:

- a list or tuple literal, splicing each ``*`` spread;
- ``a + b``;
- ``a if c else b`` and ``a or b``: every branch, each member *conditional* on
  the condition that selects it;
- ``[x for x in L if f]`` and ``filter(f, L)``: the members of ``L``,
  conditional on the filter -- an over-approximation, and said to be one;
- ``list(L)``, ``tuple(L)`` and ``sorted(L)``;
- a name bound once -- in the enclosing function or at module level -- to any
  of the above and never changed in place, followed through
  repository-local imports.

Whatever it cannot follow is an :class:`UnresolvedPart` with a reason and a
location. Resolving the rest never makes the expression complete.

The in-place change test (``read_only_use``) moved here from the OpenAI Agents
SDK reader, which used it for literal lists only (#879 review): every use of a
list must be one that cannot change it -- iterated, indexed, compared, tested,
spread, handed to a read-only builtin, to an agent's own list argument, or to
a function that treats its parameter the same way.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.static_inputs import active_static_input_snapshot
from agents_shipgate.inputs.common import list_input_directory
from agents_shipgate.inputs.python_imports import (
    MODULE_NOT_FOUND,
    ImportResolver,
    PythonModule,
    Resolution,
    ScopeIndex,
    _Stop,
    reference_spelling,
    reflective_access,
)
from agents_shipgate.inputs.python_static import dotted_name

if TYPE_CHECKING:
    from agents_shipgate.inputs.builder_calls import BuilderCalls, Invocation

MAX_DEPTH = 16
#: Members one expression may hold before the reader stops counting them.
MAX_MEMBERS = 1000
#: Expressions one resolution may visit: a list spread into itself many
#: times grows exponentially, so the work is bounded, not only the depth.
MAX_VISITS = 20000

#: Calls that read the values they are given and never change them.
READ_ONLY_CALLS = frozenset(
    {
        "len", "print", "repr", "str", "bool", "id", "hash", "isinstance", "type",
        "list", "tuple", "set", "frozenset", "sorted", "reversed", "enumerate", "iter",
        "any", "all", "sum", "min", "max", "zip", "map", "filter",
        "copy.copy", "copy.deepcopy", "json.dumps", "pprint", "pprint.pprint",
        "pprint.pformat", "pformat",
    }
)
LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})

#: ``(name, site) -> [(binding node, its statement)]``, empty when unbound.
BindingsAt = Callable[[str, ast.AST], list[tuple[ast.AST, ast.AST | None]]]
#: Whether passing a list to ``call`` (at a position or keyword) only reads it.
CallReads = Callable[[ast.Call, int | None, str | None], bool]
#: Whether ``call`` builds an agent that reads the list it is given as
#: ``keyword`` -- the framework's own constructors, supplied by its reader.
AgentReads = Callable[[ast.Call, str | None], bool]

#: Builtins whose result holds the same members as their one argument.
_SAME_MEMBERS = frozenset({"list", "tuple", "sorted"})
#: An agent's list arguments, and the attributes it keeps them under.
CAPABILITY_FIELDS = frozenset({"tools", "handoffs", "sub_agents", "mcp_servers"})
# Canonical SDK/ADK fields can be initialized or inspected even when omitted
# from a call. An unread owner may be the constructor class, so installing a
# descriptor here cannot establish that construction leaves borrowed lists alone.
_CONSTRUCTOR_FIELDS = frozenset({
    *CAPABILITY_FIELDS,
    "name", "instructions", "instruction", "description", "handoff_description",
    "prompt", "model", "model_settings", "mcp_config", "input_guardrails",
    "output_guardrails", "output_type", "hooks", "tool_use_behavior", "reset_tool_choice",
    "config_type", "parent_agent", "before_agent_callback", "after_agent_callback",
    "global_instruction", "static_instruction", "generate_content_config", "mode",
    "parallel_worker", "disallow_transfer_to_parent", "disallow_transfer_to_peers",
    "include_contents", "input_schema", "output_schema", "output_key", "planner",
    "code_executor", "before_model_callback", "after_model_callback", "on_model_error_callback",
    "before_tool_callback", "after_tool_callback", "on_tool_error_callback",
    "DEFAULT_MODEL", "DEFAULT_LIVE_MODEL", "_default_model", "_default_live_model",
    "_resolved_model", "_resolved_live_model",
})


@dataclass(frozen=True)
class ListMember:
    """One element an expression can hold, written where ``module`` says."""

    expr: ast.expr
    #: ``None``: the module being read. Otherwise the module an imported list
    #: is written in; its names are that module's, not the reader's.
    module: PythonModule | None
    #: Every condition that must hold for the member to be present; empty when
    #: it always is. Conditions are source text, read and never evaluated.
    conditions: tuple[str, ...] = ()
    #: Each list the member came through, outermost first: ``NAME (path:line)``.
    via: tuple[str, ...] = ()
    #: The invocation whose original AST supplied this member. It is source
    #: provenance, not an additional membership condition.
    invocation: Invocation | None = None


@dataclass(frozen=True)
class UnresolvedPart:
    reason: str
    location: str


@dataclass(frozen=True)
class ListResolution:
    members: tuple[ListMember, ...] = ()
    unresolved: tuple[UnresolvedPart, ...] = ()
    #: Whether reading it followed a name to its binding, even one that held
    #: nothing: a binding some other module could change.
    followed: bool = False

    @property
    def complete(self) -> bool:
        return not self.unresolved

    def __add__(self, other: ListResolution) -> ListResolution:
        # The same element under the same conditions is one member, however
        # many lists spread it: binding it twice binds nothing more.
        seen = {_identity(member) for member in self.members}
        added = []
        for member in other.members:
            if _identity(member) not in seen:
                seen.add(_identity(member))
                added.append(member)
        return ListResolution(
            self.members + tuple(added),
            self.unresolved + other.unresolved,
            self.followed or other.followed,
        )

    def under(self, condition: str) -> ListResolution:
        return ListResolution(
            tuple(
                ListMember(m.expr, m.module, (condition, *m.conditions), m.via, m.invocation)
                for m in self.members
            ),
            self.unresolved,
            self.followed,
        )

    def through(self, step: str) -> ListResolution:
        return ListResolution(
            tuple(
                ListMember(m.expr, m.module, m.conditions, (step, *m.via), m.invocation)
                for m in self.members
            ),
            self.unresolved,
            True,
        )


def bindings_at(scopes: ScopeIndex, module_bindings: dict[str, list[Any]]) -> BindingsAt:
    # ``from helpers import *`` may bind any name: none is proven unbound.
    star = any(isinstance(node, ast.alias) and node.name == "*" for node in scopes.parents)

    def found(name: str, site: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
        local = scopes.enclosing_bindings(evaluation_site(scopes, site), name)
        if local:
            return [(item, scopes.statement_of(item)) for item in local]
        module = [(item.node, item.statement) for item in module_bindings.get(name, [])]
        return module or ([(site, None)] if star else [])

    return found


def leaves_arguments_alone(call: ast.Call, bindings: BindingsAt) -> bool:
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
    if name in READ_ONLY_CALLS:
        head, _, rest = name.partition(".")
        found = bindings(head, call)
        if not found:
            return not rest
        if len(found) != 1:
            return False
        node, statement = found[0]
        if not isinstance(node, ast.alias):
            return False
        if isinstance(statement, ast.ImportFrom):
            # ``from pprint import pprint``.
            return not rest and not statement.level and f"{statement.module}.{node.name}" in READ_ONLY_CALLS
        # ``import json`` then ``json.dumps``.
        return bool(rest) and isinstance(statement, ast.Import) and node.name == head and node.asname is None
    if not (isinstance(call.func, ast.Attribute) and call.func.attr in LOG_METHODS):
        return False
    receiver = call.func.value
    if not isinstance(receiver, ast.Name):
        return False
    found = bindings(receiver.id, call)
    if len(found) != 1:
        return False
    node, statement = found[0]
    if isinstance(node, ast.alias):
        # ``logging.info(...)``.
        return isinstance(statement, ast.Import) and node.name == "logging" and receiver.id == "logging"
    value = getattr(statement, "value", None)
    # ``logger = logging.getLogger(__name__)``.
    return (
        isinstance(statement, ast.Assign | ast.AnnAssign)
        and isinstance(value, ast.Call)
        and (reference_spelling(value.func) or "").rsplit(".", 1)[-1] in {"getLogger", "get_logger"}
    )


def parameter_left_alone(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    bindings: BindingsAt,
    call_reads: CallReads | None = None,
) -> bool:
    """Whether every use of parameter ``name`` in ``function`` only reads it.

    The same test as a module list's own uses, one level deep: handing it on
    to any call but a read-only builtin or logging method is not a read.
    ``bindings`` answers for the function's own module.
    """

    parents = {child: node for node in ast.walk(function) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(function):
        if isinstance(node, ast.Global | ast.Nonlocal) and name in node.names:
            return False
        if isinstance(node, ast.Name) and node.id == name:
            if not isinstance(node.ctx, ast.Load):
                return False
            if not read_only_use(
                node,
                parents,
                call_reads or (lambda call, *_: leaves_arguments_alone(call, bindings)),
            ):
                return False
    return True


def read_only_use(
    node: ast.expr,
    parents: dict[ast.AST, ast.AST],
    call_reads: CallReads,
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
        return read_only_use(parent, parents, call_reads)
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
        called = isinstance(grand, ast.Call) and grand.func is parent
        if called:
            return parent.attr in {"count", "index", "copy", "get", "keys", "values", "items"}
        # ``helpers.TOOLS`` handed on: ``helpers`` is read, and the attribute's
        # own use decides. A call (``helpers.register(x)``) may change anything.
        return isinstance(parent.ctx, ast.Load) and read_only_use(parent, parents, call_reads)
    return False


@dataclass
class _View:
    """One module as this resolver reads it."""

    ref: str
    tree: ast.Module
    scopes: ScopeIndex
    bindings: dict[str, list[Any]]
    module: PythonModule | None
    #: Bindings some code changes in place: ``id(local binding)`` or
    #: ``("module", name)``.
    changed: set[object]
    #: Whether this is the module the agent is built in: only there do the
    #: framework's own constructions read the list they are handed.
    entry: bool = False
    #: Lines of the module's ``from x import *``, which may rebind any name.
    star_lines: tuple[int, ...] = ()
    #: Imports whose binding some code changes in place, wherever in the
    #: module: the list they import is changed, whatever name reaches it.
    changed_imports: list[tuple[ast.alias, ast.stmt]] = field(default_factory=list)
    #: ``bindings_at`` for this module, built once.
    lookup: BindingsAt | None = None
    #: Imported namespace paths visibly replaced, resolved at each store's
    #: evaluation site so two aliases of one namespace share the obligation.
    qualified_patches: set[str] = field(default_factory=set)
    #: A visible write may replace a builtin namespace getter too.
    namespace_builtins_changed: set[str] = field(default_factory=set)
    fresh_dictionaries: dict[tuple[int, bool], bool] = field(default_factory=dict)
    resolver: ImportResolver | None = None
    unprovided_roots: dict[str, bool] = field(default_factory=dict)
    agent_calls: frozenset[int] = frozenset()
    builder_calls: BuilderCalls | None = None
    module_agent_reads: Callable[[PythonModule, ast.Call, str | None], bool] | None = None
    returned_instances: dict[int, bool] = field(default_factory=dict)
    selected_instance_call: ast.Call | None = None
    constructor_reads: dict[tuple[Any, ...], bool] = field(default_factory=dict)


_NAMESPACE_ALIAS_LIMIT = 128
_UNREAD_NAMESPACE_ALIAS = "<unread_namespace_alias>"
_NAMESPACE_BUILTINS = {"vars", "getattr", "setattr", "delattr", "dict", "type", "object"}
_NAMESPACE_SETTERS = {
    "builtins.setattr", "builtins.delattr", "operator.setitem", "operator.delitem",
    *(f"builtins.{owner}.{method}" for owner in {"type", "object"}
      for method in {"__setattr__", "__delattr__"}),
}
_NAMESPACE_PRIMITIVES = {
    *(f"builtins.{name}" for name in _NAMESPACE_BUILTINS),
    *(f"builtins.{owner}.{method}" for owner in {"type", "object"}
      for method in {"__setattr__", "__delattr__"}),
    "operator.setitem", "operator.delitem", "operator.ior",
}


def _standard_operator(view: _View) -> bool:
    """Only an unprovided module can use the standard-library call model."""
    return _unprovided_root(view, "operator")


def _unprovided_root(view: _View, provider: str) -> bool:
    """Require captured absence before assigning an external import its role."""
    if provider not in view.unprovided_roots:
        view.unprovided_roots[provider] = False
        resolver = view.resolver
        if resolver is None or view.module is None or resolver._layout is None:
            return False
        try:
            resolver._absolute(view.module, provider)
        except _Stop as stop:
            if stop.reason != MODULE_NOT_FOUND or any(names is None for names in resolver._listings.values()):
                return False
        else:
            return False
        layout = resolver._layout
        root = resolver.scope_root
        for _ in layout.scope.split("/") if layout.scope else ():
            root = root.parent
        snapshot = active_static_input_snapshot()
        for base in resolver._import_roots():
            # Inspect each prefix so a missing root is distinguished from an
            # unread/linked root. A verifier cannot use an unbound external
            # directory's absence as lasting evidence.
            prefixes = [""] + ["/".join(base.split("/")[:i]) for i in range(1, len(base.split("/")) + 1)] if base else [""]
            previous: frozenset[str] | None = None
            for prefix in prefixes:
                name = prefix.rsplit("/", 1)[-1]
                directory = root / prefix
                if prefix and previous is not None and name not in previous:
                    if snapshot is not None:
                        if not snapshot.bind_dependency_absence(directory):
                            return False
                    else:
                        try:
                            directory.lstat()
                        except FileNotFoundError:
                            pass
                        except OSError:
                            return False
                        else:
                            return False
                    break
                names = layout.entries(prefix)
                if names is None or (prefix and name in layout.links(prefix.rpartition("/")[0])):
                    return False
                if snapshot is not None:
                    if directory != snapshot.root and not snapshot.contains(directory):
                        return False
                    try:
                        captured = frozenset(child.name for child in list_input_directory(directory))
                    except InputParseError:
                        return False
                    if captured != names:
                        return False
                previous = names
            else:
                if {provider, provider + ".py"} & (previous or frozenset()):
                    return False
                for name in (provider, provider + ".py"):
                    candidate = root / base / name
                    if snapshot is not None:
                        if not snapshot.bind_dependency_absence(candidate):
                            return False
                    else:
                        try:
                            candidate.lstat()
                        except FileNotFoundError:
                            pass
                        except OSError:
                            return False
                        else:
                            return False
        view.unprovided_roots[provider] = True
    return view.unprovided_roots[provider]


def _direct_agent_instance(view: _View, value: ast.Call) -> bool:
    if view.star_lines or id(value) not in view.agent_calls:
        return False
    qualified = _namespace_call_reference(view, value)
    return bool(
        qualified is not None and not qualified.startswith(_UNREAD_NAMESPACE_ALIAS)
        and qualified.rsplit(".", 1)[-1] not in {"clone", "replace"}
        and _unprovided_root(view, qualified.partition(".")[0])
    )


def _returned_agent_instance(view: _View, value: ast.Call) -> bool:
    """Only a bound source-local builder's direct Agent return owns this role."""
    if id(value) in view.returned_instances:
        return view.returned_instances[id(value)]
    if len(view.returned_instances) >= _NAMESPACE_ALIAS_LIMIT:
        return False
    view.returned_instances[id(value)] = False
    calls = view.builder_calls
    if calls is None or view.module is None or view.module_agent_reads is None:
        return False
    resolution = calls.resolve(view.module, value.func)
    module, function = resolution.module, resolution.definition
    if (
        not resolution.resolved or resolution.caveats or module is None
        or not isinstance(function, ast.FunctionDef) or function not in module.tree.body
    ):
        return False
    from agents_shipgate.inputs.builder_calls import CallLimit, CallSite, single_return

    try:
        calls.invoke(module, function, CallSite(view.module, value))
    except CallLimit:
        return False
    returned = single_return(function)
    if not isinstance(returned, ast.Call):
        return False
    scopes = calls.scopes(module)
    constructor = _View(module.ref, module.tree, scopes, module.bindings, module, set())
    constructor.star_lines = tuple(
        node.lineno for node in module.tree.body
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
    )
    constructor.lookup = bindings_at(scopes, module.bindings)
    constructor.resolver = view.resolver
    constructor.agent_calls = frozenset(
        id(node) for node in ast.walk(module.tree)
        if isinstance(node, ast.Call) and view.module_agent_reads(module, node, "tools")
    )
    if not _direct_agent_instance(constructor, returned):
        return False
    # Exclude only this exact invocation's instance-field replacement when
    # it occurs in the defining module. Its returned constructor must still
    # survive the complete census, including class/reflective/unknown writes.
    # No other factory result can recursively receive this role here.
    if module.tree is view.tree:
        constructor.selected_instance_call = value
    constructor.qualified_patches = _qualified_attribute_patches(constructor)
    established = _constructor_unchanged(constructor, returned) and _constructor_imports_unchanged(
        view, constructor, returned, value
    )
    view.returned_instances[id(value)] = established
    return established


def _agent_instance(view: _View, owner: ast.expr) -> bool:
    """Follow plain aliases of an existing finite Agent construction role."""
    seen: set[int] = set()
    while isinstance(owner, ast.Name):
        if id(owner) in seen or len(seen) >= _NAMESPACE_ALIAS_LIMIT:
            return False
        seen.add(id(owner))
        lookup = view.lookup or bindings_at(view.scopes, view.bindings)
        declarations = lookup(owner.id, owner)
        if len(declarations) != 1:
            return False
        _, statement = declarations[0]
        if not isinstance(statement, ast.Assign | ast.AnnAssign):
            return False
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            return False
        value = statement.value
        if isinstance(value, ast.Call):
            if value is view.selected_instance_call:
                return True
            return _direct_agent_instance(view, value) or _returned_agent_instance(view, value)
        if not isinstance(value, ast.Name):
            return False
        owner = value
    return False


def _namespace_call_reference(view: _View, call: ast.Call) -> str | None:
    spelling = reference_spelling(call.func)
    if spelling is None:
        return None
    root = spelling.partition(".")[0]
    if root in _NAMESPACE_BUILTINS:
        lookup = view.lookup or bindings_at(view.scopes, view.bindings)
        if not lookup(root, call.func):
            return f"builtins.{spelling}"
    references = _imported_references(view, spelling, call.func, follow_results=False)
    return next(iter(references)) if len(references) == 1 else None


def _namespace_dictionary_owner(view: _View, value: ast.expr) -> ast.expr | None:
    if isinstance(value, ast.Attribute) and value.attr == "__dict__":
        return value.value
    if not isinstance(value, ast.Call) or value.keywords:
        return None
    reference = _namespace_call_reference(view, value)
    if reference is None or reference in view.namespace_builtins_changed:
        return None
    if reference == "builtins.vars" and len(value.args) == 1:
        return value.args[0]
    if (
        reference == "builtins.getattr" and len(value.args) == 2
        and isinstance(value.args[1], ast.Constant) and value.args[1].value == "__dict__"
    ):
        return value.args[0]
    return None


def _fresh_dictionary(view: _View, value: ast.expr, *, allow_namespace: bool = False) -> bool:
    """A literal/new builtin dictionary cannot write a namespace's dictionary."""
    key = (id(value), allow_namespace)
    if key in view.fresh_dictionaries:
        return view.fresh_dictionaries[key]
    pending = [value]
    seen: set[int] = set()
    fresh = True
    while pending:
        item = pending.pop()
        if id(item) in seen or len(seen) + len(pending) >= _NAMESPACE_ALIAS_LIMIT:
            fresh = False
            break
        seen.add(id(item))
        if isinstance(item, ast.Dict):
            continue
        if allow_namespace and _namespace_dictionary_owner(view, item) is not None:
            continue
        if isinstance(item, ast.Call) and (
            _namespace_call_reference(view, item) == "builtins.dict"
            and "builtins.dict" not in view.namespace_builtins_changed
        ):
            continue
        if isinstance(item, ast.Name):
            lookup = view.lookup or bindings_at(view.scopes, view.bindings)
            declarations = [statement for _, statement in lookup(item.id, item)
                            if not (isinstance(statement, ast.AugAssign)
                                    and isinstance(statement.op, ast.BitOr))]
            if declarations and all(isinstance(statement, ast.Assign | ast.AnnAssign)
                                    and statement.value is not None for statement in declarations):
                pending.extend(statement.value for statement in declarations)
                continue
        fresh = False
        break
    view.fresh_dictionaries[key] = fresh
    return fresh


def _imported_references(
    view: _View, spelling: str, site: ast.AST, *, follow_results: bool = True,
) -> set[str]:
    """Keep imported namespace provenance through bounded plain assignments."""
    references: set[str] = set()
    pending = [(spelling, site)]
    visited: set[tuple[str, int]] = set()
    while pending:
        reference, at = pending.pop()
        key = (reference, id(at))
        if key in visited:
            continue
        if len(visited) >= _NAMESPACE_ALIAS_LIMIT:
            return references | {_UNREAD_NAMESPACE_ALIAS}
        visited.add(key)
        root, _, suffix = reference.partition(".")
        found = view.scopes.enclosing_bindings(evaluation_site(view.scopes, at), root)
        nodes = found or [binding.node for binding in view.bindings.get(root, [])]
        if not nodes:
            lookup = view.lookup or bindings_at(view.scopes, view.bindings)
            if not follow_results and root in _NAMESPACE_BUILTINS and not lookup(root, at):
                references.add(f"builtins.{reference}")
            else:
                references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{suffix}")
        for binding in nodes:
            statement = view.scopes.statement_of(binding)
            if isinstance(binding, ast.alias):
                if isinstance(statement, ast.ImportFrom) and not statement.level and statement.module:
                    imported = f"{statement.module}.{binding.name}"
                    if follow_results:
                        # An exported object may itself retain another module.
                        references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{suffix.rsplit('.', 1)[-1]}")
                elif isinstance(statement, ast.Import):
                    imported = binding.name if binding.asname else binding.name.split(".", 1)[0]
                    owner = f"{imported}.{suffix}".rpartition(".")[0]
                    if follow_results and suffix and owner not in {imported, binding.name}:
                        references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{suffix.rsplit('.', 1)[-1]}")
                else:
                    references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{suffix}")
                    continue
                references.add(f"{imported}.{suffix}" if suffix else imported)
            elif isinstance(statement, ast.Assign | ast.AnnAssign):
                value = statement.value
                if not follow_results and not isinstance(value, ast.Name | ast.Attribute):
                    references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{suffix}")
                    continue
                if value is not None and follow_results:
                    dictionary_owner = _namespace_dictionary_owner(view, value)
                    if dictionary_owner is not None:
                        value = dictionary_owner
                    if any(isinstance(item, ast.Call) for item in ast.walk(value)):
                        # A callee import is never proof of its result's owner.
                        references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{suffix.rsplit('.', 1)[-1]}")
                # A container or opaque producer may retain any namespace its
                # expression loads. Keep those possibilities only to withhold
                # a read; this never proves the carrier's result or a receiver.
                loaded = (
                    [value] if isinstance(value, ast.Name | ast.Attribute)
                    else [item for item in ast.walk(value) if isinstance(item, ast.Name | ast.Attribute)
                          and isinstance(item.ctx, ast.Load)] if value is not None else []
                )
                for item in loaded:
                    saved = reference_spelling(item)
                    if saved is None:
                        continue
                    if len(pending) + len(visited) >= _NAMESPACE_ALIAS_LIMIT:
                        return references | {_UNREAD_NAMESPACE_ALIAS}
                    pending.append((f"{saved}.{suffix}" if suffix else saved, item))
            elif (
                follow_results and isinstance(statement, ast.AugAssign)
                and isinstance(statement.op, ast.BitOr)
                and _fresh_dictionary(view, statement.target, allow_namespace=True)
            ):
                # A known dictionary's |= writes keys without replacing the
                # dictionary object; its original declarations own the keys.
                continue
            else:
                references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{suffix}")
    return references


def _constructor_unchanged(
    view: _View, call: ast.Call, *, qualified: str | None = None,
) -> bool:
    # An import's canonical spelling does not prove the callee stayed the
    # framework's constructor: a visible store may replace any prefix.
    spelling = qualified or reference_spelling(call.func)
    if view.module is not None and spelling is not None:
        if (
            _UNREAD_NAMESPACE_ALIAS in view.qualified_patches
            or f"{_UNREAD_NAMESPACE_ALIAS}.*" in view.qualified_patches
        ):
            return False
        fields = _CONSTRUCTOR_FIELDS | {item.arg for item in call.keywords if item.arg is not None}
        if any(
            patch.startswith(_UNREAD_NAMESPACE_ALIAS + ".") and (
                (name := patch.removeprefix(_UNREAD_NAMESPACE_ALIAS + ".")) in fields
                or name.startswith("_")
                or name.startswith("model_")
            )
            for patch in view.qualified_patches
        ):
            return False
        parts = spelling.split(".")
        if any(
            ".".join(parts[:length]) in view.module.attribute_patches
            for length in range(2, len(parts) + 1)
        ):
            return False
        if view.qualified_patches:
            references = {qualified} if qualified is not None else {
                spelling, *_imported_references(view, spelling, call.func)
            }
            for reference in references:
                parts = reference.split(".")
                if any(f"{_UNREAD_NAMESPACE_ALIAS}.{part}" in view.qualified_patches for part in parts):
                    return False
                if any(
                    ".".join(parts[:length]) + ".*" in view.qualified_patches
                    for length in range(1, len(parts) + 1)
                ):
                    return False
                if any(
                    ".".join(parts[:length]) in view.qualified_patches
                    for length in range(2, len(parts) + 1)
                ):
                    return False
                if any(patch.startswith(reference + ".") for patch in view.qualified_patches):
                    # Constructor implementation fields are not immutable.
                    return False
    return True


def _constructor_imports_unchanged(
    view: _View, constructor: _View, returned: ast.Call, invocation: ast.Call,
    *, checked_modules: frozenset[object] = frozenset(),
    additional_callers: tuple[_View, ...] = (),
    qualified: str | None = None,
) -> bool:
    """Census source-local import effects before granting a returned instance."""
    resolver, calls = view.resolver, view.builder_calls
    if resolver is None or calls is None or view.module is None or constructor.module is None:
        return False
    qualified = qualified or _namespace_call_reference(constructor, returned)
    if qualified is None or qualified.startswith(_UNREAD_NAMESPACE_ALIAS):
        return False
    try:
        above = resolver._above_scope()
        if above.unread or above.patched or above.tables:
            return False
        pending = [constructor.module, view.module, *(
            caller.module for caller in additional_callers if caller.module is not None
        )]
        if above.inscope:
            assert resolver._layout is not None
            for path in above.inscope:
                relative = PurePosixPath(path).relative_to(resolver._layout.scope)
                pending.append(resolver._patch_scan(resolver.scope_root / relative))
        seen: set[object] = set()
        while pending:
            module = pending.pop()
            if module.path in seen:
                continue
            if len(seen) >= _NAMESPACE_ALIAS_LIMIT or calls.dynamic_importer(module) is not None:
                return False
            seen.add(module.path)
            scopes = calls.scopes(module)
            other = _View(module.ref, module.tree, scopes, module.bindings, module, set())
            other.lookup = bindings_at(scopes, module.bindings)
            other.resolver = resolver
            other.star_lines = tuple(
                node.lineno for node in module.tree.body
                if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            )
            assert view.module_agent_reads is not None
            other.agent_calls = frozenset(
                id(node) for node in ast.walk(module.tree)
                if isinstance(node, ast.Call) and view.module_agent_reads(module, node, "tools")
            )
            if module.tree is view.tree:
                other.selected_instance_call = invocation
            other.qualified_patches = _qualified_attribute_patches(other)
            if module.path not in checked_modules and not _constructor_unchanged(other, returned, qualified=qualified):
                return False
            paths = resolver._enclosing_packages(module.path)
            for node in ast.walk(module.tree):
                if not isinstance(node, ast.Import | ast.ImportFrom):
                    continue
                imported, missing = resolver._imported_paths(module, node)
                for stop, spelling, _ in missing:
                    if (
                        stop.reason != MODULE_NOT_FOUND
                        or isinstance(node, ast.ImportFrom) and node.level
                        or not _unprovided_root(other, spelling.partition(".")[0])
                    ):
                        return False
                paths.extend(imported)
            pending.extend(resolver._patch_scan(path) for path in paths if path not in seen)
    except _Stop:
        return False
    return True


def _qualified_attribute_patches(view: _View, *, primitive_pass: int = 0) -> set[str]:
    """Retain visible namespace mutations without flattening lexical imports.

    Multiple possible import owners keep every possibility: uncertainty cannot
    prove that the constructor remained unchanged. This only withholds reads;
    it never resolves an otherwise unknown constructor into a framework call.
    """
    if view.module is None:
        return set()
    changed: set[str] = set()
    nodes = list(ast.walk(view.tree))

    def record(owner: ast.expr, attribute: str | None) -> None:
        dictionary_owner = _namespace_dictionary_owner(view, owner)
        if dictionary_owner is not None:
            owner = dictionary_owner
        loaded = (
            [owner] if isinstance(owner, ast.Name | ast.Attribute)
            else [item for item in ast.walk(owner) if isinstance(item, ast.Name | ast.Attribute)
                  and isinstance(item.ctx, ast.Load)]
        )
        references: set[str] = set()
        if any(isinstance(item, ast.Call) for item in ast.walk(owner)):
            references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{attribute or '*'}")
        for item in loaded:
            spelling = reference_spelling(item)
            if spelling is not None:
                references.update(_imported_references(
                    view, f"{spelling}.{attribute or '*'}", item
                ))
        if not references:
            references.add(f"{_UNREAD_NAMESPACE_ALIAS}.{attribute or '*'}")
        for reference in references:
            if reference.startswith(_UNREAD_NAMESPACE_ALIAS + "."):
                # An unread owner may be any namespace; a literal stored
                # field is still known. A computed field can replace any one.
                changed.add(f"{_UNREAD_NAMESPACE_ALIAS}.{attribute or '*'}")
            else:
                changed.add(reference.replace(".__dict__.", "."))

    def dictionary_fields(owner: ast.expr, arguments: list[ast.expr], keywords: list[ast.keyword]) -> None:
        if _fresh_dictionary(view, owner):
            return
        for argument in arguments:
            if isinstance(argument, ast.Dict):
                for key in argument.keys:
                    record(owner, key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None)
            else:
                record(owner, None)
        for keyword in keywords:
            record(owner, keyword.arg)

    dictionary_methods = {"__init__", "update", "clear", "pop", "popitem", "setdefault", "__setitem__", "__delitem__", "__ior__"}

    def dictionary_mutation(owner: ast.expr, method: str, arguments: list[ast.expr], keywords: list[ast.keyword]) -> None:
        if _fresh_dictionary(view, owner):
            return
        if method in {"__init__", "update", "__ior__"}:
            dictionary_fields(owner, arguments, keywords)
        elif method in {"clear", "popitem"} or not arguments:
            record(owner, None)
        else:
            key = arguments[0]
            record(owner, key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None)

    for node in nodes:
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del):
            if (
                isinstance(node.ctx, ast.Store)
                and isinstance(view.scopes.parents.get(node), ast.Assign | ast.AnnAssign)
                and node.attr in {"tools", "handoffs", "mcp_servers", "sub_agents"}
                and _agent_instance(view, node.value)
            ):
                # A plain capability-field replacement on a known instance
                # does not write its constructor class or a sibling's list.
                # The existing handle/list census still checks this instance.
                continue
            record(node.value, node.attr)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del):
            if _fresh_dictionary(view, node.value):
                continue
            key = node.slice
            if isinstance(key, ast.Constant) and not isinstance(key.value, str):
                continue
            record(node.value, key.value if isinstance(key, ast.Constant) else None)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr):
            dictionary_fields(node.target, [node.value], [])
        elif isinstance(node, ast.Call):
            reference = _namespace_call_reference(view, node)
            raw = reference_spelling(node.func)
            setter = raw is not None and raw.rsplit(".", 1)[-1] in {"setattr", "delattr", "__setattr__", "__delattr__"}
            unbound_dictionary = (
                reference is not None and reference.startswith("builtins.dict.")
                and reference.rsplit(".", 1)[-1] in dictionary_methods
            )
            if (setter or reference in _NAMESPACE_SETTERS or reference == "operator.ior" or unbound_dictionary) and (
                not node.args or isinstance(node.args[0], ast.Starred)
            ):
                # No established receiver position: never infer a mutation
                # owner from an unpacked payload or a missing argument.
                changed.add(f"{_UNREAD_NAMESPACE_ALIAS}.*")
                continue
            if len(node.args) >= 2 and (setter or reference in _NAMESPACE_SETTERS):
                key = node.args[1]
                if (setter and reference not in _NAMESPACE_SETTERS) or reference in view.namespace_builtins_changed or (
                    reference is not None and reference.startswith("operator.") and not _standard_operator(view)
                ):
                    changed.add(f"{_UNREAD_NAMESPACE_ALIAS}.*")
                else:
                    record(node.args[0], key.value if isinstance(key, ast.Constant)
                           and isinstance(key.value, str) else None)
            elif reference == "operator.ior" and node.args:
                if reference in view.namespace_builtins_changed or not _standard_operator(view):
                    changed.add(f"{_UNREAD_NAMESPACE_ALIAS}.*")
                else:
                    dictionary_fields(node.args[0], node.args[1:], node.keywords)
            elif unbound_dictionary:
                if "builtins.dict" in view.namespace_builtins_changed or not node.args:
                    changed.add(f"{_UNREAD_NAMESPACE_ALIAS}.*")
                else:
                    dictionary_mutation(node.args[0], reference.rsplit(".", 1)[-1], node.args[1:], node.keywords)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in dictionary_methods:
                dictionary_mutation(node.func.value, node.func.attr, node.args, node.keywords)
        elif isinstance(node, ast.Attribute) and node.attr in dictionary_methods:
            parent = view.scopes.parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node) and not _fresh_dictionary(view, node.value):
                # An exported mutator may later write any key.
                record(node.value, None)
    # A getter's spelling is trustworthy only if its actual namespace remains
    # unchanged. Unknown keys/mapping arguments retain namespace wildcards;
    # unrelated foreign/fresh dictionary fields do not poison builtin names.
    unstable = {
        primitive for primitive in _NAMESPACE_PRIMITIVES if any(
            patch in {_UNREAD_NAMESPACE_ALIAS, f"{_UNREAD_NAMESPACE_ALIAS}.*",
                      f"{_UNREAD_NAMESPACE_ALIAS}.{primitive.rsplit('.', 1)[-1]}"}
            or primitive == patch or primitive.startswith(patch + ".")
            or patch.startswith(primitive + ".")
            or any(patch == prefix + ".*" for prefix in (
                primitive.rsplit(".", 1)[0], primitive.split(".", 1)[0]
            ))
            for patch in changed
        )
    }
    if not unstable <= view.namespace_builtins_changed:
        if primitive_pass >= len(_NAMESPACE_PRIMITIVES):
            return changed | {_UNREAD_NAMESPACE_ALIAS}
        view.namespace_builtins_changed.update(unstable)
        view.fresh_dictionaries.clear()
        return _qualified_attribute_patches(view, primitive_pass=primitive_pass + 1)
    return changed


@dataclass
class ListCache:
    """What reading lists learns about modules, shared by every module one load reads.

    An imported module is indexed once however many agent files import it
    (#909 review). Keys hold the tree's id and whether the view is the
    module an agent is built in; the cache keeps every view, and so every
    tree, alive for as long as the ids are used.
    """

    views: dict[tuple[int, bool], _View] = field(default_factory=dict)
    memo: dict[tuple[Any, ...], ListResolution] = field(default_factory=dict)
    changes: dict[tuple[int, bool, int, str], int | None] = field(default_factory=dict)
    callees: dict[tuple[int, str, tuple], bool] = field(default_factory=dict)
    scopes: dict[int, ScopeIndex] = field(default_factory=dict)


class ListExpressions:
    """Resolve list expressions read in one module, following its imports.

    ``agent_reads`` tells the in-place change test which calls are the
    framework's own agent constructions, whose list arguments are reads.
    """

    def __init__(
        self,
        *,
        ref: str,
        tree: ast.Module,
        scopes: ScopeIndex,
        bindings: dict[str, list[Any]],
        module: PythonModule | None,
        resolver: ImportResolver | None,
        agent_reads: AgentReads,
        cache: ListCache | None = None,
        builder_calls: BuilderCalls | None = None,
        module_agent_reads: Callable[[PythonModule, ast.Call, str | None], bool] | None = None,
    ) -> None:
        self._resolver = resolver
        self._agent_reads = agent_reads
        self._module_agent_reads = module_agent_reads
        self._builder_calls = builder_calls
        self._invocation: Invocation | None = None
        self._field = "tools"
        self._checking_returns: set[tuple[int, str]] = set()
        self._cache = cache if cache is not None else ListCache()
        self._visits = 0
        self.entry = self._view(ref, tree, scopes, bindings, module, entry=True)

    @property
    def calls(self) -> BuilderCalls | None:
        if self._builder_calls is None and self._resolver is not None:
            from agents_shipgate.inputs.builder_calls import BuilderCalls

            self._builder_calls = BuilderCalls(self._resolver)
        return self._builder_calls

    # -- views -------------------------------------------------------------

    def _view(
        self,
        ref: str,
        tree: ast.Module,
        scopes: ScopeIndex,
        bindings: dict[str, list[Any]],
        module: PythonModule | None,
        *,
        entry: bool = False,
    ) -> _View:
        star_lines = tuple(
            statement.lineno
            for statement in tree.body
            if isinstance(statement, ast.ImportFrom)
            and any(alias.name == "*" for alias in statement.names)
        )
        key = (id(tree), entry)
        cached = self._cache.views.get(key)
        if cached is not None:
            return cached
        view = _View(ref, tree, scopes, bindings, module, set(), entry, star_lines)
        view.lookup = bindings_at(scopes, bindings)
        view.resolver = self._resolver
        view.builder_calls = self.calls
        view.module_agent_reads = self._module_agent_reads
        if module is not None and self._module_agent_reads is not None:
            view.agent_calls = frozenset(
                id(node) for node in ast.walk(tree)
                if isinstance(node, ast.Call) and self._module_agent_reads(module, node, "tools")
            )
        view.qualified_patches = _qualified_attribute_patches(view)
        self._cache.views[key] = view
        if entry:
            # Read-only forwarding may inspect another module while indexing
            # this one. Its entry identity must already be available.
            self.entry = view
        self._index_changes(view)
        return view

    def _foreign(self, module: PythonModule) -> _View:
        if module.tree is self.entry.tree:
            return self.entry
        cached = self._cache.views.get((id(module.tree), False))
        if cached is not None:
            return cached
        return self._view(module.ref, module.tree, self._scopes(module.tree), module.bindings, module)

    def _scopes(self, tree: ast.Module) -> ScopeIndex:
        scopes = self._cache.scopes.get(id(tree))
        if scopes is None:
            scopes = self._cache.scopes[id(tree)] = ScopeIndex(tree)
        return scopes

    def _index_changes(self, view: _View) -> None:
        """Mark every binding some code may change in place (#879 review)."""

        # A name the reader could read as a list: one bound to an expression
        # it follows. A name bound to anything else is never read, so a change
        # to it does not matter, and following its uses would cost a callee
        # read per call (#909 review).
        tracked = (
            {
                target.id
                for node in ast.walk(view.tree)
                if isinstance(node, ast.Assign | ast.AnnAssign) and _listish(node.value)
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(target, ast.Name)
            }
            | {
                (alias.asname or alias.name).split(".", 1)[0]
                for node in ast.walk(view.tree)
                if isinstance(node, ast.Import | ast.ImportFrom)
                for alias in node.names
                if alias.name != "*"
            }
            | {argument.arg for argument in ast.walk(view.tree) if isinstance(argument, ast.arg)}
        )
        if reflective_access(view.tree) is not None:
            # ``globals()["TOOLS"]``, ``vars()`` and ``sys.modules[__name__]``
            # reach a module list without spelling its name.
            view.changed.update(("module", name) for name in tracked)
        call_reads = self._call_reads(view)
        for node in ast.walk(view.tree):
            if isinstance(node, ast.Global):
                view.changed.update(("module", name) for name in node.names)
            elif isinstance(node, ast.Nonlocal):
                for name in node.names:
                    found = view.scopes.enclosing_bindings(node, name)
                    if found:
                        view.changed.add(id(found[0]))
            elif isinstance(node, ast.Attribute) and node.attr in CAPABILITY_FIELDS:
                # ``helper.tools.append(x)`` changes the list ``helper`` was
                # built with, which other agents may read too, here or in any
                # module importing it. ``helper.tools = [...]`` only replaces it.
                parent = view.scopes.parents.get(node)
                if isinstance(node.ctx, ast.Load):
                    changes = not read_only_use(node, view.scopes.parents, call_reads)
                else:
                    changes = isinstance(parent, ast.AugAssign)
                if changes:
                    view.changed.update(self._built_with(view, node.value))
            elif isinstance(node, ast.MatchMapping) and node.rest in tracked:
                # ``case {**tools}:`` rebinds ``tools`` where it matches.
                found = view.scopes.enclosing_bindings(
                    evaluation_site(view.scopes, node), node.rest
                )
                view.changed.update(id(binding) for binding in found)
                view.changed.add(("module", node.rest))
            elif isinstance(node, ast.Name) and node.id in tracked:
                parent = view.scopes.parents.get(node)
                if isinstance(node.ctx, ast.Load):
                    unchanged = read_only_use(node, view.scopes.parents, call_reads)
                else:
                    # A walrus rebinds the name in the scope around any
                    # comprehension it sits in, which no binding index sees.
                    unchanged = isinstance(node.ctx, ast.Store) and not isinstance(
                        parent, ast.AugAssign | ast.NamedExpr
                    )
                if not unchanged:
                    self._mark_changed(view, node)
        # Shared-list holders and namespace reflection can mark an imported
        # binding without loading that name directly. Publish those changes
        # to every other importer of the same list as well.
        for statement in ast.walk(view.tree):
            if isinstance(statement, ast.Import | ast.ImportFrom):
                for alias in statement.names:
                    name = alias.asname or alias.name.split(".", 1)[0]
                    if ("module", name) in view.changed or id(alias) in view.changed:
                        pair = (alias, statement)
                        if pair not in view.changed_imports:
                            view.changed_imports.append(pair)
        # Many uses of one imported Agent/function produce one ownership
        # obligation, not a fresh import per construction. Shared-list checks
        # query this index for each list; retaining duplicate AST pairs made
        # that otherwise bounded work quadratic in the number of agents.
        view.changed_imports = list(dict.fromkeys(view.changed_imports))

    def _mark_changed(self, view: _View, node: ast.Name) -> None:
        """Mark every binding a change at ``node`` may reach (#909 review).

        The name is looked up where Python evaluates it: a default, decorator
        or annotation in the scope around its definition, a class body's code
        in the class and then outside it, since the class may not have bound
        the name yet. A walrus binds around the comprehensions it sits in.
        """

        site = evaluation_site(view.scopes, node)
        found = view.scopes.enclosing_bindings(site, node.id)
        view.changed.add(id(found[0]) if found else ("module", node.id))
        for binding in found:
            view.changed.add(id(binding))
            statement = view.scopes.statement_of(binding)
            if isinstance(binding, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
                view.changed_imports.append((binding, statement))
        if not found:
            for item in view.bindings.get(node.id, []):
                if isinstance(item.node, ast.alias) and isinstance(item.statement, ast.Import | ast.ImportFrom):
                    view.changed_imports.append((item.node, item.statement))
        scope = _nearest_scope(view.scopes, site)
        walrus = isinstance(view.scopes.parents.get(node), ast.NamedExpr)
        if isinstance(scope, ast.ClassDef) or walrus:
            outer = view.scopes.enclosing_bindings(scope, node.id) if scope is not None else []
            view.changed.add(id(outer[0]) if outer else ("module", node.id))
            for binding in outer:
                view.changed.add(id(binding))
            if walrus:
                view.changed.add(("module", node.id))

    def _built_with(self, view: _View, receiver: ast.expr) -> set[object]:
        """The list bindings an agent named ``receiver`` was built with."""

        if not isinstance(receiver, ast.Name):
            return set()
        site = evaluation_site(view.scopes, receiver)
        local = view.scopes.enclosing_bindings(site, receiver.id)
        if local:
            statements = [view.scopes.statement_of(binding) for binding in local]
        else:
            statements = [item.statement for item in view.bindings.get(receiver.id, [])]
        keys: set[object] = set()
        for statement in statements:
            value = getattr(statement, "value", None)
            if isinstance(value, ast.Call):
                fields = [item.value for item in value.keywords if item.arg in CAPABILITY_FIELDS]
                keys |= shared_lists(view.scopes, view.bindings, fields)
        return keys

    def _call_reads(self, view: _View) -> CallReads:
        bindings = view.lookup or bindings_at(view.scopes, view.bindings)

        def reads(call: ast.Call, position: int | None, keyword: str | None) -> bool:
            if leaves_arguments_alone(call, bindings):
                return True
            # Another module's ``Agent`` may be its own class: only the module
            # the agent is built in says which constructions are the framework's.
            if self._reads_agent(view, call, keyword):
                return True
            return self._callee_leaves_alone(view, call, position, keyword)

        return reads

    def _reads_agent(self, view: _View, call: ast.Call, keyword: str | None) -> bool:
        if not _constructor_unchanged(view, call):
            return False
        recognized = (view.entry and self._agent_reads(call, keyword)) or bool(
            view.module is not None
            and self._module_agent_reads is not None
            and self._module_agent_reads(view.module, call, keyword)
        )
        if not recognized or view.module is None:
            # Pure-AST callers supply their own consumer predicate. File
            # adapters establish a scoped module before granting that role.
            return bool(recognized)
        # Ordinary constructions have the same import-effect obligation as a
        # returned instance. Reuse the existing view's own mutation census;
        # rebuilding it would discard its established instance roles.
        callers = [view]
        context = self._invocation
        seen: set[int] = set()
        while context is not None:
            if id(context) in seen or len(seen) >= _NAMESPACE_ALIAS_LIMIT:
                return False
            seen.add(id(context))
            caller = self._foreign(context.site.module)
            if all(caller.tree is not item.tree for item in callers):
                callers.append(caller)
            context = context.parent
        qualified = _namespace_call_reference(view, call)
        if (
            (qualified is None or qualified.startswith(_UNREAD_NAMESPACE_ALIAS))
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "clone" and isinstance(call.func.value, ast.Name)
        ):
            # The SDK's existing copy role requires one direct Agent
            # construction. Prove that constructor too, then census the
            # canonical method without treating a local instance as an import.
            lookup = view.lookup or bindings_at(view.scopes, view.bindings)
            found = lookup(call.func.value.id, call.func.value)
            owner = getattr(found[0][1], "value", None) if len(found) == 1 else None
            if not isinstance(owner, ast.Call) or not _direct_agent_instance(view, owner):
                return False
            constructor = _namespace_call_reference(view, owner)
            if constructor is None or not self._reads_agent(view, owner, keyword):
                return False
            qualified = constructor + ".clone"
        if qualified in {"dataclasses.replace", "copy.replace"}:
            # These functions invoke the receiver's construction or replacement
            # protocol. Their standard-library import cannot prove an arbitrary
            # receiver leaves a supplied capability list alone.
            if len(call.args) != 1 or not isinstance(call.args[0], ast.Name):
                return False
            lookup = view.lookup or bindings_at(view.scopes, view.bindings)
            found = lookup(call.args[0].id, call.args[0])
            owner = getattr(found[0][1], "value", None) if len(found) == 1 else None
            if not isinstance(owner, ast.Call) or not _direct_agent_instance(view, owner):
                return False
            if not self._reads_agent(view, owner, keyword):
                return False
        # Canonical import spelling cannot give a repository-local provider
        # the external constructor's list-consumption role.
        if (
            qualified is None or qualified.startswith(_UNREAD_NAMESPACE_ALIAS)
            or not _unprovided_root(view, qualified.partition(".")[0])
        ):
            return False
        # The dependency census compares one canonical constructor and its
        # keyword fields in immutable module views, not a particular return
        # value. Many equivalent ordinary reads can reuse that same proof.
        key = (qualified, tuple(sorted(item.arg for item in call.keywords if item.arg is not None)),
               tuple(id(caller.tree) for caller in callers))
        if key not in view.constructor_reads:
            view.constructor_reads[key] = False
            checked = frozenset(
                item.module.path for item in callers if item.module is not None
                and _constructor_unchanged(item, call, qualified=qualified)
            )
            if any(caller.module is not None and caller.module.path not in checked for caller in callers):
                return False
            view.constructor_reads[key] = _constructor_imports_unchanged(
                view, view, call, call, checked_modules=checked,
                additional_callers=tuple(callers[1:]),
                qualified=qualified,
            )
        return view.constructor_reads[key]

    def _callee_leaves_alone(
        self, view: _View, call: ast.Call, position: int | None, keyword: str | None
    ) -> bool:
        """Whether the function ``call`` names never changes the argument it passes."""

        spelling = reference_spelling(call.func)
        calls = self.calls
        if spelling is None or calls is None or view.module is None:
            return False
        resolution = calls.resolve(view.module, call.func)
        function = resolution.definition if resolution.resolved else None
        if function is None or function.decorator_list or resolution.caveats:
            # A decorator may hand the list to code this does not read.
            return False
        from agents_shipgate.inputs.builder_calls import CallLimit, CallSite

        parent = self._invocation
        if parent is not None and (
            parent.module.tree is not view.tree
            or _enclosing_function(view.scopes, evaluation_site(view.scopes, call)) is not parent.function
        ):
            # A shared-list census also reads unrelated importers; they are
            # not nested callers of the construction currently being read.
            parent = None
        try:
            invocation = calls.invoke(resolution.module, function, CallSite(view.module, call), parent=parent)
        except CallLimit:
            return False
        positional = [*function.args.posonlyargs, *function.args.args]
        if position is not None:
            if position >= len(positional) or any(
                isinstance(arg, ast.Starred) for arg in call.args[:position]
            ):
                return False
            parameter = positional[position].arg
        elif keyword in {arg.arg for arg in [*positional, *function.args.kwonlyargs]}:
            parameter = str(keyword)
        else:
            return False
        defining = resolution.module
        assert defining is not None
        key = (id(function), parameter, invocation.key)
        if key not in self._cache.callees:
            # A recursive forwarding cycle is not a read-only proof.
            self._cache.callees[key] = False
            foreign = self._foreign(defining)
            reads = self._call_reads(foreign)

            def safe_read(inner: ast.Call, position: int | None, field: str | None) -> bool:
                if field is not None and self._reads_agent(foreign, inner, field):
                    return not self._agent_result_changed(foreign, inner, field, borrowed=True)
                return reads(inner, position, field)

            previous, self._invocation = self._invocation, invocation
            try:
                self._cache.callees[key] = parameter_left_alone(
                    function,
                    parameter,
                    bindings_at(self._scopes(defining.tree), defining.bindings),
                    safe_read if self._module_agent_reads is not None else None,
                )
            finally:
                self._invocation = previous
        return self._cache.callees[key]

    def _agent_result_changed(
        self, view: _View, call: ast.Call, keyword: str, *, borrowed: bool = False
    ) -> bool:
        """Follow returned builder handles before proving a borrowed list read-only."""
        if reflective_access(view.tree) is not None:
            return True
        if (
            view.module is not None
            and self.calls is not None
            and self.calls.exported_elsewhere(view.module, call)
        ):
            return True
        parent = view.scopes.parents.get(call)
        if isinstance(parent, ast.Return):
            function = _enclosing_function(view.scopes, parent)
            calls = self.calls
            if function is None or calls is None or view.module is None:
                return True
            key = (id(function), keyword)
            if key in self._checking_returns or len(self._checking_returns) >= 4:
                return True
            self._checking_returns.add(key)
            try:
                census = calls.callers(view.module, function)
                return bool(census.limits) or any(
                    self._agent_result_changed(
                        self._foreign(site.module), site.call, keyword, borrowed=borrowed
                    )
                    for site in census.sites
                )
            finally:
                self._checking_returns.remove(key)
        if not isinstance(parent, ast.Assign | ast.AnnAssign) or parent.value is not call:
            return not isinstance(parent, ast.Expr)
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            return True
        target = targets[0]
        lookup = view.lookup or bindings_at(view.scopes, view.bindings)
        owner = lookup(target.id, target)
        for node in ast.walk(view.tree):
            if not (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id == target.id
                and lookup(node.id, node) == owner
            ):
                continue
            use = view.scopes.parents.get(node)
            if isinstance(use, ast.Return):
                # Follow ``agent = Agent(...); return agent`` with its actual
                # source scope, never a synthetic AST lacking lexical parents.
                function = _enclosing_function(view.scopes, use)
                calls = self.calls
                if function is None or calls is None or view.module is None:
                    return True
                key = (id(function), keyword)
                if key in self._checking_returns or len(self._checking_returns) >= 4:
                    return True
                self._checking_returns.add(key)
                try:
                    census = calls.callers(view.module, function)
                    if census.limits or any(
                        self._agent_result_changed(
                            self._foreign(site.module), site.call, keyword, borrowed=borrowed
                        )
                        for site in census.sites
                    ):
                        return True
                finally:
                    self._checking_returns.remove(key)
            elif isinstance(use, ast.Attribute):
                if use.attr == "__dict__":
                    return True
                if use.attr == keyword:
                    if isinstance(view.scopes.parents.get(use), ast.AugAssign):
                        return True
                    if not borrowed and not isinstance(use.ctx, ast.Load):
                        return True
                    if isinstance(use.ctx, ast.Load) and not read_only_use(
                        use, view.scopes.parents, self._call_reads(view)
                    ):
                        return True
                elif isinstance(use.ctx, ast.Load) and not read_only_use(
                    node, view.scopes.parents, self._call_reads(view)
                ):
                    return True
            elif isinstance(use, ast.Call) and reference_spelling(use.func) in {
                "getattr",
                "setattr",
                "delattr",
                "vars",
            }:
                return True
            elif isinstance(use, ast.Assign | ast.AnnAssign | ast.NamedExpr):
                # An alias can subsequently reach the same mutable list.
                return True
            elif not read_only_use(node, view.scopes.parents, self._call_reads(view)):
                return True
        return False

    # -- resolution --------------------------------------------------------

    def constructor_changed(self, call: ast.Call, invocation: Invocation | None) -> bool:
        """A bound construction must retain its constructor even for literals."""
        if invocation is None:
            return False
        previous, self._invocation = self._invocation, invocation
        try:
            return not self._reads_agent(self.entry, call, "tools")
        finally:
            self._invocation = previous

    def construction_changed(self, invocation: Invocation | None, field: str) -> bool:
        """Guard an entire construction, including fields omitted by its source."""
        return invocation is not None and self._agent_result_changed(
            self._foreign(invocation.site.module), invocation.site.call, field
        )

    def resolve(
        self, expr: ast.expr | None, *, invocation: Invocation | None = None
    ) -> ListResolution:
        """The members ``expr``, read in the entry module, can hold."""

        if expr is None:
            return ListResolution()
        self._visits = 0
        previous, self._invocation = self._invocation, invocation
        previous_field, self._field = self._field, self._capability_field(expr)
        try:
            if invocation is not None and self._agent_result_changed(
                self._foreign(invocation.site.module),
                invocation.site.call,
                self._capability_field(expr),
            ):
                result = self._stop(
                    self.entry,
                    expr,
                    "the caller's returned agent handle may change its capability list",
                )
            else:
                result = self._resolve(expr, self.entry, 0, frozenset())
        finally:
            self._invocation = previous
            self._field = previous_field
        if invocation is not None:
            for condition in invocation.conditions:
                result = result.under(condition)
        if len(result.members) > MAX_MEMBERS:
            return self._stop(
                self.entry,
                expr,
                f"the list holds more than {MAX_MEMBERS} members, more than the reader follows",
            )
        return result

    def _capability_field(self, expr: ast.expr) -> str:
        parent = self.entry.scopes.parents.get(expr)
        return (
            parent.arg
            if isinstance(parent, ast.keyword) and parent.arg in CAPABILITY_FIELDS
            else "tools"
        )

    def _where(self, view: _View, node: ast.AST) -> str:
        return f"{view.ref}:{getattr(node, 'lineno', '?')}"

    def _stop(self, view: _View, node: ast.AST, reason: str) -> ListResolution:
        return ListResolution(unresolved=(UnresolvedPart(reason, self._where(view, node)),))

    def _member(self, view: _View, node: ast.expr) -> ListResolution:
        return ListResolution(
            members=(
                ListMember(
                    node, None if view is self.entry else view.module, invocation=self._invocation
                ),
            )
        )

    def _resolve(self, node: ast.expr, view: _View, depth: int, seen: frozenset) -> ListResolution:
        self._visits += 1
        if self._visits > MAX_VISITS:
            return self._stop(view, node, "the expression is larger than the reader follows")
        if depth > MAX_DEPTH:
            return self._stop(view, node, "the expression nests further than the reader follows")
        if isinstance(node, ast.List | ast.Tuple):
            result = ListResolution()
            for item in node.elts:
                if isinstance(item, ast.Starred):
                    result += self._resolve(item.value, view, depth + 1, seen)
                else:
                    result += self._member(view, item)
            return result
        if isinstance(node, ast.Constant) and node.value is None:
            return ListResolution()
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._resolve(node.left, view, depth + 1, seen) + self._resolve(
                node.right, view, depth + 1, seen
            )
        if isinstance(node, ast.IfExp):
            return self._choice(node, view, depth, seen)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            return self._either(node, view, depth, seen)
        if isinstance(node, ast.ListComp | ast.GeneratorExp):
            return self._filtered_comprehension(node, view, depth, seen)
        if isinstance(node, ast.Call):
            return self._call(node, view, depth, seen)
        if isinstance(node, ast.Name):
            return self._name(node, view, depth, seen)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, view, depth, seen)
        return self._stop(view, node, f"`{_source(node)}` is not an expression the reader follows")

    def _choice(self, node: ast.IfExp, view: _View, depth: int, seen: frozenset) -> ListResolution:
        if isinstance(node.test, ast.Constant):
            branch = node.body if node.test.value else node.orelse
            return self._resolve(branch, view, depth + 1, seen)
        test = _condition(node.test)
        return self._resolve(node.body, view, depth + 1, seen).under(f"`{test}`") + self._resolve(
            node.orelse, view, depth + 1, seen
        ).under(f"not `{test}`")

    def _either(self, node: ast.BoolOp, view: _View, depth: int, seen: frozenset) -> ListResolution:
        """``a or b or c``: the first operand that is not empty, else the last."""

        result = ListResolution()
        reached: tuple[str, ...] = ()
        last = len(node.values) - 1
        for index, value in enumerate(node.values):
            part = self._resolve(value, view, depth + 1, seen)
            fixed = part.complete and all(not member.conditions for member in part.members)
            if index == last or _always_true(value, view):
                # The last operand, or one that is true however empty its
                # members are (a ``filter`` object, a generator): the value.
                return result + _all_under(part, reached)
            if fixed and part.members:
                # Known not empty: it is the value, and nothing after it is reached.
                return result + _all_under(part, reached)
            if fixed:
                # Known empty: never the value.
                continue
            text = _condition(value)
            result += _all_under(part, (*reached, f"`{text}` is not empty"))
            reached = (*reached, f"`{text}` is empty")
        return result

    def _filtered_comprehension(
        self, node: ast.ListComp | ast.GeneratorExp, view: _View, depth: int, seen: frozenset
    ) -> ListResolution:
        generators = node.generators
        if not (
            len(generators) == 1
            and not generators[0].is_async
            and isinstance(generators[0].target, ast.Name)
            and isinstance(node.elt, ast.Name)
            and node.elt.id == generators[0].target.id
        ):
            return self._stop(view, node, "a comprehension that builds new elements is not followed")
        part = self._resolve(generators[0].iter, view, depth + 1, seen)
        if not generators[0].ifs:
            return part
        kept = " and ".join(_condition(test) for test in generators[0].ifs)
        return part.under(f"the filter `{kept}` keeps it")

    def _call(self, node: ast.Call, view: _View, depth: int, seen: frozenset) -> ListResolution:
        func = node.func
        builtin = (
            func.id
            if isinstance(func, ast.Name) and not (view.lookup or bindings_at(view.scopes, view.bindings))(func.id, func)
            else None
        )
        if builtin in _SAME_MEMBERS and len(node.args) == 1 and not node.keywords:
            return self._resolve(node.args[0], view, depth + 1, seen)
        if builtin == "filter" and len(node.args) == 2 and not node.keywords:
            predicate, iterable = node.args
            part = self._resolve(iterable, view, depth + 1, seen)
            if isinstance(predicate, ast.Constant) and predicate.value is None:
                return part
            return part.under(f"the filter `{_condition(predicate)}` keeps it")
        return self._stop(view, node, f"a call to `{_source(func)}`, whose result is not read")

    def _name(self, node: ast.Name, view: _View, depth: int, seen: frozenset) -> ListResolution:
        name = node.id
        local = view.scopes.enclosing_bindings(evaluation_site(view.scopes, node), name)
        if local:
            if len(local) != 1:
                return self._stop(view, node, f"`{name}` is bound more than once in its function")
            binding = local[0]
            if isinstance(binding, ast.arg):
                origin = (
                    self._invocation.argument(binding) if self._invocation is not None else None
                )
                if origin is not None:
                    if id(binding) in view.changed:
                        return self._stop(
                            view, node, f"`{name}` may be changed in place by its builder"
                        )
                    defining, supplied, context = origin
                    default = next(
                        (
                            value
                            for value in self._invocation.arguments
                            if value.parameter is binding and value.default
                        ),
                        None,
                    )
                    if default is not None and self.calls is not None:
                        # A mutable Python default is one object shared by
                        # every omitted argument, unlike a fresh caller list.
                        from agents_shipgate.inputs.builder_calls import CallLimit, bind_call

                        census = self.calls.callers(
                            self._invocation.module, self._invocation.function
                        )
                        changed = bool(census.limits)
                        for site in census.sites:
                            try:
                                omitted = any(
                                    value.parameter is binding and value.default
                                    for value in bind_call(self._invocation.function, site.call)
                                )
                            except CallLimit:
                                changed = True
                                continue
                            if omitted and self._agent_result_changed(
                                self._foreign(site.module), site.call, self._field, borrowed=True
                            ):
                                changed = True
                        if changed:
                            return self._stop(
                                view,
                                node,
                                f"`{name}` has a shared default that another caller may change",
                            )
                    key = (id(binding), self._invocation.key)
                    if key in seen:
                        return self._stop(view, node, f"`{name}` is passed back to itself")
                    previous, self._invocation = self._invocation, context
                    try:
                        result = self._resolve(
                            supplied, self._foreign(defining), depth + 1, seen | {key}
                        )
                    finally:
                        self._invocation = previous
                    return result.through(f"{name} supplied at {previous.site.location}")
                function = _enclosing_function(view.scopes, binding)
                where = f" of `{function.name}`" if function is not None else ""
                return self._stop(
                    view, node, f"`{name}` is a parameter{where}, so its value comes from a caller"
                )
            statement = view.scopes.statement_of(binding)
            if isinstance(binding, ast.alias) and isinstance(
                statement, ast.Import | ast.ImportFrom
            ):
                if id(binding) in view.changed:
                    return self._stop(
                        view, statement, f"`{name}` may be changed in place after it is imported"
                    )
                return self._imported_local(node, binding, statement, view, depth, seen)
            return self._assigned(
                node, binding, statement, view, depth, seen, changed=id(binding) in view.changed
            )
        bindings = view.bindings.get(name, [])
        if not bindings:
            return self._stop(view, node, f"`{name}` is not bound where the list is read")
        if len(bindings) != 1 or not bindings[0].top_level:
            return self._stop(
                view, node, f"`{name}` is bound more than once, or conditionally, in {view.ref}"
            )
        binding = bindings[0]
        rebinding = _star_after(view, binding.statement)
        if rebinding is not None:
            return self._stop(
                view,
                node,
                f"`{name}` may be rebound by the wildcard import at {view.ref}:{rebinding}",
            )
        if isinstance(binding.node, ast.alias):
            return self._imported(node, name, view, depth, seen)
        return self._assigned(
            node,
            binding.node,
            binding.statement,
            view,
            depth,
            seen,
            changed=("module", name) in view.changed,
        )

    def _assigned(
        self,
        node: ast.Name,
        binding: ast.AST,
        statement: ast.AST | None,
        view: _View,
        depth: int,
        seen: frozenset,
        *,
        changed: bool,
    ) -> ListResolution:
        name = node.id
        if not (
            isinstance(binding, ast.Name)
            and isinstance(statement, ast.Assign | ast.AnnAssign)
            and statement.value is not None
            and _single_target(statement) == name
        ):
            kind = (
                "a function"
                if isinstance(binding, ast.FunctionDef | ast.AsyncFunctionDef)
                else "not a list"
            )
            return self._stop(view, node, f"`{name}` is {kind}, not a list of tools")
        if _conditional_between(view.scopes, statement, node):
            return self._stop(
                view, statement, f"`{name}` is bound only under a condition or in a loop"
            )
        if changed:
            return self._stop(
                view, statement, f"`{name}` may be changed in place after it is built"
            )
        if view.module is not None and statement in view.tree.body:
            limit = self._shared_list_limit(view.module, name, view, node)
            if limit is not None:
                return limit
        key = (id(view.tree), id(statement))
        if key in seen:
            return self._stop(view, statement, f"`{name}` refers to itself")
        step = f"{name} ({view.ref}:{statement.lineno})"
        return self._value_of(key, statement.value, view, depth, seen).through(step)

    def _value_of(
        self, key: tuple[int, int], value: ast.expr, view: _View, depth: int, seen: frozenset
    ) -> ListResolution:
        """A name's value, read once however many lists spread it."""

        memo = (key[0], view.entry, key[1], self._invocation.key if self._invocation else ())
        cached = self._cache.memo.get(memo)
        if cached is None:
            cached = self._resolve(value, view, depth + 1, seen | {key})
            self._cache.memo[memo] = cached
        return cached

    def _imported(self, node: ast.expr, spelling: str, view: _View, depth: int, seen: frozenset) -> ListResolution:
        if self._resolver is None or view.module is None:
            return self._stop(view, node, f"`{spelling}` is imported, and imports are not followed here")
        resolution = self._resolver.resolve(view.module, spelling)
        return self._from_resolution(node, spelling, resolution, view, depth, seen)

    def _imported_local(
        self,
        node: ast.Name,
        alias: ast.alias,
        statement: ast.Import | ast.ImportFrom,
        view: _View,
        depth: int,
        seen: frozenset,
    ) -> ListResolution:
        if self._resolver is None or view.module is None:
            return self._stop(view, node, f"`{node.id}` is imported, and imports are not followed here")
        resolution = self._resolver.resolve_local_import(view.module, statement, alias, node.id)
        return self._from_resolution(node, node.id, resolution, view, depth, seen)

    def _from_resolution(self, node, spelling, resolution, view, depth, seen) -> ListResolution:
        if resolution.definition is not None:
            return self._stop(view, node, f"`{spelling}` is a function, not a list of tools")
        value, defining = resolution.value, resolution.module
        if value is None or defining is None:
            return self._stop(view, node, f"`{spelling}` is not resolved: {resolution.detail}")
        if resolution.caveats:
            return self._stop(
                view, node, f"`{spelling}` is not established: {'; '.join(resolution.caveats)}"
            )
        foreign = self._foreign(defining)
        name = next(
            (step["name"] for step in reversed(resolution.steps) if step.get("binding") == "value"),
            None,
        )
        if name is None:
            return self._stop(view, node, f"`{spelling}` does not end at an assignment")
        bindings = foreign.bindings.get(name, [])
        if len(bindings) != 1 or not bindings[0].top_level or ("module", name) in foreign.changed:
            return self._stop(
                view, node, f"`{name}` in {foreign.ref} may be changed in place or rebound"
            )
        rebinding = _star_after(foreign, bindings[0].statement)
        if rebinding is not None:
            return self._stop(
                view,
                node,
                f"`{name}` may be rebound by the wildcard import at {foreign.ref}:{rebinding}",
            )
        head = spelling.split(".", 1)[0]
        if ("module", head) in view.changed:
            return self._stop(view, node, f"`{head}` may be changed in place in {view.ref}")
        limit = self._shared_list_limit(defining, name, view, node)
        if limit is not None:
            return limit
        for other in [view, *self._chain_views(resolution, defining, view)]:
            changer = self._changed_through(other, defining, name)
            if changer is not None:
                return self._stop(
                    view,
                    node,
                    f"`{name}` in {foreign.ref} may be changed in place at {other.ref}:{changer}",
                )
        statement = bindings[0].statement
        key = (id(foreign.tree), id(statement))
        if key in seen:
            return self._stop(view, node, f"`{spelling}` refers to itself")
        step = f"{name} ({foreign.ref}:{statement.lineno})"
        return self._value_of(key, value, foreign, depth, seen).through(step)

    def _shared_list_limit(
        self, defining: PythonModule, name: str, view: _View, node: ast.expr
    ) -> ListResolution | None:
        calls = self.calls
        if calls is None:
            return None
        from agents_shipgate.inputs.builder_calls import CallLimit

        try:
            for module in calls.borrowers(defining, name):
                dynamic = calls.dynamic_importer(module)
                if dynamic is not None:
                    return self._stop(
                        view,
                        node,
                        f"`{name}` has unread dynamic import machinery at {module.ref}:{dynamic.lineno}",
                    )
                other = self._foreign(module)
                changer = self._changed_through(other, defining, name)
                if changer is not None:
                    return self._stop(
                        view,
                        node,
                        f"`{name}` in {defining.ref} may be changed in place at {other.ref}:{changer}",
                    )
        except CallLimit as exc:
            return self._stop(view, node, f"`{name}` has an incomplete shared-list census: {exc}")
        return None

    def _chain_views(self, resolution: Resolution, defining: PythonModule, view: _View) -> list[_View]:
        """The other modules importing the list runs: each one the import passes
        through, and each package above the defining module, which runs first.

        A change from a module the import never passes through is not looked for.
        """

        assert self._resolver is not None
        refs = {step["path"] for step in resolution.steps if step.get("binding") == "import"}
        packages = PurePosixPath(defining.ref).parts[:-1]
        refs.update(f"{'/'.join(packages[:depth])}/__init__.py" for depth in range(1, len(packages) + 1))
        views = []
        for ref in sorted(refs - {defining.ref, view.ref}):
            path = self._resolver.scope_root / ref
            if not path.is_file():
                continue
            try:
                views.append(self._foreign(self._resolver.module(path)))
            except _Stop:
                continue
        return views

    def _changed_through(self, view: _View, defining: PythonModule, name: str) -> int | None:
        """The line of an import ``view`` changes in place that reaches ``defining``.

        ``from tools import BASE as B; B.append(x)`` in a sibling function, or
        ``import tools; tools.BASE.append(x)``, changes the list another
        spelling of it reads. Any import of the defining module, or of a name
        in it, whose binding is changed counts, which may also count a
        different list of that module.
        """

        if self._resolver is None or view.module is None:
            return None
        key = (id(view.tree), view.entry, id(defining.tree), name)
        if key not in self._cache.changes:
            self._cache.changes[key] = self._first_change_reaching(view, defining, name)
        return self._cache.changes[key]

    def _first_change_reaching(self, view: _View, defining: PythonModule, name: str) -> int | None:
        """An import changed in ``view`` that is the list, or the module holding it."""

        assert self._resolver is not None and view.module is not None
        from agents_shipgate.inputs.python_imports import (
            MODULE_NOT_FOUND,
            NAME_NOT_DEFINED,
            NOT_A_FUNCTION,
            NOT_BOUND,
        )

        names, _ = (
            self.calls.borrower_spellings(defining, name)
            if self.calls is not None
            else ((name,), frozenset())
        )
        for alias, statement in view.changed_imports:
            local = alias.asname or alias.name.split(".", 1)[0]
            direct = self._resolver.resolve_local_import(view.module, statement, alias, local)
            if direct.module is defining and _value_name(direct) == name:
                return statement.lineno
            if (
                direct.module is not None
                and not direct.caveats
                and isinstance(direct.value, ast.List | ast.Tuple)
                and all(
                    isinstance(member, ast.Constant)
                    or (
                        (reference := reference_spelling(member)) is not None
                        and (member_resolution := self._resolver.resolve(direct.module, reference)).resolved
                        and not member_resolution.caveats
                    )
                    for member in direct.value.elts
                )
            ):
                # A different fresh literal of functions/constants retains no
                # list holder or namespace. Do not confuse its assigned-value
                # NOT_A_FUNCTION result with a module namespace. Unresolved or
                # retained list members still take the conservative route.
                continue
            possible = self.calls is not None and self.calls.import_may_share(
                view.module, defining, name, statement, alias
            )
            if not possible and self.calls is not None:
                continue
            # ``from tools import BASE as B`` is the list; ``import tools`` is
            # the module holding it. Re-exports can rename BASE, or retain a
            # namespace under bridge.lib; inspect those spellings as well.
            prefixes = {local}
            prefixes.update(
                spelling
                for node in ast.walk(view.tree)
                if isinstance(node, ast.Attribute)
                and (spelling := reference_spelling(node)) is not None
                and spelling.startswith(f"{local}.")
            )
            spellings = {
                local,
                *(f"{prefix}.{export}" for prefix in prefixes for export in names),
                *prefixes,
            }
            for spelling in sorted(spellings):
                if statement in view.tree.body:
                    resolution = self._resolver.resolve(view.module, spelling)
                else:
                    resolution = self._resolver.resolve_local_import(
                        view.module, statement, alias, spelling
                    )
                if resolution.module is defining and _value_name(resolution) == name:
                    return statement.lineno
                if possible and resolution.reason == NOT_A_FUNCTION:
                    # A namespace can reach another namespace under a renamed
                    # export. This imported binding was marked as possibly
                    # mutated, including opaque consumers and computed access.
                    return statement.lineno
                if (
                    possible
                    and resolution.reason is not None
                    and resolution.reason not in {NOT_BOUND, NAME_NOT_DEFINED, NOT_A_FUNCTION}
                ):
                    if (
                        resolution.reason == MODULE_NOT_FOUND
                        and self.calls is not None
                        and self.calls.another_library(statement, alias)
                    ):
                        continue
                    # An ambiguous/unread changed import may be this list.
                    # Failure to establish its identity is not proof it cannot
                    # mutate a holder elsewhere.
                    return statement.lineno
        return None

    def _attribute(self, node: ast.Attribute, view: _View, depth: int, seen: frozenset) -> ListResolution:
        spelling = reference_spelling(node)
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in {"self", "cls"}:
            return self._stop(view, node, f"`{_source(node)}` is an attribute of the object, set elsewhere")
        if spelling is None or not isinstance(root, ast.Name):
            return self._stop(view, node, f"`{_source(node)}` is not an expression the reader follows")
        local = view.scopes.enclosing_bindings(evaluation_site(view.scopes, node), root.id)
        if local:
            # The function's own ``root``, not the module's: a parameter, a
            # local value, or an import made in the function.
            binding = local[0]
            statement = view.scopes.statement_of(binding)
            if (
                len(local) == 1
                and isinstance(binding, ast.alias)
                and isinstance(statement, ast.Import | ast.ImportFrom)
                and id(binding) not in view.changed
                and self._resolver is not None
                and view.module is not None
            ):
                resolution = self._resolver.resolve_local_import(view.module, statement, binding, spelling)
                return self._from_resolution(node, spelling, resolution, view, depth, seen)
            return self._stop(
                view, node, f"`{root.id}` is bound in its function, so `{spelling}` is not the module's"
            )
        return self._imported(node, spelling, view, depth, seen)


class Conditions:
    """Which names a list holds only under a condition.

    Each alternative is one way the name gets in, its conditions joined with
    "and". A name the list also holds unconditionally has none.
    """

    def __init__(self) -> None:
        self._alternatives: dict[str, set[str]] = {}
        self._always: set[str] = set()

    def add(self, name: str, conditions: tuple[str, ...]) -> None:
        if conditions:
            self._alternatives.setdefault(name, set()).add(" and ".join(conditions))
        else:
            self._always.add(name)

    def only_when(self, dropped: set[str] | None = None) -> dict[str, list[str]]:
        return {
            name: sorted(alternatives)
            for name, alternatives in self._alternatives.items()
            if name not in self._always and name not in (dropped or set())
        }


def unread_parts(listed: ListResolution) -> str:
    return "; ".join(f"{part.reason} ({part.location})" for part in listed.unresolved)


def unread_list_reason(framework: str, target: str, pointer: str, listed: ListResolution) -> str:
    """One sentence naming every part of a tools list the reader could not read.

    A list read in part keeps its readable members; the agent stays incomplete
    and each unread part is named where it is.
    """

    if listed.members:
        return (
            f"{framework} agent {target!r} at {pointer} has a tools list it reads only in "
            f"part; its binding graph is incomplete. Not read: {unread_parts(listed)}."
        )
    return (
        f"{framework} agent {target!r} at {pointer} uses a dynamic tools expression; its "
        f"binding graph is incomplete. Not read: {unread_parts(listed)}."
    )


def source_text(node: ast.AST) -> str:
    return _source(node)


def evaluation_site(scopes: ScopeIndex, node: ast.AST) -> ast.AST:
    """The node whose enclosing scope Python evaluates ``node`` in.

    A function's defaults, annotations and decorators, a class's bases,
    keywords and decorators, and a comprehension's first iterable run in the
    scope around them; ``ScopeIndex`` would look them up inside. They are
    anchored at the definition, whose enclosing scope is the right one.
    """

    child, parent = node, scopes.parents.get(node)
    via_iter = False
    while parent is not None and not isinstance(parent, ast.Module):
        if isinstance(parent, ast.comprehension):
            via_iter = child is parent.iter
        elif isinstance(parent, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
            if via_iter and parent.generators and child is parent.generators[0]:
                return evaluation_site(scopes, parent)
            return node
        elif isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            return node if child in parent.body else evaluation_site(scopes, parent)
        elif isinstance(parent, ast.Lambda):
            return node if child is parent.body else evaluation_site(scopes, parent)
        child, parent = parent, scopes.parents.get(parent)
    return node


def _nearest_scope(scopes: ScopeIndex, node: ast.AST) -> ast.AST | None:
    current = scopes.parents.get(node)
    while current is not None and not isinstance(
        current,
        ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
        | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ):
        current = scopes.parents.get(current)
    return current


def _identity(member: ListMember) -> tuple:
    return (
        id(member.expr),
        id(member.module),
        member.conditions,
        member.invocation.key if member.invocation else (),
    )


def _condition(node: ast.AST) -> str:
    """A condition's whole source text: it is compared, so never shortened."""

    return ast.unparse(node)


def _always_true(node: ast.expr, view: _View) -> bool:
    """A value true however empty it is: a ``filter``/``map`` object or a generator."""

    if isinstance(node, ast.GeneratorExp):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"filter", "map", "iter", "reversed", "zip", "enumerate"}
        and not (view.lookup or bindings_at(view.scopes, view.bindings))(node.func.id, node.func)
    )


def _star_after(view: _View, statement: ast.AST | None) -> int | None:
    """The line of a wildcard import after ``statement``, which may rebind its name."""

    line = getattr(statement, "lineno", 0)
    return next((star for star in view.star_lines if star > line), None)


def _all_under(part: ListResolution, conditions: tuple[str, ...]) -> ListResolution:
    for condition in reversed(conditions):
        part = part.under(condition)
    return part


def _source(node: ast.AST) -> str:
    text = ast.unparse(node)
    return text if len(text) <= 160 else text[:157] + "..."


def _single_target(statement: ast.Assign | ast.AnnAssign) -> str | None:
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None


def _conditional_between(scopes: ScopeIndex, statement: ast.AST, use: ast.AST) -> bool:
    """Whether ``statement`` runs under a condition or in a loop that ``use`` is outside of.

    A ``with`` block, or a ``try`` body, runs whenever the code around it does.
    A branch, a loop body, a handler or a ``match`` case does not, unless the
    use sits in the same branch: then the binding always precedes it.
    """

    child = statement
    current = scopes.parents.get(statement)
    while current is not None and not isinstance(
        current, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ):
        branch = _branch_holding(current, child)
        if branch is not None and not any(_within(scopes, use, item) for item in branch):
            return True
        child = current
        current = scopes.parents.get(current)
    return False


def _branch_holding(compound: ast.AST, child: ast.AST) -> list[ast.AST] | None:
    """The statements of ``compound`` that run only on some paths and hold ``child``."""

    if isinstance(compound, ast.If | ast.For | ast.AsyncFor | ast.While):
        return compound.body if child in compound.body else compound.orelse
    if isinstance(compound, ast.Try | ast.TryStar):
        if child in compound.body or child in compound.finalbody:
            return None
        return compound.orelse if child in compound.orelse else [child]
    if isinstance(compound, ast.ExceptHandler | ast.match_case):
        return compound.body
    if isinstance(compound, ast.Match):
        return [child]
    return None


def _within(scopes: ScopeIndex, node: ast.AST, ancestor: ast.AST) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = scopes.parents.get(current)
    return False


def _listish(value: ast.expr | None) -> bool:
    """Whether a name bound to ``value`` is one the reader may read as a list."""

    if isinstance(value, ast.Call):
        return isinstance(value.func, ast.Name) and value.func.id in _SAME_MEMBERS | {"filter"}
    return isinstance(
        value,
        ast.List | ast.Tuple | ast.ListComp | ast.GeneratorExp | ast.BinOp | ast.IfExp
        | ast.BoolOp | ast.Name | ast.Attribute,
    ) or (isinstance(value, ast.Constant) and value.value is None)


def _value_name(resolution: Resolution) -> str | None:
    return next(
        (step["name"] for step in reversed(resolution.steps) if step.get("binding") == "value"),
        None,
    )


def shared_lists(
    scopes: ScopeIndex, module_bindings: dict[str, list[Any]], values: list[ast.expr]
) -> set[object]:
    """Every list binding these values may be the very object of (#909).

    ``BASE``, ``BASE or []`` and ``A if c else B`` are the list itself, not a
    copy, and so is a name bound to one of them; a literal, ``+``, a
    comprehension or ``list(...)`` builds a new one. Keys are the binding's
    id, or ``("module", name)``.
    """

    found: set[object] = set()
    pending = [(value, 0) for value in values]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_DEPTH:
            continue
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            pending.extend((value, depth + 1) for value in node.values)
        elif isinstance(node, ast.IfExp):
            pending.extend([(node.body, depth + 1), (node.orelse, depth + 1)])
        elif isinstance(node, ast.Name):
            local = scopes.enclosing_bindings(evaluation_site(scopes, node), node.id)
            key: object = id(local[0]) if local else ("module", node.id)
            if key in found:
                continue
            found.add(key)
            found.update(id(binding) for binding in local)
            if local:
                statement = scopes.statement_of(local[0]) if len(local) == 1 else None
            else:
                bindings = module_bindings.get(node.id, [])
                statement = bindings[0].statement if len(bindings) == 1 else None
            value = getattr(statement, "value", None)
            if isinstance(statement, ast.Assign | ast.AnnAssign) and value is not None:
                # ``TOOLS = BASE``: one object under a second name.
                pending.append((value, depth + 1))
    return found


def _enclosing_function(scopes: ScopeIndex, node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = scopes.parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current
        current = scopes.parents.get(current)
    return None


__all__ = [
    "CAPABILITY_FIELDS",
    "MAX_DEPTH",
    "Conditions",
    "ListCache",
    "ListExpressions",
    "ListMember",
    "ListResolution",
    "UnresolvedPart",
    "bindings_at",
    "evaluation_site",
    "leaves_arguments_alone",
    "read_only_use",
    "shared_lists",
    "source_text",
    "unread_list_reason",
    "unread_parts",
]
