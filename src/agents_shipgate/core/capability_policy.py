from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    OpenAIApiArtifacts,
)
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.capabilities import build_capability_facts
from agents_shipgate.core.domain import Tool, ToolParameter
from agents_shipgate.core.policy_evidence import (
    conjunction_status,
    disjunction_status,
    finding_support,
    negated_disjunction_status,
    predicate_evidence,
)
from agents_shipgate.core.risk_hints import (
    CANONICAL_RISK_TAG_MAP,
    risk_tags,
)
from agents_shipgate.schemas.capabilities import CapabilityFactV1
from agents_shipgate.schemas.common import Confidence, SourceReference, confidence_rank
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.policy_pack import (
    PolicyPackCapabilityMatch,
    PolicyPackMatch,
    PolicyPackParameterMatch,
)
from agents_shipgate.schemas.report import (
    CapabilityPolicyEvidence,
    FindingSupport,
    PolicyMatchStatus,
    PolicyPredicateEvidence,
)
from agents_shipgate.schemas.surfaces import ActionFact, ActionSurfaceFacts


@dataclass(frozen=True)
class CapabilityPolicySubject:
    """Internal policy-matching view over one durable capability fact.

    ``CapabilityFactV1`` deliberately keeps schemas hashed rather than
    embedding full parameter lists. Policy packs still need parameter
    predicates, so the subject pairs the fact with the existing action/tool
    schema data without changing capability-lock wire shape.
    """

    fact: CapabilityFactV1
    action: ActionFact
    tool: Tool
    parameters: tuple[ToolParameter, ...]
    legacy_risk_tags: tuple[str, ...]
    effective_approval_required: bool
    effective_confirmation_required: bool
    effective_idempotency_known: bool


@dataclass(frozen=True)
class CapabilityPolicyMatch:
    subject: CapabilityPolicySubject
    status: PolicyMatchStatus
    evidence: dict[str, Any]
    matched_predicates: dict[str, Any]
    capability_policy_evidence: CapabilityPolicyEvidence
    support: FindingSupport


def build_capability_policy_subjects(
    manifest: AgentsShipgateManifest,
    *,
    agent_id: str,
    tools: list[Tool],
    action_surface_facts: ActionSurfaceFacts,
    artifact_bag: ArtifactBag,
) -> tuple[list[CapabilityFactV1], list[CapabilityPolicySubject]]:
    facts = build_capability_facts(manifest, agent_id=agent_id, tools=tools)
    facts_by_tool_id = {fact.identity.tool_id: fact for fact in facts}
    actions_by_tool_id = {action.tool_id: action for action in action_surface_facts.actions}
    approval_tools = _approval_tools(manifest, artifact_bag)
    confirmation_tools = _confirmation_tools(manifest, artifact_bag)
    idempotency_tools = _idempotency_tools(manifest, artifact_bag)

    subjects: list[CapabilityPolicySubject] = []
    for tool in tools:
        fact = facts_by_tool_id.get(tool.id)
        action = actions_by_tool_id.get(tool.id)
        if fact is None or action is None:
            continue
        subjects.append(
            CapabilityPolicySubject(
                fact=fact,
                action=action,
                tool=tool,
                parameters=tuple(tool.parameters),
                legacy_risk_tags=tuple(sorted(set(risk_tags(tool, min_confidence="medium")))),
                effective_approval_required=(
                    fact.controls.approval_required is True or tool.name in approval_tools
                ),
                effective_confirmation_required=(
                    fact.controls.confirmation_required is True or tool.name in confirmation_tools
                ),
                effective_idempotency_known=(
                    fact.effect.idempotency_known is True
                    or fact.controls.safeguard_idempotency is True
                    or tool.name in idempotency_tools
                ),
            )
        )
    subjects.sort(key=lambda subject: _subject_sort_key(subject))
    return facts, subjects


