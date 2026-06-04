from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate import __version__
from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.core.privacy import redact_data
from agents_shipgate.report.sarif import _level, _source_to_location, _summarize_evidence
from agents_shipgate.skill.models import SkillReviewReport


def write_skill_json_report(report: SkillReviewReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(skill_json_payload(report), indent=2), encoding="utf-8")


def skill_json_payload(report: SkillReviewReport) -> dict[str, Any]:
    payload = report.model_dump(mode="json", exclude_none=False)
    for finding in payload.get("findings", []):
        if finding.get("patches") is None:
            finding.pop("patches", None)
        source = finding.get("source")
        if isinstance(source, dict):
            for key in ("end_line", "start_column", "pointer"):
                if source.get(key) is None:
                    source.pop(key, None)
    return redact_data(payload)


def write_skill_markdown_report(report: SkillReviewReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_skill_markdown_report(report), encoding="utf-8")


def render_skill_markdown_report(report: SkillReviewReport) -> str:
    report = SkillReviewReport.model_validate(skill_json_payload(report))
    title = {
        "lint": "Agents Shipgate Skill Lint",
        "security": "Agents Shipgate Skill Security Review",
        "review": "Agents Shipgate Skill Review",
    }[report.command]
    summary = report.summary
    lines = [
        f"# {title}",
        "",
        f"Workspace: `{_safe(report.workspace)}`",
        f"Verdict: **{summary.verdict.upper()}**",
        f"CI mode: `{report.ci_mode}`",
        "",
        "## Summary",
        "",
        f"- Artifacts: {summary.artifact_count}",
        f"- Findings: {summary.finding_count}",
        f"- Critical: {summary.critical_count}",
        f"- High: {summary.high_count}",
        f"- Medium: {summary.medium_count}",
        f"- Low: {summary.low_count}",
        f"- Suppressed: {summary.suppressed_count}",
        "",
    ]
    if report.source_warnings:
        lines.extend(["## Source Warnings", ""])
        for warning in report.source_warnings:
            lines.append(f"- {_safe(warning)}")
        lines.append("")
    lines.extend(["## Findings", ""])
    active = [finding for finding in report.findings if not finding.suppressed]
    if not active:
        lines.extend(["No active findings.", ""])
    else:
        for finding in sorted(active, key=lambda item: (_severity_sort(item.severity), item.check_id)):
            location = ""
            if finding.source and finding.source.path:
                location = f" ({finding.source.path}"
                if finding.source.start_line:
                    location += f":{finding.source.start_line}"
                location += ")"
            lines.extend(
                [
                    f"### {finding.check_id}: {_safe(finding.title)}",
                    "",
                    f"- Severity: `{finding.severity}`",
                    f"- Confidence: `{finding.confidence}`",
                    f"- Location: `{_safe(location.strip(' ()'))}`" if location else "- Location: `(none)`",
                    f"- Recommendation: {_safe(finding.recommendation)}",
                    "",
                ]
            )
    if any(finding.suppressed for finding in report.findings):
        lines.extend(["## Suppressed Findings", ""])
        for finding in report.findings:
            if finding.suppressed:
                lines.append(
                    f"- {finding.check_id}: {_safe(finding.title)} "
                    f"({_safe(finding.suppression_reason or 'suppressed')})"
                )
        lines.append("")
    lines.extend(["## Artifacts", ""])
    if not report.artifacts:
        lines.extend(["No skill or instruction artifacts matched.", ""])
    else:
        for artifact in report.artifacts:
            lines.append(f"- `{artifact.path}` ({artifact.kind})")
        lines.append("")
    return "\n".join(lines)


def write_skill_sarif_report(report: SkillReviewReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(render_skill_sarif_report(report), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def render_skill_sarif_report(report: SkillReviewReport) -> dict[str, Any]:
    report = SkillReviewReport.model_validate(skill_json_payload(report))
    findings = [finding for finding in report.findings if not finding.suppressed]
    metadata_by_id = {metadata.id: metadata for metadata in check_catalog(plugins_enabled=False)}
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Agents Shipgate Skill Review",
                        "semanticVersion": __version__,
                        "informationUri": "https://github.com/ThreeMoonsLab/agents-shipgate",
                        "rules": _rules(findings, metadata_by_id),
                    }
                },
                "results": [_result(finding) for finding in findings],
            }
        ],
    }


def _rules(findings: list[Any], metadata_by_id: dict[str, Any]) -> list[dict[str, Any]]:
    rules = []
    for check_id in sorted({finding.check_id for finding in findings}):
        metadata = metadata_by_id.get(check_id)
        sample = next(finding for finding in findings if finding.check_id == check_id)
        rule: dict[str, Any] = {
            "id": check_id,
            "name": check_id,
            "shortDescription": {"text": metadata.description if metadata else sample.title},
            "fullDescription": {
                "text": (metadata.rationale or metadata.description) if metadata else sample.recommendation
            },
            "defaultConfiguration": {
                "level": _level(metadata.default_severity if metadata else sample.severity)
            },
            "properties": {
                "category": metadata.category if metadata else sample.category,
                "severity": metadata.default_severity if metadata else sample.severity,
                "tags": sorted({metadata.category if metadata else sample.category}),
            },
        }
        if metadata and metadata.docs_url:
            rule["helpUri"] = metadata.docs_url
        if metadata and metadata.recommendation:
            rule["help"] = {"text": metadata.recommendation}
        rules.append(rule)
    return rules


def _result(finding: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": finding.check_id,
        "level": _level(finding.severity),
        "message": {"text": finding.title},
        "properties": {
            "severity": finding.severity,
            "category": finding.category,
            "recommendation": finding.recommendation,
            "confidence": finding.confidence,
            "provenance_kind": finding.provenance_kind,
            "evidence": _summarize_evidence(finding.evidence),
        },
    }
    if finding.fingerprint:
        result["fingerprints"] = {"agentsShipgateFingerprint": finding.fingerprint}
    location = _source_to_location(finding.source, finding.check_id)
    if location is not None:
        result["locations"] = [location]
    return result


def _severity_sort(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(severity, 5)


def _safe(value: str) -> str:
    return value.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")
