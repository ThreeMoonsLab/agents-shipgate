"""Repository-local import resolution for Python framework readers (#864).

A tool list names a callable by a local spelling — ``lookup``,
``memory_bank.remember_firm_finding`` — and the definition behind it often
lives in a sibling module. This module answers, from source alone, which
function *definition* that spelling reaches.

The boundary is deliberately narrow:

* Only regular ``.py`` files inside one explicit scope root are read — the
  directory the reader was already given. Each is read through the same
  bounded, snapshot-aware reader every input uses, so a module a resolution
  depends on is part of the run's input identity, and is parsed with
  :mod:`ast`. Nothing is imported or executed.
* Only static ``import`` / ``from ... import`` statements, attribute access on
  an imported module, and a plain ``alias = name`` assignment are followed. The
  name has to be bound exactly once, by a statement directly in the module
  body, in every module the chain passes through.
* Directory entries are matched by exact spelling from a listing, never through
  a case-folding or aliasing filesystem lookup, and a symbolic link is never
  followed.
* Every outcome other than one exact definition carries a named reason, so a
  caller reports the reference individually instead of guessing, dropping it,
  or treating it as an empty surface.

Absolute module names are looked up against a bounded set of roots: the
importing file's directory and each of its ancestors up to the scope root,
plus — when the scope root is itself a regular package — the scope root's
parent, for names that begin with the scope's own package name. A module found
under more than one root is reported as ambiguous rather than chosen.
"""

from __future__ import annotations

import ast
import hashlib
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import list_input_directory, load_text_file

#: Stable reason codes. The sentence that goes with each one is
#: :attr:`Resolution.detail`.
NOT_BOUND = "not_bound"
MODULE_NOT_FOUND = "module_not_found"
OUTSIDE_SCOPE = "outside_scope"
AMBIGUOUS_MODULE = "ambiguous_module"
REBOUND_NAME = "rebound_name"
CONDITIONAL_BINDING = "conditional_binding"
STAR_IMPORT = "star_import"
IMPORT_CYCLE = "import_cycle"
NAME_NOT_DEFINED = "name_not_defined"
NOT_A_FUNCTION = "not_a_function"
LINKED_MODULE = "linked_module"
UNREADABLE_MODULE = "unreadable_module"
RESOLUTION_LIMIT = "resolution_limit"
LOCAL_BINDING = "local_binding"
#: A tool factory's return that is not one function wrapped as a tool (#865).
FACTORY_RETURN = "factory_return"

#: Distinct modules one resolver will parse, and lookups one reference may
#: take. A repository-local tool is normally one or two hops away; the bounds
#: exist so a pathological re-export web ends in a named reason, not a hang.
MAX_MODULES = 64
MAX_STEPS = 32
#: Modules read only to check what runs before a name is used for patches.
MAX_PATCH_SCAN_MODULES = 1024
#: Module names generated at build time, which define data and patch nothing:
#: ``*_pb2`` / ``*_pb2_grpc`` from a ``.proto``, setuptools-scm's ``_version``.
_GENERATED_SUFFIXES = ("_pb2", "_pb2_grpc")
_GENERATED_NAMES = frozenset({"_version"})


@dataclass(frozen=True)
class RepositoryLayout:
    """What the repository holds outside the read scope (#879 review).

    An absolute import that no file in the scope provides is a third-party
    package, or the application's own code outside the scope (``from
    common.patches import applied`` in a monorepo). Only the repository can
    tell the two apart; a directory named like the ancestor is not enough
    (``agents/`` holding OpenAI Agents SDK apps).
    """

    #: The scope's path from the repository root; "" for the root itself.
    scope: str
    #: The names directly inside one repository directory ("" is the root);
    #: None when it is not a directory or cannot be listed.
    entries: Callable[[str], frozenset[str] | None]
    #: The names inside one directory that are symbolic links or submodules:
    #: code an import can reach that is not read (#879 review).
    links: Callable[[str], frozenset[str]] = lambda path: frozenset()
    #: The text of one repository file outside the scope, or None: what the
    #: packages enclosing the scope run first (#879 review).
    read: Callable[[str], str | None] = lambda path: None
    #: Actual disk root, only for layouts built by the disk reader. Tree-backed
    #: callbacks need their own input binding; absence is not inferred from a
    #: coincidentally matching temporary directory.
    disk_root: Path | None = None


_REPOSITORY: ContextVar[RepositoryLayout | None] = ContextVar("repository_layout", default=None)


@contextmanager
def repository_layout(layout: RepositoryLayout | None) -> Iterator[None]:
    """Read imports against ``layout`` — the comparison's Git tree — while active."""

    token = _REPOSITORY.set(layout)
    try:
        yield
    finally:
        _REPOSITORY.reset(token)


#: Directories above the scope read as the project when no checkout encloses
#: it (an exported tree).
MAX_UNVERSIONED_LEVELS = 3


def _disk_layout(scope_root: Path) -> RepositoryLayout | None:
    """The checkout that holds ``scope_root``, read from disk.

    Outside a checkout — an exported or extracted tree — the directories a few
    levels above the scope stand in for it, so an import of the project's own
    code there is still named (#879 review).
    """

    root = scope_root
    while not (root / ".git").exists():
        if root.parent == root:
            root = scope_root
            for _ in range(MAX_UNVERSIONED_LEVELS):
                if root.parent == root:
                    break
                root = root.parent
            break
        root = root.parent
    listings: dict[str, frozenset[str] | None] = {}

    def entries(path: str) -> frozenset[str] | None:
        if path not in listings:
            directory = root / path if path else root
            try:
                listings[path] = (
                    frozenset(child.name for child in list_input_directory(directory))
                    if directory.is_dir() and not directory.is_symlink()
                    else None
                )
            except (InputParseError, OSError):
                listings[path] = None
        return listings[path]

    def links(path: str) -> frozenset[str]:
        directory = root / path if path else root
        return frozenset(
            name
            for name in entries(path) or ()
            if (directory / name).is_symlink()
        )

    def read(path: str) -> str | None:
        target = root / path
        if target.is_symlink() or not target.is_file():
            return None
        try:
            return load_text_file(target)
        except (InputParseError, OSError):
            return None

    scope = scope_root.relative_to(root).as_posix()
    return RepositoryLayout("" if scope == "." else scope, entries, links, read, disk_root=root)

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


@dataclass(frozen=True)
class _Binding:
    """One module-scope binding of a name."""

    node: ast.AST
    statement: ast.stmt
    #: The binding statement is a direct child of the module body — not nested
    #: in an ``if``, ``try``, ``with`` or loop, and not a ``global`` rebinding
    #: from inside a function.
    top_level: bool


@dataclass
class PythonModule:
    """One parsed module inside the scope root."""

    path: Path
    #: Scope-relative POSIX path; what evidence and tool locations print.
    ref: str
    tree: ast.Module
    text: str
    sha256: str
    #: An ``__init__.py``: a name it does not bind falls through to a submodule.
    package: bool
    bindings: dict[str, list[_Binding]]
    star_import: bool
    #: ``dotted.path -> line`` for ``a.b = ...`` / ``setattr(a, "b", ...)``.
    attribute_patches: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Resolution:
    """Where one reference led, and every module it read on the way."""

    reference: str
    reason: str | None = None
    detail: str | None = None
    #: The module that holds ``definition`` (or ``value``).
    module: PythonModule | None = None
    definition: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    #: When the chain ends at ``name = <expression>`` that is not a plain alias,
    #: the expression — a caller may recognise a wrapper constructed there.
    value: ast.expr | None = None
    steps: tuple[dict[str, Any], ...] = ()
    #: Why a definition reached is still not established as what the name
    #: holds when the module runs: a module the chain runs first imports code
    #: above the read scope, which could reassign it (#879 review). A caller
    #: names the tool and reports it, never as an established binding.
    caveats: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.definition is not None

    def evidence(self) -> dict[str, Any]:
        """JSON-safe record of the chain: every hop and the digest it read."""

        inputs: dict[str, str] = {}
        for step in self.steps:
            inputs.setdefault(step["path"], step["sha256"])
        payload: dict[str, Any] = {
            "reference": self.reference,
            "steps": [dict(step) for step in self.steps],
            "inputs": [
                {"path": path, "sha256": digest} for path, digest in sorted(inputs.items())
            ],
        }
        if self.module is not None and self.definition is not None:
            payload["definition"] = f"{self.module.ref}:{self.definition.lineno}"
        if self.reason is not None:
            payload["reason"] = self.reason
            payload["detail"] = self.detail
        if self.caveats:
            payload["caveats"] = list(self.caveats)
        return payload


@dataclass
class _Container:
    """What a dotted module name located: a module file, or a namespace dir."""

    directory: Path
    module_path: Path | None = None
    package: bool = False


