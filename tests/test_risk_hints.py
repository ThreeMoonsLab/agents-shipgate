from agents_shipgate.config.schema import (
    AgentConfig,
    AgentsShipgateManifest,
    EnvironmentConfig,
    ProjectConfig,
    ToolSourceConfig,
)
from agents_shipgate.core.models import AuthInfo, Tool
from agents_shipgate.core.risk_hints import (
    enrich_tools_with_risk_hints,
    has_risk_tag,
    is_effectively_read_only,
    is_write_tool,
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


def test_sdk_keyword_classifier_tags_update_function_as_write():
    tool = _enrich(_tool(name="update_seat", description="Change a seat assignment."))

    assert is_write_tool(tool), f"expected write tag, got {[h.tag for h in tool.risk_hints]}"
    assert has_risk_tag(tool, {"write"}, min_confidence="medium")


def test_sdk_keyword_classifier_tags_get_function_as_read():
    tool = _enrich(_tool(name="get_user_profile", description="Look up a user."))

    assert has_risk_tag(tool, {"read_only"}, min_confidence="medium")
    assert not is_write_tool(tool)


def test_get_endpoint_with_infrastructure_keyword_is_effectively_read_only():
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
    assert is_effectively_read_only(tool), (
        "GET listing should be effectively read-only despite the infra keyword"
    )


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
        hint
        for hint in tool.risk_hints
        if hint.tag == "read_only" and hint.source == "sdk_keyword"
    ]
    assert not read_only_keyword_hints
