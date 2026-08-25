from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from agents_shipgate.core.action_semantics import ACTION_EFFECT_RANK, builtin_obligations
from agents_shipgate.core.domain import (
    DECLARATION_CLAIM_SOURCES,
    DECLARATION_OVERRIDE_SOURCE,
    DECLARED_EFFECT_SOURCE,
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
from agents_shipgate.schemas.common import Confidence, ProvenanceKind, confidence_rank
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    ActionEffectOverrideConfig,
    ActionRiskTag,
)
from agents_shipgate.schemas.surfaces import ActionEffect

_EFFECT_RANK: dict[ActionEffect, int] = {
    "read": 0,
    "write": 1,
    "privileged_data_access": 2,
    "identity_access": 3,
    "code_execution": 4,
    "production_operation": 5,
    "external_communication": 6,
    "financial_write": 7,
    "destructive": 8,
}
_EFFECT_VALUES = frozenset(_EFFECT_RANK)
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
    declared = [
        claim for claim in authoritative if claim.source == DECLARED_EFFECT_SOURCE
    ]
    structural = [
        claim for claim in authoritative if claim.source != DECLARED_EFFECT_SOURCE
    ]
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

    # #409 — the monotone declaration rule. A heuristic must never *drive* a
    # verdict (#357): it cannot prove a read-only action and it cannot block on
    # its own. Challenging a human assertion is a different power, and one flag
    # governed both — so the one asymmetric edit went unremarked. Declaring
    # ``read`` on a tool this scanner itself tagged ``external_write`` closed
    # the very gap that evidence raised and made the action pass-eligible with
    # zero findings. Escalation past the evidence stays silent; only
    # de-escalation is compared, and the declaration remains operative either
    # way.
    #
    # A first draft exempted declarations the source itself corroborates —
    # ``support.search_kb`` declares ``read`` and carries ``readOnlyHint:
    # true``, so why make the reviewer defend a protocol annotation against a
    # keyword? Because this resolver already refuses to pass on that annotation
    # alone: with no declaration the same tool is ``inferred_effect_only`` and
    # not pass-eligible, precisely because a hint outranks it. Letting a
    # declaration that merely restates the annotation close that gap would put
    # the exemption back where #409 found it, and it would take its
    # corroboration from source content that is not conditioned on
    # ``tool_sources[].trust`` — an MCP server can assert ``readOnlyHint: true``
    # about itself. So corroboration does not exempt; it is *named* in the row
    # instead, which is what the reviewer needs to answer it in one line.
    below_declared: list[SemanticClaim] = []
    contradictory: list[SemanticClaim] = []
    corroborating: list[SemanticClaim] = []
    acknowledged_override: ActionEffectOverrideConfig | None = None
    below_effect: ActionEffect | None = None
    below_sources: list[str] = []
    if declaration is not None and declaration.effect is not None:
        declared_rank = _EFFECT_RANK[declaration.effect]
        # Unchanged: policy-eligible evidence outranking the declaration is a
        # blocking conflict, and no override may acknowledge it away.
        contradictory = [
            claim
            for claim in [*structural, *inferred]
            if claim.policy_eligible
            and _EFFECT_RANK[_as_effect(claim.value)] > declared_rank
        ]
        if not contradictory:
            below_declared = claims_above_declared_effect(
                [*structural, *inferred], declaration.effect
            )
        if below_declared:
            below_effect = _strongest_effect(
                [_as_effect(claim.value) for claim in below_declared]
            )
            below_sources = sorted(
                {claim.source for claim in below_declared if claim.value == below_effect}
            )
            # What the source says *for* the declared value, so the row can
            # state both readings. Never the manifest row's own restatements
            # of itself — a declaration confirming itself is not evidence.
            corroborating = [
                claim
                for claim in claims
                if claim.policy_eligible
                and claim.source not in DECLARATION_CLAIM_SOURCES
                and claim.value == declaration.effect
            ]
            # An acknowledged override is itself reviewed evidence: it records
            # that a human read the inference and rejected it, with a reason
            # the next reviewer can re-check. It rides in ``claims`` rather
            # than a new assessment field so only tools that actually carry one
            # change shape.
            acknowledged_override = declaration.override
            if acknowledged_override is not None:
                claims.append(
                    _claim(
                        "effect",
                        declaration.effect,
                        "high",
                        "static_declaration",
                        "reviewed_declaration",
                        DECLARATION_OVERRIDE_SOURCE,
                        f"action_surface.actions[tool={tool.name!r}].override",
                        {
                            "overridden_effect": below_effect,
                            "overridden_sources": below_sources,
                            # Every observation this acknowledgement suppresses,
                            # with the producers that made each one. The
                            # singular pair above names the strongest reading;
                            # a tool can carry two, and projecting only the
                            # strongest let the second disappear from the
                            # reviewer's row after it had been waived
                            # (PR #413 review 2).
                            "overridden_observations": _overridden_observations(
                                below_declared
                            ),
                            # Exactly which claims this acknowledgement covers.
                            # Policy applicability has to consume the same set
                            # the reviewer answered, and re-deriving it at each
                            # consumer is how the two drift apart.
                            "overridden_claim_ids": sorted(
                                {
                                    claim.claim_id
                                    for claim in below_declared
                                    if claim.claim_id
                                }
                            ),
                            "corroborating_sources": sorted(
                                {claim.source for claim in corroborating}
                            ),
                            "evidence": acknowledged_override.evidence,
                            "reason": acknowledged_override.reason,
                        },
                    )
                )

    claims = _sorted_claims(claims)
    all_effects = [_as_effect(claim.value) for claim in claims if claim.value in _EFFECT_VALUES]
    conservative = _strongest_effect(all_effects) if all_effects else "write"

    if declaration is not None and declaration.effect is not None:
        declared_effect = declaration.effect
        has_read, has_non_read = _source_read_conflict(structural)
        if has_read and has_non_read:
            status = "conflicting"
            confidence = "low"
            issues.append(
                _issue(
                    "conflicting_effect_evidence",
                    "effect",
                    _conflicting_declaration_message(
                        declaration,
                        "high-confidence read and side-effect evidence conflict",
                    ),
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
                    _conflicting_declaration_message(
                        declaration,
                        "declared effect is weaker than high-confidence source evidence",
                    ),
                    "action_surface_declaration",
                    f"action_surface.actions[tool={tool.name!r}].effect",
                )
            )
        elif below_declared and acknowledged_override is None:
            # The declaration still wins — status stays ``declared`` and the
            # human's value is what policy reads. What changes is that the
            # de-escalation is now on the record and owed an answer, so the
            # action is no longer evidence-backed-pass until the reviewer
            # either raises the effect or acknowledges the override.
            status = "declared"
            confidence = "high"
            issues.append(
                _issue(
                    "declaration_below_inferred_evidence",
                    "effect",
                    _below_evidence_message(
                        declared_effect,
                        below_effect,
                        below_sources,
                        corroborating,
                        below_declared,
                    ),
                    "action_surface_declaration",
                    f"action_surface.actions[tool={tool.name!r}].effect",
                )
            )
        else:
            status = "declared"
            confidence = "high"
    elif structural:
        has_read, has_non_read = _source_read_conflict(structural)
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