class _Stop(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class _PatchScan:
    """What running one module, and the modules it imports, may reassign."""

    #: ``attribute name -> [(module ref, line, modules the root names)]``
    #: reassigned on an imported module; the last is None when the import
    #: the patch is rooted at cannot be located.
    patched: dict[str, list[tuple[str, int, frozenset[Path] | None]]]
    #: Imports that climb above the read scope, whose code is not read.
    unread: tuple[str, ...]
    #: Actual table-row module provenance only; never a module safety verdict.
    table_modules: dict[tuple[str, str, int], tuple[PythonModule, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class _ConstructorSyntax:
    """Ordered actual syntax only; no identity, purity or ownership result."""

    nodes: tuple[ast.AST, ...]
    imports: tuple[ast.Import | ast.ImportFrom, ...]
    references: tuple[ast.Name | ast.Attribute, ...]


@dataclass
class ImportResolver:
    """Resolves references inside one scope root. One instance per read."""

    scope_root: Path
    _modules: dict[Path, PythonModule | _Stop] = field(default_factory=dict)
    _listings: dict[Path, frozenset[str] | None] = field(default_factory=dict)
    _patches: dict[Path, _PatchScan | _Stop] = field(default_factory=dict)
    _scanned: dict[Path, PythonModule | _Stop] = field(default_factory=dict)
    _parsed: int = 0
    _layout: RepositoryLayout | None = None
    _above: _AboveScope | None = None
    _layout_modules: dict[str, PythonModule | None] = field(default_factory=dict)
    #: Repository files the read bound left unread.
    _over_budget: set[str] = field(default_factory=set)
    _constructor_reference_mode: bool = False
    _import_search_captured: Mapping[Path, str] | None = None
    _checking_source_module_slot: bool = False
    _checking_fresh_dictionary_primitive: bool = False
    _constructor_namespace_owners: dict[tuple[str, int, str], tuple[PythonModule, ast.Call, str]] = field(default_factory=dict)
    _constructor_container_owners: dict[int, tuple[PythonModule, ast.expr]] = field(default_factory=dict)
    _constructor_member_sinks: dict[int, tuple[PythonModule, ast.expr]] = field(default_factory=dict)
    _constructor_dictionary_sinks: dict[tuple[str, int], tuple[PythonModule, ast.expr, str]] = field(default_factory=dict)
    _constructor_dictionary_class_sinks: dict[tuple[str, int], tuple[PythonModule, ast.expr, str, str]] = field(default_factory=dict)
    _constructor_unused_namespace_sinks: dict[int, tuple[PythonModule, ast.Name]] = field(default_factory=dict)
    _constructor_decorator_owners: dict[int, tuple[PythonModule, ast.FunctionDef | ast.AsyncFunctionDef]] = field(default_factory=dict)
    _constructor_wrapped_operands: set[int] = field(default_factory=set)
    #: Every module the constructor census reads, set before it runs.
    _constructor_runners: tuple[Path, ...] = ()
    _annotation_readers: list[str | None] = field(default_factory=list)
    _constructor_import_proofs: dict[tuple, str | None] = field(default_factory=dict)
    _external_provider_proofs: dict[tuple[int, str, tuple[str, ...]], str | None] = field(default_factory=dict)
    _runtime_patch_maps: dict[int, dict[str, int]] = field(default_factory=dict)
    _reflection_import_loads: dict[ast.Module, frozenset[str]] = field(default_factory=dict)
    _constructor_scopes: dict[ast.Module, ScopeIndex] = field(default_factory=dict)
    _constructor_syntax_trees: dict[ast.Module, _ConstructorSyntax] = field(default_factory=dict)

    def _constructor_scope(self, module: PythonModule) -> ScopeIndex:
        """Reuse structural AST roles only; never cache an ownership verdict."""
        if module.tree not in self._constructor_scopes:
            self._constructor_scopes[module.tree] = ScopeIndex(module.tree)
        return self._constructor_scopes[module.tree]

    def _constructor_syntax(self, module: PythonModule) -> _ConstructorSyntax:
        """Reuse the actual immutable parse; all semantic checks stay fresh."""
        cached = self._constructor_syntax_trees.get(module.tree)
        if cached is None:
            nodes = tuple(ast.walk(module.tree))
            scopes = self._constructor_scope(module)
            cached = _ConstructorSyntax(
                nodes,
                tuple(node for node in nodes if isinstance(node, ast.Import | ast.ImportFrom)),
                tuple(node for node in nodes if isinstance(node, ast.Name | ast.Attribute)
                      and (not isinstance(node, ast.Name) or isinstance(node.ctx, ast.Load))
                      and not (isinstance(parent := scopes.parents.get(node), ast.Attribute)
                               and parent.value is node)),
            )
            self._constructor_syntax_trees[module.tree] = cached
        return cached

    def _runtime_attribute_patches(self, module: PythonModule) -> dict[str, int]:
        """Executed stores, with lexical bindings and raw flag stores intact.

        Recompute from nodes rather than filtering the stored first line: a
        dead first store must not hide a later live store of the same key.
        The cache belongs to this resolver's provider/search-root context.
        """
        if not module.attribute_patches:
            return module.attribute_patches
        key = id(module.tree)
        if key not in self._runtime_patch_maps:
            excluded = frozenset(_type_checking_only(module, self))
            self._runtime_patch_maps[key] = _attribute_patches(module.tree, excluded=excluded)
        return self._runtime_patch_maps[key]

    def _import_search_module_guard(self, path: Path) -> None:
        """A captured context never borrows another source or another AST."""
        if self._import_search_captured is None:
            return
        expected = self._import_search_captured.get(path)
        captured = self._modules.get(path)
        if expected is None or not isinstance(captured, PythonModule) or captured.text != expected:
            raise _Stop(OUTSIDE_SCOPE, f"{path} is not the text-accounted import-search source")

    def _import_search_context(
        self, paths: list[Path],
    ) -> tuple[list[PythonModule], set[Path], tuple[str, ...]]:
        """One bounded startup import graph, separate from patch-target rules.

        The carrier set is structural provenance, not a purity/owner verdict.
        It is local to this invocation and never cached as a positive result.
        """
        from collections import deque
        modules: list[PythonModule] = []
        pending = deque()
        seen: set[Path] = set()
        reverse: dict[Path, set[Path]] = {}
        carriers: set[Path] = set()
        unread: list[str] = []

        def enqueue(path: Path) -> None:
            if path in seen:
                return
            if len(seen) >= MAX_PATCH_SCAN_MODULES:
                raise _Stop(RESOLUTION_LIMIT, "the import-search closure exceeds its module bound")
            seen.add(path)
            pending.append(path)

        for path in paths:
            for item in (path, *self._enclosing_packages(path)):
                enqueue(item)
        while pending:
            path = pending.popleft()
            module = self._patch_scan(path)
            modules.append(module)
            scopes = self._constructor_scope(module)
            typing_only = _type_checking_only(module, self, scopes)
            guarded = _import_guarded(module.tree)
            for statement in ast.walk(module.tree):
                if id(statement) in typing_only or not isinstance(statement, ast.Import | ast.ImportFrom):
                    continue
                for alias in statement.names:
                    imported = _absolute_import_reference(alias, statement)
                    if imported is not None and (imported in {"sys", "site"}
                            or imported.startswith("site.")
                            or imported.split('.', 1)[0] == "sys" and imported.split('.', 1)[-1] in {
                                "path", "meta_path", "path_hooks", "path_importer_cache",
                                "__dict__", "__class__", "__getattribute__", "__getattr__", "__setattr__",
                            }):
                        carriers.add(path)
                imported_paths, missing = self._imported_paths(module, statement)
                for stop, spelling, names in missing:
                    caveat = self._unread_import(module, statement, stop, spelling, names,
                                                 id(statement) in guarded)
                    if caveat is not None and caveat not in unread:
                        unread.append(caveat)
                for target in imported_paths:
                    for item in (target, *self._enclosing_packages(target)):
                        reverse.setdefault(item, set()).add(path)
                        enqueue(item)
        propagation = deque(sorted(carriers))
        while propagation:
            target = propagation.popleft()
            for importer in sorted(reverse.get(target, set())):
                if importer not in carriers:
                    carriers.add(importer)
                    propagation.append(importer)
        return modules, carriers, tuple(unread)

    def _import_search_issue(self, module: PythonModule, carriers: set[Path]) -> str | None:
        """Project actual maximal references without public resolution recursion."""
        from agents_shipgate.inputs.list_expressions import evaluation_site
        scopes = self._constructor_scope(module)
        excluded = frozenset(_type_checking_only(module, self, scopes))
        effects = _import_search_effects(module.tree, excluded=excluded, scopes=scopes)
        if effects:
            node = next(iter(effects))
            return f"{module.ref}:{node.lineno} changes or retains {effects[node]}; import search identity is unread"
        projections: dict[ast.AST, str] = {}
        work = 0
        for node in ast.walk(module.tree):
            if id(node) in excluded or not isinstance(node, ast.Name | ast.Attribute):
                continue
            parent = scopes.parents.get(node)
            if isinstance(parent, ast.Attribute) and parent.value is node:
                continue
            parts = _dotted(node)
            if parts is None:
                continue
            candidates = scopes.enclosing_bindings(evaluation_site(scopes, node), parts[0])
            if not candidates:
                candidates = [binding.node for binding in module.bindings.get(parts[0], [])]
            if not any(isinstance(candidate, ast.alias | ast.Name) for candidate in candidates):
                continue
            work += 1
            if work > MAX_STEPS * MAX_STEPS:
                return f"{module.ref}:{node.lineno} exceeds the import-search reference bound"
            current, reference = module, node
            followed: set[tuple[ast.Module, ast.AST]] = set()
            outcome: dict[str, Any] = {}
            for _ in range(MAX_STEPS):
                key = (current.tree, reference)
                if key in followed:
                    return f"{module.ref}:{node.lineno} has cyclic import-search carrier evidence"
                followed.add(key)
                outcome = self._constructor_reference(current, reference, self._constructor_scope(current))
                value = outcome.get("value")
                if not isinstance(value, ast.Name | ast.Attribute):
                    break
                owner = outcome.get("module")
                if not isinstance(owner, PythonModule):
                    return f"{module.ref}:{node.lineno} has unread import-search alias ownership"
                current, reference = owner, value
            else:
                return f"{module.ref}:{node.lineno} exceeds the import-search alias bound"
            handle = outcome.get("import_search_handle")
            if isinstance(handle, str):
                projections[node] = handle
                continue
            namespace = outcome.get("retained_namespace")
            if isinstance(namespace, Path) and not isinstance(parent, ast.Expr):
                if namespace in carriers or any(path.is_relative_to(namespace) for path in carriers):
                    return f"{module.ref}:{node.lineno} retains an imported sys/site namespace; import search identity is unread"
            if outcome.get("constructor_unread") or not outcome:
                for candidate in candidates:
                    statement = scopes.statement_of(candidate)
                    if not isinstance(candidate, ast.alias) or not isinstance(statement, ast.Import | ast.ImportFrom):
                        continue
                    paths, _ = self._imported_paths(module, statement)
                    if any(path in carriers for path in paths):
                        return f"{module.ref}:{node.lineno} has unread import-search carrier identity"
        effects = _import_search_effects(module.tree, excluded=excluded, projections=projections, scopes=scopes)
        if effects:
            node = next(iter(effects))
            return f"{module.ref}:{node.lineno} changes or retains {effects[node]}; import search identity is unread"
        return None

    def _import_search_limit(self, paths: list[Path]) -> tuple[str, ...]:
        modules, carriers, unread = self._import_search_context(paths)
        for module in modules:
            if (issue := self._import_search_issue(module, carriers)) is not None:
                return (*unread, issue)
        return unread

    def _constructor_reference(self, module: PythonModule, node: ast.expr, scopes: ScopeIndex) -> dict[str, Any]:
        """Use the import resolver for identity only, stopping at external imports.

        These private outcomes never become tool resolutions or evidence. The
        ordinary resolver still requires a repository-local definition.
        """
        parts = _dotted(node)
        if parts is None:
            return {}
        previous = self._constructor_reference_mode
        from agents_shipgate.inputs.list_expressions import evaluation_site
        self._constructor_reference_mode = True
        try:
            local = scopes.enclosing_bindings(evaluation_site(scopes, node), parts[0])
            if local:
                if len(local) == 1 and isinstance(local[0], ast.FunctionDef | ast.AsyncFunctionDef):
                    return {"module": module, "definition": local[0], "constructor_callable": len(parts) == 1}
                if len(local) == 1 and isinstance(local[0], ast.ClassDef):
                    return {"module": module, "retained_class": local[0]}
                if len(local) == 1 and isinstance(local[0], ast.Name):
                    statement = scopes.statement_of(local[0])
                    if isinstance(statement, ast.Assign | ast.AnnAssign) and len(parts) == 1:
                        return {"module": module, "value": statement.value}
                if len(local) != 1 or not isinstance(local[0], ast.alias):
                    return {}
                statement = scopes.statement_of(local[0])
                if not isinstance(statement, ast.Import | ast.ImportFrom):
                    return {}
                return self._through_import(module, statement, local[0], parts, [], set())
            return self._in_module(module, parts, [], set())
        except _Stop as stop:
            return {"constructor_unread": stop.reason, "detail": stop.detail,
                    **({"unread_external_handle": True} if stop.reason == MODULE_NOT_FOUND else {})}
        finally:
            self._constructor_reference_mode = previous

    def __post_init__(self) -> None:
        self.scope_root = self.scope_root.resolve()
        self._layout = _REPOSITORY.get() or _disk_layout(self.scope_root)

    def _annotation_introspection(self) -> str | None:
        """Where a module the constructor census reads can read a function's annotations.

        An eager annotation stores the class it names in the function's
        ``__annotations__``. That is only a retained handle if something reads
        the dictionary, so a framework class is accepted there only when no
        module of the census names one of the ways to read it. ``inspect`` and
        ``getattr`` are already refused as reflective machinery.
        """
        if not self._annotation_readers:
            found: str | None = None
            for path in self._constructor_runners:
                module = self._patch_scan(path)
                for node in self._constructor_syntax(module).nodes:
                    name = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                            else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None)
                    if name in _ANNOTATION_READERS:
                        found = f"{module.ref}:{getattr(node, 'lineno', 0)}"
                        break
                if found is not None:
                    break
            self._annotation_readers.append(found)
        return self._annotation_readers[0]

    def _unread_import_candidate(self, module: PythonModule, statement: ast.AST, alias: ast.alias) -> bool:
        """Keep an actual unread import candidate without resolving its identity."""
        if not isinstance(statement, ast.Import | ast.ImportFrom):
            return False
        if isinstance(statement, ast.ImportFrom) and statement.level:
            return False
        dotted = statement.module if isinstance(statement, ast.ImportFrom) else alias.name
        if (dotted or "").split(".", 1)[0] in _PRELOADED:
            return True  # A sibling file cannot replace the startup import.
        thin = _single_import(statement, alias)
        paths, missing = self._imported_paths(module, thin)
        for stop, _, _ in missing:
            if stop.reason != MODULE_NOT_FOUND:
                raise _Stop(stop.reason, f"constructor import ownership is unread: {stop.detail}")
        if any(stop.reason == MODULE_NOT_FOUND for stop, _, _ in missing):
            return True
        # A namespace portion supplies no regular module identity. An external
        # regular package can win over it, and an absent member is not a read
        # external object. A known local child file keeps its ordinary route.
        return not paths and any(item.module_path is None for item in self._absolute_candidates(module, dotted or ""))

    # -- modules ---------------------------------------------------------------

    def ref(self, path: Path) -> str:
        return path.relative_to(self.scope_root).as_posix()

    def contains(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.scope_root)

    def entry(self, path: Path, tree: ast.Module, text: str) -> PythonModule | None:
        """Register a module the reader already parsed; None outside the scope."""

        resolved = path.resolve()
        if not resolved.is_relative_to(self.scope_root):
            return None
        cached = self._modules.get(resolved)
        if isinstance(cached, PythonModule):
            return cached
        scanned = self._scanned.get(resolved) if cached is None else None
        if isinstance(scanned, PythonModule) and scanned.path == resolved and scanned.text == text:
            # Keep actual AST roles when an externally parsed entry promotes the same captured source.
            self._modules[resolved] = scanned
            return scanned
        module = _module(resolved, self.ref(resolved), tree, text)
        self._modules[resolved] = module
        return module

    def module(self, path: Path) -> PythonModule:
        """Parse one in-scope module file, at most once; raise :class:`_Stop`."""

        self._import_search_module_guard(path)
        cached = self._modules.get(path)
        if isinstance(cached, _Stop):
            raise cached
        if cached is not None:
            return cached
        ref = self.ref(path)
        if isinstance(self._scanned.get(path), PythonModule):
            # Already read for an enclosing package's patches: one object per
            # module (#879 review). It now counts against the resolution budget.
            if self._parsed >= MAX_MODULES:
                raise _Stop(
                    RESOLUTION_LIMIT,
                    f"resolving it would read more than {MAX_MODULES} modules",
                )
            self._parsed += 1
            scanned = self._scanned[path]
            assert isinstance(scanned, PythonModule)
            self._modules[path] = scanned
            return scanned
        if self._parsed >= MAX_MODULES:
            stop = _Stop(
                RESOLUTION_LIMIT,
                f"resolving it would read more than {MAX_MODULES} modules",
            )
            raise stop
        self._parsed += 1
        try:
            text = load_text_file(path)
            tree = ast.parse(text, filename=str(path))
        except (InputParseError, SyntaxError, ValueError, RecursionError):
            stop = _Stop(UNREADABLE_MODULE, f"{ref} could not be read or parsed")
            self._modules[path] = stop
            raise stop from None
        module = _module(path, ref, tree, text)
        self._modules[path] = module
        return module

    # -- resolution ------------------------------------------------------------

    def resolve_local_import(
        self,
        module: PythonModule,
        statement: ast.Import | ast.ImportFrom,
        alias: ast.alias,
        reference: str,
    ) -> Resolution:
        """Resolve ``reference`` through an import inside a function (#879 review).

        A builder's own ``from support import lookup`` is the binding its agent
        receives, so it is followed exactly as a module-level import would be.
        """

        parts = reference.split(".")
        steps: list[dict[str, Any]] = [
            {
                "path": module.ref,
                "line": _line(statement),
                "name": parts[0],
                "sha256": module.sha256,
                "binding": "local_import",
            }
        ]
        try:
            self._no_attribute_patch(module, parts)
            outcome = self._through_import(
                module, statement, alias, parts, steps, set()
            )
            caveats = self._no_import_patch(outcome, steps)
        except _Stop as stop:
            return Resolution(
                reference=reference, reason=stop.reason, detail=stop.detail, steps=tuple(steps)
            )
        return Resolution(reference=reference, steps=tuple(steps), caveats=caveats, **outcome)

    def _no_attribute_patch(self, module: PythonModule, parts: list[str]) -> None:
        """Stop when this module rebinds an imported attribute on the path.

        ``import tools; tools.lookup = tools.dangerous`` (or ``setattr``) makes
        ``tools.lookup`` mean something the module file does not say.
        """

        for length in range(2, len(parts) + 1):
            line = self._runtime_attribute_patches(module).get(".".join(parts[:length]))
            if line is not None:
                raise _Stop(
                    REBOUND_NAME,
                    f"{'.'.join(parts[:length])!r} is reassigned by attribute in "
                    f"{module.ref}:{line}",
                )

    def imported_constructor_issue(
        self, module: PythonModule, expression: ast.expr, scopes: ScopeIndex,
        *, contexts: tuple[PythonModule, ...] = (),
    ) -> str | None:
        from agents_shipgate.inputs.list_expressions import evaluation_site
        reference = expression.value if isinstance(expression, ast.Subscript) else expression
        # These are separate (sometimes lazy) evaluation frames, not the
        # module import a spelling outside them would resolve to. Keep them
        # unread rather than flattening an unsupported annotation scope.
        child, current = reference, scopes.parents.get(reference)
        argument_annotation = False
        while current is not None:
            if hasattr(ast, "TypeAlias") and isinstance(current, ast.TypeAlias):
                return f"constructor identity in a deferred type-alias scope is not established at {module.ref}:{_line(reference)}"
            if isinstance(current, ast.arg | ast.AnnAssign) and child is current.annotation:
                if scopes.annotations_postponed:
                    return f"constructor identity in a postponed annotation is not established at {module.ref}:{_line(reference)}"
                if isinstance(current, ast.AnnAssign):
                    block = scopes.parents.get(current)
                    while block is not None and not isinstance(block, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                        block = scopes.parents.get(block)
                    if isinstance(block, ast.FunctionDef | ast.AsyncFunctionDef):
                        return f"constructor identity in an unevaluated function-local annotation is not established at {module.ref}:{_line(reference)}"
            if isinstance(current, ast.arg) and child is current.annotation:
                argument_annotation = True
            if (isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef)
                    and child is current.returns and scopes.annotations_postponed):
                return f"constructor identity in a postponed annotation is not established at {module.ref}:{_line(reference)}"
            if getattr(current, "type_params", ()):
                if child in current.type_params:
                    return f"constructor identity in a deferred type-parameter scope is not established at {module.ref}:{_line(reference)}"
                generic_annotation = (
                    isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef)
                    and (child is current.returns or child is current.args and argument_annotation)
                    or isinstance(current, ast.ClassDef) and child in [*current.bases, *current.keywords]
                )
                if generic_annotation:
                    return f"constructor identity in a generic annotation scope is not established at {module.ref}:{_line(reference)}"
            child, current = current, scopes.parents.get(current)
        spelling = reference_spelling(reference)
        head = spelling.split(".", 1)[0] if spelling else ""
        nodes = scopes.enclosing_bindings(evaluation_site(scopes, reference), head) or [binding.node for binding in module.bindings.get(head, [])]
        key = (id(module.tree), spelling, tuple(id(node) for node in nodes),
               tuple(sorted({id(home.tree) for home in contexts})))
        if key not in self._constructor_import_proofs:
            self._constructor_import_proofs[key] = self._imported_constructor_issue(module, expression, scopes, contexts=contexts)
        return self._constructor_import_proofs[key]

    def _imported_constructor_issue(
        self, module: PythonModule, expression: ast.expr, scopes: ScopeIndex,
        *, contexts: tuple[PythonModule, ...] = (),
    ) -> str | None:
        """Why a recognized external constructor is not its unchanged import.

        Recognition remains the framework reader's job. This checks the
        binding and code the import runs without importing the framework.
        Changing the class itself (``Agent.__init__ = other``) matters just as
        much as replacing the module's ``Agent`` attribute. An external class
        has no defining-module exemption for handing a local definition on.
        """
        if isinstance(expression, ast.Subscript):
            expression = expression.value
        from agents_shipgate.inputs.list_expressions import evaluation_site
        spelling = reference_spelling(expression)
        if spelling is None:
            return "the constructor is not a plain imported reference"
        head = spelling.split(".", 1)[0]
        local = scopes.enclosing_bindings(evaluation_site(scopes, expression), head)
        nodes = local or [binding.node for binding in module.bindings.get(head, [])]
        if not nodes or any(not isinstance(node, ast.alias) for node in nodes):
            return f"the constructor root {head!r} is not bound only by framework imports"
        canonical_paths, import_owners = set(), set()
        for alias in nodes:
            statement = scopes.statement_of(alias)
            owner = scopes.parents.get(statement)
            if not isinstance(owner, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef):
                return "the constructor import is conditional or is not in its lexical body"
            import_owners.add(id(owner))
            imported = _absolute_import_reference(alias, statement)
            if imported is None:
                return "the constructor does not come from an absolute framework import"
            canonical_paths.add(imported + spelling[len(head):])
            provider_issue = self._external_provider_issue(module, statement, alias)
            if provider_issue is not None:
                return provider_issue
        if len(import_owners) != 1 or len(canonical_paths) != 1:
            return "the constructor imports do not establish one lexical framework identity"
        canonical = canonical_paths.pop()
        if canonical.startswith(("agents.", "openai_agents.")):
            family = canonical.split(".", 1)[0]
            if canonical not in {f"{family}.Agent", f"{family}.agent.Agent"}:
                return "the constructor import path is outside the supported SDK constructor paths"
        elif canonical.startswith("google.adk."):
            family = "google.adk"
        else:
            return "the constructor does not come from a supported framework import"
        names = {head, spelling.rsplit(".", 1)[-1], "__init__", "__new__", "__call__"}
        names.update(alias.name.rsplit(".", 1)[-1] for alias in nodes)
        steps = [{"path": home.ref, "name": name}
                 for home in (module, *contexts) for name in sorted(names)]
        try:
            for patched, line in self._runtime_attribute_patches(module).items():
                if patched == spelling or patched.startswith(spelling + ".") or spelling.startswith(patched + "."):
                    return f"the constructor reference is changed by attribute in {module.ref}:{line}"
            caveats = self._no_import_patch({"module": module}, steps, external_symbol=family)
        except _Stop as stop:
            return stop.detail
        return "; ".join(caveats) or None

    def _external_provider_issue(self, module: PythonModule, statement: ast.AST, alias: ast.alias) -> str | None:
        dotted = statement.module if isinstance(statement, ast.ImportFrom) else alias.name
        names = [alias.name] if isinstance(statement, ast.ImportFrom) else []
        return self._external_provider_path_issue(module, dotted or "", names)

    def _external_provider_path_issue(self, module: PythonModule, dotted: str, names: list[str]) -> str | None:
        key = (id(module.tree), dotted, tuple(names))
        if key in self._external_provider_proofs:
            return self._external_provider_proofs[key]
        if dotted.split(".", 1)[0] in _PRELOADED:
            self._external_provider_proofs[key] = None
            return None
        issue = None
        if self.repository_holds(dotted or "", names):
            issue = "the repository supplies the framework import; its external identity is not established"
        else:
            parts = (dotted or "").split(".")
            for length in range(1, len(parts) + 1):
                try:
                    candidates = self._installed_candidates(module, ".".join(parts[:length]))
                except _Stop as stop:
                    if stop.reason != MODULE_NOT_FOUND:
                        issue = stop.detail
                        break
                else:
                    if any(container.module_path is not None for container in candidates):
                        issue = "a local module or regular package supplies the framework import"
                        break
        self._external_provider_proofs[key] = issue
        return issue

    def _no_import_patch(
        self, outcome: dict[str, Any], steps: list[dict[str, Any]],
        *, external_symbol: str | None = None,
    ) -> tuple[str, ...]:
        """Stop when code that runs before the name is used may rebind it.

        Every module the chain reads, every package enclosing one of them, and
        every module those import runs before the agent receives the name:
        ``import patches`` in the agent's own file, or
        ``from . import impl; impl.lookup = other`` in a package ``__init__``,
        replaces what ``from pkg.impl import lookup`` receives (#879 review).
        A reassignment there of an imported module's attribute named like a
        step of the chain is a named stop. The defining module's own
        assignments do not count: ``registry.lookup = lookup`` there hands the
        definition on and cannot replace it.

        An import of code above the read scope — relative, or absolute through
        the name of a directory above it — or of a module no file in the scope
        provides runs code that is not read, and so does a package
        ``__getattr__`` that is not the lazy-submodule idiom. Each is returned
        as a caveat, so the caller names the tool but never establishes it. A
        generated ``*_pb2`` module, an optional import under ``except
        ImportError``, and an absolute import of anything else no file in the
        scope provides (a third-party package) are the read's boundary. A
        module none of these import is not looked for.
        """

        defining = outcome.get("module")
        if not isinstance(defining, PythonModule) or not steps:
            return ()
        names = sorted({step["name"] for step in steps if isinstance(step.get("name"), str)})
        runners: list[Path] = []
        for path in [*(self.scope_root / step["path"] for step in steps), defining.path]:
            for item in (path, *self._enclosing_packages(path)):
                if item not in runners:
                    runners.append(item)
        caveats: list[str] = [
            f"{step['path']} answers {step['name']!r} through a module-level __getattr__, "
            "which is not evaluated and could return something other than the submodule"
            for step in steps
            if step.get("module_getattr") and not step.get("lazy_submodule")
        ]
        scoped = {_module_name(ref) for ref in (*(step["path"] for step in steps), defining.ref)}
        layout = self._layout
        prefix = layout.scope.replace("/", ".") if layout is not None and layout.scope else ""
        full = {f"{prefix}.{name}" if name else prefix for name in scoped} if prefix else set()
        if external_symbol is not None:
            full |= _external_constructor_dependency_paths(external_symbol)
            full |= _external_constructor_dependency_modules(external_symbol)
        caveats.extend(
            f"{step['path']} changes __path__, so {step['name']!r} may be found in another "
            "directory"
            for step in steps
            if step.get("path_extended")
        )

        def table(
            key: str, where: str, line: int, runner_ref: str, *, repository_ref: bool = False,
            marker_module: PythonModule | None = None,
        ) -> None:
            if (key == MODULE_TABLE_COMPUTED and not repository_ref
                    and isinstance(marker_module, PythonModule) and marker_module.ref == where):
                from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                try:
                    if (self._patch_scan(marker_module.path) is marker_module
                            and BuilderCalls(self).source_saved_vars_table_marker(marker_module, key, line)):
                        return  # Only this actual row's fully proved intrinsic initializer origin.
                except (CallLimit, _Stop):
                    pass  # The ordinary computed-marker obligation remains on every refusal.
            verdict = _table_verdict(
                key, where, scoped, full, prefix, repository_ref=repository_ref
            )
            if verdict == "caveat":
                caveat = (
                    f"{where}:{line} stores into sys.modules or globals() under a computed "
                    "name, which could replace a module or name an import reaches"
                )
                if caveat not in caveats:
                    caveats.append(caveat)
            elif verdict == "stop":
                raise _Stop(
                    REBOUND_NAME,
                    f"{where}:{line} stores into sys.modules a module this chain imports, "
                    f"which {runner_ref} runs before the name is used",
                )

        above = self._above_scope()
        for where, line, key in above.tables:
            table(key, where, line, "a package enclosing the scope", repository_ref=True)
        for name in names:
            for where, line in above.patched.get(name, []):
                raise _Stop(
                    REBOUND_NAME,
                    f"an attribute named {name!r} is reassigned in {where}:{line}, which a "
                    "package enclosing the scope runs before the name is used",
                )
        caveats.extend(item for item in above.unread if item not in caveats)
        if layout is not None and layout.scope:
            for target in above.inscope:
                # ``from .app import bootstrap`` in ``svc/__init__.py``: the
                # scope's own module, run before the chain (#879 review).
                relative = PurePosixPath(target).relative_to(layout.scope)
                entry = self._file_entry(self.scope_root / relative.parent, relative.name)
                if entry is None:
                    caveat = f"a package enclosing the scope runs {target}, which could not be read"
                    if caveat not in caveats:
                        caveats.append(caveat)
                    continue
                for item in (entry, *self._enclosing_packages(entry)):
                    if item not in runners:
                        runners.append(item)
        retaining: set[Path] = set()
        dependency_modules: set[str] = set()
        if external_symbol is not None:
            context = self._above_constructor_context(above)
            if context is not None and layout is not None and layout.scope:
                for module in context[1]:
                    if not module.ref.startswith(layout.scope + "/"):
                        continue
                    relative = PurePosixPath(module.ref).relative_to(layout.scope)
                    path = self.scope_root / relative
                    # These are executable import candidates, not merely files
                    # independently discovered by the framework reader. Hand
                    # each back to the original scope and ownership census.
                    for item in (path, *self._enclosing_packages(path)):
                        if item not in runners:
                            if len(runners) >= MAX_PATCH_SCAN_MODULES:
                                raise _Stop(RESOLUTION_LIMIT, "the constructor import closure exceeds its module bound")
                            runners.append(item)
            imports: dict[Path, set[Path]] = {}
            dependency_families = {external_symbol}
            constructor_paths = (_external_constructor_paths(external_symbol) | _external_wrapper_paths(external_symbol)
                                 | _external_decorator_paths(external_symbol)
                                 | _external_constructor_dependency_paths(external_symbol))
            for runner in runners:
                running = self._patch_scan(runner)
                typing_only = _type_checking_only(running, self)
                imports[runner] = set()
                for statement in self._constructor_syntax(running).imports:
                    if id(statement) in typing_only:
                        continue
                    imported_names = [alias.name if isinstance(statement, ast.Import)
                                      else _absolute_import_reference(alias, statement)
                                      for alias in statement.names]
                    dependency_families |= {family for family in _FRAMEWORK_MODULES
                                            if any(name is not None and (name == family or name.startswith(family + "."))
                                                   for name in imported_names)}
                    if any((path := _absolute_import_reference(alias, statement)) is not None
                           and any(path == constructor or constructor.startswith(path + ".")
                                   or path.startswith(constructor + ".") for constructor in constructor_paths)
                           for alias in statement.names):
                        retaining.add(runner)
                    paths, _ = self._imported_paths(running, statement)
                    if any(self._unread_import_candidate(running, statement, alias) for alias in statement.names):
                        # An unread imported object can carry construction
                        # machinery in its globals or ancestry. Preserve that
                        # obligation through local module/function reexports.
                        retaining.add(runner)
                    for path in paths:
                        for item in (path, *self._enclosing_packages(path)):
                            imports[runner].add(item)
                            if item not in runners:
                                if len(runners) >= MAX_PATCH_SCAN_MODULES:
                                    raise _Stop(RESOLUTION_LIMIT, "the constructor import closure exceeds its module bound")
                                runners.append(item)
            # A foreign framework can expose the same Pydantic/typing objects
            # through its globals. Its imports also load implicit dependencies,
            # even when the application only writes an independent class slot.
            dependency_modules = set().union(*(_external_constructor_dependency_modules(family)
                                               for family in dependency_families))
            full |= dependency_modules
            for where, line, key in above.tables:
                table(key, where, line, "a package enclosing the scope", repository_ref=True)
            # Modules, functions, classes and packages can carry an imported
            # constructor in their namespace even without re-exporting its name.
            for _ in range(len(runners) + 1):
                added = {path for path, dependencies in imports.items() if dependencies & retaining}
                added |= {path for path in runners if path.name == "__init__.py"
                          and any(other != path and other.is_relative_to(path.parent) for other in retaining)}
                if added <= retaining:
                    break
                retaining |= added
            issue = self._above_constructor_use(context, external_symbol, retaining)
            if issue is not None:
                raise _Stop(REBOUND_NAME, issue)
        search_limits = self._import_search_limit(runners)
        if external_symbol is not None and search_limits:
            raise _Stop(REBOUND_NAME, search_limits[0])
        caveats.extend(item for item in search_limits if item not in caveats)
        if external_symbol is not None:
            self._constructor_runners = tuple(runners)
            self._annotation_readers.clear()
        for runner in runners:
            scan = self._patched_names(runner)
            if external_symbol is not None:
                running = self._patch_scan(runner)
                # An entry point or importer can supply a sibling dependency
                # even when the constructor lives in a repository-root helper.
                # Prove every bounded caller/import context, not just its owner.
                for dependency in sorted(dependency_modules):
                    issue = self._external_provider_path_issue(running, dependency, [])
                    if issue is not None:
                        raise _Stop(REBOUND_NAME, f"constructor dependency {dependency!r} is unread in {running.ref}: {issue}")
                issue = _external_constructor_use(self, running, external_symbol, retaining)
                if issue is not None:
                    raise _Stop(REBOUND_NAME, issue)
            path_line = self._runtime_attribute_patches(self._patch_scan(runner)).get(PATH_PATCH)
            if path_line is not None:
                # ``__path__.insert(0, ...)`` in a package on the chain: a
                # submodule may be found in another directory (#879 review).
                caveat = (
                    f"{self.ref(runner)}:{path_line} changes __path__, so a module this "
                    "chain imports may be found in another directory"
                )
                if caveat not in caveats:
                    caveats.append(caveat)
            for key, stores in scan.patched.items():
                if key.startswith(MODULE_TABLE_PATCH):
                    for where, line, _ in stores:
                        origins = scan.table_modules.get((key, where, line), ())
                        table(key, where, line, self.ref(runner),
                              marker_module=origins[0] if len(origins) == 1 else None)
            for name in names:
                if external_symbol is not None:
                    continue  # Canonical namespace/class uses are checked above.
                for where, line, targets in scan.patched.get(name, []):
                    if not external_symbol and where == defining.ref and targets is not None and defining.path not in targets:
                        # ``registry.lookup = lookup``: handing the definition on.
                        continue
                    raise _Stop(
                        REBOUND_NAME,
                        f"an attribute named {name!r} is reassigned in {where}:{line}, "
                        f"which {self.ref(runner)} runs before the name is used",
                    )
            caveats.extend(item for item in scan.unread if item not in caveats)
        return tuple(caveats)

    def _above_constructor_context(
        self, above: _AboveScope,
    ) -> tuple[_RepositoryConstructorResolver, list[PythonModule], dict[Path, set[Path]]] | None:
        """All executable candidates in the enclosing snapshot import closure."""
        if not above.modules or self._layout is None:
            return None
        reader = _RepositoryConstructorResolver(self)
        modules = [reader.module(reader.scope_root / ref) for ref in above.modules]
        imports: dict[Path, set[Path]] = {}
        for module in modules:
            typing_only = _type_checking_only(module, reader)
            imports[module.path] = set()
            for statement in ast.walk(module.tree):
                if not isinstance(statement, ast.Import | ast.ImportFrom) or id(statement) in typing_only:
                    continue
                paths, missing = reader._imported_paths(module, statement)
                for stop, _, _ in missing:
                    if stop.reason != MODULE_NOT_FOUND or isinstance(statement, ast.ImportFrom) and statement.level:
                        raise _Stop(stop.reason, f"constructor import ownership is unread above the read scope: {stop.detail}")
                for path in paths:
                    for target in (path, *reader._enclosing_packages(path)):
                        imports[module.path].add(target)
                        if target not in imports and all(item.path != target for item in modules):
                            if len(modules) >= MAX_PATCH_SCAN_MODULES:
                                raise _Stop(RESOLUTION_LIMIT, "the above-scope constructor import closure exceeds its module bound")
                            modules.append(reader.module(target))
        return reader, modules, imports

    def _above_constructor_use(
        self, context: tuple[_RepositoryConstructorResolver, list[PythonModule], dict[Path, set[Path]]] | None,
        family: str, retaining: set[Path],
    ) -> str | None:
        """Check above-scope handles in a separate, snapshot-only namespace."""
        if context is None:
            return None
        assert self._layout is not None
        reader, modules, imports = context
        prefix = self._layout.scope
        held = {reader.scope_root / prefix / self.ref(path) for path in retaining}
        for module in modules:
            typing_only = _type_checking_only(module, reader)
            for statement in ast.walk(module.tree):
                if id(statement) not in typing_only and isinstance(statement, ast.Import | ast.ImportFrom) and any(
                    reader._unread_import_candidate(module, statement, alias) for alias in statement.names
                ):
                    held.add(module.path)
        for _ in range(len(modules) + 1):
            added = {path for path, dependencies in imports.items() if dependencies & held}
            added |= {item.path for item in modules if item.package
                      and any(other != item.path and other.is_relative_to(item.path.parent) for other in held)}
            if added <= held:
                break
            held |= added
        for module in modules:
            if module.ref.startswith(prefix + "/"):
                continue  # In-scope owners belong to the original list reader.
            if PATH_PATCH in reader._runtime_attribute_patches(module):
                return f"constructor module search ownership is unread: {module.ref} changes __path__ above the read scope"
            typing_only = _type_checking_only(module, reader)
            scopes = ScopeIndex(module.tree)
            for statement in ast.walk(module.tree):
                if not isinstance(statement, ast.Import | ast.ImportFrom) or id(statement) in typing_only:
                    continue
                if _type_checking_import_role(module, statement, scopes, reader, typing_only):
                    continue
                for alias in statement.names:
                    imported = _absolute_import_reference(alias, statement)
                    if imported is not None and any(
                        imported == root or imported.startswith(root + ".") or root.startswith(imported + ".")
                        for root in (*_CONSTRUCTOR_IMPORT_MODULES, *_STDLIB_SHARED_DEPENDENCY_PATHS)
                    ):
                        return f"constructor dependency ownership is unread: {module.ref}:{statement.lineno} imports a protected namespace above the read scope"
            issue = _external_constructor_use(reader, module, family, held, allow_owner_routes=False)
            if issue is not None:
                return f"{issue} above the read scope"
            if (reader._constructor_namespace_owners or reader._constructor_container_owners
                    or reader._constructor_member_sinks or reader._constructor_dictionary_sinks
                    or reader._constructor_dictionary_class_sinks
                    or reader._constructor_unused_namespace_sinks
                    or reader._constructor_decorator_owners
                    or reader._constructor_wrapped_operands):
                # These are obligations, not proof. The scope's list reader
                # cannot finish an ownership census in this separate namespace.
                return f"constructor handle ownership is unread in {module.ref} above the read scope"
        return None

    def _enclosing_packages(self, path: Path) -> list[Path]:
        """The package ``__init__`` files importing ``path`` runs first, innermost first."""

        packages: list[Path] = []
        directory = path.parent
        while directory.is_relative_to(self.scope_root):
            init = self._file_entry(directory, "__init__.py")
            if init is not None and init != path:
                packages.append(init)
            if directory == self.scope_root:
                break
            directory = directory.parent
        return packages

    def _patched_names(self, path: Path) -> _PatchScan:
        """What running ``path`` may reassign, directly or through the in-scope
        modules it imports."""

        cached = self._patches.get(path)
        if isinstance(cached, _Stop):
            raise cached
        if cached is not None:
            return cached
        try:
            scan = self._scan_imports(path)
        except _Stop as stop:
            # A module it imports but that cannot be read — a link, a missing
            # file — could patch the name; so it stops, every time (#879
            # review).
            self._patches[path] = stop
            raise
        self._patches[path] = scan
        return scan

    def _scan_imports(self, path: Path) -> _PatchScan:
        runner = self._patch_scan(path)
        modules = [runner]
        unread: list[str] = []
        guarded = _import_guarded(runner.tree)
        typing_only = _type_checking_only(runner, self)
        for node in ast.walk(runner.tree):
            if not isinstance(node, ast.Import | ast.ImportFrom) or id(node) in typing_only:
                continue
            paths, missing = self._imported_paths(runner, node)
            for stop, spelling, names in missing:
                caveat = self._unread_import(
                    runner, node, stop, spelling, names, id(node) in guarded
                )
                if caveat is not None and caveat not in unread:
                    unread.append(caveat)
            for item in paths:
                for module_path in (item, *self._enclosing_packages(item)):
                    if module_path != path:
                        modules.append(self._patch_scan(module_path))
        patched: dict[str, list[tuple[str, int, frozenset[Path] | None]]] = {}
        table_modules: dict[tuple[str, str, int], tuple[PythonModule, ...]] = {}
        for module in modules:
            for dotted, line in self._runtime_attribute_patches(module).items():
                if dotted.startswith(MODULE_TABLE_PATCH):
                    patched.setdefault(dotted, []).append((module.ref, line, None))
                    row = (dotted, module.ref, line)
                    origins = table_modules.get(row, ())
                    if not any(origin is module for origin in origins):
                        table_modules[row] = (*origins, module)
                    continue
                if dotted.startswith(SELF_PATCH):
                    # The module rebinds its own name: it is the target.
                    patched.setdefault(dotted[len(SELF_PATCH):], []).append(
                        (module.ref, line, frozenset({module.path}))
                    )
                    continue
                if dotted in {PATH_PATCH, IMPORT_SEARCH_PATCH}:
                    continue
                # Only an attribute of an imported module can be the definition:
                # ``self.lookup = ...`` in a class, or ``backend.lookup`` on a
                # parameter, reassigns some other object (#879 review).
                root = dotted.split(".", 1)[0]
                imports = _root_imports(module, root)
                if not imports:
                    continue
                found = patched.setdefault(dotted.rsplit(".", 1)[-1], [])
                if any(entry[:2] == (module.ref, line) for entry in found):
                    continue
                found.append((module.ref, line, self._patch_targets(module, imports)))
        return _PatchScan(patched, tuple(unread), table_modules)

    def _patch_targets(
        self, module: PythonModule, imports: list[ast.Import | ast.ImportFrom]
    ) -> frozenset[Path] | None:
        """The in-scope modules a patch's root import names; None when it cannot be located."""

        targets: set[Path] = set()
        for statement in imports:
            paths, missing = self._imported_paths(module, statement)
            if any(stop.reason != MODULE_NOT_FOUND for stop, _, _ in missing):
                return None
            targets.update(paths)
        return frozenset(targets)

    def _unread_import(
        self,
        runner: PythonModule,
        node: ast.Import | ast.ImportFrom,
        stop: _Stop,
        spelling: str,
        names: list[str],
        guarded: bool,
    ) -> str | None:
        """The caveat for one import target no file in the scope provides; raise
        :class:`_Stop` for one that could hide a patch and cannot be named."""

        where = f"{runner.ref}:{node.lineno}"
        relative = isinstance(node, ast.ImportFrom) and bool(node.level)
        if stop.reason == OUTSIDE_SCOPE:
            # Application code above the scope runs here, unread: the binding
            # is named, never established (#879 review).
            return (
                f"{where} imports {spelling!r} from above the read scope, which is not "
                "read and could reassign it"
            )
        if stop.reason != MODULE_NOT_FOUND:
            raise stop
        if not relative and self._repository_provides(spelling.split("."), names):
            return (
                f"{where} imports {spelling!r}, which the repository holds outside the "
                "read scope, which is not read and could reassign it"
            )
        if guarded or not relative:
            # An optional import may be absent; an absolute import no file in
            # the repository provides is a third-party package — the read's
            # boundary, as for every import.
            return None
        last = spelling.rsplit(".", 1)[-1]
        if last.endswith(_GENERATED_SUFFIXES) or last in _GENERATED_NAMES:
            # Generated at build time; it defines data.
            return None
        return (
            f"{where} imports {spelling!r}, which no file in the read scope provides and "
            "which could reassign it"
        )

    def repository_holds(self, dotted: str, names: list[str]) -> bool:
        """Whether the repository holds, anywhere, the module an absolute import
        names: application code outside the read scope, not a third-party
        package (#865)."""

        return self._repository_provides(dotted.split("."), names)

    def _repository_provides(self, parts: list[str], names: list[str]) -> bool:
        """Whether the repository holds the module an absolute import names.

        A module or regular package at the repository root (or ``src/``). A
        directory without ``__init__.py`` only when it holds the submodule
        named: an installed package of the same name wins over a namespace
        directory, so ``agents/`` holding SDK apps is not the ``agents`` that
        ``from agents import Agent`` imports.
        """

        layout = self._layout
        if layout is None or not parts or not parts[0] or parts[0] in _PRELOADED:
            # Loaded before any application code runs: never shadowed.
            return False
        scope_parts = layout.scope.split("/") if layout.scope else []
        for base in self._import_roots():
            prefix = f"{base}/" if base else ""
            top = layout.entries(base)
            if not top:
                continue
            first = parts[0]
            if first in sys.stdlib_module_names and "__init__.py" in top:
                # A regular package is imported through its parent, so its
                # ``types.py`` does not shadow the standard library; a plain
                # directory on the path (``backend/calendar.py``) does
                # (#879 review).
                continue
            if f"{first}.py" in top or first in layout.links(base):
                # A link or a submodule spelled by the import: its code is not
                # read.
                return True
            if first not in top:
                continue
            inside = layout.entries(prefix + first)
            if inside is None:
                continue
            depth = len(base.split("/")) if base else 0
            on_the_way = len(scope_parts) > depth and scope_parts[depth] == first
            # The package on the way to the scope, seen from this root, is the
            # scope's own: ``from agents import Agent`` beside
            # ``app/agents/support`` is the SDK unless ``agents`` there holds
            # the name (#879 review).
            if "__init__.py" in inside and not on_the_way:
                return True
            for path in [parts[1:]] if parts[1:] else [[name] for name in names if name != "*"]:
                directory, entries = prefix + first, inside
                for index, part in enumerate(path):
                    last = index == len(path) - 1
                    if last and f"{part}.py" in entries:
                        return True
                    if part not in entries:
                        break
                    directory = f"{directory}/{part}"
                    entries = layout.entries(directory)
                    if entries is None:
                        break
                    if last:
                        return True
        return False

    def _above_scope(self) -> _AboveScope:
        """The ``__init__.py`` of every package between the repository root and
        the scope, and the modules each imports, read for patches.

        Importing the scope's modules through their package (``svc.app.tools``)
        runs ``svc/__init__.py`` first; its reassignments are what the agent
        receives (#879 review). Read through the repository layout, never
        imported, each file once and within ``MAX_PATCH_SCAN_MODULES``; a
        module it imports is followed as the in-scope reader follows one — the
        packages it runs, the submodules named — with the same exemptions.
        """

        if self._above is not None:
            return self._above
        found = _AboveScope()
        self._above = found
        layout = self._layout
        if layout is None or not layout.scope:
            return found
        scope_prefix = layout.scope + "/"
        parts = layout.scope.split("/")
        type_reader = _RepositoryConstructorResolver(self)
        runners: list[PythonModule] = []
        for length in range(len(parts)):
            directory = "/".join(parts[:length])
            if "__init__.py" not in (layout.entries(directory) or ()):
                continue
            init = f"{directory}/__init__.py" if directory else "__init__.py"
            module = self._layout_module(init)
            if module is None:
                found.unread.append(f"{init} runs before the scope and could not be read")
                continue
            runners.append(module)
        for runner in runners:
            modules = [runner]
            directory = runner.ref.rsplit("/", 1)[0] if "/" in runner.ref else ""
            # Provider lookup needs the enclosing repository's snapshot path,
            # never a repository-relative path in the selected-scope resolver.
            typing_only = _type_checking_only(type_reader.module(type_reader.scope_root / runner.ref), type_reader)
            guarded = _import_guarded(runner.tree)
            for node in ast.walk(runner.tree):
                if not isinstance(node, ast.ImportFrom | ast.Import) or id(node) in typing_only:
                    continue
                for target, spelling in self._layout_targets(directory, node):
                    if target is None:
                        last = spelling.rstrip(".").rsplit(".", 1)[-1]
                        if id(node) in guarded or last in _GENERATED_NAMES or last.endswith(_GENERATED_SUFFIXES):
                            continue
                        found.unread.append(
                            f"{runner.ref}:{node.lineno} imports {spelling!r}, which is not read and "
                            "could reassign it"
                        )
                    elif target.startswith(scope_prefix):
                        # ``from .app import bootstrap`` in ``svc/__init__.py``
                        # runs the scope's own module first (#879 review).
                        if target not in found.inscope:
                            found.inscope.append(target)
                    else:
                        imported = self._layout_module(target)
                        if imported is None:
                            message = (
                                f"the packages enclosing the scope run more than "
                                f"{MAX_PATCH_SCAN_MODULES} modules, past the read bound, and the "
                                "rest are not read"
                                if self._layout_budget_spent(target)
                                else f"{runner.ref}:{node.lineno} runs {target}, which could not be read"
                            )
                            if message not in found.unread:
                                found.unread.append(message)
                        elif imported not in modules:
                            modules.append(imported)
                            if imported not in runners:
                                runners.append(imported)
            for item in modules:
                if item.ref in found.modules:
                    continue
                found.modules[item.ref] = item
                item_directory = item.ref.rsplit("/", 1)[0] if "/" in item.ref else ""
                runtime_item = type_reader.module(type_reader.scope_root / item.ref)
                for dotted, line in type_reader._runtime_attribute_patches(runtime_item).items():
                    if dotted.startswith(MODULE_TABLE_PATCH):
                        found.tables.append((item.ref, line, dotted))
                        continue
                    if dotted.startswith(SELF_PATCH):
                        found.patched.setdefault(dotted[len(SELF_PATCH):], []).append((item.ref, line))
                        continue
                    if dotted == IMPORT_SEARCH_PATCH:
                        found.unread.append(f"{item.ref}:{line} changes or retains import-search machinery above the scope")
                        continue
                    if dotted == PATH_PATCH:
                        # ``__path__.insert(0, ...)`` above the scope: the
                        # scope's own package may be found elsewhere.
                        found.unread.append(
                            f"{item.ref}:{line} changes __path__, so the scope's modules may be "
                            "found in another directory"
                        )
                        continue
                    root = dotted.split(".", 1)[0]
                    imports = _root_imports(item, root)
                    if not imports:
                        continue
                    if not self._other_module(item, item_directory, dotted, imports, scope_prefix):
                        found.patched.setdefault(dotted.rsplit(".", 1)[-1], []).append((item.ref, line))
        if found.modules:
            search_limits = type_reader._import_search_limit(
                [type_reader.scope_root / ref for ref in found.modules]
            )
            found.unread.extend(item for item in search_limits if item not in found.unread)
        return found

    def _other_module(
        self,
        item: PythonModule,
        directory: str,
        dotted: str,
        imports: list[ast.Import | ast.ImportFrom],
        scope_prefix: str,
    ) -> bool:
        """Whether a patch above the scope sets an attribute of another module
        file outside it: ``registry.tools = []`` with ``registry`` a submodule
        its package does not otherwise bind. A longer path
        (``registry.tools.lookup``), a name imported from a module, or a root
        the package also binds could reach the scope (#879 review)."""

        if dotted.count(".") != 1:
            return False
        root = dotted.split(".", 1)[0]
        for statement in imports:
            targets = self._layout_targets(directory, statement)
            if not targets or any(target is None for target, _ in targets):
                return False
            files = [target for target, _ in targets if target is not None]
            alias = next(
                (name for name in statement.names if (name.asname or name.name.split(".", 1)[0]) == root),
                None,
            )
            if alias is None:
                return False
            if isinstance(statement, ast.ImportFrom):
                # ``from P import M``: ``M`` must be P's submodule file, and P
                # must bind nothing else under that name.
                module = next(
                    (
                        file
                        for file in files
                        if file.endswith((f"/{alias.name}.py", f"/{alias.name}/__init__.py"))
                        or file in {f"{alias.name}.py", f"{alias.name}/__init__.py"}
                    ),
                    None,
                )
                if module is None:
                    return False
                module_dir = module[: -len("/__init__.py")] if module.endswith("/__init__.py") else module[: -len(".py")]
                parent = module_dir.rsplit("/", 1)[0] if "/" in module_dir else ""
                if "__init__.py" in (self._layout.entries(parent) or ()):  # type: ignore[union-attr]
                    holder = self._layout_module(f"{parent}/__init__.py" if parent else "__init__.py")
                    if holder is None or any(
                        not (
                            isinstance(binding.statement, ast.ImportFrom)
                            and binding.statement.level
                            and not binding.statement.module
                        )
                        for binding in holder.bindings.get(alias.name, [])
                    ):
                        # ``from .app import tools as helpers`` in the package
                        # makes ``helpers`` its attribute, not the submodule.
                        return False
            else:
                module = files[-1]
            if module.startswith(scope_prefix):
                return False
        return True

    def _layout_budget_spent(self, path: str) -> bool:
        return path in self._over_budget

    def _layout_module(self, path: str) -> PythonModule | None:
        """One repository file outside the scope, read and parsed once."""

        assert self._layout is not None
        if path in self._layout_modules:
            return self._layout_modules[path]
        module: PythonModule | None = None
        if len(self._layout_modules) >= MAX_PATCH_SCAN_MODULES:
            self._over_budget.add(path)
        else:
            text = self._layout.read(path)
            if text is not None:
                try:
                    tree = ast.parse(text, filename=path)
                except (SyntaxError, ValueError, RecursionError):
                    tree = None
                if tree is not None:
                    # Named by its repository path: it lies outside the scope.
                    module = _module(Path(path), path, tree, text)
        self._layout_modules[path] = module
        return module

    def _layout_targets(
        self, directory: str, node: ast.Import | ast.ImportFrom
    ) -> list[tuple[str | None, str]]:
        """The repository files one import in ``directory`` runs — every
        package on the way, the module, and each submodule named — or None
        for a relative one that cannot be found. An absolute import the
        repository does not hold is a third-party package: nothing to read."""

        assert self._layout is not None
        layout = self._layout

        def locate(base: str, parts: list[str]) -> list[str] | None:
            """The files importing ``parts`` from ``base`` runs; None if absent."""

            current, files = base, []
            for part in parts:
                entries = layout.entries(current) or frozenset()
                directory = f"{current}/{part}" if current else part
                inside = layout.entries(directory) if part in entries else None
                package = inside is not None and "__init__.py" in inside
                if not package and f"{part}.py" in entries:
                    # A regular package, then a module, then a namespace
                    # portion: ``config.py`` wins over a ``config/`` data
                    # directory beside it (#879 review).
                    return [*files, f"{directory}.py"]
                if inside is None:
                    return None
                current = directory
                if package:
                    files.append(f"{current}/__init__.py")
            return files

        def named(base_files: list[str], base_dir: str, names: list[str]) -> list[str]:
            extra: list[str] = []
            for name in names:
                if name == "*":
                    continue
                inner = locate(base_dir, [name])
                if inner:
                    extra += inner[-1:]
            return [*base_files, *extra]

        results: list[tuple[str | None, str]] = []
        if isinstance(node, ast.ImportFrom) and node.level:
            base_parts = directory.split("/") if directory else []
            base_parts = base_parts[: len(base_parts) - (node.level - 1)] if node.level > 1 else base_parts
            base = "/".join(base_parts)
            spelling = "." * node.level + (node.module or "")
            module_parts = node.module.split(".") if node.module else []
            located = locate(base, module_parts) if module_parts else []
            if located is None:
                results.append((None, spelling))
                return results
            container = "/".join([base, *module_parts]) if base else "/".join(module_parts)
            files = named(located, container if module_parts else base, [alias.name for alias in node.names])
            results += [(item, spelling) for item in files]
            return results
        dotted = [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
        for name in dotted:
            if not name or name.split(".", 1)[0] in _PRELOADED:
                continue
            for root in self._import_roots():
                located = locate(root, name.split("."))
                if located is not None:
                    container = "/".join([root, *name.split(".")]) if root else "/".join(name.split("."))
                    names = [alias.name for alias in node.names] if isinstance(node, ast.ImportFrom) else []
                    results += [(item, name) for item in named(located, container, names)]
                    break
        return results

    def _import_roots(self) -> list[str]:
        """Where an absolute import may be looked up from: the repository root,
        ``src/``, and every directory between the root and the scope
        (``backend/`` of ``backend/app``), innermost last."""

        roots = ["", "src"]
        if self._layout is not None and self._layout.scope:
            parts = self._layout.scope.split("/")
            # A package directory can be on the path too — a service run from
            # ``backend/`` with a stray ``backend/__init__.py`` (#879 review).
            roots += ["/".join(parts[:length]) for length in range(1, len(parts))]
        return list(dict.fromkeys(roots))

    def _imported_paths(
        self, runner: PythonModule, node: ast.Import | ast.ImportFrom
    ) -> tuple[list[Path], list[tuple[_Stop, str, list[str]]]]:
        """The in-scope module files one import statement runs, and each target
        it could not locate with why.

        Every location an ambiguous absolute name could mean is read: which one
        the import system takes depends on the path, and any of them could
        patch.
        """

        paths: list[Path] = []
        missing: list[tuple[_Stop, str, list[str]]] = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in _PRELOADED:
                    continue  # The startup module wins over repository siblings.
                try:
                    containers = self._absolute_candidates(runner, alias.name)
                except _Stop as stop:
                    missing.append((stop, alias.name, []))
                    continue
                paths.extend(item.module_path for item in containers if item.module_path)
            return paths, missing
        if not node.level and (node.module or "").split(".", 1)[0] in _PRELOADED:
            return paths, missing
        spelling = _from_spelling(node)
        if not node.module:
            # ``from .. import patches``: the modules are the names.
            spelling += ", ".join(alias.name for alias in node.names)
        try:
            containers = (
                [self._from_base(runner, node)]
                if node.level
                else self._absolute_candidates(runner, node.module or "")
            )
        except _Stop as stop:
            missing.append((stop, spelling, [alias.name for alias in node.names]))
            return paths, missing
        for container in containers:
            if container.module_path is not None:
                paths.append(container.module_path)
            if container.package or container.module_path is None:
                # ``from pkg import name`` runs ``pkg/name.py`` when it is a
                # submodule.
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    found = self._locate(container.directory, [alias.name], spelling=alias.name)
                    if found is not None and found.module_path is not None:
                        paths.append(found.module_path)
        return paths, missing

    def _patch_scan(self, path: Path) -> PythonModule:
        """A module that runs before a name is used, read only for its patches.

        Parsed on its own bounded budget: a package that re-exports seventy
        modules must not use up the resolution budget of every tool in it
        (#879 review). Read through the same snapshot-aware reader.
        """

        self._import_search_module_guard(path)
        cached = self._modules.get(path)
        if isinstance(cached, PythonModule):
            return cached
        scanned = self._scanned.get(path)
        if isinstance(scanned, _Stop):
            raise scanned
        if scanned is not None:
            return scanned
        if len(self._scanned) >= MAX_PATCH_SCAN_MODULES:
            raise _Stop(
                RESOLUTION_LIMIT,
                f"checking the code that runs before it for patches would read more "
                f"than {MAX_PATCH_SCAN_MODULES} modules",
            )
        ref = self.ref(path)
        try:
            text = load_text_file(path)
            tree = ast.parse(text, filename=str(path))
        except (InputParseError, SyntaxError, ValueError, RecursionError):
            stop = _Stop(UNREADABLE_MODULE, f"{ref} could not be read or parsed")
            self._scanned[path] = stop
            raise stop from None
        module = _module(path, ref, tree, text)
        self._scanned[path] = module
        return module

    def resolve(self, module: PythonModule, reference: str) -> Resolution:
        """Resolve ``reference`` (``name`` or ``module.attr...``) in ``module``."""

        parts = reference.split(".")
        steps: list[dict[str, Any]] = []
        try:
            self._no_attribute_patch(module, parts)
            outcome = self._in_module(module, parts, steps, set())
            caveats = self._no_import_patch(outcome, steps)
        except _Stop as stop:
            return Resolution(
                reference=reference,
                reason=stop.reason,
                detail=stop.detail,
                steps=tuple(steps),
            )
        return Resolution(reference=reference, steps=tuple(steps), caveats=caveats, **outcome)

    def _in_module(
        self,
        module: PythonModule,
        parts: list[str],
        steps: list[dict[str, Any]],
        seen: set[tuple[Path, tuple[str, ...]]],
    ) -> dict[str, Any]:
        name, rest = parts[0], parts[1:]
        key = (module.path, tuple(parts))
        if key in seen:
            raise _Stop(
                IMPORT_CYCLE,
                f"the import chain for {'.'.join(parts)!r} returns to {module.ref}",
            )
        seen.add(key)
        if len(steps) >= MAX_STEPS:
            raise _Stop(
                RESOLUTION_LIMIT,
                f"the import chain is longer than {MAX_STEPS} steps",
            )
        bindings = module.bindings.get(name, [])
        if module.star_import:
            raise _Stop(
                STAR_IMPORT,
                f"{module.ref} has a wildcard import that may rebind {name!r}",
            )
        if not bindings:
            if module.package:
                container = self._locate(module.path.parent, [name], spelling=name)
                if container is not None:
                    steps.append(_fallthrough(module, name, patches=self._runtime_attribute_patches(module)))
                    return self._member(container, rest, steps, seen, spelling=name)
            raise _Stop(
                NOT_BOUND if not steps else NAME_NOT_DEFINED,
                f"{module.ref} does not define {name!r}",
            )
        if len(bindings) > 1 and _same_package_imports(bindings, name):
            # ``import a.b`` and ``import a.c`` both bind ``a`` to one package:
            # not a rebinding. Follow the statement that imports the longest
            # prefix of this reference.
            bindings = [_longest_import_prefix(bindings, parts)]
        if len(bindings) > 1:
            lines = ", ".join(
                str(line) for line in sorted({_line(item.statement) for item in bindings})
            )
            raise _Stop(
                REBOUND_NAME,
                f"{name!r} is bound more than once in {module.ref} (lines {lines})",
            )
        binding = bindings[0]
        line = _line(binding.statement)
        if not binding.top_level:
            raise _Stop(
                CONDITIONAL_BINDING,
                f"{name!r} is bound only inside a compound statement or from a "
                f"function in {module.ref}:{line}",
            )
        node = binding.node
        step = {"path": module.ref, "line": line, "name": name, "sha256": module.sha256}
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            steps.append({**step, "binding": "definition"})
            if rest and not self._constructor_reference_mode:
                raise _Stop(
                    NOT_A_FUNCTION,
                    f"{'.'.join(parts)!r} reads an attribute of function {name!r} "
                    f"in {module.ref}:{line}",
                )
            return {"module": module, "definition": node, **({"constructor_callable": not rest} if self._constructor_reference_mode else {})}
        if isinstance(node, ast.alias):
            statement = binding.statement
            steps.append({**step, "binding": "import"})
            assert isinstance(statement, ast.Import | ast.ImportFrom)
            return self._through_import(module, statement, node, parts, steps, seen)
        return self._not_an_import(module, binding, node, name, parts, rest, step, steps, seen, line)

    def _through_import(
        self,
        module: PythonModule,
        statement: ast.Import | ast.ImportFrom,
        node: ast.alias,
        parts: list[str],
        steps: list[dict[str, Any]],
        seen: set[tuple[Path, tuple[str, ...]]],
    ) -> dict[str, Any]:
        rest = parts[1:]
        if self._constructor_reference_mode:
            external = _absolute_import_reference(node, statement)
            projected = external + ("." + ".".join(rest) if rest else "") if external is not None else None
            if projected is not None and (projected == "pathlib.Path" or any(projected == family or projected.startswith(family + ".")
                                            or family.startswith(projected + ".")
                                            for family in (*_CONSTRUCTOR_IMPORT_MODULES, *_STDLIB_SHARED_DEPENDENCY_PATHS))):
                issue = self._external_provider_issue(module, statement, node)
                if issue is not None:
                    return {"local_constructor_provider": issue}
                return {"external_constructor": projected}
            if projected is not None and projected.split(".", 1)[0] in _PRELOADED:
                return {"constructor_unread": MODULE_NOT_FOUND, "unread_external_handle": True,
                        "detail": f"{projected!r} is supplied by a startup module, not repository source",
                        **({"import_search_handle": projected} if projected.split(".", 1)[0] in {"sys", "site"} else {})}
        if isinstance(statement, ast.Import):
            imported = node.name.split(".")
            if not node.asname and len(imported) > 1 and parts[: len(imported)] == imported:
                # ``import a.b`` then ``a.b.f``: the import system sets ``a.b``
                # to the submodule after ``a/__init__`` runs, so a binding of
                # ``b`` in the package cannot answer (#879 review).
                container = self._absolute(module, node.name)
                return self._member(
                    container, parts[len(imported):], steps, seen, spelling=node.name
                )
            dotted = node.name if node.asname else imported[0]
            container = self._absolute(module, dotted)
            return self._member(container, rest, steps, seen, spelling=dotted)
        container = self._from_base(module, statement)
        return self._member(
            container,
            [node.name, *rest],
            steps,
            seen,
            spelling=_from_spelling(statement),
        )

    def _not_an_import(
        self,
        module: PythonModule,
        binding: _Binding,
        node: ast.AST,
        name: str,
        parts: list[str],
        rest: list[str],
        step: dict[str, Any],
        steps: list[dict[str, Any]],
        seen: set[tuple[Path, tuple[str, ...]]],
        line: int,
    ) -> dict[str, Any]:
        statement = binding.statement
        if (
            isinstance(statement, ast.Assign | ast.AnnAssign)
            and isinstance(node, ast.Name)
            and statement.value is not None
        ):
            target = _dotted(statement.value)
            if target is not None:
                steps.append({**step, "binding": "alias"})
                return self._in_module(module, [*target, *rest], steps, seen)
            steps.append({**step, "binding": "value"})
            if rest:
                raise _Stop(
                    NOT_A_FUNCTION,
                    f"{'.'.join(parts)!r} reads an attribute of a value assigned "
                    f"in {module.ref}:{line}",
                )
            return {"module": module, "value": statement.value,
                    "reason": NOT_A_FUNCTION,
                    "detail": f"{name!r} in {module.ref}:{line} is assigned a "
                              "value, not a function definition"}
        if self._constructor_reference_mode and isinstance(node, ast.ClassDef):
            return {"module": module, "retained_class": node}
        kind = "class" if isinstance(node, ast.ClassDef) else "binding"
        raise _Stop(
            NOT_A_FUNCTION,
            f"{name!r} in {module.ref}:{line} is a {kind}, not a function definition",
        )

    def _member(
        self,
        container: _Container,
        parts: list[str],
        steps: list[dict[str, Any]],
        seen: set[tuple[Path, tuple[str, ...]]],
        *,
        spelling: str,
    ) -> dict[str, Any]:
        """Continue ``parts`` inside a located module or namespace package."""

        if not parts:
            if self._constructor_reference_mode:
                return {"retained_namespace": container.module_path or container.directory}
            raise _Stop(
                NOT_A_FUNCTION,
                f"{spelling!r} names a module, not a function definition",
            )
        if container.module_path is not None:
            module = self.module(container.module_path)
            name = parts[0]
            own_submodule = container.package and _imports_own_submodule(module, name)
            if not own_submodule and (
                name in module.bindings or module.star_import or not container.package
            ):
                return self._in_module(module, parts, steps, seen)
            steps.append(_fallthrough(module, name, patches=self._runtime_attribute_patches(module)))
        submodule = self._locate(container.directory, [parts[0]], spelling=parts[0])
        if submodule is None:
            where = (
                self.ref(container.module_path)
                if container.module_path is not None
                else f"namespace package {self._display_dir(container.directory)}"
            )
            raise _Stop(NAME_NOT_DEFINED, f"{where} does not define {parts[0]!r}")
        return self._member(
            submodule, parts[1:], steps, seen, spelling=_join(spelling, parts[0])
        )

    def _from_base(self, module: PythonModule, statement: ast.ImportFrom) -> _Container:
        spelling = _from_spelling(statement)
        if statement.level == 0:
            return self._absolute(module, statement.module or "")
        base = module.path.parent
        for _ in range(statement.level - 1):
            if base == self.scope_root:
                raise _Stop(
                    OUTSIDE_SCOPE,
                    f"the relative import {spelling!r} in {module.ref} climbs above "
                    "the read scope",
                )
            base = base.parent
        if not statement.module:
            init = self._file_entry(base, "__init__.py")
            return _Container(directory=base, module_path=init, package=init is not None)
        container = self._locate(base, statement.module.split("."), spelling=spelling)
        if container is None:
            raise _Stop(
                MODULE_NOT_FOUND,
                f"no file inside the read scope provides {spelling!r} imported "
                f"by {module.ref}",
            )
        return container

    def _installed_candidates(self, module: PythonModule, dotted: str) -> list[_Container]:
        """Where a name an installed package provides could be found from ``module``.

        :meth:`_absolute_candidates` treats every directory from the module's
        own up to the scope as a place an import could start from. A directory
        that is a regular package of the module's own is not one, unless the
        module sits directly in it (a script's directory): the import system
        reaches such a package through its parent. ``src/opencmo/agents/blog.py``
        importing ``agents`` is the SDK, not its ``opencmo/agents`` sibling.
        """
        found = self._absolute_candidates(module, dotted)
        depth = len(dotted.split("."))
        chain: set[Path] = set()
        directory = module.path.parent
        while True:
            if directory != module.path.parent and self._file_entry(directory, "__init__.py") is not None:
                chain.add(directory)
            if directory == self.scope_root:
                break
            directory = directory.parent
        kept = []
        for container in found:
            location = container.module_path or container.directory
            base = location.parent if location.name == "__init__.py" else location.with_suffix("") if location.suffix == ".py" else location
            ancestors = base.parents
            root = ancestors[depth - 1] if len(ancestors) >= depth else None
            if root is None or root not in chain:
                kept.append(container)
        if not kept:
            raise _Stop(MODULE_NOT_FOUND, f"no file inside the read scope provides module {dotted!r} imported by {module.ref}")
        return kept

    def _installed_absolute(self, module: PythonModule, dotted: str) -> _Container:
        candidates = self._installed_candidates(module, dotted)
        if len(candidates) > 1:
            raise _Stop(AMBIGUOUS_MODULE, f"module {dotted!r} imported by {module.ref} matches more than one location in the read scope")
        return candidates[0]

    def _absolute(self, module: PythonModule, dotted: str) -> _Container:
        candidates = self._absolute_candidates(module, dotted)
        if len(candidates) > 1:
            names = ", ".join(
                sorted(
                    self.ref(item.module_path)
                    if item.module_path is not None
                    else self._display_dir(item.directory)
                    for item in candidates
                )
            )
            raise _Stop(
                AMBIGUOUS_MODULE,
                f"module {dotted!r} imported by {module.ref} matches more than one "
                f"location in the read scope: {names}",
            )
        return candidates[0]

    def _absolute_candidates(self, module: PythonModule, dotted: str) -> list[_Container]:
        parts = dotted.split(".")
        roots: list[tuple[Path, list[str]]] = []
        directory = module.path.parent
        while True:
            roots.append((directory, parts))
            if directory == self.scope_root:
                break
            directory = directory.parent
        # The scope root is itself a package: ``from app.x import f`` inside
        # scope ``app`` names ``app/x.py``. Only that package's own name is
        # looked up above the scope, and the target is inside it.
        if (
            parts[0] == self.scope_root.name
            and self._file_entry(self.scope_root, "__init__.py") is not None
        ):
            roots.append((self.scope_root, parts[1:]))
        # The scope spelled from the repository root (``svc.app.tools`` with
        # scope ``svc/app``, or ``app.tools`` under ``src/app``): its own files.
        if self._layout is not None and self._layout.scope:
            layout = self._layout
            for base in self._import_roots():
                if base and not layout.scope.startswith(base + "/"):
                    continue
                prefix = layout.scope[len(base) + 1 :].split("/") if base else layout.scope.split("/")
                top = f"{base}/{prefix[0]}" if base else prefix[0]
                # Only a regular package: an installed package of the same name
                # wins over a namespace directory (#879 review).
                if parts[: len(prefix)] == prefix and "__init__.py" in (layout.entries(top) or ()):
                    roots.append((self.scope_root, parts[len(prefix):]))
        found: dict[Path, _Container] = {}
        for root, remaining in roots:
            if not remaining:
                init = self._file_entry(root, "__init__.py")
                container = _Container(directory=root, module_path=init, package=True)
            else:
                container = self._locate(root, remaining, spelling=dotted)
            if container is None:
                continue
            identity = container.module_path or container.directory
            found.setdefault(identity, container)
        if not found:
            raise _Stop(
                MODULE_NOT_FOUND,
                f"no file inside the read scope provides module {dotted!r} "
                f"imported by {module.ref}",
            )
        # A namespace directory is the weakest match; a module file anywhere
        # else wins over it exactly as the import system would prefer it.
        files = [item for item in found.values() if item.module_path is not None]
        return files or list(found.values())

    def _locate(self, base: Path, parts: list[str], *, spelling: str) -> _Container | None:
        """Find ``parts`` under ``base`` with the import system's precedence.

        A regular package (``part/__init__.py``) wins over a module file
        (``part.py``), which wins over a namespace directory. Raises
        :class:`_Stop` for a link; returns None when nothing matches.
        """

        current = base
        container: _Container | None = None
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            directory_kind = self._kind(current, part)
            file_kind = self._kind(current, f"{part}.py")
            if "link" in (directory_kind, file_kind):
                raise _Stop(
                    LINKED_MODULE,
                    f"module path {self._display_dir(current / part)} for "
                    f"{spelling!r} is a symbolic link, which is not followed",
                )
            if directory_kind == "dir":
                init_kind = self._kind(current / part, "__init__.py")
                if init_kind == "link":
                    raise _Stop(
                        LINKED_MODULE,
                        f"{self._display_dir(current / part)}/__init__.py is a "
                        "symbolic link, which is not followed",
                    )
                if init_kind == "file":
                    current = current / part
                    container = _Container(
                        directory=current,
                        module_path=current / "__init__.py",
                        package=True,
                    )
                    continue
            if file_kind == "file":
                if not last:
                    return None
                return _Container(directory=current, module_path=current / f"{part}.py")
            if directory_kind == "dir":
                current = current / part
                container = _Container(directory=current)
                continue
            return None
        return container

    def _file_entry(self, directory: Path, name: str) -> Path | None:
        kind = self._kind(directory, name)
        if kind == "link":
            raise _Stop(
                LINKED_MODULE,
                f"{self._display_dir(directory)}/{name} is a symbolic link, "
                "which is not followed",
            )
        return directory / name if kind == "file" else None

    def _kind(self, directory: Path, name: str) -> str | None:
        """Classify one exactly-spelled entry without following links."""

        names = self._listing(directory)
        if names is None or name not in names:
            return None
        try:
            mode = (directory / name).lstat().st_mode
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            return "link"
        if stat.S_ISDIR(mode):
            return "dir"
        if stat.S_ISREG(mode):
            return "file"
        return None

    def _listing(self, directory: Path) -> frozenset[str] | None:
        if directory in self._listings:
            return self._listings[directory]
        names: frozenset[str] | None
        if not directory.is_relative_to(self.scope_root):
            names = None
        else:
            try:
                names = frozenset(child.name for child in list_input_directory(directory))
            except InputParseError:
                names = None
        self._listings[directory] = names
        return names

    def _display_dir(self, directory: Path) -> str:
        try:
            relative = directory.relative_to(self.scope_root).as_posix()
        except ValueError:
            return directory.as_posix()
        return relative or "."


def _constructor_structure_original(
    originals: Mapping[Path, tuple[PythonModule, PythonModule, ast.Module]], module: PythonModule,
) -> PythonModule | None:
    """A retained wrapper may reuse only its original immutable AST roles."""
    row = originals.get(module.path)
    if row is not None and row[0] is module and module.tree is row[2] and row[1].tree is row[2]:
        return row[1]
    return None


class _CapturedImportSearchResolver(ImportResolver):
    """Identity-only search reader over an already accounted source context.

    Keep the exact snapshot namespace and its structural lookup methods. No
    semantic, patch, or ownership cache is inherited from the source reader.
    Missing text is refused before either module cache can answer.
    """

    def __init__(self, source: ImportResolver, modules: list[PythonModule]) -> None:
        super().__init__(source.scope_root)
        self._source = source
        self._layout = source._layout
        self._modules.update({module.path: module for module in modules})
        self._scanned.update({module.path: module for module in modules})
        self._constructor_structure_originals = {module.path: (module, module, module.tree) for module in modules}
        self._import_search_captured = MappingProxyType(
            {module.path: module.text for module in modules}
        )

    def __post_init__(self) -> None:
        pass  # A synthetic source namespace must never be normalized on disk.

    def _constructor_scope(self, module: PythonModule) -> ScopeIndex:
        original = _constructor_structure_original(self._constructor_structure_originals, module)
        return self._source._constructor_scope(original) if original is not None else super()._constructor_scope(module)

    def _constructor_syntax(self, module: PythonModule) -> _ConstructorSyntax:
        original = _constructor_structure_original(self._constructor_structure_originals, module)
        return self._source._constructor_syntax(original) if original is not None else super()._constructor_syntax(module)


    def _listing(self, directory: Path) -> frozenset[str] | None:
        return self._source._listing(directory)

    def _kind(self, directory: Path, name: str) -> str | None:
        return self._source._kind(directory, name)

    def _absolute_candidates(self, module: PythonModule, dotted: str) -> list[_Container]:
        return self._source._absolute_candidates(module, dotted)


class _RepositoryConstructorResolver(ImportResolver):
    """Private ownership reader; every lookup uses the same repository snapshot.

    Repository-relative AST paths must never enter the scope-bound resolver.
    The synthetic root gives its path operations one bounded absolute namespace;
    no method here consults that namespace on disk or publishes tool evidence.
    """

    def __init__(self, source: ImportResolver) -> None:
        super().__init__(Path("/__agents_shipgate_repository__"))
        assert source._layout is not None
        self._source = source
        layout = source._layout
        self._layout = RepositoryLayout("", layout.entries, layout.links, layout.read)
        self._constructor_structure_originals: dict[Path, tuple[PythonModule, PythonModule, ast.Module]] = {}

    def __post_init__(self) -> None:
        pass  # The synthetic namespace is not a disk checkout.

    def _constructor_scope(self, module: PythonModule) -> ScopeIndex:
        original = _constructor_structure_original(self._constructor_structure_originals, module)
        return self._source._constructor_scope(original) if original is not None else super()._constructor_scope(module)

    def _constructor_syntax(self, module: PythonModule) -> _ConstructorSyntax:
        original = _constructor_structure_original(self._constructor_structure_originals, module)
        return self._source._constructor_syntax(original) if original is not None else super()._constructor_syntax(module)


    def _patch_scan(self, path: Path) -> PythonModule:
        return self.module(path)  # Repository paths are snapshot-only, never disk paths.

    def module(self, path: Path) -> PythonModule:
        self._import_search_module_guard(path)
        if not path.is_relative_to(self.scope_root):
            raise _Stop(OUTSIDE_SCOPE, "the repository ownership lookup leaves its snapshot")
        cached = self._modules.get(path)
        if isinstance(cached, PythonModule):
            return cached
        original = self._source._layout_module(self.ref(path))
        if original is None:
            reason = RESOLUTION_LIMIT if self._source._layout_budget_spent(self.ref(path)) else UNREADABLE_MODULE
            raise _Stop(reason, f"{self.ref(path)} could not be read for above-scope constructor ownership")
        module = _module(path, original.ref, original.tree, original.text)
        self._modules[path] = module
        self._constructor_structure_originals[path] = (module, original, original.tree)
        return module

    def _listing(self, directory: Path) -> frozenset[str] | None:
        assert self._layout is not None
        if not directory.is_relative_to(self.scope_root):
            return None
        ref = self.ref(directory)
        return self._layout.entries("" if ref == "." else ref)

    def _kind(self, directory: Path, name: str) -> str | None:
        assert self._layout is not None
        if name not in (self._listing(directory) or ()):
            return None
        ref = self.ref(directory)
        ref = "" if ref == "." else ref
        if name in self._layout.links(ref):
            return "link"
        target = f"{ref}/{name}" if ref else name
        if self._layout.entries(target) is not None:
            return "dir"
        if name.endswith(".py"):
            return "file"  # Listed blobs remain candidates even past the read cap.
        raise _Stop(UNREADABLE_MODULE, f"the listed repository entry {target!r} cannot be classified for constructor ownership")

    def _absolute_candidates(self, module: PythonModule, dotted: str) -> list[_Container]:
        try:
            found = super()._absolute_candidates(module, dotted)
        except _Stop as stop:
            if stop.reason != MODULE_NOT_FOUND:
                raise
            found = []
        by_path = {item.module_path or item.directory: item for item in found}
        for root in self._source._import_roots():
            item = self._locate(self.scope_root / root, dotted.split("."), spelling=dotted)
            if item is not None:
                by_path.setdefault(item.module_path or item.directory, item)
        if not by_path:
            raise _Stop(MODULE_NOT_FOUND, f"the repository snapshot does not provide {dotted!r} imported by {module.ref}")
        files = [item for item in by_path.values() if item.module_path is not None]
        return files or list(by_path.values())


def _single_import(statement: ast.Import | ast.ImportFrom, alias: ast.alias) -> ast.Import | ast.ImportFrom:
    """One candidate's import, excluding unrelated aliases in the statement."""
    if isinstance(statement, ast.Import):
        return ast.Import(names=[alias])
    return ast.ImportFrom(module=statement.module, names=[alias], level=statement.level)


def _import_guarded(tree: ast.Module) -> set[int]:
    """Imports inside a ``try`` whose handler catches ``ImportError``."""

    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches = any(
            handler.type is None
            or any(
                isinstance(kind, ast.Name)
                and kind.id in {"ImportError", "ModuleNotFoundError", "Exception"}
                for kind in (
                    handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
                )
            )
            for handler in node.handlers
        )
        if catches:
            for statement in node.body:
                guarded.update(id(child) for child in ast.walk(statement))
    return guarded


_TYPING_MODULES = frozenset({"typing", "typing_extensions"})


def _canonical_type_checking_test(
    module: PythonModule, test: ast.expr, scopes: ScopeIndex, resolver: ImportResolver,
) -> bool:
    """One directly tested, stable canonical false flag in its actual scope.

    A parameter, local assignment, closure or nonlocal binding wins over the
    module's import. Provider proof is mandatory even for the familiar spelling;
    a repository's ``typing.py`` is executable source, not the standard flag.
    """
    if module.star_import or any(key.endswith(".TYPE_CHECKING") for key in module.attribute_patches):
        return False
    spelling = reference_spelling(test)
    if spelling is None:
        return False
    head, _, rest = spelling.partition(".")
    if rest not in {"", "TYPE_CHECKING"}:
        return False
    from agents_shipgate.inputs.list_expressions import evaluation_site
    bindings = scopes.enclosing_bindings(evaluation_site(scopes, test), head)
    bindings = bindings or [binding.node for binding in module.bindings.get(head, [])]
    if len(bindings) != 1 or not isinstance(alias := bindings[0], ast.alias):
        return False
    statement = scopes.statement_of(alias)
    if (not isinstance(statement, ast.Import | ast.ImportFrom)
            or not isinstance(scopes.parents.get(statement), ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            or statement.lineno > test.lineno):
        return False
    canonical = (
        isinstance(statement, ast.Import) and bool(rest) and alias.name in _TYPING_MODULES
        or isinstance(statement, ast.ImportFrom) and not rest and not statement.level
        and statement.module in _TYPING_MODULES and alias.name == "TYPE_CHECKING"
    )
    if not canonical:
        return False
    try:
        return resolver._external_provider_issue(module, statement, alias) is None
    except _Stop:
        return False  # Unread or ambiguous providers cannot prove a dead branch.


def _type_checking_only(module: PythonModule, resolver: ImportResolver, scopes: ScopeIndex | None = None) -> set[int]:
    """Nodes under a proved direct ``if TYPE_CHECKING:`` runtime-false guard."""
    scopes = scopes or ScopeIndex(module.tree)

    skipped: set[int] = set()
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.If):
            continue
        if _canonical_type_checking_test(module, node.test, scopes, resolver):
            for statement in node.body:
                skipped.update(id(child) for child in ast.walk(statement))
    return skipped


def _type_checking_import_role(
    module: PythonModule, statement: ast.Import | ast.ImportFrom,
    scopes: ScopeIndex, resolver: ImportResolver, excluded: set[int],
) -> bool:
    """Every actual use of this canonical import is its proved boolean role.

    This narrow above-scope role grants no typing namespace or flag retention.
    Unused, shadowed, mixed and mutated imports keep the protected-import limit.
    """
    from agents_shipgate.inputs.list_expressions import evaluation_site
    for alias in statement.names:
        if not (
            isinstance(statement, ast.Import) and alias.name in _TYPING_MODULES
            or isinstance(statement, ast.ImportFrom) and not statement.level
            and statement.module in _TYPING_MODULES and alias.name == "TYPE_CHECKING"
        ):
            return False
        head = alias.asname or alias.name
        used = False
        for node in ast.walk(module.tree):
            if id(node) in excluded or not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load) or node.id != head:
                continue
            bindings = scopes.enclosing_bindings(evaluation_site(scopes, node), head)
            bindings = bindings or [binding.node for binding in module.bindings.get(head, [])]
            if alias not in bindings:
                continue  # A different lexical binding does not use this import.
            if bindings != [alias]:
                return False
            test = scopes.parents.get(node) if isinstance(statement, ast.Import) else node
            parent = scopes.parents.get(test)
            if not (isinstance(test, ast.Name | ast.Attribute) and isinstance(parent, ast.If)
                    and parent.test is test and _canonical_type_checking_test(module, test, scopes, resolver)):
                return False
            used = True
        if not used:
            return False
    return bool(statement.names)


def replaces_builtin_namespace(node: ast.AST, scopes: ScopeIndex) -> bool:
    """A binding of the module's special builtin namespace, including globals.

    Replacing this dictionary/module changes what an otherwise unshadowed
    builtin name means. Import aliases and definition names bind it without
    an ast.Name Store. Ordinary local/class stores retain their lexical scope.
    """
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        name = node.id
    elif isinstance(node, ast.alias):
        name = node.asname or node.name.split(".", 1)[0]
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
        name = node.name
    elif isinstance(node, ast.MatchMapping):
        name = node.rest
    else:
        return False
    if name != "__builtins__":
        return False
    from agents_shipgate.inputs.list_expressions import evaluation_site
    site = evaluation_site(scopes, node)
    if isinstance(scopes.parents.get(node), ast.NamedExpr):
        # A walrus binds around comprehension scopes, including inside a
        # lambda whose body ScopeIndex otherwise deliberately does not index.
        # Its header still runs where evaluation_site placed it.
        current = scopes.parents.get(site)
        while current is not None and not isinstance(current, ast.Module):
            if isinstance(current, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
                current = scopes.parents.get(evaluation_site(scopes, current))
                continue
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
                _, declared_global, declared_nonlocal = scopes._scope(current)
                if name in declared_global:
                    return True
                if name in declared_nonlocal:
                    return False
                return False
            current = scopes.parents.get(current)
        return True
    if scopes.enclosing_bindings(site, name):
        return False
    current = scopes.parents.get(site)
    while current is not None and not isinstance(current, ast.Module):
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            _, declared_global, declared_nonlocal = scopes._scope(current)
            if name in declared_global:
                return True
            # Nonlocal stores never target a module. Its outer cell can be
            # created by a walrus/header ScopeIndex does not otherwise index.
            if name in declared_nonlocal:
                return False
        current = scopes.parents.get(current)
    return True


def reflective_access(tree: ast.Module, *, excluded: frozenset[int] = frozenset()) -> ast.AST | None:
    """A node that reaches the module's own names without spelling them.

    A bare ``globals()``, ``vars()`` or ``locals()`` call, ``sys.modules``
    however ``sys`` or ``modules`` is imported, or importing the module by
    ``__name__``: ``sys.modules[__name__].TOOLS.append(f)`` changes a list no
    use of ``TOOLS`` shows (#879 review).
    """

    sys_names = {"sys"}
    modules_names: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in excluded:
            continue
        if isinstance(node, ast.Import):
            sys_names.update(
                alias.asname for alias in node.names if alias.name == "sys" and alias.asname
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "sys" and not node.level:
            modules_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "modules"
            )
    for node in ast.walk(tree):
        if id(node) in excluded:
            continue
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"globals", "vars", "locals"}
                and not node.args
            ):
                return node
            if (
                reference_spelling(node.func)
                in {"importlib.import_module", "import_module", "__import__"}
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "__name__"
            ):
                return node
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "modules"
            and isinstance(node.value, ast.Name)
            and node.value.id in sys_names
        ):
            return node
        elif isinstance(node, ast.Name) and node.id in modules_names:
            return node
    return None


