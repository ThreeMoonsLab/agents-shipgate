from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from agents_shipgate.core.domain import (
    SURFACE_ENUMERATED,
    AuthorityMode,
    AuthoritySemanticAssessment,
    BindingSemanticAssessment,
    EffectSemanticAssessment,
    EvidenceBasis,
    Scope,
    SemanticClaim,
    SemanticIssue,
    Tool,
    ToolIdentityAssessment,
    ToolSemanticAssessment,
)
from agents_shipgate.core.effect_override import (
    EFFECT_EVIDENCE_RANK as _EFFECT_RANK,
)
from agents_shipgate.core.effect_override import (
    EFFECT_EVIDENCE_VALUES as _EFFECT_VALUES,
)
from agents_shipgate.core.effect_override import (
    assess_effect_override,
    render_override_issue,
)
from agents_shipgate.schemas.common import Confidence, ProvenanceKind, confidence_rank
from agents_shipgate.schemas.manifest import ActionDeclarationConfig
from agents_shipgate.schemas.surfaces import ActionEffect

_MCP_SOURCE_TYPES = frozenset(
    {
        "mcp",
        "codex_config_mcp",
        "codex_plugin_mcp_inventory",
        "n8n_mcp_client_tool",
        "conductor_mcp_call",
    }
)
#: Source types whose tool surface is read out of source code rather than out
#: of a published contract, so completeness has to be established rather than
#: assumed. Membership alone is *not* a verdict: an adapter that proves it
#: enumerated the surface says so on the tool (see
#: :data:`agents_shipgate.core.domain.SURFACE_ENUMERATED`), and only a source
#: type that says nothing is treated as incomplete.
_AST_ONLY_SOURCE_TYPES = frozenset(
    {
        "sdk_function",
        "langchain_function",
        "langchain_structured_tool",
        "crewai_function",
        "crewai_class_tool",
        "crewai_prebuilt_tool",
        "google_adk",
        "google_adk_config",
        "google_adk_function",
    }
)
_BOOLEAN_ANNOTATIONS = (
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
)
_PERMISSION_EFFECTS: dict[str, ActionEffect] = {
    "read": "read",
    "write": "write",
    "destructive": "destructive",
    "external": "external_communication",
    "financial": "financial_write",
    "production": "production_operation",
}
_TAG_EFFECTS: dict[str, ActionEffect] = {
    "read_only": "read",
    "write": "write",
    "writes_data": "write",
    "filesystem_write": "write",
    "destructive": "destructive",
    "irreversible": "destructive",
    "external_write": "external_communication",
    "external_communication": "external_communication",
    "customer_communication": "external_communication",
    "external_side_effect": "external_communication",
    "financial_action": "financial_write",
    "financial_write": "financial_write",
    "infrastructure_change": "production_operation",
    "production_operation": "production_operation",
    "production_ops": "production_operation",
    "sensitive_data_access": "privileged_data_access",
    "privileged_data_access": "privileged_data_access",
    "privileged_data": "privileged_data_access",
    "secret_access": "privileged_data_access",
    "code_execution": "code_execution",
    "identity_access": "identity_access",
    "unknown_side_effect": "write",
}


def assess_tool_semantics(
    tool: Tool,
    declaration: ActionDeclarationConfig | None = None,
) -> ToolSemanticAssessment:
    """Resolve one tool's static effect and authority evidence.

    The resolver is deterministic, local-only, and conservative. Parsed input
    is not itself safety evidence: ambiguous protocol defaults and heuristic-
    only effects remain non-pass-eligible.
    """

    effect, conservative_effect = _assess_effect(tool, declaration)
    authority = _assess_authority(tool, declaration)
    identity = tool.identity_assessment or _compat_identity_assessment(tool)
    binding = tool.binding_assessment or _compat_binding_assessment(tool)
    surface_complete = _surface_is_complete(tool)
    extraction_complete = tool.extraction_confidence == "high"
    pass_eligible = (
        identity.pass_eligible
        and binding.pass_eligible
        and surface_complete
        and extraction_complete
        and effect.status in {"declared", "structural"}
        and effect.confidence == "high"
        and authority.status in {"declared", "structural"}
        and authority.mode in {"none", "scoped"}
        and not effect.issues
        and not authority.issues
    )
    return ToolSemanticAssessment(
        conservative_effect=conservative_effect,
        identity=identity,
        binding=binding,
        effect=effect,
        authority=authority,
        pass_eligible=pass_eligible,
    )


