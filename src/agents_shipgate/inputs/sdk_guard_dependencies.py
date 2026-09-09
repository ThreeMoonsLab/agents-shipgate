"""Read a deliberately small, static imported Boolean guard profile.

No imports or application code execute. An observed predicate is a fact about
the selected source definition, not proof of deployed Python wiring, the
effect after the guard, or the capability's complete dependency closure.
"""

from __future__ import annotations

import ast
import hashlib
from itertools import islice
from pathlib import Path

from agents_shipgate.core.domain import Tool
from agents_shipgate.core.static_inputs import (
    active_static_input_snapshot,
    read_static_input_bytes,
)
from agents_shipgate.core.tool_identity import source_observation_id
from agents_shipgate.core.trust_roots import IdentityBoundReadSession
from agents_shipgate.inputs.sdk_boolean_source import read_boolean_source_behavior
from agents_shipgate.schemas.guard_dependencies import (
    GuardDependencyEvidence,
    GuardInputEvidence,
)

MAX_GUARD_BYTES = 256 * 1024
MAX_GUARD_AST_NODES = 4096
MAX_BOOLEAN_PARAMETERS = 8


def guard_module_metadata(tree: ast.Module, source_text: str) -> tuple[str, bool]:
    """Compute source evidence once, even when a large module defines many tools."""

    data = source_text.encode("utf-8")
    bounded = (
        len(data) <= MAX_GUARD_BYTES
        and sum(1 for _ in islice(ast.walk(tree), MAX_GUARD_AST_NODES + 1)) <= MAX_GUARD_AST_NODES
    )
    return hashlib.sha256(data).hexdigest(), bounded


class _Unresolved(ValueError):
    pass


def _body(node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _read(path: Path, root: Path, role: str, evidence: GuardDependencyEvidence) -> ast.Module:
    snapshot = active_static_input_snapshot()
    try:
        contained = path.is_relative_to(root) and path.resolve() == path
    except (OSError, RuntimeError):
        # Python 3.11/3.12 raise RuntimeError for a symlink loop; newer
        # pathlib versions can raise OSError instead. Both are failed input
        # resolution, not evidence that the dependency was absent or read.
        contained = False
    if not contained:
        if snapshot is not None and snapshot.contains(path):
            snapshot.mark_unconfirmable_dependency(path)
        raise _Unresolved("dependency_path_not_contained_or_aliased")
    if _absent(path, root, evidence):
        raise _Unresolved("dependency_input_unavailable")
    try:
        data = read_static_input_bytes(path, max_bytes=MAX_GUARD_BYTES)
    except (OSError, ValueError):
        if snapshot is not None and snapshot.contains(path):
            snapshot.mark_unconfirmable_dependency(path)
        raise
    if snapshot is not None and snapshot.contains(path):
        snapshot.mark_dependency_input(path)
    tree = ast.parse(data.decode("utf-8"), filename=str(path))
    if sum(1 for _ in ast.walk(tree)) > MAX_GUARD_AST_NODES:
        raise _Unresolved("dependency_ast_limit")
    evidence.inputs.append(
        GuardInputEvidence(
            path=path.relative_to(root).as_posix(),
            sha256=hashlib.sha256(data).hexdigest(),
            role=role,
        )
    )
    return tree


def _absent(path: Path, root: Path, evidence: GuardDependencyEvidence) -> bool:
    snapshot = active_static_input_snapshot()
    if snapshot is not None and snapshot.contains(path):
        absent = snapshot.bind_dependency_absence(path)
    else:
        session = IdentityBoundReadSession(root, max_entries=10000, max_total_bytes=MAX_GUARD_BYTES)
        absent = path.name not in session.directory_entries(path.parent.relative_to(root))
        session.finish()
    if absent:
        evidence.absent_paths.append(path.relative_to(root).as_posix())
    return absent


def _plain_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = node.args
    return not (
        args.posonlyargs
        or args.kwonlyargs
        or args.vararg
        or args.kwarg
        or args.defaults
        or args.kw_defaults
        or getattr(node, "type_params", ())
    )


def _simple_annotation(node: ast.AST | None) -> bool:
    return (
        node is None
        or isinstance(node, ast.Name)
        or (isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, str)))
    )


