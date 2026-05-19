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
    suggested_sources: list[dict[str, str]] = Field(default_factory=list)
    codex_plugin_candidates: list[CodexPluginCandidate] = Field(default_factory=list)
    next_action: str = ""
    workspace_signals: WorkspaceSignals = Field(default_factory=WorkspaceSignals)