def _compat_binding_assessment(tool: Tool) -> BindingSemanticAssessment:
    """Compatibility for focused unit callers outside the scan pipeline.

    Production scans always attach graph-derived binding evidence before this
    resolver runs. Direct semantic unit tests model one already-selected tool.
    """

    claim = SemanticClaim(
        dimension="binding",
        value=f"legacy_direct->{tool.id}",
        confidence="high",
        provenance_kind="static_declaration",
        basis="reviewed_declaration",
        source="compat_direct_binding",
        source_pointer=tool.source_pointer or tool.source_ref,
    )
    return BindingSemanticAssessment(
        status="structural",
        confidence="high",
        root_agent_id="legacy_direct",
        reachable_path=["legacy_direct", tool.id],
        claims=[claim],
        pass_eligible=True,
    )


def attach_semantic_assessments(
    tools: list[Tool],
    declarations: Mapping[str, ActionDeclarationConfig] | None = None,
    *,
    copy_tools: bool = True,
) -> list[Tool]:
    """Attach one declaration-aware assessment keyed strictly by tool ID.

    Risk-hint enrichment already owns a deep-copied tool graph.  This boundary
    only adds an immutable top-level assessment. Direct callers retain the
    non-mutation default; the scan pipeline sets ``copy_tools=False`` because
    it exclusively owns the enriched objects. Name-keyed declaration maps are
    intentionally ignored so same-name providers can never share evidence.
    """

    by_tool = declarations or {}
    assessed: list[Tool] = []
    for original in tools:
        tool = original.model_copy() if copy_tools else original
        declaration = by_tool.get(tool.id)
        tool.semantic_assessment = assess_tool_semantics(tool, declaration)
        assessed.append(tool)
    return assessed


def _compat_identity_assessment(tool: Tool) -> ToolIdentityAssessment:
    """Identity for direct unit callers that bypass the extraction catalog."""

    provider = tool.provider or tool.source_id or tool.source_type
    observation_id = tool.observation_id or f"legacy:{tool.source_type}:{provider}:{tool.id}"
    claim = SemanticClaim(
        dimension="identity",
        value=observation_id,
        confidence="high",
        provenance_kind="static_declaration",
        basis="reviewed_declaration",
        source="compat_tool_identity",
        source_pointer=tool.source_pointer or tool.source_ref,
        evidence={"source_type": tool.source_type, "source_id": tool.source_id},
    )
    return ToolIdentityAssessment(
        tool_id=tool.id,
        status="structural",
        provider=provider,
        primary_observation_id=observation_id,
        observation_ids=[observation_id],
        claims=[claim],
        pass_eligible=True,
    )


