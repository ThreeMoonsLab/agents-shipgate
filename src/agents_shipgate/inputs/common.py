from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import ToolParameter

HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}
MAX_INPUT_FILE_BYTES = 10 * 1024 * 1024
CONVENTIONAL_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,128}$")


def resolve_input_path(base_dir: Path, value: str) -> Path:
    base = base_dir.resolve()
    raw_path = Path(value)
    path = raw_path if raw_path.is_absolute() else base / raw_path
    resolved = path.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise InputParseError(
            f"Input path {value!r} resolves outside manifest directory: {resolved}"
        ) from exc
    return resolved


def load_structured_file(path: Path) -> Any:
    if not path.exists():
        raise InputParseError(f"Input file not found: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputParseError(f"Unable to inspect input file {path}: {exc}") from exc
    if size > MAX_INPUT_FILE_BYTES:
        raise InputParseError(
            f"Input file too large: {path} is {size} bytes; "
            f"maximum is {MAX_INPUT_FILE_BYTES} bytes"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputParseError(f"Unable to read input file {path}: {exc}") from exc
    try:
        stripped = text.lstrip()
        if path.suffix.lower() == ".json" or stripped.startswith(("{", "[")):
            return json.loads(text)
        return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise InputParseError(f"Unable to parse input file {path}: {exc}") from exc


def load_text_file(path: Path) -> str:
    if not path.exists():
        raise InputParseError(f"Input file not found: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputParseError(f"Unable to inspect input file {path}: {exc}") from exc
    if size > MAX_INPUT_FILE_BYTES:
        raise InputParseError(
            f"Input file too large: {path} is {size} bytes; "
            f"maximum is {MAX_INPUT_FILE_BYTES} bytes"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputParseError(f"Unable to read input file {path}: {exc}") from exc


def stable_tool_id(name: str) -> str:
    return f"tool:{name}"


def tool_name_warning(name: str) -> str | None:
    if CONVENTIONAL_TOOL_NAME_RE.fullmatch(name):
        return None
    return (
        f"Tool name {name!r} is accepted but non-conventional; prefer "
        "letters, numbers, dots, underscores, or hyphens, starting with a letter."
    )


def schema_to_parameters(schema: dict[str, Any] | None) -> list[ToolParameter]:
    if not isinstance(schema, dict):
        return []
    if schema.get("type") != "object" and "properties" not in schema:
        return [
            ToolParameter(
                name="input",
                type=schema.get("type"),
                required=True,
                description=schema.get("description"),
                enum=schema.get("enum"),
                minimum=schema.get("minimum"),
                maximum=schema.get("maximum"),
                format=schema.get("format"),
                default=schema.get("default"),
            )
        ]
    required = set(schema.get("required") or [])
    parameters: list[ToolParameter] = []
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return parameters
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            prop = {}
        parameters.append(
            ToolParameter(
                name=name,
                type=infer_schema_type(prop),
                required=name in required,
                description=prop.get("description"),
                enum=prop.get("enum"),
                minimum=prop.get("minimum"),
                maximum=prop.get("maximum"),
                format=prop.get("format"),
                default=prop.get("default"),
            )
        )
    return parameters


def infer_schema_type(schema: dict[str, Any]) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return "|".join(str(item) for item in schema_type)
    if isinstance(schema_type, str):
        return schema_type
    if "enum" in schema:
        return "enum"
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return None
