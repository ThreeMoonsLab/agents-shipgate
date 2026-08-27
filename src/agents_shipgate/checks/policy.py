from __future__ import annotations

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.capability_policy import (
    capability_policy_evidence_for_subject,
    subject_requires_approval_review,
    subject_requires_confirmation_review,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.control_packs import resolve_control_pack


def run(context: ScanContext):
    findings = []
    # #410 §F: which effects oblige approval / confirmation is the manifest's
    # one answer, not a set written out in this file.
    pack = resolve_control_pack(context.manifest)
    subjects_by_tool_id = {
        subject.tool.id: subject for subject in context.capability_policy_subjects
    }
    for tool in context.tools:
        subject = subjects_by_tool_id.get(tool.id)
        if subject is None:
            continue
        tool = subject.tool
        if subject_requires_approval_review(subject, pack=pack):
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-POLICY-APPROVAL-MISSING",
                    title=f"{tool.name} lacks a declared approval policy",
                    severity="critical",
                    category="policy",
                    evidence={
                        "risk_tags": list(subject.legacy_risk_tags),
                        "policy_match": None,
                        "control_pack": pack.id,
                    },
                    confidence="high",
                    recommendation=f"Declare an approval policy for {tool.name} or remove this tool from the release.",
                    context=context,
                    provenance_kind="static_declaration",
                    # Reviewer-grade provenance: the *tool* source already
                    # points at where the high-risk tool is defined; the
                    # *manifest* evidence pointer tells the reviewer
                    # where to declare the missing policy.
                    policy_evidence_pointer="/policies/require_approval_for_tools",
                    capability_refs=[subject.fact.id],
                    capability_policy_evidence=capability_policy_evidence_for_subject(
                        subject,
                        matched_predicates={
                            "missing_approval_policy": True,
                            "effect": [subject.fact.effect.effect],
                        },
                    ),
                )
            )
        if subject_requires_confirmation_review(subject, pack=pack):
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-POLICY-CONFIRMATION-MISSING",
                    title=f"{tool.name} lacks a declared confirmation policy",
                    severity="high",
                    category="policy",
                    evidence={
                        "risk_tags": list(subject.legacy_risk_tags),
                        "policy_match": None,
                        "control_pack": pack.id,
                    },
                    confidence="high",
                    recommendation=f"Declare a user confirmation policy for {tool.name} or remove this action from the release.",
                    context=context,
                    provenance_kind="static_declaration",
                    policy_evidence_pointer="/policies/require_confirmation_for_tools",
                    capability_refs=[subject.fact.id],
                    capability_policy_evidence=capability_policy_evidence_for_subject(
                        subject,
                        matched_predicates={
                            "missing_confirmation_policy": True,
                            "effect": [subject.fact.effect.effect],
                        },
                    ),
                )
            )
    return findings