def reference_spelling(node: ast.AST) -> str | None:
    """``name`` or ``module.attr`` for a plain dotted reference, else None."""

    parts = _dotted(node)
    return ".".join(parts) if parts is not None else None


def _dotted(node: ast.AST) -> list[str] | None:
    # Iterative: a chain thousands of attributes deep is valid Python, and a
    # recursive walk turned it into a crash of the whole run (#879 review).
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    parts.reverse()
    return parts


def _fallthrough(module: PythonModule, name: str, *, patches: dict[str, int] | None = None) -> dict[str, Any]:
    """The package ``__init__`` read and found not to bind ``name`` itself.

    A module-level ``__getattr__`` there is not evaluated: the submodule of
    that name is taken as the target, and the step says a hook could have
    answered first, so a caller can decline to call the result proven.
    """

    step: dict[str, Any] = {
        "path": module.ref,
        "line": None,
        "name": name,
        "sha256": module.sha256,
        "binding": "submodule",
    }
    if PATH_PATCH in (module.attribute_patches if patches is None else patches):
        # ``__path__.insert(0, ...)``: the submodule may come from elsewhere.
        step["path_extended"] = True
    if "__getattr__" in module.bindings:
        step["module_getattr"] = True
        if _hook_answers_submodule(module, name):
            # It can only import and return the submodule of that name.
            step["lazy_submodule"] = True
    return step


