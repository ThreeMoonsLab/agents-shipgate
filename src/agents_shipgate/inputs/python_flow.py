"""What an agent's capability argument receives from the code around it (#874).

A builder names no tool::

    def create_secretary_agent(tools, model):
        return LlmAgent(name="SecretaryAgent", model=model, tools=tools)

The list is chosen where the builder is called, often in another module and
often through a factory's dict::

    tool_groups = create_tools(user_id=user_id, db=db)
    secretary = create_secretary_agent(tools=tool_groups["calendar"] + tool_groups["email"])

A reader that meets a capability keyword it cannot read as a literal list asks
this module what the expression evaluates to, from source alone:

* Literal lists and tuples, ``+`` and ``*`` splices of them, and ``x or [...]``
  when every operand before the one taken is a known list or ``None``.
* A name bound once, in its enclosing scope or at module level, to such a
  value, and never changed in place.
* A literal-key subscript or ``.get`` of a dict literal, including one a
  factory function returns; the value of a factory function's single
  ``return``.
* A parameter of a module-level function: the value its one call site in the
  read scope passes, to a bounded depth. Finding that call site is a census
  of the read scope. Any reference to the function other than a direct call
  (the function passed as a value, named in a string, reached through a
  binding the resolver cannot prove) stops the flow, because a caller the
  census cannot see could pass anything. Test code is not read, as it is not
  read as the application anywhere else (#876). Two call sites stop the flow
  too: their lists are never merged.

The answer is the list's elements, each with the module and position it is
written at, and the call sites followed. The reader resolves each element as it
resolves any tool reference. Everything else is a :class:`FlowStop` with a
named reason, which the reader reports as a limit on that agent, never as an
empty list. Nothing is imported or executed. Every file the census reads goes
through the snapshot-aware input reader, so it is part of the run's input
identity.
"""

from __future__ import annotations

import ast
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import is_test_path, list_input_directory, load_text_file
from agents_shipgate.inputs.python_imports import (
    LOCAL_BINDING,
    MODULE_NOT_FOUND,
    NAME_NOT_DEFINED,
    NOT_A_FUNCTION,
    NOT_BOUND,
    OUTSIDE_SCOPE,
    ImportResolver,
    PythonModule,
    Resolution,
    ScopeIndex,
    _leaves_arguments_alone,
    _read_only_use,
    _Stop,
    local_binding_detail,
    reference_spelling,
)

#: Stable reason codes; the sentence is :attr:`FlowStop.detail`.
NO_CALL_SITE = "no_call_site"
MULTIPLE_CALL_SITES = "multiple_call_sites"
UNACCOUNTED_REFERENCE = "unaccounted_reference"
UNSUPPORTED_EXPRESSION = "unsupported_expression"
OPAQUE_ARGUMENTS = "opaque_arguments"
MISSING_ARGUMENT = "missing_argument"
MUTATED_VALUE = "mutated_value"
MISSING_KEY = "missing_key"
FLOW_LIMIT = "flow_limit"

#: Functions whose callers one evaluation follows before stopping.
MAX_FLOW_DEPTH = 4
#: Indirections one evaluation follows before stopping.
MAX_FLOW_STEPS = 64
#: Python files the census lists before stopping.
MAX_CENSUS_FILES = 2000
#: Files mentioning one function's name that the census parses.
MAX_CENSUS_CANDIDATES = 32

#: Directories the census never enters, whatever they hold: build caches and
#: installed packages, not the application.
_SKIPPED_DIRECTORIES = frozenset({"__pycache__", "node_modules", "site-packages"})
#: Methods that change a list's or a dict's members in place.
_MUTATORS = frozenset(
    {"append", "extend", "insert", "remove", "pop", "clear", "update", "setdefault", "popitem"}
)
#: Keywords that hand a list to an agent, which reads it and never changes it.
_CAPABILITY_KEYWORDS = frozenset({"tools", "handoffs", "mcp_servers", "sub_agents"})
#: Resolution outcomes that prove a reference is *not* a given in-scope
#: function: nothing binds it, a local of the same name shadows it, the module
#: it names is found but does not define it, or it is found outside the scope.
_ELSEWHERE = frozenset({NOT_BOUND, LOCAL_BINDING, NAME_NOT_DEFINED, NOT_A_FUNCTION, OUTSIDE_SCOPE})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

Function = ast.FunctionDef | ast.AsyncFunctionDef


class FlowStop(Exception):
    """A value the flow cannot establish, and why."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class AgentRule(Protocol):
    """A reader's own answer to "is this an agent, and what is it called?"."""

    def constructs(self, module: PythonModule, call: ast.Call) -> bool:
        """Whether ``call`` constructs one of the reader's agents."""

    def identity(self, module: PythonModule, call: ast.Call, assigned: str | None) -> str | None:
        """The identity of the agent ``call`` constructs, when the construction names it."""


