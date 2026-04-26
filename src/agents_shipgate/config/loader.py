from __future__ import annotations

from difflib import get_close_matches
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.errors import ConfigError

KNOWN_MANIFEST_FIELDS = {
    "agent",
    "annotations",
    "ci",
    "confidence",
    "credential_mode",
    "declared_purpose",
    "deep_import",
    "directory",
    "downstream_critical_fields",
    "entrypoint",
    "environment",
    "fail_on",
    "formats",
    "function_schemas",
    "id",
    "ignore",
    "instructions_preview",
    "mode",
    "model_config",
    "name",
    "object",
    "openai_api",
    "optional",
    "output",
    "owner",
    "path",
    "permissions",
    "policies",
    "pr_comment",
    "project",
    "prohibited_actions",
    "prompt_files",
    "reason",
    "repo",
    "response_formats",
    "require_approval_for_tools",
    "require_confirmation_for_tools",
    "require_idempotency_for_tools",
    "risk_overrides",
    "scopes",
    "sdk",
    "severity_overrides",
    "static_extract",
    "tags",
    "target",
    "test_cases",
    "tool",
    "tool_sources",
    "tools",
    "trace_samples",
    "trust",
    "type",
    "upload_artifact",
    "version",
}


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
    version = data.get("version")
    if version != "0.1":
        raise ConfigError(
            f"Unsupported manifest version {version!r}; this Agents Shipgate build supports version '0.1'."
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
    return matches[0] if matches else None
