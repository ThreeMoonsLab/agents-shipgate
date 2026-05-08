from __future__ import annotations

from typing import Any

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import (
    Finding,
    HitlProvenanceType,
    HitlSourceProvenance,
    Tool,
    sorted_hitl_source_provenance,
)
from agents_shipgate.core.risk_hints import is_high_risk_tool, risk_tags

CANONICAL_LIMITED_AUTO_APPROVAL_FLAGS = (
    "approval_trace_required",
    "override_reason_required",
    "high_risk_auto_approval_exclusion_required",
)


def run(context: ScanContext) -> list[Finding]:
    validation = context.manifest.validation
    if validation is None:
        return []

    findings: list[Finding] = []
    required = validation.required_evidence
    if required.approval_trace_required:
        findings.extend(_approval_trace_findings(context))
    if required.override_reason_required:
        findings.extend(_override_reason_findings(context))
    if required.high_risk_auto_approval_exclusion_required:
        findings.extend(_high_risk_exclusion_findings(context))
    findings.extend(_promotion_criteria_findings(context))
    return findings


def _approval_trace_findings(context: ScanContext) -> list[Finding]:
    artifacts = context.validation_artifacts
    trace_files = artifacts.approval_trace_files if artifacts else []
    traces = artifacts.approval_traces if artifacts else []
    approved_tools = {
        event["tool_name"]
        for event in traces
        if isinstance(event.get("tool_name"), str) and event.get("approved") is True
    }
    required_tools = _approval_tools(context)
    findings: list[Finding] = []
    tool_lookup = {tool.name: tool for tool in context.tools}
    if not trace_files:
        reason = "file_missing"
    elif not traces:
        reason = "no_trace_events"
    else:
        reason = "approved_trace_missing"
    for tool_name in sorted(required_tools - approved_tools):
        if reason == "approved_trace_missing":
            title = (
                "Loaded local approval trace evidence does not show "
                f"approval for {tool_name}"
            )
        elif reason == "no_trace_events":
            title = (
                "Loaded local approval trace evidence has no recorded "
                f"events for {tool_name}"
            )
        else:
            title = f"No local approval trace evidence found for {tool_name}"
        evidence = {
            "tool_name": tool_name,
            "required": "approval_trace_required",
            "reason": reason,
            "trace_files": trace_files,
            "approved_tools": sorted(approved_tools),
            "source_provenance": _source_provenance(
                context,
                type="approval_trace",
                requirement_location=(
                    f"{_manifest_ref(context)}"
                    "#/validation/required_evidence/approval_trace_required"
                ),
                requirement_detail="approval_trace_required is true",
                evidence_location=(
                    f"{_manifest_ref(context)}#/validation/evidence/approval_traces"
                ),
                evidence_detail="no local approval trace source declared",
            ),
        }
        findings.append(
            _evidence_finding_for_tool(
                context,
                tool_lookup.get(tool_name),
                check_id="SHIP-EVIDENCE-APPROVAL-TRACE-MISSING",
                title=title,
                evidence=evidence,
                recommendation=(
                    f"Add local approval trace evidence for {tool_name}, fix the "
                    "declared source, or change the validation review posture."
                ),
            )
        )
    return findings


def _override_reason_findings(context: ScanContext) -> list[Finding]:
    artifacts = context.validation_artifacts
    log_files = artifacts.override_log_files if artifacts else []
    events = artifacts.override_events if artifacts else []
    if not log_files:
        reason = "file_missing"
    elif not events:
        reason = "no_override_events"
    else:
        missing_reason_events = [
            {
                "tool_name": event.get("tool_name"),
                "action": event.get("action"),
            }
            for event in events
            if not event.get("reason")
        ]
        if not missing_reason_events:
            return []
        return [
            agent_finding(
                check_id="SHIP-EVIDENCE-OVERRIDE-REASON-MISSING",
                title=(
                    "Loaded local override evidence has events without "
                    "recorded reasons"
                ),
                severity="high",
                category="evidence",
                evidence={
                    "required": "override_reason_required",
                    "reason": "reason_missing",
                    "override_log_files": log_files,
                    "events_missing_reason": missing_reason_events,
                    "source_provenance": _source_provenance(
                        context,
                        type="override_log",
                        requirement_location=(
                            f"{_manifest_ref(context)}"
                            "#/validation/required_evidence/override_reason_required"
                        ),
                        requirement_detail="override_reason_required is true",
                        evidence_location=(
                            f"{_manifest_ref(context)}"
                            "#/validation/evidence/override_logs"
                        ),
                        evidence_detail="no local override log source declared",
                    ),
                },
                confidence="high",
                recommendation=(
                    "Record a non-empty reason for each HITL override, bypass, "
                    "or auto-approval event."
                ),
                context=context,
            )
        ]

    title = (
        "Loaded local override evidence has no recorded events"
        if reason == "no_override_events"
        else "No local override-reason evidence found"
    )
    return [
        agent_finding(
            check_id="SHIP-EVIDENCE-OVERRIDE-REASON-MISSING",
            title=title,
            severity="high",
            category="evidence",
            evidence={
                "required": "override_reason_required",
                "reason": reason,
                "override_log_files": log_files,
                "events_missing_reason": [],
                "source_provenance": _source_provenance(
                    context,
                    type="override_log",
                    requirement_location=(
                        f"{_manifest_ref(context)}"
                        "#/validation/required_evidence/override_reason_required"
                    ),
                    requirement_detail="override_reason_required is true",
                    evidence_location=(
                        f"{_manifest_ref(context)}#/validation/evidence/override_logs"
                    ),
                    evidence_detail="no local override log source declared",
                ),
            },
            confidence="high",
            recommendation=(
                "Add local override log evidence with non-empty reasons for "
                "override, bypass, or auto-approval events."
            ),
            context=context,
        )
    ]


