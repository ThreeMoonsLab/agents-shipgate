"""Build the packet-only Evidence Matrix from public report JSON.

The matrix is intentionally a compact reviewer aid, not a second gate.
It reads only the same sanitized ``report.json`` payload that users and
CI consumers see, and it copies blocker/review-item references from
``release_decision`` instead of re-deriving release semantics.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agents_shipgate.schemas.packet import (
    EvidenceMatrixConfidence,
    EvidenceMatrixDomain,
    EvidenceMatrixRow,
    EvidenceMatrixSection,
    SectionStatus,
)
from agents_shipgate.schemas.report import ReleaseDecisionItem

_LOGGER = logging.getLogger(__name__)

_MATRIX_NOTE = (
    "Evidence Matrix Light is derived from public report.json only. "
    "Release decisions, CI exit behavior, and baseline semantics remain "
    "owned by release_decision. Domain rows intentionally overlap; a "
    "single finding can appear in multiple rows when it is relevant to "
    "each review lens."
)
_UNAVAILABLE_NOTE = (
    "Evidence matrix unavailable: this packet was loaded from an older "
    "packet schema without public report.json context."
)


@dataclass(frozen=True)
class _DomainSpec:
    domain: EvidenceMatrixDomain
    default_empty_status: SectionStatus
    sources: Callable[[dict[str, Any], list[dict[str, Any]]], list[str]]


_DOMAIN_ORDER: tuple[EvidenceMatrixDomain, ...] = (
    "Inventory",
    "Schema",
    "Auth",
    "Approval",
    "Confirmation",
    "Idempotency",
    "Side effects",
    "Memory isolation",
    "Human-in-the-loop evidence",
    "Prompt/scope alignment",
    "Retry/timeout",
    "Baseline debt",
    "Action-surface policy",
)

# Domain sets intentionally overlap: a single finding can populate
# multiple matrix rows when it is relevant to more than one review lens.
_INVENTORY_CHECKS = {
    "SHIP-INVENTORY-NOT-ENUMERABLE",
    "SHIP-INVENTORY-WILDCARD-TOOLS",
    "SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE",
    "SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE",
    "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE",
    "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
    "SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
    "SHIP-CODEX-PLUGIN-MCP-SERVER-NOT-ENUMERABLE",
    "SHIP-CODEX-PLUGIN-APP-SURFACE-NOT-ENUMERABLE",
    "SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
    "SHIP-N8N-MCP-CLIENT-TOOLSET-UNFILTERED",
}
_SCHEMA_CHECKS = {
    "SHIP-SCHEMA-BROAD-FREE-TEXT",
    "SHIP-SCHEMA-MISSING-BOUNDS",
    "SHIP-SCHEMA-FREEFORM-OUTPUT",
    "SHIP-API-FUNCTION-SCHEMA-STRICTNESS",
    "SHIP-API-STRUCTURED-OUTPUT-READINESS",
    "SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING",
    "SHIP-ADK-FUNCTION-TOOL-METADATA-MISSING",
    "SHIP-LANGCHAIN-FUNCTION-TOOL-METADATA-MISSING",
    "SHIP-CREWAI-FUNCTION-TOOL-METADATA-MISSING",
    "SHIP-N8N-AI-TOOL-METADATA-MISSING",
    "SHIP-CODEX-PLUGIN-SKILL-METADATA-MISSING",
}
_AUTH_CHECKS = {
    "SHIP-AUTH-MISSING-SCOPE",
    "SHIP-AUTH-MANIFEST-BROAD-SCOPE",
    "SHIP-AUTH-TOOL-BROAD-SCOPE",
    "SHIP-AUTH-SCOPE-COVERAGE-MISSING",
    "SHIP-MANIFEST-UNUSED-SCOPE",
    "SHIP-N8N-CREDENTIAL-EVIDENCE-MISSING",
}
_APPROVAL_CHECKS = {
    "SHIP-POLICY-APPROVAL-MISSING",
    "SHIP-API-TRACE-APPROVAL-MISSING",
    "SHIP-EVIDENCE-APPROVAL-TRACE-MISSING",
    "SHIP-EVIDENCE-OVERRIDE-REASON-MISSING",
    "SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING",
    "SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING",
    "SHIP-ACTION-APPROVAL-REMOVED",
    "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
    "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
    "SHIP-ACTION-CONTROL-DOWNGRADE",
}
_CONFIRMATION_CHECKS = {
    "SHIP-POLICY-CONFIRMATION-MISSING",
    "SHIP-API-TRACE-CONFIRMATION-MISSING",
}
_IDEMPOTENCY_CHECKS = {
    "SHIP-SIDEFX-IDEMPOTENCY-MISSING",
    "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY",
    "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
    "SHIP-ACTION-SAFEGUARD-REMOVED",
}
_SIDE_EFFECT_CHECKS = {
    "SHIP-SIDEFX-IDEMPOTENCY-MISSING",
    "SHIP-POLICY-APPROVAL-MISSING",
    "SHIP-POLICY-CONFIRMATION-MISSING",
    "SHIP-SCHEMA-BROAD-FREE-TEXT",
    "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
    "SHIP-ACTION-EFFECT-ESCALATED",
    "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
    "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
    "SHIP-ACTION-SAFEGUARD-REMOVED",
}
_HITL_CHECKS = {
    "SHIP-API-TRACE-APPROVAL-MISSING",
    "SHIP-API-TRACE-CONFIRMATION-MISSING",
    "SHIP-EVIDENCE-APPROVAL-TRACE-MISSING",
    "SHIP-EVIDENCE-OVERRIDE-REASON-MISSING",
    "SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING",
    "SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING",
}
_PROMPT_SCOPE_CHECKS = {
    "SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE",
    "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT",
    "SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH",
}
_RETRY_TIMEOUT_CHECKS = {
    "SHIP-API-RETRY-POLICY-MISSING",
    "SHIP-API-TIMEOUT-MISSING",
    "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY",
}
_BASELINE_CHECKS = {
    "SHIP-BASELINE-INTEGRITY-MISMATCH",
    "SHIP-BASELINE-ENTRY-EXPIRED",
    "SHIP-BASELINE-ENTRY-STALE",
}


def build_evidence_matrix(report_payload: dict[str, Any] | None) -> EvidenceMatrixSection:
    """Return the compact packet matrix for a public report payload."""

    if not isinstance(report_payload, dict):
        return unavailable_evidence_matrix()

    active_findings = [
        item
        for item in _list_of_dicts(report_payload.get("findings"))
        if item.get("suppressed") is not True
    ]
    blockers = _release_items(report_payload, "blockers")
    review_items = _release_items(report_payload, "review_items")

    rows = [
        _build_row(spec, report_payload, active_findings, blockers, review_items)
        for spec in _domain_specs()
    ]
    return EvidenceMatrixSection(rows=rows, notes=[_MATRIX_NOTE])


def unavailable_evidence_matrix() -> EvidenceMatrixSection:
    return EvidenceMatrixSection(
        rows=[
            EvidenceMatrixRow(
                domain=domain,
                evidence_present="not_declared" if domain == "Memory isolation" else "informational",
                evidence_source=[],
                confidence="unknown",
            )
            for domain in _DOMAIN_ORDER
        ],
        notes=[_UNAVAILABLE_NOTE],
    )


def _build_row(
    spec: _DomainSpec,
    report: dict[str, Any],
    active_findings: list[dict[str, Any]],
    blockers: list[ReleaseDecisionItem],
    review_items: list[ReleaseDecisionItem],
) -> EvidenceMatrixRow:
    domain_findings = [
        finding for finding in active_findings if spec.domain in _domains_for_finding(finding)
    ]
    sources = spec.sources(report, domain_findings)
    missing_controls = [_control_summary(finding) for finding in domain_findings]
    status = _row_status(
        sources=sources,
        missing_controls=missing_controls,
        default_empty_status=spec.default_empty_status,
    )
    confidence = _confidence_for_row(domain_findings, sources)
    return EvidenceMatrixRow(
        domain=spec.domain,
        evidence_present=status,
        evidence_source=sources,
        confidence=confidence,
        missing_controls=missing_controls,
        blocking_findings=[
            item for item in blockers if spec.domain in _domains_for_release_item(item)
        ],
        review_items=[
            item for item in review_items if spec.domain in _domains_for_release_item(item)
        ],
    )


def _domain_specs() -> tuple[_DomainSpec, ...]:
    return (
        _DomainSpec("Inventory", "missing", _inventory_sources),
        _DomainSpec("Schema", "not_declared", _schema_sources),
        _DomainSpec("Auth", "not_declared", _auth_sources),
        _DomainSpec("Approval", "not_declared", _approval_sources),
        _DomainSpec("Confirmation", "not_declared", _confirmation_sources),
        _DomainSpec("Idempotency", "not_declared", _idempotency_sources),
        _DomainSpec("Side effects", "informational", _side_effect_sources),
        _DomainSpec("Memory isolation", "not_declared", _memory_sources),
        _DomainSpec("Human-in-the-loop evidence", "not_declared", _hitl_sources),
        _DomainSpec("Prompt/scope alignment", "not_declared", _prompt_scope_sources),
        _DomainSpec("Retry/timeout", "not_declared", _retry_timeout_sources),
        _DomainSpec("Baseline debt", "informational", _baseline_sources),
        _DomainSpec("Action-surface policy", "not_declared", _action_surface_sources),
    )


def _inventory_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if report.get("tool_inventory"):
        sources.append("tool_inventory")
    if report.get("tool_surface"):
        sources.append("tool_surface")
    if report.get("source_warnings"):
        sources.append("source_warnings")
    return _with_findings_source(sources, findings)


def _schema_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    tools = _nested_list(report, "tool_surface_facts", "tools")
    if any(isinstance(tool.get("hashes"), dict) and tool["hashes"] for tool in tools):
        sources.append("tool_surface_facts.tools[].hashes")
    if report.get("api_surface"):
        sources.append("api_surface")
    return _with_findings_source(sources, findings)


def _auth_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if _nested_list(report, "tool_surface_facts", "scopes"):
        sources.append("tool_surface_facts.scopes")
    if any(item.get("auth_scopes") for item in _list_of_dicts(report.get("tool_inventory"))):
        sources.append("tool_inventory[].auth_scopes")
    return _with_findings_source(sources, findings)


def _approval_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    return _with_findings_source(
        _control_sources(report, "approval_policy"),
        findings,
    )


def _confirmation_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    return _with_findings_source(
        _control_sources(report, "confirmation_policy"),
        findings,
    )


def _idempotency_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources = _control_sources(report, "idempotency_evidence")
    if any(
        isinstance(action.get("safeguards"), dict)
        # Source presence means the idempotency field was declared,
        # including explicit false; gaps still come from findings[].
        and action["safeguards"].get("idempotency") is not None
        for action in _nested_list(report, "action_surface_facts", "actions")
    ):
        sources.append("action_surface_facts.actions[].safeguards.idempotency")
    return _with_findings_source(sources, findings)


def _side_effect_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if any(item.get("risk_tags") for item in _list_of_dicts(report.get("tool_inventory"))):
        sources.append("tool_inventory[].risk_tags")
    if any(
        action.get("effect") and action.get("effect") != "read"
        for action in _nested_list(report, "action_surface_facts", "actions")
    ):
        sources.append("action_surface_facts.actions[].effect")
    return _with_findings_source(sources, findings)


def _memory_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    return []


def _hitl_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if any(
        isinstance(finding.get("evidence"), dict)
        and finding["evidence"].get("source_provenance")
        for finding in findings
    ):
        sources.append("findings[].evidence.source_provenance")
    return _with_findings_source(sources, findings)


def _prompt_scope_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if report.get("declared_intentions"):
        sources.append("declared_intentions")
    if report.get("misalignments"):
        sources.append("misalignments")
    if report.get("capability_facts"):
        sources.append("capability_facts")
    return _with_findings_source(sources, findings)


def _retry_timeout_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if report.get("api_surface"):
        sources.append("api_surface")
    return _with_findings_source(sources, findings)


def _baseline_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if report.get("baseline") is not None:
        sources.append("baseline")
    baseline_delta = _nested_dict(report, "release_decision", "baseline_delta")
    if (
        baseline_delta.get("enabled") is True
        or any(baseline_delta.get(key) for key in ("matched_count", "new_count", "resolved_count"))
    ):
        sources.append("release_decision.baseline_delta")
    return _with_findings_source(sources, findings)


def _action_surface_sources(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    if _nested_list(report, "action_surface_facts", "actions"):
        sources.append("action_surface_facts.actions")
    diff = _nested_dict(report, "action_surface_diff")
    if diff and diff.get("enabled") is True:
        sources.append("action_surface_diff")
    if any(finding.get("blocks_release") is True for finding in findings):
        sources.append("findings[].blocks_release")
    return _with_findings_source(sources, findings)


def _control_sources(report: dict[str, Any], kind: str) -> list[str]:
    controls = _nested_list(report, "tool_surface_facts", "controls")
    if any(control.get("kind") == kind for control in controls):
        return [f"tool_surface_facts.controls[kind={kind}]"]
    return []


def _with_findings_source(
    sources: list[str],
    findings: list[dict[str, Any]],
) -> list[str]:
    if findings:
        sources.append("findings[]")
    return _dedupe(sources)


def _row_status(
    *,
    sources: list[str],
    missing_controls: list[str],
    default_empty_status: SectionStatus,
) -> SectionStatus:
    if missing_controls and sources:
        return "partial"
    if missing_controls:
        return "missing"
    if sources:
        return "covered"
    return default_empty_status


def _confidence_for_row(
    findings: list[dict[str, Any]],
    sources: list[str],
) -> EvidenceMatrixConfidence:
    values = {
        confidence
        for finding in findings
        if (confidence := finding.get("confidence")) in {"high", "medium", "low"}
    }
    if len(values) > 1:
        return "mixed"
    if len(values) == 1:
        return values.pop()  # type: ignore[return-value]
    return "medium" if sources else "unknown"


def _control_summary(finding: dict[str, Any]) -> str:
    check_id = str(finding.get("check_id") or "UNKNOWN")
    title = str(finding.get("title") or "Missing control")
    tool = finding.get("tool_name")
    if isinstance(tool, str) and tool:
        return f"{check_id} on {tool}: {title}"
    return f"{check_id}: {title}"


def _release_items(
    report: dict[str, Any],
    key: str,
) -> list[ReleaseDecisionItem]:
    decision = _nested_dict(report, "release_decision")
    items = _list_of_dicts(decision.get(key))
    out: list[ReleaseDecisionItem] = []
    for item in items:
        try:
            out.append(ReleaseDecisionItem.model_validate(item))
        except ValueError as exc:
            _LOGGER.debug("Skipping malformed release_decision.%s item: %s", key, exc)
            continue
    return out


def _domains_for_release_item(item: ReleaseDecisionItem) -> set[EvidenceMatrixDomain]:
    return _domains_for_check_id(
        check_id=item.check_id,
        baseline_status=item.baseline_status,
        blocks_release=item.blocks_release,
    )


def _domains_for_finding(finding: dict[str, Any]) -> set[EvidenceMatrixDomain]:
    check_id = str(finding.get("check_id") or "")
    category = str(finding.get("category") or "")
    baseline_status = finding.get("baseline_status")
    blocks_release = finding.get("blocks_release") is True
    return _domains_for_check_id(
        check_id=check_id,
        category=category,
        baseline_status=baseline_status if isinstance(baseline_status, str) else None,
        blocks_release=blocks_release,
    )


def _domains_for_check_id(
    *,
    check_id: str,
    category: str = "",
    baseline_status: str | None = None,
    blocks_release: bool = False,
) -> set[EvidenceMatrixDomain]:
    domains: set[EvidenceMatrixDomain] = set()

    if check_id in _INVENTORY_CHECKS or category == "inventory":
        domains.add("Inventory")
    if check_id in _SCHEMA_CHECKS or category == "schema":
        domains.add("Schema")
    if check_id in _AUTH_CHECKS or category == "auth":
        domains.add("Auth")
    if check_id in _APPROVAL_CHECKS:
        domains.add("Approval")
    if check_id in _CONFIRMATION_CHECKS:
        domains.add("Confirmation")
    if check_id in _IDEMPOTENCY_CHECKS or category == "side_effects":
        domains.add("Idempotency")
    if check_id in _SIDE_EFFECT_CHECKS or category == "side_effects":
        domains.add("Side effects")
    if check_id in _HITL_CHECKS or category == "evidence":
        domains.add("Human-in-the-loop evidence")
    if check_id in _PROMPT_SCOPE_CHECKS or category == "scope":
        domains.add("Prompt/scope alignment")
    if check_id in _RETRY_TIMEOUT_CHECKS:
        domains.add("Retry/timeout")
    if check_id in _BASELINE_CHECKS or category == "baseline":
        domains.add("Baseline debt")
    if (
        check_id.startswith("SHIP-ACTION-")
        or category == "action_surface"
        or blocks_release
    ):
        domains.add("Action-surface policy")
    if baseline_status == "matched":
        domains.add("Baseline debt")
    return domains


def _nested_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _nested_list(data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return []
        current = current.get(key)
    return _list_of_dicts(current)


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = ["build_evidence_matrix", "unavailable_evidence_matrix"]
