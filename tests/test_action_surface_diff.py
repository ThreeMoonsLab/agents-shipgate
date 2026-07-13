import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core import semantic_assessment as semantic_assessment_module
from agents_shipgate.core.baseline import write_baseline
from agents_shipgate.core.domain import (
    Tool,
    ToolRiskHint,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.findings import assign_finding_ids
from agents_shipgate.core.lenses.action_surface import (
    _canonical_action_id,
    _dedupe_findings,
    _stable_hash,
    build_action_surface_facts,
    compute_action_surface_diff,
    enrich_action_surface_diff_with_source,
    evaluate_action_surface_policies,
)
from agents_shipgate.core.semantic_assessment import attach_semantic_assessments
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import Finding
from agents_shipgate.schemas.surfaces import (
    ActionApprovalFact,
    ActionEvidenceFact,
    ActionFact,
    ActionSafeguardsFact,
    ActionSurfaceChange,
    ActionSurfaceDiff,
    ActionSurfaceFacts,
    ActionSurfaceHashes,
)

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")


def _manifest(extra: dict | None = None) -> AgentsShipgateManifest:
    payload = {
        "version": "0.1",
        "project": {"name": "action-test"},
        "agent": {"name": "agent", "declared_purpose": ["test action surface"]},
        "environment": {"target": "production_like"},
        "tool_sources": [{"id": "tools", "type": "mcp", "path": "tools.json"}],
    }
    if extra:
        payload.update(extra)
    return AgentsShipgateManifest.model_validate(payload)


def _action(
    action_id: str,
    *,
    effect: str = "read",
    risk_tags: list[str] | None = None,
    scopes: list[str] | None = None,
    approval_required: bool | None = None,
    safeguards: dict | None = None,
    input_fields: list[str] | None = None,
    required_input_fields: list[str] | None = None,
) -> ActionFact:
    approval = ActionApprovalFact(required=approval_required)
    safeguard_fact = ActionSafeguardsFact(**(safeguards or {}))
    input_fields = sorted(input_fields or [])
    required_input_fields = sorted(required_input_fields or [])
    hashes = ActionSurfaceHashes(
        identity_hash=f"id-{action_id}",
        schema_hash=f"schema-{','.join(input_fields)}-{','.join(required_input_fields)}",
        policy_hash=f"policy-{approval.model_dump()}-{safeguard_fact.model_dump()}",
        risk_hash=f"risk-{effect}-{risk_tags or []}-{scopes or []}",
    )
    return ActionFact(
        action_id=action_id,
        agent_id="agent:action-test/agent",
        tool_id=f"tool:{action_id}",
        tool_name=action_id.rsplit(":", 1)[-1],
        provider="tools",
        source_type="mcp",
        source_id="tools",
        operation=action_id.rsplit(":", 1)[-1],
        effect=effect,
        risk_tags=sorted(risk_tags or []),
        required_scopes=sorted(scopes or []),
        approval_policy=approval,
        safeguards=safeguard_fact,
        evidence=ActionEvidenceFact(owner="platform"),
        input_fields=input_fields,
        required_input_fields=required_input_fields,
        input_schema_hash=hashes.schema_hash,
        hashes=hashes,
    )


def test_action_surface_facts_normalize_mcp_and_explicit_metadata():
    manifest = _manifest(
        {
            "action_surface": {
                "actions": [
                    {
                        "tool": "refund_customer",
                        "provider": "stripe",
                        "operation": "refund_customer",
                        "effect": "financial_write",
                        "risk_tags": ["external_write", "financial_action"],
                        "scopes": ["refunds:create"],
                        "approval": {"required": True, "threshold": "amount <= 100"},
                        "safeguards": {"idempotency": True, "audit_log": True},
                        "evidence": {
                            "owner": "payments",
                            "runbook": "docs/runbooks/refunds.md",
                        },
                    }
                ]
            }
        }
    )
    tool = Tool(
        id="tool:refund_customer",
        name="refund_customer",
        source_type="mcp",
        source_id="stripe-mcp",
        provider="stripe",
        auth={"scopes": ["refunds:write"]},
        risk_hints=[
            ToolRiskHint(
                tag="financial_action",
                source="test",
                confidence="high",
            )
        ],
        owner="fallback-owner",
        extraction_confidence="high",
    )

    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    action = facts.actions[0]
    assert action.action_id.startswith("agent:action-test/agent:action_v2_")
    assert action.effect == "financial_write"
    assert action.risk_tags == [
        "external_communication",
        "financial_write",
        "writes_data",
    ]
    assert action.required_scopes == ["refunds:create"]
    assert action.approval_policy.required is True
    assert action.approval_policy.threshold == "amount <= 100"
    assert action.safeguards.idempotency is True
    assert action.safeguards.audit_log is True
    assert action.evidence.owner == "payments"


def test_action_surface_facts_normalize_openapi_operation():
    manifest = _manifest()
    tool = Tool(
        id="tool:update_user",
        name="update_user",
        source_type="openapi",
        source_id="billing-api",
        annotations={"httpMethod": "post", "path": "/users//:id/"},
        extraction_confidence="high",
    )

    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    action = facts.actions[0]
    assert action.operation == "POST /users/{id}"
    assert action.action_id.startswith("agent:action-test/agent:action_v2_")
    assert action.effect == "write"


def test_action_surface_dotted_tool_name_without_source_id_uses_source_type_provider():
    manifest = _manifest()
    tool = Tool(
        id="tool:gmail.threads.send",
        name="gmail.threads.send",
        source_type="sdk_function",
        extraction_confidence="high",
    )

    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    action = facts.actions[0]
    assert action.provider == "sdk_function"
    assert action.operation == "gmail.threads.send"
    assert action.action_id.startswith("agent:action-test/agent:action_v2_")


def test_action_surface_rejects_duplicate_tool_declarations():
    with pytest.raises(ValueError, match=r"Duplicate action_surface\.actions\[\] tool selectors"):
        _manifest(
            {
                "action_surface": {
                    "actions": [
                        {"tool": "refund_customer", "approval": {"required": True}},
                        {"tool": "refund_customer", "approval": {"required": False}},
                    ]
                }
            }
        )


def test_action_surface_rejects_action_id_collisions():
    """An explicit ``actions[].id`` colliding with another action's inferred
    id is a manifest config error and must stay a hard ConfigError — even on
    the fail-soft (warnings-sink) path the live scan uses. Only purely
    inferred collisions degrade; a user-declared id is never silently
    rewritten."""
    colliding_id = _canonical_action_id(
        agent_id="agent:action-test/agent",
        tool_id="tool:beta",
        provider="tools",
        operation="beta",
    )
    manifest = _manifest(
        {
            "action_surface": {
                "actions": [
                    {"tool": "alpha", "id": colliding_id},
                ]
            }
        }
    )
    tools = [
        Tool(
            id="tool:alpha",
            name="alpha",
            source_type="mcp",
            source_id="tools",
            extraction_confidence="high",
        ),
        Tool(
            id="tool:beta",
            name="beta",
            source_type="mcp",
            source_id="tools",
            extraction_confidence="high",
        ),
    ]

    with pytest.raises(ConfigError, match="Duplicate action_surface action_id"):
        build_action_surface_facts(
            manifest,
            agent_id="agent:action-test/agent",
            tools=tools,
        )
    # The live scan path always passes a warnings sink; an explicit-vs-inferred
    # collision must NOT be silently disambiguated into review_required.
    with pytest.raises(ConfigError, match="Duplicate action_surface action_id"):
        build_action_surface_facts(
            manifest,
            agent_id="agent:action-test/agent",
            tools=tools,
            warnings=[],
        )


def test_action_surface_provider_operation_do_not_collapse_distinct_tool_ids():
    """``provider``/``operation`` declarations override action_id components
    just like ``id`` does. A collision between two such manifest-authored
    identities must stay a hard ConfigError even on the warnings-sink path —
    not be silently rewritten to ``...:shared#beta``."""
    manifest = _manifest(
        {
            "action_surface": {
                "actions": [
                    {"tool": "alpha", "provider": "tools", "operation": "shared"},
                    {"tool": "beta", "provider": "tools", "operation": "shared"},
                ]
            }
        }
    )
    tools = [
        Tool(
            id="tool:alpha",
            name="alpha",
            source_type="mcp",
            source_id="tools",
            extraction_confidence="high",
        ),
        Tool(
            id="tool:beta",
            name="beta",
            source_type="mcp",
            source_id="tools",
            extraction_confidence="high",
        ),
    ]

    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=tools,
        warnings=[],
    )
    assert len({action.action_id for action in facts.actions}) == 2