def _source_read_conflict(structural: Sequence[SemanticClaim]) -> tuple[bool, bool]:
    """Does the *source* claim both read-only and a side effect at high confidence?

    ``structural`` holds every authoritative claim that is not the declared
    ``effect`` field — which includes the manifest's own ``risk_tags``,
    ``scopes``, and acknowledged ``override``. Those restate the reviewed row;
    they are not a second opinion about it, and
    :data:`DECLARATION_CLAIM_SOURCES` exists to name exactly that set.

    Reading them here made a manifest contradict itself. Declaring
    ``risk_tags: [code_execution]`` on a tool whose server published
    ``readOnlyHint: true`` was reported as "high-confidence read and
    side-effect evidence conflict" — attributed to ``tool_source``, which had
    said only one of the two. That is not a hypothetical: it is exactly the
    repair the ``declaration_below_inferred_evidence`` row publishes, and the
    declaration this module proposes for an undeclared action, so the published
    next step could not close the row it was printed on.

    Escalating past a source annotation is a reviewed human assertion and the
    monotone rule (#409) already lets it stand silently; a source annotation is
    tool-published content that is not conditioned on ``tool_sources[].trust``,
    so it may never be the thing that blocks a human from over-declaring.
    """

    high = [
        claim
        for claim in structural
        if claim.confidence == "high" and claim.source not in DECLARATION_CLAIM_SOURCES
    ]
    return (
        any(claim.value == "read" for claim in high),
        any(claim.value != "read" for claim in high),
    )


