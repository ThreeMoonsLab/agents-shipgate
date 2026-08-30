from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from agents_shipgate.core.action_semantics import (
    ACTION_EFFECT_RANK,
    builtin_obligations,
    normalize_declared_strings,
)
from agents_shipgate.core.domain import (
    DECLARATION_CLAIM_SOURCES,
    DECLARATION_OVERRIDE_SOURCE,
    DECLARED_EFFECT_SOURCE,
    DECLARED_SOURCE_AUTHORITY_SOURCE,
    ENVIRONMENT_TEMPLATE_AUTHORITY_SOURCE,
    REVIEWED_RISK_TAG_CLAIM_SOURCES,
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
from agents_shipgate.core.tool_identity import configured_tool_source
from agents_shipgate.schemas.common import Confidence, ProvenanceKind, confidence_rank
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    ActionEffectOverrideConfig,
    ActionRiskTag,
    ToolSourceConfig,
)
from agents_shipgate.schemas.manifest.action_surface import CONFIRMED_BASIS_PREFIX
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
    *,
    tool_source: ToolSourceConfig | None = None,
    environment_target: str | None = None,
) -> ToolSemanticAssessment:
    """Resolve one tool's static effect and authority evidence.

    The resolver is deterministic, local-only, and conservative. Parsed input
    is not itself safety evidence: ambiguous protocol defaults and heuristic-
    only effects remain non-pass-eligible.

    ``tool_source`` is the ``tool_sources[]`` entry this tool was extracted
    from, when one configures it. It carries the source-wide authority
    declaration (#410 increment 3) and, declared or not, it is what tells the
    resolver that a source block exists to answer this action's authority in.
    Omitting it resolves exactly as before — which is what makes the
    counterfactual in ``declaration_questions`` mean "with no reviewed
    declaration at all", covering both sites in one call.

    ``environment_target`` is ``environment.target``. The one value it changes
    anything for is ``template``, which answers the authority dimension for
    actions that declare none of their own (#410 §G). It is omitted by the
    counterfactual for the same reason ``declaration`` and ``tool_source``
    are: it is a third site the same reviewed claim can be written at, and the
    counterfactual has to mean "with no reviewed declaration anywhere".
    """

    # Resolved once and read by both dimensions: which of the three manifest
    # sites is operative decides the permission list an action is judged on,
    # and the effect evidence drawn from it has to be the same list the
    # authority dimension reports. Two derivations meant two answers.
    reviewed = reviewed_authority(
        tool, declaration, tool_source, environment_target=environment_target
    )
    effect, conservative_effect = _assess_effect(tool, declaration, reviewed)
    authority = _assess_authority(
        tool, declaration, tool_source, reviewed, environment_target
    )
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
    tool_sources: Mapping[str, ToolSourceConfig] | None = None,
    environment_target: str | None = None,
    copy_tools: bool = True,
) -> list[Tool]:
    """Attach one declaration-aware assessment keyed strictly by tool ID.

    Risk-hint enrichment already owns a deep-copied tool graph.  This boundary
    only adds an immutable top-level assessment. Direct callers retain the
    non-mutation default; the scan pipeline sets ``copy_tools=False`` because
    it exclusively owns the enriched objects. Name-keyed declaration maps are
    intentionally ignored so same-name providers can never share evidence.

    ``tool_sources`` is the manifest's configured sources keyed by id. The join
    runs through ``configured_tool_source``, which reads the provenance the
    dispatcher recorded — never ``tool.source_id``, which is minted by the
    adapter and shares a namespace with configured ids, so joining on it
    applied an MCP row's reviewed authority to OpenAI API actions and failed to
    apply a ``codex_config`` row's to the source it was written for.
    """

    by_tool = declarations or {}
    by_source = tool_sources or {}
    assessed: list[Tool] = []
    for original in tools:
        tool = original.model_copy() if copy_tools else original
        declaration = by_tool.get(tool.id)
        tool_source = configured_tool_source(tool, by_source) if by_source else None
        tool.semantic_assessment = assess_tool_semantics(
            tool,
            declaration,
            tool_source=tool_source,
            environment_target=environment_target,
        )
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
    reviewed: ReviewedAuthority | None = None,
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
                # The reviewed tag list *verbatim*, because a repair that
                # publishes ``risk_tags`` publishes the whole value of that key
                # and has to be able to write back what is already there. It
                # cannot be rebuilt from the tag claims below: three members of
                # the vocabulary — ``read_only``, ``network_access`` and
                # ``customer_data`` — map to no positive effect and produce no
                # claim, so a list rebuilt from claims would drop what a
                # reviewer wrote (#424 review).
                #
                # Kept on the declared-effect claim for ``effect_repair``,
                # whose post-declaration path already anchors its comparison
                # on that claim. ``EffectSemanticAssessment.declared_risk_tags``
                # carries the same exact list for the pre-declaration proposal
                # path, where no declared-effect claim exists to hold it.
                {"declared_risk_tags": list(declaration.risk_tags)}
                if declaration.risk_tags
                else None,
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
    # The *operative* reviewed permission list, which is the same one the
    # authority dimension reports and the same one the action fact publishes as
    # ``required_scopes`` — the capability standard requires those two to
    # agree. Reading a different list here is how a declared ``crm.delete``
    # grant could sit beside a declared ``effect: read`` with nothing
    # objecting: a write-verb scope this manifest asserts the action requires
    # has to bound its effect, whichever of the two sites asserted it (#410
    # increment 3).
    for raw_scope in _reviewed_scopes(declaration, reviewed):
        scope_sources.append((raw_scope, "action_scope"))
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
            if claim.source in REVIEWED_RISK_TAG_CLAIM_SOURCES and claim.value != "read"
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
        # Unchanged: policy-eligible *source* evidence outranking the
        # declaration is a blocking conflict, and no override may acknowledge
        # it away.
        #
        # #424 — but a reviewed risk tag is not source evidence. The row this
        # branch pre-empts publishes exactly one non-raise route: "add
        # ``risk_tags: [X]`` so the X controls apply to this action". Reading
        # that tag back as evidence contradicting the ``effect`` beside it
        # turned the published repair into a *blocking* conflict whose message
        # blamed the reviewer's own manifest — 281 of the 390 declared/observed
        # pairs that take the tag route could not close the row they were
        # printed on. :func:`claims_above_declared_effect` had already decided
        # the other way one branch over, and ``_source_read_conflict`` was
        # fixed for the same reason; this branch never got the treatment.
        #
        # Narrow on purpose: only the two spellings of "declare this category
        # as reviewed" are excluded, never every manifest-owned claim. That
        # wider set covers ``action_scope`` too, and #417 deliberately made a
        # declared ``crm.delete`` grant bound the action's effect — a grant
        # asserts an independent fact, a tag refines the effect the same person
        # wrote.
        contradictory = [
            claim
            for claim in [*structural, *inferred]
            if claim.policy_eligible
            and claim.source not in REVIEWED_RISK_TAG_CLAIM_SOURCES
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
            # state both readings. Never the manifest's own restatements of
            # itself — a declaration confirming itself is not evidence, and
            # the sentence this feeds says "source evidence agrees with the
            # declaration" in so many words.
            #
            # By both routes the manifest reaches this dimension, not just the
            # action row: a reviewed ``risk_overrides.tags`` entry matching the
            # declared effect was named to the reviewer as the *source*
            # agreeing with them (#424 review).
            corroborating = [
                claim
                for claim in claims
                if claim.policy_eligible
                and not is_manifest_owned_effect_claim(claim)
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

    # #410 §E — drift pinning. Declarations are matched by name and nothing
    # ever re-opened one, so a green gate at month twelve could rest on a
    # description of a function that no longer does what it did. A pinned
    # declaration says which evidence it was answered against; when that
    # evidence moves, the question comes back.
    #
    # Complementary to the monotone rule rather than a second copy of it: #409
    # asks whether the declaration is *weaker than* today's evidence, which
    # only fires when the movement was upward past the declared value. This
    # asks whether today's evidence is the evidence that was answered at all,
    # which is also true when a reading disappears or when a stronger
    # declaration stops matching what the code does. Unpinned declarations —
    # every one written before this field existed — are untouched.
    if declaration is not None and declaration.basis is not None:
        readings = _readings_from_claims(claims)
        if declaration.basis != confirmed_basis(readings):
            observed = render_effect_readings(readings)
            issues.append(
                _issue(
                    "declaration_drift",
                    "effect",
                    "the effect evidence for this action is not the evidence this "
                    "declaration was confirmed against; "
                    + (
                        f"it now reads {observed}"
                        if observed
                        else "nothing is observed about it any more"
                    ),
                    DECLARED_EFFECT_SOURCE,
                    f"action_surface.actions[tool={tool.name!r}].basis",
                )
            )

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
            declared_risk_tags=(
                tuple(declaration.risk_tags) if declaration is not None else ()
            ),
            risk_tags_declared=(
                declaration is not None
                and "risk_tags" in declaration.model_fields_set
            ),
        ),
        conservative,
    )