def test_action_surface_disambiguates_openapi_action_id_collisions_fail_soft():
    """Two OpenAPI operations whose paths normalize identically (a
    trailing-slash variant) collapse to one ``action_id``. With a warnings
    sink the scan degrades instead of raising: ids stay distinct and one
    warning is recorded. Real-world repro: block/goose's spec."""
    manifest = _manifest()
    tools = [
        Tool(
            id="tool:get_session",
            name="get_session",
            source_type="openapi",
            source_id="goose-api",
            annotations={"httpMethod": "get", "path": "/sessions/{session_id}"},
            extraction_confidence="high",
        ),
        Tool(
            id="tool:get_session_detail",
            name="get_session_detail",
            source_type="openapi",
            source_id="goose-api",
            # Trailing slash normalizes to the same path as get_session.
            annotations={"httpMethod": "get", "path": "/sessions/{session_id}/"},
            extraction_confidence="high",
        ),
    ]

    warnings: list[str] = []
    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=tools,
        warnings=warnings,
    )

    action_ids = [action.action_id for action in facts.actions]
    assert len(set(action_ids)) == 2
    assert all(action_id.startswith("agent:action-test/agent:action_v2_") for action_id in action_ids)
    assert {action.tool_name for action in facts.actions} == {
        "get_session",
        "get_session_detail",
    }
    assert all(action.hashes.identity_hash == _stable_hash(action.action_id) for action in facts.actions)
    assert warnings == []


