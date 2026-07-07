"""Conservative static trace: is an expression bound from a config read?

Supports the config-bound capability detection design
(docs/engineering/config-bound-capability-detection.md). A dynamic toolkit
factory's authority-bearing argument is classified as one of:

- ``config``  — statically traceable to a *config read*: a ``json`` /
  ``yaml`` / ``toml`` / ``tomllib`` load call, an ``os.environ`` /
  ``os.getenv`` read, or the instantiation of an in-file pydantic
  ``BaseSettings`` subclass.
- ``unknown`` — present but not statically readable (a call we do not
  recognize, a name we cannot resolve, an ambiguous reassignment).

The trace is deliberately conservative: same-file, same-scope assignment
tracing only — the enclosing function's body first, then module-level
statements — with no interprocedural or cross-module dataflow. A name
assigned more than once in the searched scope is ambiguous and resolves to
``unknown``. The design doc is explicit that guessing here ships a
false-positive generator: ``unknown`` must never fire the removal check.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from agents_shipgate.inputs.python_static import dotted_name

# Dotted call names that read configuration. Deliberately small: each entry
# is a well-known "parse config from file/env" entry point, not a generic
# I/O call. ``os.environ.get`` is handled as a dotted attribute call;
# ``os.environ[...]`` subscripts are handled structurally below.
_CONFIG_READ_CALLS: frozenset[str] = frozenset(
    {
        "json.load",
        "json.loads",
        "yaml.safe_load",
        "yaml.load",
        "yaml.full_load",
        "toml.load",
        "toml.loads",
        "tomllib.load",
        "tomllib.loads",
        "os.getenv",
        "os.environ.get",
        "configparser.ConfigParser",
    }
)

# Modules whose ``from <module> import <name>`` symbols count as config
# readers when called bare (e.g. ``from json import load`` → ``load(...)``).
_CONFIG_READ_FROM_IMPORTS: dict[str, frozenset[str]] = {
    "json": frozenset({"load", "loads"}),
    "yaml": frozenset({"safe_load", "load", "full_load"}),
    "toml": frozenset({"load", "loads"}),
    "tomllib": frozenset({"load", "loads"}),
    "os": frozenset({"getenv"}),
}

# Readers whose first argument is a variable/section name, never a file path.
_NON_PATH_READERS: frozenset[str] = frozenset(
    {"os.getenv", "os.environ.get", "configparser.ConfigParser"}
)

_SETTINGS_BASE_SUFFIXES = ("BaseSettings",)

_MAX_TRACE_DEPTH = 3


@dataclass(frozen=True)
class ConfigBindingTrace:
    """Outcome of tracing one authority-bearing expression."""

    binding: str  # "config" | "unknown"
    config_path: str | None = None
    reader: str | None = None


def trace_config_binding(
    expr: ast.expr,
    *,
    tree: ast.Module,
    line: int,
) -> ConfigBindingTrace:
    """Classify ``expr`` (at ``line`` in ``tree``) as config-bound or unknown.

    ``line`` locates the enclosing function so the trace searches the same
    scope the expression executes in; module-level expressions search the
    module body. Never raises; anything unresolvable is ``unknown``.
    """
    scopes = _scopes_for_line(tree, line)
    readers = _reader_names(tree)
    settings_classes = _settings_class_names(tree)
    return _trace(expr, scopes, readers, settings_classes, depth=0)


def _trace(
    expr: ast.expr,
    scopes: list[dict[str, ast.expr | None]],
    readers: dict[str, str],
    settings_classes: set[str],
    *,
    depth: int,
) -> ConfigBindingTrace:
    if depth > _MAX_TRACE_DEPTH:
        return ConfigBindingTrace(binding="unknown")
    node: ast.expr | None = expr
    # Unwrap value-preserving wrappers: ``await x``, ``x["key"]``,
    # ``x.attr`` all carry the authority of their root object.
    while True:
        if isinstance(node, ast.Await):
            node = node.value
            continue
        if isinstance(node, ast.Subscript):
            # ``os.environ["NAME"]`` is itself a config read.
            if dotted_name(node.value) == "os.environ":
                return ConfigBindingTrace(binding="config", reader="os.environ")
            node = node.value
            continue
        if isinstance(node, ast.Attribute):
            # Attribute access carries its root's authority:
            # ``settings.actions`` → ``settings``; ``Settings().actions``
            # → the ``Settings()`` call.
            node = node.value
            continue
        break
    if isinstance(node, ast.Call):
        classified = _classify_call(node, readers, settings_classes)
        if classified is not None:
            return classified
        # ``cfg.get("actions")``-style method call on a traced name: the
        # authority is the receiver's, so trace it.
        if isinstance(node.func, ast.Attribute):
            return _trace(
                node.func.value, scopes, readers, settings_classes, depth=depth + 1
            )
        return ConfigBindingTrace(binding="unknown")
    if isinstance(node, ast.Name):
        resolved = _resolve_name(node.id, scopes)
        if resolved is None:
            return ConfigBindingTrace(binding="unknown")
        return _trace(resolved, scopes, readers, settings_classes, depth=depth + 1)
    return ConfigBindingTrace(binding="unknown")


def _classify_call(
    call: ast.Call,
    readers: dict[str, str],
    settings_classes: set[str],
) -> ConfigBindingTrace | None:
    name = dotted_name(call.func)
    if name is None:
        # ``Path("x").read_text()``-style chains have a Call in the attribute
        # root; ``dotted_name`` cannot render them. Not a recognized reader.
        return None
    canonical = readers.get(name, name)
    if canonical in _CONFIG_READ_CALLS:
        return ConfigBindingTrace(
            binding="config",
            # Env readers take a variable name, not a file path.
            config_path=(
                None
                if canonical in _NON_PATH_READERS
                else _config_path_from_call(call)
            ),
            reader=canonical,
        )
    if name in settings_classes:
        return ConfigBindingTrace(binding="config", reader="pydantic_settings")
    return None


def _config_path_from_call(call: ast.Call) -> str | None:
    """Best-effort literal path of the file feeding a config-read call.

    Recognizes ``load("cfg.toml")``, ``load(open("cfg.json"))``, and
    ``loads(Path("cfg.yaml").read_text())``. Env/settings reads and
    non-literal paths return ``None`` (the detection then degrades to a
    path-less binding — never a guess).
    """
    if not call.args:
        return None
    return _literal_path(call.args[0])


def _literal_path(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open" and node.args:
            return _literal_path(node.args[0])
        if isinstance(func, ast.Attribute) and func.attr in {"read_text", "open"}:
            inner = func.value
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "Path"
                and inner.args
            ):
                return _literal_path(inner.args[0])
    return None


def _reader_names(tree: ast.Module) -> dict[str, str]:
    """Map local call names to canonical config-reader dotted names.

    Resolves ``from json import load [as jload]`` and
    ``import yaml as y`` (→ ``y.safe_load`` canonicalizes to
    ``yaml.safe_load``) so aliased imports still count. Only the known
    config modules contribute; an unrelated local ``load`` is not matched.
    """
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".", 1)[0]
            symbols = _CONFIG_READ_FROM_IMPORTS.get(module)
            if symbols is None:
                continue
            for alias in node.names:
                if alias.name in symbols:
                    names[alias.asname or alias.name] = f"{module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top in _CONFIG_READ_FROM_IMPORTS and alias.asname:
                    # ``import yaml as y`` → canonicalize ``y.safe_load``.
                    for symbol in _CONFIG_READ_FROM_IMPORTS[top]:
                        names[f"{alias.asname}.{symbol}"] = f"{top}.{symbol}"
    return names


def _settings_class_names(tree: ast.Module) -> set[str]:
    """In-file class names inheriting a pydantic ``BaseSettings`` base."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_name = dotted_name(base) or ""
            if base_name.endswith(_SETTINGS_BASE_SUFFIXES):
                names.add(node.name)
                break
    return names


