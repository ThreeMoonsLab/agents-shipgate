from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agents_shipgate.core.capability_lattice import mcp_permission_risk_hints
from agents_shipgate.core.domain import AuthInfo, Tool, ToolRiskHint
from agents_shipgate.core.semantic_assessment import (
    acknowledged_effect_claim_ids,
    assess_tool_semantics,
    attach_semantic_assessments,
)
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.schemas.manifest import ActionDeclarationConfig, ToolSourceConfig


def _tool(**updates: object) -> Tool:
    values: dict[str, object] = {
        "id": "tool:process_order",
        "name": "process_order",
        "source_type": "mcp",
        "source_id": "orders",
        "source_pointer": "/tools/0",
        "extraction_confidence": "high",
        "extraction": {"method": "mcp_json", "confidence": "high"},
    }
    values.update(updates)
    return Tool.model_validate(values)


def test_neutral_mcp_tool_is_not_pass_eligible() -> None:
    assessment = assess_tool_semantics(_tool())

    assert assessment.conservative_effect == "write"
    assert assessment.effect.status == "protocol_default"
    assert assessment.authority.status == "unknown"
    assert assessment.pass_eligible is False
    assert {issue.kind for issue in assessment.effect.issues} == {"missing_effect_evidence"}
    assert {issue.kind for issue in assessment.authority.issues} == {"missing_authority_evidence"}


def test_structural_read_and_explicit_no_authority_are_pass_eligible() -> None:
    assessment = assess_tool_semantics(
        _tool(
            annotations={"readOnlyHint": True},
            auth=AuthInfo(source="mcp", mode="none", explicit=True),
        )
    )

    assert assessment.conservative_effect == "read"
    assert assessment.effect.status == "structural"
    assert assessment.authority.status == "structural"
    assert assessment.authority.mode == "none"
    assert assessment.pass_eligible is True


def test_read_only_hint_cannot_suppress_write_scope() -> None:
    assessment = assess_tool_semantics(
        _tool(
            annotations={"readOnlyHint": True},
            auth=AuthInfo(
                type="oauth2",
                scopes=["orders:write"],
                source="mcp",
                mode="scoped",
                explicit=True,
            ),
        )
    )

    assert assessment.conservative_effect == "write"
    assert assessment.effect.status == "conflicting"
    assert assessment.pass_eligible is False
    assert "conflicting_effect_evidence" in {issue.kind for issue in assessment.effect.issues}


def test_structural_scope_risk_hint_preserves_typed_basis() -> None:
    tool = _tool(
        auth=AuthInfo(
            type="oauth2",
            scopes=["orders:write"],
            source="mcp",
            mode="scoped",
            explicit=True,
        )
    )
    tool.risk_hints.extend(mcp_permission_risk_hints(tool))

    assessment = assess_tool_semantics(tool)

    scope_hints = [
        claim
        for claim in assessment.effect.claims
        if claim.source == "risk_hint:auth_scope"
    ]
    assert scope_hints
    assert {claim.basis for claim in scope_hints} == {"structural_scope"}
    assert not any(
        issue.kind == "invalid_evidence_provenance"
        for issue in assessment.effect.issues
    )


def test_permission_class_aliases_are_unioned_and_conflict_checked() -> None:
    assessment = assess_tool_semantics(
        _tool(
            annotations={
                "shipgate_permission_classes": ["read"],
                "permission_classes": ["destructive"],
            },
            auth=AuthInfo(source="mcp", mode="none", explicit=True),
        )
    )

    assert assessment.conservative_effect == "destructive"
    assert assessment.effect.status == "conflicting"
    assert assessment.pass_eligible is False


def test_malformed_boolean_annotation_is_a_semantic_gap() -> None:
    assessment = assess_tool_semantics(
        _tool(
            annotations={"readOnlyHint": "true"},
            auth=AuthInfo(source="mcp", mode="none", explicit=True),
        )
    )

    assert assessment.pass_eligible is False
    assert "invalid_semantic_annotation" in {issue.kind for issue in assessment.effect.issues}