def match_policy_pack_subject(
    subject: CapabilityPolicySubject,
    rule_match: PolicyPackMatch,
    *,
    environment_target: str | None,
    base_evidence: dict[str, Any] | None = None,
) -> CapabilityPolicyMatch:
    """Evaluate a policy-pack selector without converting uncertainty to true.

    The legacy matcher remains the exact value comparator. This wrapper first
    assesses whether every predicate has policy-eligible support. A lexical or
    otherwise incomplete possible match is returned as ``indeterminate`` and
    is routed to an evidence gap by the caller, never to a Finding.
    """

    predicate_rows = _assess_match_support(
        subject,
        rule_match,
        environment_target=environment_target,
    )
    support = finding_support(predicate_rows)
    if support.status != "matched":
        return CapabilityPolicyMatch(
            subject=subject,
            status=support.status,
            evidence=dict(base_evidence or {}),
            matched_predicates={},
            capability_policy_evidence=_capability_policy_evidence(
                subject,
                matched_predicates={},
            ),
            support=support,
        )

    matched = _binary_match_policy_pack_subject(
        subject,
        rule_match,
        environment_target=environment_target,
        base_evidence=base_evidence,
    )
    if matched is None:
        rejected_support = finding_support(
            [
                *predicate_rows,
                predicate_evidence(
                    "exact_value_comparison",
                    "not_matched",
                    confidence="high",
                    evidence_bases=["protocol_structure"],
                    policy_eligible=True,
                ),
            ],
            status="not_matched",
        )
        return CapabilityPolicyMatch(
            subject=subject,
            status="not_matched",
            evidence=dict(base_evidence or {}),
            matched_predicates={},
            capability_policy_evidence=_capability_policy_evidence(
                subject,
                matched_predicates={},
            ),
            support=rejected_support,
        )
    return CapabilityPolicyMatch(
        subject=subject,
        status="matched",
        evidence=matched.evidence,
        matched_predicates=matched.matched_predicates,
        capability_policy_evidence=matched.capability_policy_evidence,
        support=support,
    )


def _binary_match_policy_pack_subject(
    subject: CapabilityPolicySubject,
    rule_match: PolicyPackMatch,
    *,
    environment_target: str | None,
    base_evidence: dict[str, Any] | None = None,
) -> CapabilityPolicyMatch | None:
    evidence: dict[str, Any] = dict(base_evidence or {})
    matched_predicates: dict[str, Any] = {}

    if rule_match.risk_tags:
        matched = _matched_risk_tags(subject, rule_match.risk_tags)
        if not matched:
            return None
        evidence["risk_tags"] = matched
        matched_predicates["risk_tags"] = matched
    if rule_match.source_types:
        if subject.fact.evidence.source_type not in rule_match.source_types:
            return None
        evidence["source_type"] = subject.fact.evidence.source_type
        matched_predicates["source_types"] = [subject.fact.evidence.source_type]
    if rule_match.environment_targets:
        if environment_target not in rule_match.environment_targets:
            return None
        evidence["environment_target"] = environment_target
        matched_predicates["environment_targets"] = [environment_target]
    if rule_match.missing_owner is not None:
        missing = _missing_owner(subject)
        if missing is not rule_match.missing_owner:
            return None
        evidence["missing_owner"] = missing
        matched_predicates["missing_owner"] = missing
    if rule_match.missing_auth_scopes is not None:
        missing = _missing_auth_scopes(subject)
        if missing is not rule_match.missing_auth_scopes:
            return None
        evidence["missing_auth_scopes"] = missing
        matched_predicates["missing_auth_scopes"] = missing
    if rule_match.missing_approval_policy is not None:
        missing = _missing_approval_policy(subject)
        if missing is not rule_match.missing_approval_policy:
            return None
        evidence["missing_approval_policy"] = missing
        matched_predicates["missing_approval_policy"] = missing
    if rule_match.missing_confirmation_policy is not None:
        missing = _missing_confirmation_policy(subject)
        if missing is not rule_match.missing_confirmation_policy:
            return None
        evidence["missing_confirmation_policy"] = missing
        matched_predicates["missing_confirmation_policy"] = missing
    if rule_match.missing_idempotency_policy is not None:
        missing = _missing_idempotency_policy(subject)
        if missing is not rule_match.missing_idempotency_policy:
            return None
        evidence["missing_idempotency_policy"] = missing
        matched_predicates["missing_idempotency_policy"] = missing
    if rule_match.parameters:
        matched_parameters = matched_parameters_for_subject(subject, rule_match.parameters)
        if len(matched_parameters) != len(rule_match.parameters):
            return None
        evidence["parameters"] = matched_parameters
        matched_predicates["parameters"] = matched_parameters
    if rule_match.capability is not None:
        capability_match = _match_capability_selector(subject, rule_match.capability)
        if capability_match is None:
            return None
        matched_predicates["capability"] = capability_match

    # v0.2 combinators. Each branch is a full nested match evaluated
    # recursively against the same subject; the flat predicates above stay
    # implicitly ANDed with the combinators. Deterministic: branches are
    # evaluated in declaration order and any_of records the first hit.
    if rule_match.all_of:
        all_branches: list[dict[str, Any]] = []
        for sub_match in rule_match.all_of:
            sub = _binary_match_policy_pack_subject(
                subject, sub_match, environment_target=environment_target
            )
            if sub is None:
                return None
            all_branches.append(sub.matched_predicates)
        evidence["all_of"] = all_branches
        matched_predicates["all_of"] = all_branches
    if rule_match.any_of:
        any_hit: dict[str, Any] | None = None
        for index, sub_match in enumerate(rule_match.any_of):
            sub = _binary_match_policy_pack_subject(
                subject, sub_match, environment_target=environment_target
            )
            if sub is not None:
                any_hit = {"index": index, "matched": sub.matched_predicates}
                break
        if any_hit is None:
            return None
        evidence["any_of"] = any_hit
        matched_predicates["any_of"] = any_hit
    if rule_match.none_of:
        for sub_match in rule_match.none_of:
            if (
                _binary_match_policy_pack_subject(
                    subject, sub_match, environment_target=environment_target
                )
                is not None
            ):
                return None
        matched_predicates["none_of"] = {"branch_count": len(rule_match.none_of)}

    return CapabilityPolicyMatch(
        subject=subject,
        status="matched",
        evidence=evidence,
        matched_predicates=matched_predicates,
        capability_policy_evidence=_capability_policy_evidence(
            subject,
            matched_predicates=matched_predicates,
        ),
        support=finding_support([]),
    )


