import hashlib
import json
from pathlib import Path

from agents_shipgate.cli.explain_finding import explain_finding_payload
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.baseline import apply_baseline, baseline_resolved_fingerprints
from agents_shipgate.core.findings import (
    assign_finding_ids,
    dedupe_findings,
    finding_fingerprint,
)
from agents_shipgate.core.logging import _might_contain_sensitive_payload
from agents_shipgate.core.privacy import (
    RedactionStats,
    redact_data,
    redact_text,
    sanitize_findings,
)
from agents_shipgate.schemas.baseline import BaselineFile, BaselineFinding
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.report import Finding, ReadinessReport


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


def test_redact_text_covers_quoted_labeled_and_common_secret_patterns():
    secrets = [
        '"api_key":"abcdefghijklmnop1234567890"',
        "password=abcdefghijklmnop1234567890",
        "password=correcthorsebatterystaple",
        "password=12345678901234567890",
        "password=longlonglonglonglongvalue",
        "ASIAABCDEFGHIJKLMNOP",
        "github_pat_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "xoxa-2-aaaaaaaaaaaaaaaa",
        "whsec_aaaaaaaaaaaaaaaaaaaaaaaa",
        "redis://user:password@example.test:6379/0",
        "clickhouse://user:password@example.test/db",
    ]
    stats = RedactionStats()

    redacted = redact_data({"text": " ".join(secrets)}, stats=stats, path="$")
    redacted_text = redacted["text"]
    rendered = json.dumps(redacted, sort_keys=True)

    for secret in secrets:
        assert secret not in rendered
    assert '"api_key":"[REDACTED:labeled_secret_value]"' in redacted_text
    assert "password=[REDACTED:labeled_secret_value]" in redacted_text
    assert "password= [REDACTED:labeled_secret_value]" not in redacted_text
    assert "correcthorsebatterystaple" not in rendered
    assert "12345678901234567890" not in rendered
    assert "longlonglonglonglongvalue" not in rendered
    assert "[REDACTED:aws_access_key]" in rendered
    assert "[REDACTED:github_token]" in rendered
    assert "[REDACTED:slack_token]" in rendered
    assert "[REDACTED:stripe_webhook_secret]" in rendered
    assert rendered.count("[REDACTED:database_url]") == 2


def test_sensitive_parent_keys_force_redact_nested_string_values():
    payload = {
        "credentials": ["alpha", "beta"],
        "credential": {"value": "short"},
        "metadata": {"value": "short"},
    }
    stats = RedactionStats()

    redacted = redact_data(payload, stats=stats, path="$")
    rendered = json.dumps(redacted, sort_keys=True)

    assert "alpha" not in rendered
    assert "beta" not in rendered
    assert '"credential": {"value": "[REDACTED:sensitive_field]"}' in rendered
    assert redacted["metadata"]["value"] == "short"
    assert stats.occurrence_count == 3


def test_sanitized_findings_preserve_distinct_raw_secret_evidence():
    raw_one = "sk-firstaaaaaaaaaaaaaaaa"
    raw_two = "sk-secondaaaaaaaaaaaaaaa"
    source = SourceReference(type="openapi", path="openapi.yaml", start_line=7)
    findings = [
        Finding(
            check_id="SHIP-DOC-SECRET-IN-DESCRIPTION",
            title="tool description appears to contain a secret",
            severity="high",
            category="control_missing",
            tool_name="tool",
            evidence={"description": f"token {raw_one}"},
            confidence="high",
            provenance_kind="static_declaration",
            source=source,
            recommendation="Rotate the exposed credential.",
        ),
        Finding(
            check_id="SHIP-DOC-SECRET-IN-DESCRIPTION",
            title="tool description appears to contain a secret",
            severity="high",
            category="control_missing",
            tool_name="tool",
            evidence={"description": f"token {raw_two}"},
            confidence="high",
            provenance_kind="static_declaration",
            source=source,
            recommendation="Rotate the exposed credential.",
        ),
    ]

    raw_deduped = dedupe_findings(findings)
    public_findings = sanitize_findings(raw_deduped, stats=RedactionStats())
    assign_finding_ids(public_findings)

    assert len(raw_deduped) == 2
    assert len(public_findings) == 2
    assert public_findings[0].fingerprint == public_findings[1].fingerprint
    assert public_findings[0].id != public_findings[1].id


