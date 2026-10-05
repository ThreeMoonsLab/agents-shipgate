"""Bounded, source-only callers of agent builders (#874).

This indexes callers and binds Python arguments. Membership stays with
``ListExpressions``: one invocation supplies all of a construction's fields,
so tools from one call can never be paired with handoffs from another.
"""

from __future__ import annotations

import ast
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.static_inputs import active_static_input_snapshot
from agents_shipgate.inputs.common import list_input_directory, load_text_file
from agents_shipgate.inputs.list_expressions import evaluation_site
from agents_shipgate.inputs.python_imports import (
    LOCAL_BINDING,
    MODULE_NOT_FOUND,
    NAME_NOT_DEFINED,
    NOT_A_FUNCTION,
    NOT_BOUND,
    ImportResolver,
    PythonModule,
    Resolution,
    ScopeIndex,
    _Stop,
    reference_spelling,
    reflective_access,
)

Function = ast.FunctionDef | ast.AsyncFunctionDef
MAX_FILES = 2000
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_CANDIDATES = 32
MAX_ALIAS_ROUNDS = 4
MAX_DEPTH = 4
MAX_CONTEXTS = 128
_SKIP = frozenset({"__pycache__", "node_modules", "site-packages"})
_ELSEWHERE = frozenset({NOT_BOUND, LOCAL_BINDING, NAME_NOT_DEFINED, NOT_A_FUNCTION})


class CallLimit(Exception):
    """Why an invocation or its complete caller census is not established."""


@dataclass(frozen=True)
class CallSite:
    module: PythonModule
    call: ast.Call

    @property
    def location(self) -> str:
        return f"{self.module.ref}:{self.call.lineno}"


@dataclass(frozen=True)
class Argument:
    parameter: ast.arg
    expr: ast.expr
    default: bool = False


def bind_call(function: Function, call: ast.Call) -> tuple[Argument, ...]:
    """Bind the entire call, refusing every shape this reader cannot prove.

    Non-capability arguments may be dynamic expressions; their values are not
    evaluated. Invalid or opaque arguments elsewhere still invalidate a call.
    """

    args = function.args
    if args.vararg is not None or args.kwarg is not None:
        raise CallLimit("a variadic builder signature is not followed")
    if any(isinstance(value, ast.Starred) for value in call.args) or any(
        value.arg is None for value in call.keywords
    ):
        raise CallLimit("arguments passed through * or ** are not followed")
    positional = [*args.posonlyargs, *args.args]
    parameters = [*positional, *args.kwonlyargs]
    if len(call.args) > len(positional):
        raise CallLimit("the call supplies too many positional arguments")
    supplied = dict(zip((arg.arg for arg in positional), call.args, strict=False))
    named = {arg.arg for arg in [*args.args, *args.kwonlyargs]}
    for keyword in call.keywords:
        if keyword.arg not in named:
            raise CallLimit(f"keyword {keyword.arg!r} is unknown or positional-only")
        if keyword.arg in supplied:
            raise CallLimit(f"argument {keyword.arg!r} is supplied more than once")
        supplied[keyword.arg] = keyword.value
    defaults = {
        parameter.arg: value
        for parameter, value in zip(
            positional[len(positional) - len(args.defaults) :], args.defaults, strict=True
        )
    }
    defaults.update(
        {
            parameter.arg: value
            for parameter, value in zip(args.kwonlyargs, args.kw_defaults, strict=True)
            if value is not None
        }
    )
    result = []
    for parameter in parameters:
        if parameter.arg in supplied:
            result.append(Argument(parameter, supplied[parameter.arg]))
        elif parameter.arg in defaults:
            result.append(Argument(parameter, defaults[parameter.arg], default=True))
        else:
            raise CallLimit(f"required argument {parameter.arg!r} is missing")
    return tuple(result)


@dataclass(frozen=True)
class Invocation:
    module: PythonModule
    function: Function
    site: CallSite
    arguments: tuple[Argument, ...]
    parent: Invocation | None = None
    conditions: tuple[str, ...] = ()

    @property
    def key(self) -> tuple:
        return (id(self.function), id(self.site.call), self.parent.key if self.parent else ())

    @property
    def locations(self) -> tuple[str, ...]:
        return (self.site.location, *(self.parent.locations if self.parent else ()))

    def argument(
        self, parameter: ast.arg
    ) -> tuple[PythonModule, ast.expr, Invocation | None] | None:
        for value in self.arguments:
            if value.parameter is parameter:
                return (
                    (self.module, value.expr, None)
                    if value.default
                    else (self.site.module, value.expr, self.parent)
                )
        return self.parent.argument(parameter) if self.parent else None