def _high_risk_exclusion_findings(context: ScanContext) -> list[Finding]:
    artifacts = context.validation_artifacts
    exclusion_files = artifacts.high_risk_exclusion_files if artifacts else []
    exclusions = artifacts.high_risk_auto_approval_exclusions if artifacts else []
    excluded_tools = {
        entry["tool"] for entry in exclusions if isinstance(entry.get("tool"), str)
    }
    approval_declared = _approval_tools(context)
    findings: list[Finding] = []
    reason = "file_missing" if not exclusion_files else "tool_missing"
    for tool in sorted(context.tools, key=lambda item: item.name):
        if not is_high_risk_tool(tool):
            continue
        if tool.name not in approval_declared:
            continue
        if tool.name in excluded_tools:
            continue
        title = (
            "Loaded local high-risk auto-approval exclusion evidence "
            f"does not list {tool.name}"
            if reason == "tool_missing"
            else (
                "No local high-risk auto-approval exclusion evidence "
                f"found for {tool.name}"
            )
        )
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING",
                title=title,
                severity="high",
                category="evidence",
                evidence={
                    "required": "high_risk_auto_approval_exclusion_required",
                    "reason": reason,
                    "risk_tags": risk_tags(tool, min_confidence="medium"),
                    "exclusion_files": exclusion_files,
                    "excluded_tools": sorted(excluded_tools),
                    "source_provenance": _source_provenance(
                        context,
                        type="high_risk_exclusion",
                        requirement_location=(
                            f"{_manifest_ref(context)}"
                            "#/validation/required_evidence/"
                            "high_risk_auto_approval_exclusion_required"
                        ),
                        requirement_detail=(
                            "high_risk_auto_approval_exclusion_required is true"
                        ),
                        evidence_location=(
                            f"{_manifest_ref(context)}"
                            "#/validation/evidence/high_risk_exclusions"
                        ),
                        evidence_detail=(
                            "no local high-risk exclusion source declared"
                        ),
                    ),
                },
                confidence="high",
                recommendation=(
                    f"Document {tool.name} in high_risk_auto_approval_exclusions "
                    "with a reason before targeting limited auto-approval."
                ),
                context=context,
            )
        )
    return findings


def _promotion_criteria_findings(context: ScanContext) -> list[Finding]:
    validation = context.manifest.validation
    if validation is None or validation.target_review_posture != "limited_auto_approval":
        return []
    artifacts = context.validation_artifacts
    criteria_files = artifacts.promotion_criteria_files if artifacts else []
    criteria = artifacts.promotion_criteria if artifacts else []
    if not criteria_files:
        return [
            _promotion_criteria_finding(
                context,
                reason="file_missing",
                criteria_files=criteria_files,
                manifest_flags_missing=_missing_manifest_flags(context),
                criteria_flags_missing=[],
            )
        ]

    manifest_flags_missing = _missing_manifest_flags(context)
    criteria_flags_missing = _missing_criteria_flags(
        criteria,
        validation.target_review_posture,
    )
    if not manifest_flags_missing and not criteria_flags_missing:
        return []
    return [
        _promotion_criteria_finding(
            context,
            reason="flags_missing",
            criteria_files=criteria_files,
            manifest_flags_missing=manifest_flags_missing,
            criteria_flags_missing=criteria_flags_missing,
        )
    ]


