from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PR_PROJECTION_SCHEMA_VERSION = "0.1"

_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}
_IMPACT_TO_SEVERITY = {
    "blocks_release": "critical",
    "insufficient_evidence": "high",
    "review_required": "high",
    "informational": "low",
    "none": "info",
}


@dataclass(frozen=True)
class PrReviewItem:
    """One deterministic PR-facing item projected from report/verifier facts."""

    check_id: str
    title: str
    severity: str
    level: str
    message: str
    recommendation: str
    source_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_column: int | None = None
    selector: str | None = None
    finding_id: str | None = None
    fingerprint: str | None = None
    merge_impact: str | None = None
    related_finding_ids: tuple[str, ...] = ()
    capability_subject: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["related_finding_ids"] = list(self.related_finding_ids)
        return payload


def select_pr_items(
    report: dict[str, Any] | None,
    verifier: dict[str, Any] | None = None,
    *,
    limit: int = 50,
) -> list[PrReviewItem]:
    """Return prioritized PR review items from existing gate artifacts.

    Selection is intentionally a projection only: release-decision blockers and
    review items come first, source-backed capability changes can add reviewer
    context, and active critical/high findings are only a fallback for legacy or
    malformed reports that lack a release-decision block.
    """

    report = report if isinstance(report, dict) else {}
    verifier = verifier if isinstance(verifier, dict) else {}
    normalized_limit = max(0, limit)
    findings = _active_findings(report)
    by_id, by_fingerprint = _finding_indexes(findings)
    selected: list[PrReviewItem] = []
    seen = set[str]()

    release_decision = report.get("release_decision") or {}
    for bucket in ("blockers", "review_items"):
        for decision_item in release_decision.get(bucket) or []:
            if not isinstance(decision_item, dict):
                continue
            finding = _matching_finding(decision_item, by_id, by_fingerprint)
            item = _item_from_finding(finding or decision_item, decision_item)
            _append_unique(selected, seen, item)

    # A reviewed exception is the row a reviewer is meant to read: ✓ rows are
    # machine-verified as evidence-consistent, ⚠ rows are the overrides. A
    # count alone is not a review surface (#409, PR #411 review 2).
    for override in _acknowledged_overrides(report):
        _append_unique(selected, seen, _item_from_acknowledged_override(override))

    for change in _top_capability_changes(verifier):
        item = _item_from_capability_change(change)
        if item.source_path:
            _append_unique(selected, seen, item)

    if not selected:
        for finding in sorted(_critical_high_findings(findings), key=_finding_sort_key):
            _append_unique(selected, seen, _item_from_finding(finding))

    return selected[:normalized_limit]


def item_to_action_annotation(item: PrReviewItem) -> dict[str, Any]:
    """Project one PR item onto a GitHub Actions workflow annotation."""

    return {
        "level": item.level,
        "path": item.source_path,
        "start_line": item.source_start_line,
        "end_line": item.source_end_line,
        "start_column": item.source_start_column,
        "selector": item.selector,
        "title": _truncate(f"{item.check_id}: {item.title}", 160),
        "message": _truncate(item.message, 1000),
        "check_id": item.check_id,
        "severity": item.severity,
        "finding_id": item.finding_id,
        "fingerprint": item.fingerprint,
        "merge_impact": item.merge_impact,
        "capability_subject": item.capability_subject,
        "related_finding_ids": list(item.related_finding_ids),
    }


def item_to_check_run_annotation(item: PrReviewItem) -> dict[str, Any]:
    """Project one PR item onto a GitHub Checks API annotation."""

    line = item.source_start_line if isinstance(item.source_start_line, int) else 1
    return {
        "path": item.source_path,
        "start_line": line,
        "end_line": item.source_end_line or line,
        "annotation_level": _check_run_level(item.level),
        "message": _truncate(item.message, 1000),
        "title": _truncate(item.check_id or "agents-shipgate", 255),
    }


def _item_from_finding(
    finding: dict[str, Any],
    decision_item: dict[str, Any] | None = None,
) -> PrReviewItem:
    decision_item = decision_item if isinstance(decision_item, dict) else {}
    merged = {**decision_item, **finding}
    source = _best_source(merged)
    path = str(source.get("path")) if source and source.get("path") else None
    selector = _selector(source, path) if source and path else None
    severity = str(merged.get("severity") or "info")
    recommendation = str(merged.get("recommendation") or "")
    title = str(merged.get("title") or merged.get("check_id") or "Shipgate finding")
    message = recommendation or title
    if selector:
        message = f"{message} Source: {selector}"
    return PrReviewItem(
        check_id=str(merged.get("check_id") or "agents-shipgate"),
        title=title,
        severity=severity,
        level=_action_level(severity),
        message=message,
        recommendation=recommendation,
        source_path=path,
        source_start_line=_int_or_none(source.get("start_line") if source else None),
        source_end_line=_int_or_none(source.get("end_line") if source else None),
        source_start_column=_int_or_none(
            source.get("start_column") if source else None
        ),
        selector=selector,
        finding_id=_str_or_none(merged.get("id")),
        fingerprint=_str_or_none(merged.get("fingerprint")),
        merge_impact=(
            "blocks_release" if bool(merged.get("blocks_release")) else None
        ),
        related_finding_ids=tuple(_string_list(merged.get("related_finding_ids"))),
        capability_subject=_capability_subject(merged),
    )