@dataclass(frozen=True)
class CallSite:
    """One direct call of a builder."""

    module: PythonModule
    call: ast.Call

    @property
    def location(self) -> str:
        return f"{self.module.ref}:{self.call.lineno}"


@dataclass(frozen=True)
class Element:
    """One element of a flowed list, read where it is written."""

    module: PythonModule
    node: ast.expr
    #: The node whose enclosing scopes a name in ``node`` is looked up from:
    #: the element itself, except inside a parameter default, which Python
    #: evaluates where the ``def`` stands.
    context: ast.AST

    @property
    def location(self) -> str:
        return f"{self.module.ref}:{_line(self.node)}"


@dataclass
class Flow:
    """The elements an expression evaluated to, and what was read to get them."""

    elements: list[Element] = field(default_factory=list)
    #: Call sites followed, in the order they were entered: the first one is
    #: the call of the agent's own builder.
    via: list[CallSite] = field(default_factory=list)
    #: ``module ref -> sha256`` of every module the evaluation read.
    inputs: dict[str, str] = field(default_factory=dict)

    @property
    def call_site(self) -> str | None:
        return self.via[0].location if self.via else None

    def evidence(self) -> dict[str, object]:
        return {
            "call_sites": [site.location for site in self.via],
            "inputs": [
                {"path": path, "sha256": digest} for path, digest in sorted(self.inputs.items())
            ],
        }


@dataclass
class _State:
    flow: Flow
    #: The call site chosen for each builder whose parameters were read.
    sites: dict[int, CallSite] = field(default_factory=dict)
    #: ``(function, parameter)`` pairs being evaluated, for cycles.
    active: set[tuple[int, str]] = field(default_factory=set)
    steps: int = 0
    #: Whether the value followed is a list or dict whose members matter, so
    #: any use that could change it stops the flow. An agent object named as
    #: a handoff is not: putting it in a list is how it is used.
    guard_changes: bool = True

    def step(self) -> None:
        self.steps += 1
        if self.steps > MAX_FLOW_STEPS:
            raise FlowStop(
                FLOW_LIMIT, f"the value passes through more than {MAX_FLOW_STEPS} bindings"
            )

    def read(self, module: PythonModule) -> None:
        self.flow.inputs[module.ref] = module.sha256