def _promotion_criteria_finding(
    context: ScanContext,
    *,
    reason: str,
    criteria_files: list[str],
    manifest_flags_missing: list[str],
    criteria_flags_missing: list[str],
) -> Finding:
    title = (
        "Loaded local HITL promotion criteria evidence is incomplete"
        if reason == "flags_missing"
        else "No local HITL promotion criteria evidence found"
    )
    return agent_finding(
        check_id="SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING",
        title=title,
        severity="high",
        category="evidence",
        evidence={
            "target_review_posture": "limited_auto_approval",
            "reason": reason,
            "criteria_files": criteria_files,
            "manifest_flags_missing": manifest_flags_missing,
            "criteria_flags_missing": criteria_flags_missing,
            "source_provenance": _source_provenance(
                context,
                type="promotion_criteria",
                requirement_location=(
                    f"{_manifest_ref(context)}#/validation/target_review_posture"
                ),
                requirement_detail=(
                    "target_review_posture is limited_auto_approval"
                ),
                evidence_location=(
                    f"{_manifest_ref(context)}#/validation/evidence/promotion_criteria"
                ),
                evidence_detail="no local promotion criteria source declared",
            ),
        },
        confidence="high",
        recommendation=(
            "Add local promotion criteria evidence documenting the limited "
            "auto-approval review posture and required evidence flags."
        ),
        context=context,
    )


def _missing_manifest_flags(context: ScanContext) -> list[str]:
    validation = context.manifest.validation
    if validation is None:
        return list(CANONICAL_LIMITED_AUTO_APPROVAL_FLAGS)
    required = validation.required_evidence
    return [
        flag
        for flag in CANONICAL_LIMITED_AUTO_APPROVAL_FLAGS
        if getattr(required, flag) is not True
    ]


def _missing_criteria_flags(
    criteria_items: list[dict[str, Any]],
    target_review_posture: str,
) -> list[str]:
    for item in criteria_items:
        if item.get("target_review_posture") != target_review_posture:
            continue
        required = item.get("required_evidence")
        if not isinstance(required, dict):
            continue
        missing = [
            flag
            for flag in CANONICAL_LIMITED_AUTO_APPROVAL_FLAGS
            if required.get(flag) is not True
        ]
        if not missing:
            return []
        return missing
    return list(CANONICAL_LIMITED_AUTO_APPROVAL_FLAGS)


def _approval_tools(context: ScanContext) -> set[str]:
    tools = set(context.manifest.policies.approval_tools())
    if context.api_artifacts:
        tools |= context.api_artifacts.approval_tools()
    if context.anthropic_artifacts:
        tools |= context.anthropic_artifacts.approval_tools()
    return tools


def _evidence_finding_for_tool(
    context: ScanContext,
    tool: Tool | None,
    *,
    check_id: str,
    title: str,
    evidence: dict[str, object],
    recommendation: str,
) -> Finding:
    if tool is not None:
        return tool_finding(
            tool=tool,
            check_id=check_id,
            title=title,
            severity="high",
            category="evidence",
            evidence=evidence,
            confidence="high",
            recommendation=recommendation,
            context=context,
        )
    return agent_finding(
        check_id=check_id,
        title=title,
        severity="high",
        category="evidence",
        evidence=evidence,
        confidence="medium",
        recommendation=recommendation,
        context=context,
    )


def _source_provenance(
    context: ScanContext,
    *,
    type: HitlProvenanceType,
    requirement_location: str,
    requirement_detail: str,
    evidence_location: str,
    evidence_detail: str,
) -> list[dict[str, Any]]:
    items: list[HitlSourceProvenance] = [
        HitlSourceProvenance(
            type="manifest_requirement",
            ref=_manifest_ref(context),
            location=requirement_location,
            status="requirement_only",
            detail=requirement_detail,
        )
    ]
    artifacts = context.validation_artifacts
    if artifacts is not None:
        items.extend(item for item in artifacts.source_provenance if item.type == type)
    if not _declared_evidence_sources(context, type):
        items.append(
            HitlSourceProvenance(
                type=type,
                ref=_manifest_ref(context),
                location=evidence_location,
                status="expected_but_absent",
                detail=evidence_detail,
            )
        )
    return [
        item.model_dump(mode="json")
        for item in sorted_hitl_source_provenance(items)
    ]


def _declared_evidence_sources(
    context: ScanContext,
    type: HitlProvenanceType,
) -> bool:
    validation = context.manifest.validation
    if validation is None:
        return False
    evidence = validation.evidence
    return bool(
        {
            "approval_trace": evidence.approval_traces,
            "override_log": evidence.override_logs,
            "high_risk_exclusion": evidence.high_risk_exclusions,
            "promotion_criteria": evidence.promotion_criteria,
            "manifest_requirement": [context.config_path.name],
        }[type]
    )


def _manifest_ref(context: ScanContext) -> str:
    return context.config_path.name
