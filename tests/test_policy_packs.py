import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan as _run_scan
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.findings.identity import legacy_policy_routing_fingerprint
from agents_shipgate.schemas.baseline import BaselineFile, BaselineFinding

runner = CliRunner()


def run_scan(*args, **kwargs):
    """Keep policy-pack fixtures focused on policy evaluation, not binding gaps."""

    config_path = Path(kwargs["config_path"])
    text = config_path.read_text(encoding="utf-8")
    if (
        "agent_bindings:" not in text
        and "type: openapi" in text
        and "path: openapi.yaml" in text
        and (config_path.parent / "openapi.yaml").is_file()
    ):
        config_path.write_text(
            text
            + """
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - {tool: create_refund, source_id: api}
      handoffs: []
      reason: reviewed policy-pack fixture binding
""",
            encoding="utf-8",
        )
    return _run_scan(*args, **kwargs)

ROUTING_EVIDENCE_KEYS = {
    "policy_owner",
    "policy_reviewers",
    "policy_approval_required",
    "policy_approval_teams",
    "policy_approval_min_approvals",
    "policy_approval_enforced",
}


def test_manifest_policy_pack_emits_suppressible_overridable_findings(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "org-pack.yaml").write_text(
        """
name: Org Release Policy
version: "1.0"
rules:
  - id: ORG-HIGH-RISK-OWNER-MISSING
    title: High-risk production tool has no org owner
    category: org_policy
    severity: high
    confidence: high
    recommendation: Assign an owning team before production release.
    match:
      risk_tags: [financial_action]
      source_types: [openapi]
      environment_targets: [production_like]
      missing_owner: true
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: policy-pack
agent:
  name: policy-pack-agent
  declared_purpose:
    - process refunds
environment:
  target: production_like
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - {tool: create_refund, source_id: api}
      handoffs: []
      reason: reviewed policy-pack fixture binding
risk_overrides:
  tools:
    create_refund:
      tags: [financial_action]
      reason: reviewed financial policy fixture
checks:
  policy_packs:
    - path: org-pack.yaml
  severity_overrides:
    ORG-HIGH-RISK-OWNER-MISSING: medium
  acknowledge_overrides:
    # v0.17 (M1): high → medium crosses the high → normal tier
    # boundary, so the override requires explicit acknowledgement.
    # Policy-pack rule IDs go through the same tier contract as
    # built-ins.
    - check_id: ORG-HIGH-RISK-OWNER-MISSING
      reason: internal tracker covers owner attribution off-band
  ignore:
    - check_id: ORG-HIGH-RISK-OWNER-MISSING
      tool: create_refund
      reason: tracked in release exception
""",
        encoding="utf-8",
    )

    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json", "markdown", "sarif"],
        ci_mode="advisory",
    )

    assert exit_code == 0
    assert report.loaded_policy_packs[0].model_dump(mode="json") == {
        "id": "org-pack",
        "name": "Org Release Policy",
        "version": "1.0",
        "path": "org-pack.yaml",
        "source": None,
        "sha256": None,
        "sha256_status": "unpinned",
        "owner": None,
        "rule_count": 1,
    }
    finding = next(item for item in report.findings if item.check_id == "ORG-HIGH-RISK-OWNER-MISSING")
    assert finding.tool_name == "create_refund"
    assert finding.severity == "medium"
    assert finding.suppressed is True
    assert finding.evidence["default_severity"] == "high"
    assert finding.evidence["risk_tags"] == ["financial_action"]
    assert finding.capability_refs
    assert finding.capability_policy_evidence is not None
    assert finding.capability_policy_evidence.capability_id in finding.capability_refs
    markdown = (tmp_path / "reports" / "report.md").read_text(encoding="utf-8")
    assert "Loaded Policy Packs" in markdown
    sarif = (tmp_path / "reports" / "report.sarif").read_text(encoding="utf-8")
    assert "ORG-HIGH-RISK-OWNER-MISSING" not in sarif