def test_heuristic_effect_only_cannot_qualify_a_pass() -> None:
    assessment = assess_tool_semantics(
        _tool(
            source_type="sdk_function",
            risk_hints=[
                ToolRiskHint(
                    tag="write",
                    source="sdk_keyword",
                    confidence="medium",
                )
            ],
            auth=AuthInfo(source="sdk_static", mode="none", explicit=True),
        )
    )

    assert assessment.conservative_effect == "write"
    assert assessment.effect.status == "inferred"
    assert assessment.pass_eligible is False


def test_manual_positive_risk_refines_structural_effect() -> None:
    assessment = assess_tool_semantics(
        _tool(
            source_type="openapi",
            annotations={"httpMethod": "POST"},
            risk_hints=[
                ToolRiskHint(
                    tag="financial_action",
                    source="manual",
                    confidence="high",
                    basis="reviewed_declaration",
                    provenance_kind="static_declaration",
                )
            ],
            auth=AuthInfo(source="openapi", mode="none", explicit=True),
        )
    )

    assert assessment.conservative_effect == "financial_write"
    assert assessment.effect.status == "structural"
    assert assessment.pass_eligible is True


def test_manual_positive_risk_alone_cannot_close_effect_gap() -> None:
    assessment = assess_tool_semantics(
        _tool(
            source_type="sdk_function",
            risk_hints=[
                ToolRiskHint(
                    tag="financial_action",
                    source="manual",
                    confidence="high",
                    basis="reviewed_declaration",
                    provenance_kind="static_declaration",
                )
            ],
            auth=AuthInfo(source="sdk_static", mode="none", explicit=True),
        )
    )

    assert assessment.conservative_effect == "financial_write"
    assert assessment.effect.status == "inferred"
    assert assessment.pass_eligible is False


def test_topical_auth_scope_is_not_authoritative_financial_evidence() -> None:
    assessment = assess_tool_semantics(
        _tool(
            source_type="openapi",
            risk_hints=[
                ToolRiskHint(
                    tag="financial_action",
                    source="auth_scope",
                    confidence="high",
                    basis="inferred_keyword",
                    provenance_kind="keyword_heuristic",
                    evidence={"scope": "payments:refund:write"},
                )
            ],
            auth=AuthInfo(
                type="oauth2",
                scopes=["payments:refund:write"],
                source="openapi",
                mode="scoped",
                explicit=True,
            ),
        )
    )

    financial = next(
        claim for claim in assessment.effect.claims if claim.value == "financial_write"
    )
    write = next(claim for claim in assessment.effect.claims if claim.value == "write")
    assert financial.basis == "inferred_keyword"
    assert financial.policy_eligible is False
    assert financial.provenance_kind == "keyword_heuristic"
    assert write.basis == "structural_scope"
    assert write.policy_eligible is True
    assert assessment.conservative_effect == "financial_write"
    assert assessment.effect.status == "inferred"


def test_heuristic_claim_cannot_conflict_with_reviewed_effect() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
        }
    )
    assessment = assess_tool_semantics(
        _tool(
            source_type="langchain_inventory",
            risk_hints=[
                ToolRiskHint(
                    tag="destructive",
                    source="misleading_keyword",
                    confidence="high",
                    basis="inferred_keyword",
                    provenance_kind="keyword_heuristic",
                )
            ],
        ),
        declaration,
    )

    assert assessment.conservative_effect == "destructive"
    assert assessment.effect.status == "declared"
    assert "conflicting_effect_evidence" not in {
        issue.kind for issue in assessment.effect.issues
    }
    # ...but it is no longer silent: a heuristic cannot *drive* the verdict and
    # cannot raise the blocking conflict, and it can now *challenge* the
    # assertion it disagrees with (#409).
    assert "declaration_below_inferred_evidence" in {
        issue.kind for issue in assessment.effect.issues
    }


def test_untyped_risk_hint_fails_closed() -> None:
    assessment = assess_tool_semantics(
        _tool(
            source_type="langchain_inventory",
            risk_hints=[
                ToolRiskHint(
                    tag="write",
                    source="third_party_magic",
                    confidence="high",
                )
            ],
            auth=AuthInfo(source="inventory", mode="none", explicit=True),
        )
    )

    assert "invalid_evidence_provenance" in {
        issue.kind for issue in assessment.effect.issues
    }
    assert assessment.pass_eligible is False