def test_action_surface_diff_degrades_duplicate_base_action_ids_fail_soft():
    """A ``--diff-from`` report or baseline serialized by a pre-collision-fix
    engine can carry duplicate ``action_id`` values in its round-tripped
    ``action_surface_facts``. The diff must degrade fail-soft with a warning
    (routing to review_required) instead of crashing the scan with a
    ConfigError that points at a manifest which is fine — the engine
    inferred those ids itself. Without a sink the legacy hard error is
    preserved. Real-world repro: block/goose miner rows dying with exit 2."""
    base_id = "agent:action-test/agent:openapi:goose-api:GET /sessions/{session_id}"
    base_first = _action(base_id)
    base_first.tool_name = "get_session"
    base_second = _action(base_id)
    base_second.tool_name = "get_session_detail"
    base = ActionSurfaceFacts(actions=[base_first, base_second])
    # Fresh head facts: build-time disambiguation already suffixed the second.
    head_first = _action(base_id)
    head_first.tool_name = "get_session"
    head_second = _action(f"{base_id}#get_session_detail")
    head_second.tool_name = "get_session_detail"
    current = ActionSurfaceFacts(actions=[head_first, head_second])

    # Legacy no-sink callers keep the hard ConfigError.
    with pytest.raises(ConfigError, match="Duplicate action_surface action_id"):
        compute_action_surface_diff(current, base)

    warnings: list[str] = []
    diff = compute_action_surface_diff(current, base, warnings=warnings)

    assert diff.enabled
    # The base side was disambiguated with the same tool-name suffix strategy
    # the head build uses, so an identical surface diffs without churn.
    assert diff.added == []
    assert diff.removed == []
    assert {action.action_id for action in base.actions} == {
        base_id,
        f"{base_id}#get_session_detail",
    }
    assert len(warnings) == 1
    assert "base reference" in warnings[0]
    assert "Duplicate action_surface action_id" in warnings[0]


def test_action_surface_policy_requires_typed_expected_values():
    with pytest.raises(ValueError, match="safeguards.audit_log"):
        _manifest(
            {
                "action_surface": {
                    "policies": [
                        {
                            "id": "require-audit",
                            "match": {"tools": ["lookup"]},
                            "require": {"safeguards.audit_log": "true"},
                            "severity": "high",
                        }
                    ]
                }
            }
        )


def test_action_surface_risk_tags_reject_unknown_values():
    with pytest.raises(ValueError, match="not_a_real_action_tag"):
        _manifest(
            {
                "action_surface": {
                    "actions": [
                        {
                            "tool": "lookup",
                            "risk_tags": ["not_a_real_action_tag"],
                        }
                    ]
                }
            }
        )


def test_action_surface_external_side_effect_alias_matches_external_communication_policy():
    manifest = _manifest(
        {
            "action_surface": {
                "actions": [
                    {
                        "tool": "send_customer_email",
                        "effect": "external_communication",
                        "risk_tags": ["external_side_effect"],
                    }
                ],
                "policies": [
                    {
                        "id": "require-audit-for-external-communication",
                        "match": {"risk_tags": ["external_communication"]},
                        "require": {"safeguards.audit_log": True},
                        "severity": "high",
                        "block": True,
                    }
                ],
            }
        }
    )
    tool = Tool(
        id="tool:send_customer_email",
        name="send_customer_email",
        source_type="mcp",
        source_id="tools",
        extraction_confidence="high",
    )
    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    assert facts.actions[0].risk_tags == ["external_communication"]
    findings = evaluate_action_surface_policies(
        manifest,
        facts,
        ActionSurfaceDiff(),
        agent_id="agent:action-test/agent",
    )

    assert any(
        finding.check_id == "SHIP-ACTION-POLICY-VIOLATION"
        and finding.evidence["policy_id"] == "require-audit-for-external-communication"
        and finding.blocks_release
        for finding in findings
    )


