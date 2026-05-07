from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import ArtifactPathConfig, ValidationConfig
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import ValidationArtifacts
from agents_shipgate.inputs.common import (
    load_structured_file,
    load_text_file,
    resolve_input_path,
)
from agents_shipgate.inputs.traces import normalize_trace_event

STREAM_SUFFIXES = {".json", ".jsonl"}
DECLARATIVE_SUFFIXES = {".json", ".yaml", ".yml"}
OVERRIDE_ACTIONS = {"override", "bypass", "auto_approve"}


def load_validation_artifacts(
    config: ValidationConfig | None,
    base_dir: Path,
) -> ValidationArtifacts | None:
    if config is None:
        return None

    artifacts = ValidationArtifacts()
    evidence = config.evidence

    files, traces = _load_approval_traces(
        evidence.approval_traces,
        base_dir,
        artifacts.warnings,
    )
    artifacts.approval_trace_files.extend(files)
    artifacts.approval_traces.extend(traces)

    files, events = _load_override_logs(
        evidence.override_logs,
        base_dir,
        artifacts.warnings,
    )
    artifacts.override_log_files.extend(files)
    artifacts.override_events.extend(events)

    files, exclusions = _load_high_risk_exclusions(
        evidence.high_risk_exclusions,
        base_dir,
        artifacts.warnings,
    )
    artifacts.high_risk_exclusion_files.extend(files)
    artifacts.high_risk_auto_approval_exclusions.extend(exclusions)

    files, criteria = _load_promotion_criteria(
        evidence.promotion_criteria,
        base_dir,
        artifacts.warnings,
    )
    artifacts.promotion_criteria_files.extend(files)
    artifacts.promotion_criteria.extend(criteria)

    return artifacts


def _load_approval_traces(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    warnings: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    files: list[str] = []
    traces: list[dict[str, Any]] = []
    for ref in refs:
        loaded = _load_stream_items(ref, base_dir, warnings, "approval trace")
        if loaded is None:
            continue
        path, items = loaded
        files.append(_display_path(path, base_dir))
        for item in items:
            normalized = normalize_trace_event(item)
            if normalized is None:
                warnings.append(
                    "validation: approval trace artifact "
                    f"{ref.path!r} contains an entry that could not be normalized."
                )
                continue
            traces.append(normalized)
    return files, traces


def _load_override_logs(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    warnings: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    files: list[str] = []
    events: list[dict[str, Any]] = []
    for ref in refs:
        loaded = _load_stream_items(ref, base_dir, warnings, "override log")
        if loaded is None:
            continue
        path, items = loaded
        files.append(_display_path(path, base_dir))
        for item in items:
            normalized = normalize_override_event(item)
            if normalized is None:
                warnings.append(
                    "validation: override log artifact "
                    f"{ref.path!r} contains an entry that could not be normalized."
                )
                continue
            events.append(normalized)
    return files, events


def normalize_override_event(item: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = _nested_string(
        item,
        [
            ("tool_name",),
            ("tool",),
            ("name",),
            ("tool_call", "name"),
            ("function_call", "name"),
        ],
    )
    if not tool_name:
        return None
    action = item.get("action")
    if not isinstance(action, str) or action not in OVERRIDE_ACTIONS:
        return None
    normalized: dict[str, Any] = {
        "tool_name": tool_name,
        "action": action,
    }
    reason = item.get("reason")
    if isinstance(reason, str) and reason.strip():
        normalized["reason"] = reason.strip()
    for key in ("actor", "timestamp"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    return normalized


def _load_high_risk_exclusions(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    warnings: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    files: list[str] = []
    exclusions: list[dict[str, Any]] = []
    for ref in refs:
        loaded = _load_declarative_object(ref, base_dir, warnings, "high-risk exclusions")
        if loaded is None:
            continue
        path, data = loaded
        raw_entries = data.get("high_risk_auto_approval_exclusions")
        if not isinstance(raw_entries, list):
            raise InputParseError(
                "validation: high-risk exclusions file must contain "
                f"high_risk_auto_approval_exclusions: {ref.path}"
            )
        files.append(_display_path(path, base_dir))
        for entry in raw_entries:
            normalized = _normalize_exclusion(entry)
            if normalized is None:
                warnings.append(
                    "validation: high-risk exclusions artifact "
                    f"{ref.path!r} contains an invalid entry."
                )
                continue
            exclusions.append(normalized)
    return files, exclusions


def _load_promotion_criteria(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    warnings: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    files: list[str] = []
    criteria: list[dict[str, Any]] = []
    for ref in refs:
        loaded = _load_declarative_object(ref, base_dir, warnings, "promotion criteria")
        if loaded is None:
            continue
        path, data = loaded
        files.append(_display_path(path, base_dir))
        criteria.append(data)
    return files, criteria


def _load_stream_items(
    ref: ArtifactPathConfig,
    base_dir: Path,
    warnings: list[str],
    label: str,
) -> tuple[Path, list[dict[str, Any]]] | None:
    path = resolve_input_path(base_dir, ref.path)
    try:
        _require_suffix(path, STREAM_SUFFIXES, label)
        items = _load_json_items(path, ref.path, warnings)
    except InputParseError:
        if not ref.optional:
            raise
        warnings.append(f"validation: optional {label} artifact {ref.path!r} failed to load.")
        return None
    return path, items


def _load_declarative_object(
    ref: ArtifactPathConfig,
    base_dir: Path,
    warnings: list[str],
    label: str,
) -> tuple[Path, dict[str, Any]] | None:
    path = resolve_input_path(base_dir, ref.path)
    try:
        _require_suffix(path, DECLARATIVE_SUFFIXES, label)
        data = load_structured_file(path)
        if not isinstance(data, dict):
            raise InputParseError(
                f"validation: {label} artifact must contain an object: {ref.path}"
            )
    except InputParseError:
        if not ref.optional:
            raise
        warnings.append(f"validation: optional {label} artifact {ref.path!r} failed to load.")
        return None
    return path, data


def _load_json_items(
    path: Path,
    ref: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        items: list[dict[str, Any]] = []
        for line_number, line in enumerate(load_text_file(path).splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputParseError(
                    f"validation: unable to parse JSONL {path}:{line_number}: {exc}"
                ) from exc
            if isinstance(item, dict):
                items.append(item)
            else:
                warnings.append(
                    "validation: artifact "
                    f"{ref!r} line {line_number} is not an object."
                )
        return items
    data = load_structured_file(path)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("events", "trace_samples", "traces", "tool_calls", "overrides"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [data]
    raise InputParseError(f"validation: artifact must be an object or array: {path}")


def _require_suffix(path: Path, allowed: set[str], label: str) -> None:
    suffix = path.suffix.lower()
    if suffix not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise InputParseError(
            f"validation: {label} artifact {path} must use one of: {allowed_text}"
        )


def _normalize_exclusion(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    tool = item.get("tool")
    reason = item.get("reason")
    if not isinstance(tool, str) or not tool.strip():
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    normalized: dict[str, Any] = {
        "tool": tool.strip(),
        "reason": reason.strip(),
    }
    owner = item.get("owner")
    if isinstance(owner, str) and owner.strip():
        normalized["owner"] = owner.strip()
    return normalized


def _nested_string(item: dict[str, Any], paths: list[tuple[str, ...]]) -> str | None:
    for path in paths:
        value: Any = item
        for part in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if isinstance(value, str) and value:
            return value
    return None


def _display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
