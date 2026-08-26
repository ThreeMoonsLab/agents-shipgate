from __future__ import annotations

import json

from agents_shipgate.core.capabilities import (
    build_capability_facts,
    capability_fact_from_action,
)
from agents_shipgate.core.domain import (
    AuthInfo,
    Scope,
    Tool,
    ToolParameter,
    ToolRiskHint,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.lenses.action_surface import action_to_fact, build_action
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import ReadinessReport


def _manifest(**updates: object) -> AgentsShipgateManifest:
    data: dict[str, object] = {
        "version": "0.1",
        "project": {"name": "capability-domain"},
        "agent": {
            "name": "support-agent",
            "declared_purpose": ["Support customer account workflows."],
        },
        "environment": {"target": "local"},
        "tool_sources": [
            {
                "id": "support_api",
                "type": "openapi",
                "path": "tools/support.openapi.yaml",
            }
        ],
    }
    data.update(updates)
    return AgentsShipgateManifest.model_validate(data)


def _tool(
    name: str,
    *,
    source_type: str = "openapi",
    source_id: str | None = "support_api",
    annotations: dict[str, object] | None = None,
    scopes: list[str] | None = None,
    hints: list[tuple[str, str]] | None = None,
    parameters: list[ToolParameter] | None = None,
    input_schema: dict[str, object] | None = None,
    source_path: str | None = "tools/support.openapi.yaml",
    source_start_line: int | None = 12,
) -> Tool:
    return Tool(
        id=f"tool:{name}",
        name=name,
        description=f"{name} test tool",
        source_type=source_type,
        source_id=source_id,
        source_path=source_path,
        source_start_line=source_start_line,
        source_pointer=f"/paths/{name}",
        annotations=annotations or {},
        input_schema=input_schema or {},
        auth=AuthInfo(
            type="oauth",
            credential_mode="delegated",
            source="manifest",
            scopes=scopes or [],
        ),
        risk_hints=[
            ToolRiskHint(tag=tag, source="test", confidence=confidence)
            for tag, confidence in (hints or [])
        ],
        parameters=parameters or [],
        owner="support-platform",
        extraction_confidence="high",
    )


def _dump_facts(tools: list[Tool]) -> str:
    facts = build_capability_facts(_manifest(), agent_id="agent:one", tools=tools)
    return json.dumps(
        [fact.model_dump(mode="json") for fact in facts],
        sort_keys=True,
    )


def test_capability_facts_are_deterministic_for_repeated_builds() -> None:
    tool = _tool(
        "stripe.create_refund",
        annotations={"httpMethod": "POST"},
        scopes=["stripe:refunds:write"],
        hints=[("write", "high"), ("financial_action", "high")],
        parameters=[ToolParameter(name="amount", type="number", required=True)],
    )

    first = _dump_facts([tool])
    second = _dump_facts([tool.model_copy(deep=True)])

    assert first == second
    assert '"id": "cap_' in first


def test_scope_and_broad_scope_facts_reuse_scope_parser() -> None:
    fact = build_capability_facts(
        _manifest(),
        agent_id="agent:one",
        tools=[
            _tool(
                "stripe.refund_admin",
                scopes=["stripe:*", "stripe:refunds:write"],
                hints=[("write", "high")],
            )
        ],
    )[0]

    assert fact.identity.scope == ("stripe:*", "stripe:refunds:write")
    assert fact.authority.scopes == ("stripe:*", "stripe:refunds:write")
    assert fact.authority.broad_scopes == tuple(
        scope for scope in fact.authority.scopes if Scope.parse(scope).is_broad()
    )


def test_source_location_does_not_change_capability_identity() -> None:
    manifest = _manifest()
    original = _tool(
        "cases.list",
        annotations={"httpMethod": "GET"},
        scopes=["cases:read"],
        source_path="tools/one.yaml",
        source_start_line=10,
    )
    moved = _tool(
        "cases.list",
        annotations={"httpMethod": "GET"},
        scopes=["cases:read"],
        source_path="tools/two.yaml",
        source_start_line=200,
    )

    first = build_capability_facts(manifest, agent_id="agent:one", tools=[original])[0]
    second = build_capability_facts(manifest, agent_id="agent:one", tools=[moved])[0]

    assert first.id == second.id
    assert first.hashes.identity_hash == second.hashes.identity_hash
    assert first.evidence.source_path != second.evidence.source_path
    assert first.evidence.source_start_line != second.evidence.source_start_line


def test_effect_classification_matches_typed_action_side_effect() -> None:
    manifest = _manifest()
    tool = _tool(
        "stripe.create_refund",
        annotations={"httpMethod": "POST"},
        scopes=["stripe:refunds:write"],
        hints=[("write", "high"), ("financial_action", "high")],
    )
    action = build_action(manifest, agent_id="agent:one", tool=tool, declaration=None)
    fact = capability_fact_from_action(action, tool)

    assert fact.effect.effect == action.effect
    assert fact.effect.high_risk == action.side_effect.is_high_risk
    assert fact.effect.financial is True
    assert fact.effect.externally_visible is True


def test_controls_map_from_action_and_manifest_confirmation_policy() -> None:
    manifest = _manifest(
        policies={
            "require_approval_for_tools": ["messaging.send_customer_email"],
            "require_confirmation_for_tools": ["messaging.send_customer_email"],
            "require_idempotency_for_tools": ["messaging.send_customer_email"],
        },
        action_surface={
            "actions": [
                {
                    "tool": "messaging.send_customer_email",
                    "approval": {"threshold": "manager"},
                    "safeguards": {
                        "audit_log": True,
                        "rollback": True,
                        "dry_run": False,
                    },
                    "evidence": {
                        "owner": "support-ops",
                        "runbook": "RUNBOOK-12",
                        "approval_ticket": "SEC-42",
                    },
                }
            ]
        },
    )
    fact = build_capability_facts(
        manifest,
        agent_id="agent:one",
        tools=[
            _tool(
                "messaging.send_customer_email",
                hints=[
                    ("write", "high"),
                    ("customer_communication", "high"),
                    ("external_write", "high"),
                ],
            )
        ],
    )[0]

    assert fact.controls.approval_required is True
    assert fact.controls.approval_threshold == "manager"
    assert fact.controls.confirmation_required is True
    assert fact.controls.safeguard_idempotency is True
    assert fact.controls.safeguard_audit_log is True
    assert fact.controls.safeguard_rollback is True
    assert fact.controls.safeguard_dry_run is False
    assert fact.controls.evidence_owner == "support-ops"
    assert fact.controls.evidence_runbook == "RUNBOOK-12"
    assert fact.controls.evidence_approval_ticket == "SEC-42"


def test_default_confirmation_hash_matches_batch_builder_without_policy() -> None:
    manifest = _manifest()
    tool = _tool("messaging.preview_email", annotations={"httpMethod": "GET"})
    action = build_action(manifest, agent_id="agent:one", tool=tool, declaration=None)

    direct = capability_fact_from_action(action, tool)
    batch = build_capability_facts(manifest, agent_id="agent:one", tools=[tool])[0]

    assert direct.controls.confirmation_required is False
    assert batch.controls.confirmation_required is False
    assert direct.hashes.control_hash == batch.hashes.control_hash


def test_capability_facts_sort_stably_regardless_of_tool_input_order() -> None:
    alpha = _tool("alpha.read", annotations={"httpMethod": "GET"}, scopes=["alpha:read"])
    beta = _tool(
        "beta.write",
        annotations={"httpMethod": "POST"},
        scopes=["beta:write"],
        hints=[("write", "high")],
    )

    first = build_capability_facts(_manifest(), agent_id="agent:one", tools=[beta, alpha])
    second = build_capability_facts(_manifest(), agent_id="agent:one", tools=[alpha, beta])

    assert [fact.id for fact in first] == [fact.id for fact in second]
    assert [fact.identity.tool_name for fact in first] == ["alpha.read", "beta.write"]


def test_capability_schema_hash_does_not_drop_finding_evidence_keys() -> None:
    manifest = _manifest()
    base = _tool(
        "cases.search",
        annotations={"httpMethod": "POST"},
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    with_observed = _tool(
        "cases.search",
        annotations={"httpMethod": "POST"},
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "observed": {"type": "string"},
                "source_provenance": {"type": "string"},
            },
            "default_severity": "high",
        },
    )

    base_fact = build_capability_facts(manifest, agent_id="agent:one", tools=[base])[0]
    observed_fact = build_capability_facts(
        manifest,
        agent_id="agent:one",
        tools=[with_observed],
    )[0]

    assert base_fact.id == observed_fact.id
    assert base_fact.hashes.schema_hash != observed_fact.hashes.schema_hash


