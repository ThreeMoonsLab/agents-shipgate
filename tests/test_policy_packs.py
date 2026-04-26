import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.errors import ConfigError

runner = CliRunner()


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
checks:
  policy_packs:
    - path: org-pack.yaml
  severity_overrides:
    ORG-HIGH-RISK-OWNER-MISSING: medium
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
        "rule_count": 1,
    }
    finding = next(item for item in report.findings if item.check_id == "ORG-HIGH-RISK-OWNER-MISSING")
    assert finding.tool_name == "create_refund"
    assert finding.severity == "medium"
    assert finding.suppressed is True
    assert finding.evidence["default_severity"] == "high"
    markdown = (tmp_path / "reports" / "report.md").read_text(encoding="utf-8")
    assert "Loaded Policy Packs" in markdown
    sarif = (tmp_path / "reports" / "report.sarif").read_text(encoding="utf-8")
    assert "ORG-HIGH-RISK-OWNER-MISSING" not in sarif


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


def _manifest_without_policy_pack() -> str:
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
"""
