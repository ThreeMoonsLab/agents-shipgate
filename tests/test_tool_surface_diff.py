from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import (
    Finding,
    Tool,
    ToolRiskHint,
    ToolSurfaceControlFact,
    ToolSurfaceFacts,
    ToolSurfaceFindingDeltaItem,
    ToolSurfacePolicyFact,
    ToolSurfaceScopeFact,
    ToolSurfaceToolFact,
)
from agents_shipgate.report.tool_surface_diff import (
    ToolSurfaceDiffReference,
    build_tool_surface_facts,
    compute_tool_surface_diff,
)


def test_tool_surface_diff_reports_surface_changes():
    base = ToolSurfaceFacts(
        tools=[
            ToolSurfaceToolFact(
                name="refund.lookup",
                source_type="mcp",
                risk_tags=["read_only"],
                auth_scopes=["refunds:read"],
                owner="support",
                extraction_confidence="medium",
            ),
            ToolSurfaceToolFact(
                name="stripe.refund",
                source_type="mcp",
                risk_tags=["financial_action"],
                auth_scopes=["refunds:write"],
                owner="payments",
                extraction_confidence="high",
            ),
        ],
        scopes=[
            ToolSurfaceScopeFact(
                kind="tool_required",
                scope="refunds:read",
                tool_names=["refund.lookup"],
            )
        ],
        controls=[
            ToolSurfaceControlFact(
                kind="approval_policy",
                tool="stripe.refund",
                source="manifest",
            )
        ],
        policies=[
            ToolSurfacePolicyFact(
                kind="severity_override",
                key="SHIP-OLD",
                value_hash="old",
            )
        ],
    )
    current = ToolSurfaceFacts(
        tools=[
            ToolSurfaceToolFact(
                name="refund.lookup",
                source_type="mcp",
                risk_tags=["read_only"],
                auth_scopes=["refunds:read", "refunds:write"],
                owner="support-platform",
                extraction_confidence="high",
            ),
            ToolSurfaceToolFact(
                name="payment.refund",
                source_type="openapi",
                risk_tags=["financial_action", "external_write"],
                auth_scopes=["payments:*"],
                owner="payments",
                extraction_confidence="high",
            ),
        ],
        scopes=[
            ToolSurfaceScopeFact(
                kind="tool_required",
                scope="refunds:read",
                tool_names=["refund.lookup"],
            ),
            ToolSurfaceScopeFact(
                kind="tool_required",
                scope="payments:*",
                tool_names=["payment.refund"],
                broad=True,
            ),
        ],
        controls=[
            ToolSurfaceControlFact(
                kind="idempotency_evidence",
                tool="payment.refund",
                source="manifest",
            )
        ],
        policies=[
            ToolSurfacePolicyFact(
                kind="severity_override",
                key="SHIP-OLD",
                value_hash="new",
            )
        ],
    )
    findings = [
        Finding(
            id="fp_new",
            fingerprint="fp_new",
            check_id="SHIP-POLICY-APPROVAL-MISSING",
            title="approval missing",
            severity="critical",
            category="policy",
            confidence="high",
            recommendation="Add approval.",
            baseline_status="new",
        ),
        Finding(
            id="fp_debt",
            fingerprint="fp_debt",
            check_id="SHIP-DOC-MISSING-DESCRIPTION",
            title="description missing",
            severity="medium",
            category="documentation",
            confidence="medium",
            recommendation="Add description.",
            baseline_status="matched",
        ),
    ]
    reference = ToolSurfaceDiffReference(
        kind="report",
        facts=base,
        findings=[
            ToolSurfaceFindingDeltaItem(
                fingerprint="fp_old",
                check_id="SHIP-OLD",
                severity="high",
                title="old finding",
            )
        ],
    )

    diff = compute_tool_surface_diff(
        current,
        base,
        findings,
        reference=reference,
    )

    assert diff.enabled is True
    assert diff.summary.tools_added == 1
    assert diff.summary.tools_removed == 1
    assert diff.summary.tools_changed == 1
    assert diff.summary.new_scopes == 1
    assert diff.summary.controls_added == 1
    assert diff.summary.controls_removed == 1
    assert diff.summary.metadata_changes == 3
    assert diff.summary.policy_drift_items == 1
    assert diff.summary.new_findings == 1
    assert diff.summary.resolved_findings == 1
    assert diff.summary.accepted_debt == 1
    assert {item.fingerprint for item in diff.finding_deltas.new_findings} == {
        "fp_new"
    }
    assert {item.fingerprint for item in diff.finding_deltas.accepted_debt} == {
        "fp_debt"
    }
    assert any(item.tag == "external_write" for item in diff.high_risk_effects)