def declaration_covers(declared: str, inferred: str) -> bool:
    """Does asserting ``declared`` account for an observation of ``inferred``?

    Two conditions, both necessary.

    *Risk*: the declaration must not rank below the observation under **either**
    published rank table. They disagree — :data:`_EFFECT_RANK` orders
    ``privileged_data_access`` above ``write`` and ``ACTION_EFFECT_RANK`` orders
    it below — and picking a winner would either loosen an existing gate path or
    leave this comparison contradicting the one
    ``_non_authoritative_effect_escalation_support`` makes about the same
    action. Requiring both makes them agree without weakening either.

    *Obligations*: the declaration must oblige at least the controls the
    observation would. Rank is a total order; obligations are not.
    ``financial_write`` outranks ``external_communication`` and requires
    approval, audit, and idempotency — but not confirmation, which is precisely
    what communicating outward requires. Testing rank alone let a declaration
    discharge a category it does not cover: the action went pass-eligible with
    no gap and no external-communication finding, while the external-write risk
    tag sat untouched in the same report.

    Nothing here decides *policy*. An uncovered observation becomes a reviewed
    question, never a control a heuristic imposed on its own (#357).
    """

    if declared == inferred:
        return True
    if declared not in _EFFECT_RANK or inferred not in _EFFECT_RANK:
        return False
    if _EFFECT_RANK[_as_effect(declared)] < _EFFECT_RANK[_as_effect(inferred)]:
        return False
    if ACTION_EFFECT_RANK[_as_effect(declared)] < ACTION_EFFECT_RANK[_as_effect(inferred)]:
        return False
    return builtin_obligations(_as_effect(inferred)).issubset(
        builtin_obligations(_as_effect(declared))
    )


def claims_above_declared_effect(
    claims: Sequence[SemanticClaim],
    declared_effect: str,
) -> list[SemanticClaim]:
    """Effect claims the reviewed surface does not account for.

    The monotone rule's comparison, in one place. The resolver needs the claims
    (it names their sources in the row); the release-decision projection needs
    only the value they resolve to, so it can publish the exact effect to raise
    to instead of an instruction that names nothing. Two derivations of the same
    comparison is the recurring defect class in this codebase, so there is one.

    Policy-eligible claims are excluded from the *challengers* because
    outranking the declaration *with* policy-eligible evidence is
    ``conflicting_effect_evidence`` — a blocking conflict, decided elsewhere —
    and because the declaration's own claim is policy-eligible and must never
    compare against itself. They are included in what *covers*: the reviewed
    surface is more than the ``effect`` field, and a
    ``risk_tags: [financial_action]`` entry produces a policy-eligible
    ``financial_write`` claim that applies the financial-write controls, so a
    heuristic reading the same effect is already accounted for. This is the set
    ``_control_effects`` unions, for the same reason.
    """

    if declared_effect not in _EFFECT_RANK:
        return []
    covering = {declared_effect}
    covering.update(
        claim.value
        for claim in claims
        if claim.policy_eligible and claim.value in _EFFECT_VALUES
    )
    return [
        claim
        for claim in claims
        if not claim.policy_eligible
        and claim.value in _EFFECT_VALUES
        and not any(declaration_covers(asserted, claim.value) for asserted in covering)
    ]