def _acknowledged_overrides(report: dict[str, Any]) -> list[dict[str, Any]]:
    coverage = (
        ((report.get("release_decision") or {}).get("evidence_coverage") or {}).get(
            "semantic_coverage"
        )
        or {}
    )
    rows = coverage.get("acknowledged_overrides")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _item_from_acknowledged_override(override: dict[str, Any]) -> PrReviewItem:
    """One ⚠ row per reviewed exception, carrying both readings and the reason."""

    subject = str(override.get("subject") or "unknown")
    declared = str(override.get("declared_effect") or "unknown")
    inferred = str(override.get("inferred_effect") or "unknown")
    sources = ", ".join(_string_list(override.get("inferred_sources"))) or "static evidence"
    agrees = _string_list(override.get("corroborating_sources"))
    evidence = str(override.get("evidence") or "")
    reason = str(override.get("reason") or "")
    manifest_path = _str_or_none(override.get("manifest_path"))
    message = (
        f"Acknowledged override: {subject} declares {declared!r}; "
        f"{sources} infers {inferred!r}."
    )
    if agrees:
        message += f" Source evidence agrees with the declaration ({', '.join(agrees)})."
    message += f" Evidence: {evidence} — Reason: {reason}"
    return PrReviewItem(
        check_id="SHIP-ACTION-EFFECT-OVERRIDE-ACKNOWLEDGED",
        title=f"{subject} declares {declared!r} against inferred {inferred!r}",
        severity="medium",
        level=_action_level("medium"),
        message=_truncate(message, 1000),
        recommendation=(
            f"Confirm the recorded evidence and reason, or set effect to {inferred!r}."
        ),
        source_path=manifest_path.split("#")[0] if manifest_path else None,
        selector=manifest_path,
        merge_impact="review_required",
        capability_subject=f"action:{subject}",
    )


def _item_from_capability_change(change: dict[str, Any]) -> PrReviewItem:
    impact = str(change.get("impact") or "informational")
    severity = _IMPACT_TO_SEVERITY.get(impact, "info")
    subject_kind = str(change.get("subject_kind") or "capability")
    subject = str(change.get("subject") or change.get("id") or "unknown")
    change_type = str(change.get("change_type") or "changed")
    title = f"{subject_kind} {change_type}: {subject}"
    rationale = str(change.get("rationale") or title)
    source_path = _str_or_none(change.get("source_path"))
    line = _int_or_none(change.get("source_start_line"))
    return PrReviewItem(
        check_id="SHIP-CAPABILITY-CHANGE",
        title=title,
        severity=severity,
        level=_action_level(severity),
        message=_truncate(rationale, 1000),
        recommendation=rationale,
        source_path=source_path,
        source_start_line=line,
        source_end_line=line,
        selector=source_path,
        finding_id=None,
        fingerprint=None,
        merge_impact=impact,
        related_finding_ids=tuple(_string_list(change.get("related_finding_ids"))),
        capability_subject=f"{subject_kind}:{subject}",
    )


def _active_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        finding
        for finding in report.get("findings") or []
        if isinstance(finding, dict) and not finding.get("suppressed")
    ]


def _finding_indexes(
    findings: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id = {str(finding.get("id")): finding for finding in findings if finding.get("id")}
    by_fingerprint = {
        str(finding.get("fingerprint")): finding
        for finding in findings
        if finding.get("fingerprint")
    }
    return by_id, by_fingerprint


def _matching_finding(
    item: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_fingerprint: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    finding_id = item.get("id")
    if finding_id is not None and str(finding_id) in by_id:
        return by_id[str(finding_id)]
    fingerprint = item.get("fingerprint")
    if fingerprint is not None and str(fingerprint) in by_fingerprint:
        return by_fingerprint[str(fingerprint)]
    return None


def _top_capability_changes(verifier: dict[str, Any]) -> list[dict[str, Any]]:
    review = verifier.get("capability_review") or {}
    return [
        change
        for change in review.get("top_changes") or []
        if isinstance(change, dict)
    ]


def _critical_high_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        finding
        for finding in findings
        if str(finding.get("severity") or "") in {"critical", "high"}
    ]


def _append_unique(
    selected: list[PrReviewItem],
    seen: set[str],
    item: PrReviewItem,
) -> None:
    if any(
        f"finding-id:{finding_id}" in seen
        for finding_id in item.related_finding_ids
    ):
        return
    key = _item_key(item)
    if key in seen:
        return
    selected.append(item)
    seen.add(key)


def _item_key(item: PrReviewItem) -> str:
    if item.finding_id:
        return f"finding-id:{item.finding_id}"
    if item.fingerprint:
        return f"fingerprint:{item.fingerprint}"
    if item.related_finding_ids:
        return "related:" + ",".join(item.related_finding_ids)
    return "|".join(
        [
            item.check_id,
            item.title,
            item.source_path or "",
            str(item.source_start_line or ""),
            item.capability_subject or "",
        ]
    )


def _finding_sort_key(finding: dict[str, Any]) -> tuple[int, str, str]:
    return (
        _SEVERITY_ORDER.get(str(finding.get("severity") or ""), 99),
        str(finding.get("check_id") or ""),
        str(finding.get("title") or ""),
    )


def _best_source(item: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("source", "policy_evidence_source"):
        source = item.get(key)
        if isinstance(source, dict) and source.get("path"):
            return source
    return None


def _selector(source: dict[str, Any], path: str | None) -> str | None:
    if path is None:
        return None
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


def _capability_subject(item: dict[str, Any]) -> str | None:
    refs = item.get("capability_refs")
    if isinstance(refs, list) and refs:
        return ",".join(str(ref) for ref in refs if str(ref))
    tool_name = item.get("tool_name")
    if tool_name:
        return str(tool_name)
    return None


def _action_level(severity: str) -> str:
    if severity in {"critical", "high"}:
        return "error"
    if severity == "medium":
        return "warning"
    return "notice"


def _check_run_level(action_level: str) -> str:
    if action_level == "error":
        return "failure"
    if action_level == "warning":
        return "warning"
    return "notice"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "..."
