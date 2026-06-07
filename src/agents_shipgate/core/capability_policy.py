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
from agents_shipgate.core.risk_hints import (
    CANONICAL_RISK_TAG_MAP,
    is_effectively_read_only,
    risk_tags,
)
from agents_shipgate.schemas.capabilities import CapabilityFactV1
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.policy_pack import (
    PolicyPackCapabilityMatch,
    PolicyPackMatch,
    PolicyPackParameterMatch,
)
from agents_shipgate.schemas.report import CapabilityPolicyEvidence
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
    evidence: dict[str, Any]
    matched_predicates: dict[str, Any]
    capability_policy_evidence: CapabilityPolicyEvidence


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
    actions_by_tool_id = {
        action.tool_id: action for action in action_surface_facts.actions
    }
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
                    fact.controls.approval_required is True
                    or tool.name in approval_tools
                ),
                effective_confirmation_required=(
                    fact.controls.confirmation_required is True
                    or tool.name in confirmation_tools
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
        matched_parameters = matched_parameters_for_subject(
            subject, rule_match.parameters
        )
        if len(matched_parameters) != len(rule_match.parameters):
            return None
        evidence["parameters"] = matched_parameters
        matched_predicates["parameters"] = matched_parameters
    if rule_match.capability is not None:
        capability_match = _match_capability_selector(subject, rule_match.capability)
        if capability_match is None:
            return None
        matched_predicates["capability"] = capability_match

    return CapabilityPolicyMatch(
        subject=subject,
        evidence=evidence,
        matched_predicates=matched_predicates,
        capability_policy_evidence=_capability_policy_evidence(
            subject,
            matched_predicates=matched_predicates,
        ),
    )


def subject_requires_approval_review(subject: CapabilityPolicySubject) -> bool:
    return (
        not _subject_is_effectively_read_only(subject)
        and _subject_has_any_risk_tag(
            subject,
            {
                "financial_action",
                "destructive",
                "infrastructure_change",
                "code_execution",
            },
        )
        and _missing_approval_policy(subject)
    )


def subject_requires_confirmation_review(subject: CapabilityPolicySubject) -> bool:
    return (
        not _subject_is_effectively_read_only(subject)
        and _subject_has_any_risk_tag(
            subject,
            {
                "destructive",
                "external_write",
                "customer_communication",
            },
        )
        and _missing_confirmation_policy(subject)
    )


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
        scope_matches = sorted(
            set(fact.identity.scope).intersection(selector.scopes)
        )
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
        matched_parameters = matched_parameters_for_subject(
            subject, selector.parameters
        )
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
        tag
        for tag in sorted(set(requested))
        if _canonical_risk_tag(tag) in canonical_subject_tags
    ]
    return matched


def _subject_has_any_risk_tag(
    subject: CapabilityPolicySubject,
    requested: Iterable[str],
) -> bool:
    return bool(_matched_risk_tags(subject, requested))


def _subject_is_effectively_read_only(subject: CapabilityPolicySubject) -> bool:
    return is_effectively_read_only(subject.tool)


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
