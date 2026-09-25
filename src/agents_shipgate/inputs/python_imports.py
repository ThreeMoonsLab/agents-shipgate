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
from dataclasses import dataclass, field
from pathlib import Path
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

#: Distinct modules one resolver will parse, and lookups one reference may
#: take. A repository-local tool is normally one or two hops away; the bounds
#: exist so a pathological re-export web ends in a named reason, not a hang.
MAX_MODULES = 64
MAX_STEPS = 32

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


@dataclass
class ImportResolver:
    """Resolves references inside one scope root. One instance per read."""

    scope_root: Path
    _modules: dict[Path, PythonModule | _Stop] = field(default_factory=dict)
    _listings: dict[Path, frozenset[str] | None] = field(default_factory=dict)
    _parsed: int = 0

    def __post_init__(self) -> None:
        self.scope_root = self.scope_root.resolve()

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

    def resolve(self, module: PythonModule, reference: str) -> Resolution:
        """Resolve ``reference`` (``name`` or ``module.attr...``) in ``module``."""

        parts = reference.split(".")
        steps: list[dict[str, Any]] = []
        try:
            outcome = self._in_module(module, parts, steps, set())
        except _Stop as stop:
            return Resolution(
                reference=reference,
                reason=stop.reason,
                detail=stop.detail,
                steps=tuple(steps),
            )
        return Resolution(reference=reference, steps=tuple(steps), **outcome)

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
            if isinstance(statement, ast.Import):
                dotted = node.name if node.asname else node.name.split(".", 1)[0]
                container = self._absolute(module, dotted)
                return self._member(container, rest, steps, seen, spelling=dotted)
            assert isinstance(statement, ast.ImportFrom)
            container = self._from_base(module, statement)
            return self._member(
                container,
                [node.name, *rest],
                steps,
                seen,
                spelling=_from_spelling(statement),
            )
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
        candidates = files or list(found.values())
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


def reference_spelling(node: ast.AST) -> str | None:
    """``name`` or ``module.attr`` for a plain dotted reference, else None."""

    parts = _dotted(node)
    return ".".join(parts) if parts is not None else None


def _dotted(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return [*prefix, node.attr] if prefix is not None else None
    return None


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
    if "__getattr__" in module.bindings:
        step["module_getattr"] = True
    return step


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
    )


def _module_bindings(tree: ast.Module) -> tuple[dict[str, list[_Binding]], bool]:
    """Every module-scope binding of every name, and whether ``*`` is imported.

    Function, class, lambda and comprehension bodies bind their own scopes and
    are skipped, except that ``global name`` inside them rebinds the module's
    ``name`` out of view — which is recorded, so it can never be proven.
    """

    parents: dict[ast.AST, ast.AST] = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    body = set(map(id, tree.body))
    bindings: dict[str, list[_Binding]] = {}
    star_import = False

    def statement_of(node: ast.AST) -> ast.stmt:
        current = node
        while not isinstance(current, ast.stmt):
            current = parents[current]
        return current

    def module_scoped(node: ast.AST) -> bool:
        current = parents.get(node)
        while current is not None and current is not tree:
            if isinstance(current, _SCOPE_NODES):
                return False
            current = parents.get(current)
        return True

    def record(name: str, node: ast.AST, statement: ast.stmt, *, top: bool) -> None:
        bindings.setdefault(name, []).append(
            _Binding(node=node, statement=statement, top_level=top and id(statement) in body)
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            for name in node.names:
                record(name, node, node, top=False)
            continue
        if not module_scoped(node):
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            record(node.name, node, node, top=True)
        elif isinstance(node, ast.alias):
            statement = statement_of(node)
            if node.name == "*":
                star_import = True
                continue
            name = node.asname or node.name.split(".", 1)[0]
            record(name, node, statement, top=True)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            statement = statement_of(node)
            simple = (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and statement.targets[0] is node
            ) or (isinstance(statement, ast.AnnAssign) and statement.target is node)
            record(node.id, node, statement, top=simple)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            record(node.name, node, statement_of(node), top=False)
        elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
            record(node.name, node, statement_of(node), top=False)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            record(node.rest, node, statement_of(node), top=False)
    return bindings, star_import


__all__ = [
    "AMBIGUOUS_MODULE",
    "CONDITIONAL_BINDING",
    "IMPORT_CYCLE",
    "ImportResolver",
    "LINKED_MODULE",
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
    "UNREADABLE_MODULE",
    "reference_spelling",
]
