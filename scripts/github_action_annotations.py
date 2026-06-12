from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ANNOTATION_SCHEMA_VERSION = "0.1"


def build_annotations(output_dir: Path, *, limit: int = 50) -> dict[str, Any]:
    report_path = output_dir / "report.json"
    payload = _load_json(report_path)
    findings = payload.get("findings") or []
    release_decision = payload.get("release_decision") or {}
    selected = _selected_findings(findings, release_decision)
    normalized_limit = max(0, limit)
    annotations: list[dict[str, Any]] = []
    omitted_no_source = 0
    omitted_by_limit = 0
    for finding in selected:
        source = _best_source(finding)
        if source is None:
            omitted_no_source += 1
            continue
        if len(annotations) >= normalized_limit:
            omitted_by_limit += 1
            continue
        annotations.append(_annotation(finding, source))
    return {
        "annotation_schema_version": ANNOTATION_SCHEMA_VERSION,
        "source_report": str(report_path),
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


def _selected_findings(
    findings: list[Any],
    release_decision: dict[str, Any],
) -> list[dict[str, Any]]:
    active = [
        finding
        for finding in findings
        if isinstance(finding, dict) and not finding.get("suppressed")
    ]
    by_id = {finding.get("id"): finding for finding in active if finding.get("id")}
    by_fingerprint = {
        finding.get("fingerprint"): finding
        for finding in active
        if finding.get("fingerprint")
    }
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for kind in ("blockers", "review_items"):
        for item in release_decision.get(kind) or []:
            if not isinstance(item, dict):
                continue
            finding = by_id.get(item.get("id")) or by_fingerprint.get(
                item.get("fingerprint")
            )
            if finding is not None and id(finding) not in seen:
                selected.append(finding)
                seen.add(id(finding))
    if selected:
        return selected
    return sorted(active, key=_finding_sort_key)


def _finding_sort_key(finding: dict[str, Any]) -> tuple[int, str, str]:
    return (
        {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(
            str(finding.get("severity") or ""),
            9,
        ),
        str(finding.get("check_id") or ""),
        str(finding.get("title") or ""),
    )


def _best_source(finding: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("source", "policy_evidence_source"):
        source = finding.get(key)
        if not isinstance(source, dict):
            continue
        if source.get("path"):
            return source
    return None


def _annotation(finding: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    path = str(source.get("path") or "")
    selector = _selector(source, path)
    title = f"{finding.get('check_id')}: {finding.get('title')}"
    recommendation = str(finding.get("recommendation") or "")
    message = recommendation or str(finding.get("title") or finding.get("check_id") or "")
    if selector:
        message = f"{message} Source: {selector}"
    return {
        "level": _annotation_level(str(finding.get("severity") or "")),
        "path": path,
        "start_line": source.get("start_line"),
        "end_line": source.get("end_line"),
        "start_column": source.get("start_column"),
        "selector": selector,
        "title": _truncate(title, 160),
        "message": _truncate(message, 1000),
        "check_id": finding.get("check_id"),
        "severity": finding.get("severity"),
        "finding_id": finding.get("id"),
        "fingerprint": finding.get("fingerprint"),
    }


def _annotation_level(severity: str) -> str:
    if severity in {"critical", "high"}:
        return "error"
    if severity == "medium":
        return "warning"
    return "notice"


def _selector(source: dict[str, Any], path: str) -> str:
    pointer = source.get("pointer")
    if pointer is not None:
        return f"{path}#{pointer}"
    location = source.get("location")
    if location:
        return str(location)
    ref = source.get("ref")
    if ref:
        return str(ref)
    return path


def _escape_data(value: object) -> str:
    return (
        str(value)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _escape_property(value: object) -> str:
    return (
        _escape_data(value)
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "..."


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
