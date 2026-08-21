from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agents_shipgate.core.capability_lattice import mcp_permission_risk_hints
from agents_shipgate.core.domain import AuthInfo, Tool, ToolRiskHint
from agents_shipgate.core.semantic_assessment import (
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