def test_custom_action_policy_cannot_launder_heuristic_risk_tag() -> None:
    manifest = _manifest(
        {
            "action_surface": {
                "policies": [
                    {
                        "id": "heuristic-financial-block",
                        "match": {"risk_tags": ["financial_write"]},
                        "require": {"approval.required": True},
                        "severity": "critical",
                        "block": True,
                    }
                ]
            }
        }
    )
    tool = Tool(
        id="tool:refund_status",
        name="refund_status",
        source_type="langchain_inventory",
        source_id="tools",
        extraction_confidence="high",
        risk_hints=[
            ToolRiskHint(
                tag="financial_action",
                source="keyword",
                confidence="high",
                basis="inferred_keyword",
                provenance_kind="keyword_heuristic",
            )
        ],
    )
    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )
    gaps = []

    findings = evaluate_action_surface_policies(
        manifest,
        facts,
        ActionSurfaceDiff(),
        agent_id="agent:action-test/agent",
        tools=[tool],
        policy_evidence_gaps=gaps,
    )

    assert not any(
        finding.evidence.get("policy_id") == "heuristic-financial-block"
        for finding in findings
    )
    gap = next(item for item in gaps if "heuristic-financial-block" in item.why)
    assert gap.kind == "inferred_policy_applicability"


def test_enrich_action_surface_diff_populates_structured_source_fields():
    """v0.19 reviewer-grade provenance: every change row gains
    structured ``source_path`` / ``source_start_line`` fields when the
    underlying tool source is known. Without these, action-surface
    change rows in report.json carried no clue where the underlying
    tool was defined and reviewers had to grep tool_inventory.

    The fields are deliberately structured (not a ``(source: ...)``
    suffix in ``reason``) because ``ActionSurfaceChange.model_dump``
    lands in policy-finding ``evidence`` payloads and finding
    fingerprints hash ``evidence`` — text in ``reason`` would leak
    line numbers into baseline identity. The reason field stays
    byte-stable; the structured fields ride on the public diff only.
    """
    base = ActionSurfaceFacts(
        actions=[
            ActionFact(
                action_id="agent:stripe.create_refund",
                agent_id="agent",
                tool_id="tool:stripe.create_refund",
                tool_name="stripe.create_refund",
                provider="custom",
                source_type="openapi",
                operation="create_refund",
                effect="financial_write",
                input_schema_hash="0" * 64,
                approval_policy=ActionApprovalFact(required=True),
                hashes=ActionSurfaceHashes(
                    identity_hash="0" * 64,
                    schema_hash="0" * 64,
                    policy_hash="0" * 64,
                    risk_hash="0" * 64,
                ),
            )
        ]
    )
    current = ActionSurfaceFacts(
        actions=[
            ActionFact(
                action_id="agent:stripe.create_refund",
                agent_id="agent",
                tool_id="tool:stripe.create_refund",
                tool_name="stripe.create_refund",
                provider="custom",
                source_type="openapi",
                operation="create_refund",
                effect="financial_write",
                input_schema_hash="0" * 64,
                # Approval removed → trigger APPROVAL_REMOVED row.
                approval_policy=ActionApprovalFact(required=False),
                hashes=ActionSurfaceHashes(
                    identity_hash="1" * 64,
                    schema_hash="0" * 64,
                    policy_hash="1" * 64,
                    risk_hash="0" * 64,
                ),
            )
        ]
    )
    diff = compute_action_surface_diff(current, base)
    pre_reason = next(row for row in diff.modified if row.type == "APPROVAL_REMOVED").reason
    enrich_action_surface_diff_with_source(
        diff,
        {"stripe.create_refund": ("api.yaml", 97)},
    )
    assert diff.modified
    approval_removed = next(row for row in diff.modified if row.type == "APPROVAL_REMOVED")
    assert approval_removed.source_path == "api.yaml"
    assert approval_removed.source_start_line == 97
    # Reason stays byte-stable so finding fingerprints don't churn.
    assert approval_removed.reason == pre_reason


def test_action_policy_finding_evidence_excludes_v019_source_fields():
    """v0.19 reviewer-grade provenance: ``ActionSurfaceChange`` carries
    ``source_path`` / ``source_start_line`` fields but
    ``evaluate_action_surface_policies`` MUST NOT include them in the
    ``evidence`` payload it dumps into action policy findings, or every
    existing action-surface finding fingerprint would churn relative
    to pre-v0.19 baselines (even when the new fields are ``None``,
    their mere presence as keys shifts the canonicalised hash).

    Regression for review #5: ``change.model_dump(mode='json')``
    silently included ``source_path: None`` / ``source_start_line: None``
    keys; finding_fingerprint hashed them and the same legacy change
    payload produced two different fingerprints. The fix excludes
    those keys at the dump site so the evidence stays byte-equal to
    legacy. The diff row itself still carries the structured fields
    for renderers.
    """
    from agents_shipgate.core.lenses.action_surface import _change_evidence

    change = ActionSurfaceChange(
        type="APPROVAL_REMOVED",
        action_id="agent:t",
        agent_id="agent",
        tool_name="t",
        operation="op",
        severity="critical",
        reason="Action approval policy was removed.",
        before={"required": True},
        after={"required": False},
    )
    # Pre-enrichment dump — same as a pre-v0.19 payload.
    pre = _change_evidence(change)
    # Enrich to simulate the public diff carrying source fields.
    change.source_path = "api.yaml"
    change.source_start_line = 97
    # Post-enrichment dump must still match — source fields excluded.
    post = _change_evidence(change)
    assert pre == post, "evidence dump must drop source_path/source_start_line"
    assert "source_path" not in post
    assert "source_start_line" not in post
    # Reason and other fields are preserved verbatim.
    assert post["reason"] == "Action approval policy was removed."


