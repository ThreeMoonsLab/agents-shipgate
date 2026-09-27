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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
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
    return RepositoryLayout("" if scope == "." else scope, entries, links, read)

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

    def __post_init__(self) -> None:
        self.scope_root = self.scope_root.resolve()
        self._layout = _REPOSITORY.get() or _disk_layout(self.scope_root)

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
        module = _module(resolved, self.ref(resolved), tree, text)
        self._modules[resolved] = module
        return module

    def module(self, path: Path) -> PythonModule:
        """Parse one in-scope module file, at most once; raise :class:`_Stop`."""

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
            line = module.attribute_patches.get(".".join(parts[:length]))
            if line is not None:
                raise _Stop(
                    REBOUND_NAME,
                    f"{'.'.join(parts[:length])!r} is reassigned by attribute in "
                    f"{module.ref}:{line}",
                )

    def _no_import_patch(
        self, outcome: dict[str, Any], steps: list[dict[str, Any]]
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
        caveats.extend(
            f"{step['path']} changes __path__, so {step['name']!r} may be found in another "
            "directory"
            for step in steps
            if step.get("path_extended")
        )

        def table(key: str, where: str, line: int, runner_ref: str, *, repository_ref: bool = False) -> None:
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
        for runner in runners:
            scan = self._patched_names(runner)
            path_line = self._patch_scan(runner).attribute_patches.get(PATH_PATCH)
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
                        table(key, where, line, self.ref(runner))
            for name in names:
                for where, line, targets in scan.patched.get(name, []):
                    if where == defining.ref and targets is not None and defining.path not in targets:
                        # ``registry.lookup = lookup``: handing the definition on.
                        continue
                    raise _Stop(
                        REBOUND_NAME,
                        f"an attribute named {name!r} is reassigned in {where}:{line}, "
                        f"which {self.ref(runner)} runs before the name is used",
                    )
            caveats.extend(item for item in scan.unread if item not in caveats)
        return tuple(caveats)

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
        typing_only = _type_checking_only(runner)
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
        for module in modules:
            for dotted, line in module.attribute_patches.items():
                if dotted.startswith(MODULE_TABLE_PATCH):
                    patched.setdefault(dotted, []).append((module.ref, line, None))
                    continue
                if dotted.startswith(SELF_PATCH):
                    # The module rebinds its own name: it is the target.
                    patched.setdefault(dotted[len(SELF_PATCH):], []).append(
                        (module.ref, line, frozenset({module.path}))
                    )
                    continue
                if dotted == PATH_PATCH:
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
        return _PatchScan(patched, tuple(unread))

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
            typing_only = _type_checking_only(runner)
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
            for item in modules:
                item_directory = item.ref.rsplit("/", 1)[0] if "/" in item.ref else ""
                for dotted, line in item.attribute_patches.items():
                    if dotted.startswith(MODULE_TABLE_PATCH):
                        found.tables.append((item.ref, line, dotted))
                        continue
                    if dotted.startswith(SELF_PATCH):
                        found.patched.setdefault(dotted[len(SELF_PATCH):], []).append((item.ref, line))
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
                try:
                    containers = self._absolute_candidates(runner, alias.name)
                except _Stop as stop:
                    missing.append((stop, alias.name, []))
                    continue
                paths.extend(item.module_path for item in containers if item.module_path)
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
                    steps.append(_fallthrough(module, name))
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
            if rest:
                raise _Stop(
                    NOT_A_FUNCTION,
                    f"{'.'.join(parts)!r} reads an attribute of function {name!r} "
                    f"in {module.ref}:{line}",
                )
            return {"module": module, "definition": node}
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
            steps.append(_fallthrough(module, name))
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


def _type_checking_only(module: PythonModule) -> set[int]:
    """Nodes under ``if TYPE_CHECKING:``, which never run.

    Only the flag ``typing`` provides counts: a module's own
    ``TYPE_CHECKING = True`` runs its block (#879 review).
    """

    def imported_from_typing(name: str, *, as_module: bool) -> bool:
        bindings = module.bindings.get(name, [])
        return bool(bindings) and all(
            isinstance(item.node, ast.alias)
            and (
                isinstance(item.statement, ast.Import)
                and as_module
                and item.node.name in _TYPING_MODULES
                or isinstance(item.statement, ast.ImportFrom)
                and not as_module
                and not item.statement.level
                and item.statement.module in _TYPING_MODULES
                and item.node.name == "TYPE_CHECKING"
            )
            for item in bindings
        )

    skipped: set[int] = set()
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.If):
            continue
        spelling = reference_spelling(node.test)
        if spelling is None:
            continue
        head, _, rest = spelling.partition(".")
        if (not rest and imported_from_typing(head, as_module=False)) or (
            rest == "TYPE_CHECKING" and imported_from_typing(head, as_module=True)
        ):
            for statement in node.body:
                skipped.update(id(child) for child in ast.walk(statement))
    return skipped


