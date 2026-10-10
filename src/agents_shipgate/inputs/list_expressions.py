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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
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
    _external_constructor_paths,
    _independent_foreign_class_slot_write,
    _Stop,
    _unused_reflection_import,
    reference_spelling,
    reflective_access,
    replaces_builtin_namespace,
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
#: Owner roles whose result may leave its module through a plain ``from m import name``
#: when every importer's use of it is read: an agent handle, an inert data instance, a
#: tool object and the SDK's wrapped function. The ADK wrapper and field-data roles keep
#: their narrower proof, which refuses any export.
_EXPORTABLE_ROLES = frozenset({"__class__", "instance_data", "toolset_data", "tool_wrapper"})
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
                ListMember(m.expr, m.module, tuple(dict.fromkeys((condition, *m.conditions))), m.via, m.invocation)
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


def _source_slot_context(rows: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Bound supplemental refusal text; it never supplies an ownership fact."""
    return tuple(dict.fromkeys(row[:1024] for row in rows))[:2]


def _record_constructor_refusal(rows: list[str] | None, message: str) -> bool:
    if rows is not None:
        rows[:] = _source_slot_context((*rows, message))
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
    changed_import_context: dict[tuple[ast.alias, ast.stmt], tuple[str, ...]] = field(default_factory=dict)
    #: ``bindings_at`` for this module, built once.
    lookup: BindingsAt | None = None
    #: Imported namespace paths visibly replaced, resolved at each store's
    #: evaluation site so two aliases of one namespace share the obligation.
    qualified_patches: set[str] = field(default_factory=set)
    family_qualified_patches: dict[str, set[str]] = field(default_factory=dict)
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
    constructor_read_contexts: dict[tuple[Any, ...], tuple[str, ...]] = field(default_factory=dict)


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


def _folded_in(name: str, entries: Iterable[str]) -> bool:
    """Whether a directory listing holds ``name`` under any letter case."""
    folded = name.casefold()
    return any(entry.casefold() == folded for entry in entries)


def _unprovided_root(view: _View, provider: str) -> bool:
    """Require captured absence before assigning an external import its role."""
    if provider not in view.unprovided_roots:
        view.unprovided_roots[provider] = False
        resolver = view.resolver
        if resolver is None or view.module is None or resolver._layout is None:
            return False
        try:
            resolver._installed_absolute(view.module, provider)
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
        if snapshot is not None and root != snapshot.root and not snapshot.contains(root):
            snapshot = None  # A disjoint materialized tree has its own input identity.
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
                    if _folded_in(name, previous):
                        return False  # A differently spelled entry may be this root on another filesystem.
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
                # The exact listing decides, never the host filesystem: a
                # differently cased ``Operator.py`` provides ``operator`` on a
                # case-insensitive filesystem and not on a case-sensitive one,
                # and a proof of absence cannot depend on which one ran it.
                if _folded_in(provider, previous or ()) or _folded_in(provider + ".py", previous or ()):
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


def _direct_alias_constructor(view: _View, owner: ast.expr) -> ast.Call | None:
    """Resolve only unique plain aliases of one actual direct Agent call."""
    lookup = view.lookup or bindings_at(view.scopes, view.bindings)
    seen: set[int] = set()
    while isinstance(owner, ast.Name):
        if id(owner) in seen or len(seen) >= _NAMESPACE_ALIAS_LIMIT:
            return None
        seen.add(id(owner))
        found = lookup(owner.id, owner)
        if len(found) != 1:
            return None
        _, statement = found[0]
        if not isinstance(statement, ast.Assign | ast.AnnAssign):
            return None
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            return None
        owner = statement.value
    return owner if isinstance(owner, ast.Call) and _direct_agent_instance(view, owner) else None


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


def _constructor_family(view: _View, call: ast.Call) -> str | None:
    """Take a family only from this construction or its exact direct receiver."""
    qualified = _namespace_call_reference(view, call)
    receiver = None
    if isinstance(call.func, ast.Attribute) and call.func.attr == "clone":
        receiver = call.func.value
    elif qualified in {"dataclasses.replace", "copy.replace"} and len(call.args) == 1:
        receiver = call.args[0]
    if receiver is not None:
        if not isinstance(receiver, ast.Name):
            return None
        lookup = view.lookup or bindings_at(view.scopes, view.bindings)
        found = lookup(receiver.id, receiver)
        owner = getattr(found[0][1], "value", None) if len(found) == 1 else None
        if not isinstance(owner, ast.Call) or not _direct_agent_instance(view, owner):
            return None
        qualified = _namespace_call_reference(view, owner)
    return next((family for family in ("agents", "openai_agents", "google.adk")
                 if qualified in _external_constructor_paths(family)), None)


def _constructor_patches(view: _View, family: str | None) -> set[str]:
    if family is None:
        return view.qualified_patches
    if family not in view.family_qualified_patches:
        # Primitive stability is a fixed point of this family's exact write
        # census. Never reuse another family's exclusions or builtin flags.
        independent = replace(view, namespace_builtins_changed=set(), fresh_dictionaries={},
                              family_qualified_patches={}, returned_instances={})
        view.family_qualified_patches[family] = _qualified_attribute_patches(independent, family=family)
    return view.family_qualified_patches[family]


def _constructor_unchanged(
    view: _View, call: ast.Call, *, qualified: str | None = None, family: str | None = None,
    refused_context: list[str] | None = None,
) -> bool:
    # An import's canonical spelling does not prove the callee stayed the
    # framework's constructor: a visible store may replace any prefix.
    def refused(phase: str) -> bool:
        return _record_constructor_refusal(refused_context, f"constructor namespace in {view.ref}: {phase}")

    spelling = qualified or reference_spelling(call.func)
    patches = _constructor_patches(view, family or _constructor_family(view, call))
    if view.module is not None and spelling is not None:
        if (
            _UNREAD_NAMESPACE_ALIAS in patches
            or f"{_UNREAD_NAMESPACE_ALIAS}.*" in patches
        ):
            return refused('unread namespace replacement')
        fields = _CONSTRUCTOR_FIELDS | {item.arg for item in call.keywords if item.arg is not None}
        if any(
            patch.startswith(_UNREAD_NAMESPACE_ALIAS + ".") and (
                (name := patch.removeprefix(_UNREAD_NAMESPACE_ALIAS + ".")) in fields
                or name.startswith("_")
                or name.startswith("model_")
            )
            for patch in patches
        ):
            return refused('unread constructor field')
        parts = spelling.split(".")
        if any(
            ".".join(parts[:length]) in view.module.attribute_patches
            for length in range(2, len(parts) + 1)
        ):
            return refused('visible constructor prefix replacement')
        if patches:
            references = {qualified} if qualified is not None else {
                spelling, *_imported_references(view, spelling, call.func)
            }
            for reference in references:
                parts = reference.split(".")
                if any(f"{_UNREAD_NAMESPACE_ALIAS}.{part}" in patches for part in parts):
                    return refused('visible or unread constructor namespace patch')
                if any(
                    ".".join(parts[:length]) + ".*" in patches
                    for length in range(1, len(parts) + 1)
                ):
                    return refused('visible or unread constructor namespace patch')
                if any(
                    ".".join(parts[:length]) in patches
                    for length in range(2, len(parts) + 1)
                ):
                    return refused('visible or unread constructor namespace patch')
                if any(patch.startswith(reference + ".") for patch in patches):
                    # Constructor implementation fields are not immutable.
                    return refused('visible or unread constructor namespace patch')
    return True


def _constructor_imports_unchanged(
    view: _View, constructor: _View, returned: ast.Call, invocation: ast.Call,
    *, checked_modules: frozenset[object] = frozenset(),
    additional_callers: tuple[_View, ...] = (),
    qualified: str | None = None,
    family: str | None = None,
    refused_context: list[str] | None = None,
) -> bool:
    """Census source-local import effects before granting a returned instance."""
    def refused(phase: str) -> bool:
        return _record_constructor_refusal(refused_context, f"constructor imports in {view.ref}: {phase}")

    resolver, calls = view.resolver, view.builder_calls
    if resolver is None or calls is None or view.module is None or constructor.module is None:
        return refused('missing scoped constructor reader')
    qualified = qualified or _namespace_call_reference(constructor, returned)
    family = family or _constructor_family(constructor, returned)
    if qualified is None or qualified.startswith(_UNREAD_NAMESPACE_ALIAS):
        return refused('unread canonical constructor receiver')
    try:
        above = resolver._above_scope()
        if above.unread or above.patched or above.tables:
            return refused('above-scope import context is unread or patched')
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
                return refused('module census bound or unread dynamic importer')
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
            if module.path not in checked_modules and not _constructor_unchanged(
                other, returned, qualified=qualified, family=family, refused_context=refused_context,
            ):
                return refused(f'namespace patch refusal in {module.ref}')
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
                        return refused(f'unread import {module.ref}:{node.lineno}: {stop.reason}: {stop.detail}')
                paths.extend(imported)
            pending.extend(resolver._patch_scan(path) for path in paths if path not in seen)
    except _Stop as stop:
        return refused(f"{stop.reason}: {stop.detail}")
    return True


def _independent_plain_class_slot_write(view: _View, node: ast.Attribute) -> bool:
    """An inert source-local class owns only its exact ordinary init store."""
    if node.attr != "__init__" or not isinstance(node.ctx, ast.Store) or view.resolver is None or view.module is None:
        return False
    parent = view.scopes.parents.get(node)
    if not ((isinstance(parent, ast.Assign) and parent.targets == [node])
            or (isinstance(parent, ast.AnnAssign) and parent.target is node)):
        return False
    spelling = reference_spelling(node.value)
    if spelling is None:
        return False
    root = spelling.partition(".")[0]
    if view.scopes.enclosing_bindings(evaluation_site(view.scopes, node), root):
        return False
    bindings = view.bindings.get(root, [])
    if (len(bindings) != 1 or not bindings[0].top_level or not isinstance(bindings[0].node, ast.alias)
            or bindings[0].statement not in view.tree.body):
        return False
    outcome = view.resolver._constructor_reference(view.module, node.value, view.scopes)
    definition, home = outcome.get("retained_class"), outcome.get("module")
    if (set(outcome) != {"retained_class", "module"} or not isinstance(home, PythonModule)
            or not isinstance(definition, ast.ClassDef) or definition not in home.tree.body
            or definition.bases or definition.decorator_list or definition.keywords
            or getattr(definition, "type_params", [])
            or len(definition.body) != 1 or not isinstance(definition.body[0], ast.Pass)):
        return False
    owners = home.bindings.get(definition.name, [])
    return len(owners) == 1 and owners[0].node is definition and owners[0].top_level


def _provisional_agent_list_clear(view: _View, call: ast.Call) -> bool:
    """Separate an exact literal-list clear from dictionary namespace writes.

    This AST-only role does not grant ownership. Calling the final member
    proof while constructing its mutation census would make it recursive.
    """
    if (not isinstance(call.func, ast.Attribute) or call.func.attr != "clear"
            or call.args or call.keywords or not isinstance(view.scopes.parents.get(call), ast.Expr)):
        return False
    field = call.func.value
    if not isinstance(field, ast.Attribute) or field.attr not in CAPABILITY_FIELDS:
        return False
    lookup = view.lookup or bindings_at(view.scopes, view.bindings)

    def direct(owner: ast.expr) -> ast.Call | None:
        if not isinstance(owner, ast.Name):
            return None
        found = lookup(owner.id, owner)
        if len(found) != 1:
            return None
        _, statement = found[0]
        if not isinstance(statement, ast.Assign | ast.AnnAssign):
            return None
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        value = statement.value
        return value if (len(targets) == 1 and isinstance(targets[0], ast.Name)
                         and isinstance(value, ast.Call) and _direct_agent_instance(view, value)) else None

    def literal(value: ast.expr | None) -> bool:
        if isinstance(value, ast.Name):
            found = lookup(value.id, value)
            if len(found) != 1:
                return False
            value = getattr(found[0][1], "value", None)
        return isinstance(value, ast.List)

    owner = direct(field.value)
    if owner is None or not literal(next((item.value for item in owner.keywords if item.arg == field.attr), None)):
        return False
    for node in ast.walk(view.tree):
        if (not isinstance(node, ast.Attribute) or node.attr != field.attr
                or not isinstance(node.ctx, ast.Store | ast.Del) or direct(node.value) is not owner):
            continue
        statement = view.scopes.parents.get(node)
        if not (isinstance(node.ctx, ast.Store)
                and ((isinstance(statement, ast.Assign) and statement.targets == [node])
                     or (isinstance(statement, ast.AnnAssign) and statement.target is node))
                and literal(statement.value)):
            return False
    return True


def _qualified_attribute_patches(
    view: _View, *, primitive_pass: int = 0, family: str | None = None,
    raw_namespaces: bool = False,
    patch_producers: dict[str, set[ast.AST | None]] | None = None,
) -> set[str]:
    """Retain visible namespace mutations without flattening lexical imports.

    Multiple possible import owners keep every possibility: uncertainty cannot
    prove that the constructor remained unchanged. This only withholds reads;
    it never resolves an otherwise unknown constructor into a framework call.
    """
    if view.module is None:
        return set()
    changed: set[str] = set()
    nodes = list(ast.walk(view.tree))

    def emit(marker: str, producer: ast.AST | None) -> None:
        changed.add(marker)
        if patch_producers is not None:
            patch_producers.setdefault(marker, set()).add(producer)

    def record(owner: ast.expr, attribute: str | None, producer: ast.AST) -> None:
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
                emit(f"{_UNREAD_NAMESPACE_ALIAS}.{attribute or '*'}", producer)
            else:
                emit(reference.replace(".__dict__.", "."), producer)

    def dictionary_fields(owner: ast.expr, arguments: list[ast.expr], keywords: list[ast.keyword], producer: ast.AST) -> None:
        if _fresh_dictionary(view, owner):
            return
        for argument in arguments:
            if isinstance(argument, ast.Dict):
                for key in argument.keys:
                    record(owner, key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None, producer)
            else:
                record(owner, None, producer)
        for keyword in keywords:
            record(owner, keyword.arg, producer)

    dictionary_methods = {"__init__", "update", "clear", "pop", "popitem", "setdefault", "__setitem__", "__delitem__", "__ior__"}

    def dictionary_mutation(owner: ast.expr, method: str, arguments: list[ast.expr], keywords: list[ast.keyword], producer: ast.AST) -> None:
        if _fresh_dictionary(view, owner):
            return
        if method in {"__init__", "update", "__ior__"}:
            dictionary_fields(owner, arguments, keywords, producer)
        elif method in {"clear", "popitem"} or not arguments:
            record(owner, None, producer)
        else:
            key = arguments[0]
            record(owner, key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None, producer)

    for node in nodes:
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del):
            if not raw_namespaces and ((
                view.resolver is not None
                and family is not None
                and _independent_foreign_class_slot_write(view.resolver, view.module, node, view.scopes, family)
            ) or _independent_plain_class_slot_write(view, node)):
                continue  # Only this exact foreign slot store; other loads and RHS still count.
            if (not raw_namespaces and
                isinstance(node.ctx, ast.Store)
                and isinstance(view.scopes.parents.get(node), ast.Assign | ast.AnnAssign)
                and node.attr in {"tools", "handoffs", "mcp_servers", "sub_agents"}
                and _agent_instance(view, node.value)
            ):
                # A plain capability-field replacement on a known instance
                # does not write its constructor class or a sibling's list.
                # The existing handle/list census still checks this instance.
                continue
            record(node.value, node.attr, node)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del):
            if _fresh_dictionary(view, node.value):
                continue
            key = node.slice
            if isinstance(key, ast.Constant) and not isinstance(key.value, str):
                continue
            record(node.value, key.value if isinstance(key, ast.Constant) else None, node)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr):
            dictionary_fields(node.target, [node.value], [], node)
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
                emit(f"{_UNREAD_NAMESPACE_ALIAS}.*", node)
                continue
            if len(node.args) >= 2 and (setter or reference in _NAMESPACE_SETTERS):
                key = node.args[1]
                if (setter and reference not in _NAMESPACE_SETTERS) or reference in view.namespace_builtins_changed or (
                    reference is not None and reference.startswith("operator.") and not _standard_operator(view)
                ):
                    emit(f"{_UNREAD_NAMESPACE_ALIAS}.*", node)
                else:
                    record(node.args[0], key.value if isinstance(key, ast.Constant)
                           and isinstance(key.value, str) else None, node)
            elif reference == "operator.ior" and node.args:
                if reference in view.namespace_builtins_changed or not _standard_operator(view):
                    emit(f"{_UNREAD_NAMESPACE_ALIAS}.*", node)
                else:
                    dictionary_fields(node.args[0], node.args[1:], node.keywords, node)
            elif unbound_dictionary:
                if "builtins.dict" in view.namespace_builtins_changed or not node.args:
                    emit(f"{_UNREAD_NAMESPACE_ALIAS}.*", node)
                else:
                    dictionary_mutation(node.args[0], reference.rsplit(".", 1)[-1], node.args[1:], node.keywords, node)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in dictionary_methods:
                if not raw_namespaces and _provisional_agent_list_clear(view, node):
                    continue  # Membership and retained members still need the final owner proof.
                dictionary_mutation(node.func.value, node.func.attr, node.args, node.keywords, node)
        elif isinstance(node, ast.Attribute) and node.attr in dictionary_methods:
            parent = view.scopes.parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node) and not _fresh_dictionary(view, node.value):
                # An exported mutator may later write any key.
                record(node.value, None, node)
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
            emit(_UNREAD_NAMESPACE_ALIAS, None)
            return changed
        view.namespace_builtins_changed.update(unstable)
        view.fresh_dictionaries.clear()
        return _qualified_attribute_patches(
            view, primitive_pass=primitive_pass + 1, family=family, raw_namespaces=raw_namespaces,
            patch_producers=patch_producers,
        )
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
    change_contexts: dict[tuple[int, bool, int, str], tuple[str, ...]] = field(default_factory=dict)
    callees: dict[tuple[int, str, tuple, bool, bool], bool] = field(default_factory=dict)
    scopes: dict[int, ScopeIndex] = field(default_factory=dict)
    callable_changes: dict[tuple, str | None] = field(default_factory=dict)
    executable_modules: dict[int, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class _Projection:
    key: str
    view: _View
    invocation: Invocation | None
    required: bool = True
    default: ast.expr | None = None
    caller_defaults: set[tuple[int, int]] = field(default_factory=set, compare=False)

    @property
    def identity(self) -> tuple:
        return (
            self.key, self.required, id(self.default), id(self.view.tree),
            self.invocation.key if self.invocation else (),
        )


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
        self._checking_constructor_namespaces = False
        self._checking_handle_owners: set[tuple[int, int, str, bool, bool, bool]] = set()
        self._constructor_owner_results: dict[tuple[int, int, str, bool, bool, bool], bool] = {}
        self._constructor_container_results: dict[tuple[int, int, bool | None], bool] = {}
        self._checking_container_owners: set[tuple[int, int, bool | None]] = set()
        self._constructor_owner_work = 0
        self._constructor_namespace_result: tuple[object, bool] | None = None
        self._constructor_namespace_refusal: tuple[object, tuple[str, ...]] | None = None
        self.constructor_namespace_issue: str | None = None
        self._constructor_change_context: tuple[ast.Call, tuple, tuple[str, ...]] | None = None
        self._builder_calls = builder_calls
        self._invocation: Invocation | None = None
        self._field = "tools"
        self._projection: _Projection | None = None
        self._checking_returns: set[tuple[int, str]] = set()
        self._checking_member_receivers: set[tuple[int, int]] = set()
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

    def _reflection(self, view: _View) -> ast.AST | None:
        excluded = (
            self.calls.runtime_exclusions(view.module)
            if self.calls is not None and view.module is not None else frozenset()
        )
        return reflective_access(view.tree, excluded=excluded)

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
        if self._reflection(view) is not None:
            # ``globals()["TOOLS"]``, ``vars()`` and ``sys.modules[__name__]``
            # reach a module list without spelling its name.
            view.changed.update(("module", name) for name in tracked)
        refused_reads: list[str] = []
        call_reads = self._call_reads(view, refused_reads=refused_reads)
        for node in ast.walk(view.tree):
            refused_reads.clear()
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
                    self._mark_changed(view, node, refused_context=_source_slot_context(refused_reads))
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

    def _mark_changed(
        self, view: _View, node: ast.Name, *, refused_context: tuple[str, ...] = (),
    ) -> None:
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
                pair = (binding, statement)
                view.changed_imports.append(pair)
                view.changed_import_context[pair] = _source_slot_context(
                    (*view.changed_import_context.get(pair, ()), *refused_context)
                )
        if not found:
            for item in view.bindings.get(node.id, []):
                if isinstance(item.node, ast.alias) and isinstance(item.statement, ast.Import | ast.ImportFrom):
                    pair = (item.node, item.statement)
                    view.changed_imports.append(pair)
                    view.changed_import_context[pair] = _source_slot_context(
                        (*view.changed_import_context.get(pair, ()), *refused_context)
                    )
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

    def _call_reads(self, view: _View, *, refused_reads: list[str] | None = None) -> CallReads:
        bindings = view.lookup or bindings_at(view.scopes, view.bindings)

        def reads(call: ast.Call, position: int | None, keyword: str | None) -> bool:
            if leaves_arguments_alone(call, bindings):
                return True
            # Another module's ``Agent`` may be its own class: only the module
            # the agent is built in says which constructions are the framework's.
            staged: list[str] = []
            if self._reads_agent(view, call, keyword, refused_context=staged):
                return True
            unchanged = self._callee_leaves_alone(view, call, position, keyword)
            if not unchanged and refused_reads is not None:
                refused_reads[:] = _source_slot_context((*refused_reads, *staged))
            return unchanged

        return reads

    def _reads_agent(
        self, view: _View, call: ast.Call, keyword: str | None, *, refused_context: list[str] | None = None,
    ) -> bool:
        def refused(phase: str) -> bool:
            return _record_constructor_refusal(refused_context, f"constructor read in {view.ref}: {phase}")

        family = _constructor_family(view, call)
        if not _constructor_unchanged(view, call, family=family, refused_context=refused_context):
            return False
        recognized = (view.entry and self._agent_reads(call, keyword)) or bool(
            view.module is not None
            and self._module_agent_reads is not None
            and self._module_agent_reads(view.module, call, keyword)
        )
        if not recognized or view.module is None:
            # Pure-AST callers supply their own consumer predicate. File
            # adapters establish a scoped module before granting that role.
            result = bool(recognized)
            if not result:
                refused("source consumer predicate did not recognize this construction")
                if view.module is not None and view.builder_calls is not None:
                    issue = view.builder_calls.cached_constructor_issue(view.module, call)
                    if issue:
                        refused(f"cached constructor issue: {issue}")
            return result
        if self.constructor_namespace_changed():
            result = self._constructor_namespace_result
            refusal = self._constructor_namespace_refusal
            if (refused_context is not None and result is not None and result[1]
                    and refusal is not None and refusal[0] == result[0]):
                refused_context[:] = _source_slot_context((*refused_context, *refusal[1]))
            elif refused_context is not None:
                refused("retained constructor namespace ownership census refused")
            return False
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
        key = (qualified, family, tuple(sorted(item.arg for item in call.keywords if item.arg is not None)),
               tuple(id(caller.tree) for caller in callers))
        if key not in view.constructor_reads:
            view.constructor_reads[key] = False
            view.constructor_read_contexts[key] = ()
            staged: list[str] = []
            checked_paths: set[object] = set()
            for item in callers:
                if item.module is not None and _constructor_unchanged(
                    item, call, qualified=qualified, family=family, refused_context=staged,
                ):
                    checked_paths.add(item.module.path)
            checked = frozenset(checked_paths)
            if any(caller.module is not None and caller.module.path not in checked for caller in callers):
                view.constructor_read_contexts[key] = _source_slot_context(staged)
            else:
                staged = []
                view.constructor_reads[key] = _constructor_imports_unchanged(
                    view, view, call, call, checked_modules=checked,
                    additional_callers=tuple(callers[1:]),
                    qualified=qualified,
                    family=family,
                    refused_context=staged,
                )
                view.constructor_read_contexts[key] = (
                    () if view.constructor_reads[key] else _source_slot_context(staged)
                )
        if not view.constructor_reads[key] and refused_context is not None:
            refused_context[:] = _source_slot_context(
                (*refused_context, *view.constructor_read_contexts.get(key, ()))
            )
        return view.constructor_reads[key]

    def constructor_namespace_changed(self) -> bool:
        """Known agent destinations must retain constructor-carrying tools safely.

        Every instance retains its class, even when its tools are empty. Local
        tool functions also carry module globals. Prove all receiving handles
        and containers before granting the import reader's retention exception.
        """
        if self._resolver is None:
            return False
        if self._checking_constructor_namespaces:
            # Known constructor retention is checked by the outer census. A
            # recursive handle edge is separately refused by the owner guard.
            return False
        def inventory() -> tuple[int, int, int, int, int, int, int]:
            return (len(self._resolver._constructor_namespace_owners), len(self._resolver._constructor_container_owners),
                    len(self._resolver._constructor_member_sinks), len(self._resolver._constructor_decorator_owners),
                    len(self._resolver._constructor_dictionary_sinks), len(self._resolver._constructor_unused_namespace_sinks),
                    len(self._resolver._constructor_dictionary_class_sinks))

        generation = inventory()
        context = (self._invocation.key if self._invocation else (),
                   self._projection.identity if self._projection else ())
        cache_key = (generation, context)
        if self._constructor_namespace_result is not None and self._constructor_namespace_result[0] == cache_key:
            return self._constructor_namespace_result[1]
        self._checking_constructor_namespaces = True
        self._constructor_namespace_refusal = None
        self.constructor_namespace_issue = None
        self._constructor_owner_results.clear()
        self._constructor_container_results.clear()
        self._constructor_owner_work = 0
        checked: set[tuple[int, str]] = set()
        try:
            while True:
                owners = [(*owner, True) for owner in self._resolver._constructor_namespace_owners.values()]
                owners += [(module, expression, "container", False)
                           for module, expression in self._resolver._constructor_container_owners.values()]
                owners += [(module, expression, "member_sink", False)
                           for module, expression in self._resolver._constructor_member_sinks.values()]
                owners += [(module, expression, "decorator_handle", False)
                           for module, expression in self._resolver._constructor_decorator_owners.values()]
                owners += [(module, expression, "dictionary_sink:" + family, False)
                           for module, expression, family in self._resolver._constructor_dictionary_sinks.values()]
                owners += [(module, expression, "unused_namespace_sink", False)
                           for module, expression in self._resolver._constructor_unused_namespace_sinks.values()]
                owners += [(module, expression, "dictionary_class_sink:" + family, False)
                           for module, expression, family, _ in self._resolver._constructor_dictionary_class_sinks.values()]
                pending = [owner for owner in owners if (id(owner[1]), owner[2]) not in checked]
                if not pending:
                    self._constructor_namespace_result = ((
                        inventory(),
                        context,
                    ), False)
                    return False
                if len(checked) + len(pending) > 1024:
                    self.constructor_namespace_issue = "the constructor namespace census exceeds 1024 owners"
                    return True
                current_generation = inventory()
                if current_generation != generation:
                    self._constructor_owner_results.clear()
                    self._constructor_container_results.clear()
                    generation = current_generation
                for module, expression, field, is_handle in pending:
                    checked.add((id(expression), field))
                    view = self._foreign(module)
                    refused_models: list[str] = []
                    if is_handle:
                        changed = self._agent_result_changed(view, expression, field, strict_container=True)
                    elif field == "member_sink":
                        changed = not (
                            self._uncalled_member_sink(view, expression, refused_models=refused_models)
                            or self._member_receiver_owned(view, expression)
                        )
                    elif field == "decorator_handle":
                        changed = self._retained_callable_changes(expression, {
                            (id(module), id(expression)): Resolution(reference=expression.name, module=module, definition=expression),
                        }, factory_return=False) is not None
                    elif field.startswith("dictionary_sink:"):
                        changed = not self._dictionary_function_sink_owned(view, expression, field.removeprefix("dictionary_sink:"))
                    elif field == "unused_namespace_sink":
                        changed = not self._unused_namespace_sink_owned(view, expression)
                    elif field.startswith("dictionary_class_sink:"):
                        family = field.removeprefix("dictionary_class_sink:")
                        _, _, _, canonical = self._resolver._constructor_dictionary_class_sinks[(family, id(expression))]
                        changed = not self._dictionary_class_sink_owned(view, expression, family, canonical)
                    else:
                        changed = self._factory_result_changed(view, expression)
                    if changed:
                        self._constructor_namespace_result = (cache_key, True)
                        owner_phase = f"constructor namespace owner {field} in {module.ref}:{expression.lineno} refused"
                        self._constructor_namespace_refusal = (
                            self._constructor_namespace_result[0],
                            _source_slot_context((*refused_models, owner_phase)),
                        )
                        return True
        finally:
            self._checking_constructor_namespaces = False

    def _uncalled_member_sink(
        self, view: _View, expression: ast.expr, *, refused_models: list[str] | None = None,
    ) -> bool:
        """Discharge only a sink in a function with a complete empty census."""
        staged: list[str] = []
        calls = self.calls
        if calls is None or view.module is None:
            return False
        function = calls.function_at(view.module, expression)
        if function is None:
            return False
        census = calls.callers(view.module, function, allow_empty=True, confined_dictionary_data=True,
                               unused_namespace_data=True)
        if not census.limits and not census.sites:
            return True
        if not census.sites:
            from agents_shipgate.inputs.builder_calls import CallLimit

            try:
                if calls.uncalled_source_slot(view.module, function, refused_models=staged):
                    return True
                recorded = (self._resolver._constructor_member_sinks.get(id(expression))
                            if self._checking_constructor_namespaces and self._resolver is not None else None)
                if (recorded is not None
                        and recorded[0] is view.module and recorded[1] is expression):
                    for home, data, family in tuple(self._resolver._constructor_dictionary_sinks.values()):
                        if (calls._fresh_unbound_dictionary_edge(home, data) is None
                                and calls._absent_field_dictionary_edge(home, data) is None):
                            continue
                        if self._fresh_dictionary_function_sink_owned(
                            self._foreign(home), data, family,
                            expected_module=view.module, expected_function=function,
                        ):
                            return True
            except CallLimit as exc:
                self._constructor_limit((str(exc),))
        if refused_models is not None:
            refused_models[:] = _source_slot_context((*refused_models, *staged))
        return False

    def _fresh_dictionary_function_sink_owned(
        self, view: _View, expression: ast.expr, family: str, *,
        expected_module: PythonModule | None = None,
        expected_function: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
    ) -> bool:
        calls = self.calls
        if calls is None or view.module is None:
            return False
        with calls.fresh_unbound_dictionary_receipt(view.module, expression, family) as receipt:
            if receipt is None:
                return self._absent_field_dictionary_function_sink_owned(
                    view, expression, family, expected_module=expected_module, expected_function=expected_function,
                )
            resolution = receipt.proof.resolve(view.module, expression)
            if (not resolution.resolved or resolution.caveats or resolution.module is None
                    or not isinstance(resolution.definition, ast.FunctionDef | ast.AsyncFunctionDef)):
                return False
            if expected_module is not None or expected_function is not None:
                if resolution.module is not expected_module or resolution.definition is not expected_function:
                    return False
            census = receipt.proof._census(
                resolution.module, resolution.definition, allow_empty=True,
                confined_dictionary_data=True, unused_namespace_data=True,
                confined_primitive_dictionary_values=receipt.values,
            )
            if census.sites or census.limits:
                return False
            receipt.reconfirm()
            return True

    def _absent_field_dictionary_function_sink_owned(
        self, view: _View, expression: ast.expr, family: str, *,
        expected_module: PythonModule | None = None,
        expected_function: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
    ) -> bool:
        calls = self.calls
        if calls is None or view.module is None:
            return False
        with calls.absent_field_dictionary_receipt(view.module, expression, family) as receipt:
            if receipt is None:
                return False
            if expected_module is not None or expected_function is not None:
                if receipt.roles.home is not expected_module or receipt.roles.function is not expected_function:
                    return False
            census = receipt.census()
            if census.sites or census.limits:
                return False
            receipt.reconfirm()
            return True

    def _dictionary_function_sink_owned(self, view: _View, expression: ast.expr, family: str) -> bool:
        from agents_shipgate.inputs.builder_calls import CallLimit

        calls = self.calls
        if calls is None or view.module is None:
            return False
        try:
            if not calls.confined_dictionary_value(view.module, expression):
                return self._fresh_dictionary_function_sink_owned(view, expression, family)
        except CallLimit as exc:
            self._constructor_limit((str(exc),))
            return False
        resolution = calls.resolve(view.module, expression)
        if (not resolution.resolved or resolution.caveats or resolution.module is None
                or not isinstance(resolution.definition, ast.FunctionDef | ast.AsyncFunctionDef)):
            return False
        census = calls.callers(resolution.module, resolution.definition, allow_empty=True,
                               confined_dictionary_data=True, unused_namespace_data=True)
        return not census.sites and not census.limits

    def _dictionary_class_sink_owned(
        self, view: _View, expression: ast.expr, family: str, canonical: str,
    ) -> bool:
        from agents_shipgate.inputs.builder_calls import CallLimit

        calls = self.calls
        if calls is None or view.module is None or self._resolver is None:
            return False
        try:
            if not calls.confined_dictionary_value(view.module, expression):
                return False
        except CallLimit as exc:
            self._constructor_limit((str(exc),))
            return False
        # Identity only: the outer namespace/import/mutation census remains
        # mandatory. Never ask this passive token to prove a receiving call.
        outcome = self._resolver._constructor_reference(view.module, expression, calls.scopes(view.module))
        return (canonical in _external_constructor_paths(family)
                and outcome.get("external_constructor") == canonical)

    def _unused_namespace_sink_owned(self, view: _View, expression: ast.expr) -> bool:
        from agents_shipgate.inputs.builder_calls import CallLimit

        if self.calls is None or view.module is None:
            return False
        try:
            return self.calls.unused_namespace_value(view.module, expression)
        except CallLimit as exc:
            self._constructor_limit((str(exc),))
            return False

    def _callee_leaves_alone(
        self, view: _View, call: ast.Call, position: int | None, keyword: str | None,
        *, strict_container: bool = False,
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
        key = (id(function), parameter, invocation.key,
               strict_container, self._checking_constructor_namespaces)
        if key not in self._cache.callees:
            # A recursive forwarding cycle is not a read-only proof.
            self._cache.callees[key] = False
            if strict_container:
                try:
                    if self._constructor_limit(calls.callers(defining, function).limits):
                        return False
                except CallLimit as exc:
                    self._constructor_limit((str(exc),))
                    return False
            foreign = self._foreign(defining)
            reads = self._call_reads(foreign)

            def safe_read(inner: ast.Call, position: int | None, field: str | None) -> bool:
                if field is not None and self._reads_agent(foreign, inner, field):
                    return not self._agent_result_changed(foreign, inner, field, borrowed=True)
                return reads(inner, position, field)

            previous, self._invocation = self._invocation, invocation
            try:
                if strict_container:
                    self._cache.callees[key] = all(
                        isinstance(node.ctx, ast.Load) and self._container_read(foreign, node)
                        for node in ast.walk(function)
                        if isinstance(node, ast.Name) and node.id == parameter
                    ) and not any(
                        isinstance(node, ast.Global | ast.Nonlocal) and parameter in node.names
                        for node in ast.walk(function)
                    )
                else:
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
        self, view: _View, call: ast.Call, keyword: str, *, borrowed: bool = False,
        strict_container: bool = False, initial_field: bool = False,
    ) -> bool:
        key = (id(view.tree), id(call), keyword, borrowed, strict_container, initial_field)
        if key in self._checking_handle_owners or len(self._checking_handle_owners) >= 128:
            if self._checking_constructor_namespaces:
                self.constructor_namespace_issue = "recursive constructor-handle ownership or more than 128 retained edges"
            return True
        if self._checking_constructor_namespaces:
            if key in self._constructor_owner_results:
                return self._constructor_owner_results[key]
            self._constructor_owner_work += 1
            if self._constructor_owner_work > 4096:
                self.constructor_namespace_issue = "the constructor ownership traversal exceeds 4096 edges"
                return True
        self._checking_handle_owners.add(key)
        try:
            result = self._agent_result_changed_inner(view, call, keyword, borrowed=borrowed,
                                                     strict_container=strict_container, initial_field=initial_field)
            if self._checking_constructor_namespaces:
                self._constructor_owner_results[key] = result
            return result
        finally:
            self._checking_handle_owners.remove(key)

    def _agent_result_changed_inner(
        self, view: _View, call: ast.Call, keyword: str, *, borrowed: bool = False,
        strict_container: bool = False, initial_field: bool = False,
    ) -> bool:
        """Follow returned builder handles before proving a borrowed list read-only."""
        if self._reflection(view) is not None:
            return True
        if (
            view.module is not None
            and self.calls is not None
            and self.calls.exported_elsewhere(view.module, call)
        ):
            self._constructor_limit((self.calls.export_limit(call),))
            if not (
                strict_container and self._checking_constructor_namespaces
                and keyword in _EXPORTABLE_ROLES
                and self.calls.export_routes(call) == {"value_import"}
                and self._imported_members_owned(
                    view, call,
                    lambda foreign, subject: isinstance(subject, ast.Name) and not self._handle_use_changed(
                        foreign, subject, keyword, borrowed=borrowed, strict_container=True, initial_field=initial_field,
                    ),
                    carriers=keyword != "instance_data",
                    require_use=keyword == "__class__",
                )
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
                census = calls.callers(view.module, function, allow_empty=self._checking_constructor_namespaces)
                return self._constructor_limit(census.limits) or any(
                    self._agent_result_changed(
                        self._foreign(site.module), site.call, keyword, borrowed=borrowed,
                        strict_container=strict_container, initial_field=initial_field,
                    )
                    for site in census.sites
                )
            finally:
                self._checking_returns.remove(key)
        entered = (
            strict_container and self._checking_constructor_namespaces and keyword == "toolset_data"
            and isinstance(parent, ast.withitem) and parent.context_expr is call
            and isinstance(parent.optional_vars, ast.Name) and self._enters_as_itself(view, call)
        )
        if not entered and (not isinstance(parent, ast.Assign | ast.AnnAssign) or parent.value is not call):
            return not isinstance(parent, ast.Expr) and not (
                strict_container and (
                    self._field_data_read(view, call) if keyword == "field_data"
                    else self._toolset_data_read(view, call) if keyword == "toolset_data"
                    else self._instance_data_read(view, call) if keyword == "instance_data"
                    else self._container_read(view, call)
                )
            )
        if entered:
            assert isinstance(parent, ast.withitem)
            targets = [parent.optional_vars]
        else:
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
            if self._handle_use_changed(
                view, node, keyword, borrowed=borrowed, strict_container=strict_container, initial_field=initial_field,
            ):
                return True
        return False

    def _handle_use_changed(
        self, view: _View, node: ast.Name, keyword: str, *, borrowed: bool, strict_container: bool, initial_field: bool = False,
    ) -> bool:
        """Whether one load of a constructed handle may change or hand it on."""
        if (self._checking_constructor_namespaces and self._resolver is not None
                and id(node) in self._resolver._constructor_wrapped_operands
                and keyword in {"func", "wrapper_class"}):
            return False  # This exact operand loads the function before wrapping it.
        use = view.scopes.parents.get(node)
        if (keyword in {"__class__", *CAPABILITY_FIELDS} and strict_container
                and self._checking_constructor_namespaces and not initial_field
                and self._agent_copy_receiver_read(view, node, keyword)):
            return False
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
                census = calls.callers(view.module, function, allow_empty=self._checking_constructor_namespaces)
                if self._constructor_limit(census.limits) or any(
                    self._agent_result_changed(
                        self._foreign(site.module), site.call, keyword, borrowed=borrowed,
                        strict_container=strict_container, initial_field=initial_field,
                    )
                    for site in census.sites
                ):
                    return True
            finally:
                self._checking_returns.remove(key)
        elif keyword == "field_data":
            if not self._field_data_read(view, node):
                return True
        elif keyword == "toolset_data":
            if not self._toolset_data_read(view, node):
                return True
        elif keyword == "instance_data":
            if not self._instance_data_read(view, node):
                return True
        elif (strict_container and self._checking_constructor_namespaces
              and isinstance(use, ast.Attribute) and use.value is node and use.attr == "as_tool"
              and isinstance(use.ctx, ast.Load)
              and isinstance(call := view.scopes.parents.get(use), ast.Call) and call.func is use):
            # The SDK's documented ``agent.as_tool(...)``: a new tool object that
            # holds the agent. The handle goes no further; the tool has its own owner.
            return not self._register_as_tool(view, call)
        elif strict_container and self._checking_constructor_namespaces and self._agent_tool_argument(view, node, use):
            return False  # ADK's ``AgentTool(agent=...)`` holds the agent and goes no further.
        elif (strict_container and self._checking_constructor_namespaces and isinstance(use, ast.Attribute)
              and use.value is node and self._discarded_method_call(view, use)):
            return False  # A statement calling a method of the exact class; nothing is handed on.
        elif isinstance(use, ast.Attribute):
            if use.attr == "__dict__":
                return True
            if keyword == "__class__" and use.attr in CAPABILITY_FIELDS:
                # Membership changes do not change the instance's class.
                # Retained class-carrying members have their own ownership
                # edges; their namespace use must remain proved separately.
                return False
            if use.attr == keyword:
                if initial_field and not isinstance(use.ctx, ast.Load):
                    return True  # Reading a constructor value requires the field to remain unchanged.
                if isinstance(view.scopes.parents.get(use), ast.AugAssign):
                    return True
                if (strict_container and self._checking_constructor_namespaces
                        and isinstance(use.ctx, ast.Store | ast.Del)):
                    if isinstance(use.ctx, ast.Store) and not self._member_list_shape(view, use):
                        return True
                    return False  # Dropping members does not export their identity.
                if not borrowed and not isinstance(use.ctx, ast.Load):
                    return True
                if isinstance(use.ctx, ast.Load) and not (
                    self._container_read(view, use) if strict_container
                    else read_only_use(use, view.scopes.parents, self._call_reads(view))
                ):
                    return True
            elif isinstance(use.ctx, ast.Load) and not (
                self._container_read(view, use) if strict_container
                else read_only_use(node, view.scopes.parents, self._call_reads(view))
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
            if (strict_container and self._checking_constructor_namespaces
                    and keyword in {"__class__", *CAPABILITY_FIELDS} and not initial_field
                    and self._direct_alias_stores_owned(view, node)):
                return False
            # An alias can subsequently reach the same mutable list.
            return True
        elif not (self._container_read(view, node) if strict_container
                  else read_only_use(node, view.scopes.parents, self._call_reads(view))):
            return True
        return False

    def _enters_as_itself(self, view: _View, call: ast.Call) -> bool:
        """``async with MCPServerStdio(...) as server``: the SDK's server is its own context.

        Its ``__aenter__`` connects and returns the server itself, so the name
        the ``with`` binds is the object this owner already checks. Only the
        SDK's MCP server classes, by their exact import, have that protocol.
        """
        from agents_shipgate.inputs.object_tools import SDK_MCP_SERVERS

        resolver = self._resolver
        if resolver is None or view.module is None:
            return False
        canonical = resolver._constructor_reference(view.module, call.func, view.scopes).get("external_constructor")
        return (isinstance(canonical, str) and canonical.partition(".")[0] in {"agents", "openai_agents"}
                and canonical.rsplit(".", 1)[-1] in SDK_MCP_SERVERS)

    @staticmethod
    def _discarded_method_call(view: _View, attribute: ast.Attribute) -> bool:
        """``await agent.run()`` as a statement: a method call whose result is dropped.

        The handle is an instance of the framework's own class, whose methods
        do not edit its capability lists, and nothing is passed in or kept. A
        method that could re-initialize it (``__init__``, ``model_*``) or a
        field of the class is not this; a method the code stores on the
        instance runs with no access to it except through names this census
        already reads.
        """
        name = attribute.attr
        if (not isinstance(attribute.ctx, ast.Load) or name.startswith("_") or name.startswith("model_")
                or name in _CONSTRUCTOR_FIELDS):
            return False
        call = view.scopes.parents.get(attribute)
        if not (isinstance(call, ast.Call) and call.func is attribute):
            return False
        statement = view.scopes.parents.get(call)
        if isinstance(statement, ast.Await):
            statement = view.scopes.parents.get(statement)
        return isinstance(statement, ast.Expr)

    def _register_as_tool(self, view: _View, call: ast.Call) -> bool:
        """``agent.as_tool(...)`` builds a tool object; it needs an owner like any other."""
        resolver = self._resolver
        if resolver is None or view.module is None:
            return False
        family = next((key[0] for key in resolver._constructor_namespace_owners if key[0] != "google.adk"), "agents")
        resolver._constructor_namespace_owners.setdefault(
            (family, id(call), "toolset_data"), (view.module, call, "toolset_data"),
        )
        return True

    def _agent_tool_argument(self, view: _View, node: ast.Name, use: ast.AST | None) -> bool:
        """Whether ``node`` is the agent ADK's ``AgentTool`` wraps, by that class's exact import."""
        from agents_shipgate.inputs.object_tools import ADK_AGENT_TOOLS

        resolver = self._resolver
        if resolver is None or view.module is None:
            return False
        if isinstance(use, ast.keyword) and use.arg == "agent" and use.value is node:
            call = view.scopes.parents.get(use)
        elif isinstance(use, ast.Call) and use.args and use.args[0] is node and use.func is not node:
            call = use
        else:
            return False
        if not isinstance(call, ast.Call):
            return False
        return resolver._constructor_reference(view.module, call.func, view.scopes).get("external_constructor") in ADK_AGENT_TOOLS

    def _direct_alias_stores_owned(self, view: _View, node: ast.Name) -> bool:
        """An exact direct-instance alias may only replace owned list fields."""
        calls = self.calls
        constructor = _direct_alias_constructor(view, node)
        if constructor is None or calls is None or view.module is None:
            return False
        lookup = view.lookup or bindings_at(view.scopes, view.bindings)

        def scope(at: ast.AST) -> ast.AST:
            current = view.scopes.parents.get(evaluation_site(view.scopes, at))
            while current is not None:
                if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
                    return current
                current = view.scopes.parents.get(current)
            return view.tree

        origin_scope = scope(node)
        if scope(constructor) is not origin_scope:
            return False  # A captured instance does not receive this local-store role.
        pending = [node]
        checked: set[int] = set()
        stores = 0
        work = 0
        while pending:
            source = pending.pop()
            parent = view.scopes.parents.get(source)
            if not isinstance(parent, ast.Assign | ast.AnnAssign) or parent.value is not source:
                return False
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                return False
            target = targets[0]
            if id(parent) in checked or len(checked) >= _NAMESPACE_ALIAS_LIMIT:
                return False
            checked.add(id(parent))
            bindings = lookup(target.id, target)
            if (len(bindings) != 1 or bindings[0][1] is not parent
                    or calls.exported_elsewhere(view.module, source)):
                return False
            for use in ast.walk(view.tree):
                work += 1
                if work > MAX_VISITS:
                    return False
                if (not isinstance(use, ast.Name) or not isinstance(use.ctx, ast.Load)
                        or use.id != target.id or lookup(use.id, use) != bindings):
                    continue
                if scope(use) is not origin_scope or _direct_alias_constructor(view, use) is not constructor:
                    return False
                destination = view.scopes.parents.get(use)
                if isinstance(destination, ast.Assign | ast.AnnAssign) and destination.value is use:
                    pending.append(use)
                elif (isinstance(destination, ast.Attribute) and destination.value is use
                      and destination.attr in CAPABILITY_FIELDS and isinstance(destination.ctx, ast.Store)
                      and self._member_list_shape(view, destination)):
                    stores += 1
                else:
                    return False
        return stores > 0

    def _agent_copy_receiver_read(self, view: _View, node: ast.Name, keyword: str) -> bool:
        """Retain an exact SDK copy receiver only through its owned result."""
        parent = view.scopes.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node and parent.attr == "clone":
            call = view.scopes.parents.get(parent)
            if not isinstance(call, ast.Call) or call.func is not parent:
                return False
        elif (isinstance(parent, ast.Call) and parent.args == [node]
              and _namespace_call_reference(view, parent) in {"dataclasses.replace", "copy.replace"}):
            call = parent
        else:
            return False
        # The existing consumer proof checks the direct Agent receiver, its
        # actual imports and providers, and class/protocol mutations. A copy
        # must also retain its class safely through every use of its result.
        return (
            self._reads_agent(view, call, "tools")
            and not self._agent_result_changed(view, call, "__class__", strict_container=True)
            and (keyword == "__class__" or not self._agent_result_changed(
                view, call, keyword, strict_container=True,
            ))
        )

    @staticmethod
    def _field_data_read(view: _View, node: ast.expr) -> bool:
        """FieldInfo is data carrying a shared class, never a builtin container."""
        parent = view.scopes.parents.get(node)
        return isinstance(parent, ast.Expr) or (
            isinstance(parent, ast.Compare)
            and all(isinstance(operator, ast.Is | ast.IsNot) for operator in parent.ops)
        )

    @staticmethod
    def _instance_data_read(view: _View, node: ast.expr) -> bool:
        """An inert dataclass instance is read for its fields and goes nowhere else.

        Its fields hold strings and ``None`` (the class body admits nothing
        else), so a field read hands on no namespace. Passing the instance on,
        calling anything on it or aliasing it is not that read.
        """
        parent = view.scopes.parents.get(node)
        if isinstance(parent, ast.Expr) or (
            isinstance(parent, ast.Compare) and all(isinstance(operator, ast.Is | ast.IsNot) for operator in parent.ops)
        ):
            return True
        if not (isinstance(parent, ast.Attribute) and parent.value is node and isinstance(parent.ctx, ast.Load)
                and not parent.attr.startswith("_")):
            return False
        call = view.scopes.parents.get(parent)
        return not (isinstance(call, ast.Call) and call.func is parent)

    def _toolset_data_read(self, view: _View, node: ast.expr) -> bool:
        """A toolset instance only reaches the existing Agent.tools destination.

        Literal list/tuple packaging does not make this object a builtin
        container: methods, indexing, aliases and other consumers stay unread.
        Returned factories are followed by the surrounding handle census.
        """
        parent = view.scopes.parents.get(node)
        if isinstance(parent, ast.Expr) or (
            isinstance(parent, ast.Compare) and all(isinstance(operator, ast.Is | ast.IsNot) for operator in parent.ops)
        ):
            return True
        current: ast.AST = node
        while isinstance(view.scopes.parents.get(current), ast.List | ast.Tuple):
            current = view.scopes.parents[current]
        keyword = view.scopes.parents.get(current)
        call = view.scopes.parents.get(keyword)
        return bool(
            isinstance(keyword, ast.keyword) and keyword.arg in {"tools", "mcp_servers"} and isinstance(call, ast.Call)
            and self._reads_agent(view, call, keyword.arg)
            and not self._agent_result_changed(view, call, keyword.arg, borrowed=True, strict_container=True)
        )

    # -- resolution --------------------------------------------------------

    def constructor_changed(self, call: ast.Call, invocation: Invocation | None) -> bool:
        """A bound construction must retain its constructor even for literals."""
        self._constructor_change_context = None
        if invocation is None:
            return False
        previous, self._invocation = self._invocation, invocation
        try:
            staged: list[str] = []
            changed = not self._reads_agent(self.entry, call, "tools", refused_context=staged)
            self._constructor_change_context = (
                (call, invocation.key, _source_slot_context(staged)) if changed else None
            )
            return changed
        except BaseException:
            self._constructor_change_context = None
            raise
        finally:
            self._invocation = previous

    def constructor_refusal_context(self, call: ast.Call, invocation: Invocation | None) -> tuple[str, ...]:
        row = self._constructor_change_context
        return row[2] if row is not None and row[0] is call and invocation is not None and row[1] == invocation.key else ()

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
        if self._projection is not None:
            if isinstance(node, ast.Dict):
                return self._dictionary(node, view, depth, seen)
            if not isinstance(node, ast.Name | ast.Attribute | ast.Call):
                return self._stop(view, node, "the projected value is not a proven literal dictionary")
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
        if isinstance(node, ast.Subscript):
            if not isinstance(node.slice, ast.Constant) or not isinstance(node.slice.value, str):
                return self._stop(view, node, "a dictionary projection needs one literal string key")
            return self._project(node.value, _Projection(node.slice.value, view, self._invocation), view, depth, seen)
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
        if self._projection is None and isinstance(func, ast.Attribute) and func.attr == "get":
            if (
                len(node.args) not in {1, 2} or node.keywords
                or not isinstance(node.args[0], ast.Constant)
                or not isinstance(node.args[0].value, str)
            ):
                return self._stop(view, node, "a dictionary get needs one literal string key and an optional positional default")
            if len(node.args) == 2 and not self._nonexecuting_default(view, node.args[1]):
                return self._stop(view, node, "a dictionary get default is evaluated eagerly and its executable or unresolved expression is not read")
            return self._project(
                func.value,
                _Projection(node.args[0].value, view, self._invocation, required=False,
                            default=node.args[1] if len(node.args) == 2 else None),
                view, depth, seen,
            )
        builtin = (
            func.id
            if isinstance(func, ast.Name) and not (view.lookup or bindings_at(view.scopes, view.bindings))(func.id, func)
            else None
        )
        if self._projection is None and builtin in _SAME_MEMBERS and len(node.args) == 1 and not node.keywords:
            return self._resolve(node.args[0], view, depth + 1, seen)
        if self._projection is None and builtin == "filter" and len(node.args) == 2 and not node.keywords:
            predicate, iterable = node.args
            part = self._resolve(iterable, view, depth + 1, seen)
            if isinstance(predicate, ast.Constant) and predicate.value is None:
                return part
            return part.under(f"the filter `{_condition(predicate)}` keeps it")
        return self._factory(node, view, depth, seen)

    def _nonexecuting_default(self, view: _View, node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return node.value is None
        if isinstance(node, ast.List | ast.Tuple):
            return all(self._nonexecuting_default(view, item) for item in node.elts)
        if isinstance(node, ast.Name | ast.Attribute) and view.module is not None and self.calls is not None:
            resolution = self.calls.resolve(view.module, node)
            return resolution.resolved and not resolution.caveats
        return False

    def _project(
        self, node: ast.expr, projection: _Projection, view: _View, depth: int, seen: frozenset
    ) -> ListResolution:
        previous, self._projection = self._projection, projection
        try:
            return self._resolve(node, view, depth + 1, seen)
        finally:
            self._projection = previous

    def _dictionary(self, node: ast.Dict, view: _View, depth: int, seen: frozenset) -> ListResolution:
        projection = self._projection
        assert projection is not None
        keys: dict[str, ast.expr] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                return self._stop(view, node, "dictionary spreads or nonliteral string keys may replace the projected entry")
            if key.value in keys:
                return self._stop(view, node, f"dictionary key {key.value!r} is defined more than once")
            keys[key.value] = value
        selected = keys.get(projection.key)
        if selected is None and projection.required:
            return self._stop(view, node, f"dictionary key {projection.key!r} is absent; subscription does not yield an empty list")
        previous, self._projection = self._projection, None
        invocation = self._invocation
        try:
            if selected is not None:
                return self._resolve(selected, view, depth + 1, seen).through(
                    f"dictionary key {projection.key!r} ({self._where(view, node)})"
                )
            if projection.default is None:
                return ListResolution()
            self._invocation = projection.invocation
            result = self._resolve(projection.default, projection.view, depth + 1, seen)
            projection.caller_defaults.update((id(member.expr), id(member.module)) for member in result.members)
            return result
        finally:
            self._projection, self._invocation = previous, invocation

    def _factory(self, node: ast.Call, view: _View, depth: int, seen: frozenset) -> ListResolution:
        calls = self.calls
        if calls is None or view.module is None:
            return self._stop(view, node, f"a call to `{_source(node.func)}`, whose result is not read")
        from agents_shipgate.inputs.builder_calls import CallLimit, CallSite, single_return

        resolution = calls.resolve(view.module, node.func)
        if not resolution.resolved:
            return self._stop(view, node, f"a call to `{_source(node.func)}`, whose result is not read")
        if resolution.caveats:
            detail = "; ".join(resolution.caveats)
            return self._stop(view, node, f"a call to `{_source(node.func)}`, whose result is not read: {detail}")
        home, function = resolution.module, resolution.definition
        assert home is not None and function is not None
        returned = single_return(function)
        if returned is None:
            return self._stop(view, node, "the factory has no single final return expression")
        key = ("factory", id(function))
        if key in seen:
            return self._stop(view, node, "the returned-list factory calls itself")
        try:
            census = calls.callers(home, function)
            if census.limits:
                return self._stop(view, node, "the factory callable is not established: " + "; ".join(census.limits))
            invocation = calls.invoke(home, function, CallSite(view.module, node), self._invocation)
        except CallLimit as exc:
            return self._stop(view, node, f"the returned-list factory call is not established: {exc}")
        if not self._inert_factory(function) or any(
            not (established := calls.resolve(home, item)).resolved or established.caveats
            for item in ast.walk(returned) if isinstance(item, ast.Name)
        ):
            return self._stop(view, node, "the factory needs an inert prelude and a literal list, tuple or dictionary return; executable or unresolved expressions are not read by this increment")
        # Another invocation can hand on the same module callables (and their
        # globals) before this call. A fresh container at the selected site
        # cannot establish ownership while any other result escapes.
        if any(self._factory_result_changed(self._foreign(site.module), site.call) for site in census.sites):
            return self._stop(view, node, "the returned factory container or a projected member may be changed or handed on")
        previous, self._invocation = self._invocation, invocation
        try:
            result = self._resolve(returned, self._foreign(home), depth + 1, seen | {key})
        finally:
            self._invocation = previous
        changed = self._factory_member_change(
            self._foreign(home), function, result,
            caller_defaults=self._projection.caller_defaults if self._projection else frozenset(),
        )
        if changed is not None:
            return self._stop(view, node, changed)
        for condition in invocation.conditions:
            result = result.under(condition)
        return result.through(f"{function.name} returned at {self._where(view, node)}")

    @staticmethod
    def _inert_factory(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Limit outer statements and eager returned values to supported syntax."""
        if not all(
            isinstance(statement, ast.Import | ast.ImportFrom | ast.Pass)
            or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str))
            for statement in function.body[:-1]
        ):
            return False

        def literal(node: ast.expr) -> bool:
            if isinstance(node, ast.List | ast.Tuple):
                return all(isinstance(item, ast.Name) for item in node.elts)
            if isinstance(node, ast.Dict):
                return all(
                    isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and (isinstance(value, ast.List | ast.Tuple) and literal(value) or isinstance(value, ast.Constant) and value.value is None)
                    for key, value in zip(node.keys, node.values, strict=True)
                )
            return False

        returned = function.body[-1]
        return isinstance(returned, ast.Return) and returned.value is not None and literal(returned.value)

    def _constructor_limit(self, limits: tuple[str | None, ...]) -> bool:
        reasons = [reason for reason in limits if reason]
        if reasons and self._checking_constructor_namespaces:
            self.constructor_namespace_issue = self.constructor_namespace_issue or "; ".join(reasons)
        return bool(reasons)

    def _factory_result_changed(self, view: _View, call: ast.expr, *, dictionary: bool | None = None) -> bool:
        if not self._checking_constructor_namespaces:
            return self._factory_result_changed_inner(view, call, dictionary=dictionary)
        key = (id(view.tree), id(call), dictionary)
        if key in self._checking_container_owners or len(self._checking_container_owners) >= 128:
            self.constructor_namespace_issue = "recursive constructor-container ownership or more than 128 retained edges"
            return True
        if key in self._constructor_container_results:
            return self._constructor_container_results[key]
        self._constructor_owner_work += 1
        if self._constructor_owner_work > 4096:
            self.constructor_namespace_issue = "the constructor ownership traversal exceeds 4096 edges"
            return True
        self._checking_container_owners.add(key)
        try:
            result = self._factory_result_changed_inner(view, call, dictionary=dictionary)
            self._constructor_container_results[key] = result
            return result
        finally:
            self._checking_container_owners.remove(key)

    def _factory_result_changed_inner(self, view: _View, call: ast.expr, *, dictionary: bool | None = None) -> bool:
        if self._reflection(view) is not None:
            return True
        if view.module is not None and self.calls is not None and self.calls.exported_elsewhere(view.module, call):
            self._constructor_limit((self.calls.export_limit(call),))
            if not (self._checking_constructor_namespaces and isinstance(call, ast.List | ast.Tuple)
                    and self._imported_members_owned(view, call)):
                return True
        parent = view.scopes.parents.get(call)
        if dictionary is None:
            dictionary = isinstance(call, ast.Dict) or self._projection is not None
        if isinstance(parent, ast.Return):
            function = _enclosing_function(view.scopes, parent)
            if function is None or view.module is None or self.calls is None:
                return True
            key = (id(function), "namespace_dictionary" if dictionary else "namespace_result")
            if key in self._checking_returns or len(self._checking_returns) >= 4:
                return True
            self._checking_returns.add(key)
            try:
                census = self.calls.callers(view.module, function, allow_empty=self._checking_constructor_namespaces)
                return self._constructor_limit(census.limits) or any(
                    self._factory_result_changed(self._foreign(site.module), site.call, dictionary=dictionary)
                    for site in census.sites
                )
            finally:
                self._checking_returns.remove(key)
        if not isinstance(parent, ast.Assign | ast.AnnAssign) or parent.value is not call:
            return not self._container_read(view, call, dictionary=dictionary)
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        if (self._checking_constructor_namespaces and len(targets) == 1
                and isinstance(targets[0], ast.Attribute) and targets[0].attr in CAPABILITY_FIELDS):
            return not self._container_read(view, call, dictionary=dictionary)
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            return True
        return self._container_binding_changed(view, targets[0], dictionary=dictionary)

    def _container_binding_changed(self, view: _View, target: ast.Name, *, dictionary: bool = True) -> bool:
        lookup = view.lookup or bindings_at(view.scopes, view.bindings)
        owner = lookup(target.id, target)
        return any(
            not self._container_read(view, node, dictionary=dictionary)
            for node in ast.walk(view.tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            and node.id == target.id and lookup(node.id, node) == owner
        )

    def _container_read(self, view: _View, node: ast.expr, *, dictionary: bool = False) -> bool:
        """Read a retained container through its projection, never stop at indexing."""
        parent = view.scopes.parents.get(node)
        if isinstance(parent, ast.arguments):
            function = view.scopes.parents.get(parent)
            if (view.module is None or self.calls is None or not isinstance(function, ast.FunctionDef)
                    or function.decorator_list
                    or any(isinstance(item, ast.Yield | ast.YieldFrom | ast.GeneratorExp) for item in ast.walk(function))):
                return False
            positional = [*parent.posonlyargs, *parent.args]
            defaults = list(zip(positional[len(positional) - len(parent.defaults):], parent.defaults, strict=True))
            defaults += list(zip(parent.kwonlyargs, parent.kw_defaults, strict=True))
            parameter = next((argument for argument, value in defaults if value is node), None)
            if parameter is None:
                return False
            key = (id(function), "namespace_default:" + parameter.arg)
            if key in self._checking_returns or len(self._checking_returns) >= 4:
                return False
            from agents_shipgate.inputs.builder_calls import CallLimit
            self._checking_returns.add(key)
            try:
                census = self.calls.callers(
                    view.module, function, allow_empty=self._checking_constructor_namespaces,
                )
                if self._constructor_limit(census.limits):
                    return False
                for site in census.sites:
                    self.calls.invoke(view.module, function, site)
                uses = [
                    (item, view.scopes.enclosing_bindings(evaluation_site(view.scopes, item), item.id))
                    for item in ast.walk(function) if isinstance(item, ast.Name) and item.id == parameter.arg
                ]
                return not any(
                    isinstance(item, ast.Global | ast.Nonlocal) and parameter.arg in item.names
                    for item in ast.walk(function)
                ) and all(
                    bindings == [parameter] and isinstance(item.ctx, ast.Load)
                    and self._container_read(view, item, dictionary=dictionary)
                    for item, bindings in uses if parameter in bindings
                )
            except CallLimit as exc:
                self._constructor_limit((str(exc),))
                return False
            finally:
                self._checking_returns.remove(key)
        if isinstance(parent, ast.Return):
            return not self._factory_result_changed(view, node, dictionary=dictionary)
        if isinstance(parent, ast.Dict) and node in parent.values:
            return all(isinstance(key, ast.Constant) and isinstance(key.value, str) for key in parent.keys) and self._container_read(view, parent, dictionary=True)
        if isinstance(parent, ast.List | ast.Tuple) and node in parent.elts:
            return self._container_read(view, parent)
        known_copy = (isinstance(node, ast.Call) and not node.keywords and len(node.args) == 1
                      and reference_spelling(node.func) in {"list", "tuple"}
                      and leaves_arguments_alone(node, view.lookup or bindings_at(view.scopes, view.bindings)))
        known_copy = known_copy or (
            not dictionary and isinstance(node, ast.Call) and not node.args and not node.keywords
            and isinstance(node.func, ast.Attribute) and node.func.attr == "copy"
            and self._member_list_shape(view, node.func.value, tuple_ok=True)
        )
        if ((isinstance(node, ast.List | ast.Tuple | ast.Dict | ast.BoolOp | ast.IfExp | ast.BinOp) or known_copy)
                and isinstance(parent, ast.Assign | ast.AnnAssign) and parent.value is node):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            if (self._checking_constructor_namespaces and len(targets) == 1
                    and isinstance(targets[0], ast.Attribute) and targets[0].attr in CAPABILITY_FIELDS):
                # Prove the sink after the current handle walk has completed;
                # recursively asking the same handle now would reject even an
                # owned replacement. The outer census validates this exact edge.
                if self._resolver is None or view.module is None:
                    return False
                self._resolver._constructor_member_sinks[id(targets[0])] = (view.module, targets[0])
                return self._member_list_shape(view, targets[0])
            return (len(targets) == 1 and isinstance(targets[0], ast.Name)
                    and not self._container_binding_changed(view, targets[0], dictionary=dictionary))
        if isinstance(parent, ast.BoolOp) or (
            isinstance(parent, ast.IfExp) and parent.test is not node
        ):
            return self._container_read(view, parent, dictionary=dictionary)
        if isinstance(parent, ast.Starred) and parent.value is node:
            outer = view.scopes.parents.get(parent)
            if (isinstance(outer, ast.Call) and outer.args == [parent] and not outer.keywords
                    and reference_spelling(outer.func) == "print"
                    and leaves_arguments_alone(outer, view.lookup or bindings_at(view.scopes, view.bindings))):
                return True
            return isinstance(outer, ast.List | ast.Tuple) and self._container_read(view, outer)
        if isinstance(parent, ast.BinOp):
            other = parent.right if parent.left is node else parent.left
            return not dictionary and isinstance(parent.op, ast.Add) and self._list_operand(view, other, subject=node) and self._container_read(view, parent)
        if isinstance(parent, ast.Compare):
            return all(isinstance(operator, ast.Is | ast.IsNot) for operator in parent.ops)
        if isinstance(parent, ast.Subscript) and parent.value is node:
            return (
                isinstance(parent.ctx, ast.Load) and isinstance(parent.slice, ast.Constant)
                and dictionary and isinstance(parent.slice.value, str) and self._container_read(view, parent)
            )
        if isinstance(parent, ast.Attribute) and parent.value is node and parent.attr == "get":
            call = view.scopes.parents.get(parent)
            return (
                isinstance(call, ast.Call) and call.func is parent and len(call.args) in {1, 2}
                and not call.keywords and isinstance(call.args[0], ast.Constant)
                and dictionary and isinstance(call.args[0].value, str) and self._container_read(view, call)
            )
        if dictionary and isinstance(parent, ast.Attribute | ast.BinOp):
            # dict.copy/values/items and union retain the mutable values. A
            # fresh list copy or list concatenation has separate membership.
            return False
        if isinstance(parent, ast.Attribute) and parent.value is node:
            call = view.scopes.parents.get(parent)
            if isinstance(call, ast.Call) and call.func is parent:
                if (parent.attr == "as_tool" and self._checking_constructor_namespaces
                        and self._register_as_tool(view, call)):
                    return True  # The tool object it builds has its own owner.
                if parent.attr == "copy" and not call.args and not call.keywords:
                    return self._container_read(view, call)
                if self._checking_constructor_namespaces and self._member_mutation(view, node, call):
                    return True
                return parent.attr in {"count", "index"} and all(
                    self._nonexecuting_default(view, argument) for argument in call.args
                ) and not call.keywords
            return False
        if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute)
                and parent.func.attr == "get" and len(parent.args) == 2 and not parent.keywords
                and parent.args[1] is node and isinstance(parent.args[0], ast.Constant)
                and isinstance(parent.args[0].value, str) and self._nonexecuting_default(view, node)):
            shape = self._shape_value(view, parent.func.value, 0)
            if shape is not None and isinstance(shape[0], ast.Dict):
                keys = [key.value for key in shape[0].keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
                if len(keys) == len(shape[0].keys) and len(set(keys)) == len(keys):
                    return self._container_read(view, parent)
        call, position, keyword = None, None, None
        if isinstance(parent, ast.keyword):
            call, keyword = view.scopes.parents.get(parent), parent.arg
        elif isinstance(parent, ast.Call):
            call = parent
            position = next((i for i, argument in enumerate(call.args) if argument is node), None)
        if isinstance(call, ast.Call):
            if (self._checking_constructor_namespaces and isinstance(call.func, ast.Attribute)
                    and node in call.args and call.func.attr in {"append", "insert", "extend"}
                    and self._member_mutation(view, call.func.value, call)):
                if self._resolver is None or view.module is None:
                    return False
                self._resolver._constructor_member_sinks[id(call.func.value)] = (view.module, call.func.value)
                return True
            lookup = view.lookup or bindings_at(view.scopes, view.bindings)
            if leaves_arguments_alone(call, lookup):
                name = reference_spelling(call.func)
                if name in {"list", "tuple"} and len(call.args) == 1 and not call.keywords:
                    return dictionary or self._container_read(view, call)
                return name in {"len", "repr", "str", "bool", "id", "hash", "type", "print"} and call.args == [node] and not call.keywords
            if dictionary:
                # A helper's old list-use proof stops at subscription and
                # cannot show it leaves the dictionary's mutable values alone.
                return False
            if self._reads_agent(view, call, keyword):
                return not self._agent_result_changed(view, call, str(keyword), borrowed=True, strict_container=True)
            return self._callee_leaves_alone(view, call, position, keyword, strict_container=True)
        # Iteration may retain the mutable projected list, and an alias may
        # subsequently change it. Neither is a read-only ownership proof.
        if isinstance(parent, ast.For | ast.AsyncFor | ast.comprehension):
            return False
        return read_only_use(node, view.scopes.parents, self._call_reads(view))

    def _member_mutation(self, view: _View, receiver: ast.expr, call: ast.Call) -> bool:
        """Membership edits may preserve contained objects, only on builtin lists."""
        if (not isinstance(view.scopes.parents.get(call), ast.Expr) or call.keywords
                or any(isinstance(argument, ast.Starred) for argument in call.args)
                or not isinstance(call.func, ast.Attribute)):
            return False
        method = call.func.attr
        signature = (
            method == "append" and len(call.args) == 1
            or method == "insert" and len(call.args) == 2
            and isinstance(call.args[0], ast.Constant) and type(call.args[0].value) is int
            or method in {"clear", "reverse"} and not call.args
            or method == "extend" and len(call.args) == 1
            and self._member_list_shape(view, call.args[0], tuple_ok=True)
        )
        return bool(signature) and self._member_list_shape(view, receiver)

    def _member_list_shape(
        self, view: _View, node: ast.expr, invocation: Invocation | None = None,
        *, tuple_ok: bool = False, depth: int = 0, seen: frozenset[int] = frozenset(),
    ) -> bool:
        """Prove receiver type and replacement history without granting membership."""
        if depth > MAX_DEPTH or id(node) in seen:
            return False
        self._constructor_owner_work += 1
        if self._constructor_owner_work > 4096:
            self.constructor_namespace_issue = "the constructor ownership traversal exceeds 4096 edges"
            return False
        seen = seen | {id(node)}

        def shape(other: ast.expr, source: _View = view, context: Invocation | None = invocation) -> bool:
            return self._member_list_shape(source, other, context, tuple_ok=tuple_ok, depth=depth + 1, seen=seen)

        if isinstance(node, ast.List):
            return True
        if isinstance(node, ast.Tuple):
            return tuple_ok
        if isinstance(node, ast.BoolOp):
            return all(shape(value) for value in node.values)
        if isinstance(node, ast.IfExp):
            return shape(node.body) and shape(node.orelse)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return shape(node.left) and shape(node.right)
        lookup = view.lookup or bindings_at(view.scopes, view.bindings)
        if isinstance(node, ast.Call) and leaves_arguments_alone(node, lookup):
            return (reference_spelling(node.func) == "list" and not node.keywords
                    and len(node.args) <= 1 and (not node.args or self._member_list_shape(
                        view, node.args[0], invocation, tuple_ok=True, depth=depth + 1, seen=seen)))
        if isinstance(node, ast.Name):
            bindings = lookup(node.id, node)
            if len(bindings) != 1:
                return False
            binding, statement = bindings[0]
            if isinstance(binding, ast.arg):
                origin = invocation.argument(binding) if invocation is not None else None
                if origin is not None:
                    home, value, context = origin
                    return shape(value, self._foreign(home), context)
                function = view.scopes.parents.get(view.scopes.parents.get(binding))
                if view.module is None or self.calls is None or not isinstance(function, ast.FunctionDef):
                    return False
                from agents_shipgate.inputs.builder_calls import CallLimit
                try:
                    census = self.calls.callers(view.module, function)
                    if self._constructor_limit(census.limits):
                        return False
                    for site in census.sites:
                        context = self.calls.invoke(view.module, function, site)
                        origin = context.argument(binding)
                        if origin is None or not shape(origin[1], self._foreign(origin[0]), origin[2]):
                            return False
                    return bool(census.sites)
                except CallLimit as exc:
                    self._constructor_limit((str(exc),))
                    return False
            if isinstance(binding, ast.alias) and view.module is not None and self.calls is not None:
                resolution = self.calls.resolve(view.module, node)
                return (resolution.module is not None and resolution.value is not None and not resolution.caveats
                        and shape(resolution.value, self._foreign(resolution.module), None))
            value = getattr(statement, "value", None)
            return isinstance(binding, ast.Name) and isinstance(value, ast.expr) and shape(value)
        if isinstance(node, ast.Attribute) and node.attr in CAPABILITY_FIELDS and isinstance(node.value, ast.Name):
            bindings = lookup(node.value.id, node.value)
            if len(bindings) != 1:
                return False
            binding, statement = bindings[0]
            value = getattr(statement, "value", None)
            if not isinstance(binding, ast.Name):
                return False
            if isinstance(node.ctx, ast.Store):
                # Replacing a field owns the new container, even when the
                # constructor omitted that field. This grants no initial-field
                # read or mutation role, and no returned/copied alias role.
                direct = _direct_alias_constructor(view, node.value)
                write = view.scopes.parents.get(node)
                if (direct is not None and self._reads_agent(view, direct, node.attr)
                        and ((isinstance(write, ast.Assign) and write.targets == [node])
                             or (isinstance(write, ast.AnnAssign) and write.target is node))):
                    return shape(write.value)
            if not isinstance(value, ast.Call):
                return False
            field = self._member_agent_field(view, value, node.attr, invocation, depth, seen)
            if field is None or not shape(field[1], field[0], field[2]):
                return False
            # Every lexical write to this exact field must retain builtin type.
            for use in ast.walk(view.tree):
                if (isinstance(use, ast.Attribute) and use.attr == node.attr and isinstance(use.value, ast.Name)
                        and use.value.id == node.value.id and lookup(use.value.id, use.value) == bindings
                        and isinstance(use.ctx, ast.Store)):
                    write = view.scopes.parents.get(use)
                    replacement = getattr(write, "value", None)
                    if not isinstance(write, ast.Assign | ast.AnnAssign) or not isinstance(replacement, ast.expr) or not shape(replacement):
                        return False
            return True
        return False

    def _member_agent_field(
        self, view: _View, call: ast.Call, field: str, invocation: Invocation | None,
        depth: int, seen: frozenset[int], *, initial_field: bool = False,
    ) -> tuple[_View, ast.expr, Invocation | None] | None:
        if depth > 4 or view.module is None or self.calls is None:
            return None
        if self._reads_agent(view, call, field):
            if initial_field and self._agent_result_changed(view, call, field, strict_container=True, initial_field=True):
                return None
            values = [keyword.value for keyword in call.keywords if keyword.arg == field]
            return (view, values[0], invocation) if len(values) == 1 else None
        from agents_shipgate.inputs.builder_calls import CallLimit, CallSite, single_return
        resolution = self.calls.resolve(view.module, call.func)
        if resolution.module is None or resolution.definition is None or resolution.caveats:
            return None
        try:
            if self._constructor_limit(self.calls.callers(resolution.module, resolution.definition).limits):
                return None
            context = self.calls.invoke(resolution.module, resolution.definition, CallSite(view.module, call), invocation)
        except CallLimit as exc:
            self._constructor_limit((str(exc),))
            return None
        returned = single_return(resolution.definition)
        foreign = self._foreign(resolution.module)
        if isinstance(returned, ast.Name):
            bindings = (foreign.lookup or bindings_at(foreign.scopes, foreign.bindings))(returned.id, returned)
            returned = getattr(bindings[0][1], "value", None) if len(bindings) == 1 else None
        if not isinstance(returned, ast.Call) or id(returned) in seen:
            return None
        return self._member_agent_field(
            foreign, returned, field, context, depth + 1, seen | {id(returned)}, initial_field=initial_field,
        )

    def _member_receiver_owned(self, view: _View, receiver: ast.expr) -> bool:
        key = (id(view.tree), id(receiver))
        if key in self._checking_member_receivers or len(self._checking_member_receivers) >= 128:
            return False
        if not self._member_list_shape(view, receiver):
            return False
        self._checking_member_receivers.add(key)
        try:
            lookup = view.lookup or bindings_at(view.scopes, view.bindings)
            if isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name):
                bindings = lookup(receiver.value.id, receiver.value)
                value = getattr(bindings[0][1], "value", None) if len(bindings) == 1 else None
                if isinstance(receiver.ctx, ast.Store):
                    value = _direct_alias_constructor(view, receiver.value) or value
                return isinstance(value, ast.Call) and not self._agent_result_changed(
                    view, value, receiver.attr, borrowed=True, strict_container=True)
            if not isinstance(receiver, ast.Name):
                return False
            bindings = lookup(receiver.id, receiver)
            if len(bindings) != 1:
                return False
            binding, statement = bindings[0]
            if isinstance(binding, ast.arg):
                function = view.scopes.parents.get(view.scopes.parents.get(binding))
                if not isinstance(function, ast.FunctionDef):
                    return False
                return all(
                    isinstance(item.ctx, ast.Load) and self._container_read(view, item)
                    for item in ast.walk(function) if isinstance(item, ast.Name)
                    and lookup(item.id, item) == bindings
                )
            if isinstance(binding, ast.alias) and view.module is not None and self.calls is not None:
                resolution = self.calls.resolve(view.module, receiver)
                return (resolution.module is not None and isinstance(resolution.value, ast.List | ast.Tuple)
                        and not self._factory_result_changed(self._foreign(resolution.module), resolution.value))
            value = getattr(statement, "value", None)
            return isinstance(value, ast.expr) and not self._factory_result_changed(view, value)
        finally:
            self._checking_member_receivers.remove(key)

    def _imported_members_owned(
        self, view: _View, value: ast.expr,
        owned: Callable[[_View, ast.expr], bool] | None = None,
        *, carriers: bool = True, require_use: bool = False,
    ) -> bool:
        """Use the shared-list census, then inspect object identity through every use.

        ``owned`` replaces the container-read test for a value that is not a
        list: the proof that one importer's use of the value changes nothing.
        ``carriers`` is False for a value of inert data, which a function that
        merely imports it cannot make into a handle on any namespace.
        ``require_use`` refuses an export nothing uses: an agent handle that
        left its module with no read use has nothing proved about it.
        """
        if view.module is None or self.calls is None:
            return False
        names = [name for name, bindings in view.module.bindings.items()
                 if len(bindings) == 1 and bindings[0].top_level and getattr(bindings[0].statement, "value", None) is value]
        if len(names) != 1:
            return False
        from agents_shipgate.inputs.builder_calls import CallLimit
        proved = 0
        try:
            borrowers = self.calls.borrowers(view.module, names[0], namespace_carriers=True)
            retaining = self.calls.retaining_modules(view.module, names[0], namespace_carriers=True)
            families = {key[0] for key in self._resolver._constructor_namespace_owners} if self._resolver is not None else set()
            if self._resolver is not None:
                steps = [{"path": module.ref, "name": "Agent"} for module in borrowers if module.path in retaining]
                for family in families:
                    if self._resolver._no_import_patch({"module": view.module}, steps, external_symbol=family):
                        return False
            for module in borrowers:
                if self.calls.dynamic_importer(module) is not None or self._reflection(self._foreign(module)) is not None:
                    return False
                foreign = self._foreign(module)
                lookup = foreign.lookup or bindings_at(foreign.scopes, foreign.bindings)
                for node in ast.walk(module.tree):
                    if not isinstance(node, ast.Name | ast.Attribute) or not isinstance(node.ctx, ast.Load):
                        continue
                    self._constructor_owner_work += 1
                    if self._constructor_owner_work > 4096:
                        self.constructor_namespace_issue = "the constructor ownership traversal exceeds 4096 edges"
                        return False
                    parent = foreign.scopes.parents.get(node)
                    if isinstance(parent, ast.Attribute) and parent.value is node:
                        continue
                    resolution = self.calls.resolve(module, node)
                    subject = node
                    if (isinstance(node, ast.Attribute) and isinstance(parent, ast.Call) and parent.func is node
                            and (self._member_mutation(foreign, node.value, parent)
                                 or owned is not None and node.attr == "as_tool")):
                        subject = node.value
                        resolution = self.calls.resolve(module, subject)
                    elif owned is not None and isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                        # ``settings.url``: the handle is the receiver; its owner reads the use.
                        subject = node.value
                        resolution = self.calls.resolve(module, subject)
                    if resolution.module is view.module and resolution.value is value:
                        if resolution.caveats or not (
                            owned(foreign, subject) if owned is not None else self._container_read(foreign, subject)
                        ):
                            return False
                        proved += 1
                        continue
                    if (resolution.module is not None and not resolution.caveats
                            and isinstance(resolution.value, ast.List | ast.Tuple)
                            and all(isinstance(item, ast.Constant) or (
                                isinstance(item, ast.Name | ast.Attribute)
                                and (member := self.calls.resolve(resolution.module, item)).definition is not None
                                and not member.caveats
                            ) for item in resolution.value.elts)):
                        continue  # Another literal retains no shared list or namespace.
                    if not carriers:
                        continue
                    spelling = reference_spelling(node)
                    head = spelling.split(".", 1)[0] if spelling else None
                    for binding, statement in lookup(head, node) if head else ():
                        if not isinstance(binding, ast.alias) or not isinstance(statement, ast.Import | ast.ImportFrom):
                            continue
                        if self.calls.import_may_share(module, view.module, names[0], statement, binding, namespace_carriers=True):
                            # A direct callable still carries its globals.
                            # Only an owned receiving edge clears this exact
                            # imported namespace-carrier refusal.
                            if (resolution.resolved and not resolution.caveats
                                    and isinstance(resolution.definition, ast.FunctionDef | ast.AsyncFunctionDef)
                                    and self._container_read(foreign, node)):
                                continue
                            return False  # An unread namespace can retain or project a member.
            return owned is None or not require_use or proved > 0
        except (CallLimit, _Stop) as exc:
            self._constructor_limit((getattr(exc, "detail", str(exc)),))
            return False

    def _shape_value(self, view: _View, node: ast.expr, depth: int) -> tuple[ast.expr, _View] | None:
        """Find a literal container shape without re-entering ownership checks."""
        if depth > MAX_DEPTH:
            return None
        lookup = view.lookup or bindings_at(view.scopes, view.bindings)
        if isinstance(node, ast.Name):
            bindings = lookup(node.id, node)
            if len(bindings) == 1:
                value = getattr(bindings[0][1], "value", None)
                if isinstance(value, ast.expr):
                    return self._shape_value(view, value, depth + 1)
            return None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            return node, view
        if isinstance(node, ast.Call) and self.calls is not None and view.module is not None:
            from agents_shipgate.inputs.builder_calls import CallLimit, CallSite, single_return

            resolution = self.calls.resolve(view.module, node.func)
            if resolution.resolved and not resolution.caveats:
                function = resolution.definition
                returned = single_return(function)
                if returned is not None and self._inert_factory(function):
                    try:
                        if self.calls.callers(resolution.module, function).limits:
                            return None
                        self.calls.invoke(resolution.module, function, CallSite(view.module, node), self._invocation)
                    except CallLimit:
                        return None
                    return self._shape_value(self._foreign(resolution.module), returned, depth + 1)
            return None
        return node, view

    @staticmethod
    def _projection_base(node: ast.expr) -> ast.expr | None:
        if isinstance(node, ast.Subscript):
            return node.value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            return node.func.value
        return None

    def _list_operand(self, view: _View, node: ast.expr, depth: int = 0, *, subject: ast.expr | None = None) -> bool:
        shape = self._shape_value(view, node, depth)
        if shape is None:
            return False
        node, view = shape
        if isinstance(node, ast.List | ast.Tuple):
            return True
        if isinstance(node, ast.BoolOp):
            return all(self._list_operand(view, value, depth + 1, subject=subject) for value in node.values)
        if isinstance(node, ast.IfExp):
            return self._list_operand(view, node.body, depth + 1, subject=subject) and self._list_operand(view, node.orelse, depth + 1, subject=subject)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._list_operand(view, node.left, depth + 1, subject=subject) and self._list_operand(view, node.right, depth + 1, subject=subject)
        base, key, default = None, None, None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            base, key = node.value, node.slice.value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and len(node.args) in {1, 2} and not node.keywords:
            if isinstance(node.args[0], ast.Constant):
                base, key = node.func.value, node.args[0].value
                default = node.args[1] if len(node.args) == 2 else None
        if base is None or not isinstance(key, str):
            return False
        if isinstance(base, ast.Name):
            same = self._projection_base(subject) if subject is not None else None
            lookup = view.lookup or bindings_at(view.scopes, view.bindings)
            if not (isinstance(same, ast.Name) and same.id == base.id and lookup(same.id, same) == lookup(base.id, base)):
                # Only the dictionary whose ownership this strict walk is
                # checking can lend its shape through a retained projection.
                return False
        elif not isinstance(base, ast.Call):
            return False
        dictionary = self._shape_value(view, base, depth + 1)
        if dictionary is None or not isinstance(dictionary[0], ast.Dict):
            return False
        literal, home = dictionary
        keys = [item.value for item in literal.keys if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        if len(keys) != len(literal.keys) or len(set(keys)) != len(keys):
            return False
        if key in keys:
            return self._list_operand(home, literal.values[keys.index(key)], depth + 1)
        return default is not None and self._list_operand(view, default, depth + 1)

    def _factory_member_change(
        self, view: _View, function: ast.FunctionDef | ast.AsyncFunctionDef, result: ListResolution,
        *, caller_defaults: set[tuple[int, int]] | frozenset = frozenset(),
    ) -> str | None:
        """Check canonical callable ownership across its bounded borrower scope.

        The fresh container does not own a fresh function. A caller, sibling
        importer or helper may still retain and change the same callable.
        """
        calls = self.calls
        if calls is None:
            return "the returned callable ownership census is unavailable"
        targets: dict[tuple[int, int], Resolution] = {}
        for member in result.members:
            origin = member.module or self.entry.module
            resolution = calls.resolve(origin, member.expr) if origin is not None else None
            if resolution is None or not resolution.resolved or resolution.caveats:
                return "the returned or caller-default callable ownership is not established"
            if (id(member.expr), id(member.module)) not in caller_defaults:
                local = member.module is view.module or (member.module is None and view.entry)
                if not local or not isinstance(member.expr, ast.Name):
                    return "the returned factory member's lexical callable ownership is not read by this increment"
            targets[id(resolution.module), id(resolution.definition)] = resolution
        key = (id(function), tuple(sorted(targets)))
        if key not in self._cache.callable_changes:
            self._cache.callable_changes[key] = self._retained_callable_changes(function, targets)
        changed = self._cache.callable_changes[key]
        if changed is not None:
            return changed
        # Empty projections still depend on the factory's live namespace.
        # A helper/class can retain that namespace even when no selected
        # member remains to seed the returned-callable census.
        factory = Resolution(reference=function.name, module=view.module, definition=function)
        key = ("factory namespace", id(function))
        if key not in self._cache.callable_changes:
            self._cache.callable_changes[key] = self._retained_callable_changes(
                function, {(id(view.module), id(function)): factory}, factory_calls=True,
            )
        return self._cache.callable_changes[key]

    def _executable_machinery(self, view: _View) -> bool:
        """Follow existing import evidence for executable/reflection machinery."""
        key = id(view.tree)
        if key in self._cache.executable_modules:
            return self._cache.executable_modules[key]
        # A dependency cycle cannot serve as an absent-machinery proof.
        self._cache.executable_modules[key] = True
        names = {"eval", "exec", "compile", "__import__", "__builtins__", "__globals__", "__closure__", "cell_contents", "__getattribute__", "__getattr__", "getattr", "setattr", "delattr"}
        for node in ast.walk(view.tree):
            self._visits += 1
            if self._visits > MAX_VISITS:
                return True
            if replaces_builtin_namespace(node, view.scopes):
                return True
            if isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load):
                spelling = reference_spelling(node)
                if spelling and spelling.rsplit(".", 1)[-1] in names:
                    return True
            unused_reflection = (
                self._resolver is not None and view.module is not None
                and _unused_reflection_import(self._resolver, view.module, node, self.calls)
            )
            if isinstance(node, ast.ImportFrom) and node.module == "builtins" and any(alias.name in names or alias.name == "*" for alias in node.names):
                if not unused_reflection:
                    return True
            if isinstance(node, ast.Import) and any(alias.name == "builtins" for alias in node.names):
                if not unused_reflection:
                    return True
            if self._resolver is None or self.calls is None or view.module is None:
                continue
            dependencies: set[str] = set()
            if isinstance(node, ast.Name | ast.Attribute):
                dependencies.update(step["path"] for step in self.calls.resolve(view.module, node).steps)
            if isinstance(node, ast.Import | ast.ImportFrom):
                try:
                    containers = (
                        [self._resolver._from_base(view.module, node)] if isinstance(node, ast.ImportFrom)
                        else [self._resolver._absolute(view.module, alias.name) for alias in node.names]
                    )
                    dependencies.update(self._resolver.ref(container.module_path) for container in containers if container.module_path is not None)
                except _Stop as exc:
                    if exc.reason != MODULE_NOT_FOUND:
                        return True
            for ref in dependencies - {view.ref}:
                try:
                    module = self._resolver.module(self._resolver.scope_root / ref)
                except (_Stop, ValueError):
                    return True
                if self._executable_machinery(self._foreign(module)):
                    return True
        self._cache.executable_modules[key] = False
        return False

    def _retained_callable_changes(
        self, function: ast.FunctionDef | ast.AsyncFunctionDef, targets: dict[tuple[int, int], Resolution],
        *, factory_calls: bool = False, factory_return: bool = True,
    ) -> str | None:
        from agents_shipgate.inputs.builder_calls import CallLimit

        calls = self.calls
        assert calls is not None
        for target in targets.values():
            home, defining = target.module, target.definition
            assert home is not None and defining is not None
            try:
                borrowers = calls.borrowers(home, defining.name, namespace_carriers=True)
                retaining = calls.retaining_modules(home, defining.name, namespace_carriers=True)
            except CallLimit as exc:
                return f"the returned callable ownership census is not established: {exc}"
            for borrower in borrowers:
                foreign = self._foreign(borrower)
                lookup = foreign.lookup or bindings_at(foreign.scopes, foreign.bindings)
                if self._reflection(foreign) is not None or calls.dynamic_importer(borrower) is not None or self._executable_machinery(foreign):
                    return "a returned callable's borrower has reflection, executable text, dynamic imports or unresolved namespace machinery"
                for node in ast.walk(borrower.tree):
                    self._visits += 1
                    if self._visits > MAX_VISITS:
                        return "the returned callable ownership guard is larger than the reader follows"
                    if not isinstance(node, ast.Name | ast.Attribute):
                        continue
                    resolution = calls.resolve(borrower, node)
                    owned = resolution.resolved and (
                        resolution.module is home and resolution.definition is defining
                    )
                    parent = foreign.scopes.parents.get(node)
                    if self._checking_constructor_namespaces and self._resolver is not None and isinstance(parent, ast.keyword) and parent.arg == "is_enabled":
                        decorator = foreign.scopes.parents.get(parent)
                        owner = foreign.scopes.parents.get(decorator)
                        recorded = self._resolver._constructor_decorator_owners.get(id(owner))
                        if recorded is not None and recorded == (borrower, owner) and decorator in owner.decorator_list:
                            continue  # The outer census owns this exact SDK callback edge.
                    if owned and factory_calls and isinstance(parent, ast.Call) and parent.func is node:
                        continue  # Every direct result is checked by the factory caller census.
                    spelling = reference_spelling(node)
                    head = spelling.split(".", 1)[0] if spelling else None
                    namespace = resolution.resolved and resolution.module.path in retaining
                    if not resolution.resolved and borrower.path in retaining and head:
                        namespace = any(isinstance(binding, ast.ClassDef) for binding, _ in lookup(head, node))
                    if namespace and not owned and resolution.resolved and isinstance(parent, ast.Call) and parent.func is node:
                        continue  # Its read body is in the same bounded borrower census.
                    if not owned and not namespace:
                        # A namespace may retain the defining module through a
                        # bridge or parent package. Resolve its import identity,
                        # not only the terminal module reported by resolve().
                        if not resolution.resolved and not (
                            isinstance(parent, ast.Attribute) and parent.value is node
                        ):
                            spelling = reference_spelling(node)
                            head = spelling.split(".", 1)[0] if spelling else None
                            for binding, statement in lookup(head, node) if head else ():
                                if isinstance(binding, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
                                    try:
                                        retained = calls.import_may_share(borrower, home, defining.name, statement, binding, namespace_carriers=True)
                                    except CallLimit as exc:
                                        return f"the returned callable namespace census is not established: {exc}"
                                    if retained:
                                        return f"a namespace retaining a returned callable is used opaquely at {self._where(foreign, node)}"
                        continue
                    current: ast.AST = node
                    while isinstance(foreign.scopes.parents.get(current), ast.List | ast.Tuple | ast.Dict | ast.Starred | ast.BinOp | ast.BoolOp | ast.IfExp):
                        current = foreign.scopes.parents[current]
                    outer = foreign.scopes.parents.get(current)
                    if factory_return and isinstance(outer, ast.Return) and outer is function.body[-1]:
                        continue
                    if current is not node and isinstance(current, ast.expr) and self._list_operand(foreign, current, subject=node):
                        if self._container_read(foreign, current):
                            continue
                        if (
                            isinstance(outer, ast.Call) and len(outer.args) == 2 and outer.args[1] is current
                            and isinstance(outer.func, ast.Attribute) and outer.func.attr == "get"
                            and self._nonexecuting_default(foreign, current) and self._container_read(foreign, outer)
                        ):
                            continue
                    spelling = reference_spelling(node) or _source(node)
                    return f"the returned callable {spelling!r} is invoked, changed or handed on at {self._where(foreign, node)}"
        return None

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
        if self._projection is not None and self._container_binding_changed(view, binding):
            return self._stop(view, statement, f"`{name}` or one of its projected values may be changed or handed on")
        if self._projection is not None and statement in view.tree.body and isinstance(statement.value, ast.Dict):
            return self._stop(view, statement, "module-owned dictionary projection is not an established fresh factory container")
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

        memo = (
            key[0], view.entry, key[1], self._invocation.key if self._invocation else (),
            self._projection.identity if self._projection else (),
        )
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
        if self._projection is not None:
            return self._stop(view, node, "an imported dictionary container's projected ownership is not read by this increment")
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
                    f"`{name}` in {foreign.ref} may be changed in place at {other.ref}:{changer}"
                    + self._change_refusal_suffix(other, defining, name),
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
                        f"`{name}` in {defining.ref} may be changed in place at {other.ref}:{changer}"
                        + self._change_refusal_suffix(other, defining, name),
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
            selected: list[tuple[ast.alias, ast.stmt]] = []
            self._cache.changes[key] = self._first_change_reaching(view, defining, name, selected_import=selected)
            self._cache.change_contexts[key] = (
                view.changed_import_context.get(selected[0], ())
                if self._cache.changes[key] is not None and selected else ()
            )
        return self._cache.changes[key]

    def _change_refusal_suffix(self, view: _View, defining: PythonModule, name: str) -> str:
        key = (id(view.tree), view.entry, id(defining.tree), name)
        rows = self._cache.change_contexts.get(key, ())
        return " Additional constructor refusal context: " + "; ".join(rows) if rows else ""

    def _first_change_reaching(
        self, view: _View, defining: PythonModule, name: str, *,
        selected_import: list[tuple[ast.alias, ast.stmt]] | None = None,
    ) -> int | None:
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
        def selected_line(alias: ast.alias, statement: ast.stmt) -> int:
            if selected_import is not None:
                selected_import.append((alias, statement))
            return statement.lineno

        for alias, statement in view.changed_imports:
            local = alias.asname or alias.name.split(".", 1)[0]
            direct = self._resolver.resolve_local_import(view.module, statement, alias, local)
            if direct.module is defining and _value_name(direct) == name:
                return selected_line(alias, statement)
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
                    return selected_line(alias, statement)
                if possible and resolution.reason == NOT_A_FUNCTION:
                    # A namespace can reach another namespace under a renamed
                    # export. This imported binding was marked as possibly
                    # mutated, including opaque consumers and computed access.
                    return selected_line(alias, statement)
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
                    return selected_line(alias, statement)
        return None

    def _attribute(self, node: ast.Attribute, view: _View, depth: int, seen: frozenset) -> ListResolution:
        if node.attr in CAPABILITY_FIELDS and isinstance(node.value, ast.Name) and self._member_receiver_owned(view, node):
            lookup = view.lookup or bindings_at(view.scopes, view.bindings)
            bindings = lookup(node.value.id, node.value)
            call = getattr(bindings[0][1], "value", None) if len(bindings) == 1 else None
            if isinstance(call, ast.Call) and not self._agent_result_changed(
                view, call, node.attr, strict_container=True, initial_field=True,
            ):
                field = self._member_agent_field(
                    view, call, node.attr, self._invocation, depth, frozenset(), initial_field=True,
                )
                if field is not None:
                    home, expression, invocation = field
                    previous, self._invocation = self._invocation, invocation
                    try:
                        result = self._resolve(expression, home, depth + 1, seen | {(id(view.tree), id(node))})
                    finally:
                        self._invocation = previous
                    if invocation is not None:
                        for condition in invocation.conditions:
                            result = result.under(condition)
                    return result
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