class ParameterFlow:
    """Argument evaluation and call-site census over one resolver's scope."""

    def __init__(
        self,
        resolver: ImportResolver,
        *,
        agent_call: Callable[[PythonModule, ast.Call], bool] | None = None,
    ) -> None:
        self.resolver = resolver
        #: The reader's own answer to "does this call construct an agent (or a
        #: copy), which reads the list it is handed as ``tools=``?".
        self.agent_call = agent_call
        self._scopes: dict[str, ScopeIndex] = {}
        self._inventory: list[Path] | FlowStop | None = None
        self._names_in_scope: set[str] = set()
        self._texts: dict[Path, str | None] = {}
        self._census: dict[tuple[str, int], list[CallSite] | FlowStop] = {}
        #: ``(function, parameter)`` pairs whose changes are being checked.
        self._checking: set[tuple[int, str]] = set()

    def scopes(self, module: PythonModule) -> ScopeIndex:
        index = self._scopes.get(module.ref)
        if index is None:
            index = self._scopes[module.ref] = ScopeIndex(module.tree)
        return index

    # -- entry points -----------------------------------------------------------

    def elements(self, module: PythonModule, expr: ast.expr) -> Flow:
        """The elements ``expr`` evaluates to where it stands; raise :class:`FlowStop`."""

        state = _State(Flow())
        state.read(module)
        state.flow.elements = self._list(module, expr, expr, state)
        return state.flow

    def agent_name(self, element: Element, rule: AgentRule) -> str:
        """The identity of the agent one flowed element denotes; raise :class:`FlowStop`."""

        state = _State(Flow(), guard_changes=False)
        module, node, context = element.module, element.node, element.context
        assigned = node.id if isinstance(node, ast.Name) else None
        module, node, context = self._settle(module, node, context, state)
        if isinstance(node, ast.Call):
            if rule.constructs(module, node):
                name = rule.identity(module, node, assigned)
                if name is not None:
                    return name
                raise FlowStop(
                    UNSUPPORTED_EXPRESSION,
                    f"the agent constructed at {module.ref}:{node.lineno} has no literal name",
                )
            return self._returned_agent(module, node, context, rule, state)
        raise FlowStop(
            UNSUPPORTED_EXPRESSION,
            f"{_spelling(element.node)} at {element.location} is not an agent construction "
            "this reader follows",
        )

    def resolve(self, element: Element) -> Resolution:
        """The function definition one element names, read in its own module's scopes."""

        spelling = reference_spelling(element.node)
        if spelling is None:
            return Resolution(
                reference=_spelling(element.node),
                reason=UNSUPPORTED_EXPRESSION,
                detail=f"{_spelling(element.node)} at {element.location} is not a name",
            )
        head = spelling.split(".", 1)[0]
        index = self.scopes(element.module)
        found = index.enclosing_bindings(element.context, head)
        if len(found) > 1:
            return Resolution(
                reference=spelling,
                reason=LOCAL_BINDING,
                detail=local_binding_detail(element.module.ref, head, found[0], rebound=True),
            )
        if not found:
            return self.resolver.resolve(element.module, spelling)
        local = found[0]
        if isinstance(local, _FUNCTIONS) and spelling == head:
            return Resolution(
                reference=spelling,
                module=element.module,
                definition=local,
                steps=(
                    {
                        "path": element.module.ref,
                        "line": local.lineno,
                        "name": head,
                        "sha256": element.module.sha256,
                        "binding": "local_definition",
                    },
                ),
            )
        statement = index.statement_of(local)
        if isinstance(local, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
            return self.resolver.resolve_local_import(element.module, statement, local, spelling)
        return Resolution(
            reference=spelling,
            reason=LOCAL_BINDING,
            detail=local_binding_detail(element.module.ref, head, local),
        )

    def call_sites(self, module: PythonModule, function: Function) -> list[CallSite]:
        """Every direct call of ``function`` in the read scope; raise :class:`FlowStop`."""

        key = (module.ref, function.lineno)
        cached = self._census.get(key)
        if isinstance(cached, FlowStop):
            raise cached
        if cached is not None:
            return cached
        try:
            sites = self._take_census(module, function)
        except FlowStop as stop:
            self._census[key] = stop
            raise
        self._census[key] = sites
        return sites

    # -- evaluation -------------------------------------------------------------

    def _list(
        self, module: PythonModule, expr: ast.expr, context: ast.AST, state: _State
    ) -> list[Element]:
        state.step()
        if isinstance(expr, ast.List | ast.Tuple):
            items: list[Element] = []
            for item in expr.elts:
                inner = _context(item, expr, context)
                if isinstance(item, ast.Starred):
                    items.extend(
                        self._list(module, item.value, _context(item.value, item, inner), state)
                    )
                else:
                    items.append(Element(module, item, inner))
            return items
        if isinstance(expr, ast.Constant) and expr.value is None:
            return []
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return [
                *self._list(module, expr.left, _context(expr.left, expr, context), state),
                *self._list(module, expr.right, _context(expr.right, expr, context), state),
            ]
        if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or):
            # A list is truthy exactly when it has an element, so the first
            # operand with elements is the value. An operand that cannot be
            # evaluated stops the whole expression: it might be the one taken.
            for operand in expr.values[:-1]:
                items = self._list(module, operand, _context(operand, expr, context), state)
                if items:
                    return items
            last = expr.values[-1]
            return self._list(module, last, _context(last, expr, context), state)
        found_module, value, found_context = self._value(module, expr, context, state)
        if isinstance(value, ast.Call):
            found_module, value, found_context = self._returned(
                found_module, value, found_context, state
            )
        elif value is expr and found_module is module:
            raise FlowStop(
                UNSUPPORTED_EXPRESSION,
                f"{_spelling(expr)} at {module.ref}:{_line(expr)} is not a list this "
                "reader follows",
            )
        return self._list(found_module, value, found_context, state)

    def _settle(
        self, module: PythonModule, expr: ast.expr, context: ast.AST, state: _State
    ) -> tuple[PythonModule, ast.expr, ast.AST]:
        while True:
            found = self._value(module, expr, context, state)
            if found[1] is expr and found[0] is module:
                return found
            module, expr, context = found

    def _value(
        self, module: PythonModule, expr: ast.expr, context: ast.AST, state: _State
    ) -> tuple[PythonModule, ast.expr, ast.AST]:
        """Follow one indirection: a name, a module attribute, a subscript, a ``.get``.

        Returns ``expr`` itself when it is none of those.
        """

        state.step()
        if isinstance(expr, ast.Name):
            return self._name(module, expr, context, state)
        if isinstance(expr, ast.Attribute) and reference_spelling(expr) is not None:
            spelling = reference_spelling(expr)
            assert spelling is not None
            head = spelling.split(".", 1)[0]
            if self.scopes(module).enclosing_bindings(context, head):
                raise FlowStop(
                    UNSUPPORTED_EXPRESSION,
                    f"{spelling!r} at {module.ref}:{_line(expr)} reads an attribute of a "
                    "local value",
                )
            return self._bound_value(module, spelling, state)
        key: str | None = None
        container: ast.expr | None = None
        default: ast.expr | None = None
        if (
            isinstance(expr, ast.Subscript)
            and isinstance(expr.slice, ast.Constant)
            and isinstance(expr.slice.value, str)
        ):
            key, container = expr.slice.value, expr.value
        elif (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Attribute)
            and expr.func.attr == "get"
            and not expr.keywords
            and 1 <= len(expr.args) <= 2
            and isinstance(expr.args[0], ast.Constant)
            and isinstance(expr.args[0].value, str)
        ):
            key, container = expr.args[0].value, expr.func.value
            default = expr.args[1] if len(expr.args) == 2 else None
        if key is None or container is None:
            return module, expr, context
        dict_module, mapping, dict_context = self._mapping(
            module, container, _context(container, expr, context), state
        )
        # A key written twice keeps its last value, as Python does.
        for item_key, item_value in reversed(list(zip(mapping.keys, mapping.values, strict=True))):
            if isinstance(item_key, ast.Constant) and item_key.value == key:
                return dict_module, item_value, _context(item_value, mapping, dict_context)
        if isinstance(expr, ast.Call):
            # ``.get`` of an absent key: its default, or None.
            if default is not None:
                return module, default, _context(default, expr, context)
            none = ast.copy_location(ast.Constant(value=None), expr)
            return module, none, none
        raise FlowStop(
            MISSING_KEY, f"the dict at {dict_module.ref}:{_line(mapping)} has no key {key!r}"
        )

    def _name(
        self, module: PythonModule, node: ast.Name, context: ast.AST, state: _State
    ) -> tuple[PythonModule, ast.expr, ast.AST]:
        index = self.scopes(module)
        found = index.enclosing_bindings(context, node.id)
        if len(found) > 1:
            raise FlowStop(
                LOCAL_BINDING, local_binding_detail(module.ref, node.id, found[0], rebound=True)
            )
        if not found:
            # The name as this module spells it — an import of another module's
            # list — can be changed here too.
            if state.guard_changes:
                self._unmutated(module, node.id, module.tree)
            return self._bound_value(module, node.id, state)
        local = found[0]
        if isinstance(local, ast.arg):
            return self._argument(module, local, state)
        statement = index.statement_of(local)
        if (
            isinstance(local, ast.Name)
            and statement is not None
            and _single_target(statement) is local
        ):
            if state.guard_changes:
                self._unmutated(module, node.id, self._scope_of(module, local))
            value = statement.value  # type: ignore[union-attr]
            assert value is not None
            return self._value(module, value, value, state)
        if isinstance(local, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
            resolution = self.resolver.resolve_local_import(module, statement, local, node.id)
            return self._resolved_value(resolution, state)
        raise FlowStop(LOCAL_BINDING, local_binding_detail(module.ref, node.id, local))

    def _bound_value(
        self, module: PythonModule, spelling: str, state: _State
    ) -> tuple[PythonModule, ast.expr, ast.AST]:
        """A module-level name, or one imported, bound once to a value."""

        return self._resolved_value(self.resolver.resolve(module, spelling), state)

    def _resolved_value(
        self, resolution: Resolution, state: _State
    ) -> tuple[PythonModule, ast.expr, ast.AST]:
        if resolution.value is None or resolution.module is None:
            if resolution.resolved:
                assert resolution.module is not None and resolution.definition is not None
                raise FlowStop(
                    UNSUPPORTED_EXPRESSION,
                    f"{resolution.reference!r} is the function "
                    f"{resolution.definition.name!r} "
                    f"({resolution.module.ref}:{resolution.definition.lineno}), not a list",
                )
            raise FlowStop(
                resolution.reason or UNSUPPORTED_EXPRESSION,
                resolution.detail or f"{resolution.reference!r} is not followed",
            )
        defining, value = resolution.module, resolution.value
        final = resolution.steps[-1]["name"] if resolution.steps else resolution.reference
        if state.guard_changes:
            self._unmutated(defining, str(final), defining.tree)
        state.read(defining)
        return self._value(defining, value, value, state)

    def _argument(
        self, module: PythonModule, parameter: ast.arg, state: _State
    ) -> tuple[PythonModule, ast.expr, ast.AST]:
        """The value a builder's one call site passes to ``parameter``."""

        function = self._owner(module, parameter)
        if function is None or not any(function is item for item in module.tree.body):
            raise FlowStop(
                LOCAL_BINDING,
                f"{parameter.arg!r} is a parameter at {module.ref}:{_line(parameter)} of a "
                "function not defined at module level, whose callers are not followed",
            )
        key = (id(function), parameter.arg)
        if key in state.active:
            raise FlowStop(
                FLOW_LIMIT,
                f"{parameter.arg!r} of {function.name!r} ({module.ref}:{function.lineno}) "
                "is passed back to itself",
            )
        if state.guard_changes:
            self._unmutated(module, parameter.arg, function)
        site = state.sites.get(id(function))
        if site is None:
            if len(state.sites) >= MAX_FLOW_DEPTH:
                raise FlowStop(
                    FLOW_LIMIT,
                    f"following {parameter.arg!r} to its callers passes through more than "
                    f"{MAX_FLOW_DEPTH} functions",
                )
            sites = self.call_sites(module, function)
            if not sites:
                raise FlowStop(
                    NO_CALL_SITE,
                    f"no call of {function.name!r} ({module.ref}:{function.lineno}) in the "
                    f"read scope passes {parameter.arg!r}",
                )
            if len(sites) > 1:
                shown = ", ".join(item.location for item in sites[:4])
                more = ", …" if len(sites) > 4 else ""
                raise FlowStop(
                    MULTIPLE_CALL_SITES,
                    f"{function.name!r} ({module.ref}:{function.lineno}) is called at "
                    f"{len(sites)} sites ({shown}{more}), whose values for "
                    f"{parameter.arg!r} are not merged",
                )
            site = sites[0]
            state.sites[id(function)] = site
            state.flow.via.append(site)
            state.read(site.module)
        supplied = _bind_argument(function, site.call, parameter)
        if supplied is _OPAQUE:
            raise FlowStop(
                OPAQUE_ARGUMENTS,
                f"the call of {function.name!r} at {site.location} passes arguments through "
                "* or **",
            )
        state.active.add(key)
        try:
            if supplied is None:
                # Not passed: the default, evaluated where the ``def`` stands.
                default = _default(function, parameter)
                if default is None:
                    raise FlowStop(
                        MISSING_ARGUMENT,
                        f"the call at {site.location} does not pass {parameter.arg!r} to "
                        f"{function.name!r}",
                    )
                return self._settle(module, default, function, state)
            assert isinstance(supplied, ast.expr)
            return self._settle(site.module, supplied, supplied, state)
        finally:
            state.active.discard(key)

    def _mapping(
        self, module: PythonModule, expr: ast.expr, context: ast.AST, state: _State
    ) -> tuple[PythonModule, ast.Dict, ast.AST]:
        """The dict literal ``expr`` evaluates to, every key a string literal."""

        found_module, value, found_context = self._settle(module, expr, context, state)
        if isinstance(value, ast.Call):
            found_module, value, found_context = self._returned(
                found_module, value, found_context, state
            )
            found_module, value, found_context = self._settle(
                found_module, value, found_context, state
            )
        if isinstance(value, ast.Dict) and all(
            isinstance(key, ast.Constant) and isinstance(key.value, str) for key in value.keys
        ):
            return found_module, value, found_context
        raise FlowStop(
            UNSUPPORTED_EXPRESSION,
            f"{_spelling(value)} at {found_module.ref}:{_line(value)} is not a dict literal "
            "with string keys",
        )

    def _returned(
        self, module: PythonModule, call: ast.Call, context: ast.AST, state: _State
    ) -> tuple[PythonModule, ast.expr, ast.AST]:
        """What a factory call returns: the value of its one final ``return``."""

        state.step()
        defining, function = self._callee(module, call, context)
        state.read(defining)
        # The factory's own parameters are this call's arguments.
        chosen = state.sites.get(id(function))
        if chosen is not None and chosen.call is not call:
            raise FlowStop(
                MULTIPLE_CALL_SITES,
                f"{function.name!r} ({defining.ref}:{function.lineno}) is read at two call "
                f"sites ({chosen.location}, {module.ref}:{call.lineno})",
            )
        state.sites[id(function)] = CallSite(module, call)
        returned = _single_return(function)
        if returned is None:
            raise FlowStop(
                UNSUPPORTED_EXPRESSION,
                f"{function.name!r} ({defining.ref}:{function.lineno}) does not end in its "
                "only return",
            )
        return defining, returned, returned

    def _returned_agent(
        self,
        module: PythonModule,
        call: ast.Call,
        context: ast.AST,
        rule: AgentRule,
        state: _State,
    ) -> str:
        defining, function = self._callee(module, call, context)
        returned = _single_return(function)
        if not isinstance(returned, ast.Call) or not rule.constructs(defining, returned):
            raise FlowStop(
                UNSUPPORTED_EXPRESSION,
                f"{function.name!r} ({defining.ref}:{function.lineno}) does not end by "
                "returning an agent it constructs",
            )
        name = rule.identity(defining, returned, None)
        if name is not None:
            return name
        # ``return Agent(name=name, ...)``: the name this call passes.
        keyword = next((item for item in returned.keywords if item.arg == "name"), None)
        if keyword is not None and isinstance(keyword.value, ast.Name):
            found = self.scopes(defining).enclosing_bindings(keyword.value, keyword.value.id)
            if len(found) == 1 and isinstance(found[0], ast.arg):
                if self._owner(defining, found[0]) is function:
                    supplied = _bind_argument(function, call, found[0])
                    if supplied is None:
                        supplied = _default(function, found[0])
                    if isinstance(supplied, ast.Constant) and isinstance(supplied.value, str):
                        return supplied.value
        raise FlowStop(
            UNSUPPORTED_EXPRESSION,
            f"the agent {function.name!r} returns ({defining.ref}:{returned.lineno}) is "
            f"not given a literal name by the call at {module.ref}:{call.lineno}",
        )

    def _callee(
        self, module: PythonModule, call: ast.Call, context: ast.AST
    ) -> tuple[PythonModule, Function]:
        resolution = self.resolve(Element(module, call.func, _context(call.func, call, context)))
        if not resolution.resolved:
            raise FlowStop(
                resolution.reason or UNSUPPORTED_EXPRESSION,
                resolution.detail
                or f"the function called at {module.ref}:{call.lineno} is not followed",
            )
        assert resolution.module is not None and resolution.definition is not None
        return resolution.module, resolution.definition

    def _owner(self, module: PythonModule, parameter: ast.AST) -> Function | None:
        index = self.scopes(module)
        arguments = index.parents.get(parameter)
        function = index.parents.get(arguments) if arguments is not None else None
        if not isinstance(function, _FUNCTIONS):
            return None
        if parameter is function.args.vararg or parameter is function.args.kwarg:
            return None
        return function

    def _scope_of(self, module: PythonModule, node: ast.AST) -> ast.AST:
        index = self.scopes(module)
        current = index.parents.get(node)
        while current is not None and not isinstance(current, (*_NESTED, ast.Module)):
            current = index.parents.get(current)
        return current if current is not None else module.tree

    def _unmutated(self, module: PythonModule, name: str, scope: ast.AST) -> None:
        """Stop unless every use of ``name`` in ``scope`` only reads it.

        The whitelist the SDK list reader applies (#879 review): iterated,
        indexed, compared, tested, formatted, handed to a read-only builtin or
        logging method, to an agent's own ``tools=``-like keyword, or to a
        function whose every use of that parameter is such a read. A ``global``
        or ``nonlocal`` declaration, ``+=``, a method call, a second name, a
        tuple, a return or ``*args`` can change it. Deliberately by name across
        the whole scope, nested functions included: a same-named variable
        elsewhere can only stop a flow, never widen one.
        """

        index = self.scopes(module)
        parents: dict[ast.AST, ast.AST] = (
            index.parents
            if scope is module.tree or scope in index.parents
            else {child: node for node in ast.walk(scope) for child in ast.iter_child_nodes(node)}
        )
        for node in ast.walk(scope):
            line: int | None = None
            if isinstance(node, ast.Global | ast.Nonlocal) and name in node.names:
                line = node.lineno
            elif isinstance(node, ast.Name) and node.id == name:
                if isinstance(node.ctx, ast.Load):
                    if not _read_only_use(
                        node,
                        parents,
                        lambda call, position, keyword: self._call_reads(
                            module, call, position, keyword
                        ),
                    ):
                        line = node.lineno
                elif isinstance(node.ctx, ast.Del) or isinstance(parents.get(node), ast.AugAssign):
                    line = node.lineno
            if line is not None:
                raise FlowStop(
                    MUTATED_VALUE,
                    f"{name!r} can be changed at {module.ref}:{line}, which is not followed",
                )

    def _call_reads(
        self, module: PythonModule, call: ast.Call, position: int | None, keyword: str | None
    ) -> bool:
        """Whether ``call`` only reads the value it is passed there."""

        if _leaves_arguments_alone(call):
            return True
        if (
            keyword in _CAPABILITY_KEYWORDS
            and self.agent_call is not None
            and self.agent_call(module, call)
        ):
            return True
        resolution = self.resolve(Element(module, call.func, call.func))
        if not resolution.resolved:
            return False
        assert resolution.module is not None and resolution.definition is not None
        function = resolution.definition
        positional = [*function.args.posonlyargs, *function.args.args]
        if position is not None:
            if position >= len(positional):
                return False
            parameter = positional[position].arg
        elif keyword in {arg.arg for arg in [*positional, *function.args.kwonlyargs]}:
            parameter = str(keyword)
        else:
            return False
        key = (id(function), parameter)
        if key in self._checking or len(self._checking) >= MAX_FLOW_DEPTH:
            # A callee passing it on again, or back to itself: not followed.
            return False
        self._checking.add(key)
        try:
            self._unmutated(resolution.module, parameter, function)
        except FlowStop:
            return False
        finally:
            self._checking.discard(key)
        return True

    # -- census -----------------------------------------------------------------

    def _take_census(self, module: PythonModule, function: Function) -> list[CallSite]:
        where = f"{function.name!r} ({module.ref}:{function.lineno})"
        if function.decorator_list:
            raise FlowStop(
                UNACCOUNTED_REFERENCE,
                f"{where} is decorated, so what calls it is not visible in source",
            )
        bindings = module.bindings.get(function.name, [])
        if len(bindings) != 1 or bindings[0].node is not function:
            raise FlowStop(
                UNACCOUNTED_REFERENCE, f"{function.name!r} is bound more than once in {module.ref}"
            )
        pattern = re.compile(rf"\b{re.escape(function.name)}\b")
        candidates = [path for path in self._files() if pattern.search(self._text(path))]
        if len(candidates) > MAX_CENSUS_CANDIDATES:
            raise FlowStop(
                FLOW_LIMIT,
                f"{len(candidates)} files in the read scope mention {function.name!r}; the "
                f"census reads at most {MAX_CENSUS_CANDIDATES}",
            )
        sites: list[CallSite] = []
        unaccounted: list[str] = []
        for path in candidates:
            try:
                caller = self.resolver.module(path)
            except _Stop as stop:
                raise FlowStop(stop.reason, stop.detail) from None
            self._census_module(caller, module, function, sites, unaccounted)
        if unaccounted:
            shown = "; ".join(unaccounted[:3])
            more = f"; and {len(unaccounted) - 3} more" if len(unaccounted) > 3 else ""
            raise FlowStop(
                UNACCOUNTED_REFERENCE,
                f"{where} is referenced other than by a direct call ({shown}{more}), so "
                "its callers are not all visible",
            )
        return sorted(
            sites, key=lambda site: (site.module.ref, site.call.lineno, site.call.col_offset)
        )

    def _census_module(
        self,
        caller: PythonModule,
        module: PythonModule,
        function: Function,
        sites: list[CallSite],
        unaccounted: list[str],
    ) -> None:
        index = self.scopes(caller)
        aliases = {
            node.asname
            for node in ast.walk(caller.tree)
            if isinstance(node, ast.alias)
            and node.asname
            and node.name.rsplit(".", 1)[-1] == function.name
        }
        exported = _exported_strings(caller.tree)
        for node in ast.walk(caller.tree):
            if isinstance(node, ast.Constant) and node.value == function.name:
                if id(node) not in exported:
                    unaccounted.append(f"named in a string at {caller.ref}:{_line(node)}")
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id != function.name and node.id not in aliases:
                    continue
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                if node.attr != function.name:
                    continue
            else:
                continue
            outcome = self._reference_to(caller, node, index, module, function)
            if outcome is False:
                continue
            where = f"{caller.ref}:{_line(node)}"
            if isinstance(outcome, str):
                unaccounted.append(f"{outcome} at {where}")
                continue
            parent = index.parents.get(node)
            if isinstance(parent, ast.Call) and parent.func is node:
                sites.append(CallSite(caller, parent))
            else:
                unaccounted.append(f"used as a value at {where}")

    def _reference_to(
        self,
        caller: PythonModule,
        node: ast.expr,
        index: ScopeIndex,
        module: PythonModule,
        function: Function,
    ) -> bool | str:
        """True: this function. False: provably something else. A string: why it is unknown."""

        spelling = reference_spelling(node)
        if spelling is None:
            return "reached through a computed value"
        head = spelling.split(".", 1)[0]
        found = index.enclosing_bindings(node, head)
        if len(found) > 1 and any(isinstance(item, ast.alias) for item in found):
            return f"{head!r} is imported and rebound in the enclosing scope"
        resolution = self.resolve(Element(caller, node, node))
        if resolution.resolved:
            assert resolution.module is not None
            return resolution.module.path == module.path and resolution.definition is function
        if resolution.reason in _ELSEWHERE:
            return False
        if resolution.reason == MODULE_NOT_FOUND and not self._could_be_in_scope(caller, head):
            # ``from langchain.agents import create_agent``: a package nothing
            # in the read scope is named after cannot be this function.
            return False
        return f"a reference whose binding is not followed ({resolution.detail})"

    def _could_be_in_scope(self, caller: PythonModule, head: str) -> bool:
        bindings = caller.bindings.get(head, [])
        for binding in bindings:
            statement = binding.statement
            if isinstance(statement, ast.ImportFrom):
                if statement.level:
                    return True
                top = (statement.module or "").split(".", 1)[0]
            elif isinstance(statement, ast.Import) and isinstance(binding.node, ast.alias):
                top = binding.node.name.split(".", 1)[0]
            else:
                return True
            if top in self._names_in_scope:
                return True
        # A local import, or no binding the module records: be conservative.
        return not bindings

    def _files(self) -> list[Path]:
        if isinstance(self._inventory, FlowStop):
            raise self._inventory
        if self._inventory is None:
            try:
                self._inventory = self._list_files()
            except FlowStop as stop:
                self._inventory = stop
                raise
        return self._inventory

    def _list_files(self) -> list[Path]:
        root = self.resolver.scope_root
        pending = [root]
        files: list[Path] = []
        while pending:
            directory = pending.pop()
            try:
                children = list_input_directory(directory)
            except InputParseError as exc:
                raise FlowStop(FLOW_LIMIT, f"the read scope could not be listed: {exc}") from None
            for child in children:
                relative = child.relative_to(root).as_posix()
                try:
                    metadata = child.lstat()
                except OSError:
                    raise FlowStop(FLOW_LIMIT, f"{relative} could not be inspected") from None
                skipped = child.name.startswith(".") or child.name in _SKIPPED_DIRECTORIES
                if stat.S_ISLNK(metadata.st_mode):
                    if (child.name.endswith(".py") and not is_test_path(relative)) or (
                        not skipped and not is_test_path(f"{relative}/_.py") and _is_directory(child)
                    ):
                        raise FlowStop(
                            UNACCOUNTED_REFERENCE,
                            f"{relative} is a link, so a call behind it is not visible",
                        )
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if skipped or is_test_path(f"{relative}/_.py"):
                        continue
                    if (child / "pyvenv.cfg").exists():
                        # A virtual environment: installed packages.
                        continue
                    self._names_in_scope.add(child.name)
                    pending.append(child)
                    continue
                if not child.name.endswith(".py") or is_test_path(relative):
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise FlowStop(
                        UNACCOUNTED_REFERENCE,
                        f"{relative} is not a regular file, so a call in it is not visible",
                    )
                self._names_in_scope.add(child.name[: -len(".py")])
                files.append(child)
                if len(files) > MAX_CENSUS_FILES:
                    raise FlowStop(
                        FLOW_LIMIT,
                        f"the read scope holds more than {MAX_CENSUS_FILES} Python files",
                    )
        if root.name:
            self._names_in_scope.add(root.name)
        return sorted(files)

    def _text(self, path: Path) -> str:
        if path not in self._texts:
            try:
                self._texts[path] = load_text_file(path)
            except InputParseError:
                self._texts[path] = None
        text = self._texts[path]
        if text is None:
            raise FlowStop(
                FLOW_LIMIT,
                f"{self.resolver.ref(path)} could not be read, so a call in it is not visible",
            )
        return text


# -- helpers ---------------------------------------------------------------------

_OPAQUE = object()


def _bind_argument(function: Function, call: ast.Call, parameter: ast.arg) -> object:
    """The call's expression for ``parameter``: None when absent, ``_OPAQUE`` when hidden."""

    if any(isinstance(item, ast.Starred) for item in call.args) or any(
        keyword.arg is None for keyword in call.keywords
    ):
        return _OPAQUE
    arguments = function.args
    if parameter not in arguments.posonlyargs:
        for keyword in call.keywords:
            if keyword.arg == parameter.arg:
                return keyword.value
    positional = [*arguments.posonlyargs, *arguments.args]
    for position, item in enumerate(positional):
        if item is parameter and position < len(call.args):
            return call.args[position]
    return None


def _default(function: Function, parameter: ast.arg) -> ast.expr | None:
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if parameter in positional:
        offset = len(positional) - len(arguments.defaults)
        position = positional.index(parameter)
        return arguments.defaults[position - offset] if position >= offset else None
    if parameter in arguments.kwonlyargs:
        return arguments.kw_defaults[arguments.kwonlyargs.index(parameter)]
    return None


def _single_return(function: Function) -> ast.expr | None:
    """The value of a function's only ``return``, when it is the last statement."""

    returns: list[ast.Return] = []
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED):
            continue
        if isinstance(node, ast.Yield | ast.YieldFrom):
            return None
        if isinstance(node, ast.Return):
            returns.append(node)
        stack.extend(ast.iter_child_nodes(node))
    if len(returns) != 1 or function.body[-1] is not returns[0]:
        return None
    return returns[0].value