def _hook_answers_submodule(module: PythonModule, name: str) -> bool:
    """Whether a package ``__getattr__`` can answer ``name`` only with that
    submodule, or not at all (#879 review).

    Defined once, at module level, undecorated, taking one parameter it never
    rebinds. Every ``return`` it can reach for ``name`` — one guarded by ``if
    param == "other":`` cannot — gives ``importlib.import_module(f".{param}",
    __name__)`` (or ``"." + param``, or ``f"{__name__}.{param}"``), with
    ``importlib`` / ``import_module`` bound only by an import from
    ``importlib``, directly or through a local bound exactly once from it; or
    the package's own ``from . import name``. Those are the lazy-loading
    idioms; anything else could redirect the name.
    """

    bindings = module.bindings.get("__getattr__", [])
    if len(bindings) != 1 or not bindings[0].top_level:
        return False
    function = bindings[0].node
    if (
        not isinstance(function, ast.FunctionDef)
        or function.decorator_list
        or len(function.args.args) != 1
    ):
        return False
    parameter = function.args.args[0].arg
    # The package can subvert any hook: rebind ``__name__``, patch
    # ``importlib``, store into ``sys.modules``, or replace ``__getattr__``
    # through ``globals()`` (#879 review).
    if "__name__" in module.bindings or any(
        key.startswith((MODULE_TABLE_PATCH, SELF_PATCH))
        or key == PATH_PATCH
        or key.rsplit(".", 1)[-1] == "__getattr__"
        or key.split(".", 1)[0] in {"importlib", "import_module"}
        for key in module.attribute_patches
    ):
        return False
    inside = {id(node) for node in ast.walk(function)}
    for node in ast.walk(module.tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"update", "setdefault", "__setitem__"}
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id in {"globals", "vars"}
        ):
            # ``globals().update(__getattr__=...)``.
            return False
        if not (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in {"globals", "vars"}
            and not node.value.args
        ):
            continue
        # Only the idiom's own cache, ``globals()[name] = module``, inside it.
        if id(node) not in inside or not (
            isinstance(node.slice, ast.Name) and node.slice.id == parameter
        ):
            return False

    def from_importlib(head: str) -> bool:
        """``importlib`` / ``import_module`` bound in the module only by importing it."""

        found = module.bindings.get(head, [])
        return bool(found) and all(
            isinstance(item.node, ast.alias)
            and (
                (
                    isinstance(item.statement, ast.Import)
                    and head == "importlib"
                    and item.node.name == "importlib"
                    and item.node.asname is None
                )
                or (
                    isinstance(item.statement, ast.ImportFrom)
                    and head == "import_module"
                    and not item.statement.level
                    and item.statement.module == "importlib"
                    and item.node.name == "import_module"
                    and item.node.asname in (None, "import_module")
                )
            )
            for item in found
        )

    def is_parameter(node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id == parameter

    def is_import(node: ast.AST | None) -> bool:
        spelling = reference_spelling(node.func) if isinstance(node, ast.Call) else None
        if spelling not in {"importlib.import_module", "import_module"}:
            return False
        assert isinstance(node, ast.Call) and spelling is not None
        head = spelling.split(".", 1)[0]
        if head in assigned or not from_importlib(head):
            return False
        args = node.args
        package = len(args) == 2 and isinstance(args[1], ast.Name) and args[1].id == "__name__"
        target = args[0] if args else None
        relative = (
            isinstance(target, ast.JoinedStr)
            and len(target.values) == 2
            and isinstance(target.values[0], ast.Constant)
            and target.values[0].value == "."
            and isinstance(target.values[1], ast.FormattedValue)
            and is_parameter(target.values[1].value)
        ) or (
            isinstance(target, ast.BinOp)
            and isinstance(target.op, ast.Add)
            and isinstance(target.left, ast.Constant)
            and target.left.value == "."
            and is_parameter(target.right)
        )
        absolute = (
            isinstance(target, ast.JoinedStr)
            and len(target.values) == 3
            and isinstance(target.values[0], ast.FormattedValue)
            and isinstance(target.values[0].value, ast.Name)
            and target.values[0].value.id == "__name__"
            and isinstance(target.values[1], ast.Constant)
            and target.values[1].value == "."
            and isinstance(target.values[2], ast.FormattedValue)
            and is_parameter(target.values[2].value)
        )
        return (relative and package) or (absolute and len(args) == 1)

    def guard(test: ast.expr) -> str | None:
        """``param == "x"`` (either way round): the one name the branch serves."""

        if (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
        ):
            left, right = test.left, test.comparators[0]
            for one, other in ((left, right), (right, left)):
                if is_parameter(one) and isinstance(other, ast.Constant) and isinstance(other.value, str):
                    return other.value
        return None

    # One pass: the value(s) each local is bound to, and each return with the
    # names its enclosing ``if param == ...`` branches restrict it to.
    assigned: dict[str, list[ast.AST | None]] = {}
    returns: list[tuple[ast.Return, set[str]]] = []

    def bind(name: str, value: ast.AST | None) -> None:
        assigned.setdefault(name, []).append(value)

    parents = {child: node for node in ast.walk(function) for child in ast.iter_child_nodes(node)}
    stack: list[tuple[ast.AST, frozenset[str]]] = [(item, frozenset()) for item in function.body]
    while stack:
        node, guards = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            return False
        if isinstance(node, ast.Yield | ast.YieldFrom | ast.Global | ast.Nonlocal):
            return False
        if isinstance(node, ast.Return):
            returns.append((node, set(guards)))
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            # Every binding counts — ``for``, ``with``, walrus, ``del`` — so a
            # local "bound once" is bound exactly once (#879 review).
            parent_value: ast.AST | None = None
            owner = parents.get(node)
            if isinstance(owner, ast.Assign) and owner.targets == [node]:
                parent_value = owner.value
            elif isinstance(owner, ast.AnnAssign) and owner.target is node:
                parent_value = owner.value
            bind(node.id, parent_value)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind(node.name, None)
        elif isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                bind(
                    (alias.asname or alias.name).split(".", 1)[0],
                    alias
                    if isinstance(node, ast.ImportFrom) and node.level == 1 and not node.module
                    else None,
                )
        if isinstance(node, ast.If):
            served = guard(node.test)
            inner = guards | {served} if served is not None else guards
            stack.extend((item, inner) for item in node.body)
            stack.extend((item, guards) for item in node.orelse)
            stack.append((node.test, guards))
            continue
        stack.extend((child, guards) for child in ast.iter_child_nodes(node))
    if parameter in assigned:
        # ``name = "evil"`` before the import: the parameter no longer says
        # which submodule is imported.
        return False
    for node, guards in returns:
        if guards and guards != {name}:
            # Reached only for another name (or never).
            continue
        value = node.value
        if is_import(value):
            continue
        if isinstance(value, ast.Name):
            values = assigned.get(value.id, [])
            if len(values) == 1 and (
                is_import(values[0])
                # ``from . import memory`` for ``memory``, never ``from .
                # import alternate as memory`` (#879 review).
                or (isinstance(values[0], ast.alias) and value.id == name and values[0].name == name)
            ):
                continue
        return False
    return True


def _imports_own_submodule(module: PythonModule, name: str) -> bool:
    """Whether a package binds ``name`` only as ``from . import name``.

    That statement names the package's own submodule, so however it is guarded
    — ``if TYPE_CHECKING:`` is the common case — it cannot make ``name`` mean
    anything but the submodule.
    """

    bindings = module.bindings.get(name, [])
    return bool(bindings) and all(
        isinstance(item.node, ast.alias)
        and isinstance(item.statement, ast.ImportFrom)
        and item.statement.level == 1
        and not item.statement.module
        and item.node.name == name
        and item.node.asname in (None, name)
        for item in bindings
    )


def _same_package_imports(bindings: list[_Binding], name: str) -> bool:
    return all(
        isinstance(item.node, ast.alias)
        and isinstance(item.statement, ast.Import)
        and item.node.asname is None
        and item.node.name.split(".", 1)[0] == name
        and item.top_level
        for item in bindings
    )


def _longest_import_prefix(bindings: list[_Binding], parts: list[str]) -> _Binding:
    def matched(item: _Binding) -> int:
        imported = item.node.name.split(".")  # type: ignore[union-attr]
        return len(imported) if parts[: len(imported)] == imported else 0

    return max(bindings, key=lambda item: (matched(item), -_line(item.statement)))


def _join(spelling: str, name: str) -> str:
    return f"{spelling}{name}" if spelling.endswith(".") else f"{spelling}.{name}"


def _from_spelling(statement: ast.ImportFrom) -> str:
    return "." * statement.level + (statement.module or "")


def _line(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 0) or 0)


