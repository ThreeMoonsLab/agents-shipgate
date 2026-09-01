from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, cast

from agents_shipgate.core.adopter_text import INTERNAL_POLICY_IDS
from agents_shipgate.schemas.common import Confidence, confidence_rank
from agents_shipgate.schemas.report import (
    EvidenceBasis,
    EvidenceGap,
    EvidenceGapAction,
    FindingSupport,
    PolicyMatchStatus,
    PolicyPredicateEvidence,
)

_CONFIDENCE_BY_RANK: dict[int, Confidence] = {
    confidence_rank("low"): "low",
    confidence_rank("medium"): "medium",
    confidence_rank("high"): "high",
}

_POLICY_EVIDENCE_LABELS = {
    "reviewed_declaration": "a reviewed declaration",
    "protocol_structure": "protocol structure",
    "typed_provider_fact": "typed provider facts",
    "structural_scope": "structural scope evidence",
    "inferred_keyword": "keyword inference",
    "inferred_regex": "regular-expression inference",
}


def _join_evidence_labels(bases: set[EvidenceBasis]) -> str:
    if not bases:
        raise ValueError("at least one evidence basis is required")
    labels = [
        _POLICY_EVIDENCE_LABELS.get(basis, basis.replace("_", " "))
        for basis in sorted(bases)
    ]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return " and ".join(labels)
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def predicate_evidence(
    predicate: str,
    status: PolicyMatchStatus,
    *,
    expected: Any = None,
    observed: Any = None,
    confidence: Confidence = "low",
    claim_ids: Iterable[str | None] = (),
    evidence_bases: Iterable[str] = (),
    policy_eligible: bool = False,
    why: str | None = None,
) -> PolicyPredicateEvidence:
    return PolicyPredicateEvidence(
        predicate=predicate,
        status=status,
        expected=expected,
        observed=observed,
        confidence=confidence,
        claim_ids=sorted({value for value in claim_ids if value}),
        evidence_bases=sorted(set(evidence_bases)),
        policy_eligible=policy_eligible,
        why=why,
    )


def finding_support(
    predicates: Iterable[PolicyPredicateEvidence],
    *,
    requested_confidence: Confidence = "high",
    status: PolicyMatchStatus | None = None,
) -> FindingSupport:
    rows = sorted(
        predicates,
        key=lambda item: (
            item.predicate,
            item.status,
            json.dumps(item.expected, sort_keys=True, default=str),
            json.dumps(item.observed, sort_keys=True, default=str),
        ),
    )
    if not rows:
        rows = [
            predicate_evidence(
                "capability_subject",
                "indeterminate",
                observed=None,
                confidence="low",
                evidence_bases=["unknown"],
                policy_eligible=False,
                why="no predicate evidence was supplied",
            )
        ]
    resolved_status = status or conjunction_status(row.status for row in rows)
    evidence_confidence_rank = min(confidence_rank(row.confidence) for row in rows)
    effective_rank = min(confidence_rank(requested_confidence), evidence_confidence_rank)
    confidence = _CONFIDENCE_BY_RANK[effective_rank]
    eligible = (
        resolved_status == "matched"
        and all(row.status == "matched" and row.policy_eligible for row in rows)
    )
    claim_ids = sorted({claim_id for row in rows for claim_id in row.claim_ids})
    bases = sorted({basis for row in rows for basis in row.evidence_bases})
    payload = {
        "status": resolved_status,
        "confidence": confidence,
        "policy_eligible": eligible,
        "claim_ids": claim_ids,
        "evidence_bases": bases,
        "predicates": [row.model_dump(mode="json") for row in rows],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return FindingSupport(
        **payload,
        blocking_eligible=eligible,
        support_hash=f"sha256:{digest}",
    )


def conjunction_status(statuses: Iterable[PolicyMatchStatus]) -> PolicyMatchStatus:
    values = list(statuses)
    if any(value == "not_matched" for value in values):
        return "not_matched"
    if any(value == "conflicting" for value in values):
        return "conflicting"
    if any(value == "indeterminate" for value in values):
        return "indeterminate"
    return "matched"


def disjunction_status(statuses: Iterable[PolicyMatchStatus]) -> PolicyMatchStatus:
    values = list(statuses)
    if any(value == "matched" for value in values):
        return "matched"
    if any(value == "conflicting" for value in values):
        return "conflicting"
    if any(value == "indeterminate" for value in values):
        return "indeterminate"
    return "not_matched"


def negated_disjunction_status(statuses: Iterable[PolicyMatchStatus]) -> PolicyMatchStatus:
    value = disjunction_status(statuses)
    if value == "matched":
        return "not_matched"
    if value == "not_matched":
        return "matched"
    return value


def policy_evidence_gap(
    *,
    status: PolicyMatchStatus,
    subject: str,
    subject_id: str | None = None,
    policy_id: str,
    source_ref: str | None,
    support: FindingSupport,
    manifest_path: str,
    rerun_command: str = (
        "agents-shipgate verify --workspace . --config shipgate.yaml "
        "--ci-mode advisory --format json"
    ),
) -> EvidenceGap:
    bases = set(support.evidence_bases)
    authoritative = bases & {
        "reviewed_declaration",
        "protocol_structure",
        "typed_provider_fact",
        "structural_scope",
    }
    heuristic = bases & {"inferred_keyword", "inferred_regex"}
    if status == "conflicting":
        kind = "conflicting_policy_evidence"
        action_kind = "resolve_policy_evidence_conflict"
        why = "Policy applicability has conflicting authoritative evidence."
    elif bases and bases <= {"inferred_keyword", "inferred_regex"}:
        kind = "inferred_policy_applicability"
        action_kind = "provide_policy_evidence"
        why = "Policy applicability is supported only by heuristic evidence."
    elif heuristic and authoritative:
        kind = "mixed_policy_evidence"
        action_kind = "review_policy_evidence"
        why = (
            "Policy applicability combines "
            f"{_join_evidence_labels(authoritative)} with "
            f"{_join_evidence_labels(heuristic)}. Review the heuristic match "
            f"against {_join_evidence_labels(authoritative)}; correct whichever "
            "evidence source is wrong or add a reviewed declaration that resolves "
            "the conflict, then rerun verification."
        )
    else:
        kind = "unknown_policy_evidence"
        action_kind = "provide_policy_evidence"
        why = "Policy applicability cannot be established from complete static evidence."
    return EvidenceGap(
        kind=cast(Any, kind),
        subject=subject,
        subject_id=subject_id,
        source_ref=source_ref,
        policy_id=policy_id,
        # Public and organization-defined check ids remain useful, stable gap
        # labels. Engine-owned ids are different: a reader cannot locate or
        # act on ``builtin-effect-control-applicability``, so exposing it in
        # adopter prose only leaks the implementation vocabulary (#420). The
        # id remains exact in structured ``policy_id`` above; this prohibition
        # applies to prose, not machine identity.
        why=why if policy_id in INTERNAL_POLICY_IDS else f"{policy_id}: {why}",
        next_action=EvidenceGapAction(
            kind=cast(Any, action_kind),
            command=rerun_command,
            path=manifest_path,
            why="Heuristic or unknown evidence cannot create a blocking policy finding.",
            expects="Provide reviewed or structural evidence for every indeterminate predicate.",
            accepted_values=[
                "reviewed_declaration",
                "protocol_structure",
                "typed_provider_fact",
                "structural_scope",
            ],
        ),
    )


__all__ = [
    "conjunction_status",
    "disjunction_status",
    "finding_support",
    "negated_disjunction_status",
    "policy_evidence_gap",
    "predicate_evidence",
]
