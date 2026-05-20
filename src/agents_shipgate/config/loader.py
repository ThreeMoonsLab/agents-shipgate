from __future__ import annotations

from difflib import get_close_matches
from pathlib import Path
from typing import Any, get_args

import yaml
from pydantic import BaseModel, ValidationError

from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.inputs.common import (
    PositionIndex,
    load_structured_file_with_positions,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        hint = ""
        if path.name == "shipgate.yaml":
            hint = " Run `agents-shipgate init --workspace . --write` to create one."
        raise ConfigError(f"Config file not found: {path} in {Path.cwd()}.{hint}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a YAML object: {path}")
    return data


def load_manifest(path: str | Path) -> AgentsShipgateManifest:
    config_path = Path(path)
    data = load_yaml_file(config_path)
    return _validate_manifest_data(data, config_path)


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
        raise ConfigError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["Invalid shipgate.yaml:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        message = error.get("msg", "invalid value")
        suggestion = _field_suggestion(error)
        if suggestion:
            message = f"{message}. Did you mean {suggestion}?"
        lines.append(f"- {location}: {message}")
    return "\n".join(lines)


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
