import json
from pathlib import Path

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.baseline import write_baseline
from agents_shipgate.core.findings import assign_finding_ids
from agents_shipgate.core.models import (
    ActionApprovalFact,
    ActionEvidenceFact,
    ActionFact,
    ActionSafeguardsFact,
    ActionSurfaceDiff,
    ActionSurfaceFacts,
    ActionSurfaceHashes,
    Finding,
    Tool,
    ToolRiskHint,
)
from agents_shipgate.report.action_surface_diff import (
    _dedupe_findings,
    build_action_surface_facts,
    compute_action_surface_diff,
    evaluate_action_surface_policies,
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
    assert action.action_id == "agent:action-test/agent:mcp:stripe:refund_customer"
    assert action.effect == "financial_write"
    assert action.risk_tags == ["external_communication", "financial_write"]
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
        annotations={"httpMethod": "post", "path": "/users/:id"},
        extraction_confidence="high",
    )

    facts = build_action_surface_facts(
        manifest,
        agent_id="agent:action-test/agent",
        tools=[tool],
    )

    action = facts.actions[0]
    assert action.operation == "POST /users/{id}"
    assert action.action_id == "agent:action-test/agent:openapi:billing-api:POST /users/{id}"
    assert action.effect == "write"


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
        finding.check_id == "SHIP-ACTION-WILDCARD-SCOPE"
        and finding.blocks_release
        for finding in findings
    )


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


def test_action_declaration_effect_downgrade_blocks_release():
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

    finding = next(
        item
        for item in findings
        if item.check_id == "SHIP-ACTION-EFFECT-DOWNGRADE-DECLARED"
    )
    assert finding.blocks_release is True
    assert finding.evidence["inferred_effect"] == "write"
    assert finding.evidence["declared_effect"] == "read"


def test_scan_diff_from_prior_report_blocks_new_financial_action(tmp_path):
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
    assert report.action_surface_diff.summary.blocking_findings == 1
    assert report.release_decision is not None
    assert report.release_decision.decision == "blocked"
    assert any(
        finding.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
        and finding.blocks_release
        for finding in report.findings
    )


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
action_surface:
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
"""
    head_manifest = base_manifest + """
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
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-ACTION-POLICY-VIOLATION"
    )
    assert policy_finding.blocks_release is True
    assert policy_finding.evidence["missing"][0]["reason"] == "unknown_path"
