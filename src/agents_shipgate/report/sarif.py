from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate import __version__
from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.core.models import CheckMetadata, Finding, ReadinessReport

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
MAX_EVIDENCE_ITEMS = 20
MAX_EVIDENCE_STRING_LENGTH = 1000


def write_sarif_report(report: ReadinessReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(render_sarif_report(report), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def render_sarif_report(report: ReadinessReport) -> dict[str, Any]:
    active_findings = [finding for finding in report.findings if not finding.suppressed]
    metadata_by_id = {
        metadata.id: metadata for metadata in check_catalog(plugins_enabled=False)
    }
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Agents Shipgate",
                        "semanticVersion": __version__,
                        "informationUri": "https://github.com/ThreeMoonsLab/agents-shipgate",
                        "rules": _rules(active_findings, metadata_by_id),
                    }
                },
                "results": [_result(finding) for finding in active_findings],
            }
        ],
    }


def _rules(
    findings: list[Finding],
    metadata_by_id: dict[str, CheckMetadata],
) -> list[dict[str, Any]]:
    by_check: dict[str, Finding] = {}
    for finding in findings:
        by_check.setdefault(finding.check_id, finding)
    rules = []
    for check_id, finding in sorted(by_check.items()):
        metadata = metadata_by_id.get(check_id)
        description = metadata.description if metadata else finding.check_id
        full_description = (
            metadata.rationale or metadata.description
            if metadata
            else finding.recommendation
        )
        severity = metadata.default_severity if metadata else finding.severity
        rule: dict[str, Any] = {
            "id": check_id,
            "name": check_id,
            "shortDescription": {"text": description},
            "fullDescription": {"text": full_description},
            "defaultConfiguration": {"level": _level(severity)},
            "properties": {
                "category": metadata.category if metadata else finding.category,
                "severity": severity,
            },
        }
        if metadata and metadata.docs_url:
            rule["helpUri"] = metadata.docs_url
        if metadata and metadata.recommendation:
            rule["help"] = {"text": metadata.recommendation}
        rules.append(
            rule
        )
    return rules


def _result(finding: Finding) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": finding.check_id,
        "level": _level(finding.severity),
        "message": {"text": finding.title},
        "properties": {
            "severity": finding.severity,
            "category": finding.category,
            "recommendation": finding.recommendation,
            "confidence": finding.confidence,
            "evidence": _summarize_evidence(finding.evidence),
            "tool_name": finding.tool_name,
        },
    }
    if finding.fingerprint:
        result["fingerprints"] = {"agentsShipgateFingerprint": finding.fingerprint}
    location = _location(finding)
    if location:
        result["locations"] = [location]
    return result


def _level(severity: str) -> str:
    if severity in {"critical", "high"}:
        return "error"
    if severity == "medium":
        return "warning"
    return "note"


def _location(finding: Finding) -> dict[str, Any] | None:
    if not finding.source:
        return None
    source = finding.source
    artifact_uri: str | None = None
    line: int | None = None
    end_line: int | None = None
    start_column: int | None = None
    if source.path:
        artifact_uri = source.path
        line = source.start_line
        end_line = source.end_line
        start_column = source.start_column
        # Hybrid case: structured ``path`` set but no ``start_line`` (e.g.
        # MCP / OpenAI JSON inputs in v0.11, or a plugin that populates
        # `path` but leaves the line on the legacy ``path:line`` string).
        # Fall back to ``_split_location`` rather than dropping the
        # SARIF region; otherwise reviewers lose jump-to-line.
        if line is None:
            legacy = source.location or source.ref
            if legacy:
                _, line = _split_location(legacy)
    else:
        uri = source.location or source.ref
        if not uri:
            return None
        artifact_uri, line = _split_location(uri)
    physical_location: dict[str, Any] = {
        "artifactLocation": {"uri": artifact_uri},
    }
    region: dict[str, Any] = {}
    if line is not None:
        region["startLine"] = line
    if end_line is not None:
        region["endLine"] = end_line
    if start_column is not None:
        region["startColumn"] = start_column
    if region:
        physical_location["region"] = region
    location: dict[str, Any] = {
        "physicalLocation": physical_location,
        "logicalLocations": [
            {"name": finding.tool_name or finding.agent_id or finding.check_id}
        ],
    }
    # Empty string is a valid RFC 6901 root-document pointer (singleton
    # YAML object case), so check ``is not None`` rather than truthiness.
    if source.pointer is not None:
        location["properties"] = {"shipgatePointer": source.pointer}
    return location


def _split_location(value: str) -> tuple[str, int | None]:
    if "#" in value:
        value = value.split("#", 1)[0]
    path, _, maybe_line = value.rpartition(":")
    if path and maybe_line.isdigit():
        return path, int(maybe_line)
    return value, None


def _summarize_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _summarize_evidence(item) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) <= MAX_EVIDENCE_ITEMS:
            return [_summarize_evidence(item) for item in value]
        return {
            "count": len(value),
            "sample": [
                _summarize_evidence(item) for item in value[:MAX_EVIDENCE_ITEMS]
            ],
        }
    if isinstance(value, tuple | set):
        return _summarize_evidence(list(value))
    if isinstance(value, str) and len(value) > MAX_EVIDENCE_STRING_LENGTH:
        return {
            "length": len(value),
            "preview": value[:MAX_EVIDENCE_STRING_LENGTH],
            "truncated": True,
        }
    return value