def test_policy_pack_org_metadata_and_pin_are_reported(tmp_path):
    _write_openapi(tmp_path)
    pack_text = """
id: org-release
name: Org Release Policy
version: "3.0"
owner: agent-platform
rules:
  - id: ORG-ROUTED-REFUND-RULE
    title: Refund rule routed to security
    category: org_policy
    severity: high
    recommendation: Route to security before release.
    owner: security
    reviewers: [agent-platform]
    approval:
      required: true
      teams: [security]
      min_approvals: 1
    match:
      risk_tags: [financial_action]
      source_types: [openapi]
"""
    (tmp_path / "org-pack.yaml").write_text(pack_text, encoding="utf-8")
    digest = hashlib.sha256((tmp_path / "org-pack.yaml").read_bytes()).hexdigest()
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + f"""
organization:
  id: acme
  teams:
    agent-platform:
      reviewers: ["@acme/agent-platform"]
    security:
      reviewers: ["@acme/security"]
checks:
  policy_packs:
    - id: org-release
      path: org-pack.yaml
      source: github.com/acme/shipgate-policies@v3
      sha256: {digest}
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.loaded_policy_packs[0].model_dump(mode="json") == {
        "id": "org-release",
        "name": "Org Release Policy",
        "version": "3.0",
        "path": "org-pack.yaml",
        "source": "github.com/acme/shipgate-policies@v3",
        "sha256": digest,
        "sha256_status": "verified",
        "owner": "agent-platform",
        "rule_count": 1,
    }
    finding = next(
        item for item in report.findings if item.check_id == "ORG-ROUTED-REFUND-RULE"
    )
    assert finding.evidence["policy_pack_source"] == "github.com/acme/shipgate-policies@v3"
    assert finding.evidence["policy_pack_sha256"] == digest
    assert finding.evidence["policy_pack_sha256_status"] == "verified"
    assert ROUTING_EVIDENCE_KEYS.isdisjoint(finding.evidence)
    assert finding.policy_routing is not None
    assert finding.policy_routing.model_dump(mode="json") == {
        "owner": "security",
        "reviewers": ["agent-platform"],
        "approval": {
            "required": True,
            "teams": ["security"],
            "min_approvals": 1,
            "enforced": False,
        },
    }


def test_policy_pack_routing_metadata_does_not_change_fingerprint_or_gate(tmp_path):
    _write_openapi(tmp_path)
    pack_path = tmp_path / "org-pack.yaml"
    pack_template = """
name: Org Release Policy
version: "3.0"
rules:
  - id: ORG-ROUTING-ONLY-CHANGE
    title: Routing-only metadata change
    category: org_policy
    severity: medium
    recommendation: Route before release.
    owner: {owner}
    reviewers: [{reviewer}]
    approval:
      required: true
      teams: [{team}]
      min_approvals: {min_approvals}
    match:
      risk_tags: [financial_action]
      source_types: [openapi]
"""
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
organization:
  id: acme
  teams:
    security:
      reviewers: ["@acme/security"]
    agent-platform:
      reviewers: ["@acme/agent-platform"]
checks:
  policy_packs:
    - path: org-pack.yaml
""",
        encoding="utf-8",
    )
    pack_path.write_text(
        pack_template.format(
            owner="security",
            reviewer="agent-platform",
            team="security",
            min_approvals=1,
        ),
        encoding="utf-8",
    )
    report_a, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports-a",
        formats=["json"],
        ci_mode="advisory",
    )
    pack_path.write_text(
        pack_template.format(
            owner="agent-platform",
            reviewer="security",
            team="agent-platform",
            min_approvals=2,
        ),
        encoding="utf-8",
    )
    report_b, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports-b",
        formats=["json"],
        ci_mode="advisory",
    )

    finding_a = next(
        item for item in report_a.findings if item.check_id == "ORG-ROUTING-ONLY-CHANGE"
    )
    finding_b = next(
        item for item in report_b.findings if item.check_id == "ORG-ROUTING-ONLY-CHANGE"
    )
    assert finding_a.fingerprint == finding_b.fingerprint
    assert finding_a.evidence == finding_b.evidence
    assert finding_a.policy_routing != finding_b.policy_routing
    assert report_a.release_decision is not None
    assert report_b.release_decision is not None
    assert report_a.release_decision.decision == report_b.release_decision.decision


