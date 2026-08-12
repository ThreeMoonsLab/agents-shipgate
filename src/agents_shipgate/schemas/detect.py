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


class AgentProjectCandidate(BaseModel):
    """One self-contained project that defines at least one agent.

    ``init`` writes a single manifest describing a single agent surface,
    so more than one of these in a workspace means the workspace is not
    what a manifest describes — a sub-directory is. See
    :mod:`agents_shipgate.cli.discovery.scope`.
    """

    model_config = ConfigDict(extra="forbid")

    #: POSIX path relative to the inspected workspace; "." is the workspace root.
    path: str
    #: Project-marker file that made this a project root (None at a workspace
    #: root that carries no marker of its own).
    marker: str | None = None
    #: Distinct ``Agent(name=…)`` literals parsed under this project.
    agent_names: list[str] = Field(default_factory=list)


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
    agent_name_candidates: list[NameCandidate] = Field(default_factory=list)
    project_name_candidates: list[NameCandidate] = Field(default_factory=list)
    # Which directory one manifest should describe. "ambiguous" means agents
    # were found in more than one self-contained project, so the workspace as
    # a whole is not a manifest scope: `agent.name`, `declared_purpose`, and
    # the declared tool surface would each describe several unrelated agents.
    # "unknown" means discovery was capped before it could tell — a truncated
    # parse in a workspace with several project roots, where the evidence
    # behind "single" would just be whichever files were read first.
    # `init --write` refuses on both rather than adopting the first agent
    # name it parsed.
    agent_scope: Literal["single", "ambiguous", "unknown"] = "single"
    agent_project_candidates: list[AgentProjectCandidate] = Field(default_factory=list)
    suggested_sources: list[dict[str, str]] = Field(default_factory=list)
    # Glob-matched OpenAPI/MCP candidates the real input adapters reject
    # ({type, path, reason}). Kept out of suggested_sources so init never
    # writes a tool_sources entry that scan fails to parse (e.g. an
    # mcpServers-style host config matching *mcp*.json).
    excluded_sources: list[dict[str, str]] = Field(default_factory=list)
    codex_plugin_candidates: list[CodexPluginCandidate] = Field(default_factory=list)
    next_action: str = ""
    workspace_signals: WorkspaceSignals = Field(default_factory=WorkspaceSignals)