def test_ast_framework_declaration_cannot_replace_reviewed_inventory() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "write",
            "authority": {"mode": "none"},
        }
    )

    assessment = assess_tool_semantics(
        _tool(source_type="sdk_function"),
        declaration,
    )

    assert assessment.effect.status == "declared"
    assert assessment.authority.status == "declared"
    assert assessment.pass_eligible is False
    assert "incomplete_surface" in {issue.kind for issue in assessment.effect.issues}


def test_an_ast_adapter_that_proved_the_surface_closes_the_surface_gap() -> None:
    """#393: an AST source type is a question, not a verdict.

    Membership in ``_AST_ONLY_SOURCE_TYPES`` used to disqualify a tool outright,
    which made ``incomplete_surface`` a constant for every repository on a
    supported Python framework and left a reviewed inventory as the only exit.
    An adapter that measured completeness may now answer for itself.
    """

    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "write",
            "authority": {"mode": "none"},
        }
    )

    assessment = assess_tool_semantics(
        _tool(
            source_type="google_adk_function",
            extraction={
                "method": "google_adk_python_ast",
                "confidence": "high",
                "surface": "enumerated",
                "surface_gaps": [],
            },
        ),
        declaration,
    )

    assert assessment.pass_eligible is True
    assert "incomplete_surface" not in {
        issue.kind for issue in assessment.effect.issues
    }


@pytest.mark.parametrize(
    "extraction",
    [
        pytest.param({"method": "google_adk_python_ast"}, id="no-claim"),
        pytest.param(
            {
                "method": "google_adk_python_ast",
                "surface": "partial",
                "surface_gaps": ["dynamic_tools_expression"],
            },
            id="named-gap",
        ),
        pytest.param(
            {"method": "google_adk_python_ast", "surface": "Enumerated"},
            id="near-miss-spelling",
        ),
    ],
)
def test_an_ast_source_without_a_proof_stays_incomplete(extraction: dict) -> None:
    """Only the exact attestation clears the gap; silence never does.

    The dangerous shape here is the block-level "safe" signal that clears a
    path-wide guard. Absence of a claim — an adapter never taught to answer, a
    construct nobody classified, a value that does not match — has to read as
    incomplete, or the promotion this test guards becomes a fail-open.
    """

    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "write",
            "authority": {"mode": "none"},
        }
    )

    assessment = assess_tool_semantics(
        _tool(source_type="google_adk_function", extraction=extraction),
        declaration,
    )

    assert assessment.pass_eligible is False
    assert "incomplete_surface" in {issue.kind for issue in assessment.effect.issues}


def test_a_proven_surface_never_overrides_a_wildcard_exposure() -> None:
    """Wildcard exposure outranks any completeness claim about the same tool."""

    assessment = assess_tool_semantics(
        _tool(
            source_type="google_adk_function",
            annotations={"wildcard_tools": True},
            extraction={
                "method": "google_adk_python_ast",
                "confidence": "high",
                "surface": "enumerated",
            },
        )
    )

    assert assessment.pass_eligible is False
    assert "incomplete_surface" in {issue.kind for issue in assessment.effect.issues}


def test_reviewed_inventory_and_declaration_can_close_framework_gaps() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "write",
            "authority": {"mode": "none"},
        }
    )

    assessment = assess_tool_semantics(
        _tool(source_type="langchain_inventory"),
        declaration,
    )

    assert assessment.pass_eligible is True


def test_weaker_manifest_effect_conflicts_with_structural_effect() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
        }
    )
    assessment = assess_tool_semantics(
        _tool(annotations={"destructiveHint": True}),
        declaration,
    )

    assert assessment.conservative_effect == "destructive"
    assert assessment.effect.status == "conflicting"
    assert assessment.pass_eligible is False


def test_scoped_authority_declaration_cannot_drop_source_scope() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "write",
            "scopes": ["orders:read"],
            "authority": {"mode": "scoped", "auth_type": "oauth2"},
        }
    )
    assessment = assess_tool_semantics(
        _tool(
            auth=AuthInfo(
                type="oauth2",
                scopes=["orders:write"],
                source="openapi",
                mode="scoped",
                explicit=True,
            )
        ),
        declaration,
    )

    assert assessment.authority.status == "conflicting"
    assert assessment.pass_eligible is False


