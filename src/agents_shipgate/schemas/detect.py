from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FrameworkDetection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    score: float
    confidence: str  # "high" | "medium" | "low"
    evidence: list[str] = Field(default_factory=list)
    candidate_files: list[str] = Field(default_factory=list)


class NameCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    source: str  # "Agent_name_literal" | "ADK_name_field" | "pyproject" | "workspace_dir"


# The structural position a name literal occupies in the source. Ranking
# reads this, not source order: an application root outranks a worker even
# when the worker is encountered first.
AgentNameRole = Literal[
    "root_agent",  # bound as App(root_agent=…) or the ADK `root_agent` module symbol
    "sub_agent",  # named inside another agent's sub_agents=[…] / handoffs=[…]
    "agent",  # an agent construction with no hierarchy evidence either way
    "workspace_dir",  # the fallback directory-name candidate (never selectable)
]


class AgentNameCandidate(NameCandidate):
    """A ranked ``agent.name`` candidate with the evidence behind its rank.

    ``NameCandidate`` (value + source) stays the shape for project names,
    which have no hierarchy. Agent names do: the same file can construct a
    coordinator and three workers, and picking the first one encountered
    declares a worker as the reviewed identity. The extra fields exist so
    the ranking is auditable from ``detect --json`` — without them a
    reordering regression is indistinguishable from correct behaviour.
    """

    model_config = ConfigDict(extra="forbid")

    role: AgentNameRole = "agent"
    # Workspace-relative file the evidence came from. ``None`` for the
    # workspace-dir fallback, which has no source file.
    path: str | None = None
    rank_score: float = 0.0
    # Whether ``init`` may write this value as ``agent.name``. False for the
    # workspace-dir fallback and for values that fail the quality floor; when
    # nothing is selectable the manifest keeps its CHANGE_ME placeholder.
    selectable: bool = False
    # Human-readable reasons, ordered as applied. Rendered into no artifact —
    # this is the explanation surface for the ranking itself.
    rationale: list[str] = Field(default_factory=list)


class CodexPluginCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["package", "marketplace"]
    path: str
    evidence: str


class WorkspaceSignals(BaseModel):
    """Minimal workspace state used by diagnostics to discriminate
    negative-control cases (non-agent library, pure-prompt experiment,
    no surface) from one another.

    Derived inside :func:`detect_workspace` from inputs it already
    computes; not exposed in the human-readable summary, only in JSON.
    """

    model_config = ConfigDict(extra="forbid")

    python_file_count: int = 0
    has_pyproject_or_requirements: bool = False
    has_prompts_dir: bool = False
    has_tools_dir: bool = False
    conventional_dirs: list[str] = Field(default_factory=list)


class DetectResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_agent_project: bool
    frameworks: list[FrameworkDetection] = Field(default_factory=list)
    # Ranked best-first. The first entry with ``selectable`` true is the one
    # ``init`` writes; see ``signals.select_agent_name``.
    agent_name_candidates: list[AgentNameCandidate] = Field(default_factory=list)
    project_name_candidates: list[NameCandidate] = Field(default_factory=list)
    suggested_sources: list[dict[str, str]] = Field(default_factory=list)
    # Glob-matched OpenAPI/MCP candidates the real input adapters reject
    # ({type, path, reason}). Kept out of suggested_sources so init never
    # writes a tool_sources entry that scan fails to parse (e.g. an
    # mcpServers-style host config matching *mcp*.json).
    excluded_sources: list[dict[str, str]] = Field(default_factory=list)
    codex_plugin_candidates: list[CodexPluginCandidate] = Field(default_factory=list)
    next_action: str = ""
    workspace_signals: WorkspaceSignals = Field(default_factory=WorkspaceSignals)