def _assess_match_support(
    subject: CapabilityPolicySubject,
    rule_match: PolicyPackMatch,
    *,
    environment_target: str | None,
) -> list[PolicyPredicateEvidence]:
    rows: list[PolicyPredicateEvidence] = []

    if rule_match.risk_tags:
        rows.append(_risk_tag_predicate(subject, "risk_tags", rule_match.risk_tags))
    if rule_match.source_types:
        rows.append(
            _exact_predicate(
                "source_types",
                subject.fact.evidence.source_type in rule_match.source_types,
                expected=rule_match.source_types,
                observed=subject.fact.evidence.source_type,
            )
        )
    if rule_match.environment_targets:
        rows.append(
            _exact_predicate(
                "environment_targets",
                environment_target in rule_match.environment_targets,
                expected=rule_match.environment_targets,
                observed=environment_target,
                basis="reviewed_declaration",
            )
        )
    for field, actual in (
        ("missing_owner", _missing_owner(subject)),
        ("missing_approval_policy", _missing_approval_policy(subject)),
        ("missing_confirmation_policy", _missing_confirmation_policy(subject)),
        ("missing_idempotency_policy", _missing_idempotency_policy(subject)),
    ):
        expected = getattr(rule_match, field)
        if expected is not None:
            rows.append(
                _exact_predicate(
                    field,
                    actual is expected,
                    expected=expected,
                    observed=actual,
                    basis="reviewed_declaration",
                )
            )
    if rule_match.missing_auth_scopes is not None:
        rows.append(
            _authority_predicate(
                subject,
                "missing_auth_scopes",
                expected=rule_match.missing_auth_scopes,
                observed=_missing_auth_scopes(subject),
            )
        )
    if rule_match.parameters:
        matched = matched_parameters_for_subject(subject, rule_match.parameters)
        rows.append(
            _exact_predicate(
                "parameters",
                len(matched) == len(rule_match.parameters),
                expected=[item.model_dump(mode="json") for item in rule_match.parameters],
                observed=matched,
            )
        )
    if rule_match.capability is not None:
        rows.extend(_assess_capability_support(subject, rule_match.capability))

    for name, branches, reducer in (
        ("all_of", rule_match.all_of, conjunction_status),
        ("any_of", rule_match.any_of, disjunction_status),
        ("none_of", rule_match.none_of, negated_disjunction_status),
    ):
        if not branches:
            continue
        branch_support = [
            finding_support(
                _assess_match_support(
                    subject,
                    branch,
                    environment_target=environment_target,
                )
            )
            for branch in branches
        ]
        status = reducer(item.status for item in branch_support)
        contributing = _contributing_branches(name, branch_support, status)
        rows.append(
            predicate_evidence(
                name,
                status,
                expected={"branch_count": len(branches)},
                observed=[item.status for item in branch_support],
                confidence=_weakest_confidence(contributing),
                claim_ids=[
                    claim_id for item in contributing for claim_id in item.claim_ids
                ],
                evidence_bases=[
                    basis for item in contributing for basis in item.evidence_bases
                ],
                policy_eligible=(
                    status in {"matched", "not_matched"}
                    and all(_support_is_resolved(item) for item in contributing)
                ),
                why=(
                    None
                    if status in {"matched", "not_matched"}
                    else "at least one boolean-composition branch is not statically decidable"
                ),
            )
        )

    if not rows:
        rows.append(
            predicate_evidence(
                "capability_subject",
                "matched",
                observed=subject.fact.id,
                confidence="high",
                evidence_bases=["protocol_structure"],
                policy_eligible=True,
            )
        )
    return rows