def _module(path: Path, ref: str, tree: ast.Module, text: str) -> PythonModule:
    bindings, star_import = _module_bindings(tree)
    return PythonModule(
        path=path,
        ref=ref,
        tree=tree,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        package=path.name == "__init__.py",
        bindings=bindings,
        star_import=star_import,
        attribute_patches=_attribute_patches(tree),
    )


#: ``attribute_patches`` keys for a store into ``sys.modules``, which can
#: replace a module an import names (#879 review): under a literal name
#: (``*=pkg.memory``), a name built on ``__name__`` (the package's own
#: submodules), or a computed one (a plugin loader's ``spec.name``).
MODULE_TABLE_PATCH = "*"
MODULE_TABLE_LITERAL = "*="
MODULE_TABLE_OWN = "*self"
MODULE_TABLE_COMPUTED = "*?"


#: ``attribute_patches`` key prefix for a module rebinding its own name
#: (``globals()["lookup"] = x``, ``sys.modules[__name__].lookup = x``).
SELF_PATCH = "<self>."
#: ``attribute_patches`` key for a change to a package's ``__path__``.
PATH_PATCH = "<path>"
IMPORT_SEARCH_PATCH = "<import-search>"
#: Calls that only read the namespace or module table they are handed.
_NAMESPACE_READERS = frozenset(
    {"set", "frozenset", "list", "tuple", "sorted", "len", "iter", "dict", "any", "all",
     "print", "repr", "str", "enumerate", "reversed"}
)
#: Calls that read a namespace handed to them by keyword.
_NAMESPACE_KEYWORD_READERS = frozenset(
    {"get_type_hints", "get_annotations", "evaluate_forward_ref", "update_forward_refs", "model_rebuild"}
)
#: Calls that only read a module object they are handed.
_MODULE_READERS = frozenset(
    {"getattr", "hasattr", "dir", "vars", "id", "repr", "print", "isinstance", "type",
     "inspect.getmembers", "inspect.getmodule", "inspect.getfile", "getmembers"}
)
#: Standard-library modules loaded at interpreter startup, before any
#: application code: a same-named file cannot shadow them.
_PRELOADED = frozenset(
    {"abc", "builtins", "codecs", "encodings", "genericpath", "io", "marshal", "nt", "ntpath",
     "os", "posix", "posixpath", "site", "stat", "sys", "time", "zipimport"}
)
#: Names that read a function's annotation dictionary or evaluate a stored one.
_ANNOTATION_READERS = frozenset(
    {"__annotations__", "__annotate__", "__annotate_func__", "get_type_hints", "get_annotations",
     "evaluate_forward_ref", "update_forward_refs", "model_rebuild", "__wrapped__"}
)
#: The frameworks whose own modules build the tools an agent binds.
_FRAMEWORK_MODULES = ("agents", "openai_agents", "google.adk")
# These dependencies are recognized only by the private constructor-identity
# resolver. They do not become supported tool definitions or receiving sinks.
_CONSTRUCTOR_IMPORT_MODULES = (*_FRAMEWORK_MODULES, "pydantic", "abc", "_collections_abc", "typing", "typing_extensions", "google.genai", "griffe")
# A finite primary-stdlib alias/ABC ancestry boundary, read from CPython
# sources. Ordinary sibling APIs are not constructors or getter exceptions.
_STDLIB_SHARED_DEPENDENCY_MEMBERS = {
    # A module's type carries its attribute hooks, without receiving authority.
    "types": ("ModuleType",),
    "collections": ("abc", "_collections_abc", "ChainMap", "UserDict", "UserList", "UserString",
                    "_OrderedDictItemsView", "_OrderedDictKeysView", "_OrderedDictValuesView"),
    "_pyio": ("abc", "IOBase", "BufferedIOBase", "BufferedRWPair", "BufferedRandom", "BufferedReader",
              "BufferedWriter", "BytesIO", "FileIO", "RawIOBase", "StringIO", "TextIOBase", "TextIOWrapper", "_BufferedIOMixin"),
    "cgi": ("Mapping",),
    "contextlib": ("abc", "_collections_abc", "AbstractAsyncContextManager", "AbstractContextManager",
                   "AsyncExitStack", "ExitStack", "_AsyncGeneratorContextManager", "_GeneratorContextManager",
                   "_RedirectStream", "aclosing", "chdir", "closing", "nullcontext", "redirect_stderr", "redirect_stdout", "suppress"),
    "configparser": ("MutableMapping", "ConfigParser", "ConverterMapping", "RawConfigParser", "SectionProxy"),
    "dataclasses": ("abc",),
    "io": ("abc", "IOBase", "BufferedIOBase", "RawIOBase", "TextIOBase"),
    "locale": ("_collections_abc",),
    "numbers": ("ABCMeta", "abstractmethod", "Number", "Complex", "Integral", "Rational", "Real"),
    "os": ("abc", "_check_methods", "MutableMapping", "Mapping", "PathLike", "_Environ"),
    "pathlib": ("Sequence", "_PathParents"),
    "random": ("_Sequence",),
    "selectors": ("ABCMeta", "abstractmethod", "Mapping", "BaseSelector", "DefaultSelector", "DevpollSelector",
                  "EpollSelector", "KqueueSelector", "PollSelector", "SelectSelector", "_BaseSelectorImpl", "_PollLikeSelector", "_SelectorMapping"),
    "shelve": ("collections.abc", "collections._collections_abc", "BsdDbShelf", "DbfilenameShelf", "Shelf", "_ClosedDict"),
    "tracemalloc": ("Sequence", "Iterable", "Traceback", "_Traces"),
    "weakref": ("_collections_abc", "WeakKeyDictionary", "WeakValueDictionary"),
    "traceback": ("collections.abc", "collections._collections_abc"),
    "fractions": ("numbers", "Fraction"),
    "_pydecimal": ("_numbers",),
}
_STDLIB_SHARED_DEPENDENCY_PATHS = frozenset(
    f"{module}.{member}" for module, members in _STDLIB_SHARED_DEPENDENCY_MEMBERS.items() for member in members
)
# Public typing values may appear in an annotation, but not in an arbitrary
# call, retained alias, metadata read or namespace export. Private helpers and
# other packages' reexports do not gain this narrow value-role exception.
_TYPING_ANNOTATION_VALUES = frozenset({
    "Any", "Annotated", "Literal", "Optional", "Union", "List", "Dict", "Tuple", "Set", "FrozenSet",
    "Sequence", "Mapping", "MutableMapping", "MutableSequence", "MutableSet", "Callable", "Iterable",
    "Iterator", "Collection", "AbstractSet", "Type", "ClassVar", "Final", "NoReturn", "Never", "Self",
    "Concatenate", "Unpack", "TypeGuard", "TypeIs",
})


def _absolute_import_reference(alias: ast.alias, statement: ast.AST | None) -> str | None:
    if isinstance(statement, ast.Import):
        return alias.name if alias.asname else alias.name.split(".", 1)[0]
    if isinstance(statement, ast.ImportFrom) and not statement.level and statement.module:
        return f"{statement.module}.{alias.name}"
    return None


def _external_constructor_paths(family: str) -> set[str]:
    if family == "google.adk":
        return {f"{prefix}.{name}" for prefix in (family, family + ".agents", family + ".agents.llm_agent")
                for name in ("Agent", "LlmAgent")}
    # The SDK reader already recognizes both published import spellings.
    # Each actual import still needs its own provider and mutation census.
    roots = ("agents", "openai_agents") if family in {"agents", "openai_agents"} else (family,)
    return {root + suffix for root in roots for suffix in (".Agent", ".agent.Agent")}


def _external_wrapper_paths(family: str) -> set[str]:
    if family != "google.adk":
        return set()
    return {f"{prefix}.{name}" for prefix, name in (
        ("google.adk.tools", "FunctionTool"), ("google.adk.tools.function_tool", "FunctionTool"),
        ("google.adk.tools", "LongRunningFunctionTool"),
        ("google.adk.tools.function_tool", "LongRunningFunctionTool"),
    )}


def _external_decorator_paths(family: str) -> set[str]:
    return {root + suffix for root in ("agents", "openai_agents")
            for suffix in (".function_tool", ".tool.function_tool")} if family in {"agents", "openai_agents"} else set()


def _framework_tool_object(family: str, canonical: str) -> str | None:
    """How a framework object bound as a tool is made, by its exact import (#910).

    ``"value"``: the import is itself the tool (ADK's ``google_search``).
    ``"object"``: calling it builds the tool object (an MCP server, a hosted
    tool, ``AgentTool``). ``"toolset"``: ADK's MCP toolset, built only through
    :func:`_adk_toolset_value_call`. ``"wrapper"``: calling it wraps a function
    the caller names (the SDK's ``function_tool``). The role is the canonical
    path the import resolves to, never the spelling at the use.
    """
    from agents_shipgate.inputs import object_tools

    if family == "google.adk":
        if canonical in object_tools.ADK_BUILT_INS:
            return "value"
        if canonical in object_tools.ADK_AGENT_TOOLS:
            return "object"
        if canonical in object_tools.ADK_MCP_TOOLSETS:
            return "toolset"  # Its arguments have their own, narrower role.
        return None
    root, _, _ = canonical.partition(".")
    short = canonical.rsplit(".", 1)[-1]
    if root not in {"agents", "openai_agents"}:
        return None
    if short in object_tools.SDK_HOSTED_TOOLS or short in object_tools.SDK_MCP_SERVERS:
        return "object"
    if canonical in {f"{root}.function_tool", f"{root}.tool.function_tool"}:
        return "wrapper"
    return None


def _framework_connection_class(canonical: str) -> bool:
    """An ADK MCP connection-parameter class, by its exact import."""
    from agents_shipgate.inputs import object_tools

    short = canonical.rsplit(".", 1)[-1]
    return short in object_tools.ADK_CONNECTIONS and (
        canonical.startswith("google.adk.tools.mcp_tool.") or canonical.startswith("mcp.")
    )


def _tool_object_argument_is_data(
    resolver: ImportResolver, module: PythonModule, value: ast.expr, scopes: ScopeIndex, budget: list[int],
) -> bool:
    """A value a tool object is configured with, never code the framework runs.

    Constants, text built from them, containers and reads of other values are
    data; a call runs at construction in this repository, so its own uses are
    checked where they stand. A callable (a lambda, a def, a generator) is not
    data: the framework could call it with framework objects.
    """
    budget[0] -= 1
    if budget[0] < 0:
        return False
    if isinstance(value, ast.Constant):
        return True
    if isinstance(value, ast.JoinedStr):
        return all(isinstance(item, ast.Constant)
                   or isinstance(item, ast.FormattedValue) and item.format_spec is None
                   and _tool_object_argument_is_data(resolver, module, item.value, scopes, budget)
                   for item in value.values)
    if isinstance(value, ast.List | ast.Tuple | ast.Set):
        return all(not isinstance(item, ast.Starred)
                   and _tool_object_argument_is_data(resolver, module, item, scopes, budget) for item in value.elts)
    if isinstance(value, ast.Dict):
        return all(key is not None and _tool_object_argument_is_data(resolver, module, key, scopes, budget)
                   and _tool_object_argument_is_data(resolver, module, item, scopes, budget)
                   for key, item in zip(value.keys, value.values, strict=True))
    if isinstance(value, ast.BinOp):
        return (_tool_object_argument_is_data(resolver, module, value.left, scopes, budget)
                and _tool_object_argument_is_data(resolver, module, value.right, scopes, budget))
    if isinstance(value, ast.BoolOp):
        return all(_tool_object_argument_is_data(resolver, module, item, scopes, budget) for item in value.values)
    if isinstance(value, ast.IfExp):
        return all(_tool_object_argument_is_data(resolver, module, item, scopes, budget)
                   for item in (value.test, value.body, value.orelse))
    if isinstance(value, ast.Subscript):
        return (_tool_object_argument_is_data(resolver, module, value.value, scopes, budget)
                and _tool_object_argument_is_data(resolver, module, value.slice, scopes, budget))
    if isinstance(value, ast.Name | ast.Attribute):
        if not isinstance(value.ctx, ast.Load) or reference_spelling(value) is None:
            return False
        definition = resolver._constructor_reference(module, value, scopes).get("definition")
        return not isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    if isinstance(value, ast.Call):
        if any(isinstance(item, ast.Starred) for item in value.args) or any(item.arg is None for item in value.keywords):
            return False
        return (reference_spelling(value.func) is not None
                and all(_tool_object_argument_is_data(resolver, module, item, scopes, budget)
                        for item in (*value.args, *(keyword.value for keyword in value.keywords))))
    return False


def _tool_object_call_is_data(
    resolver: ImportResolver, module: PythonModule, call: ast.Call, scopes: ScopeIndex,
) -> bool:
    """A call building a framework tool object with only data arguments."""
    if any(isinstance(item, ast.Starred) for item in call.args) or any(item.arg is None for item in call.keywords):
        return False
    budget = [400]
    return all(_tool_object_argument_is_data(resolver, module, item, scopes, budget)
               for item in (*call.args, *(keyword.value for keyword in call.keywords)))


def _adk_connection_call(
    resolver: ImportResolver, module: PythonModule, value: ast.expr, scopes: ScopeIndex,
) -> bool:
    """``SseConnectionParams(url=...)``: ADK's connection data for a toolset.

    The class is the exact import of one of ADK's (or the ``mcp`` package's)
    connection-parameter classes and every argument is data; the object it
    builds only reaches the toolset's ``connection_params``.
    """
    if not isinstance(value, ast.Call):
        return False
    canonical = resolver._constructor_reference(module, value.func, scopes).get("external_constructor")
    return (isinstance(canonical, str) and _framework_connection_class(canonical)
            and _tool_object_call_is_data(resolver, module, value, scopes))


def _adk_toolset_value_call(
    resolver: ImportResolver, module: PythonModule, call: ast.Call, scopes: ScopeIndex,
    canonical: str,
) -> bool:
    """Existing static toolset data, without granting a receiving-class route.

    Inventory/path hints describe the static artifact; they do not assert that
    the installed ADK accepts those keywords. Callable and object options do
    not gain this role, and the result has its own restrictive owner census.
    """
    from agents_shipgate.inputs.object_tools import ADK_MCP_TOOLSETS

    mcp = set(ADK_MCP_TOOLSETS)  # The toolset's import path is not its identity.
    openapi = {"google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset.OpenAPIToolset"}
    if canonical not in mcp | openapi or call.args or any(item.arg is None for item in call.keywords):
        return False
    hints = ({"inventory_path", "tool_inventory_path", "mcp_tools_path", "mcp_inventory"}
             if canonical in mcp else {"spec_path", "path", "spec_file", "openapi_path", "openapi_spec"})
    strings = hints | {"tool_name_prefix", "credential_key"}
    booleans = {"require_confirmation"} if canonical in mcp else {"ssl_verify", "preserve_property_names"}
    objects = {"connection_params", "header_provider", "progress_callback", "sampling_callback",
               "sampling_capabilities", "errlog"} if canonical in mcp else {
                   "auth_scheme", "auth_credential", "header_provider", "httpx_client_factory",
               }

    def literal(value: ast.expr) -> bool:
        if isinstance(value, ast.Constant):
            return value.value is None or type(value.value) in {str, bool, int, float}
        if isinstance(value, ast.List | ast.Tuple):
            return all(literal(item) for item in value.elts)
        return isinstance(value, ast.Dict) and all(
            isinstance(key, ast.Constant) and isinstance(key.value, str) and literal(item)
            for key, item in zip(value.keys, value.values, strict=True)
        )

    def local_text(value: ast.expr) -> bool:
        if not (isinstance(value, ast.Call) and not value.args and not value.keywords
                and isinstance(value.func, ast.Attribute) and value.func.attr == "read_text"):
            return False
        path = value.func.value
        return (isinstance(path, ast.Call) and len(path.args) == 1 and not path.keywords
                and isinstance(path.args[0], ast.Constant) and isinstance(path.args[0].value, str)
                and resolver._constructor_reference(module, path.func, scopes).get("external_constructor") == "pathlib.Path")

    names = [item.arg for item in call.keywords]
    if len(names) != len(set(names)):
        return False
    for item in call.keywords:
        value = item.value
        if item.arg in strings:
            if not (isinstance(value, ast.Constant) and (isinstance(value.value, str) or value.value is None)):
                return False
        elif item.arg in booleans:
            if not (isinstance(value, ast.Constant) and type(value.value) is bool):
                return False
        elif item.arg in objects:
            if item.arg == "connection_params" and _adk_connection_call(resolver, module, value, scopes):
                continue
            if not (isinstance(value, ast.Constant) and value.value is None):
                return False
        elif item.arg == "tool_filter":
            if not (isinstance(value, ast.Constant) and value.value is None) and not (
                isinstance(value, ast.List | ast.Tuple)
                and all(isinstance(member, ast.Constant) and isinstance(member.value, str) for member in value.elts)
            ):
                return False
        elif canonical in openapi and item.arg == "spec_str_type":
            if not (isinstance(value, ast.Constant) and value.value in {"json", "yaml"}):
                return False
        elif canonical in openapi and item.arg == "spec_str":
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)) and not local_text(value):
                return False
        elif canonical in openapi and item.arg == "spec_dict":
            if not isinstance(value, ast.Dict) or not literal(value):
                return False
        else:
            return False
    return True


def _external_constructor_dependency_paths(family: str) -> set[str]:
    """Protected dependencies, distinct from supported constructor classes."""
    shared = {
        # These packages reexport mutable construction objects through public
        # and private modules. Protect the canonical namespace rather than
        # pretending an enumerated spelling proves an object's only alias.
        "pydantic", "typing", "typing_extensions", "abc", "_collections_abc",
    } | set(_FRAMEWORK_MODULES) | set(_STDLIB_SHARED_DEPENDENCY_PATHS)
    if family == "google.adk":
        return shared | {
            "google.adk.agents.BaseAgent", "google.adk.agents.base_agent.BaseAgent",
            "google.adk.workflow.BaseNode", "google.adk.workflow._base_node.BaseNode",
            "google.adk.tools.BaseTool", "google.adk.tools.base_tool.BaseTool",
            "google.adk.agents.llm_agent.BaseAgent", "google.adk.agents.base_agent.BaseNode",
            "google.adk.agents.base_agent.BaseModel", "google.adk.agents.llm_agent.BaseModel",
            "google.adk.workflow._base_node.BaseModel", "google.adk.tools.function_tool.BaseTool",
            "google.adk.agents.llm_agent.BaseTool", "google.adk.agents.llm_agent.FunctionTool",
            "google.adk.agents.base_agent.abc", "google.adk.agents.llm_agent.abc",
            "google.adk.tools.base_tool.ABC", "google.adk.tools.base_tool.BaseModel",
            "google.adk.tools.function_tool.pydantic",
            "google.adk.tools.long_running_tool.LongRunningFunctionTool",
            "google.adk.tools.function_tool", "google.adk.tools.base_tool",
            "google.adk.utils.context_utils", "google.adk.utils.variant_utils",
            "google.adk.tools._automatic_function_calling_util",
            "google.adk.tools._function_tool_declarations",
            "google.adk.tools._function_parameter_parse_util", "google.adk.tools._gemini_schema_util",
            "google.genai",
        }
    return shared | {
        "typing.Generic", "griffe",
        family + ".agent", family + ".tool", family + ".function_schema", family + ".strict_schema",
        family + ".AgentBase", family + ".agent.AgentBase",
        family + ".FunctionTool", family + ".tool.FunctionTool",
        family + ".agent.BaseModel", family + ".agent.Generic",
        family + ".tool.BaseModel", family + ".tool.Generic", family + ".tool.typing",
        family + ".tool.function_schema", family + ".function_schema.function_schema",
        family + ".function_schema.FuncSchema", family + ".function_schema.BaseModel",
        family + ".function_schema.create_model",
        family + ".run_internal.agent_tool_configuration.assign_agent_tools",
        family + ".run_internal.agent_tool_configuration",
    }


def _external_constructor_dependency_modules(family: str) -> set[str]:
    return {"pydantic", "abc", "_collections_abc", "collections", "typing", "typing_extensions"} | ({"google.genai", "mcp", "json", "yaml"} if family == "google.adk" else {"griffe", "dataclasses"})


def _independent_foreign_class_slot_write(
    resolver: ImportResolver, module: PythonModule, node: ast.expr, scopes: ScopeIndex, family: str,
) -> bool:
    """One proven independent class slot, never a method object or namespace."""
    if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Store):
        return False
    parent = scopes.parents.get(node)
    if not ((isinstance(parent, ast.Assign) and parent.targets == [node])
            or (isinstance(parent, ast.AnnAssign) and parent.target is node)):
        return False
    allowed = ({f"{prefix}.AgentBase" for prefix in ("agents", "agents.agent", "openai_agents", "openai_agents.agent")}
               if family == "google.adk" else
               {f"{prefix}.BaseAgent" for prefix in ("google.adk.agents", "google.adk.agents.base_agent", "google.adk.agents.llm_agent")})
    slots = {"__new__", "__setattr__"} if family == "google.adk" else {"__init__", "__new__"}
    parts = _dotted(node.value)
    if node.attr not in slots or parts is None:
        return False
    from agents_shipgate.inputs.list_expressions import evaluation_site
    if scopes.enclosing_bindings(evaluation_site(scopes, node), parts[0]):
        return False
    bindings = module.bindings.get(parts[0], [])
    if (len(bindings) != 1 or not bindings[0].top_level or not isinstance(bindings[0].node, ast.alias)
            or bindings[0].statement not in module.tree.body):
        return False
    canonical = resolver._constructor_reference(module, node.value, scopes).get("external_constructor")
    if canonical not in allowed:
        return False
    foreign = next(root for root in _FRAMEWORK_MODULES if canonical.startswith(root + "."))
    binding = bindings[0]
    imported = (binding.node.name if isinstance(binding.statement, ast.Import)
                else _absolute_import_reference(binding.node, binding.statement))
    # A retained ancestor (import google / google.cloud) does not establish
    # the projected ADK class or the foreign dependencies its import loads.
    return imported is not None and (imported == foreign or imported.startswith(foreign + "."))


def _unused_reflection_import(
    resolver: ImportResolver, module: PythonModule, statement: ast.AST,
) -> bool:
    """An unused canonical import grants no reflective handle to an owner.

    This discharges only the import-spelling check. The normal import closure,
    namespace mutations and retained callable census still apply to the module.
    """
    if (not isinstance(statement, ast.Import | ast.ImportFrom)
            or statement not in module.tree.body
            or isinstance(statement, ast.ImportFrom) and statement.level):
        return False
    if not statement.names or any(
        (statement.module if isinstance(statement, ast.ImportFrom) else alias.name)
        not in {"builtins", "inspect"} or alias.name == "*"
        for alias in statement.names
    ):
        return False
    # A resolver owns immutable parsed trees for one read. Cache census data
    # by the tree itself, keeping its identity alive; provider and binding
    # proofs below remain specific to each import and resolver context.
    if module.tree not in resolver._reflection_import_loads:
        resolver._reflection_import_loads[module.tree] = frozenset(
            node.id for node in ast.walk(module.tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        )
    loads = resolver._reflection_import_loads[module.tree]
    for alias in statement.names:
        name = alias.asname or (alias.name if isinstance(statement, ast.ImportFrom)
                               else alias.name.split(".", 1)[0])
        bindings = module.bindings.get(name, [])
        if (name in loads or len(bindings) != 1 or bindings[0].node is not alias
                or not bindings[0].top_level):
            return False
        try:
            if resolver._external_provider_issue(module, statement, alias) is not None:
                return False
        except _Stop:
            return False
    return True


def _namespace_copy_source_limit(
    resolver: ImportResolver, module: PythonModule, family: str,
) -> str | None:
    """Name incomplete source context for exact copies; grant no copy authority.

    A successful census deliberately leaves the normal reflection refusal in
    place. Reading sources cannot establish an installed module's getter hooks
    or the copied dictionary's lifetime and values.
    """
    namespaces = {symbol.rsplit(".", 1)[0] for symbol in _external_constructor_paths(family)}
    for statement in module.tree.body:
        if not (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)):
            continue
        allocation = statement.value
        if not (isinstance(allocation, ast.Call) and isinstance(allocation.func, ast.Name)
                and allocation.func.id == "dict" and len(allocation.args) == 1 and not allocation.keywords):
            continue
        getter = allocation.args[0]
        if not (isinstance(getter, ast.Call) and isinstance(getter.func, ast.Name)
                and getter.func.id == "vars" and len(getter.args) == 1 and not getter.keywords
                and isinstance(getter.args[0], ast.Name)):
            continue
        if module.bindings.get("dict") or module.bindings.get("vars"):
            continue
        receiver = getter.args[0]
        imported = module.bindings.get(receiver.id, [])
        destination = module.bindings.get(statement.targets[0].id, [])
        if not (len(imported) == 1 and imported[0].top_level
                and isinstance(imported[0].node, ast.alias)
                and isinstance(imported[0].statement, ast.Import)
                and imported[0].node.name in namespaces
                and (imported[0].node.asname or imported[0].node.name) == receiver.id
                and imported[0].statement.lineno < statement.lineno
                and len(destination) == 1 and destination[0].statement is statement):
            continue
        from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit
        try:
            issue = resolver._external_provider_issue(module, imported[0].statement, imported[0].node)
            if issue is not None:
                return f"namespace copy source context is unread in {module.ref}:{getter.lineno}: {issue}"
            BuilderCalls(resolver).namespace_source_context(family)
        except _Stop as stop:
            return f"namespace copy source context is unread in {module.ref}:{getter.lineno}: {stop.detail}"
        except CallLimit as stop:
            return f"namespace copy source context is unread in {module.ref}:{getter.lineno}: {stop}"
        break
    return None