def test_enrich_action_surface_diff_does_not_mutate_reason():
    """Regression: an earlier draft suffixed ``reason`` with
    ``(source: path:line)``. That leaks line numbers into the
    ``ActionSurfaceChange.model_dump()`` payload that ``evidence``
    carries in ``evaluate_action_surface_policies`` findings, and
    ``finding_fingerprint`` hashes ``evidence``. Keep the reason
    byte-stable so baseline fingerprints don't churn when a tool
    moves in its source file."""
    base = ActionSurfaceFacts()
    current = ActionSurfaceFacts(
        actions=[
            ActionFact(
                action_id="agent:t",
                agent_id="agent",
                tool_id="tool:t",
                tool_name="t",
                provider="custom",
                source_type="openapi",
                operation="op",
                effect="read",
                input_schema_hash="0" * 64,
                hashes=ActionSurfaceHashes(
                    identity_hash="0" * 64,
                    schema_hash="0" * 64,
                    policy_hash="0" * 64,
                    risk_hash="0" * 64,
                ),
            )
        ]
    )
    diff = compute_action_surface_diff(current, base)
    original = [row.reason for row in diff.added]
    enrich_action_surface_diff_with_source(diff, {"t": ("api.yaml", 42)})
    assert [row.reason for row in diff.added] == original


def test_enrich_action_surface_diff_skipped_when_index_empty():
    """No-op when the index is None / empty so callers don't need to
    branch on the absence of structured source data."""
    base = ActionSurfaceFacts()
    current = ActionSurfaceFacts(
        actions=[
            ActionFact(
                action_id="agent:t",
                agent_id="agent",
                tool_id="tool:t",
                tool_name="t",
                provider="custom",
                source_type="openapi",
                operation="op",
                effect="read",
                input_schema_hash="0" * 64,
                hashes=ActionSurfaceHashes(
                    identity_hash="0" * 64,
                    schema_hash="0" * 64,
                    policy_hash="0" * 64,
                    risk_hash="0" * 64,
                ),
            )
        ]
    )
    diff = compute_action_surface_diff(current, base)
    original_reasons = [row.reason for row in diff.added]
    enrich_action_surface_diff_with_source(diff, None)
    enrich_action_surface_diff_with_source(diff, {})
    assert [row.reason for row in diff.added] == original_reasons


def test_action_surface_diff_reports_added_and_removed_actions():
    base = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:summarize_ticket",
                effect="read",
                risk_tags=["read_only"],
            )
        ]
    )
    current = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:refund_customer",
                effect="financial_write",
                risk_tags=["financial_write"],
            )
        ]
    )

    diff = compute_action_surface_diff(current, base, reference=None)

    assert diff.enabled is True
    assert diff.summary.actions_added == 1
    assert diff.summary.actions_removed == 1
    assert diff.added[0].type == "ACTION_ADDED"
    assert diff.added[0].severity == "critical"
    assert diff.removed[0].type == "ACTION_REMOVED"
    assert diff.removed[0].severity == "info"


def test_builtin_wildcard_policy_blocks_added_action():
    manifest = _manifest()
    current = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:admin_lookup",
                effect="read",
                risk_tags=["read_only"],
                scopes=["admin:*"],
            )
        ]
    )
    diff = compute_action_surface_diff(current, ActionSurfaceFacts(), reference=None)

    findings = evaluate_action_surface_policies(
        manifest,
        current,
        diff,
        agent_id="agent:action-test/agent",
    )

    assert any(
        finding.check_id == "SHIP-ACTION-WILDCARD-SCOPE" and finding.blocks_release
        for finding in findings
    )


def test_builtin_financial_controls_apply_without_diff_reference():
    manifest = _manifest()
    current = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:process_payment",
                effect="financial_write",
                risk_tags=["financial_write"],
            )
        ]
    )

    findings = evaluate_action_surface_policies(
        manifest,
        current,
        ActionSurfaceDiff(),
        agent_id="agent:action-test/agent",
    )

    finding = next(
        item for item in findings if item.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
    )
    assert finding.blocks_release is True
    assert finding.evidence["missing"] == [
        "approval.required",
        "safeguards.audit_log",
        "safeguards.idempotency",
    ]


def test_builtin_destructive_controls_apply_to_existing_action():
    manifest = _manifest()
    current = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:delete_record",
                effect="destructive",
                risk_tags=["destructive"],
            )
        ]
    )

    findings = evaluate_action_surface_policies(
        manifest,
        current,
        ActionSurfaceDiff(),
        agent_id="agent:action-test/agent",
    )

    finding = next(
        item for item in findings if item.check_id == "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING"
    )
    assert finding.evidence["missing"] == [
        "approval.required",
        "safeguards.rollback",
        "confirmation.required",
    ]