def _assess_capability_support(
    subject: CapabilityPolicySubject,
    selector: PolicyPackCapabilityMatch,
) -> list[PolicyPredicateEvidence]:
    fact = subject.fact
    rows: list[PolicyPredicateEvidence] = []
    for field, actual in (
        ("tool_names", fact.identity.tool_name),
        ("providers", fact.identity.provider),
        ("operations", fact.identity.operation),
        ("source_types", fact.evidence.source_type),
        ("auth_types", fact.authority.auth_type),
        ("credential_modes", fact.authority.credential_mode),
    ):
        expected = getattr(selector, field)
        if expected:
            rows.append(
                _exact_predicate(
                    f"capability.{field}",
                    actual in expected,
                    expected=expected,
                    observed=actual,
                )
            )
    if selector.effects:
        rows.append(_effect_predicate(subject, "capability.effects", selector.effects))
    if selector.risk_tags:
        rows.append(
            _risk_tag_predicate(subject, "capability.risk_tags", selector.risk_tags)
        )
    if selector.scopes:
        rows.append(
            _authority_predicate(
                subject,
                "capability.scopes",
                expected=selector.scopes,
                observed=sorted(set(fact.identity.scope).intersection(selector.scopes)),
                matched=bool(set(fact.identity.scope).intersection(selector.scopes)),
            )
        )
    if selector.broad_scope is not None:
        actual = bool(fact.authority.broad_scopes)
        rows.append(
            _authority_predicate(
                subject,
                "capability.broad_scope",
                expected=selector.broad_scope,
                observed=actual,
                matched=actual is selector.broad_scope,
            )
        )
    facet_effects = {
        "externally_visible": {"external_communication"},
        "handles_sensitive_data": {"privileged_data_access"},
        "financial": {"financial_write"},
        "code_execution": {"code_execution"},
        "high_risk": {
            "destructive",
            "external_communication",
            "financial_write",
            "production_operation",
            "privileged_data_access",
            "code_execution",
            "identity_access",
        },
    }
    for field, effects in facet_effects.items():
        expected = getattr(selector, field)
        if expected is not None:
            rows.append(
                _effect_predicate(
                    subject,
                    f"capability.{field}",
                    effects,
                    expected_boolean=expected,
                )
            )
    for field, actual in (
        ("missing_owner", _missing_owner(subject)),
        ("missing_approval_policy", _missing_approval_policy(subject)),
        ("missing_confirmation_policy", _missing_confirmation_policy(subject)),
        ("missing_idempotency_policy", _missing_idempotency_policy(subject)),
    ):
        expected = getattr(selector, field)
        if expected is not None:
            rows.append(
                _exact_predicate(
                    f"capability.{field}",
                    actual is expected,
                    expected=expected,
                    observed=actual,
                    basis="reviewed_declaration",
                )
            )
    if selector.missing_auth_scopes is not None:
        rows.append(
            _authority_predicate(
                subject,
                "capability.missing_auth_scopes",
                expected=selector.missing_auth_scopes,
                observed=_missing_auth_scopes(subject),
            )
        )
    if selector.parameters:
        matched = matched_parameters_for_subject(subject, selector.parameters)
        rows.append(
            _exact_predicate(
                "capability.parameters",
                len(matched) == len(selector.parameters),
                expected=[item.model_dump(mode="json") for item in selector.parameters],
                observed=matched,
            )
        )
    return rows


