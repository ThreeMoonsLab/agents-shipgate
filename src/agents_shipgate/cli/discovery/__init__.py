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
    ANTHROPIC_POLICY_PATTERNS,
    ANTHROPIC_TOOL_PATTERNS,
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
    discover_anthropic_artifacts,
    discover_manifest_paths,
    discover_openai_api_artifacts,
    discover_tool_sources,
    render_manifest_template,
)
from agents_shipgate.cli.discovery.ci_workflow import (
    WORKFLOW_RELATIVE_PATH,
    CiWorkflowResult,
    write_ci_workflow,
)
from agents_shipgate.cli.discovery.signals import (
    DetectResult,
    FrameworkDetection,
    NameCandidate,
    WorkspaceSignals,
    detect_workspace,
)
from agents_shipgate.cli.discovery.template import render_auto_manifest

__all__ = [
    "ANTHROPIC_POLICY_PATTERNS",
    "ANTHROPIC_TOOL_PATTERNS",
    "CiWorkflowResult",
    "DetectResult",
    "FrameworkDetection",
    "MCP_PATTERNS",
    "MODEL_CONFIG_PATTERNS",
    "NameCandidate",
    "OPENAI_TOOL_PATTERNS",
    "OPENAPI_PATTERNS",
    "POLICY_RULE_PATTERNS",
    "PROMPT_PATTERNS",
    "RESPONSE_SCHEMA_PATTERNS",
    "SKIP_DIRS",
    "TEST_CASE_PATTERNS",
    "TRACE_SAMPLE_PATTERNS",
    "WORKFLOW_RELATIVE_PATH",
    "WorkspaceSignals",
    "detect_workspace",
    "discover_anthropic_artifacts",
    "discover_manifest_paths",
    "discover_openai_api_artifacts",
    "discover_tool_sources",
    "render_auto_manifest",
    "render_manifest_template",
    "write_ci_workflow",
]
