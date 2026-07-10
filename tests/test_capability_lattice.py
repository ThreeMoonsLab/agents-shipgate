from __future__ import annotations

from agents_shipgate.core.capability_lattice import (
    classify_tool_permission,
    mcp_permission_risk_hints,
)
from agents_shipgate.core.domain import AuthInfo, Tool, ToolRiskHint


def _tool(
    name: str,
    *,
    annotations: dict[str, object] | None = None,
    scopes: list[str] | None = None,
    risk_hints: list[ToolRiskHint] | None = None,
) -> Tool:
    return Tool(
        id=f"tool:{name}",
        name=name,
        source_type="mcp",
        annotations=annotations or {},
        auth=AuthInfo(scopes=scopes or []),
        risk_hints=risk_hints or [],
        extraction_confidence="high",
    )


def test_lattice_classifies_read_write_and_destructive() -> None:
    assert classify_tool_permission(
        _tool("read_docs", annotations={"readOnlyHint": True})
    ).classes == ("read",)
    assert (
        "write"
        in classify_tool_permission(
            _tool("write_file", annotations={"permission_classes": ["write"]})
        ).classes
    )
    assert (
        "destructive"
        in classify_tool_permission(
            _tool(
                "delete_record",
                annotations={"permission_classes": ["destructive"]},
            )
        ).classes
    )


def test_lattice_maps_external_financial_and_production() -> None:
    external = classify_tool_permission(
        _tool("send_email", annotations={"permission_classes": ["external"]})
    )
    financial = classify_tool_permission(
        _tool("create_refund", annotations={"permission_classes": ["financial"]})
    )
    production = classify_tool_permission(
        _tool("deploy_cluster", annotations={"permission_classes": ["production"]})
    )

    assert external.effect == "external_communication"
    assert financial.effect == "financial_write"
    assert production.effect == "production_operation"
    assert financial.risk_level == "critical"


def test_lattice_unknown_side_effect_fails_closed() -> None:
    profile = classify_tool_permission(
        _tool("custom_tool", annotations={"mcp_unknown_schema": True})
    )

    assert profile.side_effect_unknown is True
    assert "unknown" in profile.classes
    assert profile.risk_level in {"high", "critical"}


def test_lattice_neutral_mcp_tool_has_unknown_effect_not_default_read() -> None:
    profile = classify_tool_permission(_tool("process_order"))

    assert profile.classes == ("unknown",)
    assert profile.effect == "write"
    assert profile.side_effect_unknown is True
    assert "missing_effect_evidence" in profile.reasons


def test_lattice_modifiers_raise_risk_score() -> None:
    plain = classify_tool_permission(_tool("write_file"))
    approved = classify_tool_permission(
        _tool("write_file", annotations={"mcp_approval_mode": "approve"})
    )

    assert approved.risk_score > plain.risk_score


def test_lattice_does_not_treat_a_read_name_as_safety_evidence() -> None:
    profile = classify_tool_permission(_tool("read_deployments"))

    assert profile.classes == ("unknown",)
    assert profile.effect == "write"
    assert profile.side_effect_unknown is True


def test_inferred_read_never_becomes_a_read_only_permission_hint() -> None:
    inferred = mcp_permission_risk_hints(
        _tool(
            "read_docs",
            risk_hints=[
                ToolRiskHint(
                    tag="read_only",
                    source="keyword",
                    confidence="medium",
                )
            ],
        )
    )
    explicit = next(
        hint
        for hint in mcp_permission_risk_hints(
            _tool("read_docs", annotations={"readOnlyHint": True})
        )
        if hint.tag == "read_only"
    )

    assert all(hint.tag != "read_only" for hint in inferred)
    assert explicit.confidence == "high"
