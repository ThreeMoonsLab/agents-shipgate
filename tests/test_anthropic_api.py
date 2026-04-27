from pathlib import Path

import pytest

from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.config.schema import (
    AnthropicConfig,
    ArtifactPathConfig,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.inputs.anthropic_api import load_anthropic_artifacts

SAMPLE = Path("samples/simple_anthropic_agent/shipgate.yaml")


def test_anthropic_only_manifest_is_valid_and_prompt_files_satisfy_scope_text():
    manifest = load_manifest(SAMPLE)

    assert manifest.tool_sources == []
    assert manifest.openai_api is None
    assert manifest.anthropic is not None
    assert manifest.anthropic.prompt_files == ["prompts/support_refund.md"]
    # No declared_purpose / instructions_preview is present; the
    # anthropic.prompt_files entry must satisfy the scope-text validator.


def test_manifest_requires_tool_sources_or_openai_api_or_anthropic_or_google_adk(tmp_path):
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

    with pytest.raises(ConfigError, match="tool_sources, openai_api, anthropic"):
        load_manifest(manifest_path)


def test_anthropic_loader_normalizes_input_schema_into_tool_parameters():
    manifest = load_manifest(SAMPLE)
    source, artifacts = load_anthropic_artifacts(manifest.anthropic, SAMPLE.parent)

    assert source is not None
    assert artifacts is not None
    assert source.source_type == "anthropic_api"
    names = {tool.name for tool in source.tools}
    assert names == {"create_refund", "get_help_article"}

    create_refund = next(tool for tool in source.tools if tool.name == "create_refund")
    assert create_refund.source_type == "anthropic_api"
    assert create_refund.extraction_confidence == "high"
    assert create_refund.input_schema["type"] == "object"
    parameter_names = {parameter.name for parameter in create_refund.parameters}
    assert parameter_names == {"payment_id", "amount", "reason"}


def test_anthropic_loader_warns_on_invalid_tool_name(tmp_path):
    tools = tmp_path / "tools.json"
    tools.write_text(
        '[{"name": "bad.name.with.dots", "description": "Long enough description to be valid.", '
        '"input_schema": {"type": "object", "properties": {}}}]',
        encoding="utf-8",
    )
    config = AnthropicConfig(tools=[ArtifactPathConfig(path="tools.json")])

    source, artifacts = load_anthropic_artifacts(config, tmp_path)

    assert source is not None
    # Tool still loads — it's a warning, not an error.
    assert {tool.name for tool in source.tools} == {"bad.name.with.dots"}
    assert any("violates the documented" in w for w in artifacts.warnings)


def test_anthropic_loader_skips_server_side_tool_types_with_warning(tmp_path):
    tools = tmp_path / "tools.json"
    tools.write_text(
        """
{
  "tools": [
    {"type": "computer_20250124", "name": "computer"},
    {"type": "bash_20250124", "name": "bash"},
    {"type": "web_search_20250305", "name": "web_search"},
    {"type": "text_editor_20250124", "name": "str_replace_editor"},
    {
      "name": "list_records",
      "description": "List records the agent can read.",
      "input_schema": {"type": "object", "properties": {}}
    }
  ]
}
""",
        encoding="utf-8",
    )
    config = AnthropicConfig(tools=[ArtifactPathConfig(path="tools.json")])

    source, artifacts = load_anthropic_artifacts(config, tmp_path)

    assert source is not None
    assert {tool.name for tool in source.tools} == {"list_records"}
    skipped_names = {entry["name"] for entry in artifacts.skipped_server_tools}
    assert skipped_names == {"computer", "bash", "web_search", "str_replace_editor"}
    assert any("server-side tool" in w for w in artifacts.warnings)


def test_anthropic_loader_warns_on_openai_style_function_wrapper(tmp_path):
    tools = tmp_path / "tools.json"
    tools.write_text(
        """
[{
  "type": "function",
  "function": {
    "name": "create_refund",
    "description": "Create a refund.",
    "parameters": {"type": "object", "properties": {}}
  }
}]
""",
        encoding="utf-8",
    )
    config = AnthropicConfig(tools=[ArtifactPathConfig(path="tools.json")])

    source, artifacts = load_anthropic_artifacts(config, tmp_path)

    assert source is not None
    assert source.tools == []
    assert any("'function' wrapper" in w for w in artifacts.warnings)


def test_anthropic_loader_warns_when_definition_uses_parameters_not_input_schema(tmp_path):
    tools = tmp_path / "tools.json"
    tools.write_text(
        """
[{
  "name": "create_refund",
  "description": "Create a refund.",
  "parameters": {"type": "object", "properties": {}}
}]
""",
        encoding="utf-8",
    )
    config = AnthropicConfig(tools=[ArtifactPathConfig(path="tools.json")])

    source, artifacts = load_anthropic_artifacts(config, tmp_path)

    assert source is not None
    assert source.tools == []
    assert any("'parameters' instead of 'input_schema'" in w for w in artifacts.warnings)


def test_anthropic_loader_captures_cache_control_annotation_verbatim():
    manifest = load_manifest(SAMPLE)
    source, _ = load_anthropic_artifacts(manifest.anthropic, SAMPLE.parent)

    create_refund = next(tool for tool in source.tools if tool.name == "create_refund")
    assert create_refund.annotations.get("anthropicTool") is True
    assert create_refund.annotations.get("anthropicCacheControl") == {"type": "ephemeral"}


def test_anthropic_scan_runs_existing_framework_agnostic_checks(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    fingerprints = {finding.check_id for finding in report.findings if not finding.suppressed}
    # Existing framework-agnostic + generalized SHIP-API-* checks fire on
    # Anthropic tools without any new check IDs.
    assert "SHIP-API-FUNCTION-SCHEMA-STRICTNESS" in fingerprints
    assert "SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH" in fingerprints
    assert "SHIP-AUTH-MISSING-SCOPE" in fingerprints
    assert "SHIP-SCHEMA-MISSING-BOUNDS" in fingerprints
    assert "SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING" in fingerprints
    # Approval is satisfied by anthropic.policy_rules so the check should NOT fire.
    assert "SHIP-POLICY-APPROVAL-MISSING" not in fingerprints
    # No new check IDs introduced by Anthropic support.
    assert not any(check_id.startswith("SHIP-ANTHROPIC-") for check_id in fingerprints)


def test_anthropic_function_strictness_does_not_emit_missing_strict_true():
    """OpenAI's `strict: true` field is not part of Anthropic's Messages API,
    so the function-schema-strictness check must not list it as an issue
    for Anthropic tools."""
    report, _ = run_scan(
        config_path=SAMPLE,
        formats=["json"],
        ci_mode="advisory",
    )
    strictness_findings = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-API-FUNCTION-SCHEMA-STRICTNESS"
        and finding.tool_name == "create_refund"
    ]
    assert strictness_findings, "create_refund should still flag schema strictness"
    issues = strictness_findings[0].evidence.get("issues") or []
    assert "missing_strict_true" not in issues


def test_doctor_includes_anthropic_surface():
    payload = inspect_sources(config_path=SAMPLE)

    assert payload["anthropic_surface"] is not None
    assert payload["anthropic_surface"]["tool_file_count"] == 1
    assert payload["anthropic_surface"]["prompt_file_count"] == 1
    assert payload["anthropic_surface"]["policy_rule_count"] == 1
    assert payload["anthropic_surface"]["skipped_server_tool_count"] == 1