@dataclass(frozen=True)
class CallerCensus:
    sites: tuple[CallSite, ...] = ()
    limits: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConstructionContext:
    invocation: Invocation | None = None
    limits: tuple[str, ...] = ()


class BuilderCalls:
    """One caller index per resolver/read, including negative read evidence."""

    def __init__(self, resolver: ImportResolver) -> None:
        self.resolver = resolver
        self._files: list[Path] | None = None
        self._texts: dict[Path, str] = {}
        self._read_bytes = 0
        self._scopes: dict[int, ScopeIndex] = {}
        self._censuses: dict[int, CallerCensus] = {}
        self._exports: dict[int, bool] = {}
        self._borrowers: dict[tuple[int, str], tuple[PythonModule, ...]] = {}
        self._borrower_aliases: dict[tuple[int, str], tuple[str, ...]] = {}
        self._borrower_words: dict[tuple[int, str], frozenset[str]] = {}
        self._borrower_paths: dict[tuple[int, str], frozenset[Path]] = {}
        self._dynamic_imports: dict[int, ast.AST | None] = {}

    def scopes(self, module: PythonModule) -> ScopeIndex:
        found = self._scopes.get(id(module.tree))
        if found is None:
            found = self._scopes[id(module.tree)] = ScopeIndex(module.tree)
        return found

    def function_at(self, module: PythonModule, node: ast.AST) -> Function | None:
        scopes = self.scopes(module)
        current = scopes.parents.get(evaluation_site(scopes, node))
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current
            if isinstance(current, ast.ClassDef | ast.Lambda):
                return None
            current = scopes.parents.get(current)
        return None

    def resolve(self, module: PythonModule, expr: ast.expr) -> Resolution:
        spelling = reference_spelling(expr)
        if spelling is None:
            return Resolution(
                reference=ast.unparse(expr),
                reason=LOCAL_BINDING,
                detail="a computed callable is not followed",
            )
        return self._resolve_reference(module, spelling, expr)

    def _resolve_reference(self, module: PythonModule, spelling: str, site: ast.AST) -> Resolution:
        scopes = self.scopes(module)
        found = scopes.enclosing_bindings(evaluation_site(scopes, site), spelling.split(".", 1)[0])
        if found:
            statement = scopes.statement_of(found[0])
            if (
                len(found) == 1
                and isinstance(found[0], ast.alias)
                and isinstance(statement, ast.Import | ast.ImportFrom)
            ):
                return self.resolver.resolve_local_import(module, statement, found[0], spelling)
            if (
                len(found) == 1
                and isinstance(found[0], ast.FunctionDef | ast.AsyncFunctionDef)
                and "." not in spelling
                and isinstance(
                    scopes.parents.get(found[0]),
                    ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
                )
            ):
                return Resolution(reference=spelling, module=module, definition=found[0])
            return Resolution(
                reference=spelling,
                reason=LOCAL_BINDING,
                detail="the callable's enclosing binding is not established",
            )
        return self.resolver.resolve(module, spelling)

    def invoke(
        self,
        module: PythonModule,
        function: Function,
        site: CallSite,
        parent: Invocation | None = None,
    ) -> Invocation:
        if function.decorator_list:
            raise CallLimit(f"{function.name!r} is decorated, so its body is not the callable")
        if isinstance(function, ast.AsyncFunctionDef) or _has_yield(function):
            raise CallLimit("a coroutine or generator builder is not followed")
        scopes = self.scopes(site.module)
        current = scopes.parents.get(site.call)
        child: ast.AST = site.call
        conditions: list[str] = []
        while current is not None:
            if isinstance(current, ast.ClassDef | ast.Lambda):
                raise CallLimit("a class-body or lambda caller is not followed")
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
                break
            if isinstance(current, ast.If):
                positive = child in current.body
                if isinstance(current.test, ast.Constant):
                    if bool(current.test.value) != positive:
                        raise CallLimit("the caller lies in a statically unreachable branch")
                else:
                    conditions.append(
                        f"the caller's {'condition' if positive else 'negated condition'} `{ast.unparse(current.test)}` holds"
                    )
            elif isinstance(
                current,
                ast.For
                | ast.AsyncFor
                | ast.While
                | ast.Try
                | ast.TryStar
                | ast.Match
                | ast.With
                | ast.AsyncWith,
            ):
                conditions.append(f"the caller reaches its {type(current).__name__.lower()} branch")
            child = current
            current = scopes.parents.get(current)
        if parent is not None and len(parent.locations) >= MAX_DEPTH:
            raise CallLimit(f"argument flow exceeds {MAX_DEPTH} caller levels")
        return Invocation(
            module,
            function,
            site,
            bind_call(function, site.call),
            parent,
            tuple(dict.fromkeys((*conditions, *(parent.conditions if parent else ())))),
        )

    def contexts(
        self, module: PythonModule, construction: ast.Call
    ) -> tuple[ConstructionContext, ...]:
        fields = [
            keyword.value
            for keyword in construction.keywords
            if keyword.arg in {"tools", "handoffs", "sub_agents", "mcp_servers"}
        ]
        try:
            parameters = self._parameters(module, fields)
        except CallLimit as exc:
            return (ConstructionContext(limits=(str(exc),)),)
        if not parameters:
            return (ConstructionContext(),)
        function = self.function_at(module, construction)
        if function is None or function not in module.tree.body:
            return (
                ConstructionContext(
                    limits=("a nested or method builder's callers are not followed",)
                ),
            )
        current = self.scopes(module).parents.get(construction)
        while current is not None and current is not function:
            if isinstance(
                current,
                ast.If
                | ast.For
                | ast.AsyncFor
                | ast.While
                | ast.Try
                | ast.TryStar
                | ast.Match
                | ast.With
                | ast.AsyncWith,
            ):
                return (
                    ConstructionContext(
                        limits=(
                            "a conditional, loop or handler construction is not followed through caller arguments",
                        )
                    ),
                )
            current = self.scopes(module).parents.get(current)
        owned = {
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
        }
        if not parameters <= owned:
            return (
                ConstructionContext(
                    limits=("capability parameters belong to another enclosing function",)
                ),
            )
        return self._expand(module, function, parameters, frozenset())

    def dynamic_importer(self, module: PythonModule) -> ast.AST | None:
        """Include aliases and retained import machinery in the unread route."""
        key = id(module.tree)
        if key in self._dynamic_imports:
            return self._dynamic_imports[key]
        found = None
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Import) and any(
                alias.name.split(".", 1)[0] == "importlib" for alias in node.names
            ):
                found = node
                break
            if isinstance(node, ast.ImportFrom) and (
                (node.module or "").split(".", 1)[0] == "importlib"
                or (
                    node.module == "builtins"
                    and any(alias.name == "__import__" for alias in node.names)
                )
            ):
                found = node
                break
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in {"__import__", "import_module"}
            ):
                found = node
                break
        self._dynamic_imports[key] = found
        return found

    def _expand(
        self, module: PythonModule, function: Function, parameters: set[str], seen: frozenset[int]
    ) -> tuple[ConstructionContext, ...]:
        if id(function) in seen or len(seen) >= MAX_DEPTH:
            return (
                ConstructionContext(
                    limits=(f"recursive argument flow or more than {MAX_DEPTH} caller levels",)
                ),
            )
        census = self.callers(module, function)
        contexts = [ConstructionContext(limits=census.limits)] if census.limits else []
        for site in census.sites:
            try:
                arguments = bind_call(function, site.call)
                needed = [
                    value.expr
                    for value in arguments
                    if value.parameter.arg in parameters and not value.default
                ]
                parent_parameters = self._parameters(site.module, needed)
                parent_function = self.function_at(site.module, site.call)
                if parent_parameters:
                    if parent_function is None or parent_function not in site.module.tree.body:
                        raise CallLimit(
                            "capability arguments come from a nested or method caller, whose callers are not followed"
                        )
                    parents = self._expand(
                        site.module, parent_function, parent_parameters, seen | {id(function)}
                    )
                else:
                    parents = (ConstructionContext(),)
                for parent in parents:
                    if parent.limits:
                        contexts.append(ConstructionContext(limits=parent.limits))
                    else:
                        contexts.append(
                            ConstructionContext(
                                self.invoke(module, function, site, parent.invocation)
                            )
                        )
            except CallLimit as exc:
                contexts.append(ConstructionContext(limits=(f"{site.location}: {exc}",)))
            if len(contexts) > MAX_CONTEXTS:
                return (
                    ConstructionContext(
                        limits=(f"argument flow exceeds {MAX_CONTEXTS} caller contexts",)
                    ),
                )
        return tuple(contexts)

    def _parameters(self, module: PythonModule, expressions: list[ast.expr]) -> set[str]:
        """List-value dependencies only; a factory's ordinary inputs are lazy.

        A call is projected by the membership resolver. Looking into all its
        arguments here would demand callers for e.g. a database handle that
        only the nested tool body uses, although the returned list is literal.
        Unread deeper forwarding remains a named membership limit.
        """
        scopes = self.scopes(module)
        found: set[str] = set()
        seen: set[int] = set()
        pending: list[ast.AST] = list(expressions)
        while pending:
            node = pending.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            if len(seen) > 1000:
                raise CallLimit("capability dependency flow exceeds 1000 expressions")
            if isinstance(node, ast.Name):
                bindings = scopes.enclosing_bindings(evaluation_site(scopes, node), node.id)
                if len(bindings) == 1:
                    binding = bindings[0]
                    if isinstance(binding, ast.arg):
                        found.add(binding.arg)
                    else:
                        statement = scopes.statement_of(binding)
                        if (
                            isinstance(statement, ast.Assign | ast.AnnAssign)
                            and statement.value is not None
                        ):
                            pending.append(statement.value)
            elif isinstance(node, ast.Lambda):
                continue
            elif isinstance(node, ast.IfExp):
                pending.extend((node.body, node.orelse))
            elif isinstance(node, ast.ListComp | ast.GeneratorExp):
                pending.extend(generator.iter for generator in node.generators)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                    pending.append(node.func.value)
                elif isinstance(node.func, ast.Name) and node.func.id in {
                    "list",
                    "tuple",
                    "sorted",
                    "filter",
                }:
                    pending.extend(node.args[1:2] if node.func.id == "filter" else node.args[:1])
            else:
                pending.extend(ast.iter_child_nodes(node))
        return found

    def callers(self, module: PythonModule, function: Function) -> CallerCensus:
        cached = self._censuses.get(id(function))
        if cached is not None:
            return cached
        try:
            result = self._census(module, function)
        except CallLimit as exc:
            result = CallerCensus(limits=(str(exc),))
        self._censuses[id(function)] = result
        return result

    def exported_elsewhere(self, module: PythonModule, call: ast.Call) -> bool:
        """A module-level result imported elsewhere is an unread retained handle.

        This first increment does not inspect another module's uses of the
        handle. Even an unused import therefore remains a conservative limit.
        """
        cached = self._exports.get(id(call))
        if cached is not None:
            return cached
        names = {
            name
            for name, bindings in module.bindings.items()
            if any(getattr(binding.statement, "value", None) is call for binding in bindings)
        }
        exported = False
        if names:
            try:
                for path in self._candidates(_module_words(module.path), module.path):
                    caller = self._module(path)
                    if caller.path == module.path:
                        continue
                    for statement in ast.walk(caller.tree):
                        if not isinstance(statement, ast.Import | ast.ImportFrom):
                            continue
                        for alias in statement.names:
                            if self._retained_namespace(caller, statement, alias, {module.path}):
                                exported = True
                            references = (
                                [alias.asname or alias.name]
                                if isinstance(statement, ast.ImportFrom)
                                else [f"{alias.asname or alias.name}.{name}" for name in names]
                            )
                            if alias.name == "*":
                                exported = True
                            for reference in references:
                                result = self.resolver.resolve_local_import(
                                    caller, statement, alias, reference
                                )
                                if (
                                    result.module is not None
                                    and result.module.path == module.path
                                    and result.value is call
                                ):
                                    exported = True
            except CallLimit:
                exported = True
        self._exports[id(call)] = exported
        return exported

    def borrowers(self, module: PythonModule, name: str) -> tuple[PythonModule, ...]:
        """Candidate importers of a shared module value, including re-exports.

        Membership still belongs to ListExpressions. It checks each candidate
        with the existing import-identity and mutation tests. Failure to finish
        this bounded census never establishes a borrowed list's contents.
        """
        key = (id(module.tree), name)
        if key in self._borrowers:
            return self._borrowers[key]
        names, words = {name}, _module_words(module.path)
        related = {module.path}
        for _ in range(MAX_ALIAS_ROUNDS):
            previous_related = set(related)
            paths = self._candidates(names | words, module.path)
            added, expanded = set(names), set(words)
            for path in paths:
                if path == module.path:
                    continue  # Its own aliases/mutations are indexed by ListExpressions.
                caller = self._module(path)
                aliases, possible = self._expanding_imports(
                    caller, names, words, related, subject=module.path
                )
                if possible:
                    expanded.update(_module_words(path))
                    related.add(path)
                added.update(aliases)
            if names == added and words == expanded and related == previous_related:
                modules = tuple(self._module(path) for path in paths)
                self._borrowers[key] = modules
                self._borrower_aliases[key] = tuple(sorted(names))
                self._borrower_words[key] = frozenset(words)
                self._borrower_paths[key] = frozenset(related)
                return modules
            names, words = added, expanded
        raise CallLimit(
            f"shared-list imports pass through more than {MAX_ALIAS_ROUNDS} alias rounds"
        )

    def borrower_spellings(
        self, module: PythonModule, name: str
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        self.borrowers(module, name)
        key = (id(module.tree), name)
        return self._borrower_aliases[key], self._borrower_words[key]

    def import_may_share(
        self,
        caller: PythonModule,
        defining: PythonModule,
        name: str,
        statement: ast.Import | ast.ImportFrom,
        alias: ast.alias,
    ) -> bool:
        """Whether one import may retain the subject list or its namespace."""
        names, words = self.borrower_spellings(defining, name)
        key = (id(defining.tree), name)
        _, possible = self._expanding_imports(
            caller,
            set(names),
            set(words),
            set(self._borrower_paths[key]),
            (statement,),
            subject=defining.path,
            selected_alias=alias,
        )
        return possible

    def _census(self, module: PythonModule, function: Function) -> CallerCensus:
        where = f"{function.name!r} ({module.ref}:{function.lineno})"
        binding = module.bindings.get(function.name, [])
        if function.decorator_list or len(binding) != 1 or binding[0].node is not function:
            raise CallLimit(
                f"{where} is decorated, nested, or rebound; its callers are not established"
            )
        names = {function.name}
        modules = _module_words(module.path)
        related = {module.path}
        for _ in range(MAX_ALIAS_ROUNDS):
            previous_related = set(related)
            added = set(names)
            expanded = set(modules)
            for path in self._candidates(names | modules, module.path):
                caller = self._module(path)
                aliases, possible = self._expanding_imports(
                    caller, names, modules, related, subject=module.path
                )
                if possible:
                    expanded.update(_module_words(path))
                    related.add(path)
                added.update(aliases)
            if added == names and expanded == modules and related == previous_related:
                break
            names = added
            modules = expanded
        else:
            raise CallLimit(f"{where} passes through more than {MAX_ALIAS_ROUNDS} alias rounds")
        sites: list[CallSite] = []
        limits: list[str] = []
        for path in self._candidates(names | modules, module.path):
            caller = self._module(path)
            scopes = self.scopes(caller)
            exports = _export_strings(caller.tree)
            dynamic = self.dynamic_importer(caller)
            if dynamic is not None:
                limits.append(
                    f"{where} may be reached through dynamic import machinery at {caller.ref}:{dynamic.lineno}"
                )
            reflection = reflective_access(caller.tree)
            if caller.path in related and reflection is not None:
                limits.append(
                    f"{where} may be reached through reflection at {caller.ref}:{reflection.lineno}"
                )
            for node in ast.walk(caller.tree):
                if isinstance(node, ast.Call) and (reference_spelling(node.func) or "").rsplit(
                    ".", 1
                )[-1] in {"__import__", "import_module"}:
                    limits.append(
                        f"{where} may be reached through a dynamic import at {caller.ref}:{node.lineno}"
                    )
                if isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load):
                    spelling = reference_spelling(node)
                    if spelling is None:
                        continue
                    head = spelling.split(".", 1)[0]
                    bindings = scopes.enclosing_bindings(evaluation_site(scopes, node), head)
                    imported = any(isinstance(binding, ast.alias) for binding in bindings) or any(
                        isinstance(item.node, ast.alias) for item in caller.bindings.get(head, [])
                    )
                    if imported and any(
                        (
                            result := self._resolve_reference(caller, f"{spelling}.{name}", node)
                        ).resolved
                        and result.module.path == module.path
                        and result.definition is function
                        for name in names
                    ):
                        parent = scopes.parents.get(node)
                        if not (isinstance(parent, ast.Attribute) and parent.attr in names):
                            limits.append(
                                f"{where}'s module is used through computed access or as a value at {caller.ref}:{node.lineno}"
                            )
                    resolved_node = self.resolve(caller, node) if imported else None
                    if imported and resolved_node is not None and not resolved_node.resolved:
                        parent = scopes.parents.get(node)
                        if not (isinstance(parent, ast.Attribute) and parent.value is node):
                            statement = (
                                scopes.statement_of(bindings[0]) if len(bindings) == 1 else None
                            )
                            if statement is None:
                                own = caller.bindings.get(head, [])
                                statement = own[0].statement if len(own) == 1 else None
                            if isinstance(statement, ast.Import | ast.ImportFrom):
                                for alias in statement.names:
                                    if (alias.asname or alias.name.split(".", 1)[0]) != head:
                                        continue
                                    if self._retained_namespace(caller, statement, alias, related):
                                        limits.append(
                                            f"{where}'s retained namespace is used through computed access or as a value at {caller.ref}:{node.lineno}"
                                        )
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in names
                ):
                    if id(node) not in exports:
                        limits.append(f"{where} is named in a string at {caller.ref}:{node.lineno}")
                    continue
                if not (
                    (isinstance(node, ast.Name) and node.id in names)
                    or (isinstance(node, ast.Attribute) and node.attr in names)
                ) or not isinstance(node.ctx, ast.Load):
                    continue
                resolution = self.resolve(caller, node)
                if resolution.resolved and not resolution.caveats:
                    if (
                        resolution.module.path != module.path
                        or resolution.definition is not function
                    ):
                        continue
                    parent = scopes.parents.get(node)
                    if isinstance(parent, ast.Call) and parent.func is node:
                        sites.append(CallSite(caller, parent))
                    else:
                        limits.append(f"{where} is used as a value at {caller.ref}:{node.lineno}")
                elif not self._elsewhere(caller, node, resolution):
                    limits.append(
                        f"{where} has an unresolved reference at {caller.ref}:{node.lineno}: {resolution.detail}"
                    )
        derived = self.resolver.derived_scope()
        if derived is not None:
            # The change chose this scope (#875); the repository around it is
            # still the application. A caller there was not materialized, let
            # alone read, so the callers read here cannot be all of them.
            limits.append(
                f"{where} is read in `{derived}`, a scope derived from the change; its callers "
                "elsewhere in the repository are not read (select the application root with --scope)"
            )
        if not sites:
            limits.append(f"no direct caller of {where} is established in the read scope")
        if len(sites) > MAX_CONTEXTS:
            raise CallLimit(f"{where} has more than {MAX_CONTEXTS} caller contexts")
        return CallerCensus(
            tuple(
                sorted(
                    sites,
                    key=lambda site: (site.module.ref, site.call.lineno, site.call.col_offset),
                )
            ),
            tuple(dict.fromkeys(limits)),
        )

    def _expanding_imports(
        self,
        caller: PythonModule,
        names: set[str],
        words: set[str],
        related: set[Path],
        statements: tuple[ast.Import | ast.ImportFrom, ...] | None = None,
        *,
        subject: Path,
        selected_alias: ast.alias | None = None,
    ) -> tuple[set[str], bool]:
        """Expand aliases through related imports, not matching module basenames.

        Textual candidates are still inspected. Only their import identity can
        widen the next round. An unresolved local import stays a candidate;
        an established import into a different module cannot re-export this
        subject merely because its schema module has the same filename.
        """
        aliases: set[str] = set()
        possible = False
        for statement in statements if statements is not None else ast.walk(caller.tree):
            if not isinstance(statement, ast.Import | ast.ImportFrom):
                continue
            _, textual = _candidate_imports(statement, names, words)
            ancestor = any(
                self._retained_namespace(caller, statement, alias, {subject})
                for alias in statement.names
            )
            if not textual and not ancestor:
                continue
            for alias in statement.names:
                if selected_alias is not None and alias is not selected_alias:
                    continue
                relevant = False
                try:
                    if isinstance(statement, ast.ImportFrom):
                        container = self.resolver._from_base(caller, statement)
                        if alias.name != "*":
                            child = self.resolver._locate(
                                container.directory, alias.name.split("."), spelling=alias.name
                            )
                            relevant = child is not None and child.module_path in related
                    else:
                        container = self.resolver._absolute(caller, alias.name)
                        relevant = container.module_path in related
                except _Stop as exc:
                    relevant = not (
                        exc.reason == MODULE_NOT_FOUND and self.another_library(statement, alias)
                    )
                relevant |= self._retained_namespace(caller, statement, alias, related)
                local = alias.asname or alias.name
                prefixes = {local}
                prefixes.update(
                    spelling
                    for node in ast.walk(caller.tree)
                    if isinstance(node, ast.Attribute)
                    and (spelling := reference_spelling(node)) is not None
                    and spelling.startswith(f"{local}.")
                )
                for spelling in {
                    local,
                    *(f"{prefix}.{name}" for prefix in prefixes for name in names),
                }:
                    resolution = self.resolver.resolve_local_import(
                        caller, statement, alias, spelling
                    )
                    if (
                        resolution.module is not None
                        and resolution.module.path in related
                        and (
                            resolution.value is not None
                            or (
                                resolution.definition is not None
                                and resolution.module.path == subject
                                and resolution.definition.name in names
                            )
                        )
                    ):
                        relevant = True
                    elif resolution.reason is not None and resolution.reason not in _ELSEWHERE:
                        if resolution.reason != MODULE_NOT_FOUND or not self.another_library(
                            statement, alias
                        ):
                            relevant = True
                if relevant:
                    possible = True
                    if alias.name.rsplit(".", 1)[-1] in names and alias.asname:
                        aliases.add(alias.asname)
        return aliases, possible

    def _retained_namespace(
        self,
        caller: PythonModule,
        statement: ast.Import | ast.ImportFrom,
        alias: ast.alias,
        related: set[Path],
    ) -> bool:
        """An imported namespace can retain loaded children of related modules."""
        try:
            if isinstance(statement, ast.Import):
                dotted = alias.name if alias.asname else alias.name.split(".", 1)[0]
                container = self.resolver._absolute(caller, dotted)
            else:
                base = self.resolver._from_base(caller, statement)
                if alias.name == "*":
                    return base.module_path in related
                container = self.resolver._locate(
                    base.directory, alias.name.split("."), spelling=alias.name
                )
                if container is None:
                    return False
            return container.module_path in related or (
                (container.package or container.module_path is None)
                and any(path.is_relative_to(container.directory) for path in related)
            )
        except _Stop as exc:
            # Candidate discovery must not discard an ambiguous/unread parent
            # import before the ordinary unresolved-import guard can see it.
            imported = (
                alias.name
                if isinstance(statement, ast.Import)
                else f"{statement.module or ''}.{alias.name}"
            )
            words = {
                part
                for path in related
                for part in path.relative_to(self.resolver.scope_root).with_suffix("").parts
            }
            return exc.reason != MODULE_NOT_FOUND and bool(words & set(imported.split(".")))

    def _elsewhere(self, module: PythonModule, expr: ast.expr, resolution: Resolution) -> bool:
        spelling = reference_spelling(expr) or ""
        if resolution.reason == LOCAL_BINDING:
            found = self.scopes(module).enclosing_bindings(
                evaluation_site(self.scopes(module), expr), spelling.split(".", 1)[0]
            )
            if any(isinstance(binding, ast.alias) for binding in found):
                # A conditional import/rebinding may still be this builder.
                return False
        if resolution.reason in _ELSEWHERE:
            return "." not in spelling or resolution.reason not in {LOCAL_BINDING, NOT_A_FUNCTION}
        if resolution.reason != MODULE_NOT_FOUND:
            return False
        head = spelling.split(".", 1)[0]
        bindings = module.bindings.get(head, [])
        return bool(bindings) and all(
            isinstance(item.node, ast.alias)
            and isinstance(item.statement, ast.Import | ast.ImportFrom)
            and self.another_library(item.statement, item.node)
            for item in bindings
        )

    def another_library(self, statement: ast.Import | ast.ImportFrom, alias: ast.alias) -> bool:
        if isinstance(statement, ast.ImportFrom):
            if statement.level or not statement.module:
                return False
            dotted, names = statement.module, [alias.name]
        else:
            dotted, names = alias.name, []
        return not self.resolver.repository_holds(dotted, names)

    def _module(self, path: Path) -> PythonModule:
        try:
            return self.resolver._patch_scan(path)
        except _Stop as exc:
            raise CallLimit(exc.detail) from None

    def _candidates(self, names: set[str], subject: Path | None = None) -> list[Path]:
        pattern = re.compile(
            r"\b("
            + "|".join(
                re.escape(name)
                for name in sorted(names | {"__import__", "import_module", "importlib"})
            )
            + r")\b"
        )
        result = []
        ancestors = (
            set(subject.relative_to(self.resolver.scope_root).parts[:-1])
            if subject is not None
            else set()
        )
        parent_pattern = (
            re.compile(r"\b(" + "|".join(re.escape(part) for part in ancestors) + r")\b")
            if ancestors
            else None
        )
        for path in self._inventory():
            text = self._text(path)
            if path == subject or pattern.search(text):
                result.append(path)
            elif parent_pattern is not None and parent_pattern.search(text):
                caller = self._module(path)
                if any(
                    self._retained_namespace(caller, statement, alias, {subject})
                    for statement in ast.walk(caller.tree)
                    if isinstance(statement, ast.Import | ast.ImportFrom)
                    for alias in statement.names
                ):
                    result.append(path)
        if len(result) > MAX_CANDIDATES:
            raise CallLimit(f"the caller census exceeds {MAX_CANDIDATES} candidate modules")
        return result

    def _inventory(self) -> list[Path]:
        if self._files is not None:
            return self._files
        root = self.resolver.scope_root
        pending = [root]
        files: list[Path] = []
        entries = 0
        try:
            while pending:
                directory = pending.pop()
                children = list_input_directory(directory)
                entries += len(children)
                if entries > 100000:
                    raise CallLimit("the caller census exceeds 100000 directory entries")
                for child in children:
                    relative = child.relative_to(root).as_posix()
                    metadata = child.lstat()
                    if _test_path(relative):
                        continue
                    if stat.S_ISLNK(metadata.st_mode):
                        if child.suffix == ".py" or not child.name.startswith("."):
                            raise CallLimit(f"{relative} is a link; a caller behind it is not read")
                    elif stat.S_ISDIR(metadata.st_mode):
                        if child.name.startswith(".") or child.name in _SKIP:
                            continue
                        # Both presence and absence select which code is read.
                        marker = child / "pyvenv.cfg"
                        snapshot = active_static_input_snapshot()
                        absent = (
                            snapshot.bind_dependency_absence(marker)
                            if snapshot is not None
                            else not marker.exists()
                        )
                        if not absent:
                            if snapshot is not None:
                                snapshot.capture_selected_path(marker, allow_directory=False)
                                snapshot.mark_dependency_input(marker)
                            continue
                        pending.append(child)
                    elif child.suffix == ".py":
                        if not stat.S_ISREG(metadata.st_mode):
                            raise CallLimit(f"{relative} is not a regular Python file")
                        files.append(child)
                        if len(files) > MAX_FILES:
                            raise CallLimit(f"the caller census exceeds {MAX_FILES} Python files")
        except (InputParseError, OSError, ValueError) as exc:
            raise CallLimit(f"the caller census could not read the scope: {exc}") from None
        self._files = sorted(files)
        return self._files

    def _text(self, path: Path) -> str:
        if self._read_bytes > MAX_TOTAL_BYTES:
            raise CallLimit(f"the caller census exceeds {MAX_TOTAL_BYTES} source bytes")
        if path not in self._texts:
            try:
                self._texts[path] = load_text_file(path)
                snapshot = active_static_input_snapshot()
                if snapshot is not None:
                    snapshot.mark_dependency_input(path)
                self._read_bytes += len(self._texts[path].encode("utf-8"))
                if self._read_bytes > MAX_TOTAL_BYTES:
                    raise CallLimit(f"the caller census exceeds {MAX_TOTAL_BYTES} source bytes")
            except (InputParseError, ValueError) as exc:
                raise CallLimit(f"{self.resolver.ref(path)} could not be read: {exc}") from None
        return self._texts[path]


