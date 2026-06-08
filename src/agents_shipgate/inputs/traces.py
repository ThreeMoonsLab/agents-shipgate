from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import load_structured_file, load_text_file, resolve_input_path
from agents_shipgate.schemas.manifest import ArtifactPathConfig

TRACE_SOURCE_KEY = "_shipgate_source"


@dataclass(frozen=True)
class _TraceInput:
    item: dict[str, Any]
    line: int | None = None
    pointer: str | None = None
    index: int = 0


def load_trace_artifacts(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    warnings: list[str],
    *,
    label: str,
    source_type: str,
    warn_optional_fail: bool = True,
) -> tuple[list[str], list[dict[str, Any]]]:
    files: list[str] = []
    traces: list[dict[str, Any]] = []
    for ref in refs:
        path = resolve_input_path(base_dir, ref.path)
        try:
            raw_items = _load_trace_items(path, ref.path, warnings)
        except InputParseError:
            if not ref.optional:
                raise
            if warn_optional_fail:
                warnings.append(
                    f"Optional {label} trace artifact {ref.path!r} failed to load."
                )
            continue
        display_path = _display_path(path, base_dir)
        files.append(display_path)
        for raw in raw_items:
            normalized = normalize_trace_event(raw.item)
            if normalized:
                normalized[TRACE_SOURCE_KEY] = {
                    "source_type": source_type,
                    "source_ref": ref.path,
                    "source_path": display_path,
                    "source_line": raw.line,
                    "source_pointer": raw.pointer,
                    "source_index": raw.index,
                }
                traces.append(normalized)
            else:
                warnings.append(
                    f"{label} trace artifact {ref.path!r} contains an entry that "
                    "could not be normalized."
                )
    return files, traces


def normalize_trace_event(item: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = _nested_string(
        item,
        [
            ("tool_name",),
            ("tool",),
            ("name",),
            ("function_call", "name"),
            ("tool_call", "name"),
            ("content", "functionCall", "name"),
        ],
    )
    if not tool_name:
        return None
    normalized: dict[str, Any] = {"tool_name": tool_name}
    for key in ("provider", "operation", "capability_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    for key in ("approved", "confirmed", "success"):
        value = item.get(key)
        if isinstance(value, bool):
            normalized[key] = value
    error = item.get("error")
    if error is None:
        error = item.get("error_message")
    if isinstance(error, str) and error.strip():
        normalized["error"] = error.strip()
        normalized.setdefault("success", False)
    elif isinstance(error, bool):
        normalized["error"] = error
        normalized.setdefault("success", False)
    for key in ("reason", "actor", "trace_id", "run_id", "call_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    timestamp = item.get("timestamp")
    if isinstance(timestamp, str) and timestamp.strip():
        normalized["timestamp"] = timestamp.strip()
    elif isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        normalized["timestamp"] = str(timestamp)
    return normalized


def _load_trace_items(
    path: Path,
    ref: str,
    warnings: list[str],
) -> list[_TraceInput]:
    if path.suffix.lower() == ".jsonl":
        items: list[_TraceInput] = []
        event_index = 0
        for line_number, line in enumerate(load_text_file(path).splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputParseError(
                    f"Unable to parse JSONL trace {path}:{line_number}: {exc}"
                ) from exc
            if isinstance(item, dict):
                items.append(
                    _TraceInput(
                        item=item,
                        line=line_number,
                        pointer=None,
                        index=event_index,
                    )
                )
                event_index += 1
            else:
                warnings.append(f"Trace artifact {ref!r} line {line_number} is not an object.")
        return items
    data = load_structured_file(path)
    if isinstance(data, list):
        return [
            _TraceInput(item=item, pointer=f"/{index}", index=index)
            for index, item in enumerate(data)
            if isinstance(item, dict)
        ]
    if isinstance(data, dict):
        for key in ("trace_samples", "traces", "events", "tool_calls"):
            value = data.get(key)
            if isinstance(value, list):
                return [
                    _TraceInput(item=item, pointer=f"/{key}/{index}", index=index)
                    for index, item in enumerate(value)
                    if isinstance(item, dict)
                ]
        return [_TraceInput(item=data, pointer="", index=0)]
    raise InputParseError(f"Trace artifact must be an object or array: {path}")


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
