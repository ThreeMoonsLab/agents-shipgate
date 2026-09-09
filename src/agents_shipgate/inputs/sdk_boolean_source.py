"""Finite Boolean source relations over fully inspected function and binding ASTs.

This module interprets a private Boolean model, not Python/application code.
The caller has already captured the imported predicate and package bytes. No
opaque call, free/configuration variable or alternate binding is a model leaf.
A true return is a value, never evidence of approval, authority or an effect.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from agents_shipgate.schemas.guard_dependencies import BooleanSourceBehavior, BooleanSourceBinding

Value = Callable[[dict[str, bool]], bool]
Step = Callable[[dict[str, bool]], tuple[bool, bool | None]]


class _Unsupported(ValueError):
    pass


def _body(node):
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _parameters(node: ast.FunctionDef) -> list[str]:
    args = node.args
    if (
        args.posonlyargs
        or args.kwonlyargs
        or args.vararg
        or args.kwarg
        or args.defaults
        or args.kw_defaults
        or getattr(node, "type_params", ())
    ):
        raise _Unsupported("function_signature_not_closed")
    if not (
        node.returns is None or isinstance(node.returns, ast.Name) and node.returns.id == "bool"
    ):
        raise _Unsupported("function_annotation_not_boolean")
    if any(
        not isinstance(arg.annotation, ast.Name) or arg.annotation.id != "bool" for arg in args.args
    ):
        raise _Unsupported("function_parameters_not_boolean")
    names = [arg.arg for arg in args.args]
    if len(names) > 8 or len(names) != len(set(names)) or "bool" in names:
        raise _Unsupported("function_parameter_domain_not_supported")
    return names


def _expression(
    node: ast.expr,
    names: set[str],
    guard_name: str | None,
    guard: tuple[list[str], Value] | None,
    depth: int = 0,
) -> Value:
    if depth > 24:
        raise _Unsupported("source_model_depth_limit")
    if isinstance(node, ast.Constant) and type(node.value) is bool:
        return lambda values: node.value
    if isinstance(node, ast.Name) and node.id in names:
        return lambda values: values[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = _expression(node.operand, names, guard_name, guard, depth + 1)
        return lambda values: not operand(values)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        # Validate every operand before evaluating any input, including those
        # behind a literal short circuit or an unreachable-looking return.
        operands = [_expression(item, names, guard_name, guard, depth + 1) for item in node.values]
        operation = all if isinstance(node.op, ast.And) else any
        return lambda values: operation(operand(values) for operand in operands)
    if (
        guard is not None
        and isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == guard_name
        and not node.keywords
        and len(node.args) == len(guard[0])
    ):
        arguments = [_expression(arg, names, None, None, depth + 1) for arg in node.args]
        return lambda values: guard[1](
            {name: arg(values) for name, arg in zip(guard[0], arguments, strict=True)}
        )
    raise _Unsupported("function_expression_dependency_not_supported")


def _sequence(
    body: list[ast.stmt],
    names: set[str],
    guard_name: str,
    guard: tuple[list[str], Value],
    depth: int = 0,
) -> Step:
    if depth > 24:
        raise _Unsupported("source_model_depth_limit")
    steps: list[Step] = []
    for statement in body:
        if isinstance(statement, ast.Return):
            if (
                statement.value is None
                or isinstance(statement.value, ast.Constant)
                and statement.value.value is None
            ):
                steps.append(lambda values: (True, None))
            else:
                value = _expression(statement.value, names, guard_name, guard)
                steps.append(lambda values, value=value: (True, value(values)))
        elif isinstance(statement, ast.If):
            condition = _expression(statement.test, names, guard_name, guard)
            yes = _sequence(statement.body, names, guard_name, guard, depth + 1)
            no = _sequence(statement.orelse, names, guard_name, guard, depth + 1)
            steps.append(
                lambda values, condition=condition, yes=yes, no=no: (
                    yes if condition(values) else no
                )(values)
            )
        else:
            raise _Unsupported("function_statement_dependency_not_supported")

    def run(values: dict[str, bool]) -> tuple[bool, bool | None]:
        for step in steps:
            returned, value = step(values)
            if returned:
                return True, value
        return False, None

    return run


def _binding(
    tree: ast.Module,
    definition: ast.FunctionDef,
    guard_name: str,
    guard_symbol: str,
    guard_module: str,
) -> BooleanSourceBinding:
    body = _body(tree)
    if len(body) < 3 or body[-2] is not definition:
        raise _Unsupported("tool_module_not_closed")
    imported: list[str] = []
    guard_imports = 0
    for statement in body[:-2]:
        if not isinstance(statement, ast.ImportFrom):
            raise _Unsupported("tool_module_not_closed")
        if statement.module == "agents" and statement.level == 0:
            if any(
                alias.asname or alias.name not in {"Agent", "function_tool"}
                for alias in statement.names
            ):
                raise _Unsupported("sdk_binding_import_not_exact")
            imported.extend(alias.name for alias in statement.names)
        elif (
            statement.module == guard_module
            and statement.level == 1
            and len(statement.names) == 1
            and statement.names[0].name == guard_symbol
            and (statement.names[0].asname or guard_symbol) == guard_name
        ):
            guard_imports += 1
        else:
            raise _Unsupported("tool_module_import_dependency_not_supported")
    if sorted(imported) != ["Agent", "function_tool"] or guard_imports != 1:
        raise _Unsupported("sdk_binding_import_not_exact")
    reserved = {"Agent", "function_tool", "bool", guard_name, definition.name}
    if len(reserved) != 5 or any(arg.arg in reserved for arg in definition.args.args):
        raise _Unsupported("source_binding_identity_not_unique")
    if (
        len(definition.decorator_list) != 1
        or not isinstance(definition.decorator_list[0], ast.Name)
        or definition.decorator_list[0].id != "function_tool"
    ):
        raise _Unsupported("tool_decorator_not_exact")
    assignment = body[-1]
    if (
        not isinstance(assignment, ast.Assign)
        or len(assignment.targets) != 1
        or not isinstance(assignment.targets[0], ast.Name)
        or assignment.targets[0].id in reserved
    ):
        raise _Unsupported("agent_binding_not_unique_literal")
    call = assignment.value
    if (
        not isinstance(call, ast.Call)
        or not isinstance(call.func, ast.Name)
        or call.func.id != "Agent"
        or call.args
        or len(call.keywords) != 2
        or sorted(keyword.arg or "" for keyword in call.keywords) != ["name", "tools"]
    ):
        raise _Unsupported("agent_configuration_not_closed")
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    name, tools = keywords["name"], keywords["tools"]
    if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
        raise _Unsupported("agent_configuration_not_closed")
    if (
        not isinstance(tools, ast.List)
        or len(tools.elts) > 1
        or any(not isinstance(tool, ast.Name) or tool.id != definition.name for tool in tools.elts)
    ):
        raise _Unsupported("agent_tool_binding_not_literal")
    return BooleanSourceBinding(
        agent_symbol=assignment.targets[0].id, agent_name=name.value, tool_bound=bool(tools.elts)
    )


def read_boolean_source_behavior(
    *,
    tree: ast.Module,
    definition: ast.FunctionDef | ast.AsyncFunctionDef,
    guard_tree: ast.Module,
    target: ast.FunctionDef,
    guard_name: str,
    guard_module: str,
) -> BooleanSourceBehavior:
    """Model every statement and input, or publish one explicit refusal reason."""
    result = BooleanSourceBehavior(status="unresolved", reason="source_model_unavailable")
    try:
        if not isinstance(definition, ast.FunctionDef) or _body(guard_tree) != [target]:
            raise _Unsupported("source_function_or_helper_module_not_closed")
        parameters = _parameters(definition)
        guard_parameters = _parameters(target)
        if target.decorator_list:
            raise _Unsupported("helper_decorator_not_supported")
        helper = _body(target)
        if len(helper) != 1 or not isinstance(helper[0], ast.Return) or helper[0].value is None:
            raise _Unsupported("helper_function_not_closed")
        predicate = _expression(helper[0].value, set(guard_parameters), None, None)
        binding = _binding(tree, definition, guard_name, target.name, guard_module)
        function = _sequence(
            _body(definition), set(parameters), guard_name, (guard_parameters, predicate)
        )
        parameters = sorted(parameters)
        returns = []
        for mask in range(1 << len(parameters)):
            values = {name: bool(mask & (1 << i)) for i, name in enumerate(parameters)}
            _, value = function(values)
            returns.append("none" if value is None else "true" if value else "false")
        result = BooleanSourceBehavior(
            status="observed",
            reason="closed_boolean_source_function_and_literal_agent_binding",
            parameters=parameters,
            returns=returns,
            binding=binding,
            configuration_reads="none",
        )
    except _Unsupported as exc:
        result.reason = str(exc)
    except RecursionError:
        result.reason = "source_model_depth_limit"
    return result