def test_policy_pack_approval_routing_alone_does_not_block_release(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "approval-pack.yaml").write_text(
        """
name: Approval Routing Policy
rules:
  - id: ORG-APPROVAL-ROUTING-ONLY
    title: Route approval metadata only
    category: org_policy
    severity: low
    approval:
      required: true
      teams: [security]
      min_approvals: 1
    recommendation: Route to security.
    match:
      source_types: [openapi]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
organization:
  id: acme
  teams:
    security:
      reviewers: ["@acme/security"]
checks:
  policy_packs:
    - path: approval-pack.yaml
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    finding = next(
        item for item in report.findings if item.check_id == "ORG-APPROVAL-ROUTING-ONLY"
    )
    assert finding.blocks_release is False
    assert finding.policy_routing is not None
    assert finding.policy_routing.approval.required is True
    assert report.release_decision is not None
    assert all(
        item.check_id != "ORG-APPROVAL-ROUTING-ONLY"
        for item in report.release_decision.blockers
    )


def test_policy_pack_legacy_v027_baseline_cannot_accept_new_supported_finding(
    tmp_path,
):
    _write_openapi(tmp_path)
    (tmp_path / "org-pack.yaml").write_text(
        """
name: Org Release Policy
rules:
  - id: ORG-LEGACY-ROUTING-FP
    title: Legacy routing fingerprint
    category: org_policy
    severity: high
    recommendation: Route before release.
    owner: security
    reviewers: [agent-platform]
    approval:
      required: true
      teams: [security]
      min_approvals: 1
    match:
      risk_tags: [financial_action]
      source_types: [openapi]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
organization:
  id: acme
  teams:
    security:
      reviewers: ["@acme/security"]
    agent-platform:
      reviewers: ["@acme/agent-platform"]
checks:
  policy_packs:
    - path: org-pack.yaml
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports-a",
        formats=["json"],
        ci_mode="advisory",
    )
    finding = next(
        item for item in report.findings if item.check_id == "ORG-LEGACY-ROUTING-FP"
    )
    legacy_fingerprint = legacy_policy_routing_fingerprint(finding)
    assert legacy_fingerprint is not None
    assert legacy_fingerprint != finding.fingerprint
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        BaselineFile(
            created_at="2026-01-01T00:00:00Z",
            source_report_run_id="v027",
            findings=[
                BaselineFinding(
                    fingerprint=legacy_fingerprint,
                    check_id=finding.check_id,
                    tool_name=finding.tool_name,
                    severity=finding.severity,
                    title=finding.title,
                )
            ],
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )

    report_with_baseline, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports-b",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
    )

    matched = next(
        item
        for item in report_with_baseline.findings
        if item.check_id == "ORG-LEGACY-ROUTING-FP"
    )
    assert matched.fingerprint == finding.fingerprint
    assert matched.baseline_status == "new"
    assert matched.support is not None
    assert matched.support.support_hash
    assert report_with_baseline.baseline is not None
    assert report_with_baseline.baseline.matched_count == 0
    assert report_with_baseline.baseline.resolved_count == 1