def test_scoped_authority_declaration_may_explicitly_broaden_source_scope() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "write",
            "scopes": ["orders:read", "orders:write"],
            "authority": {"mode": "scoped", "auth_type": "oauth2"},
        }
    )
    assessment = assess_tool_semantics(
        _tool(
            auth=AuthInfo(
                type="oauth2",
                scopes=["orders:read"],
                source="openapi",
                mode="scoped",
                explicit=True,
            )
        ),
        declaration,
    )

    assert assessment.authority.status == "declared"
    assert assessment.authority.scopes == ["orders:read", "orders:write"]
    assert assessment.pass_eligible is True


def test_authority_declaration_cannot_replace_source_credential_mode() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "write",
            "scopes": ["orders:write"],
            "authority": {
                "mode": "scoped",
                "auth_type": "oauth2",
                "credential_mode": "delegated",
            },
        }
    )
    assessment = assess_tool_semantics(
        _tool(
            auth=AuthInfo(
                type="oauth2",
                scopes=["orders:write"],
                credential_mode="service_account",
                source="openapi",
                mode="scoped",
                explicit=True,
            )
        ),
        declaration,
    )

    assert assessment.authority.status == "conflicting"
    assert assessment.pass_eligible is False


@pytest.mark.parametrize(
    ("authority", "scopes", "message"),
    [
        ({"mode": "none"}, ["orders:read"], "requires empty scopes"),
        ({"mode": "none", "auth_type": "oauth2"}, [], "requires no auth_type"),
        ({"mode": "scoped", "auth_type": "oauth2"}, [], "requires non-empty scopes"),
        ({"mode": "unscoped", "auth_type": "apiKey"}, [], "requires reason"),
        ({"mode": "ambient"}, [], "requires reason"),
        (
            {"mode": "scoped", "auth_type": "oauth2"},
            ["   "],
            "must contain concrete",
        ),
    ],
)
def test_action_authority_declaration_validation(
    authority: dict[str, str], scopes: list[str], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ActionDeclarationConfig.model_validate(
            {
                "tool": "process_order",
                "scopes": scopes,
                "authority": authority,
            }
        )


def test_attach_is_deterministic_and_does_not_mutate_input() -> None:
    original = _tool()
    first = attach_semantic_assessments([original])
    second = attach_semantic_assessments([original])

    assert original.semantic_assessment is None
    assert first[0].semantic_assessment == second[0].semantic_assessment
    # Attaching an immutable top-level assessment must not recursively copy
    # the already-owned nested tool graph on every scan.
    assert first[0].parameters is original.parameters
    assert first[0].annotations is original.annotations
    assert "semantic_assessment" not in first[0].model_dump(mode="json")


def test_attach_ignores_name_keyed_declaration_maps() -> None:
    original = _tool()
    declaration = ActionDeclarationConfig(
        tool=original.name,
        effect="destructive",
        authority={"mode": "none"},
    )

    assessed = attach_semantic_assessments(
        [original],
        {original.name: declaration},
    )[0]

    assert not any(
        claim.source == "action_surface_declaration"
        for claim in assessed.semantic_assessment.effect.claims
    )


def test_openapi_explicit_anonymous_authority_is_structural(tmp_path) -> None:
    (tmp_path / "api.yaml").write_text(
        """
openapi: 3.1.0
info: {title: Orders, version: '1.0'}
paths:
  /orders:
    get:
      operationId: list_orders
      security: []
      responses: {'200': {description: ok}}
""",
        encoding="utf-8",
    )
    loaded = load_openapi_tools(
        ToolSourceConfig(id="orders", type="openapi", path="api.yaml"),
        tmp_path,
    )

    tool = loaded.tools[0]
    assessment = assess_tool_semantics(tool)
    assert tool.auth.mode == "none"
    assert tool.auth.explicit is True
    assert assessment.effect.status == "structural"
    assert assessment.authority.mode == "none"
    assert assessment.pass_eligible is True


def test_openapi_preserves_ambiguous_security_alternatives(tmp_path) -> None:
    (tmp_path / "api.yaml").write_text(
        """
openapi: 3.1.0
info: {title: Orders, version: '1.0'}
components:
  securitySchemes:
    ordersOAuth:
      type: oauth2
      flows:
        clientCredentials:
          tokenUrl: https://auth.example.test/token
          scopes: {orders:read: Read orders}
paths:
  /orders:
    get:
      operationId: list_orders
      security:
        - {}
        - ordersOAuth: [orders:read]
      responses: {'200': {description: ok}}
""",
        encoding="utf-8",
    )
    loaded = load_openapi_tools(
        ToolSourceConfig(id="orders", type="openapi", path="api.yaml"),
        tmp_path,
    )

    tool = loaded.tools[0]
    assessment = assess_tool_semantics(tool)
    assert len(tool.auth.alternatives) == 2
    assert tool.auth.alternatives[0].anonymous is True
    assert assessment.authority.status == "partial"
    assert assessment.authority.mode == "unknown"
    assert assessment.pass_eligible is False

    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "list_orders",
            "effect": "read",
            "scopes": ["orders:read"],
            "authority": {"mode": "scoped", "auth_type": "oauth2"},
        }
    )
    declared = assess_tool_semantics(tool, declaration)
    assert declared.authority.status == "partial"
    assert declared.authority.mode == "unknown"
    assert declared.pass_eligible is False


