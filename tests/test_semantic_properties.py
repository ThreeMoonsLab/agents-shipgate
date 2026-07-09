from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from agents_shipgate.core.domain import AuthInfo, Tool, ToolRiskHint
from agents_shipgate.core.semantic_assessment import assess_tool_semantics
from agents_shipgate.schemas.manifest import ActionDeclarationConfig

_EFFECT_RANK = {
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

_POSITIVE_TAGS = {
    "write": "write",
    "sensitive_data_access": "privileged_data_access",
    "code_execution": "code_execution",
    "infrastructure_change": "production_operation",
    "external_write": "external_communication",
    "financial_action": "financial_write",
    "destructive": "destructive",
}


def _tool(**updates: object) -> Tool:
    values: dict[str, object] = {
        "id": "tool:operation",
        "name": "operation",
        "source_type": "openapi",
        "source_id": "api",
        "source_pointer": "/paths/~1operation/post",
        "annotations": {"httpMethod": "POST"},
        "auth": AuthInfo(source="openapi", mode="none", explicit=True),
        "extraction_confidence": "high",
        "extraction": {"method": "openapi", "confidence": "high"},
    }
    values.update(updates)
    return Tool.model_validate(values)


@settings(max_examples=64, derandomize=True, deadline=None)
@given(st.lists(st.sampled_from(sorted(_POSITIVE_TAGS)), unique=True, max_size=7))
def test_adding_positive_effect_evidence_never_lowers_conservative_effect(
    tags: list[str],
) -> None:
    base = assess_tool_semantics(_tool())
    enriched = assess_tool_semantics(
        _tool(
            risk_hints=[
                ToolRiskHint(tag=tag, source="manual", confidence="high") for tag in tags
            ]
        )
    )

    assert _EFFECT_RANK[enriched.conservative_effect] >= _EFFECT_RANK[
        base.conservative_effect
    ]
    if tags:
        expected = max(
            [base.conservative_effect, *(_POSITIVE_TAGS[tag] for tag in tags)],
            key=_EFFECT_RANK.__getitem__,
        )
        assert enriched.conservative_effect == expected


@settings(max_examples=32, derandomize=True, deadline=None)
@given(st.sampled_from(["GET", "POST", "DELETE"]))
def test_removing_structural_effect_evidence_never_improves_pass_eligibility(
    method: str,
) -> None:
    complete = assess_tool_semantics(_tool(annotations={"httpMethod": method}))
    removed = assess_tool_semantics(_tool(annotations={}))

    assert complete.pass_eligible is True
    assert removed.pass_eligible is False


@settings(max_examples=32, derandomize=True, deadline=None)
@given(st.sampled_from(["unscoped", "ambient"]))
def test_broadening_authority_never_promotes_pass_eligibility(mode: str) -> None:
    narrow = assess_tool_semantics(_tool())
    authority: dict[str, str] = {
        "mode": mode,
        "reason": "reviewed provider grant has no enumerable scopes",
    }
    if mode == "unscoped":
        authority["auth_type"] = "api_key"
    declaration = ActionDeclarationConfig.model_validate(
        {
            "tool": "operation",
            "effect": "write",
            "authority": authority,
        }
    )
    broadened = assess_tool_semantics(
        _tool(auth=AuthInfo(source="openapi", explicit=False)),
        declaration,
    )

    assert narrow.pass_eligible is True
    assert broadened.pass_eligible is False
    assert broadened.authority.mode == mode


@settings(max_examples=64, derandomize=True, deadline=None)
@given(
    st.lists(st.sampled_from(sorted(_POSITIVE_TAGS)), unique=True, max_size=7),
    st.lists(
        st.sampled_from(["orders:read", "orders:write", "email:send"]),
        unique=True,
        min_size=1,
        max_size=3,
    ),
)
def test_reordering_inputs_and_repeated_execution_are_byte_identical(
    tags: list[str],
    scopes: list[str],
) -> None:
    def assessment(tag_order: list[str], scope_order: list[str]) -> str:
        value = assess_tool_semantics(
            _tool(
                risk_hints=[
                    ToolRiskHint(tag=tag, source="manual", confidence="high")
                    for tag in tag_order
                ],
                auth=AuthInfo(
                    type="oauth2",
                    scopes=scope_order,
                    source="openapi",
                    mode="scoped",
                    explicit=True,
                ),
            )
        )
        return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    first = assessment(tags, scopes)
    repeated = assessment(tags, scopes)
    reordered = assessment(list(reversed(tags)), list(reversed(scopes)))

    assert first == repeated == reordered