def effect_repair(effect: EffectSemanticAssessment) -> EffectRepair:
    """The non-override route out of this row, derived from every observation.

    Reads the assessment the resolver already produced rather than re-deciding:
    the declared claim carries the declared value, and
    :func:`claims_above_declared_effect` is the same comparison the resolver
    ran. An acknowledged override is absent from the claim list of any tool that
    still carries this gap, so it cannot mask the answer.

    A published next step has to be able to close the row it is printed on. Two
    ways the previous single-value instruction could not:

    * It named the *strongest* uncovered observation. With both a
      ``financial_write`` and an ``external_communication`` reading, raising to
      ``financial_write`` left the second one uncovered — the reviewer applied
      the exact edit the row asked for and got the same row back.
    * It fell through to "declare the ``write`` controls" for an effect that
      obliges no built-in control at all, naming nothing to do.

    So a raise is advertised only when one observed effect covers **every**
    uncovered observation *and* the value already declared — raising must not
    quietly drop the reading the reviewer chose. The candidate is drawn from
    the observations themselves: rank alone would nominate an unrelated effect
    that happens to sit higher, which is not a repair, it is a different claim.

    When no single effect covers the set, the repair is to name the categories
    as reviewed ``risk_tags``. That is not a formality — a declared tag is
    policy-eligible evidence, so it both accounts for the observation *and*
    makes that category's built-in controls apply, which is the outcome the
    uncovered obligation was asking for.
    """

    declared = next(
        (claim for claim in effect.claims if claim.source == DECLARED_EFFECT_SOURCE),
        None,
    )
    if declared is None:
        return EffectRepair(kind="raise_effect", instruction=_GENERIC_RAISE)
    above = claims_above_declared_effect(effect.claims, declared.value)
    uncovered = sorted(
        {_as_effect(claim.value) for claim in above},
        key=lambda value: (_EFFECT_RANK[value], value),
    )
    if not uncovered:
        return EffectRepair(kind="raise_effect", instruction=_GENERIC_RAISE)
    covering = [
        candidate
        for candidate in uncovered
        if declaration_covers(candidate, declared.value)
        and all(declaration_covers(candidate, value) for value in uncovered)
    ]
    if covering:
        # The weakest sufficient answer. Any of these closes the row, and
        # asking for more than that is asking the reviewer to over-declare.
        target = covering[0]
        return EffectRepair(
            kind="raise_effect",
            instruction=f"Raise action_surface.actions[].effect to {target!r}",
            effect=target,
        )
    tags = [value for value in uncovered if value in _ACTION_RISK_TAG_VALUES]
    if not tags:  # pragma: no cover - every non-read effect has a matching tag
        return EffectRepair(kind="raise_effect", instruction=_GENERIC_RAISE)
    rendered = ", ".join(tags)
    return EffectRepair(
        kind="declare_risk_tags",
        instruction=(
            f"Add action_surface.actions[].risk_tags: [{rendered}] so the "
            f"{rendered} controls apply to this action"
        ),
        risk_tags=tuple(tags),
    )


def acknowledged_effect_claim_ids(claims: Iterable[Any]) -> frozenset[str]:
    """Effect-claim ids a reviewed ``override`` has acknowledged.

    Empty unless the manifest carried an acknowledgement, so the default is the
    conservative one. Consumers ask this instead of re-running the comparison:
    an acknowledgement that policy applicability does not consume is not an
    acknowledgement at all — the reviewer follows the row's instruction and
    trades one gap for another (PR #411 review 1).

    Only non-policy-eligible claims can ever appear here: the resolver refuses
    to attach an override while policy-eligible evidence outranks the
    declaration, so a reviewed exception can never reach proven evidence.
    """

    acknowledged: set[str] = set()
    for claim in claims:
        if getattr(claim, "source", None) != DECLARATION_OVERRIDE_SOURCE:
            continue
        evidence = getattr(claim, "evidence", None)
        raw = evidence.get("overridden_claim_ids") if isinstance(evidence, dict) else None
        if isinstance(raw, list):
            acknowledged.update(str(value) for value in raw if value)
    return frozenset(acknowledged)


