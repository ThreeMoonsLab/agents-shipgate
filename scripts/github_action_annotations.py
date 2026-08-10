from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agents_shipgate.report.pr_projection import (
    PR_PROJECTION_SCHEMA_VERSION,
    item_to_action_annotation,
    select_pr_items,
)

ANNOTATION_SCHEMA_VERSION = "0.1"


def build_annotations(output_dir: Path, *, limit: int = 50) -> dict[str, Any]:
    report_path = output_dir / "report.json"
    verifier_path = output_dir / "verifier.json"
    payload = _load_json(report_path)
    verifier = _load_json(verifier_path)
    selected = select_pr_items(payload, verifier, limit=10_000)
    normalized_limit = max(0, limit)
    annotations: list[dict[str, Any]] = []
    omitted_no_source = 0
    omitted_by_limit = 0
    for item in selected:
        if item.source_path is None:
            omitted_no_source += 1
            continue
        if len(annotations) >= normalized_limit:
            omitted_by_limit += 1
            continue
        annotations.append(item_to_action_annotation(item))
    return {
        "annotation_schema_version": ANNOTATION_SCHEMA_VERSION,
        "pr_projection_schema_version": PR_PROJECTION_SCHEMA_VERSION,
        "source_report": str(report_path),
        "source_verifier": str(verifier_path),
        "limit": normalized_limit,
        "annotations": annotations,
        "omitted": {
            "no_source": omitted_no_source,
            "limit": omitted_by_limit,
        },
    }


def write_annotations(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def emit_github_annotations(payload: dict[str, Any]) -> None:
    for item in payload.get("annotations") or []:
        level = item.get("level") or "notice"
        props = [("file", item.get("path"))]
        if item.get("start_line") is not None:
            props.append(("line", item.get("start_line")))
        if item.get("end_line") is not None:
            props.append(("endLine", item.get("end_line")))
        if item.get("start_column") is not None:
            props.append(("col", item.get("start_column")))
        props.append(("title", item.get("title")))
        prop_text = ",".join(
            f"{key}={_escape_property(value)}"
            for key, value in props
            if value is not None and str(value) != ""
        )
        message = _escape_data(item.get("message") or "")
        print(f"::{level} {prop_text}::{message}")


def _escape_data(value: object) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: object) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    output_dir = Path(os.environ.get("OUTPUT_DIR") or "agents-shipgate-reports")
    raw_limit = os.environ.get("CHECK_ANNOTATION_LIMIT") or "50"
    try:
        limit = int(raw_limit)
    except ValueError:
        limit = 50
    payload = build_annotations(output_dir, limit=limit)
    write_annotations(payload, output_dir / "check-annotations.json")
    emit_github_annotations(payload)


if __name__ == "__main__":
    main()
