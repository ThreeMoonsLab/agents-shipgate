from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    Orthogonal to :class:`AgentProjectCandidate`: that one answers *which
    directory* a manifest describes, this one answers *which agent* it
    names once the directory is settled.
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


# Fields that only a ranked candidate carries. An entry holding none of
# them predates the ranking and is upgraded rather than read as "ranked
# last, not selectable".
_RANKED_FIELDS = frozenset({"role", "path", "rank_score", "selectable", "rationale"})
# The sources selection accepted before ranking existed. A legacy candidate
# keeps exactly the meaning it had.
_LEGACY_SELECTABLE_SOURCES = frozenset({"Agent_name_literal", "ADK_name_field"})


def _upgrade_legacy_candidate(data: object) -> AgentNameCandidate:
    """Validate a legacy entry as a ``NameCandidate``, then enrich it.

    Validating first is the point: building the ranked model directly
    stringified missing or wrongly typed values and quietly accepted keys
    that ``extra="forbid"`` exists to reject, so a malformed payload was
    upgraded into a well-formed lie.
    """
    legacy = NameCandidate.model_validate(data)
    return AgentNameCandidate(
        value=legacy.value,
        source=legacy.source,
        selectable=legacy.source in _LEGACY_SELECTABLE_SOURCES,
        rationale=["carried over from an unranked NameCandidate"],
    )


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
    # Directories carrying a project marker, counted from the whole walk by
    # filename alone — no parsing, and no cap. It is the one number that
    # stays trustworthy where the AST pass stops being trustworthy, so it
    # bounds a truncated candidate list: `agent_project_candidates` names
    # the agent projects found *before* the cap, this counts the project
    # roots that exist.
    project_root_count: int = 0
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
    # Whether the walk behind `agent_scope` and `agent_project_candidates`
    # was cut short: the Python parse stopped at `max_python_files` in a
    # workspace holding more than one project root. It is a separate field
    # rather than a fourth `agent_scope` value because the two facts are
    # independent — two projects found *is* an ambiguous scope however much
    # of the tree was read — and collapsing them made the honest one
    # unreachable: "unknown" only ever fired when one or fewer candidates
    # were found, so the repositories most likely to be truncated were the
    # ones that never reported it (#395). While true, the candidate list is
    # a lower bound, not an enumeration: a project in the unread remainder
    # is missing from it.
    agent_scope_truncated: bool = False
    suggested_sources: list[dict[str, str]] = Field(default_factory=list)
    # Glob-matched OpenAPI/MCP candidates the real input adapters reject
    # ({type, path, reason}). Kept out of suggested_sources so init never
    # writes a tool_sources entry that scan fails to parse (e.g. an
    # mcpServers-style host config matching *mcp*.json).
    excluded_sources: list[dict[str, str]] = Field(default_factory=list)
    codex_plugin_candidates: list[CodexPluginCandidate] = Field(default_factory=list)
    next_action: str = ""
    workspace_signals: WorkspaceSignals = Field(default_factory=WorkspaceSignals)

    @field_validator("agent_name_candidates", mode="before")
    @classmethod
    def _accept_legacy_name_candidates(cls, value: object) -> object:
        """Upgrade plain ``NameCandidate`` entries rather than rejecting them.

        ``NameCandidate`` is a public export and was the declared element
        type before ranking existed, so callers construct ``DetectResult``
        with it. Narrowing the annotation turned those calls into a
        ``ValidationError``, and a legacy dict would have parsed but landed
        on ``selectable=False`` — silently changing which name ``init``
        writes. Both are upgraded here with the rule that used to decide
        selection, so old callers keep the behaviour they had.
        """
        # Every sequence form the old `list[NameCandidate]` field accepted
        # has to keep working, tuples included; normalising only `list`
        # left a tuple of instances raising and a tuple of legacy dicts
        # silently landing on `selectable=False`.
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            return value
        upgraded: list[object] = []
        for entry in value:
            if isinstance(entry, AgentNameCandidate):
                upgraded.append(entry)
            elif isinstance(entry, NameCandidate):
                upgraded.append(_upgrade_legacy_candidate(entry.model_dump()))
            elif isinstance(entry, dict) and not _RANKED_FIELDS & set(entry):
                upgraded.append(_upgrade_legacy_candidate(entry))
            else:
                upgraded.append(entry)
        return upgraded