def _assess_effect(
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
) -> tuple[EffectSemanticAssessment, ActionEffect]:
    claims: list[SemanticClaim] = []
    issues: list[SemanticIssue] = []
    pointer = tool.source_pointer

    if declaration is not None and declaration.effect is not None:
        claims.append(
            _claim(
                "effect",
                declaration.effect,
                "high",
                "static_declaration",
                "reviewed_declaration",
                "action_surface_declaration",
                f"action_surface.actions[tool={tool.name!r}].effect",
            )
        )
    if declaration is not None:
        for tag in declaration.risk_tags:
            effect = _TAG_EFFECTS.get(tag)
            if effect is None or effect == "read":
                continue
            claims.append(
                _claim(
                    "effect",
                    effect,
                    "high",
                    "static_declaration",
                    "reviewed_declaration",
                    "action_risk_tag_declaration",
                    f"action_surface.actions[tool={tool.name!r}].risk_tags",
                    {"tag": tag},
                )
            )

    method = str(tool.annotations.get("httpMethod") or "").upper()
    method_effect = {
        "GET": "read",
        "HEAD": "read",
        "OPTIONS": "read",
        "POST": "write",
        "PUT": "write",
        "PATCH": "write",
        "DELETE": "destructive",
    }.get(method)
    if method_effect is not None:
        claims.append(
            _claim(
                "effect",
                method_effect,
                "high",
                "static_declaration",
                "protocol_structure",
                "openapi_method",
                pointer,
                {"method": method},
            )
        )

    for name in _BOOLEAN_ANNOTATIONS:
        if name in tool.annotations and type(tool.annotations[name]) is not bool:
            issues.append(
                _issue(
                    "invalid_semantic_annotation",
                    "effect",
                    f"{name} must be an exact boolean",
                    "tool_annotation",
                    pointer,
                )
            )
    if tool.annotations.get("readOnlyHint") is True:
        claims.append(
            _claim(
                "effect",
                "read",
                "high",
                "static_declaration",
                "protocol_structure",
                "mcp_annotation",
                pointer,
                {"readOnlyHint": True},
            )
        )
    if tool.annotations.get("destructiveHint") is True:
        claims.append(
            _claim(
                "effect",
                "destructive",
                "high",
                "static_declaration",
                "protocol_structure",
                "mcp_annotation",
                pointer,
                {"destructiveHint": True},
            )
        )

    permission_values: list[Any] = []
    for permission_key in (
        "shipgate_permission_classes",
        "permission_classes",
        "permission_class",
        "x-agents-shipgate-permissions",
    ):
        raw_permission_classes = tool.annotations.get(permission_key)
        if isinstance(raw_permission_classes, list):
            permission_values.extend(raw_permission_classes)
        elif raw_permission_classes is not None:
            permission_values.append(raw_permission_classes)
    for raw_value in permission_values:
        value = raw_value.strip().lower() if isinstance(raw_value, str) else ""
        if value == "unknown":
            continue
        effect = _PERMISSION_EFFECTS.get(value)
        if effect is None:
            issues.append(
                _issue(
                    "invalid_semantic_annotation",
                    "effect",
                    f"unsupported permission class {raw_value!r}",
                    "permission_class",
                    pointer,
                )
            )
            continue
        claims.append(
            _claim(
                "effect",
                effect,
                "high",
                "static_declaration",
                "protocol_structure",
                "permission_class",
                pointer,
                {"permission_class": value},
            )
        )

    scope_sources = [(raw_scope, "auth_scope") for raw_scope in tool.auth.scopes]
    if declaration is not None:
        scope_sources.extend((raw_scope, "action_scope") for raw_scope in declaration.scopes)
    for raw_scope, scope_source in scope_sources:
        scope = Scope.parse(raw_scope)
        if not scope.is_write():
            continue
        scope_effect: ActionEffect = (
            "destructive" if (scope.verb or "").lower() in {"delete", "destroy"} else "write"
        )
        claims.append(
            _claim(
                "effect",
                scope_effect,
                "high",
                "static_declaration",
                "structural_scope",
                scope_source,
                pointer,
                {"scope": raw_scope},
            )
        )

    direct_sources = {"openapi_method", "mcp_annotation"}
    for hint in tool.risk_hints:
        effect = _TAG_EFFECTS.get(hint.tag)
        if effect is None or hint.source in direct_sources:
            continue
        hint_basis = _validated_hint_basis(tool, hint, declaration)
        if hint_basis == "unknown":
            issues.append(
                _issue(
                    "invalid_evidence_provenance",
                    "effect",
                    f"risk hint {hint.tag!r} has no typed evidence basis",
                    hint.source,
                    pointer,
                )
            )
        claims.append(
            _claim(
                "effect",
                effect,
                hint.confidence,
                hint.provenance_kind,
                hint_basis,
                f"risk_hint:{hint.source}",
                pointer,
                {"tag": hint.tag, "hint_source": hint.source, **hint.evidence},
            )
        )

    authoritative = [
        claim
        for claim in claims
        if claim.policy_eligible
        and claim.source
        in {
            "action_surface_declaration",
            "openapi_method",
            "mcp_annotation",
            "permission_class",
            "auth_scope",
            "action_scope",
        }
        or (claim.policy_eligible and claim.basis == "typed_provider_fact")
    ]
    # A reviewed manual risk tag may refine positive risk once another source has
    # established the action semantics. It must never independently prove a
    # read-only action or close an otherwise missing effect-evidence gap.
    if authoritative:
        authoritative.extend(
            claim
            for claim in claims
            if claim.source in {"risk_hint:manual", "action_risk_tag_declaration"}
            and claim.value != "read"
        )
    declared = [claim for claim in authoritative if claim.source == "action_surface_declaration"]
    structural = [claim for claim in authoritative if claim.source != "action_surface_declaration"]
    inferred = [claim for claim in claims if claim not in authoritative]

    is_mcp = tool.source_type in _MCP_SOURCE_TYPES or tool.annotations.get("mcp_server") is True
    if is_mcp and not declared and not structural:
        claims.append(
            _claim(
                "effect",
                "write",
                "low",
                "static_declaration",
                "protocol_default",
                "mcp_protocol_default",
                pointer,
                {"reason": "missing effect annotations"},
            )
        )

    claims = _sorted_claims(claims)
    all_effects = [_as_effect(claim.value) for claim in claims if claim.value in _EFFECT_VALUES]
    conservative = _strongest_effect(all_effects) if all_effects else "write"

    if declaration is not None and declaration.effect is not None:
        declared_effect = declaration.effect
        high_structural = [claim for claim in structural if claim.confidence == "high"]
        has_read = any(claim.value == "read" for claim in high_structural)
        has_non_read = any(claim.value != "read" for claim in high_structural)
        contradictory = [
            claim
            for claim in [*structural, *inferred]
            if claim.policy_eligible
            and _EFFECT_RANK[_as_effect(claim.value)] > _EFFECT_RANK[declared_effect]
        ]
        if has_read and has_non_read:
            status = "conflicting"
            confidence = "low"
            issues.append(
                _issue(
                    "conflicting_effect_evidence",
                    "effect",
                    "high-confidence read and side-effect evidence conflict",
                    "tool_source",
                    pointer,
                )
            )
        elif contradictory:
            status = "conflicting"
            confidence: Confidence = "low"
            issues.append(
                _issue(
                    "conflicting_effect_evidence",
                    "effect",
                    "declared effect is weaker than high-confidence source evidence",
                    "action_surface_declaration",
                    f"action_surface.actions[tool={tool.name!r}].effect",
                )
            )
        else:
            # The declaration stands as the operative effect: human authority
            # outranks a heuristic, and #357 keeps heuristics out of the
            # driver's seat. What #409 restores is the *other* power — an
            # observation the resolver already recorded may say that the
            # declaration disagrees with it. Only de-escalation is reported;
            # a declaration at or above every observation is silent, so the
            # rule is monotone and conservative answers cost nothing.
            status = "declared"
            confidence = "high"
            override = assess_effect_override(
                claims,
                declared_effect=declared_effect,
                override=declaration.override,
            )
            if override is not None and not override.acknowledged:
                issues.append(
                    _issue(
                        "declaration_below_inferred_evidence",
                        "effect",
                        render_override_issue(override),
                        "action_surface_declaration",
                        f"action_surface.actions[tool={tool.name!r}].effect",
                    )
                )
    elif structural:
        high_structural = [claim for claim in structural if claim.confidence == "high"]
        has_read = any(claim.value == "read" for claim in high_structural)
        has_non_read = any(claim.value != "read" for claim in high_structural)
        inferred_effects = [_as_effect(claim.value) for claim in inferred]
        strongest_structural = _strongest_effect([_as_effect(claim.value) for claim in structural])
        strongest_inferred = _strongest_effect(inferred_effects) if inferred_effects else "read"
        if has_read and has_non_read:
            status = "conflicting"
            confidence = "low"
            issues.append(
                _issue(
                    "conflicting_effect_evidence",
                    "effect",
                    "high-confidence read and side-effect evidence conflict",
                    "tool_source",
                    pointer,
                )
            )
        elif _EFFECT_RANK[strongest_inferred] > _EFFECT_RANK[strongest_structural]:
            status = "inferred"
            confidence = max(
                (claim.confidence for claim in inferred),
                key=confidence_rank,
            )
            issues.append(
                _issue(
                    "inferred_effect_only",
                    "effect",
                    "the conservative effect depends on heuristic evidence",
                    "risk_hint",
                    pointer,
                )
            )
        else:
            status = "structural"
            confidence = "high"
    elif is_mcp:
        status = "protocol_default"
        confidence = "low"
        issues.append(
            _issue(
                "missing_effect_evidence",
                "effect",
                "MCP tool has no explicit or structural effect evidence",
                "mcp_protocol_default",
                pointer,
            )
        )
    elif inferred:
        status = "inferred"
        confidence = max((claim.confidence for claim in inferred), key=confidence_rank)
        issues.append(
            _issue(
                "inferred_effect_only",
                "effect",
                "tool effect is supported only by heuristic evidence",
                "risk_hint",
                pointer,
            )
        )
    else:
        status = "unknown"
        confidence = "low"
        issues.append(
            _issue(
                "missing_effect_evidence",
                "effect",
                "tool has no explicit or structural effect evidence",
                "tool_source",
                pointer,
            )
        )

    if tool.extraction_confidence != "high" or not _surface_is_complete(tool):
        issues.append(
            _issue(
                "incomplete_surface",
                "effect",
                "tool surface enumeration is incomplete or not high confidence",
                "tool_extraction",
                pointer,
            )
        )

    return (
        EffectSemanticAssessment(
            status=cast(Any, status),
            confidence=confidence,
            claims=claims,
            issues=_sorted_issues(issues),
        ),
        conservative,
    )


