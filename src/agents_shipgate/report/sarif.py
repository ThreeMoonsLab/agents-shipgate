from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

from agents_shipgate import __version__
from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.core.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.core.domain import SemanticIssueKind
from agents_shipgate.core.privacy import sanitize_report
from agents_shipgate.schemas.checks import CheckMetadata
from agents_shipgate.schemas.report import (
    EvidenceGap,
    Finding,
    ReadinessReport,
)

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
MAX_EVIDENCE_ITEMS = 20
MAX_EVIDENCE_STRING_LENGTH = 1000
SEMANTIC_GAP_RULE_PREFIX = "SHIP-SEMANTIC-"


def write_sarif_report(report: ReadinessReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(render_sarif_report(report), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def render_sarif_report(report: ReadinessReport) -> dict[str, Any]:
    report = sanitize_report(report)
    active_findings = [finding for finding in report.findings if not finding.suppressed]
    semantic_gaps = _semantic_gaps(report)
    metadata_by_id = {metadata.id: metadata for metadata in check_catalog(plugins_enabled=False)}
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
                        "rules": [
                            *_rules(active_findings, metadata_by_id),
                            *_semantic_gap_rules(semantic_gaps),
                        ],
                    }
                },
                "results": [
                    *[_result(finding) for finding in active_findings],
                    *[_semantic_gap_result(gap) for gap in semantic_gaps],
                ],
                "properties": {
                    "release_decision": (
                        report.release_decision.decision
                        if report.release_decision is not None
                        else None
                    ),
                    "static_analysis_only": True,
                    "runtime_behavior_verified": False,
                    "static_verdict_disclaimer": STATIC_VERDICT_DISCLAIMER,
                },
            }
        ],
    }


def _semantic_gaps(report: ReadinessReport) -> list[EvidenceGap]:
    if report.release_decision is None:
        return []
    semantic_kinds = set(get_args(SemanticIssueKind))
    return [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind in semantic_kinds
    ]


def _semantic_gap_rule_id(kind: str) -> str:
    return f"{SEMANTIC_GAP_RULE_PREFIX}{kind.replace('_', '-').upper()}"


def _semantic_gap_rules(gaps: list[EvidenceGap]) -> list[dict[str, Any]]:
    kinds = sorted({gap.kind for gap in gaps})
    return [
        {
            "id": _semantic_gap_rule_id(kind),
            "name": _semantic_gap_rule_id(kind),
            "shortDescription": {
                "text": f"Static semantic evidence gap: {kind.replace('_', ' ')}."
            },
            "fullDescription": {
                "text": (
                    "This action cannot contribute to an evidence-backed pass until "
                    "its static effect and authority evidence is complete and "
                    "conflict-free."
                )
            },
            "defaultConfiguration": {"level": "error"},
            "properties": {
                "category": "semantic_evidence",
                "tags": ["insufficient_evidence", "semantic_evidence"],
            },
        }
        for kind in kinds
    ]


def _semantic_gap_result(gap: EvidenceGap) -> dict[str, Any]:
    return {
        "ruleId": _semantic_gap_rule_id(gap.kind),
        "level": "error",
        "message": {"text": f"{gap.subject}: {gap.why}"},
        "properties": {
            "category": "semantic_evidence",
            "evidence_gap_kind": gap.kind,
            "subject": gap.subject,
            "source_type": gap.source_type,
            "source_ref": gap.source_ref,
            "next_action": gap.next_action.model_dump(mode="json"),
            "runtime_behavior_verified": False,
        },
    }


def _rules(
    findings: list[Finding],
    metadata_by_id: dict[str, CheckMetadata],
) -> list[dict[str, Any]]:
    by_check: dict[str, Finding] = {}
    for finding in findings:
        rule_id = _rule_id(finding)
        existing = by_check.get(rule_id)
        if existing is None or (finding.blocks_release and not existing.blocks_release):
            by_check[rule_id] = finding
    rules = []
    for rule_id, finding in sorted(by_check.items()):
        metadata = metadata_by_id.get(finding.check_id)
        description = metadata.description if metadata else finding.check_id
        full_description = (
            metadata.rationale or metadata.description if metadata else finding.recommendation
        )
        severity = metadata.default_severity if metadata else finding.severity
        tags = _sarif_tags(finding, category=metadata.category if metadata else None)
        rule: dict[str, Any] = {
            "id": rule_id,
            "name": rule_id,
            "shortDescription": {"text": description},
            "fullDescription": {"text": full_description},
            "defaultConfiguration": {"level": _level(severity)},
            "properties": {
                "check_id": finding.check_id,
                "category": metadata.category if metadata else finding.category,
                "severity": severity,
                "tags": tags,
            },
        }
        if metadata and metadata.docs_url:
            rule["helpUri"] = metadata.docs_url
        if metadata and metadata.recommendation:
            rule["help"] = {"text": metadata.recommendation}
        rules.append(rule)
    return rules