def test_mcp_preserves_explicit_authority_none(tmp_path) -> None:
    (tmp_path / "tools.json").write_text(
        """
{"tools":[{"name":"list_orders","annotations":{"readOnlyHint":true},
"auth":{"mode":"none"},"inputSchema":{"type":"object"}}]}
""",
        encoding="utf-8",
    )
    loaded = load_mcp_tools(
        ToolSourceConfig(id="orders", type="mcp", path="tools.json"),
        tmp_path,
    )

    tool = loaded.tools[0]
    assessment = assess_tool_semantics(tool)
    assert tool.auth.explicit is True
    assert tool.auth.mode == "none"
    assert assessment.pass_eligible is True


@pytest.mark.parametrize(
    "raw_auth",
    [
        {"mode": "scpoed", "type": "oauth2", "scopes": ["orders:read"]},
        {"mode": "none", "required": True},
        {"mode": "scoped", "type": "oauth2", "scopes": ["   "]},
        {"mode": "scoped", "type": "oauth2", "scopes": "orders:read"},
    ],
)
def test_mcp_malformed_authority_cannot_be_normalized_into_pass(tmp_path, raw_auth) -> None:
    (tmp_path / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "list_orders",
                        "annotations": {"readOnlyHint": True},
                        "auth": raw_auth,
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = load_mcp_tools(
        ToolSourceConfig(id="orders", type="mcp", path="tools.json"),
        tmp_path,
    )

    assessment = assess_tool_semantics(loaded.tools[0])
    assert assessment.pass_eligible is False
    assert "invalid_semantic_annotation" in {issue.kind for issue in assessment.authority.issues}


def _keyword_hint(tag: str, confidence: str = "high") -> ToolRiskHint:
    return ToolRiskHint(
        tag=tag,
        source="name",
        confidence=confidence,
        basis="inferred_keyword",
        provenance_kind="keyword_heuristic",
    )


def _heuristic_tool() -> Tool:
    """A tool whose only effect evidence is a name heuristic."""

    return _tool(
        source_type="langchain_inventory",
        risk_hints=[_keyword_hint("external_write")],
        auth=AuthInfo(source="inventory", mode="none", explicit=True),
    )


def test_declaration_below_heuristic_evidence_is_flagged() -> None:
    """#409: `read` under an `external_write` hint is accepted, never silent."""

    declaration = ActionDeclarationConfig.model_validate(
        {"tool": "process_order", "effect": "read", "authority": {"mode": "none"}}
    )

    assessment = assess_tool_semantics(_heuristic_tool(), declaration)

    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )
    assert "'read'" in issue.message
    assert "'external_communication'" in issue.message
    assert issue.source_pointer == "action_surface.actions[tool='process_order'].effect"
    # The declaration remains the operative statement — a heuristic still
    # cannot drive a verdict (#357) — but it is no longer an evidence-backed
    # pass on its own.
    assert assessment.effect.status == "declared"
    assert assessment.conservative_effect == "external_communication"
    assert assessment.pass_eligible is False