def _assess_authority(
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
) -> AuthoritySemanticAssessment:
    claims: list[SemanticClaim] = []
    issues: list[SemanticIssue] = []
    pointer = tool.source_pointer
    source_mode, source_status = _source_authority(tool)

    for message in tool.auth.invalid_annotations:
        issues.append(
            _issue(
                "invalid_semantic_annotation",
                "authority",
                message,
                tool.auth.source or tool.source_type,
                pointer,
            )
        )

    for index, alternative in enumerate(tool.auth.alternatives):
        alternative_mode = _authority_alternative_mode(alternative)
        claims.append(
            _claim(
                "authority",
                alternative_mode,
                "high" if alternative_mode != "unknown" else "low",
                "static_declaration",
                "protocol_structure",
                "openapi_security_alternative",
                f"{pointer or ''}/security/{index}",
                {
                    "anonymous": alternative.anonymous,
                    "schemes": [scheme.model_dump(mode="json") for scheme in alternative.schemes],
                },
            )
        )

    if source_mode != "unknown":
        claims.append(
            _claim(
                "authority",
                source_mode,
                "high" if source_status == "structural" else "low",
                "static_declaration",
                "protocol_structure",
                f"{tool.auth.source or tool.source_type}_authority",
                pointer,
                {
                    "auth_type": tool.auth.type,
                    "scopes": sorted(set(tool.auth.scopes)),
                    "alternative_count": len(tool.auth.alternatives),
                },
            )
        )

    authority = declaration.authority if declaration is not None else None
    if authority is not None:
        claims.append(
            _claim(
                "authority",
                authority.mode,
                "high",
                "static_declaration",
                "reviewed_declaration",
                "action_surface_declaration",
                f"action_surface.actions[tool={tool.name!r}].authority",
                {
                    "auth_type": authority.auth_type,
                    "credential_mode": authority.credential_mode,
                    "scopes": sorted(set(declaration.scopes)),
                },
            )
        )
        declared_mode = cast(AuthorityMode, authority.mode)
        if tool.auth.invalid_annotations:
            status = "conflicting"
            mode = "unknown"
        elif source_status == "partial":
            status = "partial"
            mode = "unknown"
            issues.append(
                _issue(
                    "partial_authority_evidence",
                    "authority",
                    (
                        "reviewed authority cannot replace ambiguous or incomplete "
                        "source authority alternatives"
                    ),
                    tool.auth.source or tool.source_type,
                    pointer,
                )
            )
        elif source_status == "structural" and _authority_declaration_conflicts(
            tool,
            declaration,
            declared_mode=declared_mode,
            source_mode=source_mode,
        ):
            status = "conflicting"
            mode: AuthorityMode = "unknown"
            issues.append(
                _issue(
                    "conflicting_authority_evidence",
                    "authority",
                    "declared authority conflicts with source authority evidence",
                    "action_surface_declaration",
                    f"action_surface.actions[tool={tool.name!r}].authority",
                )
            )
        else:
            status = "declared"
            mode = declared_mode
        auth_type = authority.auth_type
        credential_mode = authority.credential_mode
        scopes = sorted(set(declaration.scopes))
    else:
        status = source_status
        mode = source_mode
        auth_type = tool.auth.type
        credential_mode = tool.auth.credential_mode
        scopes = sorted(set(tool.auth.scopes))
        if status == "partial":
            issues.append(
                _issue(
                    "partial_authority_evidence",
                    "authority",
                    "authority alternatives or scope evidence are incomplete",
                    tool.auth.source or tool.source_type,
                    pointer,
                )
            )
        elif status == "unknown":
            issues.append(
                _issue(
                    "missing_authority_evidence",
                    "authority",
                    "tool has no explicit or structural authority evidence",
                    tool.auth.source or tool.source_type,
                    pointer,
                )
            )

    return AuthoritySemanticAssessment(
        status=cast(Any, status),
        mode=mode,
        auth_type=auth_type,
        credential_mode=credential_mode,
        scopes=scopes,
        claims=_sorted_claims(claims),
        issues=_sorted_issues(issues),
    )


