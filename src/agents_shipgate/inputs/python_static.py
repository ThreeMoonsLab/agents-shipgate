from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import ToolParameter
from agents_shipgate.inputs.common import load_text_file, schema_to_parameters

SKIPPED_TOOL_PARAMETERS = {
    "self",
    "cls",
    "ctx",
    "context",
    "config",
    "runtime",
    "run_manager",
    "callbacks",
}


def parse_python_file(path: Path, *, label: str) -> ast.Module:
    try:
        return ast.parse(load_text_file(path), filename=str(path))
    except SyntaxError as exc:
        raise InputParseError(f"Unable to parse {label} Python entrypoint {path}: {exc.msg}") from exc


def display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def dotted_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    return None


def last_name(node: ast.AST | None) -> str | None:
    name = dotted_name(node)
    return name.rsplit(".", 1)[-1] if name else None


def literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def literal_bool(node: ast.AST | None) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def keyword(call: ast.Call, name: str) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == name:
            return item.value
    return None


def keyword_string(call: ast.Call, name: str) -> str | None:
    return literal_string(keyword(call, name))


def keyword_name(call: ast.Call, name: str) -> str | None:
    value = keyword(call, name)
    return dotted_name(value)


def first_string_arg(call: ast.Call) -> str | None:
    return literal_string(call.args[0]) if call.args else None


def annotation_to_string(annotation: ast.AST | None) -> str | None:
    if annotation is None:
        return None
    try:
        return ast.unparse(annotation)
    except Exception:  # noqa: BLE001 - best-effort annotation rendering.
        return None


def json_schema_type(annotation: str | None) -> str:
    value = (annotation or "").replace("typing.", "")
    lower = value.lower()
    if value in {"int", "float"}:
        return "number"
    if value == "bool":
        return "boolean"
    if value in {"dict", "Dict"} or lower.startswith(("dict[", "mapping[", "typedict")):
        return "object"
    if value in {"list", "List", "set", "Set", "tuple", "Tuple"} or lower.startswith(
        ("list[", "set[", "tuple[", "sequence[")
    ):
        return "array"
    return "string"


def function_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ToolParameter]:
    parameters: list[ToolParameter] = []
    positional_args = [*node.args.posonlyargs, *node.args.args]
    positional_defaults: list[ast.expr | None] = [
        None for _ in range(len(positional_args) - len(node.args.defaults))
    ]
    positional_defaults.extend(node.args.defaults)
    for arg, default in zip(positional_args, positional_defaults, strict=True):
        if arg.arg in SKIPPED_TOOL_PARAMETERS:
            continue
        parameters.append(_parameter(arg, required=default is None))
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        if arg.arg in SKIPPED_TOOL_PARAMETERS:
            continue
        parameters.append(_parameter(arg, required=default is None))
    return parameters


def function_input_schema(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    schema: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[ToolParameter]]:
    if schema:
        return schema, schema_to_parameters(schema)
    parameters = function_parameters(node)
    properties = {
        parameter.name: {
            "type": json_schema_type(parameter.type),
            **({"description": parameter.description} if parameter.description else {}),
        }
        for parameter in parameters
    }
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": [parameter.name for parameter in parameters if parameter.required],
    }
    return input_schema, parameters


def function_output_schema(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    return_type = annotation_to_string(node.returns)
    return {"type": json_schema_type(return_type)} if return_type else {}


def function_signature(
    name: str,
    parameters: list[ToolParameter],
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    signature = f"{name}({', '.join(parameter.name for parameter in parameters)})"
    return_type = annotation_to_string(node.returns)
    if return_type:
        signature = f"{signature} -> {return_type}"
    return signature


def pydantic_model_schemas(tree: ast.Module) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(last_name(base) == "BaseModel" for base in node.bases):
            continue
        properties: dict[str, Any] = {}
        required: list[str] = []
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                continue
            field_name = statement.target.id
            prop: dict[str, Any] = {"type": json_schema_type(annotation_to_string(statement.annotation))}
            if description := field_description(statement.value):
                prop["description"] = description
            minimum, maximum = field_bounds(statement.value)
            if minimum is not None:
                prop["minimum"] = minimum
            if maximum is not None:
                prop["maximum"] = maximum
            properties[field_name] = prop
            if _field_required(statement.value):
                required.append(field_name)
        schemas[node.name] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    return schemas


def field_description(node: ast.AST | None) -> str | None:
    if not isinstance(node, ast.Call) or last_name(node.func) != "Field":
        return None
    return keyword_string(node, "description")


def field_default_string(node: ast.AST | None) -> str | None:
    if not isinstance(node, ast.Call) or last_name(node.func) != "Field":
        return None
    if value := keyword_string(node, "default"):
        return value
    return literal_string(node.args[0]) if node.args else None


def field_bounds(node: ast.AST | None) -> tuple[float | int | None, float | int | None]:
    if not isinstance(node, ast.Call) or last_name(node.func) != "Field":
        return None, None
    minimum = _number_keyword(node, ("ge", "gt"))
    maximum = _number_keyword(node, ("le", "lt"))
    return minimum, maximum


def _number_keyword(call: ast.Call, names: tuple[str, ...]) -> float | int | None:
    for name in names:
        value = keyword(call, name)
        if isinstance(value, ast.Constant) and isinstance(value.value, int | float):
            return value.value
    return None


def _field_required(node: ast.AST | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Call) and last_name(node.func) == "Field":
        if not node.args:
            return False
        first = node.args[0]
        return isinstance(first, ast.Constant) and first.value is Ellipsis
    return False


def _parameter(arg: ast.arg, *, required: bool) -> ToolParameter:
    return ToolParameter(
        name=arg.arg,
        type=annotation_to_string(arg.annotation),
        required=required,
    )