def test_acknowledged_override_restores_pass_eligibility_and_records_itself() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
            "override": {
                "evidence": "the handler returns a cached row",
                "reason": "no outbound client is constructed",
            },
        }
    )

    assessment = assess_tool_semantics(_heuristic_tool(), declaration)

    assert not assessment.effect.issues
    assert assessment.pass_eligible is True
    claim = next(
        item
        for item in assessment.effect.claims
        if item.source == "action_surface_declaration_override"
    )
    assert claim.value == "read"
    assert claim.evidence["overridden_effect"] == "external_communication"
    assert claim.evidence["overridden_sources"] == ["risk_hint:name"]
    assert claim.evidence["reason"] == "no outbound client is constructed"


def test_escalation_past_heuristic_evidence_stays_silent() -> None:
    """Monotone: adding or escalating relative to evidence needs no ceremony."""

    declaration = ActionDeclarationConfig.model_validate(
        {"tool": "process_order", "effect": "destructive", "authority": {"mode": "none"}}
    )

    assessment = assess_tool_semantics(_heuristic_tool(), declaration)

    assert assessment.effect.issues == []
    assert assessment.pass_eligible is True


def test_declaration_equal_to_heuristic_evidence_stays_silent() -> None:
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "external_communication",
            "authority": {"mode": "none"},
        }
    )

    assessment = assess_tool_semantics(_heuristic_tool(), declaration)

    assert assessment.effect.issues == []
    assert assessment.pass_eligible is True


def test_source_corroborated_declaration_is_still_challenged_and_says_so() -> None:
    """Corroboration is named in the row, not treated as an exemption.

    ``support.search_kb`` declares ``read`` and carries ``readOnlyHint: true``,
    and a keyword reading ``financial_write`` out of the word "refund" in its
    description is the weaker signal. Exempting that pair looks tempting, but
    this resolver already refuses to pass on the annotation alone: with no
    declaration the same tool is ``inferred_effect_only`` and not
    pass-eligible. A declaration that merely restates the annotation must not
    buy what the annotation could not, or #409's hole simply moves. Naming the
    corroboration is what makes the row a one-line answer instead.
    """

    tool = _tool(
        annotations={"readOnlyHint": True},
        risk_hints=[_keyword_hint("financial_action")],
        auth=AuthInfo(source="mcp", mode="none", explicit=True),
    )

    undeclared = assess_tool_semantics(tool)
    assert "inferred_effect_only" in {issue.kind for issue in undeclared.effect.issues}
    assert undeclared.pass_eligible is False

    declaration = ActionDeclarationConfig.model_validate(
        {"tool": "process_order", "effect": "read", "authority": {"mode": "none"}}
    )
    assessment = assess_tool_semantics(tool, declaration)

    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "declaration_below_inferred_evidence"
    )
    assert "source evidence agrees with the declaration (mcp_annotation)" in issue.message
    assert assessment.pass_eligible is False


def test_source_annotations_do_not_buy_an_exemption_they_did_not_earn() -> None:
    """#268's boundary: a tool source may not self-certify past a challenge.

    ``readOnlyHint`` is content the tool source supplies about itself and is not
    conditioned on ``tool_sources[].trust``. If corroboration exempted the row,
    an MCP server would only have to assert ``readOnlyHint: true`` for a
    ``read`` declaration to become pass-eligible on a tool the scanner reads as
    destructive.
    """

    declaration = ActionDeclarationConfig.model_validate(
        {"tool": "process_order", "effect": "read", "authority": {"mode": "none"}}
    )

    assessment = assess_tool_semantics(
        _tool(
            annotations={"readOnlyHint": True},
            risk_hints=[_keyword_hint("destructive")],
            auth=AuthInfo(source="mcp", mode="none", explicit=True),
        ),
        declaration,
    )

    assert "declaration_below_inferred_evidence" in {
        issue.kind for issue in assessment.effect.issues
    }
    assert assessment.pass_eligible is False
    assert assessment.conservative_effect == "destructive"


def test_declarations_own_risk_tags_cannot_corroborate_its_own_effect() -> None:
    """Self-declaration is not evidence — the manifest may not close its own gap."""

    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "write",
            "risk_tags": ["write"],
            "authority": {"mode": "none"},
        }
    )

    assessment = assess_tool_semantics(
        _tool(
            source_type="langchain_inventory",
            risk_hints=[_keyword_hint("destructive")],
            auth=AuthInfo(source="inventory", mode="none", explicit=True),
        ),
        declaration,
    )

    assert "declaration_below_inferred_evidence" in {
        issue.kind for issue in assessment.effect.issues
    }