def test_baseline_matches_legacy_raw_secret_fingerprint_after_redaction():
    raw_secret = "sk-baselineaaaaaaaaaaaaaa"
    raw_finding = Finding(
        check_id="SHIP-DOC-SECRET-IN-DESCRIPTION",
        title="tool description appears to contain a secret",
        severity="high",
        category="control_missing",
        tool_name="tool",
        evidence={"description": f"token {raw_secret}"},
        confidence="high",
        provenance_kind="static_declaration",
        recommendation="Rotate the exposed credential.",
    )
    raw_fingerprint = finding_fingerprint(raw_finding)
    public_finding = sanitize_findings([raw_finding], stats=RedactionStats())[0]
    assign_finding_ids([public_finding])
    baseline = BaselineFile(
        created_at="2026-01-01T00:00:00Z",
        source_report_run_id="legacy_v017",
        findings=[
            BaselineFinding(
                fingerprint=raw_fingerprint,
                check_id=raw_finding.check_id,
                tool_name=raw_finding.tool_name,
                severity=raw_finding.severity,
                title=raw_finding.title,
            )
        ],
    )

    summary = apply_baseline(
        [public_finding],
        baseline,
        display_path=".agents-shipgate/baseline.json",
        legacy_fingerprints=[raw_fingerprint],
    )
    stale_issues = baseline_resolved_fingerprints(
        [public_finding],
        baseline,
        legacy_fingerprints=[raw_fingerprint],
    )

    assert public_finding.fingerprint != raw_fingerprint
    assert public_finding.baseline_status == "matched"
    assert summary.matched_count == 1
    assert summary.new_count == 0
    assert summary.resolved_count == 0
    assert stale_issues == []


def test_json_logging_redaction_uses_fast_precheck():
    assert not _might_contain_sensitive_payload({"message": "plain progress"})
    assert not _might_contain_sensitive_payload({"message": "Bearer Stearns is a company"})
    assert _might_contain_sensitive_payload({"message": "Bearer abcdefghijklmnop"})
    assert _might_contain_sensitive_payload({"payload": {"api_key": "value"}})


def test_logging_precheck_covers_every_secret_pattern():
    samples = {
        "openai_api_key": "sk-abcdefghijklmnopqrst",
        "aws_access_key": "AKIAABCDEFGHIJKLMNOP",
        "github_token": "ghp_abcdefghijklmnopqrst",
        "github_pat": "github_pat_abcdefghijklmnopqrst",
        "stripe_key": "sk_live_abcdefghijklmnopqrst",
        "stripe_webhook_secret": "whsec_abcdefghijklmnopqrst",
        "slack_token": "xoxb-abcdefghij",
        "jwt": "eyJabc.eyJdef.signature",
        "bearer_token": "Bearer abcdefghijkl",
        "db_postgres": "postgres://u:p@h/d",
        "db_postgresql": "postgresql://u:p@h/d",
        "db_mysql": "mysql://u:p@h/d",
        "db_mongodb": "mongodb://u:p@h/d",
        "db_mongodb_srv": "mongodb+srv://u:p@h/d",
        "db_redis": "redis://u:p@h/d",
        "db_rediss": "rediss://u:p@h/d",
        "db_mssql": "mssql://u:p@h/d",
        "db_sqlserver": "sqlserver://u:p@h/d",
        "db_clickhouse": "clickhouse://u:p@h/d",
    }

    for kind, sample in samples.items():
        assert redact_text(sample) != sample, (
            f"{kind} sample does not match any secret redaction pattern"
        )
        assert _might_contain_sensitive_payload({"msg": sample}), (
            f"{kind} sample bypasses the logging precheck"
        )


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
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: refund_{raw_secret}
          source_id: openapi
      handoffs: []
      reason: reviewed privacy fixture binding
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


