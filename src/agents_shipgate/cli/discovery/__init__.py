"""Workspace discovery package.

Public API kept stable across the v0.5.x → v0.6.0 refactor: callers can keep
importing ``discover_manifest_paths``, ``discover_tool_sources``,
``render_manifest_template``, and ``discover_openai_api_artifacts`` from
``agents_shipgate.cli.discovery``.

Internal layout:
    artifacts.py    glob-based discovery for OpenAPI/MCP/OpenAI-API
                    artifacts. Verbatim from the pre-package module; v0.6
                    extends it with Anthropic-specific patterns.
"""

from __future__ import annotations

from agents_shipgate.cli.discovery.artifacts import (
    MCP_PATTERNS,
    MODEL_CONFIG_PATTERNS,
    OPENAI_TOOL_PATTERNS,
    OPENAPI_PATTERNS,
    POLICY_RULE_PATTERNS,
    PROMPT_PATTERNS,
    RESPONSE_SCHEMA_PATTERNS,
    SKIP_DIRS,
    TEST_CASE_PATTERNS,
    TRACE_SAMPLE_PATTERNS,
    discover_manifest_paths,
    discover_openai_api_artifacts,
    discover_tool_sources,
    render_manifest_template,
)

__all__ = [
    "MCP_PATTERNS",
    "MODEL_CONFIG_PATTERNS",
    "OPENAI_TOOL_PATTERNS",
    "OPENAPI_PATTERNS",
    "POLICY_RULE_PATTERNS",
    "PROMPT_PATTERNS",
    "RESPONSE_SCHEMA_PATTERNS",
    "SKIP_DIRS",
    "TEST_CASE_PATTERNS",
    "TRACE_SAMPLE_PATTERNS",
    "discover_manifest_paths",
    "discover_openai_api_artifacts",
    "discover_tool_sources",
    "render_manifest_template",
]
