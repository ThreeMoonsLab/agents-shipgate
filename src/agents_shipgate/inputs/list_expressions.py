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
    callees: dict[tuple[int, str, bool], bool] = field(default_factory=dict)
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
        self._builder_calls = builder_calls
        self._invocation: Invocation | None = None
        self._field = "tools"
        self._projection: _Projection | None = None
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
        if view.entry and self._agent_reads(call, keyword):
            return True
        return bool(
            view.module is not None
            and self._module_agent_reads is not None
            and self._module_agent_reads(view.module, call, keyword)
        )

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

        try:
            calls.invoke(resolution.module, function, CallSite(view.module, call))
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
        key = (id(function), parameter, strict_container)
        if key not in self._cache.callees:
            # A recursive forwarding cycle is not a read-only proof.
            self._cache.callees[key] = False
            if strict_container:
                try:
                    if calls.callers(defining, function).limits:
                        return False
                except CallLimit:
                    return False
            foreign = self._foreign(defining)
            reads = self._call_reads(foreign)

            def safe_read(inner: ast.Call, position: int | None, field: str | None) -> bool:
                if field is not None and self._reads_agent(foreign, inner, field):
                    return not self._agent_result_changed(foreign, inner, field, borrowed=True)
                return reads(inner, position, field)

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
        return self._cache.callees[key]

    def _agent_result_changed(
        self, view: _View, call: ast.Call, keyword: str, *, borrowed: bool = False,
        strict_container: bool = False,
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
                        self._foreign(site.module), site.call, keyword, borrowed=borrowed,
                        strict_container=strict_container,
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
                            self._foreign(site.module), site.call, keyword, borrowed=borrowed,
                            strict_container=strict_container,
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
                    if isinstance(use.ctx, ast.Load) and not (
                        self._container_read(view, use) if strict_container
                        else read_only_use(use, view.scopes.parents, self._call_reads(view))
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

    def _factory_result_changed(self, view: _View, call: ast.Call) -> bool:
        if reflective_access(view.tree) is not None:
            return True
        if view.module is not None and self.calls is not None and self.calls.exported_elsewhere(view.module, call):
            return True
        parent = view.scopes.parents.get(call)
        if not isinstance(parent, ast.Assign | ast.AnnAssign) or parent.value is not call:
            return not self._container_read(view, call, dictionary=self._projection is not None)
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            return True
        return self._container_binding_changed(view, targets[0], dictionary=self._projection is not None)

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
        if isinstance(parent, ast.BoolOp) or (
            isinstance(parent, ast.IfExp) and parent.test is not node
        ):
            return self._container_read(view, parent, dictionary=dictionary)
        if isinstance(parent, ast.Starred) and parent.value is node:
            outer = view.scopes.parents.get(parent)
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
                if parent.attr == "copy" and not call.args and not call.keywords:
                    return self._container_read(view, call)
                return parent.attr in {"count", "index"} and all(
                    self._nonexecuting_default(view, argument) for argument in call.args
                ) and not call.keywords
            return False
        call, position, keyword = None, None, None
        if isinstance(parent, ast.keyword):
            call, keyword = view.scopes.parents.get(parent), parent.arg
        elif isinstance(parent, ast.Call):
            call = parent
            position = next((i for i, argument in enumerate(call.args) if argument is node), None)
        if isinstance(call, ast.Call):
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
            if isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load):
                spelling = reference_spelling(node)
                if spelling and spelling.rsplit(".", 1)[-1] in names:
                    return True
            if isinstance(node, ast.ImportFrom) and node.module == "builtins" and any(alias.name in names or alias.name == "*" for alias in node.names):
                return True
            if isinstance(node, ast.Import) and any(alias.name == "builtins" for alias in node.names):
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
                if reflective_access(borrower.tree) is not None or calls.dynamic_importer(borrower) is not None or self._executable_machinery(foreign):
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
                    if isinstance(outer, ast.Return) and outer is function.body[-1]:
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
