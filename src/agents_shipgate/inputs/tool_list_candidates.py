"""Source initializer hints for an unread SDK tools list.

These members are candidates only. This walk does not prove ownership,
immutability, constructor identity or the resulting agent's tool surface.
The SDK reader must attach the original refusal to every member it uses.
"""

from __future__ import annotations

import ast

from agents_shipgate.inputs.list_expressions import (
    MAX_DEPTH,
    MAX_MEMBERS,
    MAX_VISITS,
    ListMember,
    bindings_at,
)
from agents_shipgate.inputs.python_imports import (
    ImportResolver,
    PythonModule,
    ScopeIndex,
    reference_spelling,
)


def tool_list_candidates(
    expression: ast.expr | None, module: PythonModule | None, resolver: ImportResolver,
) -> tuple[ListMember, ...]:
    """Read bounded literal initializers without granting a binding fact.

    Calls, caller parameters, ambiguous assignments and computed attributes
    remain unread. Imports use the resolver's captured source; a refused
    import with no initializer supplies no candidate.
    """
    visits = 0
    scopes: dict[int, ScopeIndex] = {}

    def walk(node: ast.expr, home: PythonModule, depth: int, seen: frozenset) -> list[ListMember]:
        nonlocal visits
        visits += 1
        if depth > MAX_DEPTH or visits > MAX_VISITS:
            return []
        if isinstance(node, ast.List | ast.Tuple):
            members = []
            for item in node.elts:
                if isinstance(item, ast.Starred):
                    members.extend(walk(item.value, home, depth + 1, seen))
                elif isinstance(item, ast.Name | ast.Attribute):
                    members.append(ListMember(item, home))
                if len(members) > MAX_MEMBERS:
                    return []
            return members
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return walk(node.left, home, depth + 1, seen) + walk(node.right, home, depth + 1, seen)
        if isinstance(node, ast.IfExp):
            if isinstance(node.test, ast.Constant):
                return walk(node.body if node.test.value else node.orelse, home, depth + 1, seen)
            condition = ast.unparse(node.test)
            return [
                ListMember(member.expr, member.module, (label, *member.conditions))
                for branch, label in ((node.body, f"`{condition}`"), (node.orelse, f"not `{condition}`"))
                for member in walk(branch, home, depth + 1, seen)
            ]
        spelling = reference_spelling(node)
        if spelling is None or not isinstance(node, ast.Name | ast.Attribute):
            return []
        index = scopes.get(id(home.tree))
        if index is None:
            index = scopes[id(home.tree)] = ScopeIndex(home.tree)
        found = bindings_at(index, home.bindings)(spelling.partition(".")[0], node)
        if len(found) != 1:
            return []
        binding, statement = found[0]
        key = (id(home.tree), id(binding))
        if key in seen:
            return []
        if isinstance(binding, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
            result = resolver.resolve_local_import(home, statement, binding, spelling)
            if result.value is None or result.module is None:
                return []
            return walk(result.value, result.module, depth + 1, seen | {key})
        if (
            isinstance(node, ast.Name) and isinstance(statement, ast.Assign | ast.AnnAssign)
            and statement.value is not None
            and (statement.targets if isinstance(statement, ast.Assign) else [statement.target]) == [binding]
        ):
            return walk(statement.value, home, depth + 1, seen | {key})
        return []

    if expression is None or module is None:
        return ()
    members = walk(expression, module, 0, frozenset())
    return tuple(members) if len(members) <= MAX_MEMBERS else ()