def test_override_cannot_silence_policy_eligible_contradiction() -> None:
    """An override acknowledges a heuristic. It never overrules proven evidence."""

    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
            "override": {
                "evidence": "I looked at the annotation",
                "reason": "I disagree with it",
            },
        }
    )

    assessment = assess_tool_semantics(
        _tool(annotations={"destructiveHint": True}),
        declaration,
    )

    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "conflicting_effect_evidence"
    )
    assert assessment.effect.status == "conflicting"
    assert assessment.pass_eligible is False
    assert not [
        claim
        for claim in assessment.effect.claims
        if claim.source == "action_surface_declaration_override"
    ]
    # …and the reviewer is told the block they wrote does not reach this
    # conflict, rather than re-running against an unchanged message.
    assert "the declared override does not apply" in issue.message


def test_an_acknowledgement_can_never_cover_policy_eligible_evidence() -> None:
    """The safety property behind consuming acknowledgements downstream.

    Policy applicability, action policies, and capability policies all drop the
    claims an override names. That is only safe because an override can never
    name a policy-eligible claim: the resolver refuses to attach one while
    policy-eligible evidence outranks the declaration, and the set it records is
    filtered on ``not policy_eligible``. Asserted here rather than at each
    consumer, because it is one property of the producer.
    """

    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
            "override": {"evidence": "checked the handler", "reason": "returns a row"},
        }
    )

    assessment = assess_tool_semantics(
        _tool(
            annotations={"readOnlyHint": True},
            risk_hints=[_keyword_hint("destructive"), _keyword_hint("write")],
            auth=AuthInfo(source="mcp", mode="none", explicit=True),
        ),
        declaration,
    )

    acknowledged = acknowledged_effect_claim_ids(assessment.effect.claims)
    assert acknowledged
    by_id = {claim.claim_id: claim for claim in assessment.effect.claims}
    for claim_id in acknowledged:
        claim = by_id[claim_id]
        assert claim.policy_eligible is False, claim
        assert claim.source not in {
            "action_surface_declaration",
            "action_surface_declaration_override",
        }


def test_the_read_versus_side_effect_conflict_also_names_an_ignored_override() -> None:
    """Both conflicting branches owe the note; it is the same user error."""

    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
            "override": {"evidence": "I read the annotations", "reason": "I disagree"},
        }
    )

    assessment = assess_tool_semantics(
        _tool(annotations={"readOnlyHint": True, "destructiveHint": True}),
        declaration,
    )

    issue = next(
        item
        for item in assessment.effect.issues
        if item.kind == "conflicting_effect_evidence"
    )
    assert "read and side-effect evidence conflict" in issue.message
    assert "the declared override does not apply here" in issue.message
    assert assessment.pass_eligible is False


def test_an_override_with_nothing_to_acknowledge_is_accepted_silently() -> None:
    """Pinned decision, not an accident.

    An override whose inferred evidence has since stopped firing stays
    accepted: nothing is asserted against, so nothing is owed. Telling the two
    apart — a reviewer's exception that went stale versus one that never
    applied — needs the ``basis: confirmed:<derivation_id>`` pin from increment
    4 of the RFC, which is where that question belongs.
    """

    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
            "override": {"evidence": "checked the handler", "reason": "returns a row"},
        }
    )

    assessment = assess_tool_semantics(
        _tool(annotations={"readOnlyHint": True}),
        declaration,
    )

    assert assessment.effect.issues == []
    assert assessment.pass_eligible is True
    assert not [
        claim
        for claim in assessment.effect.claims
        if claim.source == "action_surface_declaration_override"
    ]


def test_override_requires_a_declared_effect_to_acknowledge() -> None:
    with pytest.raises(ValidationError):
        ActionDeclarationConfig.model_validate(
            {
                "tool": "process_order",
                "override": {"evidence": "checked", "reason": "fine"},
            }
        )


@pytest.mark.parametrize("field", ["evidence", "reason"])
def test_override_rejects_a_blank_answer(field: str) -> None:
    payload = {"evidence": "checked the handler", "reason": "returns a cached row"}
    payload[field] = "   "
    with pytest.raises(ValidationError):
        ActionDeclarationConfig.model_validate(
            {"tool": "process_order", "effect": "read", "override": payload}
        )