def test_action_surface_diff_reports_modification_taxonomy():
    base = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:run_sql",
                effect="read",
                risk_tags=["read_only"],
                scopes=["db:read"],
                approval_required=True,
                safeguards={"audit_log": True, "idempotency": True},
                input_fields=["query"],
                required_input_fields=["query"],
            )
        ]
    )
    current = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:run_sql",
                effect="write",
                risk_tags=["read_only", "writes_data"],
                scopes=["db:read", "admin:*"],
                approval_required=False,
                safeguards={"audit_log": False, "idempotency": True},
                input_fields=["query", "amount"],
                required_input_fields=["query", "amount"],
            )
        ]
    )

    diff = compute_action_surface_diff(current, base, reference=None)

    assert diff.enabled is True
    assert diff.summary.actions_modified == 1
    assert diff.summary.scope_expansions == 1
    assert diff.summary.effect_escalations == 1
    assert diff.summary.risk_tags_added == 1
    assert diff.summary.approvals_removed == 1
    assert diff.summary.safeguards_removed == 1
    assert diff.summary.input_schema_expansions == 1
    assert {change.type for change in diff.modified} >= {
        "SCOPE_EXPANDED",
        "EFFECT_ESCALATED",
        "RISK_TAG_ADDED",
        "APPROVAL_REMOVED",
        "SAFEGUARD_REMOVED",
        "INPUT_SCHEMA_EXPANDED",
    }
    scope_change = next(change for change in diff.modified if change.type == "SCOPE_EXPANDED")
    assert scope_change.severity == "critical"
    assert scope_change.added == ["admin:*"]


def test_require_explicit_actions_reports_undeclared_tool():
    manifest = _manifest({"action_surface": {"require_explicit_actions": True}})
    tool = Tool(
        id="tool:lookup",
        name="lookup",
        source_type="mcp",
        source_id="tools",
        extraction_confidence="high",
    )
    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    findings = evaluate_action_surface_policies(
        manifest,
        facts,
        ActionSurfaceDiff(),
        agent_id="agent:action-test/agent",
    )

    assert any(
        finding.check_id == "SHIP-ACTION-UNDECLARED"
        and finding.tool_name == "lookup"
        and finding.blocks_release
        for finding in findings
    )


def test_require_explicit_actions_passes_when_all_tools_are_declared():
    manifest = _manifest(
        {
            "action_surface": {
                "require_explicit_actions": True,
                "actions": [{"tool": "lookup"}],
            }
        }
    )
    tool = Tool(
        id="tool:lookup",
        name="lookup",
        source_type="mcp",
        source_id="tools",
        extraction_confidence="high",
    )
    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    findings = evaluate_action_surface_policies(
        manifest,
        facts,
        ActionSurfaceDiff(),
        agent_id="agent:action-test/agent",
    )

    assert not any(finding.check_id == "SHIP-ACTION-UNDECLARED" for finding in findings)


def test_dedupe_findings_uses_canonical_evidence_order():
    first = Finding(
        check_id="SHIP-ACTION-POLICY-VIOLATION",
        title="policy failed",
        severity="high",
        category="action_surface",
        tool_name="lookup",
        evidence={"policy_id": "p", "missing": [{"path": "a", "expected": True}]},
        confidence="high",
        recommendation="fix policy",
    )
    second = first.model_copy(
        update={
            "evidence": {
                "missing": [{"expected": True, "path": "a"}],
                "policy_id": "p",
            }
        }
    )

    assert len(_dedupe_findings([first, second])) == 1


def test_user_policy_fingerprint_stays_stable_across_partial_remediation():
    manifest = _manifest(
        {
            "action_surface": {
                "policies": [
                    {
                        "id": "require-controls",
                        "match": {"tools": ["lookup"]},
                        "require": {
                            "approval.required": True,
                            "safeguards.audit_log": True,
                        },
                        "severity": "high",
                        "block": True,
                    }
                ]
            }
        }
    )
    current = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:lookup",
                approval_required=False,
                safeguards={"audit_log": False},
            )
        ]
    )
    partially_fixed = ActionSurfaceFacts(
        actions=[
            _action(
                "agent:action-test/agent:mcp:tools:lookup",
                approval_required=False,
                safeguards={"audit_log": True},
            )
        ]
    )

    first = assign_finding_ids(
        evaluate_action_surface_policies(
            manifest,
            current,
            ActionSurfaceDiff(),
            agent_id="agent:action-test/agent",
        )
    )
    second = assign_finding_ids(
        evaluate_action_surface_policies(
            manifest,
            partially_fixed,
            ActionSurfaceDiff(),
            agent_id="agent:action-test/agent",
        )
    )

    approval_first = next(
        finding
        for finding in first
        if finding.evidence["missing"][0]["path"] == "approval.required"
    )
    approval_second = next(
        finding
        for finding in second
        if finding.evidence["missing"][0]["path"] == "approval.required"
    )
    assert approval_second.fingerprint == approval_first.fingerprint
    assert len(second) == 1


