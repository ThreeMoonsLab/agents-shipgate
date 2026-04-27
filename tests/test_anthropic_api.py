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


def test_anthropic_loader_skips_anthropic_managed_server_tools(tmp_path):
    """Server-side Anthropic tools (web_search, code_execution) are
    sandboxed on Anthropic infrastructure and have no user-controlled
    schema; static checks against them would be unactionable."""
    tools = tmp_path / "tools.json"
    tools.write_text(
        """
{
  "tools": [
    {"type": "web_search_20250305", "name": "web_search"},
    {"type": "code_execution_20250522", "name": "code_execution"},
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
    assert skipped_names == {"web_search", "code_execution"}
    assert any("server-side tool" in w for w in artifacts.warnings)


def test_anthropic_loader_inventories_client_tools_with_risk_hints(tmp_path):
    """Anthropic client tools (bash, text_editor, computer, memory) execute
    in the user's application code and ARE in scope for static review.
    They are inventoried with pre-populated risk hints so the
    framework-agnostic checks (approval, auth scope, owner, idempotency)
    fire correctly — a manifest enabling bash should not silently produce
    zero actionable findings beyond a warning."""
    tools = tmp_path / "tools.json"
    tools.write_text(
        """
{
  "tools": [
    {"type": "bash_20250124", "name": "bash"},
    {"type": "text_editor_20250124", "name": "str_replace_editor"},
    {"type": "computer_20250124", "name": "computer"},
    {"type": "memory_20250818", "name": "memory"}
  ]
}
""",
        encoding="utf-8",
    )
    config = AnthropicConfig(tools=[ArtifactPathConfig(path="tools.json")])

    source, artifacts = load_anthropic_artifacts(config, tmp_path)

    assert source is not None
    assert artifacts is not None
    # All four are loaded — none skipped.
    assert artifacts.skipped_server_tools == []
    by_name = {tool.name: tool for tool in source.tools}
    assert set(by_name) == {"bash", "str_replace_editor", "computer", "memory"}

    # Each carries the type info on the tool annotations.
    assert by_name["bash"].annotations["anthropicClientTool"] is True
    assert by_name["bash"].annotations["anthropicToolType"] == "bash_20250124"

    # Risk hints are pre-populated at high confidence per the type prefix.
    bash_tags = {hint.tag for hint in by_name["bash"].risk_hints}
    assert {"code_execution", "destructive", "write"} <= bash_tags

    editor_tags = {hint.tag for hint in by_name["str_replace_editor"].risk_hints}
    assert {"destructive", "write"} <= editor_tags

    computer_tags = {hint.tag for hint in by_name["computer"].risk_hints}
    assert {"code_execution", "destructive", "write"} <= computer_tags

    memory_tags = {hint.tag for hint in by_name["memory"].risk_hints}
    assert {"write"} <= memory_tags

    # The hints are sourced from the typed-tool classifier so the evidence
    # carries the specific Anthropic type for traceability.
    bash_hint_sources = {hint.source for hint in by_name["bash"].risk_hints}
    assert "anthropic_client_tool_type" in bash_hint_sources


def test_anthropic_loader_skips_unknown_typed_tools_with_warning(tmp_path):
    """Forward-compat: a typed Anthropic tool we don't classify yet is
    skipped with an explicit warning so the user can either declare it as
    `type: "custom"` or wait for adapter support — never silently dropped."""
    tools = tmp_path / "tools.json"
    tools.write_text(
        '[{"type": "future_anthropic_tool_20990101", "name": "future_thing"}]',
        encoding="utf-8",
    )
    config = AnthropicConfig(tools=[ArtifactPathConfig(path="tools.json")])

    source, artifacts = load_anthropic_artifacts(config, tmp_path)

    assert source is not None
    assert source.tools == []
    assert any("unrecognized type" in w for w in artifacts.warnings)
    assert {entry["name"] for entry in artifacts.skipped_server_tools} == {"future_thing"}


def test_anthropic_client_tool_fires_high_risk_release_findings(tmp_path):
    """End-to-end: a manifest declaring `bash_20250124` with no approval
    policy must NOT silently pass; it should fire SHIP-POLICY-APPROVAL-MISSING
    and SHIP-AUTH-MISSING-SCOPE just like any other write-shaped tool."""
    (tmp_path / "prompt.md").write_text(
        "You are a coding assistant that runs shell commands.", encoding="utf-8"
    )
    (tmp_path / "tools.json").write_text(
        '[{"type": "bash_20250124", "name": "bash"}]',
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: anthropic-bash-agent
agent:
  name: bash-agent
  declared_purpose:
    - run bash commands
environment:
  target: production_like
anthropic:
  prompt_files:
    - prompt.md
  tools:
    - path: tools.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    bash_findings = {
        finding.check_id
        for finding in report.findings
        if finding.tool_name == "bash" and not finding.suppressed
    }
    assert "SHIP-POLICY-APPROVAL-MISSING" in bash_findings
    assert "SHIP-AUTH-MISSING-SCOPE" in bash_findings
    # The bash tool should be in the inventory (not skipped).
    inventory_names = {
        (tool.name if hasattr(tool, "name") else tool["name"])
        for tool in report.tool_inventory
    }
    assert "bash" in inventory_names


def test_anthropic_tools_do_not_fire_openai_only_checks_in_mixed_manifest(tmp_path):
    """Regression test for PR #14 review P2: when a manifest declares both
    openai_api and anthropic blocks, the OpenAI-only checks
    (structured-output-readiness, retry, timeout, test-cases,
    tool-output-schema, retry-without-idempotency) must filter to OpenAI
    tools — Anthropic tools have no equivalent artifacts and would
    otherwise produce false-positive findings."""
    (tmp_path / "openai_prompt.md").write_text("OpenAI prompt.", encoding="utf-8")
    (tmp_path / "openai-tools.json").write_text(
        """
[
  {
    "type": "function",
    "name": "openai_only_tool",
    "description": "Returns customer status.",
    "strict": true,
    "parameters": {
      "type": "object",
      "additionalProperties": false,
      "properties": {"id": {"type": "string"}},
      "required": ["id"]
    }
  }
]
""",
        encoding="utf-8",
    )
    (tmp_path / "anthropic-tools.json").write_text(
        """
[
  {
    "name": "create_refund",
    "description": "Creates a refund (financial action).",
    "input_schema": {
      "type": "object",
      "properties": {
        "amount": {"type": "number"},
        "payment_id": {"type": "string"}
      },
      "required": ["amount", "payment_id"]
    }
  }
]
""",
        encoding="utf-8",
    )
    (tmp_path / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: mixed-anthropic-openai
agent:
  name: mixed-agent
  declared_purpose:
    - look up customer status
    - issue refunds
environment:
  target: production_like
openai_api:
  prompt_files:
    - openai_prompt.md
  tools:
    - path: openai-tools.json
anthropic:
  tools:
    - path: anthropic-tools.json
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=tmp_path / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    # Anthropic create_refund must NOT appear under any OpenAI-shaped finding.
    openai_only_check_ids = {
        "SHIP-API-RETRY-POLICY-MISSING",
        "SHIP-API-TIMEOUT-MISSING",
        "SHIP-API-TEST-CASES-MISSING",
        "SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING",
        "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY",
    }
    for finding in report.findings:
        if (
            finding.check_id in openai_only_check_ids
            and finding.tool_name == "create_refund"
        ):
            raise AssertionError(
                f"OpenAI-only check {finding.check_id} fired on Anthropic tool create_refund"
            )

    # Specifically: the agent-level structured-output-readiness finding
    # (when fired) must not list Anthropic tools in high_risk_tools.
    for finding in report.findings:
        if finding.check_id == "SHIP-API-STRUCTURED-OUTPUT-READINESS":
            high_risk = finding.evidence.get("high_risk_tools") or []
            assert "create_refund" not in high_risk, (
                "Anthropic create_refund leaked into OpenAI structured-output finding"
            )


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