def single_return(function: Function) -> ast.expr | None:
    returns: list[ast.Return] = []
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        if isinstance(node, ast.Yield | ast.YieldFrom):
            return None
        if isinstance(node, ast.Return):
            returns.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return returns[0].value if len(returns) == 1 and function.body[-1] is returns[0] else None


def _has_yield(function: Function) -> bool:
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        if isinstance(node, ast.Yield | ast.YieldFrom):
            return True
        pending.extend(ast.iter_child_nodes(node))
    return False


def _test_path(path: str) -> bool:
    parts = Path(path).parts
    return (
        any(part in {"test", "tests"} for part in parts[:-1])
        or parts[-1] == "conftest.py"
        or parts[-1].startswith("test_")
        or parts[-1].endswith("_test.py")
    )


def _module_words(path: Path) -> set[str]:
    return {path.stem, path.parent.name} if path.stem == "__init__" else {path.stem}


def _candidate_imports(tree: ast.AST, names: set[str], modules: set[str]) -> tuple[set[str], bool]:
    """Expand possible imports, never arbitrary textual matches.

    These are candidates, not an import-identity proof. An ambiguous or
    conditional import still expands: refusing it belongs to the caller/list
    checks. A body/comment/string match alone cannot re-export the subject.
    """
    aliases: set[str] = set()
    possible = False
    for statement in ast.walk(tree):
        if not isinstance(statement, ast.Import | ast.ImportFrom):
            continue
        parent = set((getattr(statement, "module", None) or "").split("."))
        for alias in statement.names:
            imported = alias.name.rsplit(".", 1)[-1]
            if imported in names or modules & (parent | set(alias.name.split("."))):
                possible = True
                if imported in names and alias.asname:
                    aliases.add(alias.asname)
    return aliases, possible


def _export_strings(tree: ast.Module) -> set[int]:
    return {
        id(value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        and isinstance(node.value, ast.List | ast.Tuple)
        for value in node.value.elts
    }
