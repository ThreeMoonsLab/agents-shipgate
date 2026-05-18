import hashlib
import json
from pathlib import Path

from agents_shipgate.cli.explain_finding import explain_finding_payload
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.models import ReadinessReport
from agents_shipgate.core.privacy import RedactionStats, redact_data


def test_redact_data_redacts_nested_patterns_and_sensitive_keys():
    openai_key = "sk-aaaaaaaaaaaaaaaaaaaaaaaa"
    bearer = "Bearer abcdefghijklmnop"
    labeled = "abcdef0123456789abcdef012345"
    payload = {
        "description": f"call with {openai_key}",
        "nested": [{"Authorization": bearer}],
        "password": labeled,
        "tokenUrl": "https://auth.example.test/token",
    }
    stats = RedactionStats()

    redacted = redact_data(payload, stats=stats, path="$")
    rendered = json.dumps(redacted, sort_keys=True)

    assert openai_key not in rendered
    assert bearer not in rendered
    assert labeled not in rendered
    assert hashlib.sha256(openai_key.encode()).hexdigest() not in rendered
    assert "[REDACTED:openai_api_key]" in rendered
    assert "[REDACTED:bearer_token]" in rendered
    assert "[REDACTED:sensitive_field]" in rendered
    assert redacted["tokenUrl"] == "https://auth.example.test/token"
    assert stats.occurrence_count == 3
    assert all(openai_key not in path for path in stats.path_kinds)


def test_scan_redacts_public_outputs_and_reports_privacy_audit(tmp_path, monkeypatch):
    raw_secret = "sk-privacyaaaaaaaaaaaaaaaa"
    github_token = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    agent_secret = "sk-agentaaaaaaaaaaaaaaaa"
    project = tmp_path / "project"
    reports = tmp_path / "reports"
    project.mkdir()
    summary_path = tmp_path / "github-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    (project / "openapi.yaml").write_text(
        f"""
openapi: 3.1.0
info:
  title: Privacy Fixture
  version: "1.0"
paths:
  /refund:
    post:
      operationId: refund_{raw_secret}
      summary: Refund customer with {raw_secret}
      description: "Uses api_key: abcdef0123456789abcdef012345 before calling billing."
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                token:
                  type: string
                  default: {github_token}
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        f"""
version: "0.1"
project:
  name: privacy-project
agent:
  name: privacy-agent-{agent_secret}
  declared_purpose:
    - refund support requests
environment:
  target: local
tool_sources:
  - id: openapi
    type: openapi
    path: openapi.yaml
    optional: false
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=reports,
        formats=["markdown", "json", "sarif"],
        ci_mode="advisory",
        packet_enabled=True,
        packet_formats=["md", "json", "html"],
    )

    combined = "\n".join(
        [
            (reports / "report.json").read_text(encoding="utf-8"),
            (reports / "report.md").read_text(encoding="utf-8"),
            (reports / "report.sarif").read_text(encoding="utf-8"),
            (reports / "packet.json").read_text(encoding="utf-8"),
            (reports / "packet.md").read_text(encoding="utf-8"),
            (reports / "packet.html").read_text(encoding="utf-8"),
            summary_path.read_text(encoding="utf-8"),
        ]
    )

    for raw in (raw_secret, github_token, agent_secret):
        assert raw not in combined
        assert hashlib.sha256(raw.encode()).hexdigest() not in combined
    assert "[REDACTED:openai_api_key]" in combined
    assert report.privacy_audit is not None
    assert report.privacy_audit.enabled is True
    assert report.privacy_audit.redacted_occurrence_count > 0
    assert any(
        "github_token" in row.kinds for row in report.privacy_audit.redacted_paths
    )
    assert {
        "json",
        "markdown",
        "sarif",
        "packet_json",
        "packet_md",
        "packet_html",
        "github_step_summary",
    } <= set(report.privacy_audit.output_surfaces)
    assert all(raw_secret not in row.path for row in report.privacy_audit.redacted_paths)

    report_json = json.loads((reports / "report.json").read_text(encoding="utf-8"))
    secret_finding = next(
        finding
        for finding in report_json["findings"]
        if finding["check_id"] == "SHIP-DOC-SECRET-IN-DESCRIPTION"
    )
    explanation = explain_finding_payload(
        fingerprint=secret_finding["fingerprint"],
        report_path=reports / "report.json",
    )
    rendered_explanation = json.dumps(explanation, sort_keys=True)
    assert raw_secret not in rendered_explanation
    assert github_token not in rendered_explanation
    assert agent_secret not in rendered_explanation


def test_report_sensitive_field_inventory_covers_current_report_fields():
    inventory = json.loads(
        Path("docs/report-sensitive-fields.json").read_text(encoding="utf-8")
    )
    report_paths = {
        entry["path"]
        for entry in inventory["fields"]
        if entry.get("surface") == "report"
    }

    assert set(ReadinessReport.model_fields) <= report_paths
    assert {
        "findings",
        "source_warnings",
        "tool_inventory",
        "tool_surface_facts",
        "action_surface_facts",
        "action_surface_diff",
        "api_surface",
        "anthropic_surface",
        "frameworks",
        "codex_plugin_surface",
        "agent",
        "project",
        "privacy_audit",
    } <= report_paths
