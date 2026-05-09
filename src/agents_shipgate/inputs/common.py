from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

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


def manifest_relative_path(value: str, base_dir: Path) -> str:
    """Return ``value`` as a forward-slash, manifest-relative POSIX path.

    The v0.11 source-provenance contract says ``Finding.source.path`` is
    manifest-relative for SARIF / report portability. ``ToolSourceConfig.path``
    accepts absolute paths that resolve inside the manifest dir
    (see :func:`resolve_input_path`), so loaders must relativize before
    writing the path into ``Tool.source_path``. Already-relative inputs
    are normalized through ``Path`` to ensure POSIX separators.
    """
    raw_path = Path(value)
    if not raw_path.is_absolute():
        return raw_path.as_posix()
    try:
        return raw_path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        # Outside the manifest dir — resolve_input_path would have already
        # rejected this, but fall back to a normalized POSIX form rather
        # than emitting a Windows-style or unnormalized path.
        return raw_path.as_posix()


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


def json_pointer_escape(part: str) -> str:
    """RFC 6901 escape: ``~`` → ``~0``, ``/`` → ``~1`` (in that order)."""
    return part.replace("~", "~0").replace("/", "~1")


def json_pointer_unescape(part: str) -> str:
    """Inverse of ``json_pointer_escape``."""
    return part.replace("~1", "/").replace("~0", "~")


class PositionIndex:
    """Lookup of (line, column) positions by RFC 6901 JSON pointer.

    All positions are 1-based. ``lookup`` returns the parent's key/item
    location — for the pointer ``/paths/~1refunds/post`` the result is the
    line of the ``post:`` key, not the first child line inside that
    operation. Returning the child node's first content line gives wrong
    results for collapsed mappings.

    ``supported`` is ``False`` for inputs that cannot carry positions
    (e.g. JSON in v0.11). Lookups always return ``None`` in that case.
    """

    __slots__ = ("_positions", "_supported")

    def __init__(
        self,
        positions: dict[str, tuple[int, int]] | None = None,
        *,
        supported: bool = True,
    ) -> None:
        self._positions: dict[str, tuple[int, int]] = positions or {}
        self._supported = supported

    @property
    def supported(self) -> bool:
        return self._supported

    def lookup(self, pointer: str) -> tuple[int, int] | None:
        if not self._supported:
            return None
        return self._positions.get(pointer)


_EMPTY_INDEX = PositionIndex(supported=False)
_YAML_RT = YAML(typ="rt")


def load_structured_file_with_positions(path: Path) -> tuple[Any, PositionIndex]:
    """Load JSON/YAML and (for YAML) build a position index for findings.

    Mirrors :func:`load_structured_file` exactly for the parsed data —
    the same ``yaml.safe_load`` / ``json.loads`` calls and same error
    handling — and additionally returns a :class:`PositionIndex` built
    from a *separate* ``ruamel.yaml`` round-trip parse for YAML inputs.

    Keeping the data path on PyYAML preserves v0.10 semantics:

    - YAML 1.1 booleans (``on``, ``off``, ``yes``, ``no``) stay booleans
      rather than becoming the strings ruamel 1.2 produces.
    - Duplicate keys take the v0.10 "last wins" path rather than raising
      ``DuplicateKeyError``.
    - Octal-like scalars (``012``) parse the same way they did before.

    The position index is best-effort: if ruamel rejects a file that
    PyYAML accepts (rare — the YAML 1.1/1.2 divergence cases above), we
    return the data with an unsupported (empty) index rather than
    failing the scan.

    JSON inputs return ``(data, PositionIndex(supported=False))``;
    callers can still build pointers (e.g. ``/tools/3``) without a line
    number, which is strictly better than today's nothing.
    """
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

    stripped = text.lstrip()
    is_json = path.suffix.lower() == ".json" or stripped.startswith(("{", "["))
    if is_json:
        try:
            return json.loads(text), _EMPTY_INDEX
        except json.JSONDecodeError as exc:
            raise InputParseError(f"Unable to parse input file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InputParseError(f"Unable to parse input file {path}: {exc}") from exc

    # Position index: best-effort, separate parse. PyYAML already accepted
    # the file; if ruamel chokes (e.g. on a duplicate key), drop positions
    # rather than failing the scan.
    try:
        parsed = _YAML_RT.load(text)
    except YAMLError:
        return data, _EMPTY_INDEX

    positions: dict[str, tuple[int, int]] = {}
    # Root pointer "" is a valid RFC 6901 reference to the document root.
    # Singleton-tool YAML files (OpenAI/Anthropic ``tools`` entries that
    # are a single object rather than a ``tools`` array) emit pointer "",
    # so the lookup must return *something* sensible — the document's
    # first content line via the top-level node's ``.lc``.
    if hasattr(parsed, "lc"):
        try:
            positions[""] = (parsed.lc.line + 1, parsed.lc.col + 1)
        except (AttributeError, TypeError):
            pass
    _walk_for_positions(parsed, "", positions)
    return data, PositionIndex(positions)


def _walk_for_positions(
    node: Any,
    pointer: str,
    out: dict[str, tuple[int, int]],
) -> None:
    """Populate ``out`` with parent key/item positions for each pointer."""
    if isinstance(node, CommentedMap):
        for key in node:
            key_text = str(key)
            child_pointer = f"{pointer}/{json_pointer_escape(key_text)}"
            try:
                pos = node.lc.key(key)
            except Exception:  # noqa: BLE001 — ruamel raises a variety of types
                pos = None
            if pos is not None:
                out[child_pointer] = (pos[0] + 1, pos[1] + 1)
            _walk_for_positions(node[key], child_pointer, out)
    elif isinstance(node, CommentedSeq):
        for idx, item in enumerate(node):
            child_pointer = f"{pointer}/{idx}"
            try:
                pos = node.lc.item(idx)
            except Exception:  # noqa: BLE001
                pos = None
            if pos is not None:
                out[child_pointer] = (pos[0] + 1, pos[1] + 1)
            _walk_for_positions(item, child_pointer, out)


def iter_tool_items(
    data: Any,
) -> Iterator[tuple[int, str, dict[str, Any]]]:
    """Yield ``(original_index, pointer, item)`` for each tool-shaped item.

    Pointer convention:

    - ``dict`` with a ``tools`` list → ``/tools/{i}`` per item
    - top-level ``list`` → ``/{i}`` per item
    - singleton ``dict`` → ``''`` (one yield, index 0)

    Non-dict items are skipped but **still consume the original index**,
    so surviving items retain their source index even when the loader
    filters earlier entries. This is intentional — the existing
    ``_tool_items`` helpers in openai_api/anthropic_api filter first and
    then ``enumerate``, which produces ``#i`` source refs that don't
    match the actual file position.
    """
    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict):
                yield (i, f"/{i}", item)
        return
    if isinstance(data, dict):
        tools = data.get("tools")
        if isinstance(tools, list):
            for i, item in enumerate(tools):
                if isinstance(item, dict):
                    yield (i, f"/tools/{i}", item)
            return
        yield (0, "", data)
