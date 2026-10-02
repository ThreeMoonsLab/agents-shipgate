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
from dataclasses import dataclass
from typing import Any

from agents_shipgate.inputs.python_imports import (
    ImportResolver,
    PythonModule,
    ScopeIndex,
    reference_spelling,
    reflective_access,
)
from agents_shipgate.inputs.python_static import dotted_name

MAX_DEPTH = 16

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
_COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)


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


@dataclass(frozen=True)
class UnresolvedPart:
    reason: str
    location: str


@dataclass(frozen=True)
class ListResolution:
    members: tuple[ListMember, ...] = ()
    unresolved: tuple[UnresolvedPart, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unresolved

    def __add__(self, other: ListResolution) -> ListResolution:
        return ListResolution(self.members + other.members, self.unresolved + other.unresolved)

    def under(self, condition: str) -> ListResolution:
        return ListResolution(
            tuple(
                ListMember(m.expr, m.module, (condition, *m.conditions), m.via)
                for m in self.members
            ),
            self.unresolved,
        )

    def through(self, step: str) -> ListResolution:
        return ListResolution(
            tuple(ListMember(m.expr, m.module, m.conditions, (step, *m.via)) for m in self.members),
            self.unresolved,
        )


def bindings_at(scopes: ScopeIndex, module_bindings: dict[str, list[Any]]) -> BindingsAt:
    # ``from helpers import *`` may bind any name: none is proven unbound.
    star = any(isinstance(node, ast.alias) and node.name == "*" for node in scopes.parents)

    def found(name: str, site: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
        local = scopes.enclosing_bindings(site, name)
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
    function: ast.FunctionDef | ast.AsyncFunctionDef, name: str, bindings: BindingsAt
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
                node, parents, lambda call, *_: leaves_arguments_alone(call, bindings)
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
        return (
            parent.attr in {"count", "index", "copy", "get", "keys", "values", "items"}
            and isinstance(grand, ast.Call)
            and grand.func is parent
        )
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
    ) -> None:
        self._resolver = resolver
        self._agent_reads = agent_reads
        self._views: dict[int, _View] = {}
        self.entry = self._view(ref, tree, scopes, bindings, module)

    # -- views -------------------------------------------------------------

    def _view(
        self,
        ref: str,
        tree: ast.Module,
        scopes: ScopeIndex,
        bindings: dict[str, list[Any]],
        module: PythonModule | None,
    ) -> _View:
        view = _View(ref, tree, scopes, bindings, module, set())
        self._views[id(tree)] = view
        self._index_changes(view)
        return view

    def _foreign(self, module: PythonModule) -> _View:
        view = self._views.get(id(module.tree))
        if view is None:
            view = self._view(module.ref, module.tree, ScopeIndex(module.tree), module.bindings, module)
        return view

    def _index_changes(self, view: _View) -> None:
        """Mark every binding some code may change in place (#879 review)."""

        tracked = {
            target.id
            for node in ast.walk(view.tree)
            if isinstance(node, ast.Assign | ast.AnnAssign)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        } | {
            (alias.asname or alias.name).split(".", 1)[0]
            for node in ast.walk(view.tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
            if alias.name != "*"
        }
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
            elif isinstance(node, ast.Name) and node.id in tracked:
                parent = view.scopes.parents.get(node)
                if isinstance(node.ctx, ast.Load):
                    unchanged = read_only_use(node, view.scopes.parents, call_reads)
                else:
                    unchanged = isinstance(node.ctx, ast.Store) and not isinstance(parent, ast.AugAssign)
                if not unchanged:
                    found = view.scopes.enclosing_bindings(node, node.id)
                    view.changed.add(id(found[0]) if found else ("module", node.id))

    def _call_reads(self, view: _View) -> CallReads:
        bindings = bindings_at(view.scopes, view.bindings)

        def reads(call: ast.Call, position: int | None, keyword: str | None) -> bool:
            if leaves_arguments_alone(call, bindings):
                return True
            if self._agent_reads(call, keyword):
                return True
            return self._callee_leaves_alone(view, call, position, keyword)

        return reads

    def _callee_leaves_alone(
        self, view: _View, call: ast.Call, position: int | None, keyword: str | None
    ) -> bool:
        """Whether the function ``call`` names never changes the argument it passes."""

        spelling = reference_spelling(call.func)
        if spelling is None or self._resolver is None or view.module is None:
            return False
        resolution = self._resolver.resolve(view.module, spelling)
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
        return parameter_left_alone(
            function, parameter, bindings_at(ScopeIndex(defining.tree), defining.bindings)
        )

    # -- resolution --------------------------------------------------------

    def resolve(self, expr: ast.expr | None) -> ListResolution:
        """The members ``expr``, read in the entry module, can hold."""

        if expr is None:
            return ListResolution()
        return self._resolve(expr, self.entry, 0, frozenset())

    def _where(self, view: _View, node: ast.AST) -> str:
        return f"{view.ref}:{getattr(node, 'lineno', '?')}"

    def _stop(self, view: _View, node: ast.AST, reason: str) -> ListResolution:
        return ListResolution(unresolved=(UnresolvedPart(reason, self._where(view, node)),))

    def _member(self, view: _View, node: ast.expr) -> ListResolution:
        return ListResolution(members=(ListMember(node, None if view is self.entry else view.module),))

    def _resolve(self, node: ast.expr, view: _View, depth: int, seen: frozenset) -> ListResolution:
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
        test = _source(node.test)
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
            if index == last:
                return result + _all_under(part, reached)
            if fixed and part.members:
                # Known not empty: it is the value, and nothing after it is reached.
                return result + _all_under(part, reached)
            if fixed:
                # Known empty: never the value.
                continue
            text = _source(value)
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
        kept = " and ".join(_source(test) for test in generators[0].ifs)
        return part.under(f"the filter `{kept}` keeps it")

    def _call(self, node: ast.Call, view: _View, depth: int, seen: frozenset) -> ListResolution:
        func = node.func
        builtin = (
            func.id
            if isinstance(func, ast.Name) and not bindings_at(view.scopes, view.bindings)(func.id, func)
            else None
        )
        if builtin in _SAME_MEMBERS and len(node.args) == 1 and not node.keywords:
            return self._resolve(node.args[0], view, depth + 1, seen)
        if builtin == "filter" and len(node.args) == 2 and not node.keywords:
            predicate, iterable = node.args
            part = self._resolve(iterable, view, depth + 1, seen)
            if isinstance(predicate, ast.Constant) and predicate.value is None:
                return part
            return part.under(f"the filter `{_source(predicate)}` keeps it")
        return self._stop(view, node, f"a call to `{_source(func)}`, whose result is not read")

    def _name(self, node: ast.Name, view: _View, depth: int, seen: frozenset) -> ListResolution:
        name = node.id
        local = view.scopes.enclosing_bindings(node, name)
        if local:
            if len(local) != 1:
                return self._stop(view, node, f"`{name}` is bound more than once in its function")
            binding = local[0]
            if isinstance(binding, ast.arg):
                function = _enclosing_function(view.scopes, binding)
                where = f" of `{function.name}`" if function is not None else ""
                return self._stop(view, node, f"`{name}` is a parameter{where}, so its value comes from a caller")
            statement = view.scopes.statement_of(binding)
            if isinstance(binding, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
                return self._imported_local(node, binding, statement, view, depth, seen)
            return self._assigned(
                node, binding, statement, view, depth, seen, changed=id(binding) in view.changed
            )
        bindings = view.bindings.get(name, [])
        if not bindings:
            return self._stop(view, node, f"`{name}` is not bound where the list is read")
        if len(bindings) != 1 or not bindings[0].top_level:
            return self._stop(view, node, f"`{name}` is bound more than once, or conditionally, in {view.ref}")
        binding = bindings[0]
        if isinstance(binding.node, ast.alias):
            return self._imported(node, name, view, depth, seen)
        return self._assigned(
            node, binding.node, binding.statement, view, depth, seen,
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
            kind = "a function" if isinstance(binding, ast.FunctionDef | ast.AsyncFunctionDef) else "not a list"
            return self._stop(view, node, f"`{name}` is {kind}, not a list of tools")
        if _inside_compound(view.scopes, statement):
            return self._stop(view, statement, f"`{name}` is bound only under a condition or in a loop")
        if changed:
            return self._stop(view, statement, f"`{name}` may be changed in place after it is built")
        key = (id(view.tree), id(statement))
        if key in seen:
            return self._stop(view, statement, f"`{name}` refers to itself")
        step = f"{name} ({view.ref}:{statement.lineno})"
        return self._resolve(statement.value, view, depth + 1, seen | {key}).through(step)

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
            return self._stop(view, node, f"`{spelling}` is not established: {'; '.join(resolution.caveats)}")
        foreign = self._foreign(defining)
        name = next(
            (step["name"] for step in reversed(resolution.steps) if step.get("binding") == "value"),
            None,
        )
        if name is None:
            return self._stop(view, node, f"`{spelling}` does not end at an assignment")
        bindings = foreign.bindings.get(name, [])
        if len(bindings) != 1 or not bindings[0].top_level or ("module", name) in foreign.changed:
            return self._stop(view, node, f"`{name}` in {foreign.ref} may be changed in place or rebound")
        head = spelling.split(".", 1)[0]
        if ("module", head) in view.changed:
            return self._stop(view, node, f"`{head}` may be changed in place in {view.ref}")
        statement = bindings[0].statement
        key = (id(foreign.tree), id(statement))
        if key in seen:
            return self._stop(view, node, f"`{spelling}` refers to itself")
        step = f"{name} ({foreign.ref}:{statement.lineno})"
        return self._resolve(value, foreign, depth + 1, seen | {key}).through(step)

    def _attribute(self, node: ast.Attribute, view: _View, depth: int, seen: frozenset) -> ListResolution:
        spelling = reference_spelling(node)
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in {"self", "cls"}:
            return self._stop(view, node, f"`{_source(node)}` is an attribute of the object, set elsewhere")
        if spelling is None:
            return self._stop(view, node, f"`{_source(node)}` is not an expression the reader follows")
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


def _inside_compound(scopes: ScopeIndex, statement: ast.AST) -> bool:
    current = scopes.parents.get(statement)
    while current is not None and not isinstance(
        current, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ):
        if isinstance(current, _COMPOUND):
            return True
        current = scopes.parents.get(current)
    return False


def _enclosing_function(scopes: ScopeIndex, node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = scopes.parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current
        current = scopes.parents.get(current)
    return None


__all__ = [
    "MAX_DEPTH",
    "READ_ONLY_CALLS",
    "LOG_METHODS",
    "AgentReads",
    "BindingsAt",
    "CallReads",
    "Conditions",
    "ListExpressions",
    "ListMember",
    "ListResolution",
    "UnresolvedPart",
    "bindings_at",
    "leaves_arguments_alone",
    "parameter_left_alone",
    "read_only_use",
    "source_text",
    "unread_list_reason",
    "unread_parts",
]