#: Effects that have a same-named ``action_surface.actions[].risk_tags`` value.
#: Every effect except ``read`` does, and ``read`` can never be uncovered — it
#: ranks at the floor of both tables and obliges nothing, so anything covers it.
_ACTION_RISK_TAG_VALUES = frozenset(get_args(ActionRiskTag)) & frozenset(_EFFECT_RANK)

_GENERIC_RAISE = "Raise action_surface.actions[].effect to the inferred effect"


@dataclass(frozen=True)
class EffectRepair:
    """The non-override route out of a ``declaration_below_inferred_evidence`` row.

    Carries the shape as well as the sentence so the structured
    ``next_action`` — its template and its accepted values — describes the same
    repair the prose does. Publishing an ``effect``-only vocabulary beside an
    instruction to add ``risk_tags`` is how a machine consumer and a human
    consumer of one row end up doing different things.
    """

    kind: Literal["raise_effect", "declare_risk_tags"]
    instruction: str
    effect: ActionEffect | None = None
    risk_tags: tuple[str, ...] = ()


#: Evidence bases that state a *default* rather than an observation of the
#: tool in front of us. ``mcp_protocol_default`` fires precisely *because* the
#: server published nothing about this tool: it says what the protocol assumes
#: in the absence of evidence, not what this tool does.
#:
#: Load-bearing for :func:`propose_effect_declaration`. A pre-filled answer is
#: shipgate putting a value in front of a reviewer, and it may only do that
#: from something it observed. Proposing ``write`` for every unannotated MCP
#: tool would be an assertion drawn from an absence, and it would arrive on
#: 117 rows at once — exactly the blanket-accept the blank was protecting.
NON_OBSERVATIONAL_EFFECT_BASES = frozenset({"protocol_default"})


@dataclass(frozen=True)
class EffectReading:
    """One effect the evidence can be read as, with the producers that say so.

    ``observed`` separates evidence about *this tool* from a protocol default
    standing in for the absence of any — see
    :data:`NON_OBSERVATIONAL_EFFECT_BASES`. Both are shown to a reviewer; only
    an observation may seed a proposal.
    """

    effect: ActionEffect
    sources: tuple[str, ...]
    observed: bool


@dataclass(frozen=True)
class EffectProposal:
    """A conservative declaration that accounts for every reading.

    ``risk_tags`` is non-empty exactly when no single effect covers the set —
    the same two-route model :class:`EffectRepair` publishes, because it is the
    same question asked before the declaration exists rather than after.
    """

    effect: ActionEffect
    risk_tags: tuple[str, ...] = ()


def effect_evidence_rank(effect: str) -> int:
    """Risk order of ``effect`` on the evidence-strength table.

    The one accessor for a table two modules now order by. Unknown values sort
    at the floor rather than raising: a rank drives display order, and a
    display order must not be able to fail a scan.
    """

    return _EFFECT_RANK.get(cast(ActionEffect, effect), 0)