def _source_authority(tool: Tool) -> tuple[AuthorityMode, str]:
    auth = tool.auth
    if auth.invalid_annotations:
        return "unknown", "partial"
    alternative_signatures: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for alternative in auth.alternatives:
        alternative_mode = _authority_alternative_mode(alternative)
        if alternative_mode == "none":
            alternative_signatures.add(("none", (), ()))
            continue
        if alternative_mode == "unknown":
            return "unknown", "partial"
        scheme_types = tuple(
            sorted(f"{scheme.name}:{scheme.type or 'unknown'}" for scheme in alternative.schemes)
        )
        scopes = tuple(sorted({scope for scheme in alternative.schemes for scope in scheme.scopes}))
        alternative_signatures.add((alternative_mode, scheme_types, scopes))
    if len(alternative_signatures) > 1:
        return "unknown", "partial"
    if alternative_signatures:
        mode = next(iter(alternative_signatures))[0]
        return cast(AuthorityMode, mode), "structural"

    if auth.mode == "none":
        if auth.type or auth.scopes:
            return "unknown", "partial"
        return "none", "structural"
    if auth.mode == "scoped":
        if not auth.type or not auth.scopes:
            return "unknown", "partial"
        return "scoped", "structural"
    if auth.mode in {"unscoped", "ambient"}:
        return auth.mode, "structural"
    if auth.type and auth.scopes:
        return "scoped", "structural"
    if auth.type and not auth.scopes:
        return "unscoped", "structural"
    if auth.scopes and not auth.type:
        return "unknown", "partial"
    if auth.explicit:
        return "unknown", "partial"
    return "unknown", "unknown"