def test_risk_hash_tracks_raw_risk_tag_changes_without_effect_change() -> None:
    manifest = _manifest()
    base = _tool(
        "cases.update",
        annotations={"httpMethod": "POST"},
        hints=[("write", "high")],
    )
    with_extra_tag = _tool(
        "cases.update",
        annotations={"httpMethod": "POST"},
        hints=[("write", "high"), ("filesystem_write", "medium")],
    )

    base_fact = build_capability_facts(manifest, agent_id="agent:one", tools=[base])[0]
    tagged_fact = build_capability_facts(
        manifest,
        agent_id="agent:one",
        tools=[with_extra_tag],
    )[0]

    assert base_fact.id == tagged_fact.id
    assert base_fact.effect.effect == tagged_fact.effect.effect
    assert base_fact.hashes.effect_hash == tagged_fact.hashes.effect_hash
    assert base_fact.hashes.risk_hash != tagged_fact.hashes.risk_hash


def test_capability_evidence_provenance_classifies_ast_and_static_sources() -> None:
    manifest = _manifest()
    prebuilt = _tool("crew.file_read", source_type="crewai_prebuilt_tool")
    adk_container = _tool("adk.container", source_type="google_adk")
    adk_function = _tool("adk.lookup", source_type="google_adk_function")
    inventory = _tool("lc.search", source_type="langchain_inventory")
    workflow = _tool("n8n.mcp", source_type="n8n_mcp_client_tool")

    facts = {
        fact.identity.tool_name: fact
        for fact in build_capability_facts(
            manifest,
            agent_id="agent:one",
            tools=[prebuilt, adk_container, adk_function, inventory, workflow],
        )
    }

    assert facts["crew.file_read"].evidence.provenance_kind == "ast_extraction"
    assert facts["adk.container"].evidence.provenance_kind == "static_declaration"
    assert facts["adk.lookup"].evidence.provenance_kind == "ast_extraction"
    assert facts["lc.search"].evidence.provenance_kind == "static_declaration"
    assert facts["n8n.mcp"].evidence.provenance_kind == "static_declaration"


