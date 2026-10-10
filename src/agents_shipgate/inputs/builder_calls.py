"""Bounded, source-only callers of agent builders (#874).

This indexes callers and binds Python arguments. Membership stays with
``ListExpressions``: one invocation supplies all of a construction's fields,
so tools from one call can never be paired with handoffs from another.
"""

from __future__ import annotations

import ast
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.static_inputs import active_static_input_snapshot
from agents_shipgate.core.trust_roots import IdentityBoundReadSession
from agents_shipgate.inputs.common import MAX_INPUT_FILE_BYTES, list_input_directory, load_text_file
from agents_shipgate.inputs.list_expressions import evaluation_site
from agents_shipgate.inputs.python_imports import (
    _PRELOADED,
    IMPORT_SEARCH_PATCH,
    LOCAL_BINDING,
    MODULE_NOT_FOUND,
    MODULE_TABLE_COMPUTED,
    NAME_NOT_DEFINED,
    NOT_A_FUNCTION,
    NOT_BOUND,
    PATH_PATCH,
    SELF_PATCH,
    ImportResolver,
    PythonModule,
    RepositoryLayout,
    Resolution,
    ScopeIndex,
    _attribute_patches,
    _CapturedImportSearchResolver,
    _external_constructor_paths,
    _external_decorator_paths,
    _external_wrapper_paths,
    _Stop,
    _type_checking_only,
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
MAX_NAMESPACE_CONTEXT_NODES = 20000
_SKIP = frozenset({"__pycache__", "node_modules", "site-packages"})
_ELSEWHERE = frozenset({NOT_BOUND, LOCAL_BINDING, NAME_NOT_DEFINED, NOT_A_FUNCTION})
_SOURCE_MAPPING_MUTATORS = frozenset({"update", "__init__"})


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
class NamespaceSourceContext:
    """Parsed source and terminal imports, never a namespace ownership proof.

    Reflection, module hooks, mutation and data confinement remain separate
    obligations even when every repository source in this context was read.
    """

    modules: tuple[PythonModule, ...]
    external_imports: tuple[str, ...]


@dataclass(frozen=True)
class _FreshPrimitiveDictionaryReceipt:
    """Local exact Function-data nodes and their still-open identity ledger."""

    proof: BuilderCalls
    values: frozenset[ast.expr]

    def reconfirm(self) -> None:
        if not self.proof._fresh_receipt_active or self.proof._fresh_receipt_finished:
            raise CallLimit("the fresh dictionary receipt is no longer active")
        self.proof._namespace_directory_currency()
        self.proof._fresh_identity_session.finish()
        self.proof._fresh_receipt_finished = True


@dataclass(frozen=True)
class _AbsentFieldDictionaryRoles:
    """Actual unread-field roles, distinct from same-slot identity evidence."""

    proof: BuilderCalls
    home: PythonModule
    function: ast.FunctionDef
    value: ast.expr
    namespace_nodes: frozenset[ast.expr]
    getter_metadata: ast.Constant | None = None
    intrinsic_projection: ast.Attribute | None = None


@dataclass(frozen=True)
class _AbsentFieldDictionaryReceipt:
    proof: BuilderCalls
    roles: _AbsentFieldDictionaryRoles

    def census(self) -> CallerCensus:
        return self.proof._census(self.roles.home, self.roles.function, allow_empty=True,
                                 absent_field_roles=self.roles)

    def reconfirm(self) -> None:
        if not self.proof._fresh_receipt_active or self.proof._fresh_receipt_finished:
            raise CallLimit("the absent-field receipt is no longer active")
        self.proof._namespace_directory_currency()
        self.proof._fresh_identity_session.finish()
        self.proof._fresh_receipt_finished = True


@dataclass(frozen=True)
class _SavedSourceVarsGetter:
    """Actual bare getter assignment and its sole saved mapping projection."""

    assignment: ast.Assign
    target: ast.Name
    initializer: ast.Name
    projection: ast.Call
    callee: ast.Name


@dataclass(frozen=True)
class _SavedSourceMapping:
    """Actual saved projection and its sole lexical Store receiver."""

    declaration: ast.Assign
    target: ast.Name
    receiver: ast.Name
    projection: ast.Attribute | ast.Call
    namespace: ast.Name
    saved_getter: _SavedSourceVarsGetter | None = None


_SourceSlotParts = tuple[ast.Name, ast.Name, str, ast.Attribute | ast.Call | None,
                         ast.Constant | None, _SavedSourceMapping | None]
_SourceSlotStatement = ast.Assign | ast.Expr | ast.AugAssign


@dataclass(frozen=True)
class _SourceSavedBoundIor:
    """Actual saved intrinsic method, separate from its mapping and Function."""

    assignment: ast.Assign
    target: ast.Name
    initializer: ast.Attribute
    receiver: ast.Name
    callee: ast.Name
    call: ast.Call
    payload: ast.Dict


@dataclass(frozen=True)
class _SourceOperatorSetitem:
    """Actual standard-operator tokens; no dictionary-method or setter fiction."""

    statement: ast.Import
    imported: ast.alias
    root: ast.Name
    callee: ast.Attribute
    call: ast.Call
    projection: ast.Attribute
    key: ast.Constant
    rhs: ast.Attribute


@dataclass(frozen=True)
class _SourceDirectOperatorIor:
    """An actual inline module dictionary, with no invented saved binding."""

    statement: ast.Import
    imported: ast.alias
    root: ast.Name
    callee: ast.Attribute
    call: ast.Call
    projection: ast.Attribute
    payload: ast.Dict
    key: ast.Constant
    rhs: ast.Attribute


@dataclass(frozen=True)
class _SourceOperatorIor:
    """Actual operator call and its independently confined saved mapping."""

    statement: ast.Import
    imported: ast.alias
    root: ast.Name
    callee: ast.Attribute
    call: ast.Call
    mapping: _SavedSourceMapping
    payload: ast.Dict
    key: ast.Constant
    rhs: ast.Attribute


@dataclass(frozen=True)
class _SourceFromOperatorIor:
    """An actual from-import and Name callee, never an invented module root."""

    statement: ast.ImportFrom
    imported: ast.alias
    callee: ast.Name
    call: ast.Call
    mapping: _SavedSourceMapping
    payload: ast.Dict
    key: ast.Constant
    rhs: ast.Attribute


@dataclass(frozen=True)
class _SourceDirectBareSetter:
    """The actual direct builtin callee, without an invented saved origin."""

    callee: ast.Name


@dataclass(frozen=True)
class _SourceSlotEdge:
    """Keep the actual Function value separate from its mutation syntax."""

    statement: _SourceSlotStatement
    parts: _SourceSlotParts
    rhs: ast.Attribute
    target: ast.Attribute | ast.Subscript | ast.Name | None = None
    callee: ast.Attribute | ast.Name | None = None
    call: ast.Call | None = None
    payload: ast.Dict | ast.keyword | None = None
    primitive: ast.Name | ast.Attribute | None = None
    primitive_import: _SourcePrimitiveImport | None = None
    setter_import: _SourceSetterImport | _SourceSavedSetterImport | _SourceFromSetterImport | None = None
    bare_setter: _SourceBareSetter | None = None
    direct_bare_setter: _SourceDirectBareSetter | None = None
    saved_bound_ior: _SourceSavedBoundIor | None = None
    operator_setitem: _SourceOperatorSetitem | None = None
    operator_ior: _SourceOperatorIor | _SourceDirectOperatorIor | None = None
    from_operator_ior: _SourceFromOperatorIor | None = None


@dataclass(frozen=True)
class _SourceSetterImport:
    """Actual qualified setter tokens, distinct from dictionary primitives."""

    statement: ast.Import
    imported: ast.alias
    root: ast.Name
    callee: ast.Attribute


@dataclass(frozen=True)
class _SourceSavedSetterImport:
    """Actual saved setter assignment and call, never a lexical alias grant."""

    statement: ast.Import
    imported: ast.alias
    root: ast.Name
    callee: ast.Name
    initializer: ast.Attribute
    assignment: ast.Assign
    target: ast.Name


@dataclass(frozen=True)
class _SourceFromSetterImport:
    """Actual from-import provenance and terminal, with no invented root token."""

    statement: ast.ImportFrom
    imported: ast.alias
    callee: ast.Name


@dataclass(frozen=True)
class _SourceBareSetter:
    """Actual bare builtin assignment and terminal; no import or root fiction."""

    assignment: ast.Assign
    target: ast.Name
    initializer: ast.Name
    callee: ast.Name


@dataclass(frozen=True)
class _SourcePrimitiveImport:
    """Actual qualified primitive tokens, never an import admission alone."""

    statement: ast.Import
    imported: ast.alias
    root: ast.Name
    primitive: ast.Attribute


@dataclass(frozen=True)
class _OperatorMetadataWrite:
    """An actual imported operator field write, never Function evidence."""

    statement: ast.Assign
    target: ast.Attribute
    receiver: ast.Name
    imported: ast.alias
    declaration: ast.Import

    @property
    def marker(self) -> str:
        return f"operator.{self.target.attr}"


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
class _DictionaryDataEdges:
    """Exact, independently confined dictionary occurrences in one parsed tree."""

    tree: ast.Module
    values: frozenset[ast.expr] = frozenset()
    keys: frozenset[ast.Constant] = frozenset()


@dataclass(frozen=True)
class _UnusedNamespaceEdges:
    tree: ast.Module
    values: frozenset[ast.Name] = frozenset()


@dataclass(frozen=True)
class ConstructionContext:
    invocation: Invocation | None = None
    limits: tuple[str, ...] = ()


class _FreshPrimitiveResolver(ImportResolver):
    """Receipt-only lookup reader; shared ASTs never share semantic caches."""

    def __init__(self, source: ImportResolver, session: IdentityBoundReadSession) -> None:
        super().__init__(source.scope_root)
        self._fresh_session = session
        self._fresh_source_layout = source._layout
        self._fresh_live_texts: dict[Path, str] = {}
        self._fresh_allowed_paths: frozenset[Path] | None = None
        layout = source._layout
        assert layout is not None and layout.disk_root == session.root
        self._layout = RepositoryLayout(layout.scope, self._checked_entries, self._checked_links,
                                        self._checked_read, disk_root=layout.disk_root)
        self._modules = {path: module for path, module in source._modules.items() if isinstance(module, PythonModule)}
        self._scanned = {path: module for path, module in source._scanned.items() if isinstance(module, PythonModule)}
        self._parsed = source._parsed

    def _live_text(self, path: Path) -> str:
        if path not in self._fresh_live_texts:
            self._fresh_live_texts[path] = self._fresh_session.read_bytes(
                path.relative_to(self._fresh_session.root), max_bytes=MAX_INPUT_FILE_BYTES,
            ).decode("utf-8")
        return self._fresh_live_texts[path]

    def _checked_module(self, module: PythonModule) -> PythonModule:
        if self._fresh_allowed_paths is not None and module.path not in self._fresh_allowed_paths:
            raise CallLimit(f"{module.ref} is outside the complete fresh dictionary context")
        if module.text != self._live_text(module.path):
            raise CallLimit(f"{module.ref} differs from its live fresh module bytes")
        return module

    def module(self, path: Path) -> PythonModule:
        return self._checked_module(super().module(path))

    def _patch_scan(self, path: Path) -> PythonModule:
        return self._checked_module(super()._patch_scan(path))

    def _listing(self, directory: Path) -> frozenset[str] | None:
        names = super()._listing(directory)
        if not directory.is_relative_to(self.scope_root):
            return names
        live = frozenset(self._fresh_session.directory_entries(directory.relative_to(self._fresh_session.root)))
        if names != live:
            raise CallLimit(f"{directory} differs from its live fresh lookup inventory")
        return names

    def _kind(self, directory: Path, name: str) -> str | None:
        expected = super()._kind(directory, name)
        names = self._listing(directory)
        if names is None or name not in names:
            return expected
        live = self._fresh_session.directory_entry_kind((directory / name).relative_to(self._fresh_session.root))
        mapped = {"file": "file", "directory": "dir", "symlink": "link"}.get(live)
        if mapped is None or mapped != expected:
            raise CallLimit(f"{directory / name} has unread or changed fresh provider kind")
        return expected

    def _checked_entries(self, ref: str) -> frozenset[str] | None:
        relative = Path(ref)
        if relative.is_absolute() or ".." in relative.parts:
            raise CallLimit("a fresh repository lookup leaves its identity root")
        layout = self._fresh_source_layout
        root = self._fresh_session.root
        parent = Path()
        for part in relative.parts:
            live = frozenset(self._fresh_session.directory_entries(parent))
            parent_ref = "" if parent == Path() else parent.as_posix()
            if layout.entries(parent_ref) != live:
                raise CallLimit(f"{root / parent} differs from its fresh repository lookup names")
            if part not in live:
                if layout.entries(ref) is not None:
                    raise CallLimit(f"{ref} has stale fresh repository directory evidence")
                return None
            parent /= part
            kind = self._fresh_session.directory_entry_kind(parent)
            if kind != "directory":
                if layout.entries(ref) is not None:
                    raise CallLimit(f"{ref} changed its fresh repository directory kind")
                return None
        live = frozenset(self._fresh_session.directory_entries(relative))
        if layout.entries(ref) != live:
            raise CallLimit(f"{ref} differs from its live fresh repository inventory")
        return live

    def _checked_links(self, ref: str) -> frozenset[str]:
        names = self._checked_entries(ref)
        if names is None:
            return frozenset()
        actual = frozenset(name for name in names
                           if self._fresh_session.directory_entry_kind(Path(ref) / name) == "symlink")
        if self._fresh_source_layout.links(ref) != actual:
            raise CallLimit(f"{ref} differs from its fresh repository link evidence")
        return actual

    def _checked_read(self, ref: str) -> str | None:
        text = self._fresh_source_layout.read(ref)
        if text is None:
            return None
        if text != self._live_text(self._fresh_session.root / ref):
            raise CallLimit(f"{ref} differs from its live fresh repository source")
        return text


class BuilderCalls:
    """One caller index per resolver/read, including negative read evidence."""

    def __init__(self, resolver: ImportResolver) -> None:
        self.resolver = resolver
        self._files: list[Path] | None = None
        # Retain the topology consumed by the cached caller inventory. These
        # obligations survive each source-context ancestry-map reset.
        self._inventory_directories: dict[Path, frozenset[tuple[str, int]]] = {}
        self._inventory_venv_selectors: dict[Path, tuple[bool, int | None]] = {}
        self._namespace_directories: dict[Path, frozenset[str] | None] = {}
        self._texts: dict[Path, str] = {}
        self._read_bytes = 0
        self._scopes: dict[int, ScopeIndex] = {}
        self._censuses: dict[tuple[int, bool], CallerCensus] = {}
        self._exports: dict[int, bool] = {}
        self._export_limits: dict[int, str] = {}
        self._export_routes: dict[int, set[str]] = {}
        self._borrowers: dict[tuple[int, str, bool], tuple[PythonModule, ...]] = {}
        self._borrower_aliases: dict[tuple[int, str, bool], tuple[str, ...]] = {}
        self._borrower_words: dict[tuple[int, str, bool], frozenset[str]] = {}
        self._borrower_paths: dict[tuple[int, str, bool], frozenset[Path]] = {}
        self._dynamic_imports: dict[int, ast.AST | None] = {}
        self._constructors: dict[tuple[int, int], str | None] = {}
        self._runtime_excluded: dict[int, frozenset[int]] = {}
        self._dictionary_data: dict[ast.Module, _DictionaryDataEdges] = {}
        self._source_operator_tokens: dict[ast.Module, tuple[frozenset[ast.Name], frozenset[ast.Constant]]] = {}
        self._source_from_operator_tokens: dict[
            ast.Module, tuple[dict[str, frozenset[ast.Name]], dict[str, frozenset[ast.Constant]]]
        ] = {}
        self._source_operator_positions: dict[ast.Module, dict[ast.stmt, int]] = {}
        self._unused_namespace_data: dict[ast.Module, _UnusedNamespaceEdges] = {}
        self._fresh_identity_session: IdentityBoundReadSession | None = None
        self._fresh_context_paths: frozenset[Path] | None = None
        self._fresh_receipt_active = False
        self._fresh_receipt_finished = False

    def runtime_exclusions(self, module: PythonModule) -> frozenset[int]:
        key = id(module.tree)
        if key not in self._runtime_excluded:
            self._runtime_excluded[key] = frozenset(_type_checking_only(module, self.resolver, self.scopes(module)))
        return self._runtime_excluded[key]

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

    def cached_constructor_issue(self, module: PythonModule, call: ast.Call) -> str | None:
        """Return only an already observed issue for this exact construction.

        Absence carries no proof or verdict and performs no constructor query.
        """
        return self._constructors.get((id(module.tree), id(call)))

    def constructor_issue(self, module: PythonModule, call: ast.Call) -> str | None:
        """One external class is shared by every known caller of its builder.

        The complete caller set is used even when the capability list is a
        literal and needs no argument substitution. Refuse an unfinished or
        escaping census instead of caching a proof for just one invocation.
        """
        key = (id(module.tree), id(call))
        if key in self._constructors:
            return self._constructors[key]
        modules: dict[Path, PythonModule] = {module.path: module}
        pending = [(module, self.function_at(module, call), 0)]
        seen: set[int] = set()
        try:
            while pending:
                home, function, depth = pending.pop()
                if function is None or id(function) in seen:
                    continue
                if depth >= MAX_DEPTH:
                    raise CallLimit("the constructor caller chain exceeds its depth bound")
                seen.add(id(function))
                census = self.callers(home, function, allow_empty=True)
                if census.limits:
                    raise CallLimit("; ".join(census.limits))
                for site in census.sites:
                    modules[site.module.path] = site.module
                    pending.append((site.module, self.function_at(site.module, site.call), depth + 1))
            issue = self.resolver.imported_constructor_issue(
                module, call.func, self.scopes(module), contexts=tuple(modules.values())
            )
        except CallLimit as exc:
            issue = f"the constructor caller census is not established: {exc}"
        self._constructors[key] = issue
        return issue

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
        child: ast.AST = construction
        conditions: list[str] = []
        while current is not None and current is not function:
            if isinstance(current, ast.If):
                positive = child in current.body
                if child not in (*current.body, *current.orelse):
                    return (ConstructionContext(limits=("a construction in a condition test is not followed through caller arguments",)),)
                if isinstance(current.test, ast.Constant):
                    if bool(current.test.value) != positive:
                        return (ConstructionContext(limits=("the construction lies in a statically unreachable branch",)),)
                else:
                    conditions.append(f"the construction's {'condition' if positive else 'negated condition'} `{ast.unparse(current.test)}` holds")
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
                return (
                    ConstructionContext(
                        limits=(
                            "a loop or handler construction is not followed through caller arguments",
                        )
                    ),
                )
            child = current
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
        contexts = self._expand(module, function, parameters, frozenset())
        return tuple(
            replace(context, invocation=replace(
                context.invocation,
                conditions=tuple(dict.fromkeys((*conditions, *context.invocation.conditions))),
            )) if context.invocation is not None else context
            for context in contexts
        )

    def dynamic_importer(self, module: PythonModule) -> ast.AST | None:
        """Include aliases and retained import machinery in the unread route."""
        key = id(module.tree)
        if key in self._dynamic_imports:
            return self._dynamic_imports[key]
        found = None
        excluded = self.runtime_exclusions(module)
        for node in ast.walk(module.tree):
            if id(node) in excluded:
                continue
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

    def callers(
        self, module: PythonModule, function: Function, *, allow_empty: bool = False,
        confined_dictionary_data: bool = False,
        unused_namespace_data: bool = False,
    ) -> CallerCensus:
        if confined_dictionary_data or unused_namespace_data:
            # A refined empty-caller proof is never cached as an ordinary
            # caller answer, nor reused across invocation/projection contexts.
            # Its immutable data edges are proved before inspecting value uses.
            try:
                return self._census(module, function, allow_empty=allow_empty,
                                    confined_dictionary_data=confined_dictionary_data,
                                    unused_namespace_data=unused_namespace_data)
            except CallLimit as exc:
                return CallerCensus(limits=(str(exc),))
        key = (id(function), allow_empty)
        cached = self._censuses.get(key)
        if cached is not None:
            return cached
        try:
            result = self._census(module, function, allow_empty=allow_empty)
        except CallLimit as exc:
            result = CallerCensus(limits=(str(exc),))
        self._censuses[key] = result
        return result

    def _fresh_unbound_dictionary_edge(self, module: PythonModule, value: ast.expr):
        """Exact empty allocation and one positional receiver; syntax only."""
        scopes = self.scopes(module)
        keyword = scopes.parents.get(value)
        call = scopes.parents.get(keyword)
        terminal = scopes.parents.get(call)
        if not (isinstance(value, ast.Name | ast.Attribute) and isinstance(value.ctx, ast.Load)
                and isinstance(keyword, ast.keyword) and keyword.value is value and keyword.arg is not None
                and isinstance(call, ast.Call) and call.keywords == [keyword] and len(call.args) == 1
                and isinstance(terminal, ast.Expr) and terminal.value is call and terminal in module.tree.body
                and scopes.parents.get(terminal) is module.tree
                and isinstance(callee := call.func, ast.Attribute) and isinstance(callee.ctx, ast.Load)
                and callee.attr == "__init__"
                and isinstance(receiver := call.args[0], ast.Name) and isinstance(receiver.ctx, ast.Load)):
            return None
        primitive = callee.value
        primitive_import = None
        if isinstance(primitive, ast.Name) and isinstance(primitive.ctx, ast.Load) and primitive.id == "dict":
            if (scopes.enclosing_bindings(evaluation_site(scopes, primitive), "dict")
                    or module.bindings.get("dict")):
                return None
        else:
            primitive_import = self._source_slot_primitive_import(module, primitive)
            if primitive_import is None:
                return None
        if isinstance(value, ast.Attribute) and not (isinstance(value.value, ast.Name) and isinstance(value.value.ctx, ast.Load)):
            return None
        rows = module.bindings.get(receiver.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(target := row.node, ast.Name) and isinstance(target.ctx, ast.Store)
                and isinstance(allocation := row.statement, ast.Assign) and allocation.targets == [target]
                and scopes.parents.get(allocation) is module.tree and allocation.lineno < terminal.lineno
                and isinstance(allocation.value, ast.Dict) and not allocation.value.keys and not allocation.value.values
                and not module.bindings.get("__all__")):
            return None
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Constant) and node.value == receiver.id:
                return None
            if isinstance(node, ast.Name) and node.id == receiver.id and node not in {receiver, target}:
                lexical = scopes.enclosing_bindings(evaluation_site(scopes, node), node.id)
                if not lexical or target in lexical:
                    return None
        return allocation, receiver, call, keyword, primitive_import

    @contextmanager
    def fresh_unbound_dictionary_receipt(self, module: PythonModule, value: ast.expr, family: str):
        """One local data receipt, never a cached primitive or caller grant."""
        if family not in {"agents", "google.adk"} or self._fresh_unbound_dictionary_edge(module, value) is None:
            yield None
            return
        if self.resolver._checking_fresh_dictionary_primitive:
            raise CallLimit("the fresh dictionary primitive receipt was reentered")
        self.resolver._checking_fresh_dictionary_primitive = True
        proof = BuilderCalls(self.resolver)
        try:
            layout = self.resolver._layout
            if layout is None or layout.disk_root is None:
                raise CallLimit("the fresh dictionary receipt has no bound disk currency")
            if layout.disk_root / layout.scope != self.resolver.scope_root:
                raise CallLimit("the fresh dictionary receipt's disk ancestry does not match the read scope")
            session = IdentityBoundReadSession(layout.disk_root, max_entries=200000, max_total_bytes=MAX_TOTAL_BYTES)
            private = _FreshPrimitiveResolver(self.resolver, session)
            private._checking_fresh_dictionary_primitive = True
            proof = BuilderCalls(private)
            proof._fresh_identity_session = session
            proof._fresh_receipt_active = True
            context = proof.namespace_source_context(family)
            if not any(current is module for current in context.modules):
                raise CallLimit("the fresh dictionary's actual module is outside the captured context")
            proof._fresh_context_paths = frozenset(current.path for current in context.modules)
            private._fresh_allowed_paths = proof._fresh_context_paths
            from agents_shipgate.inputs.list_expressions import (
                _namespace_call_reference,
                _qualified_attribute_patches,
                _unprovided_root,
                _View,
                bindings_at,
            )

            values = set()
            machinery = {
                "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr",
                "type", "object", "ModuleType", "__class__", "__dict__", "__builtins__", "__globals__", "__closure__",
                "__getattr__", "__getattribute__", "__setattr__", "__delattr__", "__code__", "__defaults__",
                "__kwdefaults__", "__annotations__", "cell_contents", "_getframe", "currentframe", "f_globals", "f_locals",
                "modules", "meta_path", "path_hooks", "path", "__path__", "__loader__", "__spec__",
            }
            for current in context.modules:
                if any(marker in {PATH_PATCH, IMPORT_SEARCH_PATCH} or marker.startswith(("*", SELF_PATCH)) for marker in current.attribute_patches):
                    raise CallLimit(f"{current.ref} has raw namespace or path mutation in the fresh dictionary context")
                scopes = proof.scopes(current)
                view = _View(current.ref, current.tree, scopes, current.bindings, current, set())
                view.lookup = bindings_at(scopes, current.bindings)
                view.resolver = private
                patches = _qualified_attribute_patches(view, raw_namespaces=True)
                if view.namespace_builtins_changed or any(part in machinery for patch in patches for part in patch.split(".")):
                    raise CallLimit(f"{current.ref} has unstable primitives or hooks in the fresh dictionary context")
                for node in ast.walk(current.tree):
                    if isinstance(node, ast.ClassDef | ast.Lambda | ast.GeneratorExp):
                        raise CallLimit(f"{current.ref}:{node.lineno} has an anonymous or class carrier in the fresh dictionary context")
                    spellings = ({node.id} if isinstance(node, ast.Name) else {node.attr} if isinstance(node, ast.Attribute)
                                 else {node.name, node.asname or ""} if isinstance(node, ast.alias)
                                 else {node.name} if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                                 else {node.value} if isinstance(node, ast.Constant) and isinstance(node.value, str) else set())
                    if any(part in machinery for spelling in spellings for part in spelling.split(".")):
                        raise CallLimit(f"{current.ref}:{node.lineno} has reflective machinery in the fresh dictionary context")
                for terminal in current.tree.body:
                    if not (isinstance(terminal, ast.Expr) and isinstance(call := terminal.value, ast.Call) and len(call.keywords) == 1):
                        continue
                    candidate = call.keywords[0].value
                    edge = proof._fresh_unbound_dictionary_edge(current, candidate)
                    if edge is None:
                        continue
                    allocation, _, _, _, primitive_import = edge
                    if primitive_import is not None:
                        proof._namespace_import_disk_root(current, primitive_import.statement, "fresh dictionary primitive import")
                        if not _unprovided_root(view, "builtins") or not proof._namespace_import_root_currency(
                            current, primitive_import.statement, "fresh dictionary primitive import",
                        ):
                            raise CallLimit(f"{current.ref}:{call.lineno} has an unread fresh builtin import root")
                        for other in context.modules:
                            if other is current:
                                continue
                            _, exported = proof._expanding_imports(
                                other, {primitive_import.root.id}, _module_words(current.path), {current.path},
                                subject=current.path, namespace_carriers=True,
                            )
                            if exported:
                                raise CallLimit(f"{current.ref} has an imported fresh dictionary primitive namespace")
                    if _namespace_call_reference(view, call) != "builtins.dict.__init__":
                        raise CallLimit(f"{current.ref}:{call.lineno} has an unread fresh dictionary initialization primitive")
                    for other in context.modules:
                        if other is current:
                            continue
                        _, exported = proof._expanding_imports(
                            other, {allocation.targets[0].id}, _module_words(current.path), {current.path},
                            subject=current.path, namespace_carriers=True,
                        )
                        if exported:
                            raise CallLimit(f"{current.ref} has an imported fresh dictionary allocation")
                    values.add(candidate)
                    if len(values) > MAX_CONTEXTS:
                        raise CallLimit("the fresh dictionary receipt exceeds its exact value bound")
            if value not in values:
                yield None
                return
            receipt = _FreshPrimitiveDictionaryReceipt(proof, frozenset(values))
            yield receipt
        except (InputParseError, OSError, ValueError) as exc:
            raise CallLimit(f"the fresh dictionary currency could not be established: {exc}") from None
        finally:
            proof._fresh_receipt_active = False
            proof.resolver._checking_fresh_dictionary_primitive = False
            self.resolver._checking_fresh_dictionary_primitive = False

    def fresh_dictionary_primitive_import(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Discharge only one actual Import with an independently uncalled data Function."""
        if not isinstance(node, ast.Import):
            return False
        candidates = []
        for terminal in module.tree.body:
            if not (isinstance(terminal, ast.Expr) and isinstance(call := terminal.value, ast.Call)
                    and len(call.keywords) == 1):
                continue
            value = call.keywords[0].value
            edge = self._fresh_unbound_dictionary_edge(module, value)
            if edge is not None and edge[4] is not None and edge[4].statement is node:
                candidates.append(value)
        if len(candidates) != 1:
            return False
        value = candidates[0]
        with self.fresh_unbound_dictionary_receipt(module, value, family) as receipt:
            if receipt is None:
                return False
            resolution = receipt.proof.resolve(module, value)
            if (not resolution.resolved or resolution.caveats or resolution.module is None
                    or not isinstance(resolution.definition, ast.FunctionDef | ast.AsyncFunctionDef)):
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

    def _absent_field_dictionary_edge(self, module: PythonModule, value: ast.expr):
        """One direct source import and exact saved dictionary getter; syntax only."""
        scopes = self.scopes(module)
        keyword = scopes.parents.get(value)
        call = scopes.parents.get(keyword)
        terminal = scopes.parents.get(call)
        if not (isinstance(value, ast.Attribute) and isinstance(value.ctx, ast.Load)
                and isinstance(root := value.value, ast.Name) and isinstance(root.ctx, ast.Load)
                and isinstance(keyword, ast.keyword) and keyword.value is value and keyword.arg is not None
                and not keyword.arg.startswith("_") and keyword.arg != value.attr
                and isinstance(call, ast.Call) and call.keywords == [keyword]
                and isinstance(callee := call.func, ast.Attribute) and isinstance(callee.ctx, ast.Load)
                and callee.attr in {"__init__", "update"}
                and isinstance(terminal, ast.Expr) and terminal.value is call and terminal in module.tree.body):
            return None
        primitive = None
        primitive_import = None
        if not call.args and isinstance(callee.value, ast.Name):
            receiver = callee.value
        elif (callee.attr == "__init__" and len(call.args) == 1 and isinstance(call.args[0], ast.Name)
              and isinstance(callee.value, ast.Name) and isinstance(callee.value.ctx, ast.Load)
              and callee.value.id == "dict" and not module.bindings.get("dict")
              and not scopes.enclosing_bindings(evaluation_site(scopes, callee.value), "dict")):
            receiver, primitive = call.args[0], callee.value
        elif (callee.attr == "__init__" and len(call.args) == 1 and isinstance(call.args[0], ast.Name)
              and (primitive_import := self._source_slot_primitive_import(module, callee.value)) is not None):
            receiver, primitive = call.args[0], callee.value
        else:
            return None
        if not ((mapping := self._source_slot_mapping(module, terminal, receiver)) is not None
                and ((isinstance(projection := mapping.projection, ast.Call)
                      and isinstance(getter := projection.func, ast.Name)
                      and (getter.id == "vars" or getter.id == "getattr" and callee.attr == "update" and not call.args))
                     or (isinstance(projection, ast.Attribute) and isinstance(projection.ctx, ast.Load)
                         and projection.attr == "__dict__" and projection.value is mapping.namespace
                         and isinstance(mapping.namespace.ctx, ast.Load)
                         and callee.attr == "update" and not call.args))
                and mapping.namespace.id == root.id and not module.bindings.get("__all__")):
            return None
        rows = module.bindings.get(root.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(imported := row.node, ast.alias)
                and imported.name == root.id and imported.asname is None and "." not in imported.name
                and isinstance(statement := row.statement, ast.Import) and statement.names == [imported]
                and scopes.parents.get(statement) is module.tree and statement.lineno < mapping.declaration.lineno
                and not scopes.enclosing_bindings(evaluation_site(scopes, root), root.id)):
            return None
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Constant) and node.value in {root.id, receiver.id}:
                return None
            if isinstance(node, ast.Name) and node.id == root.id and node not in {root, mapping.namespace}:
                return None
        return keyword, call, mapping, statement, imported, root, primitive, primitive_import

    @contextmanager
    def absent_field_dictionary_receipt(self, module: PythonModule, value: ast.expr, family: str):
        """Independent unread-field data proof; never a same-slot assertion."""
        if family not in {"agents", "google.adk"} or self._absent_field_dictionary_edge(module, value) is None:
            yield None
            return
        if self.resolver._checking_fresh_dictionary_primitive:
            raise CallLimit("the absent-field dictionary receipt was reentered")
        self.resolver._checking_fresh_dictionary_primitive = True
        proof = BuilderCalls(self.resolver)
        try:
            layout = self.resolver._layout
            if layout is None or layout.disk_root is None or layout.disk_root / layout.scope != self.resolver.scope_root:
                raise CallLimit("the absent-field dictionary receipt has no bound disk ancestry")
            session = IdentityBoundReadSession(layout.disk_root, max_entries=200000, max_total_bytes=MAX_TOTAL_BYTES)
            private = _FreshPrimitiveResolver(self.resolver, session)
            private._checking_fresh_dictionary_primitive = True
            proof = BuilderCalls(private)
            proof._fresh_identity_session = session
            proof._fresh_receipt_active = True
            context = proof.namespace_source_context(family)
            if not any(current is module for current in context.modules):
                raise CallLimit("the absent-field write module is outside the captured context")
            proof._fresh_context_paths = frozenset(current.path for current in context.modules)
            private._fresh_allowed_paths = proof._fresh_context_paths
            edge = proof._absent_field_dictionary_edge(module, value)
            if edge is None:
                yield None
                return
            keyword, call, mapping, statement, _, root, primitive, primitive_import = edge
            metadata = proof._source_slot_projection_metadata(mapping.projection)
            intrinsic = mapping.projection if isinstance(mapping.projection, ast.Attribute) else None
            getter = mapping.projection.func if isinstance(mapping.projection, ast.Call) else None
            if isinstance(getter, ast.Name) and getter.id == "getattr" and metadata is None:
                raise CallLimit("the selected source getter metadata is not an exact dictionary attribute")
            paths, missing = private._imported_paths(module, statement)
            if missing or len(paths) != 1 or not private.contains(paths[0]):
                raise CallLimit("the absent-field owner is not one readable source module")
            home = proof._module(paths[0])
            if not any(current is home for current in context.modules) or keyword.arg in home.bindings:
                raise CallLimit("the selected field is present or its source home is unread")
            resolution = proof.resolve(module, value)
            if (not resolution.resolved or resolution.caveats or resolution.module is not home
                    or not isinstance(function := resolution.definition, ast.FunctionDef)
                    or function not in home.tree.body or function.decorator_list or getattr(function, "type_params", [])):
                raise CallLimit("the absent-field RHS is not the actual original source Function")
            from agents_shipgate.inputs.list_expressions import (
                _namespace_call_reference,
                _qualified_attribute_patches,
                _unprovided_root,
                _View,
                bindings_at,
            )

            machinery = {
                "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr",
                "type", "object", "ModuleType", "__class__", "__dict__", "__builtins__", "__globals__", "__closure__",
                "__getattr__", "__getattribute__", "__setattr__", "__delattr__", "__code__", "__defaults__",
                "__kwdefaults__", "__annotations__", "cell_contents", "_getframe", "currentframe", "f_globals", "f_locals",
                "modules", "meta_path", "path_hooks", "path", "__path__", "__loader__", "__spec__", "dict",
            }
            if keyword.arg in machinery:
                raise CallLimit("the absent field is primitive or reflective metadata")
            for current in context.modules:
                if any(marker in {PATH_PATCH, IMPORT_SEARCH_PATCH} or marker.startswith(("*", SELF_PATCH)) for marker in current.attribute_patches):
                    raise CallLimit(f"{current.ref} has raw namespace mutation in the absent-field context")
                scopes = proof.scopes(current)
                view = _View(current.ref, current.tree, scopes, current.bindings, current, set())
                view.lookup = bindings_at(scopes, current.bindings)
                view.resolver = private
                patches = _qualified_attribute_patches(view, raw_namespaces=True)
                if view.namespace_builtins_changed or any(part in machinery for patch in patches for part in patch.split(".")):
                    raise CallLimit(f"{current.ref} has unstable primitives in the absent-field context")
                if current is module and primitive_import is not None:
                    proof._namespace_import_disk_root(current, primitive_import.statement, "absent-field initializer import")
                    if not _unprovided_root(view, "builtins") or not proof._namespace_import_root_currency(
                        current, primitive_import.statement, "absent-field initializer import",
                    ):
                        raise CallLimit("the absent-field builtin import root is unread")
                    for other in context.modules:
                        if other is current:
                            continue
                        _, shared = proof._expanding_imports(
                            other, {primitive_import.root.id}, _module_words(current.path), {current.path},
                            subject=current.path, namespace_carriers=True,
                        )
                        if shared:
                            raise CallLimit(f"{other.ref} imports the absent-field builtin namespace")
                if current is module and getter is not None and _namespace_call_reference(view, mapping.projection) != f"builtins.{getter.id}":
                    raise CallLimit("the selected source getter is not canonical")
                if current is module and primitive is not None and _namespace_call_reference(view, call) != "builtins.dict.__init__":
                    raise CallLimit("the selected absent-field initializer is not canonical dict.__init__")
                for node in ast.walk(current.tree):
                    if isinstance(node, ast.ClassDef | ast.Lambda | ast.GeneratorExp):
                        raise CallLimit(f"{current.ref} has an opaque carrier in the absent-field context")
                    spellings = ({node.id} if isinstance(node, ast.Name) else {node.attr} if isinstance(node, ast.Attribute)
                                 else {node.name, node.asname or ""} if isinstance(node, ast.alias)
                                 else {node.name} if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                                 else {node.arg} if isinstance(node, ast.arg | ast.keyword)
                                 else set(node.names) if isinstance(node, ast.Global | ast.Nonlocal)
                                 else {node.value} if isinstance(node, ast.Constant) and isinstance(node.value, str) else set())
                    if node is not keyword and keyword.arg in spellings:
                        raise CallLimit(f"{current.ref} observes or writes the selected absent field")
                    if node is not getter and node is not primitive and node is not metadata and node is not intrinsic and any(
                        part in machinery for spelling in spellings if isinstance(spelling, str) for part in spelling.split(".")
                    ):
                        raise CallLimit(f"{current.ref} has reflective machinery in the absent-field context")
                if current is not module:
                    _, writer_shared = proof._expanding_imports(
                        current, {mapping.receiver.id, root.id}, _module_words(module.path), {module.path},
                        subject=module.path, namespace_carriers=True,
                    )
                    if writer_shared:
                        raise CallLimit(f"{current.ref} imports the writer's mapping or namespace carrier")
                for imported in ast.walk(current.tree):
                    if not isinstance(imported, ast.Import | ast.ImportFrom) or imported is statement:
                        continue
                    _, shared = proof._expanding_imports(
                        current, {function.name, mapping.receiver.id}, _module_words(home.path), {home.path},
                        (imported,), subject=home.path, namespace_carriers=True,
                    )
                    if shared:
                        raise CallLimit(f"{current.ref} imports the changed source namespace")
            roles = _AbsentFieldDictionaryRoles(
                proof, home, function, value,
                frozenset({mapping.namespace, root, mapping.receiver, call.func}
                          | ({getter} if getter is not None else set())
                          | ({primitive} if primitive is not None else set())),
                getter_metadata=metadata, intrinsic_projection=intrinsic,
            )
            yield _AbsentFieldDictionaryReceipt(proof, roles)
        except (InputParseError, OSError, ValueError) as exc:
            raise CallLimit(f"the absent-field currency could not be established: {exc}") from None
        finally:
            proof._fresh_receipt_active = False
            proof.resolver._checking_fresh_dictionary_primitive = False
            self.resolver._checking_fresh_dictionary_primitive = False

    def _absent_field_dictionary_role(
        self, module: PythonModule, node: ast.AST, family: str, *, getter: bool, intrinsic: bool = False,
    ) -> bool:
        """Admit only the exact getter after its own zero-call data proof."""
        candidates = []
        for terminal in module.tree.body:
            if isinstance(terminal, ast.Expr) and isinstance(call := terminal.value, ast.Call) and len(call.keywords) == 1:
                value = call.keywords[0].value
                edge = self._absent_field_dictionary_edge(module, value)
                if edge is not None:
                    projection = edge[2].projection
                    selected = (projection if intrinsic and isinstance(projection, ast.Attribute)
                                else projection.func if getter and isinstance(projection, ast.Call)
                                else edge[2].namespace if not getter and not intrinsic else None)
                    if selected is node:
                        candidates.append(value)
        if len(candidates) != 1:
            return False
        with self.absent_field_dictionary_receipt(module, candidates[0], family) as receipt:
            if receipt is None:
                return False
            census = receipt.census()
            if census.sites or census.limits:
                return False
            receipt.reconfirm()
            return True

    def absent_field_dictionary_getter(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        return self._absent_field_dictionary_role(module, node, family, getter=True)

    def absent_field_dictionary_namespace(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        return self._absent_field_dictionary_role(module, node, family, getter=False)

    def absent_field_dictionary_projection(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Admit only the selected intrinsic Attribute after its own complete data proof."""
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load) or node.attr != "__dict__":
            return False
        return self._absent_field_dictionary_role(module, node, family, getter=False, intrinsic=True)

    def absent_field_dictionary_primitive_import(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Admit one actual Import only after its own unread-field zero-census."""
        if not isinstance(node, ast.Import):
            return False
        candidates = []
        for terminal in module.tree.body:
            if isinstance(terminal, ast.Expr) and isinstance(call := terminal.value, ast.Call) and len(call.keywords) == 1:
                value = call.keywords[0].value
                edge = self._absent_field_dictionary_edge(module, value)
                if edge is not None and edge[7] is not None and edge[7].statement is node:
                    candidates.append(value)
        if len(candidates) != 1:
            return False
        with self.absent_field_dictionary_receipt(module, candidates[0], family) as receipt:
            if receipt is None:
                return False
            census = receipt.census()
            if census.sites or census.limits:
                return False
            receipt.reconfirm()
            return True

    def confined_dictionary_value(self, module: PythonModule, value: ast.expr) -> bool:
        return value in self._confined_dictionary_data(module).values

    def unused_namespace_value(self, module: PythonModule, value: ast.expr) -> bool:
        return value in self._unused_namespaces(module).values

    def _source_slot_statement(self, module: PythonModule, node: ast.AST) -> _SourceSlotStatement | None:
        scopes = self.scopes(module)
        if isinstance(node, ast.Expr | ast.AugAssign) and self._source_slot_edge(module, node) is not None:
            return node
        ancestor = node
        for _ in range(5):
            parent = scopes.parents.get(ancestor)
            if isinstance(parent, ast.AugAssign) and ancestor in (parent.target, parent.value):
                if self._source_slot_edge(module, parent) is not None:
                    return parent
                break
            if isinstance(parent, ast.Expr) and parent.value is ancestor:
                if self._source_slot_edge(module, parent) is not None:
                    return parent
                break
            if not isinstance(parent, ast.Attribute | ast.keyword | ast.Dict | ast.Call):
                break
            ancestor = parent
        statement = node if isinstance(node, ast.Assign) else scopes.parents.get(node)
        if (isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Attribute)
                and self._source_slot_parts(module, statement) is not None):
            return statement
        if isinstance(statement, ast.Subscript) and statement.value is node:
            owner = scopes.parents.get(statement)
            if (isinstance(owner, ast.Assign) and owner.targets == [statement]
                    and (mapping := self._source_slot_mapping(module, owner)) is not None
                    and mapping.receiver is node):
                return owner
        if isinstance(node, ast.Name) and isinstance(scopes.parents.get(node), ast.Attribute):
            terminals = [terminal for terminal in module.tree.body if isinstance(terminal, ast.Expr)
                         and (saved := self._source_saved_bound_ior(module, terminal)) is not None
                         and saved.receiver is node and self._source_slot_edge(module, terminal) is not None]
            if len(terminals) == 1:
                return terminals[0]
        projection = node if isinstance(node, ast.Attribute | ast.Call) else scopes.parents.get(node)
        if (isinstance(projection, ast.Call)
                and isinstance(declaration := scopes.parents.get(projection), ast.Assign)
                and declaration.value is projection and scopes.parents.get(declaration) is module.tree
                and len(module.tree.body) <= MAX_NAMESPACE_CONTEXT_NODES):
            terminals = [terminal for terminal in module.tree.body if isinstance(terminal, ast.Assign)
                         and (mapping := self._source_slot_mapping(module, terminal)) is not None
                         and mapping.saved_getter is not None and mapping.projection is projection]
            if len(terminals) == 1:
                return terminals[0]
        if (isinstance(projection, ast.Attribute | ast.Call)
                and self._source_slot_projection(module, projection) is not None):
            target = scopes.parents.get(projection)
            owner = scopes.parents.get(target)
            if (isinstance(target, ast.Subscript) and target.value is projection
                    and isinstance(owner, ast.Assign) and owner.targets == [target]):
                return owner
            if (isinstance(target, ast.Assign) and target.value is projection
                    and owner is module.tree and len(owner.body) <= MAX_NAMESPACE_CONTEXT_NODES):
                terminals = [terminal for terminal in owner.body if isinstance(terminal, ast.Assign | ast.Expr | ast.AugAssign)
                             and (edge := self._source_slot_edge(module, terminal)) is not None
                             and (mapping := edge.parts[5]) is not None
                             and mapping.declaration is target and mapping.projection is projection]
                return terminals[0] if len(terminals) == 1 else None
        if not (isinstance(node, ast.Name) and isinstance(statement, ast.Assign | ast.AnnAssign)
                and statement.value is node):
            return None
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        body = scopes.parents.get(statement)
        if not (len(targets) == 1 and isinstance(targets[0], ast.Name)
                and isinstance(body, ast.Module | ast.FunctionDef)):
            return None
        if isinstance(body, ast.Module) and isinstance(statement, ast.Assign):
            names = self._source_from_operator_token_index(module)[0].get(targets[0].id, frozenset())
            loads = [name for name in names if name is not targets[0]]
            if len(loads) == 1:
                call = scopes.parents.get(loads[0])
                terminal = scopes.parents.get(call)
                if (isinstance(call, ast.Call) and call.args and call.args[0] is loads[0]
                        and isinstance(terminal, ast.Expr) and terminal.value is call
                        and (receivers := self._source_direct_bare_receivers(module, terminal)) is not None
                        and receivers[0][1] is node):
                    return terminal
        for terminal in body.body:
            if (isinstance(terminal, ast.Assign)
                    and (parts := self._source_slot_parts(module, terminal)) is not None
                    and parts[0].id == targets[0].id):
                receiver = self._source_slot_receiver(module, parts[0], terminal)
                if receiver is not None and receiver[1] is node:
                    return terminal
        return None

    def _source_slot_receiver(
        self, module: PythonModule, receiver: ast.Name, terminal: _SourceSlotStatement,
    ) -> tuple[ast.Name, ast.Name | None, ast.Name | None] | None:
        """One lexical Import, optionally through one confined plain alias."""
        if isinstance(terminal, ast.Expr) and self._source_direct_bare_setter(module, terminal) is not None:
            receivers = self._source_direct_bare_receivers(module, terminal)
            if receivers is None:
                return None
            assert isinstance(terminal.value, ast.Call) and isinstance(terminal.value.args[2], ast.Attribute)
            if receiver is terminal.value.args[0]:
                return receivers[0]
            if receiver is terminal.value.args[2].value:
                return receivers[1]
            return None
        scopes = self.scopes(module)
        def bindings(node: ast.Name) -> list[ast.AST]:
            return scopes.enclosing_bindings(evaluation_site(scopes, node), node.id) or [
                binding.node for binding in module.bindings.get(node.id, [])
            ]
        found = bindings(receiver)
        if len(found) != 1:
            return None
        original = receiver
        initializer = annotation = None
        if isinstance(found[0], ast.Name):
            if not isinstance(terminal, ast.Assign):
                return None  # Saved mapping mutation cannot compose a module alias.
            target = found[0]
            declaration = scopes.parents.get(target)
            body = scopes.parents.get(declaration)
            if not (isinstance(declaration, ast.Assign | ast.AnnAssign)
                    and isinstance(declaration.value, ast.Name)
                    and isinstance(body, ast.Module | ast.FunctionDef)
                    and scopes.parents.get(terminal) is body and declaration.lineno < terminal.lineno):
                return None
            if isinstance(declaration, ast.Assign):
                if declaration.targets != [target]:
                    return None
            else:
                if not (declaration.simple and isinstance(declaration.annotation, ast.Name)
                        and declaration.annotation.id == "object"
                        and not bindings(declaration.annotation)):
                    return None
                annotation = declaration.annotation
            if isinstance(body, ast.FunctionDef):
                _, global_names, nonlocal_names = scopes._scope(body)
                if {receiver.id, declaration.value.id} & (global_names | nonlocal_names):
                    return None
            parts = self._source_slot_parts(module, terminal)
            if parts is None:
                return None
            admitted_receivers = set(parts[:2])
            for use in ast.walk(module.tree):
                if isinstance(use, ast.Constant) and use.value == receiver.id:
                    return None
                if isinstance(use, ast.Name) and use.id == receiver.id and bindings(use) == [target]:
                    if use is not target and use not in admitted_receivers:
                        return None
            initializer = original = declaration.value
            found = bindings(original)
        if len(found) != 1 or not isinstance(found[0], ast.alias):
            return None
        imported = scopes.statement_of(found[0])
        if not (isinstance(imported, ast.Import) and imported.lineno < original.lineno
                and isinstance(scopes.parents.get(imported), ast.Module | ast.FunctionDef)):
            return None
        return original, initializer, annotation

    def _source_slot_projection(
        self, module: PythonModule, projection: ast.AST,
    ) -> ast.Name | None:
        """The exact intrinsic getter shape, without an ownership proof."""
        if (isinstance(projection, ast.Attribute) and isinstance(projection.ctx, ast.Load)
                and projection.attr == "__dict__" and isinstance(projection.value, ast.Name)):
            return projection.value
        if (isinstance(projection, ast.Call) and isinstance(projection.func, ast.Name)
                and projection.func.id == "vars" and len(projection.args) == 1 and not projection.keywords
                and isinstance(projection.args[0], ast.Name)
                and not self.scopes(module).enclosing_bindings(evaluation_site(self.scopes(module), projection.func), "vars")
                and not module.bindings.get("vars")):
            return projection.args[0]
        if (isinstance(projection, ast.Call) and isinstance(projection.func, ast.Name)
                and isinstance(projection.func.ctx, ast.Load) and projection.func.id == "getattr"
                and len(projection.args) == 2 and not projection.keywords
                and isinstance(projection.args[0], ast.Name) and isinstance(projection.args[0].ctx, ast.Load)
                and isinstance(projection.args[1], ast.Constant) and projection.args[1].value == "__dict__"
                and not self.scopes(module).enclosing_bindings(evaluation_site(self.scopes(module), projection.func), "getattr")
                and not module.bindings.get("getattr")):
            return projection.args[0]
        return None

    @staticmethod
    def _source_slot_projection_metadata(projection: ast.AST | None) -> ast.Constant | None:
        """Exact getter metadata token, separate from the mutation's slot key."""
        if (isinstance(projection, ast.Call) and isinstance(projection.func, ast.Name)
                and projection.func.id == "getattr" and len(projection.args) == 2 and not projection.keywords
                and isinstance(metadata := projection.args[1], ast.Constant) and metadata.value == "__dict__"):
            return metadata
        return None

    def _source_slot_primitive_import(
        self, module: PythonModule, primitive: ast.AST,
    ) -> _SourcePrimitiveImport | None:
        scopes = self.scopes(module)
        if not (isinstance(primitive, ast.Attribute) and isinstance(primitive.ctx, ast.Load)
                and primitive.attr == "dict" and isinstance(root := primitive.value, ast.Name)
                and isinstance(root.ctx, ast.Load) and root.id == "builtins"
                and not scopes.enclosing_bindings(evaluation_site(scopes, root), root.id)):
            return None
        rows = module.bindings.get(root.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(imported := row.node, ast.alias)
                and imported.name == "builtins" and imported.asname is None
                and isinstance(statement := row.statement, ast.Import) and statement.names == [imported]
                and scopes.parents.get(imported) is statement and scopes.parents.get(statement) is module.tree
                and statement.lineno < primitive.lineno):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == root.id:
                return None
            if isinstance(use, ast.Name) and use.id == root.id and use is not root:
                lexical = scopes.enclosing_bindings(evaluation_site(scopes, use), use.id)
                if not lexical or imported in lexical:
                    return None
        return _SourcePrimitiveImport(statement, imported, root, primitive)

    def _source_slot_setter_import(self, module: PythonModule, callee: ast.AST) -> _SourceSetterImport | None:
        """One exact imported builtin setter spelling; no canonical authority yet."""
        scopes = self.scopes(module)
        if not (isinstance(callee, ast.Attribute) and isinstance(callee.ctx, ast.Load)
                and callee.attr == "setattr" and isinstance(root := callee.value, ast.Name)
                and isinstance(root.ctx, ast.Load) and root.id == "builtins"
                and not scopes.enclosing_bindings(evaluation_site(scopes, root), root.id)):
            return None
        rows = module.bindings.get(root.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(imported := row.node, ast.alias)
                and imported.name == "builtins" and imported.asname is None
                and isinstance(statement := row.statement, ast.Import) and statement.names == [imported]
                and scopes.parents.get(imported) is statement and scopes.parents.get(statement) is module.tree
                and statement.lineno < callee.lineno):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == root.id:
                return None
            if isinstance(use, ast.Name) and use.id == root.id and use is not root:
                lexical = scopes.enclosing_bindings(evaluation_site(scopes, use), use.id)
                if not lexical or imported in lexical:
                    return None
        return _SourceSetterImport(statement, imported, root, callee)

    def _source_slot_saved_setter_import(self, module: PythonModule, callee: ast.AST) -> _SourceSavedSetterImport | None:
        """One earlier plain setter handle and its sole terminal call; syntax only."""
        scopes = self.scopes(module)
        if not (isinstance(callee, ast.Name) and isinstance(callee.ctx, ast.Load)
                and not scopes.enclosing_bindings(evaluation_site(scopes, callee), callee.id)):
            return None
        rows = module.bindings.get(callee.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(target := row.node, ast.Name) and isinstance(target.ctx, ast.Store)
                and isinstance(assignment := row.statement, ast.Assign) and assignment.targets == [target]
                and scopes.parents.get(target) is assignment and scopes.parents.get(assignment) is module.tree
                and assignment.lineno < callee.lineno
                and (record := self._source_slot_setter_import(module, assignment.value)) is not None):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == callee.id:
                return None
            if isinstance(use, ast.Name) and use.id == callee.id and use not in {target, callee}:
                return None
        return _SourceSavedSetterImport(record.statement, record.imported, record.root, callee,
                                        record.callee, assignment, target)

    def _source_slot_from_setter_import(self, module: PythonModule, callee: ast.AST) -> _SourceFromSetterImport | None:
        """One actual absolute setter alias and its sole call; syntax only."""
        scopes = self.scopes(module)
        if not (isinstance(callee, ast.Name) and isinstance(callee.ctx, ast.Load)
                and not callee.id.startswith("_") and callee.id not in {"setattr", "vars", "getattr", "dict", "builtins"}
                and not scopes.enclosing_bindings(evaluation_site(scopes, callee), callee.id)):
            return None
        rows = module.bindings.get(callee.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(imported := row.node, ast.alias)
                and imported.name == "setattr" and imported.asname == callee.id
                and isinstance(statement := row.statement, ast.ImportFrom) and statement.level == 0
                and statement.module == "builtins" and statement.names == [imported]
                and scopes.parents.get(imported) is statement and scopes.parents.get(statement) is module.tree
                and statement in module.tree.body and statement.lineno < callee.lineno):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == callee.id:
                return None
            if isinstance(use, ast.Name) and use.id == callee.id and use is not callee:
                return None
        return _SourceFromSetterImport(statement, imported, callee)

    def _source_slot_bare_setter(self, module: PythonModule, callee: ast.AST) -> _SourceBareSetter | None:
        """One unshadowed bare setter initializer and its sole saved terminal."""
        scopes = self.scopes(module)
        if not (isinstance(callee, ast.Name) and isinstance(callee.ctx, ast.Load)
                and not scopes.enclosing_bindings(evaluation_site(scopes, callee), callee.id)):
            return None
        rows = module.bindings.get(callee.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(target := row.node, ast.Name) and isinstance(target.ctx, ast.Store)
                and isinstance(assignment := row.statement, ast.Assign) and assignment.targets == [target]
                and scopes.parents.get(target) is assignment and scopes.parents.get(assignment) is module.tree
                and assignment in module.tree.body and assignment.lineno < callee.lineno
                and isinstance(initializer := assignment.value, ast.Name) and isinstance(initializer.ctx, ast.Load)
                and initializer.id == "setattr" and not module.bindings.get("setattr")
                and not scopes.enclosing_bindings(evaluation_site(scopes, initializer), initializer.id)):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value in {callee.id, initializer.id}:
                return None
            if isinstance(use, ast.Name) and use.id == callee.id and use not in {target, callee}:
                return None
            if isinstance(use, ast.Name) and use.id == initializer.id and use is not initializer:
                return None
        return _SourceBareSetter(assignment, target, initializer, callee)

    def _source_direct_bare_setter(self, module: PythonModule, statement: ast.AST) -> _SourceDirectBareSetter | None:
        """Exact physical syntax; builtin identity and Function ownership remain owed."""
        if len(module.tree.body) > MAX_NAMESPACE_CONTEXT_NODES:
            return None
        positions = self._source_operator_positions.get(module.tree)
        if positions is None:
            positions = self._source_operator_positions[module.tree] = {
                node: index for index, node in enumerate(module.tree.body)
            }
        scopes = self.scopes(module)
        if not (isinstance(statement, ast.Expr) and scopes.parents.get(statement) is module.tree
                and statement in positions and isinstance(call := statement.value, ast.Call)
                and len(call.args) == 3 and not call.keywords
                and isinstance(callee := call.func, ast.Name) and isinstance(callee.ctx, ast.Load)
                and callee.id == "setattr" and not module.bindings.get(callee.id)
                and not scopes.enclosing_bindings(evaluation_site(scopes, callee), callee.id)
                and isinstance(receiver := call.args[0], ast.Name) and isinstance(receiver.ctx, ast.Load)
                and isinstance(key := call.args[1], ast.Constant) and isinstance(key.value, str)
                and isinstance(value := call.args[2], ast.Attribute) and isinstance(value.ctx, ast.Load)
                and isinstance(value.value, ast.Name) and isinstance(value.value.ctx, ast.Load)
                and key.value == value.attr and not value.attr.startswith("_")
                and not module.bindings.get("__all__")):
            return None
        return _SourceDirectBareSetter(callee)

    def _source_direct_bare_receivers(
        self, module: PythonModule, terminal: ast.Expr,
    ) -> tuple[tuple[ast.Name, ast.Name | None, None], tuple[ast.Name, None, None]] | None:
        """Confine only this direct setter's two actual source namespace reads."""
        if self._source_direct_bare_setter(module, terminal) is None:
            return None
        call = terminal.value
        assert isinstance(call, ast.Call) and isinstance(call.args[2], ast.Attribute)
        left, right = call.args[0], call.args[2].value
        assert isinstance(left, ast.Name) and isinstance(right, ast.Name)
        scopes = self.scopes(module)
        positions = self._source_operator_positions[module.tree]
        rows = module.bindings.get(right.id, [])
        if not (len(rows) == 1 and rows[0].top_level
                and isinstance(imported := rows[0].node, ast.alias)
                and imported.name == right.id and imported.asname is None and "." not in right.id
                and isinstance(origin := rows[0].statement, ast.Import) and origin.names == [imported]
                and scopes.parents.get(origin) is module.tree and origin in positions
                and positions[origin] < positions[terminal] and origin.lineno < terminal.lineno
                and not scopes.enclosing_bindings(evaluation_site(scopes, right), right.id)):
            return None
        tokens = self._source_from_operator_token_index(module)
        initializer = None
        imported_receiver = False
        if left.id != right.id:
            rows = module.bindings.get(left.id, [])
            if len(rows) != 1 or not rows[0].top_level:
                return None
            if isinstance(receiver_alias := rows[0].node, ast.alias):
                receiver_origin = rows[0].statement
                if not (receiver_alias.name == imported.name and receiver_alias.asname == left.id
                        and left.id not in {right.id, "setattr"} and not left.id.startswith("_")
                        and isinstance(receiver_origin, ast.Import) and receiver_origin.names == [receiver_alias]
                        and scopes.parents.get(receiver_origin) is module.tree and receiver_origin in positions
                        and positions[receiver_origin] < positions[terminal]
                        and receiver_origin.lineno < terminal.lineno
                        and tokens[0].get(left.id) == {left} and not tokens[1].get(left.id)):
                    return None
                imported_receiver = True
            elif not (isinstance(target := rows[0].node, ast.Name) and isinstance(target.ctx, ast.Store)
                    and isinstance(declaration := rows[0].statement, ast.Assign) and declaration.targets == [target]
                    and scopes.parents.get(target) is declaration and scopes.parents.get(declaration) is module.tree
                    and declaration in positions and positions[origin] < positions[declaration] < positions[terminal]
                    and origin.lineno < declaration.lineno < terminal.lineno
                    and isinstance(initializer := declaration.value, ast.Name) and isinstance(initializer.ctx, ast.Load)
                    and initializer.id == right.id
                    and not scopes.enclosing_bindings(evaluation_site(scopes, initializer), initializer.id)
                    and tokens[0].get(left.id) == {target, left} and not tokens[1].get(left.id)):
                return None
        expected_source_reads = {right} if imported_receiver else {initializer or left, right}
        if (scopes.enclosing_bindings(evaluation_site(scopes, left), left.id)
                or tokens[0].get(right.id) != expected_source_reads or tokens[1].get(right.id)):
            return None
        return (initializer or left, initializer, None), (right, None, None)

    def _source_slot_setter_edge(self, module: PythonModule, statement: ast.Expr) -> _SourceSlotEdge | None:
        """Direct source same-slot setter syntax, separate from dictionary methods."""
        scopes = self.scopes(module)
        if not (scopes.parents.get(statement) is module.tree and statement in module.tree.body
                and isinstance(call := statement.value, ast.Call) and len(call.args) == 3 and not call.keywords
                and (record := (self._source_slot_setter_import(module, call.func)
                                or self._source_slot_saved_setter_import(module, call.func)
                                or self._source_slot_from_setter_import(module, call.func)
                                or self._source_slot_bare_setter(module, call.func))) is not None
                and isinstance(receiver := call.args[0], ast.Name) and isinstance(receiver.ctx, ast.Load)
                and isinstance(key := call.args[1], ast.Constant) and isinstance(key.value, str)
                and isinstance(value := call.args[2], ast.Attribute) and isinstance(value.ctx, ast.Load)
                and isinstance(root := value.value, ast.Name) and isinstance(root.ctx, ast.Load)
                and root.id == receiver.id and not value.attr.startswith("_") and key.value == value.attr
                and not module.bindings.get("__all__")):
            return None
        rows = module.bindings.get(receiver.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(imported := row.node, ast.alias)
                and imported.name == receiver.id and imported.asname is None and "." not in imported.name
                and isinstance(source_import := row.statement, ast.Import) and source_import.names == [imported]
                and scopes.parents.get(source_import) is module.tree and source_import.lineno < statement.lineno
                and not scopes.enclosing_bindings(evaluation_site(scopes, receiver), receiver.id)):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == receiver.id:
                return None
            if isinstance(use, ast.Name) and use.id == receiver.id and use not in {receiver, root}:
                return None
        parts = (receiver, root, value.attr, None, key, None)
        return _SourceSlotEdge(statement, parts, value, target=receiver, callee=record.callee, call=call,
                               setter_import=None if isinstance(record, _SourceBareSetter) else record,
                               bare_setter=record if isinstance(record, _SourceBareSetter) else None)

    def _source_slot_setitem_parts(
        self, module: PythonModule, call: ast.Call,
    ) -> tuple[ast.Name, ast.Constant, ast.Attribute] | None:
        """Only the bare positional spelling; canonical identity is still unproved."""
        if not (isinstance(callee := call.func, ast.Attribute) and isinstance(callee.ctx, ast.Load)
                and callee.attr == "__setitem__" and isinstance(primitive := callee.value, ast.Name)
                and isinstance(primitive.ctx, ast.Load) and primitive.id == "dict"
                and len(call.args) == 3 and not call.keywords
                and isinstance(receiver := call.args[0], ast.Name) and isinstance(receiver.ctx, ast.Load)
                and isinstance(key := call.args[1], ast.Constant) and isinstance(key.value, str)
                and isinstance(value := call.args[2], ast.Attribute) and isinstance(value.ctx, ast.Load)):
            return None
        scopes = self.scopes(module)
        if (scopes.enclosing_bindings(evaluation_site(scopes, primitive), "dict")
                or module.bindings.get("dict")):
            return None
        return receiver, key, value

    def _source_slot_mutator_receiver(
        self, module: PythonModule, call: ast.Call,
    ) -> tuple[ast.Name, ast.Name | ast.Attribute | None] | None:
        """Recover syntax only; unbound primitive identity still needs raw proof."""
        if (positional := self._source_slot_setitem_parts(module, call)) is not None:
            return positional[0], call.func.value
        callee = call.func
        if isinstance(callee, ast.Attribute) and callee.attr == "__ior__":
            if (isinstance(callee.ctx, ast.Load) and isinstance(owner := callee.value, ast.Name)
                    and isinstance(owner.ctx, ast.Load) and len(call.args) == 1 and not call.keywords
                    and isinstance(payload := call.args[0], ast.Dict)
                    and len(payload.keys) == 1 and len(payload.values) == 1
                    and isinstance(key := payload.keys[0], ast.Constant) and isinstance(key.value, str)
                    and isinstance(value := payload.values[0], ast.Attribute) and isinstance(value.ctx, ast.Load)
                    and isinstance(value.value, ast.Name) and isinstance(value.value.ctx, ast.Load)
                    and not value.attr.startswith("_") and key.value == value.attr):
                return owner, None  # Only a bound singleton literal; never an unbound primitive.
            return None
        if not (isinstance(callee, ast.Attribute) and isinstance(callee.ctx, ast.Load)
                and callee.attr in _SOURCE_MAPPING_MUTATORS):
            return None
        owner = callee.value
        if (callee.attr in _SOURCE_MAPPING_MUTATORS and call.args
                and isinstance(receiver := call.args[0], ast.Name) and isinstance(receiver.ctx, ast.Load)
                and self._source_slot_primitive_import(module, owner) is not None):
            return receiver, owner
        if not isinstance(owner, ast.Name) or not isinstance(owner.ctx, ast.Load):
            return None
        scopes = self.scopes(module)
        if (owner.id == "dict" and callee.attr == "__init__" and call.args
                and isinstance(receiver := call.args[0], ast.Name) and isinstance(receiver.ctx, ast.Load)
                and not scopes.enclosing_bindings(evaluation_site(scopes, owner), "dict")
                and not module.bindings.get("dict")):
            return receiver, owner
        return owner, None

    def _saved_source_vars_getter(
        self, module: PythonModule, projection: ast.AST, declaration: ast.Assign,
    ) -> _SavedSourceVarsGetter | None:
        """Syntax only; restricted to a separate, saved mapping Store route."""
        scopes = self.scopes(module)
        if not (isinstance(projection, ast.Call) and projection is declaration.value
                and scopes.parents.get(projection) is declaration
                and scopes.parents.get(declaration) is module.tree and declaration in module.tree.body
                and isinstance(callee := projection.func, ast.Name) and isinstance(callee.ctx, ast.Load)
                and len(projection.args) == 1 and not projection.keywords
                and isinstance(namespace := projection.args[0], ast.Name) and isinstance(namespace.ctx, ast.Load)
                and not scopes.enclosing_bindings(evaluation_site(scopes, callee), callee.id)):
            return None
        rows = module.bindings.get(callee.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(target := row.node, ast.Name) and isinstance(target.ctx, ast.Store)
                and isinstance(assignment := row.statement, ast.Assign) and assignment.targets == [target]
                and scopes.parents.get(target) is assignment and scopes.parents.get(assignment) is module.tree
                and assignment in module.tree.body and assignment.lineno < declaration.lineno
                and isinstance(initializer := assignment.value, ast.Name) and isinstance(initializer.ctx, ast.Load)
                and initializer.id == "vars" and not module.bindings.get("vars")
                and not scopes.enclosing_bindings(evaluation_site(scopes, initializer), initializer.id)):
            return None
        source = module.bindings.get(namespace.id, [])
        if not (len(source) == 1 and source[0].top_level
                and isinstance(imported := source[0].node, ast.alias)
                and imported.name == namespace.id and imported.asname is None and "." not in imported.name
                and isinstance(statement := source[0].statement, ast.Import) and statement.names == [imported]
                and scopes.parents.get(statement) is module.tree and statement.lineno < declaration.lineno):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value in {callee.id, initializer.id}:
                return None
            if isinstance(use, ast.Name) and use.id == callee.id and use not in {target, callee}:
                return None
            if isinstance(use, ast.Name) and use.id == initializer.id and use is not initializer:
                return None
        return _SavedSourceVarsGetter(assignment, target, initializer, projection, callee)

    def _source_saved_bound_ior(
        self, module: PythonModule, terminal: ast.Expr,
    ) -> _SourceSavedBoundIor | None:
        """Syntax only: a physical discarded call through one saved method."""
        scopes = self.scopes(module)
        if not (scopes.parents.get(terminal) is module.tree and terminal in module.tree.body
                and isinstance(call := terminal.value, ast.Call)
                and isinstance(callee := call.func, ast.Name) and isinstance(callee.ctx, ast.Load)
                and len(call.args) == 1 and not call.keywords
                and isinstance(payload := call.args[0], ast.Dict)
                and len(payload.keys) == 1 and len(payload.values) == 1
                and isinstance(key := payload.keys[0], ast.Constant) and isinstance(key.value, str)
                and isinstance(value := payload.values[0], ast.Attribute) and isinstance(value.ctx, ast.Load)
                and isinstance(value.value, ast.Name) and isinstance(value.value.ctx, ast.Load)
                and not value.attr.startswith("_") and key.value == value.attr
                and not scopes.enclosing_bindings(evaluation_site(scopes, callee), callee.id)):
            return None
        rows = module.bindings.get(callee.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(target := row.node, ast.Name) and isinstance(target.ctx, ast.Store)
                and isinstance(assignment := row.statement, ast.Assign) and assignment.targets == [target]
                and scopes.parents.get(target) is assignment and scopes.parents.get(assignment) is module.tree
                and assignment in module.tree.body and assignment.lineno < terminal.lineno
                and isinstance(initializer := assignment.value, ast.Attribute) and isinstance(initializer.ctx, ast.Load)
                and initializer.attr == "__ior__" and isinstance(receiver := initializer.value, ast.Name)
                and isinstance(receiver.ctx, ast.Load)):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == callee.id:
                return None
            if isinstance(use, ast.Name) and use.id == callee.id and use not in {target, callee}:
                return None
        return _SourceSavedBoundIor(assignment, target, initializer, receiver, callee, call, payload)

    def _source_slot_mapping(
        self, module: PythonModule, terminal: ast.Assign | ast.Expr, receiver: ast.Name | None = None,
    ) -> _SavedSourceMapping | None:
        """One plain saved mapping declaration, with exactly one mutation use."""
        scopes = self.scopes(module)
        saved = None
        if scopes.parents.get(terminal) is not module.tree:
            return None
        if isinstance(terminal, ast.Assign):
            if not (receiver is None and len(terminal.targets) == 1
                    and isinstance(target := terminal.targets[0], ast.Subscript)
                    and isinstance(target.ctx, ast.Store) and isinstance(receiver := target.value, ast.Name)):
                return None
        else:
            saved = self._source_saved_bound_ior(module, terminal)
            if saved is not None:
                if saved.receiver is not receiver:
                    return None
            elif not (isinstance(terminal.value, ast.Call)
                      and (mutator := self._source_slot_mutator_receiver(module, terminal.value)) is not None
                      and mutator[0] is receiver):
                return None
        if not isinstance(receiver, ast.Name) or not isinstance(receiver.ctx, ast.Load):
            return None
        def bindings(node: ast.Name) -> list[ast.AST]:
            return scopes.enclosing_bindings(evaluation_site(scopes, node), node.id) or [
                binding.node for binding in module.bindings.get(node.id, [])
            ]
        found = bindings(receiver)
        if len(found) != 1 or not isinstance(alias := found[0], ast.Name):
            return None
        declaration = scopes.parents.get(alias)
        if not (isinstance(declaration, ast.Assign) and declaration.targets == [alias]
                and scopes.parents.get(declaration) is module.tree and declaration.lineno < terminal.lineno
                and isinstance(projection := declaration.value, ast.Attribute | ast.Call)):
            return None
        namespace = self._source_slot_projection(module, projection)
        getter = None
        if namespace is None and isinstance(terminal, ast.Assign):
            getter = self._saved_source_vars_getter(module, projection, declaration)
            if getter is not None:
                namespace = getter.projection.args[0]
        if namespace is None:
            return None
        if saved is not None:
            if not (isinstance(projection, ast.Attribute) and projection.attr == "__dict__"
                    and declaration in module.tree.body
                    and module.tree.body.index(declaration) < module.tree.body.index(saved.assignment)
                    < module.tree.body.index(terminal) and declaration.lineno < saved.assignment.lineno):
                return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == receiver.id:
                return None
            if (saved is not None
                    and isinstance(use, ast.Name) and use.id == receiver.id and use not in {alias, receiver}):
                return None
            if isinstance(use, ast.Name) and use.id == receiver.id and bindings(use) == [alias]:
                if use is not alias and use is not receiver:
                    return None
        return _SavedSourceMapping(declaration, alias, receiver, projection, namespace, getter)

    def _source_slot_augmented_mapping(self, module: PythonModule, terminal: ast.AugAssign) -> _SavedSourceMapping | None:
        """Exactly the declaration and this augmented target bind the mapping."""
        scopes = self.scopes(module)
        receiver = terminal.target
        if not (scopes.parents.get(terminal) is module.tree and terminal in module.tree.body
                and isinstance(terminal.op, ast.BitOr) and isinstance(receiver, ast.Name)
                and scopes.parents.get(receiver) is terminal
                and isinstance(receiver.ctx, ast.Store)
                and not scopes.enclosing_bindings(evaluation_site(scopes, receiver), receiver.id)):
            return None
        rows = module.bindings.get(receiver.id, [])
        if len(rows) != 2:
            return None
        augmented = [row for row in rows if row.node is receiver and row.statement is terminal]
        earlier = [row for row in rows if row.node is not receiver]
        if len(augmented) != 1 or len(earlier) != 1:
            return None
        row = earlier[0]
        alias, declaration = row.node, row.statement
        if not (row.top_level and isinstance(alias, ast.Name) and isinstance(alias.ctx, ast.Store)
                and isinstance(declaration, ast.Assign) and declaration.targets == [alias]
                and scopes.parents.get(alias) is declaration and scopes.parents.get(declaration) is module.tree
                and declaration.lineno < terminal.lineno
                and isinstance(projection := declaration.value, ast.Attribute | ast.Call)
                and (namespace := self._source_slot_projection(module, projection)) is not None):
            return None
        domain = {alias, receiver}
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == receiver.id:
                return None
            if isinstance(use, ast.Name) and use.id == receiver.id and use not in domain:
                lexical = scopes.enclosing_bindings(evaluation_site(scopes, use), use.id)
                if not lexical or domain.intersection(lexical):
                    return None
        return _SavedSourceMapping(declaration, alias, receiver, projection, namespace)

    def _source_slot_parts(
        self, module: PythonModule, statement: ast.Assign,
    ) -> _SourceSlotParts | None:
        """Exact attribute slot, or intrinsic dictionary slot on direct imports.

        The intrinsic dictionary role is source-only and remains conditional
        on the independent raw context and empty census in the caller.
        """
        if not (len(statement.targets) == 1 and isinstance(value := statement.value, ast.Attribute)
                and isinstance(value.ctx, ast.Load) and isinstance(value.value, ast.Name)
                and not value.attr.startswith("_")):
            return None
        target = statement.targets[0]
        if (isinstance(target, ast.Attribute) and isinstance(target.ctx, ast.Store)
                and target.attr == value.attr and isinstance(target.value, ast.Name)):
            return target.value, value.value, value.attr, None, None, None
        if not (isinstance(target, ast.Subscript) and isinstance(target.ctx, ast.Store)
                and isinstance(key := target.slice, ast.Constant) and key.value == value.attr
                and self.scopes(module).parents.get(statement) is module.tree):
            return None
        projection = target.value
        mapping = self._source_slot_mapping(module, statement) if isinstance(projection, ast.Name) else None
        if mapping is not None:
            projection = mapping.projection
        namespace = mapping.namespace if mapping is not None else self._source_slot_projection(module, projection)
        if namespace is None:
            return None
        if mapping is not None and mapping.saved_getter is not None and value.value.id != namespace.id:
            return None  # This getter composition uses one direct source Import on both sides.
        if self._source_slot_projection_metadata(projection) is not None and mapping is None:
            return None  # This getter route requires the independently confined saved mapping.
        assert isinstance(projection, ast.Attribute | ast.Call)
        return namespace, value.value, value.attr, projection, key, mapping

    def _source_operator_setitem(self, module: PythonModule, statement: ast.AST) -> _SourceOperatorSetitem | None:
        """Extract one discarded physical call; native provenance is not syntax."""
        if len(module.tree.body) > MAX_NAMESPACE_CONTEXT_NODES or not isinstance(statement, ast.Expr):
            return None
        positions = self._source_operator_positions.get(module.tree)
        if positions is None:
            positions = self._source_operator_positions[module.tree] = {
                node: index for index, node in enumerate(module.tree.body)
            }
        scopes = self.scopes(module)
        if not (len(module.tree.body) <= MAX_NAMESPACE_CONTEXT_NODES
                and isinstance(statement, ast.Expr) and scopes.parents.get(statement) is module.tree
                and statement in positions and isinstance(call := statement.value, ast.Call)
                and not call.keywords and len(call.args) == 3
                and isinstance(callee := call.func, ast.Attribute) and isinstance(callee.ctx, ast.Load)
                and callee.attr == "setitem" and isinstance(operator := callee.value, ast.Name)
                and isinstance(operator.ctx, ast.Load) and operator.id == "operator"
                and isinstance(projection := call.args[0], ast.Attribute) and isinstance(projection.ctx, ast.Load)
                and projection.attr == "__dict__" and isinstance(namespace := projection.value, ast.Name)
                and isinstance(namespace.ctx, ast.Load)
                and isinstance(key := call.args[1], ast.Constant) and isinstance(key.value, str)
                and isinstance(rhs := call.args[2], ast.Attribute) and isinstance(rhs.ctx, ast.Load)
                and isinstance(rhs.value, ast.Name) and isinstance(rhs.value.ctx, ast.Load)
                and rhs.value.id == namespace.id and key.value == rhs.attr and not rhs.attr.startswith("_")):
            return None
        origin = None
        for name in (operator.id, namespace.id):
            rows = module.bindings.get(name, [])
            if not (len(rows) == 1 and rows[0].top_level and isinstance(imported := rows[0].node, ast.alias)
                    and imported.name == name and imported.asname is None and "." not in name
                    and isinstance(declaration := rows[0].statement, ast.Import)
                    and declaration.names == [imported] and scopes.parents.get(declaration) is module.tree
                    and declaration in positions and positions[declaration] < positions[statement]
                    and declaration.lineno < statement.lineno):
                return None
            if name == operator.id:
                origin = (declaration, imported)
        if namespace.id == operator.id or origin is None:
            return None
        tokens = self._source_operator_tokens.get(module.tree)
        if tokens is None:
            # Structural evidence only, shared across candidate extraction in
            # this immutable tree. Native/owner verdicts are never cached here.
            names: set[ast.Name] = set()
            strings: set[ast.Constant] = set()
            for node in ast.walk(module.tree):
                if isinstance(node, ast.Name) and node.id == operator.id:
                    names.add(node)
                elif isinstance(node, ast.Constant) and node.value == operator.id:
                    strings.add(node)
            tokens = self._source_operator_tokens[module.tree] = (frozenset(names), frozenset(strings))
        if tokens[0] != {operator} or tokens[1] - {key}:
            return None
        return _SourceOperatorSetitem(*origin, operator, callee, call, projection, key, rhs)

    def _source_direct_operator_ior(self, module: PythonModule, statement: ast.AST) -> _SourceDirectOperatorIor | None:
        """Recognize exact inline syntax; the independent slot proof grants its role."""
        if len(module.tree.body) > MAX_NAMESPACE_CONTEXT_NODES or not isinstance(statement, ast.Expr):
            return None
        scopes = self.scopes(module)
        if not (scopes.parents.get(statement) is module.tree
                and isinstance(call := statement.value, ast.Call) and not call.keywords and len(call.args) == 2
                and isinstance(callee := call.func, ast.Attribute) and isinstance(callee.ctx, ast.Load)
                and callee.attr == "ior" and isinstance(operator := callee.value, ast.Name)
                and isinstance(operator.ctx, ast.Load) and operator.id == "operator"
                and isinstance(projection := call.args[0], ast.Attribute) and isinstance(projection.ctx, ast.Load)
                and projection.attr == "__dict__" and isinstance(namespace := projection.value, ast.Name)
                and isinstance(namespace.ctx, ast.Load) and namespace.id != "operator"
                and isinstance(payload := call.args[1], ast.Dict) and len(payload.keys) == len(payload.values) == 1
                and isinstance(key := payload.keys[0], ast.Constant) and isinstance(key.value, str)
                and isinstance(rhs := payload.values[0], ast.Attribute) and isinstance(rhs.ctx, ast.Load)
                and isinstance(rhs.value, ast.Name) and isinstance(rhs.value.ctx, ast.Load)
                and namespace.id == rhs.value.id and key.value == rhs.attr and not rhs.attr.startswith("_")):
            return None
        tokens = self._source_operator_tokens.get(module.tree)
        if tokens is None:
            names: set[ast.Name] = set()
            strings: set[ast.Constant] = set()
            for node in ast.walk(module.tree):
                if isinstance(node, ast.Name) and node.id == "operator":
                    names.add(node)
                elif isinstance(node, ast.Constant) and node.value == "operator":
                    strings.add(node)
            tokens = self._source_operator_tokens[module.tree] = (frozenset(names), frozenset(strings))
        if tokens[0] != {operator} or tokens[1] - {key}:
            return None
        origin = None
        for name in ("operator", namespace.id):
            bindings = module.bindings.get(name, [])
            if not (len(bindings) == 1 and bindings[0].top_level
                    and isinstance(imported := bindings[0].node, ast.alias)
                    and imported.name == name and imported.asname is None and "." not in name
                    and isinstance(source := bindings[0].statement, ast.Import) and source.names == [imported]
                    and scopes.parents.get(source) is module.tree and source.lineno < statement.lineno):
                return None
            if name == "operator":
                origin = (source, imported)
        assert origin is not None
        return _SourceDirectOperatorIor(*origin, operator, callee, call, projection, payload, key, rhs)

    def _source_operator_ior(self, module: PythonModule, statement: ast.AST) -> _SourceOperatorIor | _SourceDirectOperatorIor | None:
        """One discarded native-operator syntax candidate, without a semantic grant."""
        if (direct := self._source_direct_operator_ior(module, statement)) is not None:
            return direct
        if len(module.tree.body) > MAX_NAMESPACE_CONTEXT_NODES or not isinstance(statement, ast.Expr):
            return None
        positions = self._source_operator_positions.get(module.tree)
        if positions is None:
            positions = self._source_operator_positions[module.tree] = {
                node: index for index, node in enumerate(module.tree.body)
            }
        scopes = self.scopes(module)
        if not (scopes.parents.get(statement) is module.tree and statement in positions
                and isinstance(call := statement.value, ast.Call) and not call.keywords and len(call.args) == 2
                and isinstance(callee := call.func, ast.Attribute) and isinstance(callee.ctx, ast.Load)
                and callee.attr == "ior" and isinstance(operator := callee.value, ast.Name)
                and isinstance(operator.ctx, ast.Load) and operator.id == "operator"
                and isinstance(receiver := call.args[0], ast.Name) and isinstance(receiver.ctx, ast.Load)
                and isinstance(payload := call.args[1], ast.Dict) and len(payload.keys) == len(payload.values) == 1
                and isinstance(key := payload.keys[0], ast.Constant) and isinstance(key.value, str)
                and isinstance(rhs := payload.values[0], ast.Attribute) and isinstance(rhs.ctx, ast.Load)
                and isinstance(rhs.value, ast.Name) and isinstance(rhs.value.ctx, ast.Load)
                and key.value == rhs.attr and not rhs.attr.startswith("_")):
            return None
        tokens = self._source_operator_tokens.get(module.tree)
        if tokens is None:
            names: set[ast.Name] = set()
            strings: set[ast.Constant] = set()
            for node in ast.walk(module.tree):
                if isinstance(node, ast.Name) and node.id == "operator":
                    names.add(node)
                elif isinstance(node, ast.Constant) and node.value == "operator":
                    strings.add(node)
            tokens = self._source_operator_tokens[module.tree] = (frozenset(names), frozenset(strings))
        if tokens[0] != {operator} or tokens[1] - {key}:
            return None
        rows = module.bindings.get(receiver.id, [])
        if not (len(rows) == 1 and rows[0].top_level
                and isinstance(target := rows[0].node, ast.Name) and isinstance(target.ctx, ast.Store)
                and isinstance(declaration := rows[0].statement, ast.Assign) and declaration.targets == [target]
                and scopes.parents.get(target) is declaration and scopes.parents.get(declaration) is module.tree
                and declaration in positions and positions[declaration] < positions[statement]
                and declaration.lineno < statement.lineno
                and isinstance(projection := declaration.value, ast.Attribute) and isinstance(projection.ctx, ast.Load)
                and projection.attr == "__dict__" and isinstance(namespace := projection.value, ast.Name)
                and isinstance(namespace.ctx, ast.Load) and namespace.id == rhs.value.id
                and namespace.id != "operator" and receiver.id not in {namespace.id, "operator"}):
            return None
        origin = None
        for name, before in (("operator", statement), (namespace.id, declaration)):
            bindings = module.bindings.get(name, [])
            if not (len(bindings) == 1 and bindings[0].top_level
                    and isinstance(imported := bindings[0].node, ast.alias)
                    and imported.name == name and imported.asname is None and "." not in name
                    and isinstance(source := bindings[0].statement, ast.Import) and source.names == [imported]
                    and scopes.parents.get(source) is module.tree and source in positions
                    and positions[source] < positions[before] and source.lineno < before.lineno):
                return None
            if name == "operator":
                origin = (source, imported)
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == receiver.id:
                return None
            if isinstance(use, ast.Name) and use.id == receiver.id and use not in {target, receiver}:
                return None
        assert origin is not None
        mapping = _SavedSourceMapping(declaration, target, receiver, projection, namespace)
        return _SourceOperatorIor(*origin, operator, callee, call, mapping, payload, key, rhs)

    def _source_from_operator_token_index(
        self, module: PythonModule,
    ) -> tuple[dict[str, frozenset[ast.Name]], dict[str, frozenset[ast.Constant]]]:
        """Structural nodes only, shared without native or ownership verdicts."""
        tokens = self._source_from_operator_tokens.get(module.tree)
        if tokens is None:
            names: dict[str, set[ast.Name]] = {}
            strings: dict[str, set[ast.Constant]] = {}
            for node in ast.walk(module.tree):
                if isinstance(node, ast.Name):
                    names.setdefault(node.id, set()).add(node)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    strings.setdefault(node.value, set()).add(node)
            tokens = self._source_from_operator_tokens[module.tree] = (
                {key: frozenset(values) for key, values in names.items()},
                {key: frozenset(values) for key, values in strings.items()},
            )
        return tokens

    def _source_from_operator_ior(self, module: PythonModule, statement: ast.AST) -> _SourceFromOperatorIor | None:
        """One discarded native-operator syntax candidate, without a semantic grant."""
        if len(module.tree.body) > MAX_NAMESPACE_CONTEXT_NODES or not isinstance(statement, ast.Expr):
            return None
        positions = self._source_operator_positions.get(module.tree)
        if positions is None:
            positions = self._source_operator_positions[module.tree] = {
                node: index for index, node in enumerate(module.tree.body)
            }
        scopes = self.scopes(module)
        if not (scopes.parents.get(statement) is module.tree and statement in positions
                and isinstance(call := statement.value, ast.Call) and not call.keywords and len(call.args) == 2
                and isinstance(callee := call.func, ast.Name) and isinstance(callee.ctx, ast.Load)
                and callee.id not in {"ior", "dict", "operator", "vars", "getattr", "setattr", "eval", "exec", "__import__"}
                and not callee.id.startswith("_")
                and isinstance(receiver := call.args[0], ast.Name) and isinstance(receiver.ctx, ast.Load)
                and isinstance(payload := call.args[1], ast.Dict) and len(payload.keys) == len(payload.values) == 1
                and isinstance(key := payload.keys[0], ast.Constant) and isinstance(key.value, str)
                and isinstance(rhs := payload.values[0], ast.Attribute) and isinstance(rhs.ctx, ast.Load)
                and isinstance(rhs.value, ast.Name) and isinstance(rhs.value.ctx, ast.Load)
                and key.value == rhs.attr and not rhs.attr.startswith("_")):
            return None
        tokens = self._source_from_operator_token_index(module)
        if (tokens[0].get(callee.id) != {callee} or tokens[1].get(callee.id)
                or callee.id in {receiver.id, rhs.value.id, rhs.attr}):
            return None
        origin_rows = module.bindings.get(callee.id, [])
        if not (len(origin_rows) == 1 and origin_rows[0].top_level
                and isinstance(imported := origin_rows[0].node, ast.alias)
                and imported.name == "ior" and imported.asname == callee.id
                and isinstance(origin := origin_rows[0].statement, ast.ImportFrom)
                and origin.module == "operator" and origin.level == 0 and origin.names == [imported]
                and scopes.parents.get(origin) is module.tree and origin in positions
                and positions[origin] < positions[statement] and origin.lineno < statement.lineno):
            return None
        operator_origin = (origin, imported)
        rows = module.bindings.get(receiver.id, [])
        if not (len(rows) == 1 and rows[0].top_level
                and isinstance(target := rows[0].node, ast.Name) and isinstance(target.ctx, ast.Store)
                and isinstance(declaration := rows[0].statement, ast.Assign) and declaration.targets == [target]
                and scopes.parents.get(target) is declaration and scopes.parents.get(declaration) is module.tree
                and declaration in positions and positions[declaration] < positions[statement]
                and declaration.lineno < statement.lineno
                and isinstance(projection := declaration.value, ast.Attribute) and isinstance(projection.ctx, ast.Load)
                and projection.attr == "__dict__" and isinstance(namespace := projection.value, ast.Name)
                and isinstance(namespace.ctx, ast.Load) and namespace.id == rhs.value.id
                and namespace.id != callee.id and receiver.id not in {namespace.id, callee.id}):
            return None
        source_rows = module.bindings.get(namespace.id, [])
        if not (len(source_rows) == 1 and source_rows[0].top_level
                and isinstance(source_alias := source_rows[0].node, ast.alias)
                and source_alias.name == namespace.id and source_alias.asname is None and "." not in namespace.id
                and isinstance(source := source_rows[0].statement, ast.Import) and source.names == [source_alias]
                and scopes.parents.get(source) is module.tree and source in positions
                and positions[source] < positions[declaration] and source.lineno < declaration.lineno):
            return None
        if tokens[0].get(receiver.id) != {target, receiver} or tokens[1].get(receiver.id):
            return None
        mapping = _SavedSourceMapping(declaration, target, receiver, projection, namespace)
        return _SourceFromOperatorIor(*operator_origin, callee, call, mapping, payload, key, rhs)

    def _source_slot_edge(self, module: PythonModule, statement: _SourceSlotStatement) -> _SourceSlotEdge | None:
        if (record := self._source_direct_bare_setter(module, statement)) is not None:
            assert isinstance(statement, ast.Expr) and isinstance(call := statement.value, ast.Call)
            if self._source_direct_bare_receivers(module, statement) is None:
                return None
            receiver, key, value = call.args
            assert isinstance(receiver, ast.Name) and isinstance(value, ast.Attribute)
            parts = (receiver, value.value, value.attr, None, key, None)
            return _SourceSlotEdge(statement, parts, value, target=receiver, callee=record.callee, call=call,
                                   direct_bare_setter=record)
        if (record := self._source_from_operator_ior(module, statement)) is not None:
            mapping = record.mapping
            parts = (mapping.namespace, record.rhs.value, record.rhs.attr, mapping.projection, record.key, mapping)
            return _SourceSlotEdge(statement, parts, record.rhs, callee=record.callee, call=record.call,
                                   payload=record.payload, from_operator_ior=record)
        if (record := self._source_operator_ior(module, statement)) is not None:
            if isinstance(record, _SourceDirectOperatorIor):
                parts = (record.projection.value, record.rhs.value, record.rhs.attr, record.projection, record.key, None)
                return _SourceSlotEdge(statement, parts, record.rhs, callee=record.callee, call=record.call,
                                       payload=record.payload, operator_ior=record)
            mapping = record.mapping
            parts = (mapping.namespace, record.rhs.value, record.rhs.attr, mapping.projection, record.key, mapping)
            return _SourceSlotEdge(statement, parts, record.rhs, callee=record.callee, call=record.call,
                                   payload=record.payload, operator_ior=record)
        if (record := self._source_operator_setitem(module, statement)) is not None:
            parts = (record.projection.value, record.rhs.value, record.rhs.attr, record.projection, record.key, None)
            return _SourceSlotEdge(statement, parts, record.rhs, callee=record.callee, call=record.call,
                                   operator_setitem=record)
        if isinstance(statement, ast.Expr) and (setter := self._source_slot_setter_edge(module, statement)) is not None:
            return setter
        if isinstance(statement, ast.Expr) and (saved := self._source_saved_bound_ior(module, statement)) is not None:
            mapping = self._source_slot_mapping(module, statement, saved.receiver)
            if mapping is None:
                return None
            value = saved.payload.values[0]
            key = saved.payload.keys[0]
            assert isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and isinstance(key, ast.Constant)
            scopes = self.scopes(module)
            rows = module.bindings.get(mapping.namespace.id, [])
            if not (mapping.namespace.id == value.value.id and len(rows) == 1 and rows[0].top_level
                    and isinstance(imported := rows[0].node, ast.alias) and imported.name == mapping.namespace.id
                    and imported.asname is None and "." not in imported.name
                    and isinstance(source_import := rows[0].statement, ast.Import) and source_import.names == [imported]
                    and scopes.parents.get(source_import) is module.tree
                    and source_import in module.tree.body
                    and module.tree.body.index(source_import) < module.tree.body.index(mapping.declaration)
                    and source_import.lineno < mapping.declaration.lineno):
                return None
            parts = (mapping.namespace, value.value, value.attr, mapping.projection, key, mapping)
            return _SourceSlotEdge(statement, parts, value, callee=saved.callee, call=saved.call,
                                   payload=saved.payload, saved_bound_ior=saved)
        if isinstance(statement, ast.Assign):
            parts = self._source_slot_parts(module, statement)
            if parts is None:
                return None
            return _SourceSlotEdge(statement, parts, statement.value, statement.targets[0])
        if isinstance(statement, ast.AugAssign):
            if not (self.scopes(module).parents.get(statement) is module.tree and isinstance(statement.op, ast.BitOr)
                    and isinstance(payload := statement.value, ast.Dict)
                    and len(payload.keys) == 1 and len(payload.values) == 1
                    and isinstance(key := payload.keys[0], ast.Constant) and isinstance(key.value, str)
                    and isinstance(value := payload.values[0], ast.Attribute) and isinstance(value.ctx, ast.Load)
                    and isinstance(value.value, ast.Name) and not value.attr.startswith("_") and key.value == value.attr
                    and (mapping := self._source_slot_augmented_mapping(module, statement)) is not None):
                return None
            parts = (mapping.namespace, value.value, value.attr, mapping.projection, key, mapping)
            return _SourceSlotEdge(statement, parts, value, target=mapping.receiver, payload=payload)
        if not (self.scopes(module).parents.get(statement) is module.tree
                and isinstance(call := statement.value, ast.Call)
                and isinstance(callee := call.func, ast.Attribute)
                and (mutator := self._source_slot_mutator_receiver(module, call)) is not None):
            return None
        receiver, primitive = mutator
        arguments = call.args[1:] if primitive is not None else call.args
        key = None
        if callee.attr == "__setitem__":
            positional = self._source_slot_setitem_parts(module, call)
            assert positional is not None
            _, key, value = positional
            field, payload = key.value, None
        elif not arguments and len(call.keywords) == 1:
            payload = call.keywords[0]
            field, value = payload.arg, payload.value
        elif (not call.keywords and len(arguments) == 1 and isinstance(payload := arguments[0], ast.Dict)
              and len(payload.keys) == 1 and len(payload.values) == 1
              and isinstance(key := payload.keys[0], ast.Constant) and isinstance(key.value, str)):
            field, value = key.value, payload.values[0]
        else:
            return None
        if not (isinstance(value, ast.Attribute) and isinstance(value.ctx, ast.Load)
                and isinstance(value.value, ast.Name) and not value.attr.startswith("_")
                and field == value.attr and (mapping := self._source_slot_mapping(module, statement, receiver)) is not None):
            return None
        if callee.attr == "__ior__":
            scopes = self.scopes(module)
            rows = module.bindings.get(mapping.namespace.id, [])
            if not (isinstance(mapping.projection, ast.Attribute) and mapping.projection.attr == "__dict__"
                    and mapping.projection.value is mapping.namespace
                    and mapping.namespace.id == value.value.id and len(rows) == 1
                    and rows[0].top_level and isinstance(imported := rows[0].node, ast.alias)
                    and imported.name == mapping.namespace.id and imported.asname is None and "." not in imported.name
                    and isinstance(source_import := rows[0].statement, ast.Import) and source_import.names == [imported]
                    and scopes.parents.get(source_import) is module.tree
                    and source_import.lineno < mapping.declaration.lineno):
                return None  # Other getter/import compositions need their own declared scope.
        parts = (mapping.namespace, value.value, field, mapping.projection, key, mapping)
        return _SourceSlotEdge(statement, parts, value, callee=callee, call=call, payload=payload,
                               target=receiver if primitive is not None else None, primitive=primitive,
                               primitive_import=self._source_slot_primitive_import(module, primitive)
                               if primitive is not None else None)

    def _source_slot_candidate(
        self, module: PythonModule, node: ast.AST,
    ) -> tuple[PythonModule, ast.FunctionDef, _SourceSlotStatement] | None:
        scopes = self.scopes(module)
        statement = self._source_slot_statement(module, node)
        if not (isinstance(statement, ast.Assign | ast.Expr | ast.AugAssign) and not module.star_import
                and (edge := self._source_slot_edge(module, statement)) is not None):
            return None
        target, value, parts = edge.target, edge.rhs, edge.parts
        left_name, right_name, slot, projection, key, mapping = parts
        left_receiver = self._source_slot_receiver(module, left_name, statement)
        right_receiver = self._source_slot_receiver(module, right_name, statement)
        if left_receiver is None or right_receiver is None:
            return None
        if mapping is not None and (left_receiver[1] is not None or right_receiver[1] is not None):
            return None
        if projection is not None:
            for receiver in (left_receiver, right_receiver):
                found = self.scopes(module).enclosing_bindings(evaluation_site(scopes, receiver[0]), receiver[0].id)
                found = found or [binding.node for binding in module.bindings.get(receiver[0].id, [])]
                if len(found) != 1 or scopes.parents.get(scopes.statement_of(found[0])) is not module.tree:
                    return None
        if right_receiver[1] is not None and right_receiver[1] is not left_receiver[1]:
            return None  # One declaration per edge; RHS-only aliases are outside this route.
        if not (node is statement or node is target or node is value
                or node is edge.callee or node is edge.call or node is edge.primitive
                or edge.primitive_import is not None and node is edge.primitive_import.root
                or edge.operator_setitem is not None and node is edge.operator_setitem.root
                or edge.operator_ior is not None and node is edge.operator_ior.root
                or edge.direct_bare_setter is not None and node is key
                or edge.setter_import is not None and (node is key
                    or isinstance(edge.setter_import, _SourceSetterImport | _SourceSavedSetterImport)
                    and node is edge.setter_import.root)
                or node is left_receiver[1] or node is right_receiver[1]
                or projection is not None and (node is projection or node is left_name or node is key)
                or mapping is not None and node is mapping.receiver
                or node is self._source_slot_projection_metadata(projection)
                or isinstance(projection, ast.Call) and node is projection.func):
            return None
        left = self.resolver._constructor_reference(module, left_receiver[0], scopes)
        right = self.resolver._constructor_reference(module, right_receiver[0], scopes)
        namespace = left.get("retained_namespace")
        if not (isinstance(namespace, Path) and namespace == right.get("retained_namespace")
                and self.resolver.contains(namespace)):
            return None
        home = self._module(namespace)
        bindings = home.bindings.get(slot, [])
        if not (len(bindings) == 1 and bindings[0].top_level
                and isinstance(function := bindings[0].node, ast.FunctionDef)
                and function in home.tree.body and not function.decorator_list
                and not getattr(function, "type_params", [])):
            return None
        return home, function, statement

    @staticmethod
    def _operator_metadata_statement(module: PythonModule) -> ast.Assign | None:
        """Bound selection before the whole-tree confinement walk, with no grant."""
        found = None
        for node in module.tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(target := node.targets[0], ast.Attribute) and isinstance(target.ctx, ast.Store)
                    and target.attr in {"vars", "dict", "getattr", "setattr"}
                    and isinstance(target.value, ast.Name) and target.value.id == "operator"
                    and isinstance(node.value, ast.Constant) and node.value.value is None):
                if found is not None:
                    return None
                found = node
        return found

    def _operator_metadata_write(self, module: PythonModule, node: ast.AST | None) -> _OperatorMetadataWrite | None:
        """Pure exact write/import confinement; standard owner proof comes later."""
        scopes = self.scopes(module)
        if not (isinstance(node, ast.Assign) and scopes.parents.get(node) is module.tree
                and node in module.tree.body and len(node.targets) == 1
                and isinstance(target := node.targets[0], ast.Attribute) and isinstance(target.ctx, ast.Store)
                and scopes.parents.get(target) is node and target.attr in {"vars", "dict", "getattr", "setattr"}
                and isinstance(receiver := target.value, ast.Name) and isinstance(receiver.ctx, ast.Load)
                and receiver.id == "operator" and isinstance(node.value, ast.Constant) and node.value.value is None):
            return None
        rows = module.bindings.get(receiver.id, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if not (row.top_level and isinstance(imported := row.node, ast.alias)
                and imported.name == "operator" and imported.asname is None
                and isinstance(declaration := row.statement, ast.Import) and declaration.names == [imported]
                and scopes.parents.get(imported) is declaration and scopes.parents.get(declaration) is module.tree
                and declaration in module.tree.body and declaration.lineno < node.lineno
                and not scopes.enclosing_bindings(evaluation_site(scopes, receiver), receiver.id)
                and not module.bindings.get("__all__")):
            return None
        for use in ast.walk(module.tree):
            if isinstance(use, ast.Constant) and use.value == receiver.id:
                return None
            if isinstance(use, ast.Name) and use.id == receiver.id and use is not receiver:
                lexical = scopes.enclosing_bindings(evaluation_site(scopes, use), use.id)
                if not lexical or imported in lexical:
                    return None
        return _OperatorMetadataWrite(node, target, receiver, imported, declaration)

    def operator_metadata_write(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Route only this actual Store through explicit completed admission."""
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Store):
            return False
        statement = self.scopes(module).parents.get(node)
        metadata = self._operator_metadata_write(module, statement)
        if metadata is None or metadata.target is not node:
            return False
        candidates = [statement for statement in module.tree.body
                      if self._source_slot_candidate(module, statement) is not None]
        if len(candidates) != 1:
            return False
        return self.idempotent_source_slot(
            module, candidates[0], family, required_operator_metadata=(module, node),
        )

    def source_primitive_import(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Only a completed receipt for this exact qualified primitive Import."""
        if not (isinstance(node, ast.Import) and len(node.names) == 1
                and node.names[0].name == "builtins" and node.names[0].asname is None
                and self.scopes(module).parents.get(node) is module.tree):
            return False
        candidates = [statement for statement in module.tree.body
                      if isinstance(statement, ast.Assign | ast.Expr | ast.AugAssign)
                      and (edge := self._source_slot_edge(module, statement)) is not None
                      and edge.primitive_import is not None and edge.primitive_import.statement is node
                      and self._source_slot_candidate(module, statement) is not None]
        if len(candidates) != 1:
            return False
        return self.idempotent_source_slot(
            module, candidates[0], family, required_primitive_import=(module, node),
        )

    def source_setter_import(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Admit only this actual setter Import after its complete same-slot proof."""
        if not isinstance(node, ast.Import):
            return False
        candidates = [statement for statement in module.tree.body
                      if isinstance(statement, ast.Expr)
                      and (edge := self._source_slot_setter_edge(module, statement)) is not None
                      and edge.setter_import is not None and edge.setter_import.statement is node
                      and self._source_slot_candidate(module, statement) is not None]
        if len(candidates) != 1:
            return False
        return self.idempotent_source_slot(module, candidates[0], family, required_setter_import=(module, node))

    def source_from_setter_import(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """This actual from-import only, after its complete same-slot proof."""
        if not isinstance(node, ast.ImportFrom):
            return False
        candidates = [statement for statement in module.tree.body
                      if isinstance(statement, ast.Expr)
                      and (edge := self._source_slot_setter_edge(module, statement)) is not None
                      and isinstance(record := edge.setter_import, _SourceFromSetterImport)
                      and record.statement is node
                      and self._source_slot_candidate(module, statement) is not None]
        return len(candidates) == 1 and self.idempotent_source_slot(
            module, candidates[0], family, required_setter_import=(module, node),
        )

    def source_saved_vars_table_marker(self, module: PythonModule, key: str, line: int) -> bool:
        """Discharge one intrinsic marker origin; never remove an ordinary patch."""
        if (key != MODULE_TABLE_COMPUTED or self.resolver._checking_source_module_slot
                or self.resolver._checking_fresh_dictionary_primitive
                or module.attribute_patches.get(key) != line
                or self.resolver._runtime_attribute_patches(module).get(key) != line):
            return False
        producers: dict[str, set[ast.AST | None]] = {}
        if _attribute_patches(module.tree, patch_producers=producers) != module.attribute_patches:
            return False
        origins = producers.get(key, set())
        if len(origins) != 1:
            return False
        initializer = next(iter(origins))
        if not (isinstance(initializer, ast.Name) and isinstance(initializer.ctx, ast.Load)
                and initializer.id == "vars"):
            return False
        for family in ("agents", "google.adk"):
            try:
                if self.source_saved_vars_initializer(module, initializer, family):
                    return True
            except CallLimit:
                continue  # Each model must finish its own proof; partial evidence grants nothing.
        return False

    def source_saved_vars_initializer(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Only this actual getter initializer after its Store-only complete proof."""
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == "vars"):
            return False
        candidates = [statement for statement in module.tree.body if isinstance(statement, ast.Assign)
                      and (edge := self._source_slot_edge(module, statement)) is not None
                      and (mapping := edge.parts[5]) is not None and mapping.saved_getter is not None
                      and mapping.saved_getter.initializer is node
                      and self._source_slot_candidate(module, statement) is not None]
        return len(candidates) == 1 and self.idempotent_source_slot(module, candidates[0], family)

    def source_saved_bound_ior_initializer(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Only this actual saved method after its independent completed proof."""
        if not (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr == "__ior__"):
            return False
        if self.resolver._checking_source_module_slot or self.resolver._checking_fresh_dictionary_primitive:
            return False
        candidates = [statement for statement in module.tree.body if isinstance(statement, ast.Expr)
                      and (edge := self._source_slot_edge(module, statement)) is not None
                      and edge.saved_bound_ior is not None and edge.saved_bound_ior.initializer is node
                      and self._source_slot_candidate(module, statement) is not None]
        return len(candidates) == 1 and self.idempotent_source_slot(module, candidates[0], family)

    def source_bare_setter_initializer(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Only this actual bare initializer after its complete same-slot proof."""
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == "setattr"):
            return False
        candidates = [statement for statement in module.tree.body
                      if isinstance(statement, ast.Expr)
                      and (edge := self._source_slot_setter_edge(module, statement)) is not None
                      and edge.bare_setter is not None and edge.bare_setter.initializer is node
                      and self._source_slot_candidate(module, statement) is not None]
        return len(candidates) == 1 and self.idempotent_source_slot(module, candidates[0], family)

    def source_setter_initializer(self, module: PythonModule, node: ast.AST, family: str) -> bool:
        """Only this actual saved setter initializer after its own complete proof."""
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
            return False
        candidates = [statement for statement in module.tree.body
                      if isinstance(statement, ast.Expr)
                      and (edge := self._source_slot_setter_edge(module, statement)) is not None
                      and isinstance(record := edge.setter_import, _SourceSavedSetterImport)
                      and record.initializer is node
                      and self._source_slot_candidate(module, statement) is not None]
        return len(candidates) == 1 and self.idempotent_source_slot(module, candidates[0], family)

    def idempotent_source_slot(
        self, module: PythonModule, node: ast.AST, family: str,
        *, required_operator_metadata: tuple[PythonModule, ast.Attribute] | None = None,
        required_primitive_import: tuple[PythonModule, ast.Import] | None = None,
        required_setter_import: tuple[PythonModule, ast.Import | ast.ImportFrom] | None = None,
    ) -> bool:
        """Discharge one source declaration edge, never a runtime purity claim.

        The complete static context, raw mutation evidence and exact empty
        callable census are independent of receiving/constructor-owner proofs.
        No external module getter, copied namespace or ordinary cache is granted.
        """
        if family not in {"agents", "google.adk"}:
            return False
        statement = self._source_slot_statement(module, node)
        if not (isinstance(statement, ast.Assign | ast.Expr | ast.AugAssign)
                and self._source_slot_edge(module, statement) is not None):
            return False
        if (self._source_slot_edge(module, statement).saved_bound_ior is not None
                and self.resolver._checking_fresh_dictionary_primitive):
            return False
        if self.resolver._checking_source_module_slot:
            raise CallLimit("the source-module slot proof was reentered")
        self.resolver._checking_source_module_slot = True
        try:
            selected = self._source_slot_candidate(module, node)
            if selected is None:
                return False
            home, function, selected_statement = selected
            proof = BuilderCalls(self.resolver)
            context = proof.namespace_source_context(
                family, allow_operator_metadata=True, allow_operator_setitem=True, allow_operator_ior=True,
                allow_from_operator_ior=True,
            )
            if module not in context.modules or home not in context.modules:
                raise CallLimit("the source-module slot's actual trees are outside the captured context")
            from agents_shipgate.inputs.list_expressions import (
                _fresh_dictionary,
                _namespace_call_reference,
                _qualified_attribute_patches,
                _standard_operator,
                _unprovided_root,
                _View,
                bindings_at,
            )
            edges: dict[_SourceSlotStatement, _SourceSlotEdge] = {}
            namespace_values: set[ast.Name] = set()
            annotations: set[ast.Name] = set()
            projections: set[ast.Attribute | ast.Call] = set()
            projection_callees: set[ast.Name] = set()
            projection_keys: set[ast.Constant] = set()
            projection_metadata: set[ast.Constant] = set()
            mapping_receivers: set[ast.Name] = set()
            mapping_mutators: set[ast.Attribute] = set()
            saved_method_roles: set[ast.Attribute | ast.Name] = set()
            pending_method_markers: list[
                tuple[_View, str, ast.Attribute, ast.Attribute, frozenset[str], dict[str, frozenset[ast.AST | None]]]
            ] = []
            primitive_callees: set[ast.Name | ast.Attribute] = set()
            admitted_imports: set[tuple[int, ast.Import]] = set()
            admitted_setter_imports: set[tuple[int, ast.Import | ast.ImportFrom]] = set()
            setter_callees: set[ast.Attribute | ast.Name] = set()
            setter_import_aliases: set[ast.alias] = set()
            bare_setter_initializers: set[ast.Name] = set()
            saved_getter_initializers: set[ast.Name] = set()
            pending_getter_markers: list[tuple[PythonModule, ast.Name]] = []
            admitted_metadata: set[tuple[int, ast.Attribute]] = set()
            views: list[_View] = []
            machinery = {
                "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr",
                "type", "object", "ModuleType", "__class__", "__dict__", "__builtins__", "__globals__", "__closure__",
                "__getattr__", "__getattribute__", "__setattr__", "__delattr__", "__code__", "__defaults__",
                "__kwdefaults__", "__annotations__", "cell_contents", "_getframe", "currentframe", "f_globals", "f_locals",
                "modules", "meta_path", "path_hooks", "path", "__path__", "__loader__", "__spec__",
            }
            for current in context.modules:
                for item in ast.walk(current.tree):
                    if isinstance(item, ast.Assign | ast.Expr | ast.AugAssign) and (candidate := proof._source_slot_candidate(current, item)):
                        if candidate[0] is home and candidate[1] is function:
                            edge = proof._source_slot_edge(current, item)
                            assert edge is not None
                            edges[item] = edge
                            if (edge.callee is not None and edge.setter_import is None
                                    and edge.bare_setter is None and edge.direct_bare_setter is None
                                    and edge.saved_bound_ior is None
                                    and edge.operator_setitem is None and edge.operator_ior is None
                                    and edge.from_operator_ior is None):
                                mapping_mutators.add(edge.callee)
                            if len(edges) > MAX_CONTEXTS:
                                raise CallLimit(f"the source-module slot context exceeds {MAX_CONTEXTS} exact edges")
                            parts = edge.parts
                            if edge.setter_import is not None or edge.bare_setter is not None or edge.direct_bare_setter is not None:
                                namespace_values.add(parts[0])
                                projection_keys.add(parts[4])
                                for other in context.modules:
                                    if other is current:
                                        continue
                                    _, exported = proof._expanding_imports(
                                        other, {parts[0].id, parts[1].id}, _module_words(current.path), {current.path},
                                        subject=current.path, namespace_carriers=True,
                                    )
                                    if exported:
                                        raise CallLimit(f"{current.ref} has an imported setter source namespace")
                            if parts[3] is not None:
                                if current.bindings.get("__all__"):
                                    raise CallLimit(f"{current.ref} has an explicit export surface for the intrinsic source mapping")
                                actual_receivers = [proof._source_slot_receiver(current, name, item) for name in parts[:2]]
                                assert all(receiver is not None for receiver in actual_receivers)
                                import_names = {receiver[0].id for receiver in actual_receivers if receiver is not None}
                                if parts[5] is not None:
                                    import_names.add(parts[5].target.id)
                                    mapping_receivers.add(parts[5].receiver)
                                    if edge.saved_bound_ior is not None:
                                        import_names.add(edge.saved_bound_ior.target.id)
                                    if parts[5].saved_getter is not None:
                                        import_names.add(parts[5].saved_getter.target.id)
                                for other in context.modules:
                                    if other is current:
                                        continue
                                    _, exported = proof._expanding_imports(
                                        other, {parts[0].id, parts[1].id} | import_names, _module_words(current.path), {current.path},
                                        subject=current.path, namespace_carriers=True,
                                    )
                                    if exported:
                                        raise CallLimit(f"{current.ref} has an imported intrinsic source mapping namespace")
                                projections.add(parts[3])
                                if isinstance(parts[3], ast.Call):
                                    assert isinstance(parts[3].func, ast.Name)
                                    projection_callees.add(parts[3].func)
                                if parts[4] is not None:
                                    projection_keys.add(parts[4])
                                if (metadata := proof._source_slot_projection_metadata(parts[3])) is not None:
                                    projection_metadata.add(metadata)
                                namespace_values.add(parts[0])
                            for receiver in parts[:2]:
                                resolved = proof._source_slot_receiver(current, receiver, item)
                                assert resolved is not None
                                if resolved[1] is not None:
                                    namespace_values.add(resolved[1])
                                    alias_statement = proof.scopes(current).parents[resolved[1]]
                                    alias_target = (alias_statement.targets[0] if isinstance(alias_statement, ast.Assign)
                                                    else alias_statement.target)
                                    for other in context.modules:
                                        if other is current:
                                            continue
                                        _, exported = proof._expanding_imports(
                                            other, {alias_target.id}, _module_words(current.path), {current.path},
                                            subject=current.path, namespace_carriers=True,
                                        )
                                        if exported:
                                            raise CallLimit(f"{current.ref} has an imported source-slot namespace alias")
                                if resolved[2] is not None:
                                    annotations.add(resolved[2])
            for current in context.modules:
                current_scopes = proof.scopes(current)
                raw_markers = {marker for marker in current.attribute_patches
                               if marker in {PATH_PATCH, IMPORT_SEARCH_PATCH} or marker.startswith(("*", SELF_PATCH))}
                if raw_markers:
                    initializers = [mapping.saved_getter.initializer for statement, edge in edges.items()
                                    if current_scopes.parents.get(statement) is current.tree
                                    and (mapping := edge.parts[5]) is not None and mapping.saved_getter is not None]
                    raw_producers: dict[str, set[ast.AST | None]] = {}
                    replay = _attribute_patches(current.tree, patch_producers=raw_producers)
                    if not (raw_markers == {MODULE_TABLE_COMPUTED} and len(initializers) == 1
                            and replay == current.attribute_patches
                            and raw_producers.get(MODULE_TABLE_COMPUTED) == {initializers[0]}):
                        raise CallLimit(f"{current.ref} has raw package-path or module-namespace mutation markers")
                    pending_getter_markers.append((current, initializers[0]))
                    # This origin is only pending; no ordinary marker is removed and no proof is granted here.
                view = _View(current.ref, current.tree, current_scopes, current.bindings, current, set())
                view.lookup = bindings_at(current_scopes, current.bindings)
                view.resolver = self.resolver
                views.append(view)
                patch_producers: dict[str, set[ast.AST | None]] = {}
                patches = _qualified_attribute_patches(view, raw_namespaces=True, patch_producers=patch_producers)
                if view.namespace_builtins_changed:
                    raise CallLimit(f"{current.ref} has unstable namespace primitives in the source-module slot context")
                for statement, edge in edges.items():
                    mapping = edge.parts[5]
                    if mapping is not None and mapping.saved_getter is not None and current_scopes.parents.get(statement) is current.tree:
                        getter = mapping.saved_getter
                        if _namespace_call_reference(view, getter.projection) != "builtins.vars":
                            raise CallLimit(f"{current.ref}:{statement.lineno} has an unread saved getter primitive")
                        saved_getter_initializers.add(getter.initializer)
                    if edge.saved_bound_ior is not None and current_scopes.parents.get(statement) is current.tree:
                        saved = edge.saved_bound_ior
                        mapping = edge.parts[5]
                        assert mapping is not None and isinstance(mapping.projection, ast.Attribute)
                        marker = f"{mapping.namespace.id}.*"
                        origins = patch_producers.get(marker, set())
                        if not (marker in patches and origins in ({saved.initializer}, {saved.initializer, edge.rhs})
                                and proof._source_slot_candidate(current, statement) == (home, function, statement)):
                            raise CallLimit(f"{current.ref}:{statement.lineno} has unread saved method wildcard provenance")
                        # A source Function named update/clear can itself look like an exported
                        # mutator to the raw scanner. Only this exact original Function read is
                        # additionally pending; the zero census must complete its separate data role.
                        pending_method_markers.append((view, marker, saved.initializer, edge.rhs, frozenset(patches),
                                                       {key: frozenset(values) for key, values in patch_producers.items()}))
                        saved_method_roles.update({saved.initializer, saved.callee})
                    if edge.operator_setitem is not None and current_scopes.parents.get(statement) is current.tree:
                        record = edge.operator_setitem
                        proof._namespace_import_disk_root(current, record.statement, "operator setitem import")
                        if not (_standard_operator(view)
                                and proof._namespace_import_root_currency(current, record.statement, "operator setitem import")
                                and _namespace_call_reference(view, record.call) == "operator.setitem"):
                            raise CallLimit(f"{current.ref}:{statement.lineno} has an unread standard operator primitive")
                        for other in context.modules:
                            if other is current:
                                continue
                            _, exported = proof._expanding_imports(
                                other, {record.root.id}, _module_words(current.path), {current.path},
                                subject=current.path, namespace_carriers=True,
                            )
                            if exported:
                                raise CallLimit(f"{current.ref} has an imported operator primitive namespace")
                        primitive_callees.update({record.root, record.callee})
                    if edge.operator_ior is not None and current_scopes.parents.get(statement) is current.tree:
                        record = edge.operator_ior
                        proof._namespace_import_disk_root(current, record.statement, "operator ior import")
                        if not (_standard_operator(view)
                                and proof._namespace_import_root_currency(current, record.statement, "operator ior import")
                                and _namespace_call_reference(view, record.call) == "operator.ior"):
                            raise CallLimit(f"{current.ref}:{statement.lineno} has an unread standard operator primitive")
                        for other in context.modules:
                            if other is current:
                                continue
                            _, exported = proof._expanding_imports(
                                other, {record.root.id}, _module_words(current.path), {current.path},
                                subject=current.path, namespace_carriers=True,
                            )
                            if exported:
                                raise CallLimit(f"{current.ref} has an imported operator primitive namespace")
                        primitive_callees.update({record.root, record.callee})
                    if edge.from_operator_ior is not None and current_scopes.parents.get(statement) is current.tree:
                        record = edge.from_operator_ior
                        proof._namespace_import_disk_root(current, record.statement, "from operator ior import")
                        if not (_standard_operator(view)
                                and proof._namespace_import_root_currency(current, record.statement, "from operator ior import")
                                and _namespace_call_reference(view, record.call) == "operator.ior"):
                            raise CallLimit(f"{current.ref}:{statement.lineno} has an unread standard operator primitive")
                        for other in context.modules:
                            if other is current:
                                continue
                            _, exported = proof._expanding_imports(
                                other, {record.callee.id}, _module_words(current.path), {current.path},
                                subject=current.path, namespace_carriers=True,
                            )
                            if exported:
                                raise CallLimit(f"{current.ref} has an imported operator primitive namespace")
                        primitive_callees.add(record.callee)
                    if edge.direct_bare_setter is not None and current_scopes.parents.get(statement) is current.tree:
                        proof._namespace_import_disk_root(current, statement, "direct bare setter origin")
                        if not (_unprovided_root(view, "builtins")
                                and proof._namespace_import_root_currency(current, statement, "direct bare setter origin")
                                and edge.call is not None and _namespace_call_reference(view, edge.call) == "builtins.setattr"):
                            raise CallLimit(f"{current.ref}:{statement.lineno} has an unread direct bare setter origin")
                        setter_callees.add(edge.direct_bare_setter.callee)
                    if (edge.setter_import is not None or edge.bare_setter is not None) and current_scopes.parents.get(statement) is current.tree:
                        record = edge.setter_import or edge.bare_setter
                        assert record is not None
                        origin = record.assignment if isinstance(record, _SourceBareSetter) else record.statement
                        proof._namespace_import_disk_root(current, origin, "source setter origin")
                        if not _unprovided_root(view, "builtins") or not proof._namespace_import_root_currency(
                            current, origin, "source setter origin",
                        ):
                            raise CallLimit(f"{current.ref}:{statement.lineno} has an unread setter import root")
                        for other in context.modules:
                            if other is current:
                                continue
                            primitive_names = ({record.target.id} if isinstance(record, _SourceBareSetter)
                                               else {record.callee.id} if isinstance(record, _SourceFromSetterImport)
                                               else {record.root.id})
                            if isinstance(record, _SourceSavedSetterImport):
                                primitive_names.add(record.target.id)
                            _, exported = proof._expanding_imports(
                                other, primitive_names, _module_words(current.path), {current.path},
                                subject=current.path, namespace_carriers=True,
                            )
                            if exported:
                                raise CallLimit(f"{current.ref} has an imported setter builtin namespace")
                        if _namespace_call_reference(view, edge.call) != "builtins.setattr":
                            raise CallLimit(f"{current.ref}:{statement.lineno} has an unread setter primitive")
                        setter_callees.add(record.callee)
                        primitive_callees.add(record.callee)
                        if isinstance(record, _SourceBareSetter):
                            bare_setter_initializers.add(record.initializer)
                            primitive_callees.add(record.initializer)
                        elif isinstance(record, _SourceFromSetterImport):
                            setter_import_aliases.add(record.imported)
                        else:
                            primitive_callees.add(record.root)
                        if isinstance(record, _SourceSavedSetterImport):
                            setter_callees.add(record.initializer)
                            primitive_callees.add(record.initializer)
                        if not isinstance(record, _SourceBareSetter):
                            admitted_setter_imports.add((id(current), record.statement))
                    if edge.primitive is not None and current_scopes.parents.get(statement) is current.tree:
                        assert edge.call is not None
                        if (record := edge.primitive_import) is not None:
                            proof._namespace_import_disk_root(current, record.statement, "dictionary initialization import")
                            if not _unprovided_root(view, "builtins") or not proof._namespace_import_root_currency(
                                current, record.statement, "dictionary initialization import",
                            ):
                                raise CallLimit(f"{current.ref}:{statement.lineno} has an unread builtin import root")
                            for other in context.modules:
                                if other is current:
                                    continue
                                _, exported = proof._expanding_imports(
                                    other, {record.root.id}, _module_words(current.path), {current.path},
                                    subject=current.path, namespace_carriers=True,
                                )
                                if exported:
                                    raise CallLimit(f"{current.ref} has an imported dictionary primitive namespace")
                        expected_primitive = {"__init__": "builtins.dict.__init__", "update": "builtins.dict.update",
                                              "__setitem__": "builtins.dict.__setitem__"}[edge.callee.attr]
                        if _namespace_call_reference(view, edge.call) != expected_primitive:
                            raise CallLimit(f"{current.ref}:{statement.lineno} has an unread dictionary initialization primitive")
                        primitive_callees.add(edge.primitive)
                        if record is not None:
                            primitive_callees.add(record.root)
                            admitted_imports.add((id(current), record.statement))
                metadata_writes: dict[ast.Attribute, _OperatorMetadataWrite] = {}
                metadata = proof._operator_metadata_write(current, proof._operator_metadata_statement(current))
                if metadata is not None and _standard_operator(view):
                    for other in context.modules:
                        if other is current:
                            continue
                        _, exported = proof._expanding_imports(
                            other, {metadata.receiver.id}, _module_words(current.path), {current.path},
                            subject=current.path, namespace_carriers=True,
                        )
                        if exported:
                            raise CallLimit(f"{current.ref} has an imported operator metadata namespace")
                    if patch_producers.get(metadata.marker) == {metadata.target}:
                        metadata_writes[metadata.target] = metadata
                        admitted_metadata.add((id(current), metadata.target))
                discharged = {metadata.marker for metadata in metadata_writes.values()}
                for patch in patches:
                    if any(part in machinery for part in patch.split(".")) and patch not in discharged:
                        raise CallLimit(f"{current.ref} has module hook or import-table writes in the source-module slot context")
                for item in ast.walk(current.tree):
                    if item in metadata_writes:
                        continue  # Only this proved operator Store token, with exact unique raw producer evidence.
                    if item in annotations:
                        continue  # Only the whole, unshadowed builtin object annotation Name.
                    if item in projections:
                        continue  # Exact intrinsic source-module mapping; all raw markers remain checked.
                    if item in projection_callees:
                        continue  # Exact unshadowed getter; the independent raw primitive check already finished.
                    if item in projection_metadata:
                        continue  # Exact dictionary getter metadata, never a general reflective string exemption.
                    if item in saved_getter_initializers:
                        continue  # Only the actual bare getter initializer after canonical raw proof.
                    if item in bare_setter_initializers:
                        continue  # Only this actual unshadowed initializer after complete native/canonical proof.
                    if item in setter_import_aliases:
                        if item.asname in machinery:
                            raise CallLimit(f"{current.ref}:{item.lineno} has an unread setter alias spelling")
                        continue  # Only actual imported metadata after canonical proof; never a census value.
                    if item in setter_callees:
                        continue  # Only the exact setter Attribute after its independent canonical raw proof.
                    if isinstance(item, ast.ClassDef | ast.Lambda | ast.GeneratorExp):
                        raise CallLimit(f"{current.ref}:{item.lineno} has an unread class or anonymous carrier in the source-module slot context")
                    spellings = (
                        {item.id} if isinstance(item, ast.Name) else {item.attr} if isinstance(item, ast.Attribute)
                        else {item.name, item.asname or ""} if isinstance(item, ast.alias)
                        else {item.name} if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                        else {item.value} if isinstance(item, ast.Constant) and isinstance(item.value, str) else set()
                    )
                    if any(part in machinery for spelling in spellings for part in spelling.split(".")):
                        raise CallLimit(f"{current.ref}:{item.lineno} has reflective module machinery in the source-module slot context")
            if selected_statement not in edges:
                return False
            if (required_operator_metadata is not None
                    and (id(required_operator_metadata[0]), required_operator_metadata[1]) not in admitted_metadata):
                return False
            if (required_primitive_import is not None
                    and (id(required_primitive_import[0]), required_primitive_import[1]) not in admitted_imports):
                return False
            if (required_setter_import is not None
                    and (id(required_setter_import[0]), required_setter_import[1]) not in admitted_setter_imports):
                return False
            for view in views:
                assert view.module is not None
                for item in ast.walk(view.tree):
                    if isinstance(item, ast.Attribute) and isinstance(item.ctx, ast.Store | ast.Del) and item.attr == function.name:
                        parent = view.scopes.parents.get(item)
                        if parent in edges and item is edges[parent].target:
                            continue
                        owner = self.resolver._constructor_reference(view.module, item.value, view.scopes)
                        other = owner.get("retained_namespace")
                        if isinstance(other, Path) and other != home.path and self.resolver.contains(other):
                            continue
                        raise CallLimit(f"{view.ref}:{item.lineno} has another or unknown write to the selected source slot")
                    if isinstance(item, ast.Subscript) and isinstance(item.ctx, ast.Store | ast.Del):
                        parent = view.scopes.parents.get(item)
                        if parent in edges and item is edges[parent].target:
                            continue  # Only the selected, independently modeled same-slot store.
                        if isinstance(item.slice, ast.Constant) and item.slice.value != function.name:
                            continue
                        if not _fresh_dictionary(view, item.value):
                            raise CallLimit(f"{view.ref}:{item.lineno} has a keyed write with unread source-slot ownership")
            values = frozenset(edge.rhs for edge in edges.values())
            census = proof._census(home, function, allow_empty=True, source_slot_values=values,
                                   source_slot_namespaces=frozenset(namespace_values),
                                   source_slot_projections=frozenset(projections),
                                   source_slot_getters=frozenset(projection_callees | saved_getter_initializers),
                                   source_slot_keys=frozenset(projection_keys),
                                   source_slot_metadata=frozenset(projection_metadata),
                                   source_slot_mappings=frozenset(mapping_receivers),
                                   source_slot_mutators=frozenset(mapping_mutators),
                                   source_slot_saved_methods=frozenset(saved_method_roles),
                                   source_slot_primitives=frozenset(primitive_callees))
            if census.limits:
                raise CallLimit(census.limits[0])
            for current, initializer in pending_getter_markers:
                raw_producers: dict[str, set[ast.AST | None]] = {}
                replay = _attribute_patches(current.tree, patch_producers=raw_producers)
                if not (initializer in saved_getter_initializers and not census.sites
                        and replay == current.attribute_patches
                        and raw_producers.get(MODULE_TABLE_COMPUTED) == {initializer}):
                    raise CallLimit(f"{current.ref} has an undischarged saved getter namespace marker")
            for view, marker, initializer, rhs, expected_patches, expected_producers in pending_method_markers:
                producers: dict[str, set[ast.AST | None]] = {}
                replay = _qualified_attribute_patches(view, raw_namespaces=True, patch_producers=producers)
                if not (not census.sites and not view.namespace_builtins_changed
                        and replay == expected_patches
                        and {key: frozenset(values) for key, values in producers.items()} == expected_producers
                        and marker in replay and producers.get(marker) in ({initializer}, {initializer, rhs})
                        and initializer in saved_method_roles and rhs in values):
                    raise CallLimit(f"{view.ref} has an undischarged saved method wildcard")
            proof._namespace_directory_currency()
            return not census.sites
        except _Stop as stop:
            raise CallLimit(stop.detail) from None
        finally:
            self.resolver._checking_source_module_slot = False

    def uncalled_source_slot(
        self, module: PythonModule, function: Function, *, refused_models: list[str] | None = None,
    ) -> bool:
        """An independently completed zero-call fact for an exact self-slot.

        This does not change the ordinary caller cache or synthesize caller
        evidence. Each attempted import model completes its own static proof.
        """
        if not isinstance(function, ast.FunctionDef) or self.resolver._checking_source_module_slot:
            return False
        proof = BuilderCalls(self.resolver)
        work = 0
        for path in proof._candidates({function.name} | _module_words(module.path), module.path):
            caller = proof._module(path)
            for node in ast.walk(caller.tree):
                work += 1
                if work > MAX_NAMESPACE_CONTEXT_NODES:
                    raise CallLimit(f"the source-module slot candidate census exceeds {MAX_NAMESPACE_CONTEXT_NODES} syntax nodes")
                if not isinstance(node, ast.Assign | ast.Expr | ast.AugAssign):
                    continue
                candidate = proof._source_slot_candidate(caller, node)
                if candidate is None or candidate[0] is not module or candidate[1] is not function:
                    continue
                refused: list[str] = []
                for family in ("agents", "google.adk"):
                    try:
                        if proof.idempotent_source_slot(caller, node, family):
                            return True
                        detail = "the model did not establish empty source-slot ownership"
                    except CallLimit as exc:
                        detail = str(exc)  # Another model cannot inherit a partial proof.
                    refused.append(f"{family}: {caller.ref}:{node.lineno}: {module.ref}:{function.lineno} {function.name}: {detail}"[:1024])
                if refused_models is not None:
                    for detail in refused:
                        if detail not in refused_models and len(refused_models) < 2:
                            refused_models.append(detail)
                return False
        return False

    def _unused_namespaces(self, module: PythonModule) -> _UnusedNamespaceEdges:
        """Read a bare Import-backed namespace copied to an unused destination.

        This is a data-use proof only. Source/provider/import effects and every
        other namespace use remain subject to the normal constructor checks.
        """
        cached = self._unused_namespace_data.get(module.tree)
        if cached is not None:
            return cached
        nodes = list(ast.walk(module.tree))
        if len(nodes) > 20_000:
            return _UnusedNamespaceEdges(module.tree)
        scopes = self.scopes(module)
        canonical = {
            path.rpartition(".")[0]
            for family in ("agents", "google.adk")
            for path in (_external_constructor_paths(family) | _external_wrapper_paths(family)
                         | _external_decorator_paths(family))
        }
        values: set[ast.Name] = set()
        work = 0
        for statement in module.tree.body:
            if not (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(target := statement.targets[0], ast.Name)
                    and isinstance(value := statement.value, ast.Name)):
                continue
            destination = module.bindings.get(target.id, [])
            source = module.bindings.get(value.id, [])
            if not (len(destination) == 1 and destination[0].node is target
                    and len(source) == 1 and isinstance(alias := source[0].node, ast.alias)
                    and isinstance(imported := source[0].statement, ast.Import)
                    and imported in module.tree.body and imported.lineno < statement.lineno):
                continue
            work += len(nodes)
            if work > 20_000:
                break
            if any(
                isinstance(node, ast.Name) and node.id == target.id and node is not target
                or isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == target.id
                for node in nodes
            ):
                continue
            outcome = self.resolver._constructor_reference(module, value, scopes)
            namespace = outcome.get("retained_namespace")
            if isinstance(namespace, Path) and self.resolver.contains(namespace):
                self._module(namespace)  # Capture and parse the actual provider.
            elif outcome.get("external_constructor") in canonical:
                try:
                    if self.resolver._external_provider_issue(module, imported, alias) is not None:
                        continue
                except _Stop as exc:
                    raise CallLimit(exc.detail) from None
            else:
                continue  # Arbitrary missing imports are not namespace identities.
            words = _module_words(module.path)
            for path in self._candidates({target.id} | words, module.path):
                if path == module.path:
                    continue
                _, possible = self._expanding_imports(
                    self._module(path), {target.id}, words, {module.path},
                    subject=module.path, namespace_carriers=True,
                )
                if possible:
                    break
            else:
                values.add(value)
        result = _UnusedNamespaceEdges(module.tree, frozenset(values))
        self._unused_namespace_data[module.tree] = result
        return result

    def _confined_dictionary_data(self, module: PythonModule) -> _DictionaryDataEdges:
        """Prove confinement without asking whether any stored function is safe.

        Only literal dictionaries, module-level plain aliases and literal
        key Store/Del and exact literal initialization/update/ior writes are read. No retrieval,
        result, method handle, capture, namespace or export can borrow this proof. Other RHS obligations remain
        with the constructor reader and the ordinary import/dependency census.
        """
        cached = self._dictionary_data.get(module.tree)
        if cached is not None:
            return cached
        scopes = self.scopes(module)
        nodes = list(ast.walk(module.tree))
        empty = _DictionaryDataEdges(module.tree)
        if len(nodes) > 20_000:
            return empty
        def literal_mapping(value: ast.AST) -> tuple[tuple[ast.expr, ...], tuple[ast.Constant, ...]] | None:
            if not isinstance(value, ast.Dict) or not all(
                isinstance(key, ast.Constant) and isinstance(key.value, str)
                and not key.value.startswith("__") and isinstance(item, ast.Name | ast.Attribute)
                for key, item in zip(value.keys, value.values, strict=True)
            ):
                return None
            return tuple(value.values), tuple(key for key in value.keys if isinstance(key, ast.Constant))

        declarations: dict[str, ast.Assign] = {}
        augmented: set[ast.AugAssign] = set()
        for name, bindings in module.bindings.items():
            primary = [binding for binding in bindings if isinstance(binding.node, ast.Name)
                       and isinstance(binding.statement, ast.Assign)
                       and binding.statement.targets == [binding.node]
                       and binding.statement in module.tree.body]
            if len(primary) != 1:
                continue
            allocation = primary[0].statement
            operations: set[ast.AugAssign] = set()
            for binding in bindings:
                if binding is primary[0]:
                    continue
                statement = binding.statement
                if not (isinstance(binding.node, ast.Name) and isinstance(statement, ast.AugAssign)
                        and statement.target is binding.node and isinstance(statement.op, ast.BitOr)
                        and statement in module.tree.body and statement.lineno > allocation.lineno
                        and literal_mapping(statement.value) is not None):
                    break
                operations.add(statement)
            else:
                declarations[name] = allocation
                augmented.update(operations)
        values: set[ast.expr] = set()
        keys: set[ast.Constant] = set()
        work = 0
        for root, declaration in declarations.items():
            initial = literal_mapping(declaration.value)
            if initial is None:
                continue
            names = {root}
            for _ in range(128):
                work += len(declarations)
                if work > 20_000:
                    break
                added = {name for name, statement in declarations.items()
                         if isinstance(statement.value, ast.Name)
                         and statement.value.id in names
                         and statement.value.lineno > declarations[statement.value.id].lineno}
                if added <= names:
                    break
                names |= added
                if len(names) >= 128:
                    break
            else:
                continue
            if work > 20_000:
                break
            if len(names) >= 128:
                continue
            work += len(nodes)
            if work > 20_000:
                break
            own_values: set[ast.expr] = set(initial[0])
            own_keys: set[ast.Constant] = set(initial[1])
            confined = True
            for node in nodes:
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names:
                    confined = False  # Includes explicit/dynamic export spellings.
                    break
                if not isinstance(node, ast.Name) or node.id not in names:
                    continue
                statement = scopes.statement_of(node)
                if isinstance(node.ctx, ast.Store) and statement is declarations[node.id]:
                    continue
                if (isinstance(node.ctx, ast.Store) and isinstance(statement, ast.AugAssign)
                        and statement in augmented and statement.target is node):
                    mapping = literal_mapping(statement.value)
                    assert mapping is not None
                    own_values.update(mapping[0])
                    own_keys.update(mapping[1])
                    continue
                if not isinstance(node.ctx, ast.Load) or statement not in module.tree.body:
                    confined = False
                    break
                parent = scopes.parents.get(node)
                if (isinstance(parent, ast.Assign) and parent.value is node
                        and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name)
                        and parent.targets[0].id in names
                        and declarations[parent.targets[0].id] is parent):
                    continue
                if (isinstance(parent, ast.Subscript) and parent.value is node
                        and isinstance(parent.slice, ast.Constant)
                        and isinstance(parent.slice.value, str)
                        and not parent.slice.value.startswith("__")
                        and node.lineno > declarations[node.id].lineno):
                    if (isinstance(parent.ctx, ast.Store) and isinstance(statement, ast.Assign)
                            and statement.targets == [parent]
                            and isinstance(statement.value, ast.Name | ast.Attribute)):
                        own_values.add(statement.value)
                        own_keys.add(parent.slice)
                        continue
                    if isinstance(parent.ctx, ast.Del) and isinstance(statement, ast.Delete) and statement.targets == [parent]:
                        own_keys.add(parent.slice)
                        continue
                if (isinstance(parent, ast.Attribute) and parent.value is node and parent.attr in {"update", "__init__"}
                        and isinstance(call := scopes.parents.get(parent), ast.Call) and call.func is parent
                        and isinstance(statement, ast.Expr) and statement.value is call
                        and node.lineno > declarations[node.id].lineno):
                    if not call.args and all(
                        keyword.arg is not None and not keyword.arg.startswith("__")
                        and isinstance(keyword.value, ast.Name | ast.Attribute) for keyword in call.keywords
                    ):
                        own_values.update(keyword.value for keyword in call.keywords)
                        continue
                    if len(call.args) == 1 and not call.keywords and (mapping := literal_mapping(call.args[0])) is not None:
                        own_values.update(mapping[0])
                        own_keys.update(mapping[1])
                        continue
                confined = False
                break
            if not confined:
                continue
            # Complete the bounded import census before publishing any edges.
            words = _module_words(module.path)
            for path in self._candidates(names | words, module.path):
                if path == module.path:
                    continue
                _, possible = self._expanding_imports(
                    self._module(path), names, words, {module.path},
                    subject=module.path, namespace_carriers=True,
                )
                if possible:
                    confined = False
                    break
            if confined:
                values.update(own_values)
                keys.update(own_keys)
        result = _DictionaryDataEdges(module.tree, frozenset(values), frozenset(keys))
        self._dictionary_data[module.tree] = result
        return result

    def _implicit_child_binding(
        self, caller: PythonModule, statement: ast.Import | ast.ImportFrom,
        alias: ast.alias, child: PythonModule,
    ) -> bool:
        """An otherwise empty initializer repeats Python's own child binding."""
        if not (caller.path.name == "__init__.py" and child.path.parent == caller.path.parent
                and child.path.name == alias.name + ".py"
                and isinstance(statement, ast.ImportFrom) and statement.level == 1
                and statement.module is None and statement.names == [alias]
                and alias.asname is None and alias.name.isidentifier()):
            return False
        body = caller.tree.body
        if not (len(body) == 1 and body[0] is statement or len(body) == 2 and body[1] is statement
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            return False
        if caller.text != self._text(caller.path) or child.text != self._text(child.path):
            raise CallLimit(f"{caller.ref} or {child.ref} differs from its consumed own-child source")
        try:
            package = self.resolver._from_base(caller, statement)
            located = self.resolver._locate(package.directory, [alias.name], spelling=alias.name)
        except _Stop:
            return False  # The ordinary export path retains the unresolved import.
        return (package.module_path == caller.path and located is not None
                and located.module_path == child.path and self._module(child.path) is child)

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
        implicit_child = False
        routes = self._export_routes[id(call)] = set()
        if names:
            try:
                for path in self._candidates(_module_words(module.path), module.path):
                    caller = self._module(path)
                    if caller.path == module.path:
                        continue
                    excluded = self.runtime_exclusions(caller)
                    for statement in ast.walk(caller.tree):
                        if not isinstance(statement, ast.Import | ast.ImportFrom) or id(statement) in excluded:
                            continue
                        for alias in statement.names:
                            own_child = self._implicit_child_binding(caller, statement, alias, module)
                            implicit_child |= own_child
                            if not own_child and self._retained_namespace(caller, statement, alias, {module.path}):
                                exported = True
                                routes.add("namespace")
                                self._export_limits.setdefault(
                                    id(call), f"an import at {caller.ref}:{statement.lineno} "
                                    f"retains the constructed value from {module.ref}:{call.lineno} "
                                    f"through {alias.asname or alias.name!r}",
                                )
                            if isinstance(statement, ast.ImportFrom) and alias.name in names:
                                try:
                                    owner = self.resolver._from_base(caller, statement)
                                except _Stop:
                                    owner = None
                                if owner is not None and owner.module_path == module.path:
                                    # A self-wrapped/rebound member can resolve
                                    # to no single value while still exporting
                                    # this actual retained result. Its identity
                                    # uncertainty cannot prove absence of export.
                                    exported = True
                                    routes.add("value_import")
                                    self._export_limits.setdefault(
                                        id(call), f"an import at {caller.ref}:{statement.lineno} "
                                        f"retains the constructed value from {module.ref}:{call.lineno} "
                                        f"through {alias.asname or alias.name!r}",
                                    )
                            references = (
                                [alias.asname or alias.name]
                                if isinstance(statement, ast.ImportFrom)
                                else [f"{alias.asname or alias.name}.{name}" for name in names]
                            )
                            if alias.name == "*":
                                exported = True
                                routes.add("wildcard")
                                self._export_limits.setdefault(
                                    id(call), f"the wildcard import at {caller.ref}:{statement.lineno} "
                                    f"leaves export of the constructed value at {module.ref}:{call.lineno} unread",
                                )
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
                                    routes.add("value_import" if isinstance(statement, ast.ImportFrom) else "module_reference")
                                    self._export_limits.setdefault(
                                        id(call), f"an import at {caller.ref}:{statement.lineno} "
                                        f"retains the constructed value from {module.ref}:{call.lineno} "
                                        f"through {reference!r}",
                                    )
                if implicit_child:
                    self._namespace_directory_currency()
            except CallLimit as exc:
                self._export_limits.setdefault(id(call), str(exc))
                exported = True
                routes.add("unread")
        if exported or not implicit_child:
            self._exports[id(call)] = exported
        return exported

    def export_routes(self, call: ast.expr) -> frozenset[str]:
        """How the export census found the value leave its module; empty if it did not run."""
        return frozenset(self._export_routes.get(id(call), ()))

    def export_limit(self, call: ast.expr) -> str | None:
        """Preserve the first concrete import route or census failure beside its refusal."""
        return self._export_limits.get(id(call))

    def borrowers(self, module: PythonModule, name: str, *, namespace_carriers: bool = False) -> tuple[PythonModule, ...]:
        """Candidate importers of a shared module value, including re-exports.

        Membership still belongs to ListExpressions. It checks each candidate
        with the existing import-identity and mutation tests. Failure to finish
        this bounded census never establishes a borrowed list's contents.
        """
        key = (id(module.tree), name, namespace_carriers)
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
                    caller, names, words, related, subject=module.path, namespace_carriers=namespace_carriers
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
        self, module: PythonModule, name: str, *, namespace_carriers: bool = False
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        self.borrowers(module, name, namespace_carriers=namespace_carriers)
        key = (id(module.tree), name, namespace_carriers)
        return self._borrower_aliases[key], self._borrower_words[key]

    def retaining_modules(self, module: PythonModule, name: str, *, namespace_carriers: bool = False) -> frozenset[Path]:
        """Modules whose globals can retain a subject through imports/re-exports."""
        self.borrowers(module, name, namespace_carriers=namespace_carriers)
        return self._borrower_paths[id(module.tree), name, namespace_carriers]

    def import_may_share(
        self,
        caller: PythonModule,
        defining: PythonModule,
        name: str,
        statement: ast.Import | ast.ImportFrom,
        alias: ast.alias,
        *, namespace_carriers: bool = False,
    ) -> bool:
        """Whether one import may retain the subject list or its namespace."""
        names, words = self.borrower_spellings(defining, name, namespace_carriers=namespace_carriers)
        key = (id(defining.tree), name, namespace_carriers)
        _, possible = self._expanding_imports(
            caller,
            set(names),
            set(words),
            set(self._borrower_paths[key]),
            (statement,),
            subject=defining.path,
            selected_alias=alias,
            namespace_carriers=namespace_carriers,
        )
        return possible

    def _census(
        self, module: PythonModule, function: Function, *, allow_empty: bool = False,
        confined_dictionary_data: bool = False,
        unused_namespace_data: bool = False,
        confined_primitive_dictionary_values: frozenset[ast.expr] = frozenset(),
        absent_field_roles: _AbsentFieldDictionaryRoles | None = None,
        source_slot_values: frozenset[ast.expr] = frozenset(),
        source_slot_namespaces: frozenset[ast.Name] = frozenset(),
        source_slot_mappings: frozenset[ast.Name] = frozenset(),
        source_slot_mutators: frozenset[ast.Attribute] = frozenset(),
        source_slot_saved_methods: frozenset[ast.Attribute | ast.Name] = frozenset(),
        source_slot_primitives: frozenset[ast.Name | ast.Attribute] = frozenset(),
        source_slot_projections: frozenset[ast.Attribute | ast.Call] = frozenset(),
        source_slot_getters: frozenset[ast.Name] = frozenset(),
        source_slot_keys: frozenset[ast.Constant] = frozenset(),
        source_slot_metadata: frozenset[ast.Constant] = frozenset(),
    ) -> CallerCensus:
        if absent_field_roles is not None and not (
            absent_field_roles.proof is self and absent_field_roles.home is module
            and absent_field_roles.function is function and self._fresh_receipt_active
            and not self._fresh_receipt_finished and self._fresh_identity_session is not None
        ):
            raise CallLimit("the absent-field roles do not belong to this active exact census")
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
            data = self._confined_dictionary_data(caller) if confined_dictionary_data else _DictionaryDataEdges(caller.tree)
            namespaces = self._unused_namespaces(caller) if unused_namespace_data else _UnusedNamespaceEdges(caller.tree)
            exports = _export_strings(caller.tree)
            dynamic = self.dynamic_importer(caller)
            if dynamic is not None:
                limits.append(
                    f"{where} may be reached through dynamic import machinery at {caller.ref}:{dynamic.lineno}"
                )
            excluded = self.runtime_exclusions(caller)
            reflection = reflective_access(caller.tree, excluded=excluded)
            if caller.path in related and reflection is not None:
                limits.append(
                    f"{where} may be reached through reflection at {caller.ref}:{reflection.lineno}"
                )
            for node in ast.walk(caller.tree):
                if id(node) in excluded:
                    continue
                if absent_field_roles is not None and (
                    node is absent_field_roles.value or node in absent_field_roles.namespace_nodes
                    or node is absent_field_roles.getter_metadata
                    or node is absent_field_roles.intrinsic_projection
                ):
                    continue  # Only exact nodes from the independent unread-field receipt.
                if node in confined_primitive_dictionary_values:
                    continue  # Only exact Function-data values from a live private primitive receipt.
                if node in source_slot_values:
                    continue  # This exact RHS has its own actual identity and no-change proof.
                if node in source_slot_namespaces:
                    continue  # Independently bounded namespace node, never Function evidence.
                if node in source_slot_mappings:
                    continue  # Exact confined saved dictionary receiver, never Function evidence.
                if node in source_slot_mutators:
                    continue  # Exact modeled dictionary method; its Call and descendants remain visited.
                if node in source_slot_saved_methods:
                    continue  # Only exact saved method roles; Call and literal descendants remain visited.
                if node in source_slot_primitives:
                    continue  # Exact raw-proved builtin callee, separate from the selected Function.
                if node in source_slot_projections:
                    continue  # Only an independently modeled intrinsic source mapping.
                if node in source_slot_getters:
                    continue  # Exact canonical getter callee, never Function evidence.
                if node in source_slot_keys:
                    continue  # Exact same-slot literal key, never a general string exemption.
                if node in source_slot_metadata:
                    continue  # Exact intrinsic getter metadata, distinct from Function and slot-key evidence.
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
                        if not (isinstance(parent, ast.Attribute) and parent.attr in names) and node not in namespaces.values:
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
                                    if self._retained_namespace(caller, statement, alias, related) and node not in namespaces.values:
                                        limits.append(
                                            f"{where}'s retained namespace is used through computed access or as a value at {caller.ref}:{node.lineno}"
                                        )
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in names
                ):
                    if id(node) not in exports and node not in data.keys:
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
                    elif node not in data.values:
                        limits.append(f"{where} is used as a value at {caller.ref}:{node.lineno}")
                elif not (self._elsewhere(caller, node, resolution) or self._agent_instance_attribute(caller, node)):
                    limits.append(
                        f"{where} has an unresolved reference at {caller.ref}:{node.lineno}: {resolution.detail}"
                    )
        if not sites and not allow_empty:
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
        namespace_carriers: bool = False,
    ) -> tuple[set[str], bool]:
        """Expand aliases through related imports, not matching module basenames.

        Textual candidates are still inspected. Only their import identity can
        widen the next round. An unresolved local import stays a candidate;
        an established import into a different module cannot re-export this
        subject merely because its schema module has the same filename.
        """
        aliases: set[str] = set()
        possible = False
        excluded = self.runtime_exclusions(caller)
        for statement in statements if statements is not None else ast.walk(caller.tree):
            if not isinstance(statement, ast.Import | ast.ImportFrom) or id(statement) in excluded:
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
                        relevant = namespace_carriers and container.module_path in related
                        if alias.name != "*":
                            child = self.resolver._locate(
                                container.directory, alias.name.split("."), spelling=alias.name
                            )
                            relevant |= child is not None and child.module_path in related
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
                                and (namespace_carriers or (resolution.module.path == subject and resolution.definition.name in names))
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
        if (isinstance(statement, ast.Import) and alias.name in {"agents", "openai_agents"}
                and self.resolver._external_provider_issue(caller, statement, alias) is None):
            # Canonical external SDK roots cannot borrow an application's
            # namespace-only directory of the same name. Concrete local
            # providers and imports of actual children keep the ordinary proof.
            return False
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

    def _agent_instance_attribute(self, module: PythonModule, node: ast.expr) -> bool:
        """``assistant.run`` where ``assistant = Agent(...)`` is bound once.

        An attribute of an instance of the framework's own class is not this
        module's function of the same name: reaching the function through it
        needs the function stored as a value, and that is a limit of its own.
        The class is the exact import the constructor census resolves, so a
        local ``Agent`` or any other constructor does not qualify.
        """
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and isinstance(node.ctx, ast.Load) and isinstance(node.value.ctx, ast.Load)):
            return False
        scopes = self.scopes(module)
        found = scopes.enclosing_bindings(evaluation_site(scopes, node), node.value.id)
        if len(found) != 1 or not isinstance(found[0], ast.Name):
            return False
        statement = scopes.statement_of(found[0])
        if not (isinstance(statement, ast.Assign) and statement.targets == [found[0]]
                and isinstance(statement.value, ast.Call)):
            return False
        canonical = self.resolver._constructor_reference(module, statement.value.func, scopes).get("external_constructor")
        return isinstance(canonical, str) and canonical in (
            _external_constructor_paths("agents") | _external_constructor_paths("openai_agents")
            | _external_constructor_paths("google.adk")
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
            module = self.resolver._patch_scan(path)
            if self._fresh_identity_session is not None and module.text != self._text(path):
                raise CallLimit(f"{module.ref} differs from its fresh identity-bound source")
            return module
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

    def _namespace_directory_entries(self, directory: Path) -> frozenset[str] | None:
        """Capture each directory once; every import uses the same evidence."""
        layout = self.resolver._layout
        assert layout is not None and layout.disk_root is not None
        snapshot = active_static_input_snapshot()
        if snapshot is not None and (snapshot.excludes(directory)
                                    or directory != snapshot.root and not snapshot.contains(directory)):
            raise CallLimit(f"{directory} above the read scope is outside the bound input snapshot")
        if directory in self._namespace_directories:
            return self._namespace_directories[directory]
        if directory.is_symlink():
            raise CallLimit(f"{directory} above the read scope is a linked import root")
        if not directory.exists():
            parent = self._namespace_directory_entries(directory.parent)
            folded = directory.name.casefold()
            if parent is None or any(name.casefold() == folded for name in parent):
                # Absence is read from the exact listing under any letter case,
                # never from whether this host's filesystem finds the name.
                raise CallLimit(f"{directory} above the read scope has unread directory evidence")
            if snapshot is not None and not snapshot.bind_dependency_absence(directory):
                raise CallLimit(f"{directory} above the read scope is not absent")
            self._namespace_directories[directory] = None
            return None
        if not directory.is_dir():
            raise CallLimit(f"{directory} above the read scope is not a directory")
        current = frozenset(child.name for child in list_input_directory(directory))
        if self._fresh_identity_session is not None:
            live = frozenset(self._fresh_identity_session.directory_entries(directory.relative_to(self._fresh_identity_session.root)))
            if live != current:
                raise CallLimit(f"{directory} differs from its live fresh import-root inventory")
        self._namespace_entry_work += len(current)
        if self._namespace_entry_work > 100000:
            raise CallLimit("the namespace ancestry census exceeds 100000 directory entries")
        ref = directory.relative_to(layout.disk_root).as_posix()
        cached = layout.entries("" if ref == "." else ref)
        if cached is None or cached != current:
            raise CallLimit(f"{directory} above the read scope has unread or changed directory evidence")
        self._namespace_directories[directory] = current
        names_by_fold: dict[str, set[str]] = {}
        for name in current:
            names_by_fold.setdefault(name.casefold(), set()).add(name)
        self._namespace_directory_names[directory] = {
            folded: frozenset(names) for folded, names in names_by_fold.items()
        }
        return current

    def _namespace_directory_currency(self) -> None:
        """Reconfirm consumed source bytes, inventory topology and ancestry."""
        try:
            for directory, captured in self._inventory_directories.items():
                if directory.is_symlink() or not directory.is_dir():
                    raise CallLimit(f"{directory} changed after its caller inventory was read")
                children = list_input_directory(directory)
                if frozenset(child.name for child in children) != frozenset(name for name, _kind in captured):
                    raise CallLimit(f"{directory} changed after its caller inventory was read")
                # Only the unchanged population bounded by the original census
                # can require kind reads; reject added names before any lstat.
                current = frozenset((child.name, stat.S_IFMT(child.lstat().st_mode)) for child in children)
                if current != captured:
                    raise CallLimit(f"{directory} changed after its caller inventory was read")
            for marker, captured_selector in self._inventory_venv_selectors.items():
                present = _lists_entry(marker)
                current_selector = (present, stat.S_IFMT(marker.lstat().st_mode) if present else None)
                if current_selector != captured_selector:
                    raise CallLimit(f"{marker} changed its caller inventory virtual-environment selector")
            # Reconfirm only bytes already consumed by this proof. A quiet
            # source can become a caller without changing directory topology.
            sizes = {path: len(text.encode("utf-8")) for path, text in self._texts.items()}
            if sum(sizes.values()) > MAX_TOTAL_BYTES:
                raise CallLimit(f"the caller census exceeds {MAX_TOTAL_BYTES} source bytes")
            for path, captured_text in self._texts.items():
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != sizes[path]:
                    raise CallLimit(f"{self.resolver.ref(path)} changed after its namespace source bytes were read")
                if load_text_file(path) != captured_text:
                    raise CallLimit(f"{self.resolver.ref(path)} changed after its namespace source bytes were read")
            for directory, captured in self._namespace_directories.items():
                if directory.is_symlink() or (captured is None and directory.exists()):
                    raise CallLimit(f"{directory} changed after its namespace directory evidence was read")
                if captured is not None and (
                    not directory.is_dir()
                    or frozenset(child.name for child in list_input_directory(directory)) != captured
                ):
                    raise CallLimit(f"{directory} changed after its namespace directory evidence was read")
        except (InputParseError, OSError, ValueError) as exc:
            raise CallLimit(f"the namespace directory evidence could not be reconfirmed: {exc}") from None

    def _namespace_scope_absence(self) -> None:
        """Refuse selected scopes without bound, readable ancestor absence.

        This proves no getter behavior. Any actual initializer above the scope
        remains outside this first source-context increment, even if readable.
        """
        layout = self.resolver._layout
        if layout is None or not layout.scope:
            return
        if layout.disk_root is None:
            raise CallLimit("the namespace source context has no bound disk ancestry above the read scope")
        root = layout.disk_root
        if root / layout.scope != self.resolver.scope_root:
            raise CallLimit("the namespace source context's disk ancestry does not match the read scope")
        snapshot = active_static_input_snapshot()
        parts = layout.scope.split("/")
        for length in range(len(parts)):
            ref = "/".join(parts[:length])
            directory = root / ref
            if snapshot is not None and (snapshot.excludes(directory)
                                        or directory != snapshot.root and not snapshot.contains(directory)):
                raise CallLimit(f"{directory} above the read scope is outside the bound input snapshot")
            try:
                current = self._namespace_directory_entries(directory)
                if current is None:
                    raise CallLimit(f"{directory} above the read scope is not a readable plain directory")
                initializers = sorted(name for name in current if name.casefold() == "__init__.py")
                if initializers:
                    raise CallLimit(
                        f"the namespace source context for {layout.scope} has initializer candidates "
                        f"above the read scope: {directory}/{' or '.join(initializers)}"
                    )
                marker = directory / "__init__.py"
                if snapshot is not None and not snapshot.bind_dependency_absence(marker):
                    raise CallLimit(f"{marker} above the namespace read scope is not absent")
            except (InputParseError, OSError, ValueError) as exc:
                raise CallLimit(f"{directory} above the read scope could not be inspected: {exc}") from None

    def _namespace_operator_metadata_import(self, module: PythonModule, statement: ast.Import, imported: ast.alias) -> bool:
        """A confined metadata-only import boundary, with captured root currency."""
        if imported.name != "operator" or imported.asname is not None or statement.names != [imported]:
            return False
        metadata = self._operator_metadata_write(module, self._operator_metadata_statement(module))
        if not (metadata is not None and metadata.declaration is statement and metadata.imported is imported):
            return False
        self._namespace_import_disk_root(module, statement, "operator metadata import")
        from agents_shipgate.inputs.list_expressions import _standard_operator, _View, bindings_at

        view = _View(module.ref, module.tree, self.scopes(module), module.bindings, module, set())
        view.lookup = bindings_at(view.scopes, view.bindings)
        view.resolver = self.resolver
        if not _standard_operator(view):
            return False
        return self._namespace_import_root_currency(module, statement, "operator metadata import")

    def _namespace_operator_setitem_import(self, module: PythonModule, statement: ast.Import, imported: ast.alias) -> bool:
        """Confined import boundary only; the independent slot proof still owes every role."""
        if imported.name != "operator" or imported.asname is not None or statement.names != [imported]:
            return False
        if len(module.tree.body) > MAX_NAMESPACE_CONTEXT_NODES:
            return False
        records = [record for node in module.tree.body
                   if (record := self._source_operator_setitem(module, node)) is not None
                   and record.statement is statement and record.imported is imported]
        if len(records) != 1:
            return False
        self._namespace_import_disk_root(module, statement, "operator setitem import")
        from agents_shipgate.inputs.list_expressions import _standard_operator, _View, bindings_at

        view = _View(module.ref, module.tree, self.scopes(module), module.bindings, module, set())
        view.lookup = bindings_at(view.scopes, view.bindings)
        view.resolver = self.resolver
        return _standard_operator(view) and self._namespace_import_root_currency(module, statement, "operator setitem import")

    def _namespace_operator_ior_import(self, module: PythonModule, statement: ast.Import, imported: ast.alias) -> bool:
        """Confined import boundary only; the independent slot proof still owes every role."""
        if imported.name != "operator" or imported.asname is not None or statement.names != [imported]:
            return False
        if len(module.tree.body) > MAX_NAMESPACE_CONTEXT_NODES:
            return False
        records = [record for node in module.tree.body
                   if (record := self._source_operator_ior(module, node)) is not None
                   and record.statement is statement and record.imported is imported]
        if len(records) != 1:
            return False
        self._namespace_import_disk_root(module, statement, "operator ior import")
        from agents_shipgate.inputs.list_expressions import _standard_operator, _View, bindings_at

        view = _View(module.ref, module.tree, self.scopes(module), module.bindings, module, set())
        view.lookup = bindings_at(view.scopes, view.bindings)
        view.resolver = self.resolver
        return _standard_operator(view) and self._namespace_import_root_currency(module, statement, "operator ior import")

    def _namespace_from_operator_ior_import(self, module: PythonModule, statement: ast.ImportFrom, imported: ast.alias) -> bool:
        """Confined import boundary only; the independent slot proof still owes every role."""
        if (statement.module != "operator" or statement.level or imported.name != "ior"
                or imported.asname is None or statement.names != [imported]):
            return False
        if len(module.tree.body) > MAX_NAMESPACE_CONTEXT_NODES:
            return False
        names = self._source_from_operator_token_index(module)[0].get(imported.asname, frozenset())
        if len(names) != 1:
            return False
        callee = next(iter(names))
        scopes = self.scopes(module)
        call = scopes.parents.get(callee)
        terminal = scopes.parents.get(call)
        if not (isinstance(call, ast.Call) and call.func is callee
                and isinstance(terminal, ast.Expr) and terminal.value is call
                and (record := self._source_from_operator_ior(module, terminal)) is not None
                and record.statement is statement and record.imported is imported):
            return False
        self._namespace_import_disk_root(module, statement, "from operator ior import")
        from agents_shipgate.inputs.list_expressions import _standard_operator, _View, bindings_at

        view = _View(module.ref, module.tree, self.scopes(module), module.bindings, module, set())
        view.lookup = bindings_at(view.scopes, view.bindings)
        view.resolver = self.resolver
        return _standard_operator(view) and self._namespace_import_root_currency(module, statement, "from operator ior import")

    def _namespace_import_disk_root(self, module: PythonModule, statement: ast.Import | ast.ImportFrom | ast.Assign | ast.Expr, role: str) -> Path:
        """Bound disk provenance only, without granting any import or assignment its role."""
        layout = self.resolver._layout
        if layout is None or layout.disk_root is None:
            raise CallLimit(f"{module.ref}:{statement.lineno} has no bound import-root currency for the {role}")
        if layout.disk_root / layout.scope != self.resolver.scope_root:
            raise CallLimit(f"{module.ref}:{statement.lineno} has disk ancestry that does not match the read scope")
        return layout.disk_root

    def _namespace_import_root_currency(self, module: PythonModule, statement: ast.Import | ast.ImportFrom | ast.Assign | ast.Expr, role: str) -> bool:
        """Capture the same root prefixes; provider identity remains separate."""
        root = self._namespace_import_disk_root(module, statement, role)
        layout = self.resolver._layout
        assert layout is not None
        for search in self.resolver._import_roots():
            prefixes = [""] + ["/".join(search.split("/")[:i]) for i in range(1, len(search.split("/")) + 1)] if search else [""]
            previous: frozenset[str] | None = None
            for prefix in prefixes:
                captured = self._namespace_directory_entries(root / prefix)
                if prefix and previous is not None and prefix.rsplit("/", 1)[-1] not in previous:
                    if captured is not None:
                        return False
                    break  # The same parent listing and missing prefix used by the raw standard-owner proof.
                expected = layout.entries(prefix)
                if expected is None or captured is None or captured != frozenset(expected):
                    return False
                previous = captured
        return True

    def namespace_source_context(self, family: str, *, allow_operator_metadata: bool = False,
                                 allow_operator_setitem: bool = False,
                                 allow_operator_ior: bool = False,
                                 allow_from_operator_ior: bool = False) -> NamespaceSourceContext:
        """Read a small complete production census and its actual import closure.

        The inventory excludes tests, hidden directories and virtual environments;
        actual imports into those locations still expand the closure. This first
        increment refuses unbound ancestry, above-scope inputs and all unmodeled
        external imports.
        Nothing here calls a receiving/ownership proof or caches a semantic grant.
        """
        self._namespace_entry_work = 0
        self._namespace_directories: dict[Path, frozenset[str] | None] = {}
        self._namespace_directory_names: dict[Path, dict[str, frozenset[str]]] = {}
        self._namespace_scope_absence()
        pending = list(self._inventory())
        seen = set(pending)
        modules: list[PythonModule] = []
        boundaries: list[str] = []
        nodes = 0
        symbols = _external_constructor_paths(family) | _external_decorator_paths(family)
        namespaces = {symbol.rsplit(".", 1)[0] for symbol in _external_constructor_paths(family)}

        def append(path: Path) -> None:
            if not path.is_relative_to(self.resolver.scope_root):
                raise CallLimit(f"{path} is above the namespace source context's read scope")
            if path not in seen:
                seen.add(path)
                pending.append(path)
            if len(seen) > MAX_CANDIDATES:
                raise CallLimit(f"the namespace source context exceeds {MAX_CANDIDATES} total modules")

        if len(seen) > MAX_CANDIDATES:
            raise CallLimit(f"the namespace source context exceeds {MAX_CANDIDATES} total modules")
        try:
            for path in pending:
                text = self._text(path)  # Account captured bytes before parsing, including expanded imports.
                module = self._module(path)
                if module.text != text:
                    raise CallLimit(f"{module.ref} changed after its parsed namespace source was read")
                modules.append(module)
                for _node in ast.walk(module.tree):
                    nodes += 1
                    if nodes > MAX_NAMESPACE_CONTEXT_NODES:
                        raise CallLimit(
                            f"the namespace source context exceeds {MAX_NAMESPACE_CONTEXT_NODES} syntax nodes"
                        )
                dynamic = self.dynamic_importer(module)
                if dynamic is not None:
                    raise CallLimit(
                        f"{module.ref}:{dynamic.lineno} has unread dynamic import machinery "
                        "in the namespace source context"
                    )
                for node in ast.walk(module.tree):
                    if not isinstance(node, ast.Import | ast.ImportFrom):
                        continue
                    self._namespace_above_import(module, node)
                    if any(alias.name == "*" for alias in node.names):
                        raise CallLimit(f"{module.ref}:{node.lineno} has an unread wildcard namespace import")
                    if isinstance(node, ast.Import):
                        # Every actual alias has its own read obligation. A
                        # readable sibling cannot stand in for a namespace
                        # portion or an absent canonical external provider.
                        for alias in node.names:
                            root = alias.name.split(".", 1)[0]
                            if root in _PRELOADED:
                                boundaries.append(f"{module.ref}:{node.lineno}: preloaded {root}")
                                continue
                            try:
                                containers = self.resolver._absolute_candidates(module, alias.name)
                            except _Stop as stop:
                                if (stop.reason == MODULE_NOT_FOUND and allow_operator_metadata
                                        and self._namespace_operator_metadata_import(module, node, alias)):
                                    boundaries.append(f"{module.ref}:{node.lineno}: confined operator metadata import")
                                    continue
                                if (stop.reason == MODULE_NOT_FOUND and allow_operator_setitem
                                        and self._namespace_operator_setitem_import(module, node, alias)):
                                    boundaries.append(f"{module.ref}:{node.lineno}: confined operator setitem import")
                                    continue
                                if (stop.reason == MODULE_NOT_FOUND and allow_operator_ior
                                        and self._namespace_operator_ior_import(module, node, alias)):
                                    boundaries.append(f"{module.ref}:{node.lineno}: confined operator ior import")
                                    continue
                                if stop.reason != MODULE_NOT_FOUND or alias.name not in namespaces:
                                    raise CallLimit(f"{module.ref}:{node.lineno}: {stop.detail}") from None
                                issue = self.resolver._external_provider_issue(module, node, alias)
                                if issue is not None:
                                    raise CallLimit(f"{module.ref}:{node.lineno}: {issue}") from None
                                boundaries.append(f"{module.ref}:{node.lineno}: {alias.name}")
                                continue
                            if not containers or any(item.module_path is None for item in containers):
                                raise CallLimit(
                                    f"{module.ref}:{node.lineno} imports {alias.name!r}, "
                                    "a namespace import with no readable module"
                                )
                            for item in containers:
                                assert item.module_path is not None
                                append(item.module_path)
                                for package in self.resolver._enclosing_packages(item.module_path):
                                    append(package)
                        continue
                    paths, missing = self.resolver._imported_paths(module, node)
                    for stop, spelling, _names in missing:
                        # Only exact framework declarations have a modeled external
                        # boundary. Unknown external code is still an unread input.
                        modeled = not node.level and all(
                            f"{node.module}.{alias.name}" in symbols for alias in node.names
                        )
                        if (stop.reason == MODULE_NOT_FOUND and allow_from_operator_ior and len(node.names) == 1
                                and self._namespace_from_operator_ior_import(module, node, node.names[0])):
                            boundaries.append(f"{module.ref}:{node.lineno}: confined from operator ior import")
                            continue
                        if stop.reason != MODULE_NOT_FOUND or not modeled:
                            raise CallLimit(f"{module.ref}:{node.lineno}: {stop.detail}")
                        for alias in node.names:
                            issue = self.resolver._external_provider_issue(module, node, alias)
                            if issue is not None:
                                raise CallLimit(f"{module.ref}:{node.lineno}: {issue}")
                        boundaries.append(f"{module.ref}:{node.lineno}: {spelling}")
                    if not paths and not missing:
                        roots = [(node.module or "").split(".", 1)[0]]
                        if node.level or not all(root in _PRELOADED for root in roots):
                            raise CallLimit(f"{module.ref}:{node.lineno} has a namespace import with no readable module")
                        boundaries.append(f"{module.ref}:{node.lineno}: preloaded {','.join(roots)}")
                    for item in paths:
                        append(item)
                        for package in self.resolver._enclosing_packages(item):
                            append(package)
                for package in self.resolver._enclosing_packages(path):
                    append(package)
        except _Stop as stop:
            raise CallLimit(stop.detail) from None
        search_reader = _CapturedImportSearchResolver(self.resolver, modules)
        try:
            limits = search_reader._import_search_limit([module.path for module in modules])
        except _Stop as stop:
            raise CallLimit(f"import-search source evidence is unread: {stop.detail}") from None
        if limits:
            raise CallLimit(limits[0])
        self._namespace_directory_currency()
        return NamespaceSourceContext(tuple(modules), tuple(boundaries))

    def _namespace_above_import(self, module: PythonModule, node: ast.Import | ast.ImportFrom) -> None:
        """Do not mistake an empty target list for absence outside a sub-scope."""
        layout = self.resolver._layout
        if layout is None or not layout.scope:
            return
        if layout.disk_root is None:
            raise CallLimit("the namespace source context has no bound above-scope import roots")
        root = layout.disk_root

        try:
            if isinstance(node, ast.ImportFrom) and node.level:
                base = module.path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                if not base.is_relative_to(self.resolver.scope_root):
                    raise CallLimit(f"{module.ref}:{node.lineno} imports code above the namespace read scope")
                return  # The ordinary relative reader retains missing/submodule obligations.
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if not name or name.split(".", 1)[0] in _PRELOADED:
                    continue
                for search in self.resolver._import_roots():
                    directory = root / search
                    for part in name.split("."):
                        present = self._namespace_directory_entries(directory)
                        if present is None:
                            break
                        folded = self._namespace_directory_names[directory]
                        matches = folded.get(part.casefold(), frozenset()) | folded.get(f"{part}.py".casefold(), frozenset())
                        if not matches:
                            break  # The captured parent listing proves this root cannot provide it.
                        target = directory / part
                        if f"{part}.py" in matches or matches - {part, f"{part}.py"}:
                            if not directory.is_relative_to(self.resolver.scope_root):
                                raise CallLimit(f"{module.ref}:{node.lineno} has a module candidate for {name!r} above the read scope")
                            break
                        if not (target.is_relative_to(self.resolver.scope_root)
                                or self.resolver.scope_root.is_relative_to(target)):
                            raise CallLimit(f"{module.ref}:{node.lineno} has a namespace candidate for {name!r} above the read scope")
                        directory = target
        except (InputParseError, OSError, ValueError) as exc:
            raise CallLimit(f"{module.ref}:{node.lineno} has unread above-scope import evidence: {exc}") from None

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
                if self._fresh_identity_session is not None:
                    live = self._fresh_identity_session.directory_entries(directory.relative_to(self._fresh_identity_session.root))
                    if frozenset(live) != frozenset(child.name for child in children):
                        raise CallLimit(f"{directory} differs from its live fresh source inventory")
                entries += len(children)
                if entries > 100000:
                    raise CallLimit("the caller census exceeds 100000 directory entries")
                captured_entries: set[tuple[str, int]] = set()
                for child in children:
                    relative = child.relative_to(root).as_posix()
                    metadata = child.lstat()
                    captured_entries.add((child.name, stat.S_IFMT(metadata.st_mode)))
                    if self._fresh_identity_session is not None:
                        kind = self._fresh_identity_session.directory_entry_kind(child.relative_to(self._fresh_identity_session.root))
                        expected = ("symlink" if stat.S_ISLNK(metadata.st_mode) else "directory" if stat.S_ISDIR(metadata.st_mode)
                                    else "file" if stat.S_ISREG(metadata.st_mode) else "special")
                        if kind != expected:
                            raise CallLimit(f"{relative} changed kind in the fresh source inventory")
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
                        if snapshot is not None and not snapshot.contains(marker):
                            snapshot = None  # A materialized Git base is outside the live-head snapshot.
                        absent = (
                            snapshot.bind_dependency_absence(marker)
                            if snapshot is not None
                            else not _lists_entry(marker)
                        )
                        self._inventory_venv_selectors[marker] = (
                            not absent, stat.S_IFMT(marker.lstat().st_mode) if not absent else None
                        )
                        if self._fresh_identity_session is not None:
                            names = self._fresh_identity_session.directory_entries(child.relative_to(self._fresh_identity_session.root))
                            if absent != ("pyvenv.cfg" not in names):
                                raise CallLimit(f"{relative} changed its fresh virtual-environment selector")
                            if not absent:
                                self._fresh_identity_session.directory_entry_kind(marker.relative_to(self._fresh_identity_session.root))
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
                self._inventory_directories[directory] = frozenset(captured_entries)
        except (InputParseError, OSError, ValueError) as exc:
            raise CallLimit(f"the caller census could not read the scope: {exc}") from None
        self._files = sorted(files)
        return self._files

    def _text(self, path: Path) -> str:
        if self._fresh_context_paths is not None and path not in self._fresh_context_paths:
            raise CallLimit(f"{self.resolver.ref(path)} is outside the completed fresh dictionary context")
        if self._read_bytes > MAX_TOTAL_BYTES:
            raise CallLimit(f"the caller census exceeds {MAX_TOTAL_BYTES} source bytes")
        if path not in self._texts:
            snapshot = active_static_input_snapshot()
            if snapshot is not None and not snapshot.contains(path):
                # A materialized base tree is read under its own identity;
                # its bytes do not belong to an active live-head capture.
                snapshot = None
            try:
                self._texts[path] = load_text_file(path)
                if self._fresh_identity_session is not None:
                    live_text = self.resolver._live_text(path)
                    if live_text != self._texts[path]:
                        raise CallLimit(f"{self.resolver.ref(path)} differs from its live fresh source bytes")
                if snapshot is not None:
                    snapshot.mark_dependency_input(path)
                self._read_bytes += len(self._texts[path].encode("utf-8"))
                if self._read_bytes > MAX_TOTAL_BYTES:
                    raise CallLimit(f"the caller census exceeds {MAX_TOTAL_BYTES} source bytes")
            except (InputParseError, ValueError) as exc:
                if snapshot is not None and not snapshot.has(path):
                    snapshot.mark_unconfirmable_dependency(path)
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


def _lists_entry(path: Path) -> bool:
    """Whether its directory's exact listing names ``path``, on any filesystem.

    ``Path.exists`` asks the host: a differently cased spelling is found on a
    case-insensitive filesystem and not on a case-sensitive one. A marker whose
    presence selects the code that is read must be decided by the listing.
    """
    return path.name in {child.name for child in list_input_directory(path.parent)}


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
