"""Source-only clone chains: unique local receivers, no evaluated code."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass

FIELDS = frozenset({"tools", "handoffs", "mcp_servers", "tool_use_behavior", "model_settings"})
MAX_CLONES = 8


def is_clone(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "clone"


@dataclass(frozen=True)
class ClonePlan:
    root: ast.Call
    chain: tuple[ast.Call, ...]
    fields: dict[str, ast.expr]
    settings_unread: bool = False


def clone_plan(
    call: ast.Call,
    lookup: Callable[[ast.Name], ast.Call | None],
    denotes: Callable[[ast.Call], bool],
) -> ClonePlan | None:
    """Follow at most eight uniquely bound SDK clones to a direct Agent call.

    This is syntax correspondence only. The list reader must separately prove
    the constructor, method, retained handle and inherited field unchanged.
    """
    current = call
    chain: list[ast.Call] = []
    seen: set[int] = set()
    while is_clone(current):
        assert isinstance(current.func, ast.Attribute)
        names = [item.arg for item in current.keywords]
        if (id(current) in seen or len(chain) >= MAX_CLONES or current.args
                or None in names or len(set(names)) != len(names)
                or not isinstance(current.func.value, ast.Name)):
            return None
        seen.add(id(current))
        chain.append(current)
        owner = lookup(current.func.value)
        if owner is None:
            return None
        current = owner
    if not denotes(current):
        return None
    fields = {item.arg: item.value for item in current.keywords if item.arg in FIELDS}
    settings_unread = False
    for clone in reversed(chain):
        names = {item.arg for item in clone.keywords}
        if "model" in names and "model_settings" not in names:
            # The SDK can reset implicit model defaults on a model override.
            # Do not assert inherited settings without reading that decision.
            settings_unread |= "model_settings" in fields
            fields.pop("model_settings", None)
        elif "model_settings" in names:
            settings_unread = False
        fields.update({item.arg: item.value for item in clone.keywords if item.arg in FIELDS})
    return ClonePlan(current, tuple(reversed(chain)), fields, settings_unread)


def literal_name_filter(expression: ast.AST | None) -> str | None:
    """Only one literal equality on the unchanged member's SDK tool name."""
    if not isinstance(expression, ast.ListComp) or len(expression.generators) != 1:
        return None
    generator = expression.generators[0]
    if (generator.is_async or not isinstance(generator.target, ast.Name)
            or not isinstance(expression.elt, ast.Name) or expression.elt.id != generator.target.id
            or len(generator.ifs) != 1):
        return None
    test = generator.ifs[0]
    if (not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq)
            or len(test.comparators) != 1 or not isinstance(test.left, ast.Attribute)
            or not isinstance(test.left.value, ast.Name) or test.left.value.id != generator.target.id
            or test.left.attr != "name" or not isinstance(test.comparators[0], ast.Constant)
            or not isinstance(test.comparators[0].value, str)):
        return None
    return test.comparators[0].value
