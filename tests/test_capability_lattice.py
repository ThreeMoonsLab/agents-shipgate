from __future__ import annotations

from agents_shipgate.core.capability_lattice import (
    classify_tool_permission,
    mcp_permission_risk_hints,
)
from agents_shipgate.core.domain import AuthInfo, Tool


def _tool(
    name: str,
    *,
    annotations: dict[str, object] | None = None,
    scopes: list[str] | None = None,
) -> Tool:
    return Tool(
        id=f"tool:{name}",
        name=name,
        source_type="mcp",
        annotations=annotations or {},
        auth=AuthInfo(scopes=scopes or []),
        extraction_confidence="high",
    )


def test_lattice_classifies_read_write_and_destructive() -> None:
    assert classify_tool_permission(
        _tool("read_docs", annotations={"readOnlyHint": True})
    ).classes == ("read",)
    assert "write" in classify_tool_permission(_tool("write_file")).classes
    assert "destructive" in classify_tool_permission(_tool("delete_record")).classes


def test_lattice_maps_external_financial_and_production() -> None:
    external = classify_tool_permission(_tool("send_email"))
    financial = classify_tool_permission(_tool("create_refund"))
    production = classify_tool_permission(_tool("deploy_cluster"))

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


def test_lattice_modifiers_raise_risk_score() -> None:
    plain = classify_tool_permission(_tool("write_file"))
    approved = classify_tool_permission(
        _tool("write_file", annotations={"mcp_approval_mode": "approve"})
    )

    assert approved.risk_score > plain.risk_score


def test_lattice_preserves_ordered_name_prefix_for_read_tools() -> None:
    profile = classify_tool_permission(_tool("read_deployments"))

    assert profile.classes == ("read",)
    assert "name_description" in profile.reasons


def test_read_only_hint_confidence_distinguishes_explicit_from_inferred() -> None:
    inferred = next(
        hint
        for hint in mcp_permission_risk_hints(_tool("read_docs"))
        if hint.tag == "read_only"
    )
    explicit = next(
        hint
        for hint in mcp_permission_risk_hints(
            _tool("read_docs", annotations={"readOnlyHint": True})
        )
        if hint.tag == "read_only"
    )

    assert inferred.confidence == "medium"
    assert explicit.confidence == "high"
