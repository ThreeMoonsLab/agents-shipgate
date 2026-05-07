from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_shipgate.core.check_ids import expands_to_check_id
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import (
    BaselineSummary,
    Finding,
    ReadinessReport,
    Severity,
    ToolSurfaceFacts,
)

BASELINE_SCHEMA_VERSION = "0.3"


class BaselineFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    check_id: str
    tool_name: str | None = None
    severity: Severity
    title: str


class BaselineFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.2", "0.3"] = BASELINE_SCHEMA_VERSION
    project: dict[str, object] = Field(default_factory=dict)
    agent: dict[str, object] = Field(default_factory=dict)
    created_at: str
    source_report_run_id: str
    findings: list[BaselineFinding] = Field(default_factory=list)
    tool_surface_facts: ToolSurfaceFacts | None = None
    notes: list[str] = Field(default_factory=list)


def baseline_from_report(report: ReadinessReport) -> BaselineFile:
    return BaselineFile(
        project=report.project,
        agent=report.agent,
        created_at=_utc_now(),
        source_report_run_id=report.run_id,
        tool_surface_facts=report.tool_surface_facts,
        findings=[
            BaselineFinding(
                fingerprint=finding.fingerprint or finding.id or "",
                check_id=finding.check_id,
                tool_name=finding.tool_name,
                severity=finding.severity,
                title=finding.title,
            )
            for finding in _active_findings(report.findings)
            if finding.fingerprint or finding.id
        ],
    )


def write_baseline(report: ReadinessReport, path: Path) -> BaselineFile:
    baseline = baseline_from_report(report)
    baseline = _preserve_created_at_when_content_matches(baseline, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        baseline.model_dump_json(indent=2, exclude_none=False) + "\n",
        encoding="utf-8",
    )
    return baseline


def load_baseline(path: Path) -> BaselineFile:
    if not path.exists():
        raise InputParseError(f"Baseline file not found: {path}")
    try:
        return BaselineFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise InputParseError(f"Invalid baseline file {path}: {exc}") from exc


def apply_baseline(
    findings: list[Finding],
    baseline: BaselineFile,
    *,
    display_path: str,
) -> BaselineSummary:
    baseline_fingerprints = {
        finding.fingerprint for finding in baseline.findings if finding.fingerprint
    }
    current_active_fingerprints: set[str] = set()
    matched_legacy_fingerprints: set[str] = set()
    matched = 0
    new = 0
    for finding in findings:
        if finding.suppressed:
            continue
        fingerprint = finding.fingerprint or finding.id
        if not fingerprint:
            continue
        current_active_fingerprints.add(fingerprint)
        if fingerprint in baseline_fingerprints:
            finding.baseline_status = "matched"
            matched += 1
        elif legacy_match := _legacy_baseline_match(finding, baseline.findings):
            finding.baseline_status = "matched"
            matched += 1
            matched_legacy_fingerprints.add(legacy_match.fingerprint)
        else:
            finding.baseline_status = "new"
            new += 1
    return BaselineSummary(
        path=display_path,
        matched_count=matched,
        new_count=new,
        resolved_count=len(
            baseline_fingerprints
            - current_active_fingerprints
            - matched_legacy_fingerprints
        ),
    )


def _active_findings(findings: list[Finding]) -> list[Finding]:
    return [finding for finding in findings if not finding.suppressed]


def _legacy_baseline_match(
    finding: Finding, baseline_findings: list[BaselineFinding]
) -> BaselineFinding | None:
    for baseline_finding in baseline_findings:
        if not expands_to_check_id(baseline_finding.check_id, finding.check_id):
            continue
        if (
            baseline_finding.tool_name is not None
            and baseline_finding.tool_name != finding.tool_name
        ):
            continue
        return baseline_finding
    return None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _preserve_created_at_when_content_matches(
    baseline: BaselineFile, path: Path
) -> BaselineFile:
    if not path.exists():
        return baseline
    try:
        existing = load_baseline(path)
    except InputParseError:
        return baseline
    if _baseline_content_identity(existing) != _baseline_content_identity(baseline):
        return baseline
    return baseline.model_copy(update={"created_at": existing.created_at})


def _baseline_content_identity(baseline: BaselineFile) -> dict[str, object]:
    return baseline.model_dump(mode="json", exclude={"created_at"})