def _safe_definition(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return (
        _plain_signature(node)
        and _simple_annotation(node.returns)
        and all(_simple_annotation(arg.annotation) for arg in node.args.args)
    )


def _module_bindings(tree: ast.Module, guard_local: str) -> list[ast.ImportFrom]:
    """Reject rebinding/executed construction rather than infer import order."""

    imports = []
    bindings: dict[str, int] = {}
    for statement in _body(tree):
        names: list[str]
        if isinstance(statement, ast.ImportFrom):
            if statement.module == "agents" and statement.level == 0:
                if any(
                    a.name not in {"Agent", "function_tool"} or a.asname for a in statement.names
                ):
                    raise _Unresolved("unsupported_sdk_import")
            elif statement.level != 1 or not statement.module or "." in statement.module:
                raise _Unresolved("unsupported_import_resolution")
            if any(a.name == "*" for a in statement.names):
                raise _Unresolved("wildcard_import")
            imports.append(statement)
            names = [a.asname or a.name for a in statement.names]
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _safe_definition(statement):
                raise _Unresolved("definition_time_expression")
            if any(
                not isinstance(d, ast.Name) or d.id != "function_tool"
                for d in statement.decorator_list
            ):
                raise _Unresolved("unsupported_tool_decorator")
            names = [statement.name]
        elif (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            value = statement.value
            if (
                not isinstance(value, ast.Call)
                or not isinstance(value.func, ast.Name)
                or value.func.id != "Agent"
            ):
                raise _Unresolved("module_execution_or_configuration")
            if any(
                isinstance(
                    child, (ast.Call, ast.Lambda, ast.NamedExpr, ast.comprehension, ast.Attribute)
                )
                for child in ast.walk(value)
                if child is not value
            ):
                raise _Unresolved("dynamic_agent_configuration")
            names = [statement.targets[0].id]
        else:
            raise _Unresolved("module_execution_or_configuration")
        for name in names:
            bindings[name] = bindings.get(name, 0) + 1
    if any(count != 1 for count in bindings.values()):
        raise _Unresolved("ambiguous_module_binding")
    if bindings.get(guard_local) != 1:
        raise _Unresolved("guard_import_not_unique")
    return imports


def _predicate(node: ast.expr, values: dict[str, bool]) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in values:
        return values[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _predicate(node.operand, values)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        # Visit every operand: short-circuiting must never conceal unsupported
        # configuration or a dynamic call in an unreachable-looking branch.
        operands = [_predicate(item, values) for item in node.values]
        return all(operands) if isinstance(node.op, ast.And) else any(operands)
    raise _Unresolved("predicate_dependency_not_supported")


def _shadows_guard(definition: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """Every Python binding form matters, including ones after the first call.

    Conservatively include nested scopes: this profile does not prove their
    execution/closure relationships and must not guess that a binding is inert.
    """

    for node in ast.walk(definition):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node.id == name
        ):
            return True
        if isinstance(node, ast.arg) and node.arg == name:
            return True
        if (
            node is not definition
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        ):
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            (alias.asname or alias.name.split(".")[0]) == name for alias in node.names
        ):
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name == name:
            return True
        if isinstance(node, ast.MatchMapping) and node.rest == name:
            return True
    return False


def read_guard_dependency(
    *,
    tree: ast.Module,
    source_sha256: str,
    source_within_limits: bool,
    path: Path,
    root: Path,
    tool: Tool,
    definition: ast.FunctionDef | ast.AsyncFunctionDef,
) -> GuardDependencyEvidence:
    root = root.resolve()
    path = path.absolute()
    evidence = GuardDependencyEvidence(
        source_id=tool.source_id or "",
        observation_id=source_observation_id(tool, tool.source_id or ""),
        tool_name=tool.name,
        tool_path=tool.source_ref or "",
        tool_symbol=definition.name,
        tool_line=definition.lineno,
        status="unresolved",
        reason="guard_not_in_supported_position",
    )
    try:
        evidence.inputs.append(
            GuardInputEvidence(
                path=path.relative_to(root).as_posix(),
                sha256=source_sha256,
                role="tool_module",
            )
        )
        snapshot = active_static_input_snapshot()
        if snapshot is not None and snapshot.contains(path):
            snapshot.mark_dependency_input(path)
        if definition not in tree.body:
            raise _Unresolved("nested_tool_definition")
        if not source_within_limits:
            raise _Unresolved("tool_module_limit")
        body = _body(definition)
        if not body or not isinstance(body[0], ast.If) or body[0].orelse:
            raise _Unresolved("guard_not_in_supported_position")
        guard = body[0]
        if (
            not isinstance(guard.test, ast.UnaryOp)
            or not isinstance(guard.test.op, ast.Not)
            or not isinstance(guard.test.operand, ast.Call)
        ):
            raise _Unresolved("guard_not_in_supported_position")
        call = guard.test.operand
        if not isinstance(call.func, ast.Name) or call.keywords:
            raise _Unresolved("dynamic_guard_call")
        if (
            len(guard.body) != 1
            or not isinstance(guard.body[0], ast.Return)
            or not (
                guard.body[0].value is None
                or isinstance(guard.body[0].value, ast.Constant)
                and guard.body[0].value.value in (None, False)
            )
        ):
            raise _Unresolved("denial_not_a_literal_early_return")
        evidence.call_line = call.lineno
        imports = _module_bindings(tree, call.func.id)
        if _shadows_guard(definition, call.func.id):
            raise _Unresolved("guard_shadowed_in_tool")
        matches = [
            (statement, alias)
            for statement in imports
            for alias in statement.names
            if (alias.asname or alias.name) == call.func.id
        ]
        if len(matches) != 1 or matches[0][0].level != 1:
            raise _Unresolved("guard_import_not_unique")
        imported, alias = matches[0]
        evidence.guard_symbol = alias.name
        module = path.parent / f"{imported.module}.py"
        evidence.guard_path = module.relative_to(root).as_posix()
        if not _absent(path.parent / str(imported.module), root, evidence):
            raise _Unresolved("ambiguous_module_package_candidate")
        # Empty package initializers are the only supported static wiring.
        parent = path.parent
        while parent == root or root in parent.parents:
            initializer = parent / "__init__.py"
            if _absent(initializer, root, evidence):
                if parent == root and parent != path.parent:
                    break
                raise _Unresolved("package_initializer_unavailable")
            init_tree = _read(initializer, root, "package_initializer", evidence)
            if _body(init_tree):
                raise _Unresolved("package_initializer_execution")
            if parent == root:
                break
            parent = parent.parent
        guard_tree = _read(module, root, "guard_module", evidence)
        functions = [node for node in _body(guard_tree) if isinstance(node, ast.FunctionDef)]
        if len(functions) != len(_body(guard_tree)) or any(
            node.decorator_list or not _safe_definition(node) for node in functions
        ):
            raise _Unresolved("guard_module_execution_or_configuration")
        targets = [node for node in functions if node.name == alias.name]
        if len(targets) != 1:
            raise _Unresolved("ambiguous_guard_definition")
        target = targets[0]
        evidence.guard_line = target.lineno
        predicate_body = _body(target)
        if (
            len(predicate_body) != 1
            or not isinstance(predicate_body[0], ast.Return)
            or predicate_body[0].value is None
        ):
            raise _Unresolved("predicate_dependency_not_supported")
        parameters = target.args.args
        tool_parameters = {arg.arg: arg for arg in definition.args.args}
        if (
            not _plain_signature(definition)
            or len(call.args) != len(parameters)
            or len(parameters) > MAX_BOOLEAN_PARAMETERS
        ):
            raise _Unresolved("unsupported_guard_arguments")
        if any(
            not isinstance(arg.annotation, ast.Name) or arg.annotation.id != "bool"
            for arg in parameters
        ):
            raise _Unresolved("predicate_parameters_not_boolean")
        names = []
        for arg in call.args:
            if not isinstance(arg, ast.Name) or arg.id not in tool_parameters:
                raise _Unresolved("guard_argument_configuration_or_expression")
            annotation = tool_parameters[arg.id].annotation
            if not isinstance(annotation, ast.Name) or annotation.id != "bool":
                raise _Unresolved("guard_argument_not_boolean")
            names.append(arg.id)
        if len(set(arg.arg for arg in parameters)) != len(parameters):
            raise _Unresolved("ambiguous_predicate_parameter")
        evidence.parameters = sorted(set(names))
        allowed = []
        for mask in range(1 << len(evidence.parameters)):
            values = {name: bool(mask & (1 << i)) for i, name in enumerate(evidence.parameters)}
            arguments = {
                parameter.arg: values[name]
                for parameter, name in zip(parameters, names, strict=True)
            }
            if _predicate(predicate_body[0].value, arguments):
                allowed.append(mask)
        evidence.allowed_inputs = allowed
        evidence.status = "observed"
        evidence.reason = "bounded_source_predicate_only"
        evidence.source_behavior = read_boolean_source_behavior(
            tree=tree, definition=definition, guard_tree=guard_tree, target=target,
            guard_name=call.func.id, guard_module=str(imported.module),
        )
    except _Unresolved as exc:
        evidence.reason = str(exc)
        evidence.status = "ambiguous" if "ambiguous" in evidence.reason else "unresolved"
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError):
        evidence.reason = "dependency_input_unavailable"
    return evidence
