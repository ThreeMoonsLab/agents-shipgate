"""Workspace classification: is this an agent project, and which framework(s).

Pass A of the v0.6 detection pipeline. Walks a workspace once, AST-parses
candidate ``.py`` files, scores per-framework signals, and returns a
:class:`DetectResult` for ``shipgate detect`` and for ``init`` Pass B.

This is *new* signal-scanning logic. It deliberately does not call the
framework loaders in ``agents_shipgate.inputs.*`` — those gate on a
populated manifest and would no-op here. Instead it borrows their
constants where they map cleanly onto detection signals
(e.g. :data:`agents_shipgate.inputs.langchain.TOOL_DECORATOR_MODULES`).

Scoring (per plan §1, post-review v4):

- Strong  (+2 each): matching framework import; matching framework
  decorator; matching framework class instantiation.
- Medium  (+1 each): dependency listed in pyproject.toml /
  requirements.txt; framework-specific filename glob hit
  (``*mcp*.json``, ``*openapi*.yaml``, ``openai-config.json``,
  ``*anthropic*tools*.json``, ``*anthropic*policy*.yaml``).
- Weak  (+0.5 each): conventional directory layout
  (``prompts/``, ``tools/``, ``.agents-shipgate/``).

A framework is *detected* when its score ≥ 2.0 AND it accumulated at
least one strong signal.

Agent-name candidate ranking (corrected post-review):
``Agent(name="…")`` literal → ADK config ``name=`` → workspace dir name.
``pyproject.[project].name`` seeds ``project.name``, NOT ``agent.name``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.cli.discovery.artifacts import (
    ANTHROPIC_POLICY_PATTERNS,
    ANTHROPIC_TOOL_PATTERNS,
    MCP_PATTERNS,
    MODEL_CONFIG_PATTERNS,
    OPENAI_TOOL_PATTERNS,
    OPENAPI_PATTERNS,
    POLICY_RULE_PATTERNS,
    TEST_CASE_PATTERNS,
    _candidate_files,
    _candidate_files_matching,
    _discover_patterns,
    _relative,
)

# --- Framework signal vocabulary --------------------------------------------
# These mirror the constants used by the input adapters. Centralised here so
# detection can be tested independently of the loader modules.

LANGCHAIN_IMPORT_MODULES = {
    "langchain",
    "langchain.agents",
    "langchain.tools",
    "langchain_core",
    "langchain_core.tools",
    "langchain_core.agents",
    "langgraph",
    "langgraph.graph",
    "langgraph.prebuilt",
}
LANGCHAIN_DECORATOR_MODULES = {"langchain.tools", "langchain_core.tools"}
LANGCHAIN_AGENT_CALLS = {"create_agent", "create_react_agent", "AgentExecutor"}

CREWAI_IMPORT_MODULES = {"crewai", "crewai.tools", "crewai_tools"}
CREWAI_DECORATOR_MODULES = {"crewai.tools"}
CREWAI_CLASS_NAMES = {"Agent", "Crew", "Task"}

GOOGLE_ADK_IMPORT_MODULES = {
    "google.adk",
    "google.adk.agents",
    "google.adk.tools",
}
GOOGLE_ADK_AGENT_CLASSES = {"Agent", "LlmAgent"}
GOOGLE_ADK_TOOL_CLASSES = {
    "FunctionTool",
    "LongRunningFunctionTool",
    "OpenAPIToolset",
    "McpToolset",
    "MCPToolset",
}

ANTHROPIC_IMPORT_MODULES = {"anthropic"}

OPENAI_AGENTS_SDK_IMPORT_MODULES = {"agents", "openai_agents"}
OPENAI_AGENTS_SDK_DECORATORS = {
    "function_tool",
    "agents.function_tool",
    "openai_agents.function_tool",
}

# pyproject / requirements tokens used to score a framework presence.
PACKAGE_HINTS: dict[str, tuple[str, ...]] = {
    "langchain": ("langchain", "langchain-core", "langchain_core", "langgraph"),
    "crewai": ("crewai", "crewai-tools"),
    "google_adk": ("google-adk", "google_adk", "google-genai"),
    "anthropic": ("anthropic",),
    "openai_agents_sdk": ("openai-agents", "openai_agents", "agents"),
    # openai_api is artifact-based; package hints aren't meaningful for it.
    "openai_api": (),
}

CONVENTIONAL_DIRS = ("prompts", "tools", ".agents-shipgate")


# --- Public output models ---------------------------------------------------


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
    next_action: str = ""
    workspace_signals: WorkspaceSignals = Field(default_factory=WorkspaceSignals)


# --- Internal scoring state -------------------------------------------------


@dataclass
class _FrameworkScore:
    score: float = 0.0
    has_strong: bool = False
    evidence: list[str] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)

    def add(self, points: float, signal_class: str, evidence: str) -> None:
        self.score += points
        if signal_class == "strong":
            self.has_strong = True
        self.evidence.append(evidence)

    def add_file(self, path: str) -> None:
        if path not in self.candidate_files:
            self.candidate_files.append(path)


_PYPROJECT_NAME_RE = re.compile(r'^\s*name\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_REQUIREMENTS_TOKEN_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)", re.MULTILINE)


# --- Public entry point -----------------------------------------------------


def detect_workspace(workspace: Path, *, max_python_files: int = 1000) -> DetectResult:
    """Walk ``workspace`` and report which frameworks are present.

    Read-only. Caps Python AST parses at ``max_python_files`` to keep the
    scan bounded on large monorepos.
    """
    workspace = workspace.resolve()
    py_files = _collect_python_files(workspace, max_files=max_python_files)
    py_facts = [_parse_python_facts(path, workspace) for path in py_files]
    py_facts = [fact for fact in py_facts if fact is not None]

    pkg_tokens = _collect_package_tokens(workspace)
    glob_hits = _collect_glob_hits(workspace)
    dir_hits = _collect_dir_hits(workspace)

    scores: dict[str, _FrameworkScore] = {
        "langchain": _FrameworkScore(),
        "crewai": _FrameworkScore(),
        "google_adk": _FrameworkScore(),
        "anthropic": _FrameworkScore(),
        "openai_agents_sdk": _FrameworkScore(),
        # openai_api is the artifact-based OpenAI Messages API surface
        # (manifest.openai_api block). Distinct from openai_agents_sdk
        # (Python @function_tool decorators).
        "openai_api": _FrameworkScore(),
    }

    for fact in py_facts:
        _score_python_signals(fact, scores)

    for framework, hints in PACKAGE_HINTS.items():
        for token in pkg_tokens:
            if token.lower() in {h.lower() for h in hints}:
                scores[framework].add(
                    1.0, "medium", f"dependency declared: {token}"
                )

    for framework, hits in glob_hits.items():
        for hit in hits:
            scores[framework].add(hit.points, hit.signal_class, hit.evidence)

    for framework, dirs in dir_hits.items():
        for d in dirs:
            scores[framework].add(0.5, "weak", f"conventional dir: {d}/")

    detections: list[FrameworkDetection] = []
    for framework, state in scores.items():
        if state.score >= 2.0 and state.has_strong:
            detections.append(
                FrameworkDetection(
                    type=framework,
                    score=round(state.score, 2),
                    confidence=_confidence_label(state.score),
                    evidence=state.evidence,
                    candidate_files=state.candidate_files,
                )
            )
    detections.sort(key=lambda d: (-d.score, d.type))

    agent_name_candidates = _agent_name_candidates(py_facts, workspace)
    project_name_candidates = _project_name_candidates(workspace)
    suggested_sources = _suggested_sources(workspace)

    is_agent_project = bool(detections)
    next_action = (
        f"agents-shipgate init --workspace {workspace}"
        if is_agent_project
        else "Workspace does not appear to be an agent project. No action."
    )

    present_dirs = [d for d in CONVENTIONAL_DIRS if (workspace / d).is_dir()]
    workspace_signals = WorkspaceSignals(
        python_file_count=len(py_facts),
        has_pyproject_or_requirements=(
            (workspace / "pyproject.toml").is_file()
            or (workspace / "requirements.txt").is_file()
        ),
        has_prompts_dir="prompts" in present_dirs,
        has_tools_dir="tools" in present_dirs,
        conventional_dirs=present_dirs,
    )

    return DetectResult(
        is_agent_project=is_agent_project,
        frameworks=detections,
        agent_name_candidates=agent_name_candidates,
        project_name_candidates=project_name_candidates,
        suggested_sources=suggested_sources,
        next_action=next_action,
        workspace_signals=workspace_signals,
    )


# --- Internals --------------------------------------------------------------


def _collect_python_files(workspace: Path, *, max_files: int) -> list[Path]:
    files: list[Path] = []
    for path in _candidate_files(workspace):
        if path.suffix != ".py":
            continue
        files.append(path)
        if len(files) >= max_files:
            break
    return files


@dataclass
class _PyFacts:
    path: Path
    rel_path: str
    imports: set[str] = field(default_factory=set)
    decorators: set[str] = field(default_factory=set)
    constructors: set[str] = field(default_factory=set)
    agent_name_literals: list[str] = field(default_factory=list)


def _parse_python_facts(path: Path, workspace: Path) -> _PyFacts | None:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None

    facts = _PyFacts(path=path, rel_path=_relative(path, workspace))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                facts.imports.add(node.module)
                for alias in node.names:
                    facts.imports.add(f"{node.module}.{alias.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                name = _decorator_name(decorator)
                if name:
                    facts.decorators.add(name)
        elif isinstance(node, ast.Call):
            ctor = _call_name(node.func)
            if ctor:
                facts.constructors.add(ctor)
                literal = _name_keyword(node)
                if literal and ctor.split(".")[-1] in {"Agent", "LlmAgent"}:
                    facts.agent_name_literals.append(literal)
    return facts


def _decorator_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return _call_name(node)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        if prefix:
            return f"{prefix}.{node.attr}"
        return node.attr
    return None


def _name_keyword(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _score_python_signals(fact: _PyFacts, scores: dict[str, _FrameworkScore]) -> None:
    # LangChain
    if fact.imports & LANGCHAIN_IMPORT_MODULES:
        scores["langchain"].add(2.0, "strong", f"{fact.rel_path}: langchain import")
        scores["langchain"].add_file(fact.rel_path)
    if fact.decorators & {f"{m}.tool" for m in LANGCHAIN_DECORATOR_MODULES} or "tool" in fact.decorators and any(
        m in fact.imports for m in LANGCHAIN_DECORATOR_MODULES
    ):
        scores["langchain"].add(2.0, "strong", f"{fact.rel_path}: @tool from langchain")
        scores["langchain"].add_file(fact.rel_path)
    if fact.constructors & LANGCHAIN_AGENT_CALLS or any(
        c.endswith("." + name) for c in fact.constructors for name in LANGCHAIN_AGENT_CALLS
    ):
        scores["langchain"].add(2.0, "strong", f"{fact.rel_path}: langchain agent call")
        scores["langchain"].add_file(fact.rel_path)

    # CrewAI
    if fact.imports & CREWAI_IMPORT_MODULES:
        scores["crewai"].add(2.0, "strong", f"{fact.rel_path}: crewai import")
        scores["crewai"].add_file(fact.rel_path)
    if "tool" in fact.decorators and any(
        m in fact.imports for m in CREWAI_DECORATOR_MODULES
    ):
        scores["crewai"].add(2.0, "strong", f"{fact.rel_path}: @tool from crewai")
        scores["crewai"].add_file(fact.rel_path)
    if any(c.split(".")[-1] in CREWAI_CLASS_NAMES for c in fact.constructors) and (
        fact.imports & CREWAI_IMPORT_MODULES
    ):
        scores["crewai"].add(2.0, "strong", f"{fact.rel_path}: crewai class call")
        scores["crewai"].add_file(fact.rel_path)

    # Google ADK
    if any(m for m in fact.imports if m in GOOGLE_ADK_IMPORT_MODULES or m.startswith("google.adk")):
        scores["google_adk"].add(2.0, "strong", f"{fact.rel_path}: google.adk import")
        scores["google_adk"].add_file(fact.rel_path)
    if any(c.split(".")[-1] in (GOOGLE_ADK_AGENT_CLASSES | GOOGLE_ADK_TOOL_CLASSES) for c in fact.constructors) and any(
        m.startswith("google.adk") for m in fact.imports
    ):
        scores["google_adk"].add(
            2.0, "strong", f"{fact.rel_path}: google.adk agent/tool class call"
        )
        scores["google_adk"].add_file(fact.rel_path)

    # Anthropic (Python signal — usually paired with artifact globs to confirm)
    if fact.imports & ANTHROPIC_IMPORT_MODULES or any(
        m.startswith("anthropic.") for m in fact.imports
    ):
        scores["anthropic"].add(2.0, "strong", f"{fact.rel_path}: anthropic import")
        scores["anthropic"].add_file(fact.rel_path)

    # OpenAI Agents SDK
    if fact.imports & OPENAI_AGENTS_SDK_IMPORT_MODULES:
        scores["openai_agents_sdk"].add(
            2.0, "strong", f"{fact.rel_path}: openai-agents import"
        )
        scores["openai_agents_sdk"].add_file(fact.rel_path)
    if fact.decorators & OPENAI_AGENTS_SDK_DECORATORS:
        scores["openai_agents_sdk"].add(
            2.0, "strong", f"{fact.rel_path}: @function_tool decorator"
        )
        scores["openai_agents_sdk"].add_file(fact.rel_path)


def _collect_package_tokens(workspace: Path) -> list[str]:
    tokens: list[str] = []
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            content = ""
        # Match "name" entries inside [project.optional-dependencies]/
        # dependencies arrays without a TOML parser dependency. Keep it simple.
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            stripped = stripped.strip(",").strip("'\"")
            if "==" in stripped:
                stripped = stripped.split("==", 1)[0]
            elif ">=" in stripped:
                stripped = stripped.split(">=", 1)[0]
            elif "<=" in stripped:
                stripped = stripped.split("<=", 1)[0]
            elif "~=" in stripped:
                stripped = stripped.split("~=", 1)[0]
            elif ">" in stripped:
                stripped = stripped.split(">", 1)[0]
            elif "<" in stripped:
                stripped = stripped.split("<", 1)[0]
            stripped = stripped.strip().strip('"\'')
            if stripped and re.fullmatch(r"[A-Za-z0-9_.\-]+", stripped):
                tokens.append(stripped)
    requirements = workspace / "requirements.txt"
    if requirements.is_file():
        try:
            content = requirements.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            content = ""
        for match in _REQUIREMENTS_TOKEN_RE.finditer(content):
            tokens.append(match.group(1))
    return tokens


@dataclass
class _GlobHit:
    points: float
    signal_class: str  # "strong" | "medium" | "weak"
    evidence: str


def _collect_glob_hits(workspace: Path) -> dict[str, list[_GlobHit]]:
    """Per-framework glob signals.

    Three artifact-based frameworks have unambiguous filename markers:

    - Anthropic: ``tools/anthropic-tools.json`` /
      ``policies/anthropic-policy.yaml``.
    - OpenAI API: ``openai-config.json``, ``tools/*openai*tools*.json``,
      ``policies/*openai*.yaml`` / ``policies/*api*.yaml``,
      ``tests/*openai*cases*.json``. (Distinct from openai_agents_sdk,
      which is the Python ``@function_tool`` decorator surface.)

    MCP/OpenAPI hits don't classify a framework by themselves — they're
    reported as ``suggested_sources`` instead.
    """
    hits: dict[str, list[_GlobHit]] = {
        "langchain": [],
        "crewai": [],
        "google_adk": [],
        "anthropic": [],
        "openai_agents_sdk": [],
        "openai_api": [],
    }
    for path in _discover_patterns(workspace, ANTHROPIC_TOOL_PATTERNS):
        hits["anthropic"].append(
            _GlobHit(2.0, "strong", f"anthropic tool file: {path}")
        )
    for path in _discover_patterns(workspace, ANTHROPIC_POLICY_PATTERNS):
        hits["anthropic"].append(
            _GlobHit(2.0, "strong", f"anthropic policy file: {path}")
        )
    # openai-config.json is the OpenAI Messages API model-config marker —
    # belongs to openai_api, not the agents SDK (manifest.openai_api.model_config).
    for path in _discover_patterns(workspace, MODEL_CONFIG_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-config marker: {path}")
        )
    for path in _discover_patterns(workspace, OPENAI_TOOL_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai tool file: {path}")
        )
    for path in _discover_patterns(workspace, POLICY_RULE_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-api policy file: {path}")
        )
    for path in _discover_patterns(workspace, TEST_CASE_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-api test cases: {path}")
        )
    return hits


def _collect_dir_hits(workspace: Path) -> dict[str, list[str]]:
    present = [d for d in CONVENTIONAL_DIRS if (workspace / d).is_dir()]
    if not present:
        return {f: [] for f in (
            "langchain", "crewai", "google_adk", "anthropic", "openai_agents_sdk",
            "openai_api",
        )}
    # Conventional dirs are weak signals shared across all framework
    # candidates — they hint "this looks like an agent project" but don't
    # narrow which framework. Apply the weak credit only when a strong
    # signal already exists for that framework, which is enforced
    # downstream by ``has_strong``.
    return {
        framework: list(present)
        for framework in (
            "langchain",
            "crewai",
            "google_adk",
            "anthropic",
            "openai_agents_sdk",
            "openai_api",
        )
    }


def _confidence_label(score: float) -> str:
    if score >= 4.0:
        return "high"
    if score >= 2.5:
        return "medium"
    return "low"


def _agent_name_candidates(facts: list[_PyFacts], workspace: Path) -> list[NameCandidate]:
    candidates: list[NameCandidate] = []
    seen: set[str] = set()
    for fact in facts:
        for literal in fact.agent_name_literals:
            if literal not in seen:
                candidates.append(NameCandidate(value=literal, source="Agent_name_literal"))
                seen.add(literal)
    workspace_name = workspace.name
    if workspace_name and workspace_name not in seen:
        candidates.append(NameCandidate(value=workspace_name, source="workspace_dir"))
    return candidates


def _project_name_candidates(workspace: Path) -> list[NameCandidate]:
    candidates: list[NameCandidate] = []
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        match = _PYPROJECT_NAME_RE.search(text)
        if match:
            candidates.append(
                NameCandidate(value=match.group(1).strip(), source="pyproject")
            )
    candidates.append(NameCandidate(value=workspace.name, source="workspace_dir"))
    return candidates


def _suggested_sources(workspace: Path) -> list[dict[str, str]]:
    suggested: list[dict[str, str]] = []
    seen: set[str] = set()
    for pattern in OPENAPI_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            rel = _relative(path, workspace)
            if rel in seen:
                continue
            seen.add(rel)
            suggested.append({"type": "openapi", "path": rel})
    for pattern in MCP_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            rel = _relative(path, workspace)
            if rel in seen:
                continue
            seen.add(rel)
            suggested.append({"type": "mcp", "path": rel})
    return suggested
