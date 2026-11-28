from __future__ import annotations

from collections.abc import Mapping
from difflib import get_close_matches
from pathlib import Path
from typing import Any, get_args

import yaml
from pydantic import BaseModel, ValidationError

from agents_shipgate.core.agent_control_envelope import single_line_text
from agents_shipgate.core.errors import ConfigError, InputParseError, format_validation_error
from agents_shipgate.core.privacy import redact_text
from agents_shipgate.core.yaml_limits import BoundedSafeLoader
from agents_shipgate.inputs.common import (
    PositionIndex,
    load_structured_file_with_positions,
    load_structured_text_with_positions,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


def config_not_found_error(path: Path) -> ConfigError:
    """The one message for a manifest that is not on disk.

    Absent, empty, and present-but-not-a-mapping are three different
    workspace states with three different repairs, so they get three
    different messages. Collapsing them told a first-time user that a file
    they had never created was malformed (#384).
    """

    hint = ""
    if path.name == "shipgate.yaml":
        hint = " Run `agents-shipgate init --workspace . --write` to create one."
    return ConfigError(f"Config file not found: {path} in {Path.cwd()}.{hint}")


def manifest_read_error(path: Path, exc: OSError) -> ConfigError:
    """Classify a failed manifest read where the errno is still in hand.

    Readers that snapshot the manifest bytes collapse a failed read into
    ``b""`` so that a later failure is reported against the same bytes the
    identity hashed. That part is deliberate. But ``b""`` then parses as an
    empty YAML document and reaches the shape check below, which reports an
    absent file as a malformed one — and the control envelope that carries
    that reason routes to ``verify``, so ``reason`` and ``next_action``
    disagree about whether the file exists (#384). Classifying at the read
    keeps the ``b""`` binding and gives the two fields one story.
    """

    if isinstance(exc, FileNotFoundError):
        return config_not_found_error(path)
    if isinstance(exc, NotADirectoryError):
        # ENOTDIR: the file is absent, but "not found" would send the reader
        # to create it — and creating it is exactly what cannot work while a
        # regular file sits in the middle of the path.
        return ConfigError(
            f"Config file path runs through a file, not a directory: {path}"
        )
    if isinstance(exc, IsADirectoryError):
        return ConfigError(f"Config path is a directory, not a manifest file: {path}")
    detail = exc.strerror or str(exc)
    return ConfigError(f"Config file could not be read: {path}: {detail}")


def _empty_config_error(path: Path) -> ConfigError:
    return ConfigError(
        f"Config file is empty: {path}. A manifest needs at least "
        "version, project, agent, and environment."
    )


def _parsed_manifest_mapping(
    data: Any, text: str, config_path: Path
) -> dict[str, Any]:
    """Reject a parsed manifest that is not a mapping, saying which way."""

    if isinstance(data, dict):
        return data
    if data is None and not text.strip():
        raise _empty_config_error(config_path)
    raise ConfigError(f"Config file must contain a YAML object: {config_path}")


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise config_not_found_error(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise manifest_read_error(path, exc) from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Config file must be UTF-8: {path}") from exc
    try:
        data = yaml.load(text, Loader=BoundedSafeLoader)
    except (ValueError, yaml.YAMLError, RecursionError) as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    return _parsed_manifest_mapping(data, text, path)


def load_manifest(path: str | Path) -> AgentsShipgateManifest:
    config_path = Path(path)
    data = load_yaml_file(config_path)
    return _validate_manifest_data(data, config_path)


def load_manifest_text(
    text: str,
    *,
    source: str | Path = "shipgate.yaml",
) -> AgentsShipgateManifest:
    """Parse manifest bytes that were already read through a trusted snapshot."""

    config_path = Path(source)
    try:
        data = yaml.load(text, Loader=BoundedSafeLoader)
    except (ValueError, yaml.YAMLError, RecursionError) as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    return _validate_manifest_data(
        _parsed_manifest_mapping(data, text, config_path), config_path
    )


def load_manifest_with_positions(
    path: str | Path,
) -> tuple[AgentsShipgateManifest, PositionIndex]:
    """Load the manifest AND a JSON-pointer → ``(line, col)`` position
    index built from the same YAML source.

    The manifest is loaded through :func:`load_manifest` so monkeypatch
    hooks (test fixtures that patch the loader to inject overrides)
    continue to work. The position index is built via a separate
    :func:`load_structured_file_with_positions` call, with
    ``InputParseError`` mapped to ``ConfigError`` so doctor / scan
    exit codes remain identical to the existing :func:`load_manifest`
    contract.

    The position index may be ``PositionIndex(supported=False)`` when
    the file is JSON or when ruamel rejects content that PyYAML
    accepted; callers should treat lookups as best-effort and fall
    back to the legacy filename-only provenance when no line is
    available.
    """
    manifest = load_manifest(path)
    config_path = Path(path)
    try:
        _, positions = load_structured_file_with_positions(config_path)
    except InputParseError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    return manifest, positions


def load_manifest_text_with_positions(
    text: str,
    *,
    source: str | Path = "shipgate.yaml",
) -> tuple[AgentsShipgateManifest, PositionIndex]:
    """Load a manifest and positions from one already-captured byte snapshot."""

    manifest = load_manifest_text(text, source=source)
    config_path = Path(source)
    try:
        _, positions = load_structured_text_with_positions(
            text,
            source=config_path,
        )
    except InputParseError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    return manifest, positions


def _validate_manifest_data(
    data: dict[str, Any], config_path: Path
) -> AgentsShipgateManifest:
    version = data.get("version")
    if version != "0.1":
        raise ConfigError(
            f"Unsupported manifest version {version!r}; this Agents Shipgate build supports version '0.1'."
        )
    if "check_severity_overrides" in data:
        raise ConfigError(
            "check_severity_overrides was removed in Agents Shipgate v0.4; "
            "move these entries under checks.severity_overrides."
        )
    try:
        return AgentsShipgateManifest.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, data)) from exc