def _scopes_for_line(
    tree: ast.Module, line: int
) -> list[dict[str, ast.expr | None]]:
    """Assignment maps for the scopes visible at ``line``.

    Innermost enclosing function first, then the module body. Each map is
    ``name → assigned expr`` with ``None`` marking an ambiguous name
    (assigned more than once in that scope). Nested function/class bodies
    are excluded from their parent's scope.
    """
    scopes: list[dict[str, ast.expr | None]] = []
    function = _enclosing_function(tree, line)
    if function is not None:
        scopes.append(_scope_assignments(function.body))
    scopes.append(_scope_assignments(tree.body))
    return scopes


def _enclosing_function(
    tree: ast.Module, line: int
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        end = getattr(node, "end_lineno", None)
        if end is None or not (node.lineno <= line <= end):
            continue
        if best is None or node.lineno > best.lineno:
            best = node
    return best


def _scope_assignments(body: list[ast.stmt]) -> dict[str, ast.expr | None]:
    assignments: dict[str, ast.expr | None] = {}

    def record(name: str, value: ast.expr) -> None:
        assignments[name] = None if name in assignments else value

    def visit(statements: list[ast.stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue  # nested scope — not this scope's assignments
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        record(target.id, stmt.value)
            elif isinstance(stmt, ast.AnnAssign):
                if isinstance(stmt.target, ast.Name) and stmt.value is not None:
                    record(stmt.target.id, stmt.value)
            elif isinstance(stmt, ast.With | ast.AsyncWith):
                for item in stmt.items:
                    if isinstance(item.optional_vars, ast.Name):
                        record(item.optional_vars.id, item.context_expr)
            for child_body in _child_bodies(stmt):
                visit(child_body)

    visit(body)
    return assignments


def _child_bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    bodies: list[list[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody"):
        value = getattr(stmt, field, None)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            bodies.append(value)
    for handler in getattr(stmt, "handlers", []) or []:
        if handler.body:
            bodies.append(handler.body)
    return bodies


def _resolve_name(
    name: str, scopes: list[dict[str, ast.expr | None]]
) -> ast.expr | None:
    for scope in scopes:
        if name in scope:
            return scope[name]  # None (ambiguous) intentionally stops the trace
    return None


__all__ = ["ConfigBindingTrace", "trace_config_binding"]