def _authority_alternative_mode(alternative: Any) -> AuthorityMode:
    if alternative.anonymous or not alternative.schemes:
        return "none"
    if any(scheme.type is None for scheme in alternative.schemes):
        return "unknown"
    if all(scheme.scopes for scheme in alternative.schemes):
        return "scoped"
    return "unscoped"


def _authority_modes_conflict(left: AuthorityMode, right: AuthorityMode) -> bool:
    if left == right or right == "unknown":
        return False
    if left == "scoped" and right == "scoped":
        return False
    return True


def _authority_declaration_conflicts(
    tool: Tool,
    declaration: ActionDeclarationConfig,
    *,
    declared_mode: AuthorityMode,
    source_mode: AuthorityMode,
) -> bool:
    """Reject reviewed declarations that weaken concrete source authority.

    A declaration may resolve missing or partial source metadata, but it may
    not replace high-confidence source scopes or an authentication type with
    weaker/different values. A scope superset is an explicit authority
    broadening and remains visible to the existing broad-scope policies.
    """

    if _authority_modes_conflict(declared_mode, source_mode):
        return True

    authority = declaration.authority
    if authority is None:
        return False

    source_auth_type = (tool.auth.type or "").strip().lower()
    declared_auth_type = (authority.auth_type or "").strip().lower()
    if source_auth_type and declared_auth_type and source_auth_type != declared_auth_type:
        return True

    source_credential_mode = (tool.auth.credential_mode or "").strip().lower()
    declared_credential_mode = (authority.credential_mode or "").strip().lower()
    if (
        source_credential_mode
        and declared_credential_mode
        and source_credential_mode != declared_credential_mode
    ):
        return True

    if source_mode == "scoped":
        source_scopes = set(tool.auth.scopes)
        declared_scopes = set(declaration.scopes)
        if not source_scopes.issubset(declared_scopes):
            return True

    return False


def _claim(
    dimension: str,
    value: str,
    confidence: Confidence,
    provenance_kind: ProvenanceKind,
    basis: EvidenceBasis,
    source: str,
    source_pointer: str | None,
    evidence: dict[str, Any] | None = None,
) -> SemanticClaim:
    return SemanticClaim(
        dimension=cast(Any, dimension),
        value=value,
        confidence=confidence,
        provenance_kind=provenance_kind,
        basis=basis,
        source=source,
        source_pointer=source_pointer,
        evidence=evidence or {},
    )


