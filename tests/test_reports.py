import json
from pathlib import Path

from jsonschema import validate

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.report.markdown import _safe_markdown_text, render_markdown_report

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")
EXPECTED_MARKDOWN = Path("samples/support_refund_agent/expected/report.md")
OPENAI_API_SAMPLE = Path("samples/simple_openai_api_agent/shipgate.yaml")
OPENAI_API_EXPECTED_MARKDOWN = Path("samples/simple_openai_api_agent/expected/report.md")
REPORT_SCHEMA = Path("docs/report-schema.v0.1.json")
REPORT_SCHEMA_V02 = Path("docs/report-schema.v0.2.json")
REPORT_SCHEMA_V03 = Path("docs/report-schema.v0.3.json")


def test_sample_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    actual = actual.replace(str(Path.cwd()), "<REPO>")
    expected = EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_openai_api_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    expected = OPENAI_API_EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_json_report_contains_integration_contract_keys(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report.model_dump(mode="json")

    assert payload["summary"]["status"] == "release_blockers_detected"
    assert "critical_count" in payload["summary"]
    assert "title" in payload["findings"][0]
    assert "severity" in payload["findings"][0]
    assert "fingerprint" in payload["findings"][0]
    assert "tool_inventory" in payload
    assert "loaded_plugins" in payload
    assert payload["loaded_plugins"] == []
    assert payload["schema_version"] == "0.1"
    assert payload["report_schema_version"] == "0.3"
    assert "frameworks" in payload


def test_report_paths_use_absolute_path_when_output_escapes_manifest_base(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert str(report.generated_reports["json"]).startswith(str(tmp_path))
    assert not str(report.generated_reports["json"]).startswith("..")


def test_json_report_is_reproducible_for_same_inputs(tmp_path):
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    first = (tmp_path / "report.json").read_text(encoding="utf-8")
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    second = (tmp_path / "report.json").read_text(encoding="utf-8")

    assert first == second


def test_json_schema_is_published():
    text = REPORT_SCHEMA.read_text(encoding="utf-8")
    schema = json.loads(text)

    assert "Agents Shipgate Readiness Report v0.1" in text
    assert '"schema_version"' in text
    inventory_item = schema["properties"]["tool_inventory"]["items"]
    assert {"name", "source_type", "risk_tags", "confidence"} <= set(
        inventory_item["required"]
    )
    api_surface = schema["properties"]["api_surface"]["anyOf"][0]
    assert {
        "prompt_file_count",
        "tool_file_count",
        "response_format_count",
        "model_config_present",
    } <= set(api_surface["required"])


def test_json_report_validates_against_v03_schema(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    schema = json.loads(REPORT_SCHEMA_V03.read_text(encoding="utf-8"))

    validate(instance=report.model_dump(mode="json"), schema=schema)


def test_markdown_escapes_user_controlled_tool_metadata(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Injection
  version: "1.0"
paths:
  /records:
    post:
      operationId: "[Click here](https://evil.example)"
      summary: "Update [records](https://evil.example)"
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                updates:
                  type: object
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: "**bold** _team_ <tag>"
agent:
  name: markdown-agent
  declared_purpose:
    - update records
environment:
  target: local
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
policies:
  require_approval_for_tools:
    - "[Click here](https://evil.example)"
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    markdown = render_markdown_report(report)

    assert "[Click here](https://evil.example)" not in markdown
    assert "\\[Click here\\]\\(https://evil.example\\)" in markdown
    assert "**bold** _team_ <tag>" not in markdown
    assert "\\*\\*bold\\*\\* \\_team\\_ \\<tag\\>" in markdown
    assert _safe_markdown_text("**bold** _underscore_ <tag>") == (
        "\\*\\*bold\\*\\* \\_underscore\\_ \\<tag\\>"
    )


def test_clean_report_has_affirmative_pass_result(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up internal documentation metadata for an existing support article.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: clean-test
agent:
  name: clean-agent
  declared_purpose:
    - look up documentation metadata
environment:
  target: local
tool_sources:
  - id: docs
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert "Result: PASS - no static findings across 1 tools." in render_markdown_report(report)
