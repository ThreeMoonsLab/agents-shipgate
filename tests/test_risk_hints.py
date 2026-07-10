import pytest

from agents_shipgate.core.domain import (
    AuthInfo,
    Tool,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.risk_hints import (
    enrich_tools_with_risk_hints,
    has_risk_tag,
    is_effectively_read_only,
    is_high_risk_tool,
    is_write_tool,
)
from agents_shipgate.core.semantic_assessment import assess_tool_semantics
from agents_shipgate.schemas.manifest import (
    AgentConfig,
    AgentsShipgateManifest,
    EnvironmentConfig,
    ProjectConfig,
    ToolSourceConfig,
)


def _manifest() -> AgentsShipgateManifest:
    return AgentsShipgateManifest(
        version="0.1",
        project=ProjectConfig(name="test"),
        agent=AgentConfig(name="agent", declared_purpose=["test"]),
        environment=EnvironmentConfig(target="local"),
        tool_sources=[ToolSourceConfig(id="dummy", type="mcp", path="dummy.json")],
    )


def _tool(**kwargs) -> Tool:
    defaults = {
        "id": "tool:test",
        "name": "test",
        "source_type": "sdk_function",
        "auth": AuthInfo(),
    }
    defaults.update(kwargs)
    return Tool(**defaults)


def _enrich(tool: Tool) -> Tool:
    return enrich_tools_with_risk_hints(_manifest(), [tool])[0]


def test_hint_enrichment_does_not_run_semantic_resolver(monkeypatch):
    def unexpected_assessment(*args, **kwargs):
        raise AssertionError("hint enrichment must precede the central assessment")

    monkeypatch.setattr(
        "agents_shipgate.core.semantic_assessment.assess_tool_semantics",
        unexpected_assessment,
    )

    enriched = _enrich(
        _tool(
            name="send_customer_refund_message",
            description="Send a customer a message about a refund.",
        )
    )

    assert has_risk_tag(enriched, {"financial_action"})
    assert has_risk_tag(enriched, {"customer_communication"})


def test_sdk_keyword_classifier_tags_update_function_as_write():
    tool = _enrich(_tool(name="update_seat", description="Change a seat assignment."))

    assert is_write_tool(tool), f"expected write tag, got {[h.tag for h in tool.risk_hints]}"
    assert has_risk_tag(tool, {"write"}, min_confidence="medium")


def test_sdk_keyword_classifier_tags_get_function_as_read():
    tool = _enrich(_tool(name="get_user_profile", description="Look up a user."))

    assert has_risk_tag(tool, {"read_only"}, min_confidence="medium")
    assert not is_write_tool(tool)


def test_get_endpoint_with_infrastructure_keyword_stays_conservatively_uncertain():
    tool = _enrich(
        _tool(
            id="tool:get_v2_kubernetes_clusters",
            name="get_v2_kubernetes_clusters",
            description="List all Kubernetes clusters.",
            source_type="openapi",
            annotations={"httpMethod": "GET"},
        )
    )

    assert has_risk_tag(tool, {"infrastructure_change"}, min_confidence="medium")
    assert not is_effectively_read_only(tool)
    assessment = assess_tool_semantics(tool)
    assert assessment.conservative_effect == "production_operation"
    assert assessment.effect.status == "inferred"
    assert assessment.pass_eligible is False
    assert is_high_risk_tool(tool)


def test_financial_nouns_raise_a_structural_get_to_semantic_review():
    tool = _enrich(
        _tool(
            id="tool:get_billing_invoices",
            name="get_billing_invoices",
            description="List billing invoices and payment metadata.",
            source_type="openapi",
            annotations={"httpMethod": "GET"},
            auth=AuthInfo(scopes=["billing:invoices:read", "payments:read"]),
        )
    )

    assert not is_effectively_read_only(tool)
    assert is_write_tool(tool)
    assert is_high_risk_tool(tool)
    assert assess_tool_semantics(tool).effect.status == "inferred"


def test_mcp_read_only_hint_does_not_erase_inferred_financial_risk():
    tool = _enrich(
        _tool(
            id="tool:mcp_billing_invoices_list",
            name="billing.invoices.list",
            description="Read billing invoices for customer support review.",
            source_type="mcp",
            annotations={"readOnlyHint": True},
        )
    )

    assert not is_effectively_read_only(tool)
    assert is_write_tool(tool)
    assert is_high_risk_tool(tool)
    assert assess_tool_semantics(tool).effect.status == "inferred"


def test_read_only_hint_cannot_hide_structural_delete():
    tool = _enrich(
        _tool(
            id="tool:delete_record",
            name="delete_record",
            source_type="openapi",
            annotations={"httpMethod": "DELETE", "readOnlyHint": True},
        )
    )

    assert not is_effectively_read_only(tool)
    assert is_write_tool(tool)


def test_risk_override_cannot_remove_structural_method_evidence():
    payload = _manifest().model_dump(mode="python")
    payload["risk_overrides"] = {
        "tools": {
            "delete_record": {
                "remove_tags": ["destructive"],
                "reason": "attempt to hide a structural delete",
            }
        }
    }
    manifest = AgentsShipgateManifest.model_validate(payload)
    tool = _tool(
        id="tool:delete_record",
        name="delete_record",
        source_type="openapi",
        annotations={"httpMethod": "DELETE"},
    )

    with pytest.raises(ConfigError, match="keyword/regex hints only"):
        enrich_tools_with_risk_hints(manifest, [tool])


def test_deployments_token_does_not_match_deploy_keyword():
    tool = _enrich(
        _tool(
            id="tool:get_v2_apps_app_id_deployments",
            name="get_v2_apps_app_id_deployments",
            description="List app deployments.",
            source_type="openapi",
            annotations={"httpMethod": "GET"},
        )
    )

    assert not has_risk_tag(tool, {"infrastructure_change"}), (
        "GET listing 'deployments' should not be tagged infrastructure_change"
    )


def test_deploy_token_does_match_deploy_keyword():
    tool = _enrich(
        _tool(
            id="tool:post_v2_apps_app_id_deploy",
            name="post_v2_apps_app_id_deploy",
            description="Trigger a deploy.",
            source_type="openapi",
            annotations={"httpMethod": "POST"},
        )
    )

    assert has_risk_tag(tool, {"infrastructure_change"})


def test_financial_plural_scope_still_matches():
    tool = _enrich(
        _tool(
            id="tool:create_refund",
            name="create_refund",
            description="Issue a refund.",
            source_type="openapi",
            annotations={"httpMethod": "POST"},
            auth=AuthInfo(scopes=["stripe:refunds:write"]),
        )
    )

    assert has_risk_tag(tool, {"financial_action"}, min_confidence="medium")


def test_email_token_inside_send_email_still_matches():
    tool = _enrich(
        _tool(
            id="tool:send_email",
            name="send_customer_email",
            description="Send a customer email notification.",
            source_type="openapi",
            annotations={"httpMethod": "POST"},
        )
    )

    assert has_risk_tag(tool, {"customer_communication"})
    assert has_risk_tag(tool, {"external_write"})


def test_interview_does_not_falsely_match_view():
    tool = _enrich(
        _tool(
            id="tool:interview_log",
            name="interview_log",
            description="Log interview transcripts.",
            source_type="sdk_function",
        )
    )

    read_only_keyword_hints = [
        hint for hint in tool.risk_hints if hint.tag == "read_only" and hint.source == "sdk_keyword"
    ]
    assert not read_only_keyword_hints