def _single_target(statement: ast.stmt) -> ast.expr | None:
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        return statement.targets[0]
    if isinstance(statement, ast.AnnAssign) and statement.value is not None:
        return statement.target
    return None


def _exported_strings(tree: ast.Module) -> set[int]:
    """The string constants ``__all__`` lists: exports, not references."""

    found: set[int] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets, value = [node.target], node.value
        if isinstance(value, ast.List | ast.Tuple) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in targets
        ):
            found.update(id(item) for item in value.elts if isinstance(item, ast.Constant))
    return found


def _context(child: ast.AST, parent: ast.AST, context: ast.AST) -> ast.AST:
    """A sub-expression is looked up where it stands, unless its parent is not."""

    return child if context is parent else context


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute | ast.Subscript | ast.Call):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _spelling(node: ast.AST) -> str:
    try:
        text = ast.unparse(node)
    except (ValueError, AttributeError, RecursionError):
        return type(node).__name__
    return repr(text if len(text) <= 60 else text[:57] + "...")


def _line(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 0) or 0)


__all__ = [
    "AgentRule",
    "CallSite",
    "Element",
    "FLOW_LIMIT",
    "Flow",
    "FlowStop",
    "MAX_FLOW_DEPTH",
    "MISSING_ARGUMENT",
    "MISSING_KEY",
    "MULTIPLE_CALL_SITES",
    "MUTATED_VALUE",
    "NO_CALL_SITE",
    "OPAQUE_ARGUMENTS",
    "ParameterFlow",
    "UNACCOUNTED_REFERENCE",
    "UNSUPPORTED_EXPRESSION",
]
