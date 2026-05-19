"""Archetype-to-manifest-values table used by the overlay renderer.

The ``40-shipgate-yaml`` variant needs a fully-rendered, doctor-clean manifest
for every archetype so the "existing manifest" cell measures agent activation,
not template-cleanup behavior. ``ARCHETYPE_CONTEXTS`` is the one place that
maps archetype slug → concrete values for ``{REPO_NAME, AGENT_NAME,
ONE_LINE_PURPOSE, TOOL_SOURCES}``.

Adding a new archetype: add a row here, add the slug to ``benchmark/repos/``
via :mod:`harness.adoption.scripts.sync_fixtures`, and confirm
``shipgate doctor`` is clean on the rendered manifest.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSource:
    """One entry in ``shipgate.yaml::tool_sources``."""

    id: str
    type: str
    path: str
    mode: str | None = None
    optional: bool = False

    def to_yaml_block(self) -> str:
        """Render as a single ``- {…}`` YAML list entry, 2-space indented."""
        lines = [f"  - id: {self.id}", f"    type: {self.type}", f"    path: {self.path}"]
        if self.mode is not None:
            lines.append(f"    mode: {self.mode}")
        if self.optional:
            lines.append("    optional: true")
        return "\n".join(lines)


@dataclass(frozen=True)
class ArchetypeContext:
    """Per-archetype values consumed by the overlay renderer.

    ``tool_surface_block()`` returns the full top-level config block — usually
    ``tool_sources:``, but ``n8n:`` for the n8n archetype per the manifest
    spec at docs/manifest-v0.1.md::n8n. The template consumes ``{{TOOL_SURFACE}}``
    rather than hardcoding ``tool_sources:`` so this dispatch is clean.
    """

    repo_name: str
    agent_name: str
    one_line_purpose: str
    tool_sources: list[ToolSource] = field(default_factory=list)
    n8n_workflows_path: str | None = None
    """If set, the archetype uses the top-level ``n8n:`` config block with this
    directory as the only ``workflows[].path`` entry. ``tool_sources`` is then
    expected to be empty."""

    def tool_surface_block(self) -> str:
        """Return the top-level config block for this archetype's tool surface."""
        if self.n8n_workflows_path is not None:
            return (
                "n8n:\n"
                "  workflows:\n"
                f"    - path: {self.n8n_workflows_path}\n"
                "tool_sources: []"
            )
        if not self.tool_sources:
            return "tool_sources: []"
        return "tool_sources:\n" + "\n".join(ts.to_yaml_block() for ts in self.tool_sources)

    def as_placeholder_map(self) -> dict[str, str]:
        """Return the mapping used by the overlay renderer's substitution pass."""
        return {
            "REPO_NAME": self.repo_name,
            "AGENT_NAME": self.agent_name,
            "ONE_LINE_PURPOSE": self.one_line_purpose,
            "TOOL_SURFACE": self.tool_surface_block(),
        }


ARCHETYPE_CONTEXTS: dict[str, ArchetypeContext] = {
    "openai-agents-sdk": ArchetypeContext(
        repo_name="openai-agents-sdk",
        agent_name="refund-assistant",
        one_line_purpose="answer refund policy questions and prepare refund requests for human review",
        tool_sources=[
            ToolSource(
                id="support_openapi",
                type="openapi",
                path="specs/support-tools.openapi.yaml",
            ),
            ToolSource(
                id="openai_sdk_static",
                type="openai_agents_sdk",
                path="agents/refund_agent.py",
                mode="static",
                optional=True,
            ),
        ],
    ),
    "mcp-only": ArchetypeContext(
        repo_name="mcp-only",
        agent_name="mcp-tool-server",
        one_line_purpose="expose support knowledge-base and ticket tools via MCP",
        tool_sources=[
            ToolSource(id="mcp_main", type="mcp", path="mcp/tools.json"),
        ],
    ),
    "openapi-only": ArchetypeContext(
        repo_name="openapi-only",
        agent_name="support-openapi-agent",
        one_line_purpose="provide support-team tools described by an OpenAPI spec",
        tool_sources=[
            ToolSource(id="openapi_main", type="openapi", path="specs/support.openapi.yaml"),
        ],
    ),
    "langgraph": ArchetypeContext(
        repo_name="langgraph",
        agent_name="langgraph-assistant",
        one_line_purpose="route customer questions and call internal tools via LangGraph",
        tool_sources=[
            ToolSource(
                id="langchain_main",
                type="langchain",
                path="agent.py",
                mode="static",
            ),
        ],
    ),
    "adk-dynamic-toolset": ArchetypeContext(
        repo_name="adk-dynamic-toolset",
        agent_name="adk-support-agent",
        one_line_purpose="answer support questions through Google ADK with mixed toolsets",
        tool_sources=[
            ToolSource(
                id="adk_main",
                type="google_adk",
                path="agent.py",
                mode="static",
            ),
            ToolSource(
                id="adk_openapi",
                type="openapi",
                path="specs/support.openapi.yaml",
                optional=True,
            ),
        ],
    ),
    "crewai": ArchetypeContext(
        repo_name="crewai",
        agent_name="crewai-support-crew",
        one_line_purpose="coordinate support tasks across CrewAI agents",
        tool_sources=[
            ToolSource(
                id="crewai_main",
                type="crewai",
                path="crew.py",
                mode="static",
            ),
        ],
    ),
    "clean-read-only": ArchetypeContext(
        repo_name="clean-read-only",
        agent_name="readonly-helper",
        one_line_purpose="provide read-only lookups against internal knowledge base",
        tool_sources=[
            ToolSource(id="tools_main", type="openai_api", path="tools.json"),
        ],
    ),
    "n8n": ArchetypeContext(
        repo_name="n8n",
        agent_name="n8n-support-workflow",
        one_line_purpose="run the support-refund workflow with HTTP and Code nodes",
        tool_sources=[],
        n8n_workflows_path="workflows/",
    ),
    "non-agent-negative-control": ArchetypeContext(
        repo_name="non-agent-negative-control",
        agent_name="not-an-agent",
        one_line_purpose="library code with no agent tool surface",
        tool_sources=[],
    ),
}
"""Authoritative archetype → manifest-values mapping.

Keys match the directory names under ``benchmark/repos/`` and the slugs in
``benchmark/repos/README.md``.
"""


def get_context(archetype: str) -> ArchetypeContext:
    """Lookup with a clear error message when an archetype is missing."""
    try:
        return ARCHETYPE_CONTEXTS[archetype]
    except KeyError as exc:
        known = ", ".join(sorted(ARCHETYPE_CONTEXTS))
        raise KeyError(
            f"Unknown archetype {archetype!r}. Known archetypes: {known}"
        ) from exc


__all__ = [
    "ARCHETYPE_CONTEXTS",
    "ArchetypeContext",
    "ToolSource",
    "get_context",
]