def test_action_declaration_control_downgrade_blocks_release():
    manifest = _manifest(
        {
            "policies": {
                "require_approval_for_tools": ["lookup"],
                "require_idempotency_for_tools": ["lookup"],
            },
            "action_surface": {
                "actions": [
                    {
                        "tool": "lookup",
                        "approval": {"required": False},
                        "safeguards": {"idempotency": False},
                    }
                ]
            },
        }
    )
    tool = Tool(
        id="tool:lookup",
        name="lookup",
        source_type="mcp",
        source_id="tools",
        extraction_confidence="high",
    )
    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    findings = evaluate_action_surface_policies(
        manifest,
        facts,
        ActionSurfaceDiff(),
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    downgraded_paths = {
        finding.evidence["path"]
        for finding in findings
        if finding.check_id == "SHIP-ACTION-CONTROL-DOWNGRADE"
    }
    assert downgraded_paths == {"approval.required", "safeguards.idempotency"}
    assert all(
        finding.blocks_release
        for finding in findings
        if finding.check_id == "SHIP-ACTION-CONTROL-DOWNGRADE"
    )


def test_action_declaration_effect_downgrade_blocks_release(monkeypatch):
    manifest = _manifest(
        {
            "action_surface": {
                "actions": [
                    {
                        "tool": "create_ticket",
                        "effect": "read",
                    }
                ]
            }
        }
    )
    tool = Tool(
        id="tool:create_ticket",
        name="create_ticket",
        source_type="openapi",
        source_id="support-api",
        annotations={"httpMethod": "POST", "path": "/tickets"},
        extraction_confidence="high",
    )
    tools = attach_semantic_assessments(
        [tool],
        {tool.id: manifest.action_surface.actions[0]},
    )
    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=tools,
    )

    def unexpected_recompute(*args, **kwargs):
        raise AssertionError("attached semantic assessment was recomputed")

    monkeypatch.setattr(
        "agents_shipgate.core.lenses.action_surface.assess_tool_semantics",
        unexpected_recompute,
    )
    monkeypatch.setattr(
        semantic_assessment_module,
        "assess_tool_semantics",
        unexpected_recompute,
    )

    findings = evaluate_action_surface_policies(
        manifest,
        facts,
        ActionSurfaceDiff(),
        agent_id="agent:action-test/agent",
        tools=tools,
    )

    finding = next(
        item for item in findings if item.check_id == "SHIP-ACTION-EFFECT-DOWNGRADE-DECLARED"
    )
    assert finding.blocks_release is True
    assert finding.evidence["inferred_effect"] == "write"
    assert finding.evidence["declared_effect"] == "read"


def test_scan_diff_from_prior_report_does_not_launder_financial_keyword_into_blocker(
    tmp_path,
):
    base = tmp_path / "base"
    head = tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info: {title: T, version: "1"}
paths: {}
""",
        encoding="utf-8",
    )
    (head / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info: {title: T, version: "1"}
paths:
  /refunds:
    post:
      operationId: refund_customer
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                amount: {type: number}
              required: [amount]
      responses:
        "200": {description: ok}
""",
        encoding="utf-8",
    )
    manifest_text = """
version: "0.1"
project: {name: action-finance}
agent:
  name: agent
  declared_purpose: [test]
environment: {target: production_like}
tool_sources:
  - id: billing-api
    type: openapi
    path: openapi.yaml
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: refund_customer, source_id: billing-api}]
      handoffs: []
      reason: reviewed action-diff fixture binding
"""
    (base / "shipgate.yaml").write_text(manifest_text, encoding="utf-8")
    (head / "shipgate.yaml").write_text(manifest_text, encoding="utf-8")

    run_scan(
        config_path=base / "shipgate.yaml",
        output_dir=base / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    report, exit_code = run_scan(
        config_path=head / "shipgate.yaml",
        output_dir=head / "reports",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=base / "reports" / "report.json",
        packet_enabled=False,
    )

    assert exit_code == 0
    assert report.action_surface_diff.enabled is True
    assert report.action_surface_diff.summary.actions_added == 1
    assert report.action_surface_diff.summary.blocking_findings == 0
    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    assert not any(
        finding.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING" and finding.blocks_release
        for finding in report.findings
    )
    action = report.action_surface_facts.actions[0]
    assert action.effect == "financial_write"
    assert action.semantic_assessment is not None
    assert action.semantic_assessment.effect.status == "inferred"


def test_action_surface_diff_can_use_v04_baseline(tmp_path):
    base = tmp_path / "base"
    head = tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info: {title: T, version: "1"}
paths: {}
""",
        encoding="utf-8",
    )
    (head / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info: {title: T, version: "1"}
paths:
  /refunds:
    post:
      operationId: refund_customer
      responses:
        "200": {description: ok}
""",
        encoding="utf-8",
    )
    manifest_text = """
version: "0.1"
project: {name: action-baseline}
agent:
  name: agent
  declared_purpose: [test]
environment: {target: production_like}
tool_sources:
  - id: billing-api
    type: openapi
    path: openapi.yaml
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: refund_customer, source_id: billing-api}]
      handoffs: []
      reason: reviewed baseline-diff fixture binding
"""
    (base / "shipgate.yaml").write_text(manifest_text, encoding="utf-8")
    (head / "shipgate.yaml").write_text(manifest_text, encoding="utf-8")

    base_report, _ = run_scan(
        config_path=base / "shipgate.yaml",
        output_dir=base / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    baseline_path = tmp_path / "baseline.json"
    write_baseline(base_report, baseline_path)
    report, exit_code = run_scan(
        config_path=head / "shipgate.yaml",
        output_dir=head / "reports",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
        packet_enabled=False,
    )

    assert exit_code == 0
    assert report.action_surface_diff.enabled is True
    assert report.action_surface_diff.base.kind == "baseline"
    assert report.action_surface_diff.summary.actions_added == 1


def test_v03_baseline_disables_action_diff_without_crashing(tmp_path):
    base_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    baseline_path = tmp_path / "baseline-v03.json"
    baseline = write_baseline(base_report, baseline_path).model_dump(mode="json")
    baseline["schema_version"] = "0.3"
    baseline.pop("action_surface_facts", None)
    baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "head",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
        packet_enabled=False,
    )

    assert report.action_surface_diff.enabled is False
    assert report.action_surface_diff.notes
    assert "no action_surface_facts" in report.action_surface_diff.notes[0]