def test_cli_policy_pack_override_and_parameter_predicate(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "parameter-pack.yaml").write_text(
        """
name: Parameter Policy
rules:
  - id: ORG-REFUND-AMOUNT-BOUNDS
    title: Refund amount must be bounded
    category: org_policy
    severity: critical
    recommendation: Add a maximum refund amount.
    match:
      source_types: [openapi]
      parameters:
        - name: amount
          types: [number]
          missing_maximum: true
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(_manifest_without_policy_pack(), encoding="utf-8")

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json", "sarif"],
        ci_mode="advisory",
        policy_pack_paths=[Path("parameter-pack.yaml")],
    )

    finding = next(item for item in report.findings if item.check_id == "ORG-REFUND-AMOUNT-BOUNDS")
    assert finding.tool_name == "create_refund"
    assert finding.evidence["parameters"] == [
        {"name": "amount", "type": "number", "required": True, "maximum": None}
    ]
    sarif = (tmp_path / "reports" / "report.sarif").read_text(encoding="utf-8")
    assert "ORG-REFUND-AMOUNT-BOUNDS" in sarif


def test_policy_pack_rule_can_block_release_independent_of_severity(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "release-pack.yaml").write_text(
        """
name: Release Policy
rules:
  - id: ORG-MEDIUM-BLOCKER
    title: Medium org policy is release-blocking
    category: org_policy
    severity: medium
    block: true
    recommendation: Fix the org release rule before merge.
    match:
      source_types: [openapi]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(_manifest_without_policy_pack(), encoding="utf-8")

    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="strict",
        policy_pack_paths=[Path("release-pack.yaml")],
    )

    assert exit_code == 20
    finding = next(item for item in report.findings if item.check_id == "ORG-MEDIUM-BLOCKER")
    assert finding.severity == "medium"
    assert finding.blocks_release is True
    assert report.release_decision is not None
    assert any(item.check_id == "ORG-MEDIUM-BLOCKER" for item in report.release_decision.blockers)