def _eager_annotation_owned(
    resolver: ImportResolver, scopes: ScopeIndex, owner: ast.AST, family: str, canonical: str,
) -> bool:
    """An undecorated function's annotation naming the agent class or a tool object.

    The class lands in the function's annotation dictionary and nowhere else;
    that is a retained handle only if some module of the census can read the
    dictionary, which :meth:`ImportResolver._annotation_introspection` refuses.
    """
    function = owner
    if isinstance(owner, ast.arg):
        arguments = scopes.parents.get(owner)
        function = scopes.parents.get(arguments) if arguments is not None else None
    if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef) or function.decorator_list:
        return False
    if not (canonical in _external_constructor_paths(family)
            or _framework_tool_object(family, canonical) in {"object", "toolset"}):
        return False
    return resolver._annotation_introspection() is None


def _environment_value_role(
    resolver: ImportResolver, module: PythonModule, read: ast.Subscript, scopes: ScopeIndex, family: str,
) -> bool:
    """Where one ``os.environ[key]`` read lands, when that is a role this census checks.

    The read is a string, so the mapping goes nowhere. It is accepted only
    in the two places a tool body or an agent module reads configuration: a
    plain assignment to a local name, or the data a framework tool object is
    configured with (an MCP server's ``env``). A read kept inside another
    container literal or handed to another call stays an unread external
    handle, as the pinned application-diff controls require.
    """
    parent = scopes.parents.get(read)
    if (isinstance(parent, ast.Assign) and parent.value is read and len(parent.targets) == 1
            and isinstance(parent.targets[0], ast.Name)):
        return True
    current: ast.AST = read
    while isinstance(scopes.parents.get(current), ast.Dict | ast.List | ast.Tuple):
        current = scopes.parents[current]
    keyword = scopes.parents.get(current)
    call = scopes.parents.get(keyword) if isinstance(keyword, ast.keyword) else None
    if not isinstance(call, ast.Call):
        return False
    canonical = resolver._constructor_reference(module, call.func, scopes).get("external_constructor")
    return isinstance(canonical, str) and _framework_tool_object(family, canonical) == "object"


def _stdlib_reference(
    resolver: ImportResolver, module: PythonModule, node: ast.expr, scopes: ScopeIndex,
) -> str | None:
    """The standard-library path a name is, by its one import and nothing else.

    The head is bound once, by an absolute import of a standard-library
    module; no file the repository holds provides that module; and this
    module does not store into it. A same-named local file, a shadowed or
    reassigned name, a vendored copy or a non-standard package is None.
    """
    from agents_shipgate.inputs.list_expressions import evaluation_site

    spelling = reference_spelling(node)
    if spelling is None:
        return None
    head, _, rest = spelling.partition(".")
    local = scopes.enclosing_bindings(evaluation_site(scopes, node), head)
    found = local or [binding.node for binding in module.bindings.get(head, [])]
    if module.star_import or len(found) != 1 or not isinstance(alias := found[0], ast.alias):
        return None
    statement = scopes.statement_of(alias)
    if not isinstance(statement, ast.Import | ast.ImportFrom) or statement.lineno > node.lineno:
        return None
    imported = _absolute_import_reference(alias, statement)
    if imported is None or imported.split(".", 1)[0] not in sys.stdlib_module_names:
        return None
    try:
        if resolver._external_provider_issue(module, statement, alias) is not None:
            return None
        patched = resolver._runtime_attribute_patches(module)
    except _Stop:
        return None
    if any(key == head or key.startswith(head + ".") or key.startswith("*") or key.startswith(SELF_PATCH)
           for key in patched):
        return None
    return imported + ("." + rest if rest else "")


def _own_tool_object(
    resolver: ImportResolver, module: PythonModule, node: ast.expr, parent: ast.AST | None, scopes: ScopeIndex,
    family: str, kind: str, classes: set[str], callback_fields: set[str],
) -> bool:
    """Register the ownership obligation for one framework tool object (#910).

    The object is accepted only in the role its exact import has: ADK's built-in
    value as a member of a tools list, or the documented call that builds the
    object. Nothing here proves a use safe; each registration is an obligation
    the list reader discharges against every use of the object's result, as for
    a toolset or a wrapped function. Uses not matched here stay refused.
    """

    def receiving(container: ast.AST) -> tuple[ast.keyword, ast.Call] | None:
        keyword = scopes.parents.get(container)
        receiver = scopes.parents.get(keyword)
        if not (isinstance(keyword, ast.keyword) and isinstance(receiver, ast.Call)):
            return None
        expression = receiver.func.value if isinstance(receiver.func, ast.Subscript) else receiver.func
        target = resolver._constructor_reference(module, expression, scopes)
        return (keyword, receiver) if target.get("external_constructor") in classes else None

    if kind == "value":
        container: ast.AST = node
        while isinstance(scopes.parents.get(container), ast.List | ast.Tuple):
            container = scopes.parents[container]
        if container is node:
            return False
        found = receiving(container)
        if found is None:
            # A list kept for later: its container owner follows every use.
            resolver._constructor_container_owners[id(container)] = (module, container)
        elif found[0].arg == "tools":
            resolver._constructor_namespace_owners[(family, id(found[1]), "tools")] = (module, found[1], "tools")
        else:
            return False  # A built-in tool is a member of ``tools``, not of a callback list.
        return True
    if kind == "toolset" or not (isinstance(parent, ast.Call) and parent.func is node):
        return False
    if kind == "wrapper":
        # ``function_tool(f, needs_approval=False)``: the function is checked as
        # the wrapped operand where it stands; the rest is data.
        if not (len(parent.args) == 1 and isinstance(parent.args[0], ast.Name | ast.Attribute)
                and _tool_object_call_is_data(resolver, module, ast.Call(
                    func=parent.func, args=[], keywords=parent.keywords), scopes)):
            return False
    elif not _tool_object_call_is_data(resolver, module, parent, scopes):
        return False
    role = "tool_wrapper" if kind == "wrapper" else "toolset_data"
    resolver._constructor_namespace_owners[(family, id(parent), role)] = (module, parent, role)
    return True


def _external_constructor_use(
    resolver: ImportResolver, module: PythonModule, family: str, retaining: set[Path],
    *, allow_owner_routes: bool = True,
) -> str | None:
    """Refuse changes or retention of the external class and its namespaces.

    A plain class/namespace alias already retains the object, so refusing that
    use closes arbitrary alias chains without guessing how Python wires them.
    This deliberately does not classify a read of class metadata as harmless:
    a validator, annotations dictionary or callback can retain mutable state.
    """
    scopes = resolver._constructor_scope(module)
    typing_only = _type_checking_only(module, resolver, scopes)
    if allow_owner_routes and (copy_limit := _namespace_copy_source_limit(resolver, module, family)):
        return copy_limit
    from agents_shipgate.inputs.list_expressions import evaluation_site
    decorators = _external_decorator_paths(family)
    callback_fields = {
        "before_agent_callback", "after_agent_callback", "before_model_callback", "after_model_callback",
        "before_tool_callback", "after_tool_callback",
    } if family == "google.adk" else set()
    machinery = {"eval", "exec", "compile", "__import__", "__builtins__", "__globals__", "__closure__",
                 "cell_contents", "__getattribute__", "__getattr__", "getattr", "setattr", "delattr", "vars",
                 "_getframe", "currentframe", "getouterframes", "getinnerframes", "stack", "f_globals", "f_locals",
                 "cr_frame", "gi_frame", "ag_frame", "tb_frame", "__class__", "type"}

    def environment_read(home: PythonModule, home_scopes: ScopeIndex, value: ast.expr) -> bool:
        # ``os.getenv("NAME", "default")`` of the standard library: a string.
        if not (isinstance(value, ast.Call) and 1 <= len(value.args) <= 2 and not value.keywords
                and all(isinstance(item, ast.Constant) for item in value.args)):
            return False
        return (allow_owner_routes
                and _stdlib_reference(resolver, home, value.func, home_scopes) in {"os.getenv", "os.environ.get"})

    def dataclass_decorator(home: PythonModule, home_scopes: ScopeIndex, value: ast.expr) -> bool:
        callee = value.func if isinstance(value, ast.Call) else value
        if isinstance(value, ast.Call) and (value.args or any(
            keyword.arg is None or not isinstance(keyword.value, ast.Constant) for keyword in value.keywords
        )):
            return False
        return allow_owner_routes and _stdlib_reference(resolver, home, callee, home_scopes) == "dataclasses.dataclass"

    def class_literal(value: ast.expr | None, home: PythonModule = module, home_scopes: ScopeIndex = scopes) -> bool:
        if value is None or isinstance(value, ast.Constant):
            return True
        if environment_read(home, home_scopes, value):
            return True
        if isinstance(value, ast.BoolOp):
            return all(class_literal(item, home, home_scopes) for item in value.values)
        if isinstance(value, ast.List | ast.Tuple | ast.Set):
            return all(class_literal(item, home, home_scopes) for item in value.elts)
        if isinstance(value, ast.Dict):
            return all(key is not None and class_literal(key, home, home_scopes) and class_literal(item, home, home_scopes)
                       for key, item in zip(value.keys, value.values, strict=True))
        return False

    def plain_class(home: PythonModule, home_scopes: ScopeIndex, node: ast.ClassDef, *, methods: bool) -> bool:
        """A class whose creation runs no hook and whose body is inert data."""
        plain_bases = all(
            isinstance(base, ast.Name) and base.id == "object"
            and not home_scopes.enclosing_bindings(evaluation_site(home_scopes, base), "object")
            and not home.bindings.get("object")
            for base in node.bases
        )
        plain_body = all(
            isinstance(statement, ast.Pass)
            or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str))
            or (methods and isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef) and not statement.decorator_list)
            or (isinstance(statement, ast.Assign | ast.AnnAssign) and class_literal(statement.value, home, home_scopes))
            for statement in node.body
        )
        return (all(dataclass_decorator(home, home_scopes, item) for item in node.decorator_list)
                and not node.keywords and not getattr(node, "type_params", []) and plain_bases and plain_body)

    def callback_reference(value: ast.expr) -> bool:
        if not isinstance(value, ast.Name | ast.Attribute):
            return False
        outcome = resolver._constructor_reference(module, value, scopes)
        definition = outcome.get("definition")
        return (outcome.get("constructor_callable") is True
                and isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef)
                and not definition.decorator_list)

    for node in resolver._constructor_syntax(module).nodes:
        if id(node) in typing_only:
            continue
        if replaces_builtin_namespace(node, scopes):
            return f"constructor identity is unread through a replaced builtin namespace in {module.ref}:{node.lineno}"
        if module.path in retaining and isinstance(node, ast.ClassDef):
            # Class decorators, metaclasses and base-class hooks receive the
            # newly created class, including methods carrying this namespace.
            # Only the inert builtin object base is established by this entry.
            if not plain_class(module, scopes, node, methods=True):
                return f"an unread class construction hook receives the constructor namespace in {module.ref}:{node.lineno}"
        if module.path in retaining and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                callee = decorator.func if isinstance(decorator, ast.Call) else decorator
                outcome = resolver._constructor_reference(module, callee, scopes)
                if outcome.get("external_constructor") not in decorators:
                    return f"an unread decorator receives a function carrying the constructor namespace in {module.ref}:{node.lineno}"
                if isinstance(decorator, ast.Call) and (decorator.args or any(
                    keyword.arg is None or not (
                        isinstance(keyword.value, ast.Constant)
                        or keyword.arg == "is_enabled" and callback_reference(keyword.value)
                    )
                    for keyword in decorator.keywords
                )):
                    return f"the tool decorator's executable or unpacked configuration is unread in {module.ref}:{node.lineno}"
        if module.path in retaining and isinstance(node, ast.Lambda | ast.GeneratorExp):
            return f"an anonymous callable or generator retains the constructor namespace in {module.ref}:{node.lineno}"
        if (isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load)
                and (node.id if isinstance(node, ast.Name) else node.attr) in machinery):
            if isinstance(node, ast.Name) and node.id == "stack":
                bindings = scopes.enclosing_bindings(evaluation_site(scopes, node), node.id)
                bindings = bindings or [item.node for item in module.bindings.get(node.id, [])]
                if bindings and all(not isinstance(binding, ast.alias) for binding in bindings):
                    continue  # A lexical data binding is not inspect.stack.
            if isinstance(node, ast.Name) and node.id in {"vars", "getattr"}:
                if allow_owner_routes:
                    from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                    try:
                        if BuilderCalls(resolver).idempotent_source_slot(module, node, family):
                            continue  # Only this getter callee's independently completed source-slot proof.
                        if BuilderCalls(resolver).absent_field_dictionary_getter(module, node, family):
                            continue  # This getter has its own unread-field and zero-call receipt.
                    except CallLimit as exc:
                        return f"the source-module getter slot is unread in {module.ref}:{node.lineno}: {exc}"
                bindings = scopes.enclosing_bindings(evaluation_site(scopes, node), node.id)
                if node.id == "vars" and bindings and all(not isinstance(binding, ast.alias) for binding in bindings):
                    continue  # Function-local shadowing cannot load builtin vars.
            if allow_owner_routes and isinstance(node, ast.Name) and node.id == "vars":
                from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                try:
                    if BuilderCalls(resolver).source_saved_vars_initializer(module, node, family):
                        continue  # Only this initializer's completed saved mapping Store proof.
                except CallLimit as exc:
                    return f"the saved source getter initializer is unread in {module.ref}:{node.lineno}: {exc}"
            if allow_owner_routes and isinstance(node, ast.Name) and node.id == "setattr":
                from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                try:
                    if BuilderCalls(resolver).idempotent_source_slot(module, node, family):
                        continue  # Only this actual direct callee after its complete raw same-slot proof.
                    if BuilderCalls(resolver).source_bare_setter_initializer(module, node, family):
                        continue  # Only this actual unshadowed bare initializer's completed same-slot proof.
                except CallLimit as exc:
                    return f"the bare source setter initializer is unread in {module.ref}:{node.lineno}: {exc}"
            if allow_owner_routes and isinstance(node, ast.Attribute) and node.attr == "setattr":
                from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                try:
                    if BuilderCalls(resolver).idempotent_source_slot(module, node, family):
                        continue  # Only this exact qualified setter's independently completed same-slot proof.
                    if BuilderCalls(resolver).source_setter_initializer(module, node, family):
                        continue  # Only this actual saved initializer's independent completed same-slot proof.
                except CallLimit as exc:
                    return f"the source-module setter is unread in {module.ref}:{node.lineno}: {exc}"
            return f"constructor identity is unread through executable or reflective machinery in {module.ref}:{node.lineno}"
        if isinstance(node, ast.Import | ast.ImportFrom):
            if _unused_reflection_import(resolver, module, node):
                continue
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if any(name.split(".", 1)[0] in {"builtins", "importlib", "inspect"} for name in names):
                if allow_owner_routes and isinstance(node, ast.ImportFrom):
                    from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                    try:
                        if BuilderCalls(resolver).source_from_setter_import(module, node, family):
                            continue  # Only this actual from-setter import's complete independent same-slot proof.
                    except CallLimit as exc:
                        return f"the source setter alias import is unread in {module.ref}:{node.lineno}: {exc}"
                if allow_owner_routes and isinstance(node, ast.Import):
                    from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                    try:
                        if BuilderCalls(resolver).source_primitive_import(module, node, family):
                            continue  # This actual Import has its own completed source-slot admission.
                        if BuilderCalls(resolver).source_setter_import(module, node, family):
                            continue  # Only this actual qualified setter Import's completed raw same-slot proof.
                        if BuilderCalls(resolver).fresh_dictionary_primitive_import(module, node, family):
                            continue  # Only this actual Import's completed fresh Function-data receipt.
                        if BuilderCalls(resolver).absent_field_dictionary_primitive_import(module, node, family):
                            continue  # Only this actual Import's completed unread-field data receipt.
                    except CallLimit as exc:
                        return f"the source dictionary primitive import is unread in {module.ref}:{node.lineno}: {exc}"
                return f"constructor identity is unread through builtin or dynamic import machinery in {module.ref}:{node.lineno}"
            if isinstance(node, ast.ImportFrom) and any(alias.name in machinery for alias in node.names):
                return f"constructor identity is unread through imported reflection machinery in {module.ref}:{node.lineno}"
    if reflective_access(module.tree, excluded=frozenset(typing_only)) is not None:
        return f"constructor identity is unread through reflective namespace access in {module.ref}"
    if module.star_import and module.path in retaining:
        return f"a wildcard import may retain or replace the framework constructor in {module.ref}"
    classes = _external_constructor_paths(family) | _external_wrapper_paths(family)
    protected = classes | decorators | _external_constructor_dependency_paths(family)

    def scalar(expression: ast.expr | None) -> bool:
        if expression is None or isinstance(expression, ast.Constant):
            return True
        if isinstance(expression, ast.List | ast.Tuple | ast.Set):
            return all(scalar(item) for item in expression.elts)
        if isinstance(expression, ast.Dict):
            return all(key is not None and scalar(key) and scalar(value)
                       for key, value in zip(expression.keys, expression.values, strict=True))
        return False

    def ordinary_external_return_call(home: PythonModule, value: ast.Call, home_scopes: ScopeIndex, depth: int = 0) -> bool:
        # Inline ordinary external call results already lie outside this
        # finite namespace proof. A source-local helper can preserve that
        # boundary, but an unresolved local callee or retained result cannot.
        if depth >= 4:
            return False
        if isinstance(value.func, ast.Attribute) and isinstance(value.func.value, ast.Call):
            if value.func.attr in machinery:
                return False
            return ordinary_external_return_call(home, value.func.value, home_scopes, depth + 1)
        spelling = reference_spelling(value.func)
        if spelling is None:
            return False
        head, _, rest = spelling.partition(".")
        bindings = home_scopes.enclosing_bindings(evaluation_site(home_scopes, value.func), head)
        bindings = bindings or [binding.node for binding in home.bindings.get(head, [])]
        if home.star_import or len(bindings) != 1 or not isinstance(alias := bindings[0], ast.alias):
            return False
        statement = home_scopes.statement_of(alias)
        if (not isinstance(statement, ast.Import | ast.ImportFrom)
                or not isinstance(home_scopes.parents.get(statement), ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                or statement.lineno > value.lineno):
            return False
        imported = _absolute_import_reference(alias, statement)
        if imported is None:
            return False
        projected = imported + ("." + rest if rest else "")
        if any(projected == path or projected.startswith(path + ".") or path.startswith(projected + ".")
               for path in protected | _external_constructor_dependency_modules(family)):
            return False
        outcome = resolver._constructor_reference(home, value.func, home_scopes)
        if outcome.get("constructor_unread") != MODULE_NOT_FOUND or outcome.get("unread_external_handle") is not True:
            return False
        try:
            return resolver._external_provider_issue(home, statement, alias) is None
        except _Stop:
            return False

    def safe_function(home: PythonModule, function: ast.AST, depth: int = 0, seen: frozenset[int] = frozenset()) -> bool:
        if (not isinstance(function, ast.FunctionDef) or function.decorator_list
                or depth >= 4 or id(function) in seen
                or any(isinstance(node, ast.Yield | ast.YieldFrom | ast.GeneratorExp) for node in ast.walk(function))):
            return False
        home_scopes = resolver._constructor_scope(home)
        for statement in ast.walk(function):
            if not isinstance(statement, ast.Return) or scalar(statement.value):
                continue
            enclosing = home_scopes.parents.get(statement)
            while enclosing is not None and not isinstance(enclosing, ast.FunctionDef | ast.AsyncFunctionDef):
                enclosing = home_scopes.parents.get(enclosing)
            if enclosing is not function:
                continue
            value = statement.value
            if allow_owner_routes and isinstance(value, ast.Name):
                outcome = resolver._constructor_reference(home, value, home_scopes)
                if isinstance(outcome.get("definition"), ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                assigned = outcome.get("value")
                if isinstance(assigned, ast.Call):
                    target = resolver._constructor_reference(home, assigned.func, home_scopes)
                    if target.get("external_constructor") in classes:
                        continue
            if allow_owner_routes and isinstance(value, ast.List | ast.Tuple | ast.Dict):
                continue  # Every retained member and returned container is proved below.
            if not isinstance(value, ast.Call):
                return False
            if ordinary_external_return_call(home, value, home_scopes):
                continue  # Only the call role; its arguments and every use remain checked.
            if (allow_owner_routes and isinstance(value.func, ast.Attribute) and value.func.attr == "as_tool"
                    and isinstance(receiver := value.func.value, ast.Name)
                    and len(parameter := home_scopes.enclosing_bindings(
                        evaluation_site(home_scopes, receiver), receiver.id)) == 1
                    and isinstance(parameter[0], ast.arg)):
                # ``return agent.as_tool(...)`` on the function's own parameter.
                # The tool object it builds has an owner registered where the
                # agent handle is passed in; no namespace is returned here.
                continue
            callee = value.func.value if isinstance(value.func, ast.Subscript) else value.func
            outcome = resolver._constructor_reference(home, callee, home_scopes)
            if allow_owner_routes and outcome.get("external_constructor") in classes:
                continue
            if (allow_owner_routes and isinstance(tool_class := outcome.get("external_constructor"), str)
                    and _framework_tool_object(family, tool_class) in {"object", "toolset", "wrapper"}):
                continue  # A returned tool object has its own owner, registered where it is built.
            defining, nested = outcome.get("module"), outcome.get("definition")
            if not isinstance(defining, PythonModule) or not safe_function(defining, nested, depth + 1, seen | {id(function)}):
                return False
        return True

    unread_external_nodes: set[int] = set()
    plain_instance_nodes: set[int] = set()

    def plain_instance(node: ast.expr, current: ast.expr, home: object, outcome: dict[str, Any]) -> bool:
        # ``Settings()`` of an inert dataclass is an instance of plain data. It
        # still carries its class, so its result has an owner like any handle.
        if not (allow_owner_routes and current is node and isinstance(home, PythonModule)
                and isinstance(definition := outcome.get("retained_class"), ast.ClassDef)
                and plain_class(home, resolver._constructor_scope(home), definition, methods=False)):
            return False
        plain_instance_nodes.add(id(node))
        return True

    def references(node: ast.expr) -> tuple[set[str], bool, bool]:
        spelling = reference_spelling(node)
        head = spelling.split(".", 1)[0] if spelling else None
        local = scopes.enclosing_bindings(evaluation_site(scopes, node), head) if head else []
        candidates = local or [binding.node for binding in module.bindings.get(head, [])]
        if any(isinstance(binding, ast.alias) and resolver._unread_import_candidate(
            module, scopes.statement_of(binding), binding
        ) for binding in candidates):
            unread_external_nodes.add(id(node))
        current = node
        prefix_reads = 0
        while True:
            if prefix_reads >= MAX_STEPS:
                raise _Stop(
                    RESOLUTION_LIMIT,
                    f"constructor ownership is unread in {module.ref}:{node.lineno}: "
                    f"the unresolved attribute path exceeds {MAX_STEPS} prefix reads",
                )
            prefix_reads += 1
            outcome = resolver._constructor_reference(module, current, scopes)
            if outcome.get("unread_external_handle"):
                unread_external_nodes.add(id(node))
            if outcome.get("local_constructor_provider") is not None:
                raise _Stop(REBOUND_NAME, str(outcome["local_constructor_provider"]))
            if outcome.get("constructor_unread") in {RESOLUTION_LIMIT, LINKED_MODULE, UNREADABLE_MODULE, OUTSIDE_SCOPE, AMBIGUOUS_MODULE, IMPORT_CYCLE}:
                raise _Stop(str(outcome["constructor_unread"]), str(outcome["detail"]))
            external = outcome.get("external_constructor")
            if isinstance(external, str):
                if current is not node:
                    spelling, prefix = reference_spelling(node), reference_spelling(current)
                    if spelling is not None and prefix is not None and spelling.startswith(prefix + "."):
                        external += spelling[len(prefix):]
                return {external}, False, False
            namespace = outcome.get("retained_namespace")
            home = outcome.get("module")
            namespace_holder = outcome.get("definition") is not None or outcome.get("retained_class") is not None
            path = namespace if isinstance(namespace, Path) else home.path if isinstance(home, PythonModule) and namespace_holder else None
            if path is not None and (path in retaining or any(item.is_relative_to(path) for item in retaining)):
                return {family}, (
                    current is node and outcome.get("constructor_callable") is True
                    and isinstance(home, PythonModule) and safe_function(home, outcome.get("definition"))
                ) or plain_instance(node, current, home, outcome), current is node and outcome.get("constructor_callable") is True
            if (current is node and isinstance(node.ctx, ast.Load) and isinstance(home, PythonModule)
                    and isinstance(outcome.get("value"), ast.Constant)):
                unread_external_nodes.discard(id(node))
                return set(), False, False  # An exact repository literal is lexical data.
            if not isinstance(current, ast.Attribute):
                spelling = reference_spelling(node)
                head = spelling.split(".", 1)[0] if spelling else None
                local = scopes.enclosing_bindings(evaluation_site(scopes, node), head) if head else []
                found = local or [binding.node for binding in module.bindings.get(head, [])]
                if isinstance(node, ast.Name) and not local and len(found) == 2:
                    definition, target = found
                    statement = scopes.statement_of(target)
                    wrapper = getattr(statement, "value", None)
                    body = module.tree.body
                    if (isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef)
                            and not definition.decorator_list and isinstance(target, ast.Name)
                            and isinstance(statement, ast.Assign) and statement.targets == [target]
                            and definition in body and statement in body
                            and body.index(statement) == body.index(definition) + 1
                            and isinstance(wrapper, ast.Call) and not wrapper.args
                            and len(wrapper.keywords) == 1 and wrapper.keywords[0].arg == "func"
                            and isinstance(operand := wrapper.keywords[0].value, ast.Name)
                            and operand.id == node.id
                            and resolver._constructor_reference(module, wrapper.func, scopes).get("external_constructor")
                            in _external_wrapper_paths(family)):
                        resolver._constructor_wrapped_operands.add(id(operand))
                        if node is operand:
                            return {family}, False, True
                        if node.lineno > statement.lineno:
                            # The wrapper's actual handle is checked by its
                            # registered constructor owner, including all uses.
                            return set(), False, False
                possible = {path + spelling[len(head):] for binding in found if isinstance(binding, ast.alias)
                            if (path := _absolute_import_reference(binding, scopes.statement_of(binding))) is not None}
                def possible_holder(bindings: list[ast.AST], depth: int = 0, seen: frozenset[int] = frozenset()) -> bool:
                    if depth >= 4:
                        return True  # An unfinished alias proof cannot clear a namespace.
                    for binding in bindings:
                        if id(binding) in seen:
                            return True
                        if isinstance(binding, ast.alias) and resolver._unread_import_candidate(
                            module, scopes.statement_of(binding), binding
                        ):
                            unread_external_nodes.add(id(node))
                        if isinstance(binding, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                            return True
                        statement = scopes.statement_of(binding)
                        value = getattr(statement, "value", None)
                        alias = reference_spelling(value) if isinstance(value, ast.expr) else None
                        if isinstance(binding, ast.Name) and alias is not None:
                            target = alias.split(".", 1)[0]
                            candidates = scopes.enclosing_bindings(evaluation_site(scopes, value), target)
                            candidates = candidates or [item.node for item in module.bindings.get(target, [])]
                            if possible_holder(candidates, depth + 1, seen | {id(binding)}):
                                return True
                    return False
                holder = possible_holder(found)
                if module.path in retaining and holder:
                    possible.add(family)
                if outcome.get("constructor_unread") is not None:
                    for binding in found:
                        statement = scopes.statement_of(binding)
                        if isinstance(binding, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
                            paths, _ = resolver._imported_paths(module, statement)
                            if any(path in retaining or any(item.is_relative_to(path) for item in retaining) for path in paths):
                                possible.add(family)
                ambiguous_functions = bool(found) and all(isinstance(binding, ast.FunctionDef | ast.AsyncFunctionDef) for binding in found)
                # This does not resolve the tool's identity. It only permits
                # its finite candidate functions to remain in a proven owning
                # field, so their ambiguous name cannot poison other agents.
                return possible, False, ambiguous_functions and current is node
            current = current.value

    deferred = any(isinstance(statement, ast.ImportFrom) and statement.module == "__future__"
                   and any(alias.name == "annotations" for alias in statement.names)
                   for statement in module.tree.body)
    for node in resolver._constructor_syntax(module).references:
        if not isinstance(node, ast.Name | ast.Attribute) or id(node) in typing_only:
            continue
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            continue  # A local rebinding/deletion does not mutate the imported object.
        parent = scopes.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue  # The terminal spelling decides; do not repeatedly resolve every prefix.
        paths, known_function, function_holder = references(node)
        relevant = {path for path in paths if any(
            path == constructor or constructor.startswith(path + ".") or path.startswith(constructor + ".")
            for constructor in protected
        )}
        # A framework or dependency helper can participate in construction without being a
        # supported receiving class. Mutating its external namespace cannot
        # prove the original constructor; ordinary unrelated reads stay outside
        # this dependency set.
        framework_mutation = not isinstance(node.ctx, ast.Load) and any(
            path == root or path.startswith(root + ".") or root.startswith(path + ".")
            for path in paths
            for root in {family} | _external_constructor_dependency_modules(family)
        )
        if not relevant and not framework_mutation:
            if id(node) not in unread_external_nodes:
                continue
            parent = scopes.parents.get(node)
            if isinstance(parent, ast.Attribute) and parent.value is node:
                continue
            if allow_owner_routes and isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                try:
                    if BuilderCalls(resolver).source_setter_initializer(module, node, family):
                        continue  # Only this actual saved initializer's complete independent same-slot proof.
                    if BuilderCalls(resolver).source_saved_bound_ior_initializer(module, node, family):
                        continue  # Only this actual saved method initializer's completed ownership proof.
                except CallLimit as exc:
                    return f"the saved source setter initializer is unread in {module.ref}:{node.lineno}: {exc}"
            if allow_owner_routes and isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

                try:
                    if BuilderCalls(resolver).operator_metadata_write(module, node, family):
                        continue  # Only this actual Store's explicit independent completed admission.
                except CallLimit as exc:
                    return f"the operator metadata write is unread in {module.ref}:{node.lineno}: {exc}"
            if isinstance(node.ctx, ast.Load) and (
                isinstance(parent, ast.Expr) or isinstance(parent, ast.Call) and parent.func is node
            ):
                continue  # Ordinary external callees/results retain their existing boundary.
            if allow_owner_routes and (stdlib := _stdlib_reference(resolver, module, node, scopes)) is not None:
                if (stdlib == "os.environ" and isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                        and isinstance(parent, ast.Subscript) and parent.value is node
                        and isinstance(parent.ctx, ast.Load)
                        and _environment_value_role(resolver, module, parent, scopes, family)):
                    continue  # A read of one environment value is a string; the mapping goes nowhere.
                if (stdlib == "dataclasses.dataclass" and isinstance(parent, ast.ClassDef)
                        and node in parent.decorator_list):
                    continue  # The class hook itself is checked, with the class, where it stands.
            annotation: ast.AST = node
            while isinstance(scopes.parents.get(annotation), ast.Subscript | ast.BinOp | ast.Tuple | ast.List):
                annotation = scopes.parents[annotation]
            owner = scopes.parents.get(annotation)
            if deferred and ((isinstance(owner, ast.arg | ast.AnnAssign) and owner.annotation is annotation)
                             or isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef) and owner.returns is annotation):
                continue
            return f"an unread external imported handle is retained or changed in {module.ref}:{node.lineno}"
        parent = scopes.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue  # The longest spelling's own use decides.
        if (allow_owner_routes and isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and node.attr == "__ior__"):
            from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

            try:
                if BuilderCalls(resolver).source_saved_bound_ior_initializer(module, node, family):
                    continue  # Only this selected saved method's independent completed same-slot proof.
            except CallLimit as exc:
                return f"the saved source method initializer is unread in {module.ref}:{node.lineno}: {exc}"
        if allow_owner_routes and (function_holder or isinstance(node, ast.Name)
                                   or isinstance(node, ast.Attribute) and node.attr == "__dict__"):
            from agents_shipgate.inputs.builder_calls import BuilderCalls, CallLimit

            try:
                if BuilderCalls(resolver).idempotent_source_slot(module, node, family):
                    continue  # Only this exact completed source-function declaration edge.
                if isinstance(node, ast.Attribute) and BuilderCalls(resolver).absent_field_dictionary_projection(module, node, family):
                    continue  # Only the selected intrinsic projection with its own completed unread-field receipt.
                if isinstance(node, ast.Name) and BuilderCalls(resolver).absent_field_dictionary_namespace(module, node, family):
                    continue  # Only the exact getter argument with its own completed unread-field receipt.
            except CallLimit as exc:
                return f"the source-module slot is unread in {module.ref}:{node.lineno}: {exc}"
        if not isinstance(node.ctx, ast.Load):
            if _independent_foreign_class_slot_write(resolver, module, node, scopes, family):
                continue  # Every RHS and other use remains independently checked.
            return f"a framework constructor or namespace is changed in {module.ref}:{node.lineno}"
        if (isinstance(parent, ast.If) and parent.test is node
                and _canonical_type_checking_test(module, node, scopes, resolver)):
            continue  # A false boolean guard grants no retained typing handle.
        if (family == "google.adk" and paths <= {"pydantic.Field", "pydantic.fields.Field"}
                and resolver._constructor_reference(module, node, scopes).get("external_constructor") in paths
                and isinstance(parent, ast.Call) and parent.func is node
                and not parent.args and len(parent.keywords) == 1
                and parent.keywords[0].arg == "default"
                and isinstance(parent.keywords[0].value, ast.Constant)
                and parent.keywords[0].value.value is None):
            # A narrow value call still creates a shared-class-carrying handle.
            # Its distinct data role proves every result use without borrowing
            # Agent capability fields or builtin-container method allowances.
            resolver._constructor_namespace_owners[(family, id(parent), "field_data")] = (module, parent, "field_data")
            continue
        decorated = parent
        if isinstance(parent, ast.Call) and parent.func is node:
            decorated = scopes.parents.get(parent)
        if (relevant <= decorators and isinstance(decorated, ast.FunctionDef | ast.AsyncFunctionDef)
                and (node in decorated.decorator_list or parent in decorated.decorator_list)):
            if not allow_owner_routes:
                return f"constructor decorator ownership is unread in {module.ref}:{node.lineno}"
            continue  # The unchanged SDK decorator owns this implicit callback edge.
        retained = resolver._constructor_reference(module, node, scopes)
        if isinstance(retained.get("value"), ast.List | ast.Tuple) and isinstance(retained.get("module"), PythonModule):
            # A namespace-qualified literal holder is a container obligation,
            # never permission to export the surrounding module namespace.
            resolver._constructor_container_owners[id(retained["value"])] = (retained["module"], retained["value"])
            continue
        if isinstance(parent, ast.Call) and parent.func is node and id(node) in plain_instance_nodes:
            resolver._constructor_namespace_owners[(family, id(parent), "instance_data")] = (module, parent, "instance_data")
            continue  # An instance of inert data; every use of the result is read by its owner.
        if isinstance(parent, ast.Call) and parent.func is node and (relevant <= classes or known_function):
            if relevant <= classes:
                role = "__class__" if relevant <= _external_constructor_paths(family) else "wrapper_class"
                resolver._constructor_namespace_owners[(family, id(parent), role)] = (module, parent, role)
            continue
        if (family == "google.adk" and allow_owner_routes and isinstance(parent, ast.Call) and parent.func is node
                and isinstance(canonical := resolver._constructor_reference(module, node, scopes).get("external_constructor"), str)
                and paths == {canonical} and _adk_toolset_value_call(resolver, module, parent, scopes, canonical)):
            resolver._constructor_namespace_owners[(family, id(parent), "toolset_data")] = (module, parent, "toolset_data")
            continue
        if (family == "google.adk" and isinstance(parent, ast.Call) and parent.func is node
                and isinstance(canonical := retained.get("external_constructor"), str)
                and paths == {canonical} and _framework_connection_class(canonical)
                and isinstance(keyword := scopes.parents.get(parent), ast.keyword) and keyword.arg == "connection_params"
                and isinstance(toolset := scopes.parents.get(keyword), ast.Call)
                and isinstance(toolset_class := resolver._constructor_reference(module, toolset.func, scopes).get("external_constructor"), str)
                and _adk_toolset_value_call(resolver, module, toolset, scopes, toolset_class)):
            continue  # The toolset call is the one data role; this is its connection data.
        if (allow_owner_routes and isinstance(canonical := retained.get("external_constructor"), str)
                and paths == {canonical} and (kind := _framework_tool_object(family, canonical)) is not None
                and _own_tool_object(resolver, module, node, parent, scopes, family, kind, classes, callback_fields)):
            continue  # An exact framework tool object whose every use its owner census checks.
        if (allow_owner_routes and isinstance(node, ast.Name) and isinstance(parent, ast.Assign)
                and parent.value is node and len(parent.targets) == 1
                and isinstance(parent.targets[0], ast.Name)
                and isinstance(scopes.parents.get(parent), ast.Module)):
            imported = module.bindings.get(node.id, [])
            if (len(imported) == 1 and isinstance(imported[0].node, ast.alias)
                    and isinstance(imported[0].statement, ast.Import)):
                resolver._constructor_unused_namespace_sinks[id(node)] = (module, node)
                continue  # Only an obligation; provider, destination and exports must be proved.
        literal_allocation = (
            isinstance(parent, ast.Dict) and node in parent.values
            and isinstance(allocation := scopes.parents.get(parent), ast.Assign)
            and allocation.value is parent and len(allocation.targets) == 1
            and isinstance(allocation.targets[0], ast.Name)
            and isinstance(scopes.parents.get(allocation), ast.Module)
        )
        if (allow_owner_routes and literal_allocation
                and isinstance(canonical := retained.get("external_constructor"), str)
                and paths == {canonical} and canonical in _external_constructor_paths(family)):
            # A passive class token has its own data obligation. It grants no
            # constructor call, result, function or surrounding namespace role.
            resolver._constructor_dictionary_class_sinks[(family, id(node))] = (module, node, family, canonical)
            continue
        if function_holder:
            dictionary_value = (isinstance(parent, ast.Assign) and parent.value is node
                                and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Subscript))
            if isinstance(parent, ast.keyword) and parent.arg is not None:
                receiver = scopes.parents.get(parent)
                dictionary_value |= (isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Attribute)
                                     and receiver.func.attr in {"update", "__init__"})
            if isinstance(parent, ast.Dict) and node in parent.values:
                receiver = scopes.parents.get(parent)
                dictionary_value |= (
                    literal_allocation
                    or isinstance(receiver, ast.AugAssign) and isinstance(receiver.op, ast.BitOr)
                    or isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Attribute)
                    and receiver.func.attr in {"update", "__init__"} and receiver.args == [parent]
                )
            if (allow_owner_routes and dictionary_value
                    and isinstance(retained.get("definition"), ast.FunctionDef | ast.AsyncFunctionDef)):
                # A candidate data edge, never a grant. The list reader must
                # independently prove dictionary confinement and then every
                # remaining use of the actual function carrying this namespace.
                resolver._constructor_dictionary_sinks[(family, id(node))] = (module, node, family)
                continue
            if (isinstance(parent, ast.keyword) and parent.arg == "is_enabled"
                    and isinstance(receiver := scopes.parents.get(parent), ast.Call)
                    and isinstance(owner := scopes.parents.get(receiver), ast.FunctionDef | ast.AsyncFunctionDef)
                    and receiver in owner.decorator_list
                    and resolver._constructor_reference(module, receiver.func, scopes).get("external_constructor") in decorators):
                resolver._constructor_decorator_owners[id(owner)] = (module, owner)
                continue  # Prove all uses of the actual decorated tool handle.
            if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute)
                    and parent.func.attr in {"append", "extend", "insert"} and node in parent.args):
                # This is only a deferred obligation. The list reader must
                # prove the actual receiver's builtin shape and every use.
                resolver._constructor_member_sinks[id(parent.func.value)] = (module, parent.func.value)
                continue
            if isinstance(parent, ast.Call) and parent.args == [node]:
                target = resolver._constructor_reference(module, parent.func, scopes)
                wrapper = target.get("external_constructor")
                if wrapper in _external_wrapper_paths(family):
                    resolver._constructor_namespace_owners[(family, id(parent), "func")] = (module, parent, "func")
                    continue
                if isinstance(wrapper, str) and _framework_tool_object(family, wrapper) == "wrapper":
                    # The SDK's ``function_tool(f)``: the operand is the wrapped function and
                    # its result is the tool object, one owner for both.
                    resolver._constructor_namespace_owners[(family, id(parent), "tool_wrapper")] = (module, parent, "tool_wrapper")
                    continue
            container: ast.AST = node
            while isinstance(scopes.parents.get(container), ast.List | ast.Tuple):
                container = scopes.parents[container]
            keyword = scopes.parents.get(container)
            receiver = scopes.parents.get(keyword)
            if (isinstance(keyword, ast.keyword) and keyword.arg in {"tools", "handoffs", "sub_agents", "mcp_servers", "func"} | callback_fields
                    and isinstance(receiver, ast.Call)):
                expression = receiver.func.value if isinstance(receiver.func, ast.Subscript) else receiver.func
                target = resolver._constructor_reference(module, expression, scopes)
                if target.get("external_constructor") in classes:
                    resolver._constructor_namespace_owners[(family, id(receiver), keyword.arg)] = (module, receiver, keyword.arg)
                    continue  # ListExpressions proves ownership of this receiving handle.
            if isinstance(container, ast.List | ast.Tuple):
                resolver._constructor_container_owners[id(container)] = (module, container)
                continue  # Follow every use of this container, including unselected lists.
            if isinstance(parent, ast.Return):
                resolver._constructor_container_owners[id(node)] = (module, node)
                continue  # Follow the factory result through its complete caller set.
        if isinstance(parent, ast.Subscript) and parent.value is node and relevant <= classes:
            outer = scopes.parents.get(parent)
            if isinstance(outer, ast.Call) and outer.func is parent:
                role = "__class__" if relevant <= _external_constructor_paths(family) else "wrapper_class"
                resolver._constructor_namespace_owners[(family, id(outer), role)] = (module, outer, role)
                continue
        annotation: ast.AST = node
        while isinstance(scopes.parents.get(annotation), ast.Subscript | ast.BinOp | ast.Tuple | ast.List):
            annotation = scopes.parents[annotation]
        owner = scopes.parents.get(annotation)
        if ((isinstance(owner, ast.arg | ast.AnnAssign) and owner.annotation is annotation)
                or (isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef) and owner.returns is annotation)):
            if deferred:
                continue
            canonical = resolver._constructor_reference(module, node, scopes).get("external_constructor")
            if (canonical in {f"typing.{name}" for name in _TYPING_ANNOTATION_VALUES}
                    and paths == {canonical}):
                continue
            if (family == "google.adk" and allow_owner_routes and isinstance(owner, ast.arg)
                    and owner.annotation is node and paths == {canonical}
                    and canonical in {"google.adk.tools.ToolContext", "google.adk.tools.tool_context.ToolContext"}):
                continue  # ADK identifies this exact context type; it does not construct it.
            if (allow_owner_routes and annotation is node and isinstance(canonical, str) and paths == {canonical}
                    and _eager_annotation_owned(resolver, scopes, owner, family, canonical)):
                continue  # Nothing in the census reads the annotation dictionary it lands in.
            return f"an eager annotation retains the framework constructor in {module.ref}:{node.lineno}"
        if isinstance(parent, ast.Expr):
            continue  # A bare expression does not hand the object on.
        return f"a framework constructor or namespace is retained or used opaquely in {module.ref}:{node.lineno}"
    return None