def test_scan_does_not_revalidate_manifest_after_redaction_collapses_action_tools(
    tmp_path,
):
    raw_one = "sk-aaaaaaaaaaaaaaaaaaaa"
    raw_two = "sk-bbbbbbbbbbbbbbbbbbbb"
    project = tmp_path / "project"
    reports = tmp_path / "reports"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        f"""
openapi: 3.1.0
info:
  title: Privacy Action Fixture
  version: "1.0"
paths:
  /one:
    post:
      operationId: "send_{raw_one}"
      responses:
        "200":
          description: ok
  /two:
    post:
      operationId: "send_{raw_two}"
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
  name: privacy-action-project
agent:
  name: privacy-action-agent
  declared_purpose:
    - send messages
environment:
  target: local
action_surface:
  actions:
    - tool: "send_{raw_one}"
      effect: write
    - tool: "send_{raw_two}"
      effect: write
tool_sources:
  - id: openapi
    type: openapi
    path: openapi.yaml
    optional: false
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - tool: "send_{raw_one}"
          source_id: openapi
        - tool: "send_{raw_two}"
          source_id: openapi
      handoffs: []
      reason: reviewed privacy fixture bindings
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=reports,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    report_text = (reports / "report.json").read_text(encoding="utf-8")
    action_ids = [action.action_id for action in report.action_surface_facts.actions]

    assert raw_one not in report_text
    assert raw_two not in report_text
    assert hashlib.sha256(raw_one.encode()).hexdigest() not in report_text
    assert hashlib.sha256(raw_two.encode()).hexdigest() not in report_text
    assert len(action_ids) == 2
    assert len(set(action_ids)) == 2
    assert report.privacy_audit.redacted_occurrence_count > 0


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
        "findings[].capability_policy_evidence",
        "findings[].capability_policy_evidence.authority.scopes",
        "findings[].capability_policy_evidence.hashes",
        "findings[].capability_policy_evidence.source",
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


def test_redact_text_passthrough_only_for_known_marker_kinds():
    # A genuine marker we emitted earlier must pass through unchanged
    # (idempotent re-redaction, no spurious stats).
    stats = RedactionStats()
    marker = "[REDACTED:sensitive_field]"
    assert (
        redact_text(marker, stats=stats, path="$", force_kind="sensitive_field")
        == marker
    )
    assert stats.occurrence_count == 0


def test_redact_text_marker_lookalike_cannot_bypass_forced_redaction():
    # A scanned value formatted like a marker but with an unknown kind is
    # attacker-controllable text, not our marker. Under a sensitive key it
    # must still be force-redacted — otherwise lowercase secret material
    # can be smuggled through inside "[REDACTED:...]" syntax.
    stats = RedactionStats()
    smuggled = "[REDACTED:my-actual-lowercase-secret-0123]"
    redacted = redact_text(
        smuggled, stats=stats, path="$.password", force_kind="sensitive_field"
    )
    assert redacted == "[REDACTED:sensitive_field]"
    assert "my-actual-lowercase-secret-0123" not in redacted
    assert stats.occurrence_count == 1


def test_redact_data_marker_lookalike_under_sensitive_key_is_redacted():
    payload = {"api_key": "[REDACTED:password-is-hunter2]"}
    redacted = redact_data(payload)
    assert "hunter2" not in json.dumps(redacted)


def test_redact_text_secret_inside_marker_lookalike_is_still_pattern_redacted():
    # Even without a sensitive key, a real secret wrapped in marker syntax
    # must not survive pattern redaction.
    value = "[REDACTED:sk-aaaaaaaaaaaaaaaaaaaaaaaa]"
    redacted = redact_text(value)
    assert "sk-aaaaaaaaaaaaaaaaaaaaaaaa" not in redacted