def test_heuristic_policy_match_routes_to_evidence_gap_not_finding(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "heuristic-pack.yaml").write_text(
        """
name: Heuristic laundering regression
rules:
  - id: ORG-HEURISTIC-FINANCIAL-BLOCK
    title: Financial names must not self-create blockers
    category: org_policy
    severity: critical
    confidence: high
    block: true
    recommendation: Provide reviewed financial-effect evidence.
    match:
      risk_tags: [financial_action]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack(reviewed_financial=False)
        + """
checks:
  policy_packs:
    - path: heuristic-pack.yaml
""",
        encoding="utf-8",
    )

    report, exit_code = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="strict",
        no_heuristics=True,
    )

    assert exit_code == 20
    assert not any(
        item.check_id == "ORG-HEURISTIC-FINANCIAL-BLOCK"
        for item in report.findings
    )
    gap = next(
        item
        for item in report.policy_evidence_gaps
        if "ORG-HEURISTIC-FINANCIAL-BLOCK" in item.why
    )
    assert gap.kind == "inferred_policy_applicability"
    assert gap.next_action.kind == "provide_policy_evidence"
    assert report.release_decision is not None
    # Other concrete high findings may take the higher-precedence review route,
    # but the heuristic policy rule itself never becomes blocked or passed.
    assert report.release_decision.decision in {"review_required", "insufficient_evidence"}
    assert report.release_decision.evidence_coverage.policy_gap_count == len(
        report.policy_evidence_gaps
    )
    assert sum(
        "ORG-HEURISTIC-FINANCIAL-BLOCK" in item.why
        for item in report.policy_evidence_gaps
    ) == 1


def test_policy_pack_capability_selector_matches_semantic_subject(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "capability-pack.yaml").write_text(
        """
name: Capability Policy
rules:
  - id: ORG-FINANCIAL-CAPABILITY-APPROVAL
    title: Financial capability needs approval
    category: org_policy
    severity: critical
    recommendation: Add approval for the financial capability.
    match:
      capability:
        tool_names: [create_refund]
        providers: [api]
        effects: [financial_write]
        risk_tags: [financial_action]
        source_types: [openapi]
        financial: true
        high_risk: true
        missing_owner: true
        missing_auth_scopes: true
        missing_approval_policy: true
        missing_confirmation_policy: true
        missing_idempotency_policy: true
        parameters:
          - name: amount
            types: [number]
            required: true
            missing_maximum: true
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: capability-pack.yaml
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    finding = next(
        item
        for item in report.findings
        if item.check_id == "ORG-FINANCIAL-CAPABILITY-APPROVAL"
    )
    assert finding.tool_name == "create_refund"
    assert finding.capability_refs
    assert finding.capability_policy_evidence is not None
    evidence = finding.capability_policy_evidence
    assert evidence.capability_id == finding.capability_refs[0]
    assert evidence.identity["tool_name"] == "create_refund"
    assert evidence.effect["effect"] == "financial_write"
    assert evidence.matched_predicates["capability"]["risk_tags"] == [
        "financial_action"
    ]
    assert evidence.matched_predicates["capability"]["parameters"] == [
        {"name": "amount", "type": "number", "required": True, "maximum": None}
    ]
    assert report.release_decision is not None
    blocker = next(
        item
        for item in report.release_decision.blockers
        if item.check_id == "ORG-FINANCIAL-CAPABILITY-APPROVAL"
    )
    assert blocker.capability_refs == finding.capability_refs


def test_scan_cli_accepts_policy_pack_override(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "cli-pack.yaml").write_text(
        """
name: CLI Policy
rules:
  - id: ORG-CLI-POLICY
    description: CLI policy description.
    severity: medium
    recommendation: Review CLI policy finding.
    match:
      source_types: [openapi]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(_manifest_without_policy_pack(), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "--config",
            str(tmp_path / "shipgate.yaml"),
            "--out",
            str(tmp_path / "reports"),
            "--format",
            "json",
            "--policy-pack",
            "cli-pack.yaml",
            "--ci-mode",
            "advisory",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads((tmp_path / "reports" / "report.json").read_text(encoding="utf-8"))
    assert payload["loaded_policy_packs"][0]["name"] == "CLI Policy"
    finding = next(finding for finding in payload["findings"] if finding["check_id"] == "ORG-CLI-POLICY")
    assert finding["title"] == "CLI policy description."


def test_policy_pack_negative_predicates_do_not_fire(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "owner-pack.yaml").write_text(
        """
name: Owner Policy
rules:
  - id: ORG-OWNER-MISSING
    severity: high
    recommendation: Assign an owner.
    match:
      risk_tags: [financial_action]
      missing_owner: true
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
risk_overrides:
  tools:
    create_refund:
      owner: payments-team
      reason: production owner
checks:
  policy_packs:
    - path: owner-pack.yaml
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert "ORG-OWNER-MISSING" not in {finding.check_id for finding in report.findings}


def test_policy_pack_validation_errors_are_clear(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "ship-pack.yaml").write_text(
        """
name: Invalid
rules:
  - id: SHIP-ORG-RULE
    severity: high
    recommendation: Do not use reserved namespace.
    match: {}
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: ship-pack.yaml
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="reserved for built-in checks"):
        run_scan(
            config_path=tmp_path / "shipgate.yaml",
            output_dir=tmp_path / "reports",
            formats=["json"],
        )


def test_duplicate_policy_pack_rule_ids_are_rejected(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "duplicate-pack.yaml").write_text(
        """
name: Invalid
rules:
  - id: ORG-DUPLICATE
    severity: high
    recommendation: First.
    match: {}
  - id: ORG-DUPLICATE
    severity: medium
    recommendation: Second.
    match: {}
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: duplicate-pack.yaml
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Duplicate policy pack rule id"):
        run_scan(config_path=tmp_path / "shipgate.yaml", output_dir=tmp_path / "reports")


def test_policy_pack_path_traversal_is_rejected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_openapi(project)
    (tmp_path / "outside.yaml").write_text(
        """
name: Outside
rules: []
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: ../outside.yaml
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="resolves outside manifest directory"):
        run_scan(config_path=project / "shipgate.yaml", output_dir=project / "reports")


def test_malformed_policy_pack_is_rejected(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "malformed-pack.yaml").write_text(
        """
name: Malformed
rules:
  - id: ORG-MALFORMED
    severity: urgent
    recommendation: Invalid severity.
    match: {}
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: malformed-pack.yaml
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Invalid policy pack"):
        run_scan(config_path=tmp_path / "shipgate.yaml", output_dir=tmp_path / "reports")


def test_policy_pack_unknown_org_team_reference_is_rejected(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "org-pack.yaml").write_text(
        """
name: Routed Pack
rules:
  - id: ORG-UNKNOWN-TEAM
    severity: high
    recommendation: Route to a known team.
    owner: payments
    match:
      source_types: [openapi]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
organization:
  id: acme
  teams:
    security:
      reviewers: ["@acme/security"]
checks:
  policy_packs:
    - path: org-pack.yaml
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown organization team 'payments'"):
        run_scan(
            config_path=tmp_path / "shipgate.yaml",
            output_dir=tmp_path / "reports",
            formats=["json"],
            ci_mode="advisory",
        )


def test_optional_missing_policy_pack_warns(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: missing-pack.yaml
      optional: true
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert any("Optional policy pack 'missing-pack.yaml' failed to load" in item for item in report.source_warnings)


def _write_openapi(tmp_path: Path) -> None:
    (tmp_path / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Refund API
  version: "1.0"
paths:
  /refunds:
    post:
      operationId: create_refund
      summary: Create a customer refund.
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                amount:
                  type: number
                payment_id:
                  type: string
              required: [amount, payment_id]
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )


def _manifest_without_policy_pack(*, reviewed_financial: bool = True) -> str:
    risk_override = """
risk_overrides:
  tools:
    create_refund:
      tags: [financial_action]
      reason: reviewed financial policy fixture
""" if reviewed_financial else ""
    return """
version: "0.1"
project:
  name: policy-pack
agent:
  name: policy-pack-agent
  declared_purpose:
    - process refunds
environment:
  target: production_like
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - {tool: create_refund, source_id: api}
      handoffs: []
      reason: reviewed policy-pack fixture binding
""" + risk_override


# --- v0.2: combinators, numeric predicates, sha256 pin ----------------------


def _write_bounded_openapi(tmp_path, maximum: int | None = 5000):
    bound = f"\n                  maximum: {maximum}" if maximum is not None else ""
    (tmp_path / "openapi.yaml").write_text(
        f"""
openapi: 3.1.0
info:
  title: Refund API
  version: "1.0"
paths:
  /refunds:
    post:
      operationId: create_refund
      summary: Create a customer refund.
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                amount:
                  type: number{bound}
                payment_id:
                  type: string
              required: [amount, payment_id]
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )


_V2_PACK = """
name: Org Approval Policy v2
version: "2.0"
rules:
  - id: ORG-LARGE-FINANCIAL-NEEDS-APPROVAL
    title: Large or unbounded financial action requires declared approval
    category: org_policy
    severity: critical
    block: true
    confidence: high
    recommendation: Declare an approval policy or bound the amount below 1000.
    match:
      all_of:
        - risk_tags: [financial_action]
        - missing_approval_policy: true
        - any_of:
            - parameters:
                - name: amount
                  maximum_above: 1000
            - parameters:
                - name: amount
                  missing_maximum: true
"""


def _run_v2_scan(tmp_path):
    (tmp_path / "org-pack-v2.yaml").write_text(_V2_PACK, encoding="utf-8")
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: org-pack-v2.yaml
""",
        encoding="utf-8",
    )
    return run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )


def test_v2_combinator_fires_on_large_bounded_amount(tmp_path):
    _write_bounded_openapi(tmp_path, maximum=5000)
    report, _ = _run_v2_scan(tmp_path)
    finding = next(
        item
        for item in report.findings
        if item.check_id == "ORG-LARGE-FINANCIAL-NEEDS-APPROVAL"
    )
    assert finding.blocks_release is True
    # The any_of branch is nested inside the all_of evidence, mirroring
    # the rule structure.
    matched = finding.evidence["all_of"][2]["any_of"]
    assert matched["index"] == 0
    assert matched["matched"]["parameters"][0]["maximum"] == 5000


def test_v2_combinator_fires_on_unbounded_amount(tmp_path):
    _write_bounded_openapi(tmp_path, maximum=None)
    report, _ = _run_v2_scan(tmp_path)
    finding = next(
        item
        for item in report.findings
        if item.check_id == "ORG-LARGE-FINANCIAL-NEEDS-APPROVAL"
    )
    assert finding.evidence["all_of"][2]["any_of"]["index"] == 1


def test_v2_combinator_does_not_fire_on_small_bounded_amount(tmp_path):
    _write_bounded_openapi(tmp_path, maximum=500)
    report, _ = _run_v2_scan(tmp_path)
    assert not [
        item
        for item in report.findings
        if item.check_id == "ORG-LARGE-FINANCIAL-NEEDS-APPROVAL"
    ]


def test_v2_combinator_does_not_fire_when_approval_declared(tmp_path):
    _write_bounded_openapi(tmp_path, maximum=5000)
    (tmp_path / "org-pack-v2.yaml").write_text(_V2_PACK, encoding="utf-8")
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
policies:
  require_approval_for_tools:
    - tool: create_refund
      reason: refunds move money
checks:
  policy_packs:
    - path: org-pack-v2.yaml
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    assert not [
        item
        for item in report.findings
        if item.check_id == "ORG-LARGE-FINANCIAL-NEEDS-APPROVAL"
    ]


def test_v2_none_of_excludes_matching_subjects(tmp_path):
    _write_bounded_openapi(tmp_path, maximum=5000)
    (tmp_path / "org-pack-v2.yaml").write_text(
        """
name: None-of Policy
rules:
  - id: ORG-NON-FINANCIAL-ONLY
    title: Fires only for non-financial tools
    severity: medium
    recommendation: n/a
    match:
      none_of:
        - risk_tags: [financial_action]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: org-pack-v2.yaml
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    # create_refund is financial → excluded by none_of.
    assert not [
        item
        for item in report.findings
        if item.check_id == "ORG-NON-FINANCIAL-ONLY"
    ]


def test_v1_flat_pack_still_loads_unchanged(tmp_path):
    """Backward compatibility: a v0.1-shaped pack with only flat fields."""
    _write_openapi(tmp_path)
    (tmp_path / "org-pack.yaml").write_text(
        """
name: Flat v1 Pack
rules:
  - id: ORG-FLAT-RULE
    title: Flat rule
    severity: high
    recommendation: n/a
    match:
      risk_tags: [financial_action]
      missing_owner: true
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: org-pack.yaml
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    assert [item for item in report.findings if item.check_id == "ORG-FLAT-RULE"]


def test_sha256_pin_accepts_matching_pack(tmp_path):
    _write_openapi(tmp_path)
    pack_text = """
name: Pinned Pack
rules:
  - id: ORG-PINNED-RULE
    title: Pinned rule
    severity: high
    recommendation: n/a
    match:
      risk_tags: [financial_action]
"""
    (tmp_path / "org-pack.yaml").write_text(pack_text, encoding="utf-8")
    digest = hashlib.sha256(
        (tmp_path / "org-pack.yaml").read_bytes()
    ).hexdigest()
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + f"""
checks:
  policy_packs:
    - path: org-pack.yaml
      sha256: {digest}
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    assert [item for item in report.findings if item.check_id == "ORG-PINNED-RULE"]


def test_sha256_pin_rejects_tampered_pack(tmp_path):
    _write_openapi(tmp_path)
    (tmp_path / "org-pack.yaml").write_text(
        """
name: Tampered Pack
rules:
  - id: ORG-PINNED-RULE
    title: Pinned rule
    severity: high
    recommendation: n/a
    match:
      risk_tags: [financial_action]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        _manifest_without_policy_pack()
        + """
checks:
  policy_packs:
    - path: org-pack.yaml
      sha256: "0000000000000000000000000000000000000000000000000000000000000000"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="does not match its pinned sha256"):
        run_scan(
            config_path=tmp_path / "shipgate.yaml",
            output_dir=tmp_path / "reports",
            formats=["json"],
            ci_mode="advisory",
        )