def _format_validation_error(exc: ValidationError, data: Mapping[str, Any]) -> str:
    message = format_validation_error(
        exc, label="Invalid shipgate.yaml", suggest=_field_suggestion
    )
    return "\n".join([message, *_unquoted_colon_hints(exc, data)])


#: Pydantic error types that mean a field wanted one scalar value.
_SCALAR_ERROR_TYPES = frozenset(
    {"string_type", "int_type", "float_type", "bool_type", "literal_error", "enum"}
)


def _unquoted_colon_hints(exc: ValidationError, data: Mapping[str, Any]) -> list[str]:
    """Name the YAML cause when a scalar field received a one-key mapping.

    An unquoted ``: `` in plain text makes YAML read the words before it as a
    key, so the list item ``- Handle refunds: read orders and issue credits``
    is the mapping ``{"Handle refunds": "read orders and issue credits"}``. The
    schema then reports ``agent.declared_purpose.0: Input should be a valid
    string`` — the violation, not its cause — on the field people most often
    write by hand in prose (#328). The parsed document says which case it is,
    so the hint is added only there; it names the field, never the value.
    """

    hints: list[str] = []
    seen: set[str] = set()
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        if error["type"] not in _SCALAR_ERROR_TYPES:
            continue
        walked = _walk_location(data, error["loc"])
        if walked is None:
            continue
        location, value = walked
        if not (isinstance(value, dict) and len(value) == 1):
            continue
        # A nested value is a structure someone wrote, not a sentence split.
        ((_key, inner),) = value.items()
        if isinstance(inner, (dict, list)):
            continue
        if location in seen:
            continue
        seen.add(location)
        safe_location = single_line_text(redact_text(location) or "")
        hints.append(
            f"Hint: {safe_location} is a key-value mapping, not a single value. "
            "An unquoted ': ' inside text makes YAML read the words before it "
            "as a key; if the value is text that contains ': ', put it in quotes."
        )
    return hints


def _walk_location(
    data: Mapping[str, Any], loc: tuple[int | str, ...]
) -> tuple[str, Any] | None:
    """The value a validation error's location names in the parsed document.

    ``None`` when a part does not resolve — a union member's tag, or a key a
    validator renamed — so a location the document cannot account for gets
    no hint rather than one about some other value.
    """

    node: Any = data
    for part in loc:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node):
            node = node[part]
        else:
            return None
    if not loc:
        return None
    return ".".join(str(part) for part in loc), node



def _field_suggestion(error: dict[str, Any]) -> str | None:
    if error.get("type") != "extra_forbidden":
        return None
    loc = error.get("loc", ())
    if not loc:
        return None
    field = str(loc[-1])
    matches = get_close_matches(field, KNOWN_MANIFEST_FIELDS, n=1, cutoff=0.72)
    if not matches or matches[0] == field:
        return None
    return matches[0]


def _collect_field_names(
    model: type[BaseModel], seen: set[type[BaseModel]] | None = None
) -> set[str]:
    seen = seen or set()
    if model in seen:
        return set()
    seen.add(model)

    names: set[str] = set()
    for name, field in model.model_fields.items():
        names.add(name)
        if isinstance(field.alias, str):
            names.add(field.alias)
        for inner_model in _inner_models(field.annotation):
            names.update(_collect_field_names(inner_model, seen))
    return names


def _inner_models(annotation: object) -> set[type[BaseModel]]:
    models: set[type[BaseModel]] = set()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        models.add(annotation)
    for arg in get_args(annotation):
        models.update(_inner_models(arg))
    return models


KNOWN_MANIFEST_FIELDS = frozenset(_collect_field_names(AgentsShipgateManifest))
