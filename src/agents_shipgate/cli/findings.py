"""``shipgate findings`` — filter report findings for reviewer triage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.core.findings import (
    PROVENANCE_KIND_ORDER,
    provenance_kind_counts,
)
from agents_shipgate.core.privacy import sanitize_report_payload
from agents_shipgate.schemas.common import ProvenanceKind, parse_provenance_kind
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.report import Finding, ReadinessReport

_MIN_SUPPORTED_SCHEMA = "0.15"


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except (AttributeError, ValueError) as exc:
        raise ValueError(
            f"invalid report_schema_version: {value!r}"
        ) from exc


def _load_report(path: Path) -> ReadinessReport:
    if not path.is_file():
        raise ValueError(f"report file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read report at {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"report is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("report JSON must be an object")

    version = payload.get("report_schema_version")
    if not isinstance(version, str):
        raise ValueError(
            "input must be an agents-shipgate report.json with a "
            "string `report_schema_version`."
        )
    if _version_tuple(version) < _version_tuple(_MIN_SUPPORTED_SCHEMA):
        raise ValueError(
            f"findings provenance filtering requires report_schema_version "
            f">= {_MIN_SUPPORTED_SCHEMA} (got {version!r}). The v0.15 "
            "`provenance_kind` field is required for this command. "
            "Re-scan with the current CLI: "
            "`agents-shipgate scan -c shipgate.yaml --format json`."
        )

    payload = sanitize_report_payload(payload)
    try:
        report = ReadinessReport.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"report.json failed validation: {exc}") from exc

    missing = [
        finding.id or finding.fingerprint or finding.check_id
        for finding in report.findings
        if finding.provenance_kind is None
    ]
    if missing:
        preview = ", ".join(missing[:3])
        suffix = f", … (+{len(missing) - 3})" if len(missing) > 3 else ""
        raise ValueError(
            "report.json contains finding(s) without `provenance_kind`: "
            f"{preview}{suffix}. Re-scan with the current CLI: "
            "`agents-shipgate scan -c shipgate.yaml --format json`."
        )
    return report


def _parse_provenance_filter(value: str | None) -> list[ProvenanceKind]:
    if value is None or value.strip() == "":
        return list(PROVENANCE_KIND_ORDER)
    parsed: list[ProvenanceKind] = []
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        try:
            kind = parse_provenance_kind(token)
        except ValueError as exc:
            allowed = ", ".join(PROVENANCE_KIND_ORDER)
            raise ValueError(
                f"unsupported --provenance-kind value {token!r}; "
                f"expected one of: {allowed}"
            ) from exc
        if kind not in parsed:
            parsed.append(kind)
    if not parsed:
        allowed = ", ".join(PROVENANCE_KIND_ORDER)
        raise ValueError(
            "--provenance-kind must include at least one value; "
            f"expected one of: {allowed}"
        )
    return parsed


def _counts_payload(counts: dict[ProvenanceKind, int]) -> dict[str, int]:
    return {kind: counts[kind] for kind in PROVENANCE_KIND_ORDER}


def _finding_payload(finding: Finding) -> dict[str, Any]:
    return {
        "id": finding.id,
        "fingerprint": finding.fingerprint,
        "check_id": finding.check_id,
        "severity": finding.severity,
        "title": finding.title,
        "tool_name": finding.tool_name,
        "confidence": finding.confidence,
        "provenance_kind": finding.provenance_kind,
        "agent_action": finding.agent_action,
        "suppressed": finding.suppressed,
        "source": (
            finding.source.model_dump(mode="json", exclude_none=True)
            if finding.source is not None
            else None
        ),
    }


def findings_payload(
    *,
    report_path: Path,
    provenance_kind_filter: str | None,
    include_suppressed: bool,
) -> dict[str, Any]:
    report = _load_report(report_path)
    selected = _parse_provenance_filter(provenance_kind_filter)
    included = [
        finding
        for finding in report.findings
        if include_suppressed or not finding.suppressed
    ]
    matched = [
        finding
        for finding in included
        if finding.provenance_kind in selected
    ]
    return {
        "filters": {
            "source_report": str(report_path.resolve()),
            "provenance_kind": list(selected),
            "include_suppressed": include_suppressed,
        },
        "summary": {
            "total_findings": len(report.findings),
            "included_findings": len(included),
            "matched_findings": len(matched),
            "suppressed_omitted": 0
            if include_suppressed
            else sum(1 for finding in report.findings if finding.suppressed),
            "by_provenance_kind": _counts_payload(
                provenance_kind_counts(
                    report.findings,
                    include_suppressed=include_suppressed,
                )
            ),
            "matched_by_provenance_kind": _counts_payload(
                provenance_kind_counts(matched, include_suppressed=True)
            ),
        },
        "findings": [_finding_payload(finding) for finding in matched],
    }


def findings(
    source: Path = typer.Option(
        Path("agents-shipgate-reports/report.json"),
        "--from",
        help=(
            "Path to the scan's `report.json`. Default mirrors the "
            "canonical reports directory."
        ),
    ),
    provenance_kind: str | None = typer.Option(
        None,
        "--provenance-kind",
        help=(
            "Comma-separated provenance kinds to include. Defaults to all "
            "kinds. Values: static_declaration, ast_extraction, "
            "keyword_heuristic, regex_heuristic, policy_pack."
        ),
    ),
    include_suppressed: bool = typer.Option(
        False,
        "--include-suppressed",
        help="Include suppressed findings. Defaults to active findings only.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of text.",
    ),
) -> None:
    """Filter report findings by provenance kind for reviewer triage."""

    try:
        payload = findings_payload(
            report_path=source,
            provenance_kind_filter=provenance_kind,
            include_suppressed=include_suppressed,
        )
    except ValueError as exc:
        typer.echo(f"findings: {exc}", err=True)
        emit_agent_mode_error(
            "input_parse_error",
            message=str(exc),
            source_report=str(source),
            next_action="agents-shipgate scan -c shipgate.yaml --format json",
            next_actions=[
                NextAction(
                    kind="command",
                    command=(
                        "agents-shipgate scan -c shipgate.yaml --format json"
                    ),
                    why=(
                        f"Could not load or filter {source}. Generate a "
                        "fresh report.json with the current CLI."
                    ),
                    expects=(
                        "agents-shipgate-reports/report.json on disk, "
                        "validatable against report schema v0.15 or newer."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(3) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    summary = payload["summary"]
    typer.echo(f"Source report: {payload['filters']['source_report']}")
    typer.echo(
        "Scope: "
        + ("all findings" if include_suppressed else "active findings only")
    )
    typer.echo(f"Matched findings: {summary['matched_findings']}")
    typer.echo("Provenance counts:")
    for kind in PROVENANCE_KIND_ORDER:
        typer.echo(f"  {kind}: {summary['by_provenance_kind'][kind]}")
    typer.echo("")
    if not payload["findings"]:
        typer.echo("No findings matched.")
        return
    for finding in payload["findings"]:
        target = f" [{finding['tool_name']}]" if finding["tool_name"] else ""
        suppressed = " (suppressed)" if finding["suppressed"] else ""
        typer.echo(
            f"- {finding['severity'].upper()}: {finding['check_id']}"
            f"{target}{suppressed} "
            f"({finding['provenance_kind']}, {finding['confidence']}) - "
            f"{finding['title']}"
        )