def _issue(
    kind: str,
    dimension: str,
    message: str,
    source: str | None,
    source_pointer: str | None,
) -> SemanticIssue:
    return SemanticIssue(
        kind=cast(Any, kind),
        dimension=cast(Any, dimension),
        message=message,
        source=source,
        source_pointer=source_pointer,
    )


def _surface_is_complete(tool: Tool) -> bool:
    """Whether this tool's surface is known, not merely reported.

    Until #393 an AST source type was disqualified outright. That made
    ``incomplete_surface`` a constant for every repository built on a supported
    Python framework — true of a toolkit factory and of twelve annotated
    module-level functions alike — and a condition that holds for every input
    distinguishes nothing. The only way out was a reviewed inventory
    transcribing tools the adapter had already extracted correctly, which added
    no fact to the system.

    An AST adapter may now discharge the doubt by measuring it: it enumerates
    the surface, or it names what it could not resolve. Saying nothing still
    reads as incomplete, so adapters that have not been taught to answer keep
    their previous verdict and a newly unresolvable construct fails closed.
    """

    if (
        tool.annotations.get("wildcard_tools") is True
        or tool.annotations.get("mcp_wildcard_tools") is True
        or tool.annotations.get("mcp_unknown_schema") is True
    ):
        return False
    if tool.source_type in _AST_ONLY_SOURCE_TYPES:
        return tool.extraction.get("surface") == SURFACE_ENUMERATED
    return True


def _validated_hint_basis(
    tool: Tool,
    hint: Any,
    declaration: ActionDeclarationConfig | None,
) -> EvidenceBasis:
    """Validate producer-owned evidence basis without guessing from labels."""

    if hint.basis in {"inferred_keyword", "inferred_regex", "protocol_default"}:
        return cast(EvidenceBasis, hint.basis)
    if hint.basis == "reviewed_declaration" and hint.source == "manual":
        return "reviewed_declaration"
    if hint.basis == "structural_scope" and hint.source in {
        "auth_scope",
        "action_scope",
    }:
        raw_scopes = hint.evidence.get("scopes")
        scopes = (
            [value for value in raw_scopes if isinstance(value, str) and value.strip()]
            if isinstance(raw_scopes, list)
            else []
        )
        declared_scopes = (
            set(tool.auth.scopes)
            if hint.source == "auth_scope"
            else set(declaration.scopes if declaration is not None else [])
        )
        if scopes and set(scopes).issubset(declared_scopes):
            return "structural_scope"
    if hint.basis == "typed_provider_fact":
        if (
            hint.source == "anthropic_client_tool_type"
            and tool.annotations.get("anthropicClientTool") is True
            and hint.confidence == "high"
        ):
            return "typed_provider_fact"
        if (
            hint.source == "n8n_static"
            and tool.source_type == "n8n_code_tool"
            and hint.tag == "code_execution"
            and hint.confidence == "high"
        ):
            return "typed_provider_fact"
    if (
        hint.basis == "protocol_structure"
        and hint.source == "n8n_static"
        and tool.source_type.startswith("n8n_")
        and bool(hint.evidence.get("method"))
    ):
        return "protocol_structure"
    return "unknown"


def _as_effect(value: str) -> ActionEffect:
    return cast(ActionEffect, value)


def _strongest_effect(effects: list[ActionEffect]) -> ActionEffect:
    return max(effects, key=lambda value: _EFFECT_RANK[value])


def _sorted_claims(claims: list[SemanticClaim]) -> list[SemanticClaim]:
    return sorted(
        claims,
        key=lambda item: (
            item.dimension,
            item.source,
            item.source_pointer or "",
            item.value,
            item.confidence,
            json.dumps(item.evidence, sort_keys=True, separators=(",", ":"), default=str),
        ),
    )


def _sorted_issues(issues: list[SemanticIssue]) -> list[SemanticIssue]:
    unique = {
        (item.kind, item.dimension, item.message, item.source, item.source_pointer): item
        for item in issues
    }
    return [unique[key] for key in sorted(unique, key=lambda value: tuple(v or "" for v in value))]


__all__ = ["assess_tool_semantics", "attach_semantic_assessments"]