def is_manifest_owned_effect_claim(claim: SemanticClaim) -> bool:
    """True when a human wrote this effect claim into the manifest.

    Two tests, because the manifest reaches the effect dimension by two routes
    that carry different bases:

    * :data:`DECLARATION_CLAIM_SOURCES` names the ``action_surface.actions``
      row itself — the declared effect, its ``risk_tags``, its ``scopes`` (a
      ``structural_scope`` basis, so the basis test below does not see it), and
      an acknowledged ``override``;
    * ``reviewed_declaration`` is the basis, and in this dimension a producer
      may only carry it by being ``risk_hint:manual`` — the hint
      ``risk_overrides.tags`` writes (``_validated_hint_basis`` grants the
      basis for no other source, so tool-published content cannot claim it).

    Missing the second route left the sibling manifest surface counted as
    *source* evidence: a reviewed ``risk_overrides`` tag of ``code_execution``
    on a tool published with ``readOnlyHint: true`` was reported as the source
    contradicting itself, and no declaration could clear it.

    Public because the same question is asked outside this module.
    ``SHIP-ACTION-EFFECT-DOWNGRADE-DECLARED`` names "the effect Shipgate
    inferred", and it derived that bound by excluding
    :data:`DECLARATION_CLAIM_SOURCES` alone — the first route only. A reviewed
    ``risk_overrides.tags`` entry of ``destructive`` beside a source that says
    only ``write`` was therefore quoted back to the reviewer as Shipgate's own
    inference, in a recommendation telling them to declare the value they had
    already written (#424 review).
    """

    return (
        claim.source in DECLARATION_CLAIM_SOURCES or claim.basis == "reviewed_declaration"
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
        if claim.confidence == "high" and not is_manifest_owned_effect_claim(claim)
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

    A third way it could not close its own row, and the reason ``risk_tags``
    is the *whole* list rather than the additions: ``risk_tags`` is one YAML
    key, so a template naming it replaces it. Publishing ``[destructive]`` for
    a row already reading ``risk_tags: [financial_write]`` asked the reviewer
    to delete the tag covering the financial reading, and the next scan
    reopened the row asking for it back (#424 review). ``added_risk_tags``
    carries what changed, so the sentence and the value cannot disagree.
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
    added = [value for value in uncovered if value in _ACTION_RISK_TAG_VALUES]
    if not added:  # pragma: no cover - every non-read effect has a matching tag
        return EffectRepair(kind="raise_effect", instruction=_GENERIC_RAISE)
    # The whole list, not just the new part. ``risk_tags`` is one YAML key, so
    # a template naming it replaces it: publishing ``[destructive]`` for a row
    # that already reads ``risk_tags: [financial_write]`` asked the reviewer to
    # delete the tag that was covering the financial reading, and the next scan
    # reopened the row asking for it back — the #424 defect surviving in the
    # case #424's own repair created (#424 review).
    #
    # Declared tags keep the reviewer's own spelling and order:
    # ``financial_action`` and ``financial_write`` are one category under two
    # names, and rewriting one to the other is a change nobody asked for.
    declared_tags = [
        str(tag)
        for tag in (declared.evidence.get("declared_risk_tags") or [])
        if isinstance(tag, str)
    ]
    declared_effects = {_TAG_EFFECTS.get(tag) for tag in declared_tags}
    tags = [*declared_tags, *(value for value in added if value not in declared_effects)]
    rendered_added = ", ".join(added)
    return EffectRepair(
        kind="declare_risk_tags",
        instruction=(
            f"Set action_surface.actions[].risk_tags: [{', '.join(tags)}] so the "
            f"{rendered_added} controls apply to this action"
        ),
        risk_tags=tuple(tags),
        added_risk_tags=tuple(added),
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
    #: The **complete** value to write to ``risk_tags`` — every tag the row
    #: already declares, plus the categories this repair adds. A template
    #: carrying only the additions replaces the key it is merged into, so on an
    #: already-tagged action the published repair dropped a covering tag and
    #: reopened the same row on the next scan (#424 review).
    risk_tags: tuple[str, ...] = ()
    #: The categories being added, which is what the instruction's "so the X
    #: controls apply" clause names and what ``accepted_values`` publishes.
    #: Equal to :attr:`risk_tags` when the row declared none.
    added_risk_tags: tuple[str, ...] = ()


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

    ``policy_eligible`` is the *strength* of the reading: true when at least one
    claim behind it is evidence this scanner may act on — protocol structure, a
    typed provider fact, or a source-owned structural scope — rather than a
    heuristic that may only challenge. Manifest-owned claims are not readings
    at all. Strength is the strongest class among the remaining claims, not a
    per-producer flag, because that is what makes it stable: a second heuristic
    agreeing with an annotation changes nothing about what the reading is worth.
    """

    effect: ActionEffect
    sources: tuple[str, ...]
    observed: bool
    policy_eligible: bool = False


@dataclass(frozen=True)
class EffectProposal:
    """A conservative declaration covering every reading and reviewed tag.

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


#: Where an action nothing has bounded sorts on that table.
#:
#: Above every effect in it, deliberately. An action nothing established is not
#: a low-risk action, it is an unbounded one: the strongest thing its answer
#: could turn out to be is the top of the vocabulary, and that is the quantity
#: a questionnaire ordered by "how much can answering this move the verdict"
#: has to rank by (#419). Ranking it by the inferred floor instead put every
#: unread action below every action the scan had already read for itself.
#:
#: Not an ``ActionEffect`` and never converted to one. Nothing but display
#: order reads it, and :func:`effect_evidence_rank` cannot return it.
UNBOUNDED_EFFECT_RANK = max(_EFFECT_RANK.values()) + 1


def effect_is_measured(readings: Sequence[EffectReading]) -> bool:
    """Did this scan observe a side effect for this action at all?

    The one gate, asked by two surfaces for opposite purposes and therefore
    spelled once. :func:`propose_effect_declaration` asks it to decide whether
    a pre-filled answer may be offered at all; the questionnaire asks it to
    decide where the question sorts. They are the same fact read from both
    ends — what the scan measured is what it may propose, and what it did not
    measure is what it most needs a human for — and a second spelling is how
    the two would start disagreeing about which questions are the cheap ones
    (#419).

    ``read`` alone is **not** a measurement here. This resolver refuses to
    establish a read-only action from a heuristic (#357), so an action whose
    only *heuristic* reading is ``read`` is exactly as unproven as one with no
    reading at all, and its answer can still be anything.

    That last rule is about heuristics, and it makes this predicate wrong for
    any question other than "may a value be pre-filled" — an OpenAPI ``GET``
    and a trusted ``readOnlyHint`` are read-only and *proven*, and this still
    returns ``False`` for them. :func:`effect_is_bounded` is the one to ask
    about what the scan established.
    """

    return any(reading.observed and reading.effect != "read" for reading in readings)


#: Effect statuses that hold an action down without a human answering anything.
#:
#: ``declared`` is a reviewed declaration and ``structural`` is policy-eligible
#: source evidence; both establish the effect, ``read`` included.
#: ``conflicting`` is evidence disagreeing with itself, which is still
#: evidence — the conservative reading is a real bound, and the reviewer is
#: being asked to reconcile a known disagreement rather than to name an
#: unknown.
#:
#: Deliberately not ``protocol_default``, which is what the protocol assumes
#: when a server publishes nothing, and not ``inferred``, which is a heuristic
#: this resolver will not let establish a read-only action at all (#357). Both
#: leave the answer unbounded.
_BOUNDED_EFFECT_STATUSES: frozenset[str] = frozenset(
    {"declared", "structural", "conflicting"}
)


def effect_is_bounded(effect: EffectSemanticAssessment) -> bool:
    """Is anything other than a human's answer already holding this effect down?

    The **ordering** question, and deliberately not
    :func:`effect_is_measured`. That one answers "may a non-read effect be
    pre-filled here", which is a proposal-safety rule: it returns ``False`` for
    every read-only reading however authoritative, because a pre-filled
    ``effect: read`` is the one direction where a confirmed guess loses safety.

    Reusing it to rank questions put a structurally proven read at the *top* of
    the questionnaire — an OpenAPI ``GET`` named ``delete_account`` outranking a
    genuinely unknown effect, with a repository-chosen name breaking the tie.
    That is the defect #419 exists to fix, wearing the other hat: something the
    scan established being ordered as though it had not been.

    Two ways to be bounded, because the resolver records them differently. A
    status in :data:`_BOUNDED_EFFECT_STATUSES` means the resolver established
    the effect, and it holds for a bounded ``read`` that has no reading at all
    behind it — a reviewed ``effect: read`` is the manifest speaking, and
    declaration claims are excluded from readings on purpose. Otherwise, an
    observed side effect is a bound even where nothing established it: that is
    the ``inferred`` action whose keyword hint reads ``external_communication``.
    """

    if effect.status in _BOUNDED_EFFECT_STATUSES:
        return True
    return effect_is_measured(effect_readings(effect))


def effect_readings(effect: EffectSemanticAssessment) -> list[EffectReading]:
    """Group this action's non-declaration effect claims into readings.

    Manifest-owned claims are excluded because they restate the trust root, and
    a manifest is not evidence about itself. The manifest reaches this
    dimension through both the action row and ``risk_overrides.tags``; asking
    :func:`is_manifest_owned_effect_claim` keeps both spellings out. What is
    left is what the scan saw, which is what a reviewer needs in front of them
    to answer the question.

    Ordered weakest reading first so a consumer rendering a prefix never drops
    the strongest one.
    """

    return _readings_from_claims(effect.claims)


def _readings_from_claims(claims: Sequence[SemanticClaim]) -> list[EffectReading]:
    """:func:`effect_readings` over a claim list still being assembled.

    Split out so the resolver can compare a declaration's pin against the same
    readings the questionnaire will publish, without either side re-deriving
    the grouping.
    """

    sources: dict[tuple[ActionEffect, bool], set[str]] = {}
    eligible: set[tuple[ActionEffect, bool]] = set()
    for claim in claims:
        if is_manifest_owned_effect_claim(claim):
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
        if claim.policy_eligible:
            eligible.add(key)
    return [
        EffectReading(
            effect=value,
            sources=tuple(sorted(sources[key])),
            observed=observed,
            policy_eligible=key in eligible,
        )
        for key in sorted(
            sources,
            # Weakest reading first, and an observation ahead of a default that
            # reads the same effect.
            key=lambda item: (_EFFECT_RANK[item[0]], item[0], not item[1]),
        )
        for value, observed in (key,)
    ]


def effect_derivation_id(readings: Sequence[EffectReading]) -> str:
    """A stable digest of the source evidence an effect answer rests on.

    The pin behind ``action_surface.actions[].basis``. It answers one question
    on every later scan — *is this still the evidence that was answered?* — so
    what it digests is chosen for two properties, and both are tested:

    * **It is exactly what the reviewer was shown.** The questionnaire prints
      :func:`effect_readings` under "what this scan read this action's effect
      as"; this digests the observed ones. "Every answer is pinned to the
      evidence that justified it" is then literal rather than approximate.
    * **It does not move when the answer arrives.** The readings are the same
      set before and after the declaration is written, because declaration
      claims are excluded by :func:`effect_readings`, including the sibling
      ``risk_overrides.tags`` route, and the one claim whose *presence* depends
      on a declaration — the MCP protocol default, emitted only when nothing is
      declared — is not an observation and is filtered here. Without that a
      reviewer would confirm a proposal and get a drift row back on the very
      next scan, which is the one thing a pin may not do.

    What is digested is the reading **and its strength**, and both halves are
    load-bearing.

    Individual producers are not digested: a second source corroborating a
    reading the reviewer already answered is not new information about the
    action, and a shipgate release that adds a heuristic would otherwise
    re-open every pinned declaration on every adopter at once.

    But the *class* of the strongest producer is, because dropping it made the
    pin blind to a replacement. A tool published with ``readOnlyHint: true``
    beside a ``read_only`` keyword hint reads ``read`` twice over; delete the
    annotation and it still reads ``read``, from the heuristic alone. Digesting
    only the effect string held the pin steady while the evidence a reviewer
    actually leaned on disappeared — and ``read`` is the one classification
    where losing the authoritative half matters most, because a heuristic may
    never establish it (#357). Strength is taken as the strongest class per
    reading rather than per producer, so corroboration stays quiet and
    replacement does not.
    """

    observed = sorted(
        (reading.effect, reading.policy_eligible)
        for reading in readings
        if reading.observed
    )
    rendered = json.dumps(observed, separators=(",", ":"))
    return hashlib.sha256(rendered.encode()).hexdigest()[:12]


def confirmed_basis(readings: Sequence[EffectReading]) -> str:
    """The ``basis`` value that pins an answer to ``readings``."""

    return f"{CONFIRMED_BASIS_PREFIX}{effect_derivation_id(readings)}"


def risk_tag_answers_effect(tag: str) -> bool:
    """Whether declaring ``tag`` produces a reviewed effect claim.

    The action row's ``risk_tags`` is the second route out of a
    ``declaration_below_inferred_evidence`` row, but only for tags this
    resolver maps to a non-``read`` effect: ``_TAG_EFFECTS`` has no entry for
    ``network_access`` or ``customer_data``, and a ``read`` mapping is
    deliberately dropped because a positive risk tag may never establish
    read-only safety.

    Exported because "has this action's effect been answered?" is asked outside
    this module — the adoption ladder counted *any* non-empty ``risk_tags`` as
    an answer, so a manifest carrying only ``risk_tags: [network_access]``
    advanced a rung while the action still reported ``missing_effect_evidence``.
    """

    return _TAG_EFFECTS.get(cast(ActionRiskTag, tag)) not in (None, "read")


def declared_effect_of(effect: EffectSemanticAssessment) -> ActionEffect | None:
    """The effect this action's manifest row declares, read off the claims.

    Read from the resolved claims rather than from the manifest a second time:
    which declaration keyed onto which tool is a join the resolver already
    made, and re-deriving it here is the second implementation of it.
    """

    for claim in effect.claims:
        if claim.source == DECLARED_EFFECT_SOURCE and claim.value in _EFFECT_VALUES:
            return _as_effect(claim.value)
    return None


def render_effect_readings(readings: Sequence[EffectReading]) -> str:
    """The observed readings as one comma-separated phrase, strongest last.

    One rendering, because two surfaces state the same set: the drift issue's
    message and anything that echoes it. Empty when nothing was observed —
    callers phrase that case themselves rather than printing "()".
    """

    return ", ".join(
        dict.fromkeys(reading.effect for reading in readings if reading.observed)
    )


def reviewed_risk_tag_effects(
    effect: EffectSemanticAssessment,
) -> tuple[ActionEffect, ...]:
    """Effects the manifest's reviewed risk tags already apply to this action.

    These values constrain a proposal but are deliberately not
    :class:`EffectReading` rows. They came from the trust root rather than the
    scanned tool, so presenting them under "what this scan read" would turn a
    declaration into its own evidence and would also move the declaration's
    derivation pin.

    Both reviewed-tag spellings are included: ``action_surface.actions[].risk_tags``
    and a matching ``risk_overrides.tags`` entry. An invalid claim merely using
    one of their source labels is not included; it must also pass the shared
    manifest-ownership test.
    """

    return tuple(
        sorted(
            {
                _as_effect(claim.value)
                for claim in effect.claims
                if claim.source in REVIEWED_RISK_TAG_CLAIM_SOURCES
                and is_manifest_owned_effect_claim(claim)
                and claim.value in _EFFECT_VALUES
            },
            key=lambda value: (_EFFECT_RANK[value], value),
        )
    )


def reviewed_risk_tag_constraints(
    effect: EffectSemanticAssessment,
) -> tuple[str, ...]:
    """Exact reviewed tag spellings that constrain an effect proposal.

    Coverage math uses normalized effects, but the audit surface must name what
    the manifest actually says. Start with the action row's complete ordered
    list — including unmapped tags — then add the exact spelling of matching
    ``risk_overrides.tags`` claims. De-duplicate without sorting the action
    row, because its bytes are the reviewed answer a replacement must preserve.
    """

    tags = list(effect.declared_risk_tags)
    seen = set(tags)
    for claim in effect.claims:
        if claim.source != "risk_hint:manual" or not is_manifest_owned_effect_claim(
            claim
        ):
            continue
        tag = claim.evidence.get("tag")
        if isinstance(tag, str) and tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tuple(tags)


def propose_effect_declaration(
    readings: Sequence[EffectReading],
    *,
    reviewed_effects: Iterable[ActionEffect] = (),
    declared_risk_tags: Sequence[str] = (),
) -> EffectProposal | None:
    """The weakest declaration covering the evidence and reviewed tags.

    ``None`` — keep the blank — in exactly the two cases
    :func:`effect_is_measured` rules out, and both are the point of an
    evidence-first proposal rather than a guess:

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

    The covering computation spans **all** readings, defaults included, plus
    the effects of reviewed risk tags already present in the manifest. The
    declaration has to close the row it is printed on, and an unaccounted
    protocol default is as much a challenger to a declaration as a keyword hint
    is (both are non-policy-eligible; see :func:`claims_above_declared_effect`).
    A reviewed tag also has to survive in the proposed answer, but it never
    unlocks a proposal by itself: the gate above reads only ``readings``, so a
    manifest entry cannot masquerade as an observation.
    """

    if not effect_is_measured(readings):
        return None
    values = sorted(
        {
            *(reading.effect for reading in readings),
            *(value for value in reviewed_effects if value in _EFFECT_VALUES),
        },
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
    added = tuple(value for value in values if value in _ACTION_RISK_TAG_VALUES)
    if not added:  # pragma: no cover - only ``read`` has no matching tag
        return None
    # ``risk_tags`` is one YAML key, so the proposal replaces its whole value.
    # Preserve the action row's exact spelling and order, including tags that
    # map to no effect at all; rebuilding it from ``reviewed_effects`` turned
    # ``financial_action`` into ``financial_write`` and silently deleted
    # ``network_access``. Add only categories no existing spelling covers.
    declared = tuple(str(tag) for tag in declared_risk_tags)
    declared_effects = {_TAG_EFFECTS.get(tag) for tag in declared}
    tags = (*declared, *(value for value in added if value not in declared_effects))
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


@dataclass(frozen=True)
class ReviewedAuthority:
    """One reviewed authority claim, normalized across the two sites it can be written at.

    ``claim_source`` and ``pointer`` are carried rather than re-derived because
    every consumer that reports this claim — the claim list, the conflict
    issue, the repair a gap publishes — has to name the *same* block, and a
    second derivation of "which site was this?" is how two of them start
    naming different ones.
    """

    mode: AuthorityMode
    auth_type: str | None
    credential_mode: str | None
    scopes: list[str]
    claim_source: str
    pointer: str


#: The ``environment.target`` that answers the authority dimension by itself.
TEMPLATE_ENVIRONMENT_TARGET = "template"


def reviewed_authority(
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
    tool_source: ToolSourceConfig | None,
    *,
    environment_target: str | None = None,
) -> ReviewedAuthority | None:
    """The reviewed authority that governs this action, and where it was written.

    Two manifest sites can carry the same claim (#410 increment 3). The action
    row wins where it exists — it is the more specific statement, and it is how
    one action of a source declares an authority the rest of the source does
    not have. Everything downstream of this function reads the normalized
    record, so the two spellings cannot drift into two behaviours: the conflict
    rule, the "cannot replace ambiguous source evidence" rule, and pass
    eligibility all apply identically to both.

    The two sites are alternatives, not a mixture. Whichever one is operative
    supplies the whole record — mode, type, credential mode, and the permission
    list — and that one list is what every surface reports and judges: the
    action's ``required_scopes``, this dimension's ``scopes``, the capability
    fact's (``CapabilityFactV1`` *requires* those to agree), and the list the
    effect evidence reads. Mixing the two sites' lists, or letting one surface
    pick a different one, gave two answers to "what is this action granted?" —
    once quietly, as a declared ``crm.delete`` grant beside a declared
    ``effect: read`` with nothing objecting, and once loudly, as a validation
    error on the base-vs-head path.
    """

    if declaration is not None and declaration.authority is not None:
        authority = declaration.authority
        return ReviewedAuthority(
            mode=cast(AuthorityMode, authority.mode),
            auth_type=authority.auth_type,
            credential_mode=authority.credential_mode,
            # An action row keeps its permission list in the sibling
            # ``scopes`` field, so the canonical list stays in one place.
            scopes=sorted(set(declaration.scopes)),
            claim_source=DECLARED_EFFECT_SOURCE,
            pointer=f"action_surface.actions[tool={tool.name!r}].authority",
        )
    if tool_source is not None and tool_source.authority is not None:
        authority = tool_source.authority
        return ReviewedAuthority(
            mode=cast(AuthorityMode, authority.mode),
            auth_type=authority.auth_type,
            credential_mode=authority.credential_mode,
            scopes=sorted(set(authority.scopes)),
            claim_source=DECLARED_SOURCE_AUTHORITY_SOURCE,
            pointer=f"tool_sources[id={tool_source.id!r}].authority",
        )
    if _template_authority_applies(tool, declaration, environment_target):
        return ReviewedAuthority(
            mode="none",
            auth_type=None,
            credential_mode=None,
            scopes=[],
            claim_source=ENVIRONMENT_TEMPLATE_AUTHORITY_SOURCE,
            pointer="environment.target",
        )
    return None


def _template_authority_applies(
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
    environment_target: str | None,
) -> bool:
    """Whether ``environment.target: template`` answers *this* action.

    A template has no deployment, so nothing in it holds a credential — but
    that is a statement about the absence of evidence, and it may only stand
    where there is no evidence. Anything more specific wins, and "more
    specific" is not only what a reviewer wrote:

    * an action row's own ``authority``, and a ``tool_sources[].authority``
      block, are handled before this is reached — they return their own record;
    * an action row's bare ``scopes:`` list is a reviewed statement that this
      action *is* granted something;
    * and so is anything the **source** publishes. This is the one that is easy
      to get wrong, and getting it wrong is a fail-open rather than a nuisance:
      the record supplies the whole permission list, so applying it over a tool
      that publishes ``oauth2 + docs:read`` empties that action's
      ``required_scopes`` and ``SHIP-AUTH-SCOPE-COVERAGE-MISSING`` silently
      stops seeing anything to cover. A repository-wide claim about deployment
      may not subtract evidence a source proved.

    The test for that last one is **every field the record would replace**, not
    every field one grader happens to read. :class:`ReviewedAuthority` carries
    four — mode, auth type, credential mode, and scopes — and installing it
    overwrites all four. ``_source_authority`` grades three of them (reporting
    ``"unknown"`` status exactly when the source published nothing usable, and
    something else for the ambiguous and invalid cases, which are *something
    published*, badly, and equally not this claim's to overwrite). It never
    reads ``credential_mode``, so a tool publishing ``service_account`` and
    nothing else graded ``unknown``, took ``mode: none``, and had its credential
    mode cleared — with ``credential_modes`` policy selectors silently ceasing
    to match it. ``test_the_template_fallback_yields_to_every_field_it_would_replace``
    is keyed off the record's own fields so a fifth one cannot be forgotten.
    """

    if environment_target != TEMPLATE_ENVIRONMENT_TARGET:
        return False
    if _reviewed_scopes(declaration, None):
        return False
    if tool.auth.credential_mode:
        return False
    return _source_authority(tool)[1] == "unknown"


def _reviewed_scopes(
    declaration: ActionDeclarationConfig | None,
    reviewed: ReviewedAuthority | None,
) -> list[str]:
    """The permission list a reviewer asserted for this action.

    The operative reviewed authority owns it whenever one exists — the two
    sites are alternatives, and mixing their lists produced two answers to
    "what is this action granted?". With no reviewed authority at either site
    an action row's bare ``scopes`` list still stands, exactly as before.
    """

    if reviewed is not None:
        return list(reviewed.scopes)
    return list(declaration.scopes) if declaration is not None else []


def _answerable_source_id(
    declaration: ActionDeclarationConfig | None,
    tool_source: ToolSourceConfig | None,
) -> str | None:
    """Where an authority answer for this action belongs — a source, or nowhere.

    ``None`` means "on the action row": either a per-action declaration already
    claims this authority, or the action came from a surface that no
    ``tool_sources`` entry configures, in which case there is no source block
    to write. Never guessed from ``tool.source_id`` alone: a per-scan adapter
    stamps a source id that ``tool_sources`` does not accept, and prescribing a
    block there would publish a repair the schema rejects.
    """

    if declaration is not None and declaration.authority is not None:
        return None
    return tool_source.id if tool_source is not None else None


def resolve_action_scopes(
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
    tool_source: ToolSourceConfig | None = None,
    reviewed: ReviewedAuthority | None = None,
    *,
    environment_target: str | None = None,
) -> list[str]:
    """The one permission list this action publishes, normalized and sorted.

    A reviewed authority supplies the whole record including its scopes
    (:func:`reviewed_authority`). With none at either site the row's own
    ``scopes:`` list stands where it lists anything, and the source's
    published scopes otherwise. Note what that last rule is *not*: a declared
    list replaces the source's, so this is where a row can drop a scope the
    source proves — ``_scopes_narrow_source`` guards it, and only the
    authority resolver can, because only it holds the source's evidence grade.
    Listing scopes does not grade the authority either: a row with no reviewed
    block still reports ``missing_authority_evidence``.

    Every surface that publishes an action's permissions reads this function:
    ``ActionFact.required_scopes`` (via ``build_action``) and this dimension's
    ``scopes`` (via ``_assess_authority``), and through both,
    ``CapabilityFactV1.authority.scopes`` — which *requires* the two to be one
    list. A second spelling of the rule is therefore not a style question: two
    spellings put two permission lists on one action, and ``verify --base`` —
    the only path that rebuilds a capability fact from a serialized
    ``ActionFact`` — raises ``internal_error`` on a legal manifest. The
    reviewed half of that was closed with the normalized record; the bare
    ``scopes:`` half is closed by this being the only derivation.

    ``environment_target`` is the third site the reviewed record can come from
    (#410 §G), and it has to reach the fallback re-derivation for the same
    reason: a caller that resolves the record with the target and a caller that
    re-derives it without one are two spellings again, and this time the two
    would disagree only in the manifests that declare ``template`` — the
    hardest kind of divergence to notice. Live callers pass ``reviewed`` and
    never reach the fallback at all.
    """

    if reviewed is None:
        reviewed = reviewed_authority(
            tool, declaration, tool_source, environment_target=environment_target
        )
    if reviewed is not None:
        return normalize_declared_strings(reviewed.scopes)
    declared = normalize_declared_strings(declaration.scopes if declaration is not None else [])
    return declared or normalize_declared_strings(tool.auth.scopes)


def _assess_authority(
    tool: Tool,
    declaration: ActionDeclarationConfig | None,
    tool_source: ToolSourceConfig | None = None,
    reviewed: ReviewedAuthority | None = None,
    environment_target: str | None = None,
) -> AuthoritySemanticAssessment:
    claims: list[SemanticClaim] = []
    issues: list[SemanticIssue] = []
    pointer = tool.source_pointer
    source_mode, source_status = _source_authority(tool)
    if reviewed is None:
        reviewed = reviewed_authority(
            tool, declaration, tool_source, environment_target=environment_target
        )
    # One list, resolved once from the record this function judges on. The
    # narrowing check below and the published ``scopes`` both read it, so a
    # branch cannot compare one permission list and publish another.
    scopes = resolve_action_scopes(
        tool, declaration, tool_source, reviewed, environment_target=environment_target
    )

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

    if reviewed is not None:
        claims.append(
            _claim(
                "authority",
                reviewed.mode,
                "high",
                "static_declaration",
                "reviewed_declaration",
                reviewed.claim_source,
                reviewed.pointer,
                {
                    "auth_type": reviewed.auth_type,
                    "credential_mode": reviewed.credential_mode,
                    "scopes": reviewed.scopes,
                },
            )
        )
        declared_mode = reviewed.mode
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
            reviewed,
            source_mode=source_mode,
        ):
            status = "conflicting"
            mode: AuthorityMode = "unknown"
            issues.append(
                _issue(
                    "conflicting_authority_evidence",
                    "authority",
                    "declared authority conflicts with source authority evidence",
                    # Attributed to the site the declaration was written at, so
                    # the repair points at the block a reviewer has to edit
                    # rather than at whichever site happens to be spelled first.
                    reviewed.claim_source,
                    reviewed.pointer,
                )
            )
        else:
            status = "declared"
            mode = declared_mode
        auth_type = reviewed.auth_type
        # ``credential_mode`` is optional, and omitting it is not a claim that
        # the action has none. Overwriting a published ``service_account`` with
        # ``None`` left the dimension ``declared`` and pass-eligible while
        # capability policies matching ``credential_modes: [service_account]``
        # silently stopped matching — a control dropped by an omission (#410
        # review). Where the declaration states one it still governs, and a
        # *different* stated value is a conflict, as before.
        #
        # Not preserved under ``mode: none``: that mode is the claim that no
        # credential exists, so there is nothing for a credential mode to
        # describe — and the schema now refuses to let one be declared there.
        credential_mode = (
            reviewed.credential_mode
            if reviewed.credential_mode or reviewed.mode == "none"
            else tool.auth.credential_mode
        )
    else:
        status = source_status
        mode = source_mode
        auth_type = tool.auth.type
        credential_mode = tool.auth.credential_mode
        # A bare ``scopes:`` list is not a reviewed authority — it names no
        # mode, no auth type, no credential mode — but it *is* the permission
        # list this action publishes (``resolve_action_scopes``), so this
        # dimension publishes it too. What it must never do is silently shrink
        # authority the source proves: with no reviewed record there is no
        # ``_authority_declaration_conflicts`` call on this route, and nothing
        # else compares the two lists.
        if (
            status == "structural"
            and declaration is not None
            and _scopes_narrow_source(tool, scopes, source_mode=source_mode)
        ):
            status = "conflicting"
            mode = "unknown"
            issues.append(
                _issue(
                    "conflicting_authority_evidence",
                    "authority",
                    "declared scopes drop scopes the source proves this action requires",
                    DECLARED_EFFECT_SOURCE,
                    f"action_surface.actions[tool={tool.name!r}].scopes",
                )
            )
        elif status == "partial":
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
        answerable_source_id=_answerable_source_id(declaration, tool_source),
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
    reviewed: ReviewedAuthority,
    *,
    source_mode: AuthorityMode,
) -> bool:
    """Reject reviewed declarations that weaken concrete source authority.

    A declaration may resolve missing or partial source metadata, but it may
    not replace high-confidence source scopes or an authentication type with
    weaker/different values. A scope superset is an explicit authority
    broadening and remains visible to the existing broad-scope policies.

    Reads the normalized record, so a source-wide declaration is held to
    exactly the rule a per-action one is. That is what keeps the new spelling
    from becoming a way to weaken published evidence in bulk: declaring
    ``mode: none`` across a source whose tools publish an OAuth scope raises
    the conflict on each of those tools, and the reviewer either corrects the
    block or declares the exception on the action row.
    """

    if _authority_modes_conflict(reviewed.mode, source_mode):
        return True

    source_auth_type = (tool.auth.type or "").strip().lower()
    declared_auth_type = (reviewed.auth_type or "").strip().lower()
    if source_auth_type and declared_auth_type and source_auth_type != declared_auth_type:
        return True

    source_credential_mode = (tool.auth.credential_mode or "").strip().lower()
    declared_credential_mode = (reviewed.credential_mode or "").strip().lower()
    if (
        source_credential_mode
        and declared_credential_mode
        and source_credential_mode != declared_credential_mode
    ):
        return True

    return _scopes_narrow_source(tool, reviewed.scopes, source_mode=source_mode)


def _scopes_narrow_source(
    tool: Tool,
    resolved_scopes: Iterable[str],
    *,
    source_mode: AuthorityMode,
) -> bool:
    """Does the list this action resolved to drop a scope the source proves?

    Only meaningful against a source whose own authority is concrete: a
    ``scoped`` source names the grant this action runs under, and a list that
    is narrower has replaced that grant with a weaker claim. A superset is an
    explicit broadening and stays visible to the broad-scope policies.

    Both routes ask this question of the *resolved* list, because both replace
    the same one: a reviewed authority at either manifest site, and a bare
    ``scopes:`` list with no reviewed record at all.
    """

    if source_mode != "scoped":
        return False
    source_scopes = set(normalize_declared_strings(tool.auth.scopes))
    return not source_scopes.issubset(set(normalize_declared_strings(resolved_scopes)))


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
    "ReviewedAuthority",
    "attach_semantic_assessments",
    "confirmed_basis",
    "declared_effect_of",
    "risk_tag_answers_effect",
    "effect_derivation_id",
    "render_effect_readings",
    "reviewed_risk_tag_constraints",
    "reviewed_risk_tag_effects",
    "reviewed_authority",
    "claims_above_declared_effect",
    "EffectRepair",
    "effect_repair",
    "is_manifest_owned_effect_claim",
]