def test_tool_rename_is_added_and_removed():
    base = ToolSurfaceFacts(
        tools=[
            ToolSurfaceToolFact(
                name="legacy.refund",
                source_type="mcp",
                risk_tags=["financial_action"],
            )
        ]
    )
    current = ToolSurfaceFacts(
        tools=[
            ToolSurfaceToolFact(
                name="payment.refund",
                source_type="mcp",
                risk_tags=["financial_action"],
            )
        ]
    )

    diff = compute_tool_surface_diff(current, base, [], reference=None)

    assert [(item.kind, item.name) for item in diff.tools] == [
        ("added", "payment.refund"),
        ("removed", "legacy.refund"),
    ]
    assert "renames" in " ".join(diff.notes)


def test_finding_deltas_compute_when_reference_lacks_surface_facts():
    finding = Finding(
        id="fp_same",
        fingerprint="fp_same",
        check_id="SHIP-DOC-MISSING-DESCRIPTION",
        title="description missing",
        severity="medium",
        category="documentation",
        confidence="medium",
        recommendation="Add description.",
    )
    reference = ToolSurfaceDiffReference(
        kind="report",
        facts=None,
        report_schema_version="0.9",
        findings=[
            ToolSurfaceFindingDeltaItem(
                fingerprint="fp_same",
                check_id="SHIP-DOC-MISSING-DESCRIPTION",
                severity="medium",
                title="description missing",
            )
        ],
        notes=("Reference report is pre-v0.10 and lacks tool_surface_facts.",),
    )

    diff = compute_tool_surface_diff(
        ToolSurfaceFacts(),
        None,
        [finding],
        reference=reference,
    )

    assert diff.enabled is False
    assert diff.summary.unchanged_findings == 1
    assert diff.finding_deltas.unchanged_findings[0].fingerprint == "fp_same"
    assert any("Finding deltas were computed" in note for note in diff.notes)


def test_v02_baseline_diff_reference_reports_upgrade_note():
    reference = ToolSurfaceDiffReference(
        kind="baseline",
        facts=None,
        baseline_schema_version="0.2",
        findings=[
            ToolSurfaceFindingDeltaItem(
                fingerprint="fp_old",
                check_id="SHIP-DOC-MISSING-DESCRIPTION",
                severity="medium",
                title="description missing",
            )
        ],
        notes=("Baseline schema 0.2 has no tool_surface_facts; surface diff disabled.",),
    )

    diff = compute_tool_surface_diff(
        ToolSurfaceFacts(),
        None,
        [],
        reference=reference,
    )

    assert diff.enabled is False
    assert diff.summary.resolved_findings == 1
    assert any("baseline save" in note for note in diff.notes)


def test_scope_diff_tracks_broadness_changes():
    base = ToolSurfaceFacts(
        scopes=[
            ToolSurfaceScopeFact(
                kind="tool_required",
                scope="custom-scope",
                tool_names=["tool"],
                broad=False,
            )
        ]
    )
    current = ToolSurfaceFacts(
        scopes=[
            ToolSurfaceScopeFact(
                kind="tool_required",
                scope="custom-scope",
                tool_names=["tool"],
                broad=True,
            )
        ]
    )

    diff = compute_tool_surface_diff(current, base, [], reference=None)

    assert [(item.kind, item.scope, item.broad) for item in diff.scopes] == [
        ("changed", "custom-scope", True)
    ]


def test_build_tool_surface_facts_projects_controls_and_metadata():
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "diff-test"},
            "agent": {"name": "agent", "declared_purpose": ["refund support"]},
            "environment": {"target": "local"},
            "tool_sources": [{"id": "tools", "type": "mcp", "path": "tools.json"}],
            "permissions": {"scopes": ["refunds:*"]},
            "policies": {
                "require_approval_for_tools": ["stripe.refund"],
                "require_idempotency_for_tools": [
                    {"tool": "stripe.refund", "reason": "side effect"}
                ],
            },
        }
    )
    tool = Tool(
        id="tool:stripe.refund",
        name="stripe.refund",
        source_type="mcp",
        description="Refund a customer payment.",
        auth={"scopes": ["refunds:write"]},
        risk_hints=[
            ToolRiskHint(
                tag="financial_action",
                source="test",
                confidence="high",
            )
        ],
        owner="payments",
        extraction_confidence="high",
    )

    facts = build_tool_surface_facts(manifest, [tool], [], None, None)

    assert facts.tools[0].risk_tags == ["financial_action"]
    assert facts.tools[0].owner == "payments"
    assert any(scope.scope == "refunds:*" and scope.broad for scope in facts.scopes)
    assert {
        (control.kind, control.tool)
        for control in facts.controls
    } == {
        ("approval_policy", "stripe.refund"),
        ("idempotency_evidence", "stripe.refund"),
    }