def effect_readings(effect: EffectSemanticAssessment) -> list[EffectReading]:
    """Group this action's non-declaration effect claims into readings.

    Declaration-sourced claims are excluded for the reason
    :data:`DECLARATION_CLAIM_SOURCES` exists: they restate the manifest row, and
    a row is not evidence about itself. What is left is what the scan saw,
    which is what a reviewer needs in front of them to answer the question.

    Ordered weakest reading first so a consumer rendering a prefix never drops
    the strongest one.
    """

    sources: dict[tuple[ActionEffect, bool], set[str]] = {}
    for claim in effect.claims:
        if claim.source in DECLARATION_CLAIM_SOURCES:
            continue
        if claim.value not in _EFFECT_VALUES:
            continue
        # Keyed on the provenance class as well as the value. Grouping by
        # effect alone and OR-ing the bit put an unannotated MCP tool's
        # ``mcp_protocol_default`` into the same row as a keyword hint that
        # happened to read the same effect, and the row came out
        # ``observed=True`` — so the questionnaire printed the protocol default
        # under "what this scan read this action's effect as", which is exactly
        # what a default is not.
        key = (_as_effect(claim.value), claim.basis not in NON_OBSERVATIONAL_EFFECT_BASES)
        sources.setdefault(key, set()).add(claim.source)
    return [
        EffectReading(effect=value, sources=tuple(sorted(sources[key])), observed=observed)
        for key in sorted(
            sources,
            # Weakest reading first, and an observation ahead of a default that
            # reads the same effect.
            key=lambda item: (_EFFECT_RANK[item[0]], item[0], not item[1]),
        )
        for value, observed in (key,)
    ]


def propose_effect_declaration(
    readings: Sequence[EffectReading],
) -> EffectProposal | None:
    """The weakest declaration that accounts for every reading, or ``None``.

    ``None`` — keep the blank — in exactly two cases, and both are the point of
    an evidence-first proposal rather than a guess:

    * **Nothing was observed.** Only a protocol default stands here, and a
      default is an absence of evidence (see
      :data:`NON_OBSERVATIONAL_EFFECT_BASES`).
    * **Everything observed reads ``read``.** A heuristic must never establish
      that an action is read-only (#357); pre-filling ``effect: read`` would do
      precisely that, and it is the one direction where blanket acceptance
      loses safety rather than over-declaring. Every other proposal is at or
      above every reading, so the worst a reviewer who accepts one without
      thinking can do is over-declare — the safe direction, and one the
      monotone rule (#409) leaves visible rather than silent.

    The covering computation spans **all** readings, defaults included: the
    declaration has to close the row it is printed on, and an unaccounted
    protocol default is as much a challenger to a declaration as a keyword hint
    is (both are non-policy-eligible; see :func:`claims_above_declared_effect`).
    The gate above only decides whether to propose at all.
    """

    if not any(reading.observed and reading.effect != "read" for reading in readings):
        return None
    values = sorted(
        {reading.effect for reading in readings},
        key=lambda item: (_EFFECT_RANK[item], item),
    )
    covering = [
        candidate
        for candidate in values
        if all(declaration_covers(candidate, value) for value in values)
    ]
    if covering:
        # The weakest sufficient answer, for the reason ``effect_repair`` picks
        # the same one: anything more asks the reviewer to over-declare.
        return EffectProposal(effect=covering[0])
    # Rank alone cannot express this set — ``write`` and
    # ``privileged_data_access`` each outrank the other on one of the two
    # published tables. Declaring the strongest and naming every category as a
    # reviewed risk tag accounts for all of them and makes each category's
    # built-in controls apply, which is what an uncovered obligation is asking
    # for.
    tags = tuple(value for value in values if value in _ACTION_RISK_TAG_VALUES)
    if not tags:  # pragma: no cover - only ``read`` has no matching tag
        return None
    return EffectProposal(effect=values[-1], risk_tags=tags)


def _overridden_observations(
    below_declared: Sequence[SemanticClaim],
) -> list[dict[str, Any]]:
    """Each suppressed observation with its own producers, strongest last.

    Ordered by rank so a consumer rendering a prefix shows the mildest first
    and the strongest reading is never the one dropped.
    """

    by_effect: dict[str, set[str]] = {}
    for claim in below_declared:
        by_effect.setdefault(str(claim.value), set()).add(claim.source)
    return [
        {"effect": effect, "sources": sorted(by_effect[effect])}
        for effect in sorted(
            by_effect, key=lambda value: (_EFFECT_RANK.get(value, 0), value)
        )
    ]


