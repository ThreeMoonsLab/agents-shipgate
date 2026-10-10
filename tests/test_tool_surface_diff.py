import pytest

from agents_shipgate.core.binding_comparison import (
    binding_comparison_limits,
    comparison_withholds_absence,
)
from agents_shipgate.core.domain import (
    Tool,
    ToolRiskHint,
)
from agents_shipgate.core.lenses.tool_surface import (
    ToolSurfaceDiffReference,
    build_tool_surface_facts,
    compute_tool_surface_diff,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import Finding
from agents_shipgate.schemas.surfaces import (
    ToolSurfaceControlFact,
    ToolSurfaceFacts,
    ToolSurfaceFindingDeltaItem,
    ToolSurfacePolicyFact,
    ToolSurfaceScopeFact,
    ToolSurfaceToolFact,
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



def test_incomplete_binding_comparison_cannot_report_risk_or_finding_removals():
    from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, AgentBindingIssue

    base = ToolSurfaceFacts(tools=[ToolSurfaceToolFact(
        name="delete", source_type="mcp", risk_tags=["destructive"], auth_scopes=["admin"],
    )], scopes=[ToolSurfaceScopeFact(kind="tool_required", scope="admin", tool_names=["delete"])],
        controls=[ToolSurfaceControlFact(kind="approval_policy", tool="delete", source="manifest")])
    partial = AgentBindingGraphAssessment(status="partial", issues=[AgentBindingIssue(
        kind="partial_binding_evidence", message="Unread constructor", source="framework_constructor_ownership",
        source_pointer="agent.py:8",
    )])
    complete = AgentBindingGraphAssessment(status="structural", pass_eligible=True)
    for head_graph, base_graph in ((partial, complete), (complete, partial)):
        diff = compute_tool_surface_diff(
            ToolSurfaceFacts(), base, [], head_binding_facts=head_graph,
            reference=ToolSurfaceDiffReference(kind="report", facts=base, binding_facts=base_graph),
        )
        # The comparison is still made; only what depends on absence is withheld.
        assert diff.enabled
        assert not any((diff.tools, diff.scopes, diff.controls, diff.high_risk_effects, diff.metadata_changes))
        assert diff.finding_deltas.resolved_findings == []
        assert diff.summary.tools_removed == 0 and diff.summary.removed_high_risk_effects == 0
        assert any("comparison incomplete" in note and "agent.py:8" in note for note in diff.notes)
        assert comparison_withholds_absence(diff.notes)


def _partial_graph(source="framework_constructor_ownership"):
    from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, AgentBindingIssue

    return AgentBindingGraphAssessment(status="partial", issues=[AgentBindingIssue(
        kind="partial_binding_evidence", message="Unread constructor", source=source,
        source_pointer="agent.py:8",
    )])


def _tool_fact(name, **kwargs):
    return ToolSurfaceToolFact(name=name, source_type="mcp", **kwargs)


@pytest.mark.parametrize("side", ["head", "base"])
def test_an_incomplete_graph_still_reports_additions_changes_and_removed_controls(side):
    """What presence proves reaches the reviewer; only absence is withheld."""
    from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment

    base = ToolSurfaceFacts(
        tools=[_tool_fact("kept", auth_scopes=["read"]), _tool_fact("gone", risk_tags=["destructive"])],
        scopes=[
            ToolSurfaceScopeFact(kind="tool_required", scope="read", tool_names=["kept"]),
            ToolSurfaceScopeFact(kind="tool_required", scope="admin", tool_names=["gone"]),
        ],
        controls=[
            ToolSurfaceControlFact(kind="approval_policy", tool="kept", source="manifest"),
            ToolSurfaceControlFact(kind="approval_policy", tool="gone", source="manifest"),
        ],
    )
    head = ToolSurfaceFacts(
        tools=[_tool_fact("kept", auth_scopes=["read", "write"]), _tool_fact("brand_new")],
        scopes=[
            ToolSurfaceScopeFact(kind="tool_required", scope="read", tool_names=["kept"]),
            ToolSurfaceScopeFact(kind="tool_required", scope="write", tool_names=["kept"]),
        ],
        controls=[],
    )
    complete = AgentBindingGraphAssessment(status="structural", pass_eligible=True)
    diff = compute_tool_surface_diff(
        head, base, [],
        head_binding_facts=_partial_graph() if side == "head" else complete,
        reference=ToolSurfaceDiffReference(
            kind="report", facts=base,
            binding_facts=_partial_graph() if side == "base" else complete,
        ),
    )
    assert diff.enabled
    assert {(row.name, row.kind) for row in diff.tools} == {("brand_new", "added"), ("kept", "changed")}
    assert {(row.scope, row.kind) for row in diff.scopes} == {("write", "added")}
    # The control lost with the absent tool is that tool's absence restated;
    # the control on the tool that is still there is a real removal.
    assert [(row.tool, row.kind) for row in diff.controls] == [("kept", "removed")]
    assert diff.summary.tools_removed == 0 and diff.summary.tools_added == 1
    assert comparison_withholds_absence(diff.notes)


def test_a_toolset_inventory_gap_alone_is_not_incomplete_binding_evidence():
    """A remote toolset nobody can enumerate neither withholds nor forges a removal."""
    from agents_shipgate.core.agent_bindings import FRAMEWORK_TOOLSET_INVENTORY

    base = ToolSurfaceFacts(tools=[_tool_fact("gone")])
    for graph in (_partial_graph(FRAMEWORK_TOOLSET_INVENTORY), None):
        diff = compute_tool_surface_diff(
            ToolSurfaceFacts(), base, [], head_binding_facts=graph,
            reference=ToolSurfaceDiffReference(
                kind="report", facts=base, binding_facts=_partial_graph(FRAMEWORK_TOOLSET_INVENTORY),
            ),
        )
        assert diff.enabled and [(row.name, row.kind) for row in diff.tools] == [("gone", "removed")]
        assert not comparison_withholds_absence(diff.notes)
    # Beside a real gap it still counts only for what it is.
    mixed = _partial_graph()
    mixed = mixed.model_copy(update={"issues": [*mixed.issues, *_partial_graph(FRAMEWORK_TOOLSET_INVENTORY).issues]})
    assert len(binding_comparison_limits(mixed, None)) == 1
    only_inventory = _partial_graph(FRAMEWORK_TOOLSET_INVENTORY)
    assert binding_comparison_limits(only_inventory, only_inventory) == []
    # A partial graph with no recorded cause is not excused by the marker.
    unexplained = only_inventory.model_copy(update={"issues": []})
    assert len(binding_comparison_limits(unexplained, None)) == 1


def test_unknown_catalog_and_fact_only_comparisons_still_report_real_removals():
    from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, AgentBindingIssue

    base = ToolSurfaceFacts(tools=[ToolSurfaceToolFact(name="delete", source_type="mcp")])
    catalog = AgentBindingGraphAssessment(status="unknown", unbound_tool_ids=["delete"], issues=[
        AgentBindingIssue(kind="ambiguous_root_agent", message="Catalog has no agent")])
    for head_graph in (None, catalog):
        diff = compute_tool_surface_diff(ToolSurfaceFacts(), base, [], head_binding_facts=head_graph)
        assert diff.enabled and [(row.name, row.kind) for row in diff.tools] == [("delete", "removed")]


@pytest.mark.parametrize("side", ["head", "base"])
def test_findings_only_reference_cannot_claim_resolution_with_incomplete_bindings(side):
    from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, AgentBindingIssue

    partial = AgentBindingGraphAssessment(status="partial", issues=[AgentBindingIssue(
        kind="partial_binding_evidence", message="Unread constructor", source_pointer="agent.py:3",
    )])
    reference = ToolSurfaceDiffReference(
        kind="report", facts=None,
        findings=[ToolSurfaceFindingDeltaItem(fingerprint="old", check_id="old", title="old", severity="high")],
        binding_facts=partial if side == "base" else None,
    )
    diff = compute_tool_surface_diff(
        ToolSurfaceFacts(), None, [], reference=reference,
        head_binding_facts=partial if side == "head" else None,
    )
    assert not diff.enabled and diff.finding_deltas.resolved_findings == []
    assert diff.summary.resolved_findings == 0
    assert any("comparison incomplete on " + side in note for note in diff.notes)
    # A finding the head has and the reference lacks is presence, still reported.
    new = compute_tool_surface_diff(
        ToolSurfaceFacts(), None, [Finding(
            id="fresh", fingerprint="fresh", check_id="SHIP-DOC-MISSING-DESCRIPTION", title="new",
            severity="high", category="documentation", confidence="high", recommendation="Add it.",
        )], reference=reference,
        head_binding_facts=partial if side == "head" else None,
    )
    assert [item.fingerprint for item in new.finding_deltas.new_findings] == ["fresh"]
    assert new.finding_deltas.resolved_findings == []


@pytest.mark.parametrize("status", ["partial", "conflicting"])
def test_a_fresh_scan_does_not_claim_a_comparison_was_requested(status):
    from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment

    graph = AgentBindingGraphAssessment(status=status, possible_tool_ids=["unread"])
    diff = compute_tool_surface_diff(
        ToolSurfaceFacts(), None, [], head_binding_facts=graph,
    )
    assert not diff.enabled
    assert diff.notes == ["No --diff-from report or v0.3 baseline snapshot was provided."]
    assert diff.finding_deltas.resolved_findings == []
    assert not any((diff.tools, diff.scopes, diff.controls, diff.high_risk_effects))