def _module_table_key(key: ast.AST | None) -> str:
    """How a ``sys.modules`` key is spelled: literal, the module's own name
    with a literal suffix, built on ``__name__`` some other way, or computed."""

    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return MODULE_TABLE_LITERAL + key.value
    if isinstance(key, ast.Name) and key.id == "__name__":
        return MODULE_TABLE_OWN + "="
    suffix: ast.AST | None = None
    if (
        isinstance(key, ast.BinOp)
        and isinstance(key.op, ast.Add)
        and isinstance(key.left, ast.Name)
        and key.left.id == "__name__"
    ):
        suffix = key.right
    elif (
        isinstance(key, ast.JoinedStr)
        and len(key.values) == 2
        and isinstance(key.values[0], ast.FormattedValue)
        and isinstance(key.values[0].value, ast.Name)
        and key.values[0].value.id == "__name__"
    ):
        suffix = key.values[1]
    if isinstance(suffix, ast.Constant) and isinstance(suffix.value, str):
        return MODULE_TABLE_OWN + "=" + suffix.value
    if key is not None and any(
        isinstance(node, ast.Name) and node.id == "__name__" for node in ast.walk(key)
    ):
        return MODULE_TABLE_OWN + "?"
    return MODULE_TABLE_COMPUTED


def _module_name(ref: str) -> str:
    """``pkg/memory.py`` -> ``pkg.memory``; ``pkg/__init__.py`` -> ``pkg``."""

    parts = ref.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _key_names(stored: str, scoped: set[str], full: set[str]) -> bool:
    """Whether a ``sys.modules`` key can name a module on the chain, a package
    enclosing one, or a framework module that builds its tools (#879 review).

    ``scoped`` names are relative to the scope, whose own prefix is unknown:
    the key's tail must be their head. ``full`` names are from the repository
    root: the key may be one of them or a package above one.
    """

    parts = stored.split(".")
    for module in scoped:
        head = module.split(".") if module else []
        if any(parts[-size:] == head[:size] for size in range(1, min(len(parts), len(head)) + 1)):
            return True
    for module in (*full, *_FRAMEWORK_MODULES):
        if stored == module or module.startswith(stored + ".") or stored.startswith(module + "."):
            return True
    return False


def _root_imports(module: PythonModule, root: str) -> list[ast.Import | ast.ImportFrom]:
    """The imports a patch's root name is bound by, through one plain alias
    (``_t = tools`` then ``_t.lookup = ...``, #879 review)."""

    def imports_of(name: str) -> list[ast.Import | ast.ImportFrom]:
        return [
            binding.statement
            for binding in module.bindings.get(name, [])
            if isinstance(binding.node, ast.alias)
            and isinstance(binding.statement, ast.Import | ast.ImportFrom)
        ]

    found = imports_of(root)
    if found:
        return found
    for binding in module.bindings.get(root, []):
        statement = binding.statement
        if (
            isinstance(statement, ast.Assign | ast.AnnAssign)
            and isinstance(statement.value, ast.Name)
            and statement.value.id != root
        ):
            found += imports_of(statement.value.id)
    return found


def _module_object(node: ast.AST, sys_names: set[str], modules_names: set[str]) -> bool:
    """``sys.modules[__name__]``, ``sys.modules.get(__name__)`` or
    ``importlib.import_module(__name__)``: the module itself."""

    def is_name(item: ast.AST | None) -> bool:
        return isinstance(item, ast.Name) and item.id == "__name__"

    tables = {f"{name}.modules" for name in sys_names} | modules_names
    if isinstance(node, ast.Subscript):
        return reference_spelling(node.value) in tables and is_name(node.slice)
    if isinstance(node, ast.Call) and node.args and is_name(node.args[0]):
        spelling = reference_spelling(node.func) or ""
        if spelling in {"importlib.import_module", "import_module", "__import__"}:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            return reference_spelling(node.func.value) in tables
    return False


def _table_verdict(
    key: str,
    where: str,
    scoped: set[str],
    full: set[str],
    prefix: str,
    *,
    repository_ref: bool = False,
) -> str | None:
    """What a store into ``sys.modules`` means for a chain: ``"stop"`` when its
    key can name a module on it, ``"caveat"`` when computed, else None.
    ``where`` is scope-relative, or from the repository root when
    ``repository_ref``."""

    if key == MODULE_TABLE_COMPUTED:
        return "caveat"
    own = _module_name(where)
    own_full = f"{prefix}.{own}" if prefix and not repository_ref else own
    if key.startswith(MODULE_TABLE_OWN + "="):
        stored = own + key[len(MODULE_TABLE_OWN) + 1 :]
    elif key.startswith(MODULE_TABLE_LITERAL):
        stored = key[len(MODULE_TABLE_LITERAL) :]
    elif key == MODULE_TABLE_OWN + "?":
        # ``__name__ + name``: only the storing module's own descendants.
        chain = {*scoped, *full}
        return "stop" if any(
            item == own or item.startswith(own + ".") or item.startswith(own_full + ".")
            for item in chain
        ) else None
    else:
        return None
    return "stop" if _key_names(stored, scoped, full) else None


@dataclass
class _AboveScope:
    """What the packages enclosing the scope, above it, run first (#879 review)."""

    patched: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    tables: list[tuple[str, int, str]] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)
    #: Actual enclosing-package import closure, retaining raw canonical imports
    #: even when the repository does not provide their external modules.
    modules: dict[str, PythonModule] = field(default_factory=dict)
    #: In-scope modules they import, by repository path: the in-scope reader
    #: reads these as it reads the chain's own modules.
    inscope: list[str] = field(default_factory=list)


def _import_search_effects(
    tree: ast.Module, *, excluded: frozenset[int] = frozenset(),
    projections: dict[ast.AST, str] | None = None,
    scopes: ScopeIndex | None = None,
) -> dict[ast.AST, str]:
    """Actual imported search handles whose effects or retention are unread.

    This is syntax and lexical identity only. It neither invokes a receiving
    proof nor grants a native callable role. Projection rows must come from
    the private source-import identity walk, never inferred attribute spelling.
    """
    from agents_shipgate.inputs.list_expressions import evaluation_site

    scopes = scopes or ScopeIndex(tree)
    bindings = {
        name: [binding.node for binding in group]
        for name, group in _module_bindings(tree)[0].items()
    }
    # Lists are the actual lexical groups; retain each anchor for this invocation.
    # This is a structural import projection, never a receiving or purity proof.
    imports_by_group: dict[int, tuple[list[ast.AST], tuple[str, ...]]] = {}
    effects: dict[ast.AST, str] = {}
    sensitive = {"path", "meta_path", "path_hooks", "path_importer_cache"}

    def watched(path: str) -> bool:
        return path == "sys" or path == "site" or path.startswith("site.") or any(
            path == "sys." + field or path.startswith("sys." + field + ".")
            for field in sensitive | {"__dict__", "__class__", "__getattr__", "__getattribute__", "__setattr__"}
        )

    def read_only(node: ast.AST, path: str) -> bool:
        parent = scopes.parents.get(node)
        if isinstance(parent, ast.Expr):
            return path in {"sys", "site"} or path in {"sys." + field for field in sensitive}
        if path not in {"sys." + field for field in sensitive}:
            return False
        if isinstance(parent, ast.Subscript) and parent.value is node:
            return (isinstance(parent.ctx, ast.Load) and isinstance(parent.slice, ast.Constant)
                    and type(parent.slice.value) is int and isinstance(scopes.parents.get(parent), ast.Expr))
        if isinstance(parent, ast.Compare) and len(parent.ops) == 1:
            return (isinstance(parent.ops[0], ast.In | ast.NotIn) and parent.comparators == [node]
                    and isinstance(parent.left, ast.Constant) and isinstance(parent.left.value, str))
        return False

    def imported_paths(group: list[ast.AST]) -> tuple[str, ...]:
        key = id(group)
        cached = imports_by_group.get(key)
        if cached is not None:
            assert cached[0] is group
            return cached[1]
        paths = set()
        for candidate in group:
            if isinstance(candidate, ast.alias) and id(candidate) not in excluded:
                imported = _absolute_import_reference(candidate, scopes.statement_of(candidate))
                if imported is not None and watched(imported):
                    paths.add(imported)
        result = tuple(sorted(paths))
        imports_by_group[key] = (group, result)
        return result

    for node in ast.walk(tree):
        if id(node) in excluded:
            continue
        if isinstance(node, ast.alias):
            statement = scopes.statement_of(node)
            if isinstance(statement, ast.ImportFrom) and not statement.level:
                path = _absolute_import_reference(node, statement)
                if path is not None and watched(path):
                    effects[node] = path  # An imported handle can be reexported without a Name read.
            continue
        if not isinstance(node, ast.Name | ast.Attribute):
            continue
        parent = scopes.parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue  # One maximal reference, never repeated dotted prefixes.
        parts = _dotted(node)
        if parts is None:
            continue
        candidates = scopes.enclosing_bindings(evaluation_site(scopes, node), parts[0])
        if not candidates:
            candidates = bindings.get(parts[0], [])
        projected = projections.get(node) if projections else None
        if projected is not None and watched(projected) and not read_only(node, projected):
            effects[node] = projected
            continue
        suffix = "." + ".".join(parts[1:]) if len(parts) > 1 else ""
        for imported in imported_paths(candidates):
            path = imported + suffix
            if watched(path) and not read_only(node, path):
                effects[node] = path
                break
    return effects