def test_action_policy_blocks_strict_ci_and_supports_suppression(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "lookup",
      "description": "Lookup customer support metadata safely.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    base_manifest = """
version: "0.1"
project: {name: action-policy}
agent:
  name: agent
  declared_purpose: [test]
environment: {target: local}
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: lookup, source_id: tools}]
      handoffs: []
      reason: reviewed action-policy fixture binding
action_surface:
  actions:
    - tool: lookup
      effect: read
      authority: {mode: none}
  policies:
    - id: require-audit-for-lookup
      match:
        tools: [lookup]
      require:
        safeguards.audit_log: true
      severity: high
      block: true
"""
    config = project / "shipgate.yaml"
    config.write_text(base_manifest, encoding="utf-8")

    report, exit_code = run_scan(
        config_path=config,
        output_dir=project / "reports",
        formats=["json"],
        ci_mode="strict",
        packet_enabled=False,
    )

    assert exit_code == 20
    assert report.release_decision is not None
    assert report.release_decision.decision == "blocked"
    assert any(
        item.check_id == "SHIP-ACTION-POLICY-VIOLATION" and item.blocks_release
        for item in report.release_decision.blockers
    )

    config.write_text(
        base_manifest
        + """
checks:
  ignore:
    - check_id: SHIP-ACTION-POLICY-VIOLATION
      tool: lookup
      reason: accepted local-only policy exception for test fixture
""",
        encoding="utf-8",
    )
    suppressed_report, suppressed_exit = run_scan(
        config_path=config,
        output_dir=project / "suppressed",
        formats=["json"],
        ci_mode="strict",
        packet_enabled=False,
    )

    assert suppressed_exit == 0
    assert suppressed_report.release_decision is not None
    assert suppressed_report.release_decision.decision == "passed"


def test_user_policy_evaluates_full_surface_when_diff_enabled(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{"tools": [{"name": "lookup", "description": "Lookup support metadata."}]}
""",
        encoding="utf-8",
    )
    base_manifest = """
version: "0.1"
project: {name: action-policy-diff}
agent:
  name: agent
  declared_purpose: [test]
environment: {target: local}
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tool: lookup, source_id: tools}]
      handoffs: []
      reason: reviewed action-policy diff binding
"""
    head_manifest = (
        base_manifest
        + """
action_surface:
  policies:
    - id: require-audit-for-lookup
      match:
        tools: [lookup]
      require:
        safeguards.audit_logg: true
      severity: high
      block: true
"""
    )
    config = project / "shipgate.yaml"
    config.write_text(base_manifest, encoding="utf-8")
    run_scan(
        config_path=config,
        output_dir=project / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    config.write_text(head_manifest, encoding="utf-8")

    report, _ = run_scan(
        config_path=config,
        output_dir=project / "head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=project / "base" / "report.json",
        packet_enabled=False,
    )

    policy_finding = next(
        finding for finding in report.findings if finding.check_id == "SHIP-ACTION-POLICY-VIOLATION"
    )
    assert policy_finding.blocks_release is True
    assert policy_finding.evidence["missing"][0]["reason"] == "unknown_path"