def _result(finding: Finding) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": _rule_id(finding),
        "level": _level(finding.severity),
        "message": {"text": finding.title},
        "properties": {
            "check_id": finding.check_id,
            "severity": finding.severity,
            "category": finding.category,
            "recommendation": finding.recommendation,
            "confidence": finding.confidence,
            "provenance_kind": finding.provenance_kind,
            "evidence": _summarize_evidence(finding.evidence),
            "tool_name": finding.tool_name,
            "tags": _sarif_tags(finding),
            "capability_refs": list(finding.capability_refs),
            "capability_trace_refs": list(finding.capability_trace_refs),
        },
    }
    if finding.fingerprint:
        result["fingerprints"] = {"agentsShipgateFingerprint": finding.fingerprint}
    # v0.19 reviewer-grade provenance: SARIF results carry up to two
    # physical locations — the tool source (Finding.source) and the
    # manifest evidence pointer (Finding.policy_evidence_source). SARIF
    # 2.1.0 ``locations`` accepts a list, so reviewers opening the
    # SARIF in an IDE see both lines as separate jump targets.
    # Dedupe by physicalLocation so agent-level findings (where the
    # primary source IS the manifest pointer) don't emit two identical
    # locations — code-scanning UIs render them as visual duplicates.
    locations: list[dict[str, Any]] = []
    seen_physical: list[dict[str, Any]] = []
    for candidate in (_location(finding), _policy_evidence_location(finding)):
        if candidate is None:
            continue
        physical = candidate.get("physicalLocation")
        if physical in seen_physical:
            continue
        seen_physical.append(physical)
        locations.append(candidate)
    if locations:
        result["locations"] = locations
    return result


def _rule_id(finding: Finding) -> str:
    """Use a policy rule id for SARIF annotations when one is available."""
    evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
    for key in ("policy_rule_id", "rule_id", "policy_id"):
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return finding.check_id


def _level(severity: str) -> str:
    if severity in {"critical", "high"}:
        return "error"
    if severity == "medium":
        return "warning"
    return "note"


def _sarif_tags(finding: Finding, *, category: str | None = None) -> list[str]:
    tags = [category or finding.category]
    if finding.blocks_release:
        tags.append("release_blocker")
    return sorted({tag for tag in tags if tag})


def _location(finding: Finding) -> dict[str, Any] | None:
    """Render the primary ``Finding.source`` as a SARIF ``location``."""
    return _source_to_location(
        finding.source,
        finding.tool_name or finding.agent_id or finding.check_id,
    )


def _policy_evidence_location(finding: Finding) -> dict[str, Any] | None:
    """Render the v0.19 secondary ``Finding.policy_evidence_source`` as
    a SARIF ``location`` so the dual-source provenance round-trips
    into a second ``locations[]`` entry for the same result."""
    return _source_to_location(
        finding.policy_evidence_source,
        finding.tool_name or finding.agent_id or finding.check_id,
    )


def _source_to_location(source: Any, logical_name: str | None) -> dict[str, Any] | None:
    """Shared rendering core for primary and secondary source pointers.

    Both ``_location`` and ``_policy_evidence_location`` defer here so
    the structured / legacy fallback logic and pointer-emission rules
    stay in one place.
    """
    if not source:
        return None
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
        "logicalLocations": [{"name": logical_name or "<unknown>"}],
    }
    properties = {
        "shipgateSourceSelector": _source_selector(source, artifact_uri),
        "shipgateSourceType": source.type,
    }
    # Empty string is a valid RFC 6901 root-document pointer (singleton
    # YAML object case), so check ``is not None`` rather than truthiness.
    if source.pointer is not None:
        properties["shipgatePointer"] = source.pointer
    if source.ref:
        properties["shipgateSourceRef"] = source.ref
    location["properties"] = properties
    return location


def _split_location(value: str) -> tuple[str, int | None]:
    if "#" in value:
        value = value.split("#", 1)[0]
    path, _, maybe_line = value.rpartition(":")
    if path and maybe_line.isdigit():
        return path, int(maybe_line)
    return value, None


def _source_selector(source: Any, artifact_uri: str) -> str:
    if source.pointer is not None:
        return f"{artifact_uri}#{source.pointer}"
    if source.location:
        return source.location
    if source.ref:
        return source.ref
    return artifact_uri


def _summarize_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _summarize_evidence(item) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) <= MAX_EVIDENCE_ITEMS:
            return [_summarize_evidence(item) for item in value]
        return {
            "count": len(value),
            "sample": [_summarize_evidence(item) for item in value[:MAX_EVIDENCE_ITEMS]],
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