def _attribute_patches(
    tree: ast.Module, *, excluded: frozenset[int] = frozenset(),
    patch_producers: dict[str, set[ast.AST | None]] | None = None,
) -> dict[str, int]:
    """What running a module may reassign, as ``key -> line``.

    ``a.b`` for an attribute store; ``*=...`` / ``*self...`` / ``*?`` for a
    store into ``sys.modules`` by key; ``<self>.name`` for the module rebinding
    its own ``name`` through ``globals()`` or ``sys.modules[__name__]``;
    ``<path>`` for a change to ``__path__``. A use of ``sys.modules`` or
    ``globals()`` that is not a known read counts as a computed store: what it
    changes is not read (#879 review). Optional producer evidence records every
    origin; None means an origin this reader does not identify. It never changes
    the ordinary marker map or its first line.
    """

    patches: dict[str, int] = {}
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    sys_names = {"sys"} | {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "sys" and alias.asname
    }
    modules_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sys" and not node.level
        for alias in node.names
        if alias.name == "modules"
    }
    # The lazy-submodule idiom's own cache, ``globals()[name] = module``.
    idiom_cache: set[int] = set()
    for item in tree.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__getattr__" and item.args.args:
            parameter = item.args.args[0].arg
            idiom_cache |= {
                id(node)
                for node in ast.walk(item)
                if isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Name)
                and node.slice.id == parameter
            }

    shadowing: list[tuple[ScopeIndex, dict[str, list[_Binding]]]] = []

    def builtin(node: ast.Name) -> bool:
        """Whether a bare ``globals`` / ``vars`` is the builtin, not a variable
        of that name (``vars = stack[-2][-3]``)."""

        scopes, module_bindings = bindings_of()

        def keeps_builtin(statement: ast.AST | None) -> bool:
            # ``globals = globals`` or ``globals = builtins.globals``.
            value = getattr(statement, "value", None)
            return isinstance(statement, ast.Assign | ast.AnnAssign) and (
                (isinstance(value, ast.Name) and value.id == node.id)
                or reference_spelling(value) == f"builtins.{node.id}"
            )

        local = scopes.enclosing_bindings(node, node.id)
        if local:
            return all(keeps_builtin(scopes.statement_of(item)) for item in local)
        return all(keeps_builtin(item.statement) for item in module_bindings.get(node.id, []))

    def bindings_of() -> tuple[ScopeIndex, dict[str, list[_Binding]]]:
        if not shadowing:
            shadowing.append((ScopeIndex(tree), _module_bindings(tree)[0]))
        return shadowing[0]

    def imported(func: ast.AST) -> bool:
        """Whether a bare reader name is imported (``from typing import
        get_type_hints``), not a function of the module's own."""

        if not isinstance(func, ast.Name):
            return True
        found = bindings_of()[1].get(func.id, [])
        return bool(found) and all(isinstance(item.node, ast.alias) for item in found)

    def is_table(node: ast.AST) -> bool:
        spelling = reference_spelling(node)
        return spelling in {f"{name}.modules" for name in sys_names} or spelling in modules_names

    def is_namespace(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call) or node.args:
            return False
        if isinstance(node.func, ast.Name):
            return node.func.id in {"globals", "vars"} and builtin(node.func)
        return reference_spelling(node.func) in {"builtins.globals", "builtins.vars"}

    def record(key: str, line: int, *, producer: ast.AST | None = None) -> None:
        patches.setdefault(key, line)
        if patch_producers is not None:
            patch_producers.setdefault(key, set()).add(producer)

    def read_elsewhere(node: ast.AST, parent: ast.AST | None) -> bool:
        """``set(globals())``, ``for name in sys.modules``, ``x in globals()``,
        ``f(**globals())`` — an unpacking copies."""

        if isinstance(parent, ast.Compare | ast.Starred):
            return True
        if isinstance(parent, ast.keyword):
            if parent.arg is None:
                return True
            # ``typing.get_type_hints(fn, globalns=globals())``, never a
            # function of the module's own under that name.
            call = parents.get(parent)
            return (
                isinstance(call, ast.Call)
                and (reference_spelling(call.func) or "").rsplit(".", 1)[-1] in _NAMESPACE_KEYWORD_READERS
                and imported(call.func)
            )
        if isinstance(parent, ast.For | ast.AsyncFor | ast.comprehension):
            return parent.iter is node
        if (
            isinstance(parent, ast.Call)
            and node in parent.args[1:3]
            and (reference_spelling(parent.func) or "").rsplit(".", 1)[-1] == "get_type_hints"
            and imported(parent.func)
        ):
            # ``get_type_hints(fn, globals())``: its ``globalns``.
            return True
        return (
            isinstance(parent, ast.Call)
            and any(arg is node for arg in parent.args)
            and reference_spelling(parent.func) in _NAMESPACE_READERS
        )

    def own_or_table(key: ast.AST | None, attribute: str, line: int) -> None:
        """``sys.modules[key].attribute = ...``."""

        if isinstance(key, ast.Name) and key.id == "__name__":
            # ``setattr(sys.modules[__name__], name, v)``: a computed name.
            record(SELF_PATCH + attribute if attribute != "?" else MODULE_TABLE_COMPUTED, line)
        else:
            record(_module_table_key(key), line)

    def store_keys(call: ast.Call) -> list[ast.AST | None]:
        if call.func.attr == "update":  # type: ignore[union-attr]
            keys: list[ast.AST | None] = []
            for arg in call.args:
                keys += list(arg.keys) if isinstance(arg, ast.Dict) else [None]
            keys += [ast.Constant(value=item.arg) if item.arg else None for item in call.keywords]
            return keys
        return [call.args[0] if call.args else None]

    def self_key(value: str | None) -> str:
        return SELF_PATCH + value if value is not None else MODULE_TABLE_COMPUTED

    def literal(node: ast.AST | None) -> str | None:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def dict_use(holder: ast.AST, line: int) -> None:
        """The module's own ``__dict__`` (``m.__dict__``, ``vars(m)``,
        ``getattr(m, "__dict__")``): an item read, or a store by key."""

        parent = parents.get(holder)
        if isinstance(parent, ast.Subscript) and parent.value is holder:
            return  # a store is read with the assignment's targets
        if isinstance(parent, ast.Attribute) and parent.value is holder:
            grand = parents.get(parent)
            if parent.attr in {"setdefault", "__setitem__", "update"} and isinstance(grand, ast.Call):
                for key in store_keys(grand):
                    record(self_key(literal(key)), line)
                return
            if parent.attr in {"get", "keys", "values", "items", "copy", "__contains__", "__getitem__"}:
                return
        if isinstance(parent, ast.Compare) or (
            isinstance(parent, ast.For | ast.comprehension) and parent.iter is holder
        ):
            return
        record(MODULE_TABLE_COMPUTED, line)

    def object_use(obj: ast.AST, line: int) -> None:
        """One use of the module's own object. An attribute read or store, a
        plain alias, ``setattr``/``delattr`` by name, a comparison and a known
        reader are read; anything else — a container, a return, a walrus, a
        call — may set anything on it (#879 review)."""

        parent = parents.get(obj)
        if isinstance(parent, ast.Attribute) and parent.value is obj:
            if parent.attr == "__dict__":
                dict_use(parent, line)
            return
        if (
            isinstance(parent, ast.Assign)
            and parent.value is obj
            and all(isinstance(target, ast.Name) for target in parent.targets)
        ) or isinstance(parent, ast.Compare):
            return
        if isinstance(parent, ast.Call) and parent.func is not obj and parent.args[:1] == [obj]:
            spelling = reference_spelling(parent.func) or ""
            if spelling in {"setattr", "delattr"}:
                record(self_key(literal(parent.args[1]) if len(parent.args) > 1 else None), line)
                return
            if spelling == "vars":
                dict_use(parent, line)
                return
            if spelling == "getattr":
                if literal(parent.args[1] if len(parent.args) > 1 else None) == "__dict__":
                    dict_use(parent, line)
                return
        if (
            isinstance(parent, ast.Call)
            and any(arg is obj for arg in parent.args)
            and (reference_spelling(parent.func) or "") in _MODULE_READERS - {"vars", "getattr"}
        ):
            return
        record(MODULE_TABLE_COMPUTED, line)

    # ``_this = sys.modules[__name__]``: names bound to the module itself.
    self_aliases = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and _module_object(node.value, sys_names, modules_names)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for node in ast.walk(tree):
        if id(node) in excluded:
            continue
        line = int(getattr(node, "lineno", 0) or 0)
        parent = parents.get(node)
        # -- sys.modules: reads allowed, anything else a store ---------------
        if is_table(node) and not isinstance(getattr(node, "ctx", None), ast.Load):
            # ``sys.modules |= {...}``, ``sys.modules = ...``.
            record(MODULE_TABLE_COMPUTED, line)
            continue
        if is_table(node) and isinstance(getattr(node, "ctx", None), ast.Load):
            line = int(getattr(parent, "lineno", line) or line)
            if isinstance(parent, ast.Subscript) and parent.value is node:
                grand = parents.get(parent)
                if isinstance(parent.ctx, ast.Store):
                    record(_module_table_key(parent.slice), line)
                elif isinstance(parent.ctx, ast.Load) and isinstance(parent.slice, ast.Name) and parent.slice.id == "__name__":
                    object_use(parent, line)
                elif isinstance(parent.ctx, ast.Load):
                    if isinstance(grand, ast.Attribute) and grand.value is parent and isinstance(grand.ctx, ast.Store | ast.Del):
                        own_or_table(parent.slice, grand.attr, line)
                    elif (
                        isinstance(grand, ast.Call)
                        and any(arg is parent for arg in grand.args)
                        and (reference_spelling(grand.func) or "") not in _MODULE_READERS
                        and not (isinstance(grand.func, ast.Name) and grand.func.id in {"setattr", "delattr"})
                    ):
                        # The module object handed to a function that may set
                        # anything on it (#879 review).
                        record(MODULE_TABLE_COMPUTED, line)
                    elif (
                        isinstance(grand, ast.Call)
                        and isinstance(grand.func, ast.Name)
                        and grand.func.id in {"setattr", "delattr"}
                        and grand.args[:1] == [parent]
                    ):
                        attribute = grand.args[1] if len(grand.args) > 1 else None
                        own_or_table(
                            parent.slice,
                            attribute.value if isinstance(attribute, ast.Constant) and isinstance(attribute.value, str) else "?",
                            line,
                        )
            elif isinstance(parent, ast.Attribute) and parent.value is node:
                grand = parents.get(parent)
                if parent.attr in {"setdefault", "__setitem__", "update"} and isinstance(grand, ast.Call):
                    for key in store_keys(grand):
                        record(_module_table_key(key), line)
                elif parent.attr not in {
                    "get", "keys", "values", "items", "copy", "__contains__", "__getitem__",
                    "pop", "__delitem__", "clear",
                }:
                    record(MODULE_TABLE_COMPUTED, line)
            elif (
                isinstance(parent, ast.Call)
                and parent.args[:1] == [node]
                and (reference_spelling(parent.func) or "").rsplit(".", 1)[-1] in {"setitem", "dict"}
            ):
                # ``operator.setitem(sys.modules, key, m)``, ``monkeypatch.setitem``,
                # ``mock.patch.dict(sys.modules, {...})``: read by their keys.
                if (reference_spelling(parent.func) or "").endswith("setitem"):
                    keys: list[ast.AST | None] = [parent.args[1] if len(parent.args) > 1 else None]
                else:
                    keys = []
                    for arg in parent.args[1:]:
                        keys += list(arg.keys) if isinstance(arg, ast.Dict) else [None]
                    keys += [ast.Constant(value=item.arg) if item.arg else None for item in parent.keywords if item.arg != "clear"]
                for key in keys:
                    record(_module_table_key(key), line)
            elif not read_elsewhere(node, parent):
                # ``mods = sys.modules``: not read.
                record(MODULE_TABLE_COMPUTED, line)
            continue
        # -- the module's own object, however spelled ---------------------------
        if isinstance(node, ast.Call) and _module_object(node, sys_names, modules_names):
            # ``sys.modules.get(__name__)``, ``import_module(__name__)``.
            object_use(node, line)
        elif isinstance(node, ast.Name) and node.id in self_aliases and isinstance(node.ctx, ast.Load):
            object_use(node, line)
            continue
        if isinstance(node, ast.Attribute) and node.attr in {"f_globals", "f_locals"}:
            # A frame's namespace, some module's: ``f_globals.get("__name__")``
            # in a logging helper only reads it; ``f_globals[k] = v`` or any
            # other use may rebind a name (#879 review).
            holder_parent = parents.get(node)
            grand = parents.get(holder_parent)
            reads = (
                (isinstance(holder_parent, ast.Subscript) and holder_parent.value is node
                 and isinstance(holder_parent.ctx, ast.Load))
                or (isinstance(holder_parent, ast.Attribute) and holder_parent.value is node
                    and holder_parent.attr in {"get", "keys", "values", "items", "copy", "__contains__", "__getitem__"}
                    and isinstance(grand, ast.Call))
                or isinstance(holder_parent, ast.Compare)
                or (isinstance(holder_parent, ast.For | ast.comprehension) and holder_parent.iter is node)
            )
            if not reads:
                record(MODULE_TABLE_COMPUTED, line)
            continue
        if (
            isinstance(node, ast.Attribute)
            and reference_spelling(node) in {"builtins.globals", "builtins.vars"}
            and not (isinstance(parent, ast.Call) and parent.func is node)
        ):
            # ``_g = builtins.globals``.
            record(MODULE_TABLE_COMPUTED, line)
            continue
        # -- globals() / vars(): reads allowed ---------------------------------
        if is_namespace(node):
            if isinstance(parent, ast.Subscript) and parent.value is node:
                if isinstance(parent.ctx, ast.Store | ast.Del) and id(parent) not in idiom_cache:
                    key = parent.slice
                    record(
                        SELF_PATCH + key.value
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                        else MODULE_TABLE_COMPUTED,
                        line,
                    )
            elif isinstance(parent, ast.Attribute) and parent.value is node:
                grand = parents.get(parent)
                if parent.attr in {"setdefault", "__setitem__", "update"} and isinstance(grand, ast.Call):
                    for key in store_keys(grand):
                        record(
                            SELF_PATCH + key.value
                            if isinstance(key, ast.Constant) and isinstance(key.value, str)
                            else MODULE_TABLE_COMPUTED,
                            line,
                        )
                elif parent.attr not in {"get", "keys", "values", "items", "copy", "__contains__", "__getitem__"}:
                    record(MODULE_TABLE_COMPUTED, line)
            elif not read_elsewhere(node, parent):
                # ``g = globals()`` and anything else: not read.
                record(MODULE_TABLE_COMPUTED, line)
            continue
        # -- __path__ ----------------------------------------------------------
        if isinstance(node, ast.Name) and node.id == "__path__":
            value = getattr(parent, "value", None)
            if (
                isinstance(parent, ast.Assign)
                and isinstance(value, ast.Call)
                and (
                    value.func.attr if isinstance(value.func, ast.Attribute) else getattr(value.func, "id", None)
                )
                == "extend_path"
            ):
                # ``__path__ = pkgutil.extend_path(__path__, __name__)``: a
                # namespace declaration.
                continue
            if not isinstance(node.ctx, ast.Load) or not (
                isinstance(parent, ast.Compare | ast.Subscript | ast.Starred)
                or isinstance(parent, ast.For | ast.comprehension)
                # ``pkgutil.iter_modules(__path__)`` reads it.
                or (isinstance(parent, ast.Call) and any(arg is node for arg in parent.args))
            ):
                record(PATH_PATCH, line)
            continue
        # ``_g = globals`` then ``_g()[...]``: the namespace, not read.
        if (
            isinstance(node, ast.Name)
            and node.id in {"globals", "vars"}
            and isinstance(node.ctx, ast.Load)
            and not (isinstance(parent, ast.Call) and parent.func is node)
            and builtin(node)
        ):
            record(MODULE_TABLE_COMPUTED, line, producer=node)
            continue
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign | ast.Delete):
            targets = list(node.targets) if isinstance(node, ast.Delete) else [node.target]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "delattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = _dotted(node.args[0])
            if owner is not None:
                record(".".join([*owner, node.args[1].value]), node.lineno)
            continue
        for target in targets:
            if isinstance(target, ast.Attribute):
                dotted = _dotted(target)
                if dotted is not None:
                    if dotted[0] in self_aliases and len(dotted) == 2:
                        record(SELF_PATCH + dotted[1], node.lineno)
                    else:
                        record(".".join(dotted), node.lineno)
                elif _module_object(target.value, sys_names, modules_names):
                    # ``sys.modules.get(__name__).x = ...``,
                    # ``import_module(__name__).x = ...``.
                    record(SELF_PATCH + target.attr, node.lineno)
            elif isinstance(target, ast.Subscript) and not isinstance(node, ast.Delete):
                # ``mod.__dict__[k] = v`` / ``vars(mod)[k] = v``: ``mod.k``.
                holder: ast.AST | None = None
                if isinstance(target.value, ast.Attribute) and target.value.attr == "__dict__":
                    holder = target.value.value
                elif (
                    isinstance(target.value, ast.Call)
                    and isinstance(target.value.func, ast.Name)
                    and target.value.func.id == "vars"
                    and target.value.args
                ):
                    holder = target.value.args[0]
                elif (
                    isinstance(target.value, ast.Call)
                    and reference_spelling(target.value.func) == "getattr"
                    and len(target.value.args) > 1
                    and literal(target.value.args[1]) == "__dict__"
                ):
                    holder = target.value.args[0]
                if holder is None:
                    continue
                key = target.slice
                attribute = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None
                if attribute is None:
                    record(MODULE_TABLE_COMPUTED, node.lineno)
                elif (holder_dotted := _dotted(holder)) is not None and holder_dotted[0] not in self_aliases:
                    record(".".join([*holder_dotted, attribute]), node.lineno)
                else:
                    record(SELF_PATCH + attribute, node.lineno)
    for producer in _import_search_effects(tree, excluded=excluded):
        record(IMPORT_SEARCH_PATCH, producer.lineno, producer=producer)
    return patches


def _binding_nodes(
    roots: list[ast.AST], *, enter_bodies: bool,
) -> Iterator[tuple[ast.AST, bool, ast.stmt | None]]:
    """Walk binding sites once, keeping eager headers and walrus owners apart.

    Ordinary comprehension targets belong to their implicit scope. A walrus
    target instead belongs to the surrounding non-comprehension scope. A
    nested definition's header runs in its surrounding scope, while its body
    introduces a new one. The module census enters bodies to retain global
    declarations; an individual lexical census only enters its own body.
    """
    stack: list[tuple[ast.AST, bool, bool, ast.stmt | None]] = [
        (node, False, False, None) for node in reversed(roots)
    ]
    while stack:
        node, nested, walrus_nested, enclosing = stack.pop()
        statement = node if isinstance(node, ast.stmt) else enclosing
        yield node, nested, statement
        children: list[tuple[ast.AST, bool, bool, ast.stmt | None]] = []
        for field_name, value in ast.iter_fields(node):
            values = value if isinstance(value, list) else [value]
            for child in values:
                if not isinstance(child, ast.AST):
                    continue
                child_nested, child_walrus_nested = nested, walrus_nested
                generic = bool(getattr(node, "type_params", ()))
                if generic and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and isinstance(child, ast.arguments):
                    # Generic annotations have an annotation scope; defaults
                    # still execute where the definition is made (PEP 695).
                    for argument_field, arguments in ast.iter_fields(child):
                        for argument in arguments if isinstance(arguments, list) else [arguments]:
                            if isinstance(argument, ast.AST):
                                annotation_scope = argument_field not in {"defaults", "kw_defaults"}
                                if enter_bodies or not annotation_scope:
                                    children.append((argument, True if annotation_scope else nested,
                                                     True if annotation_scope else walrus_nested, statement))
                    continue
                if isinstance(node, ast.NamedExpr) and field_name == "target":
                    child_nested = walrus_nested
                elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
                    annotation_scope = generic and (
                        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and field_name == "returns"
                        or isinstance(node, ast.ClassDef) and field_name in {"bases", "keywords"}
                    )
                    if field_name in {"body", "type_params"} or annotation_scope:
                        if not enter_bodies:
                            continue
                        child_nested = child_walrus_nested = True
                elif hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias) and field_name in {"value", "type_params"}:
                    if not enter_bodies:
                        continue
                    child_nested = child_walrus_nested = True
                elif isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
                    child_nested = True
                children.append((child, child_nested, child_walrus_nested, statement))
        stack.extend(reversed(children))


def _module_bindings(tree: ast.Module) -> tuple[dict[str, list[_Binding]], bool]:
    """Every module-scope binding of every name, and whether ``*`` is imported.

    Function, class and lambda bodies bind their own scopes. Their eager
    headers and comprehension walruses can bind the surrounding module.
    ``global name`` inside a body is also recorded as an uncertain binding.

    One traversal carries each node's nearest statement and whether it is
    inside a nested scope, so the cost is linear in the tree: walking up a
    parent chain per node cost nodes × depth, which a deeply nested module
    turned into tens of seconds (#879 review).
    """

    body = set(map(id, tree.body))
    bindings: dict[str, list[_Binding]] = {}
    star_import = False

    def record(name: str, node: ast.AST, statement: ast.stmt, *, top: bool) -> None:
        bindings.setdefault(name, []).append(
            _Binding(node=node, statement=statement, top_level=top and id(statement) in body)
        )

    for node, nested, statement in _binding_nodes(list(tree.body), enter_bodies=True):
        if isinstance(node, ast.Global):
            for name in node.names:
                record(name, node, node, top=False)
            continue
        if not nested and statement is not None:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                record(node.name, node, node, top=True)
            elif isinstance(node, ast.alias):
                if node.name == "*":
                    star_import = True
                else:
                    name = node.asname or node.name.split(".", 1)[0]
                    record(name, node, statement, top=True)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                simple = (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and statement.targets[0] is node
                ) or (isinstance(statement, ast.AnnAssign) and statement.target is node)
                record(node.id, node, statement, top=simple)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                record(node.name, node, statement, top=False)
            elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
                record(node.name, node, statement, top=False)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                record(node.rest, node, statement, top=False)
    return bindings, star_import


class ScopeIndex:
    """Which enclosing function scope, if any, binds a name used at a node.

    A reader resolves a tool reference through the *module's* imports. When the
    reference sits inside a function that binds the same name itself — a local
    ``from support import lookup``, a nested ``def lookup``, a parameter — the
    module-scope binding is not the one Python uses there (#879 review).
    """

    def __init__(self, tree: ast.Module) -> None:
        self.annotations_postponed = any(
            isinstance(statement, ast.ImportFrom) and statement.module == "__future__"
            and any(alias.name == "annotations" for alias in statement.names)
            for statement in tree.body
        )
        self.parents: dict[ast.AST, ast.AST] = {
            child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
        }
        self._scopes: dict[int, tuple[dict[str, ast.AST], set[str]]] = {}

    def enclosing_binding(self, node: ast.AST, name: str) -> ast.AST | None:
        """The nearest enclosing non-module binding of ``name`` visible at ``node``.

        Class bodies are consulted only for code directly in them, as Python
        does. ``global name`` hands the name back to the module (None);
        ``nonlocal name`` skips the declaring scope and keeps looking outward.
        """

        found = self.enclosing_bindings(node, name)
        return found[0] if found else None

    def enclosing_bindings(self, node: ast.AST, name: str) -> list[ast.AST]:
        """Every binding of ``name`` in the nearest enclosing scope that binds it.

        More than one means the scope rebinds the name, which a reader must
        not resolve by picking one (#879 review).
        """

        current = self.parents.get(node)
        passed_function = False
        while current is not None and not isinstance(current, ast.Module):
            if isinstance(current, ast.ClassDef) and passed_function:
                # A method skips class-body locals, but generic class type
                # parameters live in an enclosing annotation cell (PEP 695).
                parameters = [parameter for parameter in getattr(current, "type_params", ())
                              if getattr(parameter, "name", None) == name]
                if parameters:
                    return parameters
            if isinstance(current, _SCOPE_NODES) and not (
                isinstance(current, ast.ClassDef) and passed_function
            ):
                bound, declared_global, declared_nonlocal = self._scope(current)
                if name in declared_global:
                    return []
                if name not in declared_nonlocal and name in bound:
                    return bound[name]
                if not isinstance(current, ast.ClassDef):
                    passed_function = True
            current = self.parents.get(current)
        return []

    def _scope(
        self, scope: ast.AST
    ) -> tuple[dict[str, list[ast.AST]], set[str], set[str]]:
        cached = self._scopes.get(id(scope))
        if cached is not None:
            return cached
        bound: dict[str, list[ast.AST]] = {}
        declared_global: set[str] = set()
        declared_nonlocal: set[str] = set()

        def bind(name: str, node: ast.AST) -> None:
            bound.setdefault(name, []).append(node)

        for parameter in getattr(scope, "type_params", ()):
            bind(parameter.name, parameter)
        arguments = getattr(scope, "args", None)
        if isinstance(arguments, ast.arguments):
            for arg in [
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *([arguments.vararg] if arguments.vararg else []),
                *([arguments.kwarg] if arguments.kwarg else []),
            ]:
                bind(arg.arg, arg)
        if isinstance(scope, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
            roots: list[ast.AST] = [gen.target for gen in scope.generators]
        elif isinstance(scope, ast.Lambda):
            roots = [scope.body]
        else:
            roots = list(getattr(scope, "body", []))
        for node, nested, _ in _binding_nodes(roots, enter_bodies=False):
            if nested:
                continue
            if isinstance(node, ast.Global):
                declared_global.update(node.names)
                continue
            if isinstance(node, ast.Nonlocal):
                declared_nonlocal.update(node.names)
                continue
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                bind(node.name, node)
                continue
            if isinstance(node, ast.alias) and node.name != "*":
                bind(node.asname or node.name.split(".", 1)[0], node)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                bind(node.id, node)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bind(node.name, node)
            elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
                bind(node.name, node)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bind(node.rest, node)
        result = (bound, declared_global, declared_nonlocal)
        self._scopes[id(scope)] = result
        return result

    def statement_of(self, node: ast.AST) -> ast.stmt | None:
        current: ast.AST | None = node
        while current is not None and not isinstance(current, ast.stmt):
            current = self.parents.get(current)
        return current


def local_binding_detail(ref: str, name: str, node: ast.AST, *, rebound: bool = False) -> str:
    """The named reason for a reference its enclosing scope binds for itself."""

    if rebound:
        return (
            f"{name!r} is bound more than once in the enclosing scope (first at "
            f"{ref}:{_line(node)}), which is not followed"
        )
    kind = (
        "a local import"
        if isinstance(node, ast.alias)
        else "a nested function"
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        else "a parameter"
        if isinstance(node, ast.arg)
        else "a local assignment"
    )
    return (
        f"{name!r} is bound by {kind} in the enclosing scope at "
        f"{ref}:{_line(node)}, which is not followed"
    )


__all__ = [
    "AMBIGUOUS_MODULE",
    "CONDITIONAL_BINDING",
    "FACTORY_RETURN",
    "IMPORT_CYCLE",
    "ImportResolver",
    "LINKED_MODULE",
    "LOCAL_BINDING",
    "MODULE_NOT_FOUND",
    "NAME_NOT_DEFINED",
    "NOT_A_FUNCTION",
    "NOT_BOUND",
    "OUTSIDE_SCOPE",
    "PythonModule",
    "REBOUND_NAME",
    "RESOLUTION_LIMIT",
    "Resolution",
    "STAR_IMPORT",
    "ScopeIndex",
    "UNREADABLE_MODULE",
    "local_binding_detail",
    "reference_spelling",
    "reflective_access",
]