def _effect_predicate(
    subject: CapabilityPolicySubject,
    predicate: str,
    requested: Iterable[str],
    *,
    expected_boolean: bool | None = None,
) -> PolicyPredicateEvidence:
    assessment = subject.tool.semantic_assessment
    requested_values = set(requested)
    claims = list(assessment.effect.claims) if assessment is not None else []
    eligible = [
        claim for claim in claims if claim.policy_eligible and claim.value in requested_values
    ]
    possible = [claim for claim in claims if claim.value in requested_values]
    if assessment is None:
        possible_match = subject.fact.effect.effect in requested_values
        return predicate_evidence(
            predicate,
            "indeterminate" if possible_match else "indeterminate",
            expected=expected_boolean if expected_boolean is not None else sorted(requested_values),
            observed=subject.fact.effect.effect,
            confidence="low",
            evidence_bases=["unknown"],
            why="semantic assessment is missing",
        )
    positive = bool(eligible)
    expected_positive = True if expected_boolean is None else expected_boolean
    if positive:
        status: PolicyMatchStatus = "matched" if expected_positive else "not_matched"
        selected = eligible
    elif possible or assessment.effect.status in {
        "inferred",
        "unknown",
        "protocol_default",
        "conflicting",
    }:
        status = "conflicting" if assessment.effect.status == "conflicting" else "indeterminate"
        selected = possible or claims
    else:
        status = "not_matched" if expected_positive else "matched"
        selected = claims
    return predicate_evidence(
        predicate,
        status,
        expected=expected_boolean if expected_boolean is not None else sorted(requested_values),
        observed=sorted({claim.value for claim in selected}),
        confidence=_claim_confidence(selected),
        claim_ids=[claim.claim_id for claim in selected],
        evidence_bases=[claim.basis for claim in selected],
        policy_eligible=(
            status in {"matched", "not_matched"}
            and bool(selected)
            and all(claim.policy_eligible for claim in selected)
        ),
        why=(
            None
            if status in {"matched", "not_matched"}
            else "effect classification is heuristic, unknown, or conflicting"
        ),
    )


def _risk_tag_predicate(
    subject: CapabilityPolicySubject,
    predicate: str,
    requested: Iterable[str],
) -> PolicyPredicateEvidence:
    requested_values = set(requested)
    requested_canonical = {_canonical_risk_tag(tag) for tag in requested_values}
    assessment = subject.tool.semantic_assessment
    claims = list(assessment.effect.claims) if assessment is not None else []

    def claim_tag(claim: Any) -> str:
        raw = claim.evidence.get("tag") if isinstance(claim.evidence, dict) else None
        return _canonical_risk_tag(str(raw or claim.value))

    matching = [claim for claim in claims if claim_tag(claim) in requested_canonical]
    eligible = [claim for claim in matching if claim.policy_eligible]
    if eligible:
        status: PolicyMatchStatus = "matched"
        selected = eligible
    elif matching:
        status = "indeterminate"
        selected = matching
    elif assessment is None or assessment.effect.status in {
        "inferred",
        "unknown",
        "protocol_default",
        "conflicting",
    }:
        status = "indeterminate"
        selected = []
    else:
        status = "not_matched"
        selected = []
    return predicate_evidence(
        predicate,
        status,
        expected=sorted(requested_values),
        observed=sorted({claim_tag(claim) for claim in selected}),
        confidence=_claim_confidence(selected),
        claim_ids=[claim.claim_id for claim in selected],
        evidence_bases=[claim.basis for claim in selected] or ["unknown"],
        policy_eligible=status == "matched" and bool(eligible),
        why=(
            None
            if status in {"matched", "not_matched"}
            else "risk-tag classification is heuristic or semantically incomplete"
        ),
    )