def test_capability_fact_from_action_supports_missing_tool_context() -> None:
    manifest = _manifest()
    tool = _tool(
        "cases.update",
        annotations={"httpMethod": "POST"},
        scopes=["cases:write"],
        hints=[("write", "high")],
    )
    action = build_action(manifest, agent_id="agent:one", tool=tool, declaration=None)

    fact = capability_fact_from_action(action, None)

    assert fact.identity.tool_name == "cases.update"
    assert fact.authority.auth_type == "oauth"
    assert fact.authority.credential_mode == "delegated"
    assert fact.authority.scopes == ("cases:write",)
    assert fact.evidence.source_type == "openapi"
    assert fact.evidence.source_id == "support_api"


def test_duplicate_capability_ids_raise() -> None:
    tool = _tool("cases.update", annotations={"httpMethod": "POST"})

    try:
        build_capability_facts(
            _manifest(),
            agent_id="agent:one",
            tools=[tool, tool.model_copy(deep=True)],
        )
    except ConfigError as exc:
        assert "Duplicate capability ids" in str(exc)
    else:  # pragma: no cover - defensive assertion path.
        raise AssertionError("expected duplicate capability id failure")


def test_building_capability_facts_does_not_change_action_fact_output() -> None:
    manifest = _manifest()
    tool = _tool(
        "billing.charge",
        annotations={"httpMethod": "POST"},
        scopes=["billing:charges:write"],
        hints=[("write", "high"), ("financial_action", "high")],
    )
    before = action_to_fact(
        build_action(manifest, agent_id="agent:one", tool=tool, declaration=None)
    ).model_dump(mode="json")

    build_capability_facts(manifest, agent_id="agent:one", tools=[tool])

    after = action_to_fact(
        build_action(manifest, agent_id="agent:one", tool=tool, declaration=None)
    ).model_dump(mode="json")
    assert before == after


def test_capability_substrate_uses_current_report_schema_version() -> None:
    assert ReadinessReport.model_fields["report_schema_version"].default == "0.39"
