from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

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
                )
            ],
            auth=AuthInfo(source="sdk_static", mode="none", explicit=True),
        )
    )

    assert assessment.conservative_effect == "financial_write"
    assert assessment.effect.status == "inferred"
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
    assert "semantic_assessment" not in first[0].model_dump(mode="json")


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