def _authority_predicate(
    subject: CapabilityPolicySubject,
    predicate: str,
    *,
    expected: Any,
    observed: Any,
    matched: bool | None = None,
) -> PolicyPredicateEvidence:
    assessment = subject.tool.semantic_assessment
    authority = assessment.authority if assessment is not None else None
    if authority is None or authority.status in {"partial", "unknown", "conflicting"}:
        return predicate_evidence(
            predicate,
            "conflicting" if authority is not None and authority.status == "conflicting" else "indeterminate",
            expected=expected,
            observed=observed,
            confidence="low",
            claim_ids=(claim.claim_id for claim in authority.claims) if authority else (),
            evidence_bases=(claim.basis for claim in authority.claims) if authority else ["unknown"],
            why="authority evidence is incomplete or conflicting",
        )
    actual_match = (observed is expected) if matched is None else matched
    return predicate_evidence(
        predicate,
        "matched" if actual_match else "not_matched",
        expected=expected,
        observed=observed,
        confidence="high",
        claim_ids=[claim.claim_id for claim in authority.claims],
        evidence_bases=[claim.basis for claim in authority.claims],
        policy_eligible=all(claim.policy_eligible for claim in authority.claims),
    )


def _exact_predicate(
    predicate: str,
    matched: bool,
    *,
    expected: Any,
    observed: Any,
    basis: str = "protocol_structure",
) -> PolicyPredicateEvidence:
    return predicate_evidence(
        predicate,
        "matched" if matched else "not_matched",
        expected=expected,
        observed=observed,
        confidence="high",
        evidence_bases=[basis],
        policy_eligible=True,
    )


def _claim_confidence(claims: Iterable[Any]) -> Confidence:
    values = [claim.confidence for claim in claims]
    return min(values, key=confidence_rank) if values else "low"


def _weakest_confidence(supports: Iterable[FindingSupport]) -> Confidence:
    values = [item.confidence for item in supports]
    return min(values, key=confidence_rank) if values else "high"


def _support_is_resolved(support: FindingSupport) -> bool:
    return support.status in {"matched", "not_matched"} and all(
        row.policy_eligible for row in support.predicates
    )


def _contributing_branches(
    name: str,
    supports: list[FindingSupport],
    status: PolicyMatchStatus,
) -> list[FindingSupport]:
    if name == "any_of" and status == "matched":
        return [next(item for item in supports if item.status == "matched")]
    if name == "all_of" and status == "not_matched":
        return [next(item for item in supports if item.status == "not_matched")]
    if name == "none_of" and status == "not_matched":
        return [next(item for item in supports if item.status == "matched")]
    return supports


def subject_requires_approval_review(subject: CapabilityPolicySubject) -> bool:
    return bool(
        _subject_control_effects(subject)
        & {
            "financial_write",
            "destructive",
            "production_operation",
            "code_execution",
        }
    ) and _missing_approval_policy(subject)


def subject_requires_confirmation_review(subject: CapabilityPolicySubject) -> bool:
    return bool(
        _subject_control_effects(subject) & {"destructive", "external_communication"}
    ) and _missing_confirmation_policy(subject)


def _subject_control_effects(subject: CapabilityPolicySubject) -> set[str]:
    """Project non-heuristic positive effect claims for hard controls."""

    assessment = getattr(subject.tool, "semantic_assessment", None)
    if assessment is None:
        return {subject.fact.effect.effect}
    effects: set[str] = set()
    for claim in assessment.effect.claims:
        if (
            claim.policy_eligible
            and claim.value
            in {
                "read",
                "write",
                "destructive",
                "external_communication",
                "financial_write",
                "production_operation",
                "privileged_data_access",
                "code_execution",
                "identity_access",
            }
        ):
            effects.add(claim.value)
    return effects


def capability_policy_evidence_for_subject(
    subject: CapabilityPolicySubject,
    *,
    matched_predicates: dict[str, Any] | None = None,
) -> CapabilityPolicyEvidence:
    return _capability_policy_evidence(
        subject,
        matched_predicates=matched_predicates or {},
    )


