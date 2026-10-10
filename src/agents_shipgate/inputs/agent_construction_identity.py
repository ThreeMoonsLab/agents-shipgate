"""Stable source labels for SDK constructions with no literal name (#912)."""

from __future__ import annotations

import ast
from collections import Counter

from agents_shipgate.inputs.python_imports import ScopeIndex, reference_spelling


def construction_identities(
    calls: list[ast.Call], scopes: ScopeIndex, source: str, reserved: set[str],
) -> tuple[dict[int, str], dict[int, str]]:
    """Name a unique source site, never its computed runtime name.

    Line numbers and construction order supply no identity. If the source
    would give two sites one label, retain an explicit ambiguity instead.
    """
    labels = {id(call): source_construction_label(call, scopes, source) for call in calls}
    counts = Counter(labels.values())
    identities, refusals = {}, {}
    for call_id, label in labels.items():
        if counts[label] > 1 or label in reserved:
            refusals[call_id] = (
                f"Source identity {label!r} would join distinct agent constructions; "
                "their correspondence is not established and their tools are not attributed."
            )
        else:
            identities[call_id] = label
    return identities, refusals


def source_construction_label(call: ast.expr | ast.alias, scopes: ScopeIndex, source: str) -> str:
    """Label syntax only; the caller must prove SDK identity separately."""
    parent = scopes.parents.get(call)
    target = None
    if isinstance(parent, ast.Assign | ast.AnnAssign) and parent.value is call:
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        if len(targets) == 1:
            target = reference_spelling(targets[0])
    elif isinstance(call, ast.alias) and isinstance(parent, ast.Import | ast.ImportFrom):
        target = call.asname or call.name.split(".", 1)[0]
    return _source_label(call, target, scopes, source)


def source_binding_label(target: ast.Name | ast.Attribute | ast.alias, scopes: ScopeIndex, source: str) -> str:
    """A qualified stored/imported target, used only to protect uncertain absence."""
    spelling = target.asname or target.name.split(".", 1)[0] if isinstance(target, ast.alias) else reference_spelling(target)
    return _source_label(target, spelling, scopes, source)


def _source_label(node: ast.AST, target: str | None, scopes: ScopeIndex, source: str) -> str:
    ancestors = []
    current = scopes.parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            ancestors.append(current)
        current = scopes.parents.get(current)
    names = []
    # A class-held attribute keeps its source label when its assignment
    # moves between methods. Competing assignments still collide above.
    attribute_owner = (
        isinstance(target, str) and target.startswith(("self.", "cls."))
        and any(isinstance(node, ast.ClassDef) for node in ancestors)
    )
    reached_class = False
    for ancestor in ancestors:
        if isinstance(ancestor, ast.ClassDef):
            reached_class = True
        if not attribute_owner or reached_class:
            names.append(ancestor.name)
    qualified = ".".join(reversed(names))
    label = ".".join(part for part in (qualified, target) if part) or "Agent"
    return f"{label}@{source}"
