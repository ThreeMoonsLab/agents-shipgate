from pathlib import Path

import pytest

from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.config.schema import ArtifactPathConfig, OpenAIApiConfig
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.inputs.openai_api import load_openai_api_artifacts

SAMPLE = Path("samples/simple_openai_api_agent/shipgate.yaml")


def test_api_only_manifest_is_valid_and_prompt_files_satisfy_scope_text():
    manifest = load_manifest(SAMPLE)

    assert manifest.tool_sources == []
    assert manifest.openai_api is not None
    assert manifest.openai_api.prompt_files == ["prompts/support_refund.md"]


def test_manifest_requires_tool_sources_or_openai_api(tmp_path):
    manifest_path = tmp_path / "shipgate.yaml"
    manifest_path.write_text(
        """
version: "0.1"
project:
  name: invalid
agent:
  name: invalid-agent
  declared_purpose:
    - test
environment:
  target: local
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="tool_sources, openai_api"):
        load_manifest(manifest_path)


def test_openai_api_loader_normalizes_responses_and_chat_style_tools():
    manifest = load_manifest(SAMPLE)
    source, artifacts = load_openai_api_artifacts(manifest.openai_api, SAMPLE.parent)

    assert source is not None
    assert artifacts is not None
    assert {tool.name for tool in source.tools} == {"create_refund", "send_customer_email"}

    refund = next(tool for tool in source.tools if tool.name == "create_refund")
    email = next(tool for tool in source.tools if tool.name == "send_customer_email")

    assert refund.source_type == "openai_api"
    assert refund.annotations["openaiStrict"] is False
    assert any(parameter.name == "amount" for parameter in refund.parameters)
    assert email.annotations["openaiStrict"] is True
    assert artifacts.response_formats[0].json_schema["type"] == "object"
    assert artifacts.retry_policy()["max_attempts"] == 2


def test_openai_api_loader_accepts_pure_function_schema_and_response_wrapper(tmp_path):
    (tmp_path / "prompt.md").write_text("Answer support questions.", encoding="utf-8")
    (tmp_path / "create_refund.schema.json").write_text(
        """
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "payment_id": {"type": "string"},
    "amount": {"type": "number", "maximum": 100}
  },
  "required": ["payment_id", "amount"]
}
""",
        encoding="utf-8",
    )
    (tmp_path / "response.json").write_text(
        """
{
  "type": "json_schema",
  "json_schema": {
    "name": "refund_decision",
    "strict": true,
    "schema": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "decision": {"type": "string", "enum": ["approve", "deny"]},
        "needs_review": {"type": "boolean"}
      },
      "required": ["decision", "needs_review"]
    }
  }
}
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: pure-schema
agent:
  name: pure-schema-agent
environment:
  target: local
openai_api:
  prompt_files:
    - prompt.md
  function_schemas:
    - name: create_refund
      path: create_refund.schema.json
  response_formats:
    - path: response.json
      downstream_critical_fields:
        - decision
""",
        encoding="utf-8",
    )

    manifest = load_manifest(tmp_path / "shipgate.yaml")
    source, artifacts = load_openai_api_artifacts(manifest.openai_api, tmp_path)

    assert source is not None
    assert [tool.name for tool in source.tools] == ["create_refund"]
    assert artifacts is not None
    assert artifacts.response_formats[0].strict is True
    assert artifacts.response_formats[0].json_schema["additionalProperties"] is False


def test_openai_api_scan_runs_new_and_existing_checks(tmp_path):
    report, exit_code = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
    )

    assert exit_code == 0
    assert report.api_surface is not None
    assert report.api_surface["prompt_file_count"] == 1
    assert report.tool_surface.sources == {"openai_api": 2}

    check_ids = {finding.check_id for finding in report.findings}
    assert "SHIP-API-FUNCTION-SCHEMA-STRICTNESS" in check_ids
    assert "SHIP-API-STRUCTURED-OUTPUT-READINESS" in check_ids
    assert "SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH" in check_ids
    assert "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY" in check_ids
    assert "SHIP-API-TIMEOUT-MISSING" in check_ids
    assert "SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING" in check_ids
    assert "SHIP-SCHEMA-MISSING-BOUNDS" in check_ids
    assert "SHIP-SIDEFX-IDEMPOTENCY-MISSING" in check_ids


def test_openai_api_source_wins_duplicate_tool_names(tmp_path):
    (tmp_path / "prompt.md").write_text("Use the enabled tools.", encoding="utf-8")
    (tmp_path / "openai-tools.json").write_text(
        """
[
  {
    "type": "function",
    "name": "shared_tool",
    "description": "OpenAI API enabled tool.",
    "strict": true,
    "parameters": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "id": {"type": "string"}
      },
      "required": ["id"]
    }
  }
]
""",
        encoding="utf-8",
    )
    (tmp_path / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Duplicate
  version: "1.0"
paths:
  /shared:
    post:
      operationId: shared_tool
      summary: OpenAPI duplicate.
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: duplicate-api
agent:
  name: duplicate-api-agent
environment:
  target: local
tool_sources:
  - id: openapi
    type: openapi
    path: openapi.yaml
openai_api:
  prompt_files:
    - prompt.md
  tools:
    - path: openai-tools.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    assert report.tool_surface.sources == {"openai_api": 1}
    assert any("Duplicate tool name 'shared_tool'" in warning for warning in report.source_warnings)


def test_openai_api_policy_rules_supplement_manifest_policies(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert not any(
        finding.check_id == "SHIP-POLICY-APPROVAL-MISSING"
        and finding.tool_name == "create_refund"
        for finding in report.findings
    )
    assert not any(
        finding.check_id == "SHIP-POLICY-CONFIRMATION-MISSING"
        and finding.tool_name == "send_customer_email"
        for finding in report.findings
    )


def test_openai_api_policy_rules_merge_overlapping_files(tmp_path):
    (tmp_path / "policy-a.yaml").write_text(
        """
approval_required:
  - create_refund
tool_output_schemas:
  create_refund:
    success_fields: [refund_id]
""",
        encoding="utf-8",
    )
    (tmp_path / "policy-b.yaml").write_text(
        """
approval_required:
  - send_customer_email
tool_output_schemas:
  send_customer_email:
    success_fields: [message_id]
""",
        encoding="utf-8",
    )
    config = OpenAIApiConfig(
        policy_rules=[
            ArtifactPathConfig(path="policy-a.yaml"),
            ArtifactPathConfig(path="policy-b.yaml"),
        ]
    )

    _, artifacts = load_openai_api_artifacts(config, tmp_path)

    assert artifacts is not None
    assert artifacts.policy_rules["approval_required"] == [
        "create_refund",
        "send_customer_email",
    ]
    assert sorted(artifacts.policy_rules["tool_output_schemas"]) == [
        "create_refund",
        "send_customer_email",
    ]
    assert any("overlaps an earlier policy file" in warning for warning in artifacts.warnings)


def test_doctor_includes_openai_api_artifacts():
    payload = inspect_sources(config_path=SAMPLE)

    assert payload["total_tools"] == 2
    assert payload["api_surface"] == {
        "prompt_file_count": 1,
        "tool_file_count": 1,
        "response_format_count": 1,
        "model_config_present": True,
        "test_case_count": 1,
        "trace_sample_count": 1,
        "policy_rule_count": 1,
    }