def reflective_access(tree: ast.Module) -> ast.AST | None:
    """A node that reaches the module's own names without spelling them.

    A bare ``globals()``, ``vars()`` or ``locals()`` call, ``sys.modules``
    however ``sys`` or ``modules`` is imported, or importing the module by
    ``__name__``: ``sys.modules[__name__].TOOLS.append(f)`` changes a list no
    use of ``TOOLS`` shows (#879 review).
    """

    sys_names = {"sys"}
    modules_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            sys_names.update(
                alias.asname for alias in node.names if alias.name == "sys" and alias.asname
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "sys" and not node.level:
            modules_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "modules"
            )
    for node in ast.walk(tree):
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


def _fallthrough(module: PythonModule, name: str) -> dict[str, Any]:
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
    if PATH_PATCH in module.attribute_patches:
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
#: The frameworks whose own modules build the tools an agent binds.
_FRAMEWORK_MODULES = ("agents", "google.adk")


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
    #: In-scope modules they import, by repository path: the in-scope reader
    #: reads these as it reads the chain's own modules.
    inscope: list[str] = field(default_factory=list)


def _attribute_patches(tree: ast.Module) -> dict[str, int]:
    """What running a module may reassign, as ``key -> line``.

    ``a.b`` for an attribute store; ``*=...`` / ``*self...`` / ``*?`` for a
    store into ``sys.modules`` by key; ``<self>.name`` for the module rebinding
    its own ``name`` through ``globals()`` or ``sys.modules[__name__]``;
    ``<path>`` for a change to ``__path__``. A use of ``sys.modules`` or
    ``globals()`` that is not a known read counts as a computed store: what it
    changes is not read (#879 review).
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

    def record(key: str, line: int) -> None:
        patches.setdefault(key, line)

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
            record(MODULE_TABLE_COMPUTED, line)
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
    return patches


def _module_bindings(tree: ast.Module) -> tuple[dict[str, list[_Binding]], bool]:
    """Every module-scope binding of every name, and whether ``*`` is imported.

    Function, class, lambda and comprehension bodies bind their own scopes and
    are skipped, except that ``global name`` inside them rebinds the module's
    ``name`` out of view — which is recorded, so it can never be proven.

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

    stack: list[tuple[ast.AST, bool, ast.stmt | None]] = [
        (child, False, None) for child in reversed(tree.body)
    ]
    while stack:
        node, nested, enclosing = stack.pop()
        statement = node if isinstance(node, ast.stmt) else enclosing
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
        inner = nested or isinstance(node, _SCOPE_NODES)
        children = list(ast.iter_child_nodes(node))
        stack.extend((child, inner, statement) for child in reversed(children))
    return bindings, star_import


class ScopeIndex:
    """Which enclosing function scope, if any, binds a name used at a node.

    A reader resolves a tool reference through the *module's* imports. When the
    reference sits inside a function that binds the same name itself — a local
    ``from support import lookup``, a nested ``def lookup``, a parameter — the
    module-scope binding is not the one Python uses there (#879 review).
    """

    def __init__(self, tree: ast.Module) -> None:
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
            roots = []
        else:
            roots = list(getattr(scope, "body", []))
        stack = list(reversed(roots))
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Global):
                declared_global.update(node.names)
                continue
            if isinstance(node, ast.Nonlocal):
                declared_nonlocal.update(node.names)
                continue
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                bind(node.name, node)
                continue
            if isinstance(node, ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
                continue
            if isinstance(node, ast.alias) and node.name != "*":
                bind(node.asname or node.name.split(".", 1)[0], node)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bind(node.id, node)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bind(node.name, node)
            elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
                bind(node.name, node)
            stack.extend(reversed(list(ast.iter_child_nodes(node))))
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