def matched_parameters_for_subject(
    subject: CapabilityPolicySubject,
    predicates: Iterable[PolicyPackParameterMatch],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for predicate in predicates:
        matched = next(
            (
                parameter
                for parameter in subject.parameters
                if _parameter_matches(parameter, predicate)
            ),
            None,
        )
        if matched is None:
            continue
        matches.append(
            {
                "name": matched.name,
                "type": matched.type,
                "required": matched.required,
                "maximum": matched.maximum,
            }
        )
    return matches


def _match_capability_selector(
    subject: CapabilityPolicySubject,
    selector: PolicyPackCapabilityMatch,
) -> dict[str, Any] | None:
    matched: dict[str, Any] = {}
    fact = subject.fact
    if selector.tool_names:
        if fact.identity.tool_name not in selector.tool_names:
            return None
        matched["tool_names"] = [fact.identity.tool_name]
    if selector.providers:
        if fact.identity.provider not in selector.providers:
            return None
        matched["providers"] = [fact.identity.provider]
    if selector.operations:
        if fact.identity.operation not in selector.operations:
            return None
        matched["operations"] = [fact.identity.operation]
    if selector.source_types:
        if fact.evidence.source_type not in selector.source_types:
            return None
        matched["source_types"] = [fact.evidence.source_type]
    if selector.effects:
        if fact.effect.effect not in selector.effects:
            return None
        matched["effects"] = [fact.effect.effect]
    if selector.risk_tags:
        risk_matches = _matched_capability_risk_tags(subject, selector.risk_tags)
        if not risk_matches:
            return None
        matched["risk_tags"] = risk_matches
    if selector.scopes:
        scope_matches = sorted(set(fact.identity.scope).intersection(selector.scopes))
        if not scope_matches:
            return None
        matched["scopes"] = scope_matches
    if selector.broad_scope is not None:
        broad_scope = bool(fact.authority.broad_scopes)
        if broad_scope is not selector.broad_scope:
            return None
        matched["broad_scope"] = broad_scope
    for field in (
        "externally_visible",
        "handles_sensitive_data",
        "financial",
        "code_execution",
        "high_risk",
    ):
        expected = getattr(selector, field)
        if expected is None:
            continue
        actual = getattr(fact.effect, field)
        if actual is not expected:
            return None
        matched[field] = actual
    if selector.auth_types:
        if fact.authority.auth_type not in selector.auth_types:
            return None
        matched["auth_types"] = [fact.authority.auth_type]
    if selector.credential_modes:
        if fact.authority.credential_mode not in selector.credential_modes:
            return None
        matched["credential_modes"] = [fact.authority.credential_mode]
    for field, func in (
        ("missing_owner", _missing_owner),
        ("missing_auth_scopes", _missing_auth_scopes),
        ("missing_approval_policy", _missing_approval_policy),
        ("missing_confirmation_policy", _missing_confirmation_policy),
        ("missing_idempotency_policy", _missing_idempotency_policy),
    ):
        expected = getattr(selector, field)
        if expected is None:
            continue
        actual = func(subject)
        if actual is not expected:
            return None
        matched[field] = actual
    if selector.parameters:
        matched_parameters = matched_parameters_for_subject(subject, selector.parameters)
        if len(matched_parameters) != len(selector.parameters):
            return None
        matched["parameters"] = matched_parameters
    return matched


def _parameter_matches(
    parameter: ToolParameter,
    predicate: PolicyPackParameterMatch,
) -> bool:
    names = set(predicate.names)
    if predicate.name:
        names.add(predicate.name)
    if names and parameter.name not in names:
        return False
    if predicate.types and parameter.type not in predicate.types:
        return False
    if predicate.missing_maximum is not None:
        missing = parameter.maximum is None
        if missing is not predicate.missing_maximum:
            return False
    if predicate.required is not None and parameter.required is not predicate.required:
        return False
    if predicate.maximum_above is not None:
        if parameter.maximum is None or not (float(parameter.maximum) > predicate.maximum_above):
            return False
    if predicate.minimum_below is not None:
        if parameter.minimum is None or not (float(parameter.minimum) < predicate.minimum_below):
            return False
    return True


def _matched_risk_tags(
    subject: CapabilityPolicySubject,
    requested: Iterable[str],
) -> list[str]:
    subject_tags = set(subject.legacy_risk_tags)
    return [tag for tag in sorted(set(requested)) if tag in subject_tags]


def _matched_capability_risk_tags(
    subject: CapabilityPolicySubject,
    requested: Iterable[str],
) -> list[str]:
    canonical_subject_tags = {_canonical_risk_tag(tag) for tag in subject.fact.risk_tags}
    matched = [
        tag for tag in sorted(set(requested)) if _canonical_risk_tag(tag) in canonical_subject_tags
    ]
    return matched


def _missing_owner(subject: CapabilityPolicySubject) -> bool:
    return not bool(subject.fact.controls.evidence_owner)


def _missing_auth_scopes(subject: CapabilityPolicySubject) -> bool:
    return not bool(subject.fact.authority.scopes)


def _missing_approval_policy(subject: CapabilityPolicySubject) -> bool:
    return subject.effective_approval_required is not True


def _missing_confirmation_policy(subject: CapabilityPolicySubject) -> bool:
    return subject.effective_confirmation_required is not True


def _missing_idempotency_policy(subject: CapabilityPolicySubject) -> bool:
    return subject.effective_idempotency_known is not True


def _capability_policy_evidence(
    subject: CapabilityPolicySubject,
    *,
    matched_predicates: dict[str, Any],
) -> CapabilityPolicyEvidence:
    fact = subject.fact
    return CapabilityPolicyEvidence(
        capability_id=fact.id,
        identity=fact.identity.model_dump(mode="json"),
        effect=fact.effect.model_dump(mode="json"),
        authority=fact.authority.model_dump(mode="json"),
        controls={
            **fact.controls.model_dump(mode="json"),
            "effective_approval_required": subject.effective_approval_required,
            "effective_confirmation_required": subject.effective_confirmation_required,
            "effective_idempotency_known": subject.effective_idempotency_known,
        },
        hashes=fact.hashes.model_dump(mode="json"),
        matched_predicates=matched_predicates,
        source=_source_reference(fact),
    )


def _source_reference(fact: CapabilityFactV1) -> SourceReference:
    evidence = fact.evidence
    return SourceReference(
        type=evidence.source_type,
        ref=evidence.source_ref,
        location=evidence.source_location,
        path=evidence.source_path,
        start_line=evidence.source_start_line,
        end_line=evidence.source_end_line,
        start_column=evidence.source_start_column,
        pointer=evidence.source_pointer,
    )


def _approval_tools(
    manifest: AgentsShipgateManifest,
    artifact_bag: ArtifactBag,
) -> set[str]:
    tools = set(manifest.policies.approval_tools())
    api_artifacts = artifact_bag.get("openai_api", OpenAIApiArtifacts)
    if api_artifacts:
        tools |= api_artifacts.approval_tools()
    anthropic_artifacts = artifact_bag.get("anthropic_api", AnthropicArtifacts)
    if anthropic_artifacts:
        tools |= anthropic_artifacts.approval_tools()
    return tools


def _confirmation_tools(
    manifest: AgentsShipgateManifest,
    artifact_bag: ArtifactBag,
) -> set[str]:
    tools = set(manifest.policies.confirmation_tools())
    api_artifacts = artifact_bag.get("openai_api", OpenAIApiArtifacts)
    if api_artifacts:
        tools |= api_artifacts.confirmation_tools()
    anthropic_artifacts = artifact_bag.get("anthropic_api", AnthropicArtifacts)
    if anthropic_artifacts:
        tools |= anthropic_artifacts.confirmation_tools()
    return tools


def _idempotency_tools(
    manifest: AgentsShipgateManifest,
    artifact_bag: ArtifactBag,
) -> set[str]:
    tools = set(manifest.policies.idempotency_tools())
    api_artifacts = artifact_bag.get("openai_api", OpenAIApiArtifacts)
    if api_artifacts:
        tools |= api_artifacts.idempotency_tools()
    anthropic_artifacts = artifact_bag.get("anthropic_api", AnthropicArtifacts)
    if anthropic_artifacts:
        tools |= anthropic_artifacts.idempotency_tools()
    return tools


def _canonical_risk_tag(tag: str) -> str:
    return CANONICAL_RISK_TAG_MAP.get(tag, tag)


def _subject_sort_key(
    subject: CapabilityPolicySubject,
) -> tuple[str, str, str, str, str]:
    fact = subject.fact
    return (
        fact.identity.agent_id,
        fact.identity.provider,
        fact.identity.operation,
        fact.identity.tool_name,
        fact.id,
    )


__all__ = [
    "CapabilityPolicyMatch",
    "CapabilityPolicySubject",
    "build_capability_policy_subjects",
    "capability_policy_evidence_for_subject",
    "match_policy_pack_subject",
    "matched_parameters_for_subject",
    "subject_requires_approval_review",
    "subject_requires_confirmation_review",
]