def _outranks(candidate: str, other: str) -> bool:
    """True when ``candidate`` is the higher-risk reading of the two.

    Risk order only. Whether the higher reading is *accounted for* is
    :func:`declaration_covers`, which also weighs obligations; this answers the
    narrower question the row's wording turns on.
    """

    if candidate not in _EFFECT_RANK or other not in _EFFECT_RANK:
        return False
    return _EFFECT_RANK[_as_effect(candidate)] > _EFFECT_RANK[_as_effect(other)]


def _other_uncovered_observations(
    below_declared: Sequence[SemanticClaim],
    primary: ActionEffect | None,
) -> str:
    """Every uncovered observation this row is *not* already naming.

    The message names the strongest reading, but a tool can carry two inferred
    effects from two different hints — and with obligations weighed alongside
    rank, both can be uncovered at once. Naming only one leaves the reviewer
    acknowledging evidence the row never showed them.
    """

    by_effect: dict[str, set[str]] = {}
    for claim in below_declared:
        if claim.value == primary:
            continue
        by_effect.setdefault(str(claim.value), set()).add(claim.source)
    if not by_effect:
        return ""
    rendered = "; ".join(
        f"{effect!r} ({', '.join(sorted(by_effect[effect]))})"
        for effect in sorted(by_effect, key=lambda value: (_EFFECT_RANK.get(value, 0), value))
    )
    return f"; also unaccounted for: {rendered}"


def _below_evidence_message(
    declared_effect: ActionEffect,
    below_effect: ActionEffect | None,
    below_sources: list[str],
    corroborating: list[SemanticClaim],
    below_declared: Sequence[SemanticClaim] = (),
) -> str:
    """State both readings, so the row can be answered without a second lookup.

    The reviewer's next move differs entirely depending on whether the source
    agrees with them. Naming the corroboration is not an exemption — the
    resolver already refuses to pass on that evidence alone (see
    ``inferred_effect_only``) — it is the sentence that makes the override one
    line to write instead of an investigation to open.
    """

    # Two ways a declaration fails to account for an observation, and they want
    # different words. Telling a reviewer who declared `financial_write` that it
    # is "weaker than" `external_communication` is false, and sends them to
    # raise an effect that already outranks it — what is missing is the
    # confirmation control that communicating outward requires.
    if below_effect is not None and not _outranks(below_effect, declared_effect):
        message = (
            f"declared effect {declared_effect!r} does not carry the controls "
            f"required by inferred {below_effect!r} evidence "
            f"({', '.join(below_sources)})"
        )
    else:
        message = (
            f"declared effect {declared_effect!r} is weaker than inferred "
            f"{below_effect!r} evidence ({', '.join(below_sources)})"
        )
    message += _other_uncovered_observations(below_declared, below_effect)
    if corroborating:
        sources = ", ".join(sorted({claim.source for claim in corroborating}))
        message += f"; source evidence agrees with the declaration ({sources})"
    return message


def _conflicting_declaration_message(
    declaration: ActionDeclarationConfig,
    message: str,
) -> str:
    """Say when a written override does not reach this conflict.

    ``override`` acknowledges *inferred* evidence. A reviewer blocked here will
    reach for it — it is the route the sibling row publishes — and silently
    ignoring the block leaves them re-running against an unchanged message with
    a reviewed exception in the manifest that the resolver discarded. Both
    conflicting branches route through here: the user error is the same one
    whether the source evidence outranks the declaration or splits read from
    side-effect.
    """

    if declaration.override is not None:
        message += (
            "; the declared override does not apply here — it acknowledges "
            "inferred evidence, and this conflict is in policy-eligible source "
            "evidence"
        )
    return message


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


__all__ = [
    "acknowledged_effect_claim_ids",
    "assess_tool_semantics",
    "attach_semantic_assessments",
    "claims_above_declared_effect",
    "EffectRepair",
    "effect_repair",
]
