"""Workspace classification: is this an agent project, and which framework(s).

Pass A of the v0.6 detection pipeline. Walks a workspace once, AST-parses
candidate ``.py`` files, scores per-framework signals, and returns a
:class:`DetectResult` for ``shipgate detect`` and for ``init`` Pass B.

This is *new* signal-scanning logic. Framework *scoring* deliberately does
not call the framework loaders in ``agents_shipgate.inputs.*`` — those gate
on a populated manifest and would no-op here. Instead it borrows their
constants where they map cleanly onto detection signals
(e.g. :data:`agents_shipgate.inputs.langchain.TOOL_DECORATOR_MODULES`).
The deliberate input-layer touchpoints are the suggested-source parse probe
(:func:`agents_shipgate.cli.discovery.artifacts.probe_suggested_source`) and
the local Codex marketplace root resolver. They keep ``init`` from writing a
source that ``scan`` rejects or duplicating a package already reached through
a marketplace; neither executes user code.

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
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from agents_shipgate.cli.discovery.artifacts import (
    ANTHROPIC_POLICY_PATTERNS,
    ANTHROPIC_TOOL_PATTERNS,
    CONDUCTOR_WORKFLOW_PATTERNS,
    MCP_PATTERNS,
    MODEL_CONFIG_PATTERNS,
    N8N_WORKFLOW_PATTERNS,
    OPENAI_TOOL_PATTERNS,
    OPENAPI_PATTERNS,
    POLICY_RULE_PATTERNS,
    TEST_CASE_PATTERNS,
    _candidate_files,
    _candidate_files_matching,
    _discover_patterns,
    _looks_like_n8n_workflow,
    _relative,
    probe_suggested_source,
)
from agents_shipgate.cli.discovery.scope import (
    PROJECT_MARKERS,
    WEAK_PROJECT_MARKERS,
    find_project_root,
    project_marker,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.codex_plugin import resolve_local_codex_marketplace_roots
from agents_shipgate.inputs.conductor import conductor_agent_task_types
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.detect import (
    AgentProjectCandidate,
    CodexPluginCandidate,
    DetectResult,
    FrameworkDetection,
    NameCandidate,
    WorkspaceSignals,
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
    "n8n": ("n8n", "@n8n/n8n-nodes-langchain"),
    "conductor": ("conductor-client", "conductor-server", "conductor-oss"),
    # openai_api is artifact-based; package hints aren't meaningful for it.
    "openai_api": (),
}

CONVENTIONAL_DIRS = ("prompts", "tools", ".agents-shipgate")


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
    # One inventory walk feeds the Python parse, the Codex plugin scan, and
    # the project-marker census below. The walk is unbounded; only the AST
    # parse is capped, and the census has to see the whole repository to
    # know whether the cap could have hidden a second project.
    inventory = _candidate_files(workspace)
    py_files, parse_truncated = _collect_python_files(
        inventory, max_files=max_python_files
    )
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
        "n8n": _FrameworkScore(),
        "conductor": _FrameworkScore(),
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
            # Artifact-defined frameworks (Anthropic, OpenAI API, n8n,
            # Conductor) have no Python file to attribute a project from, so
            # the artifact itself is their candidate file. This does not reach
            # the manifest: `_tool_sources_block` reads `candidate_files` only
            # for the Python-AST frameworks, and these four are not in that
            # set — their sources come from `suggested_sources` instead.
            scores[framework].add_file(hit.path)

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
    suggested_sources, excluded_sources = _suggested_sources(workspace)
    codex_plugin_candidates = _codex_plugin_candidates(workspace, inventory)
    agent_project_candidates = _agent_project_candidates(
        py_facts,
        detections,
        # An artifact-only project — an OpenAPI spec, an MCP export, a Codex
        # plugin package — fires no framework detection at all, so its
        # directory would otherwise be invisible to scope resolution while
        # `init` happily folds it into one root manifest. A nested
        # `shipgate.yaml` is the strongest form of the same evidence: a scope
        # somebody already drew by hand.
        [source["path"] for source in suggested_sources]
        + [candidate.path for candidate in codex_plugin_candidates]
        + _nested_manifest_paths(inventory, workspace),
        workspace,
    )
    agent_scope = _agent_scope(
        agent_project_candidates,
        parse_truncated=parse_truncated,
        project_roots=_project_root_count(inventory, workspace),
    )

    is_agent_project = bool(detections)
    if agent_scope == "ambiguous":
        # Naming one of the candidates here would be the same arbitrary pick
        # `init` refuses to make. The routable answer is the candidate list.
        next_action = (
            f"Agents were found in {len(agent_project_candidates)} separate "
            "projects; this workspace is not one manifest's scope. Run "
            "`init --workspace <agent_project_candidates[].path> --write` for "
            "the project you are changing."
        )
    elif agent_scope == "unknown":
        next_action = (
            f"Discovery stopped at {max_python_files} Python files in a "
            "workspace holding several project roots, so whether one manifest "
            "describes it was not established. Re-run with a higher "
            "--max-python-files, or run init in the project directory you are "
            "changing."
        )
    elif is_agent_project or suggested_sources or codex_plugin_candidates:
        next_action = render_command(["init", "--workspace", str(workspace)])
    else:
        next_action = "Workspace does not appear to be an agent project. No action."

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
        agent_scope=agent_scope,
        agent_project_candidates=agent_project_candidates,
        suggested_sources=suggested_sources,
        excluded_sources=excluded_sources,
        codex_plugin_candidates=codex_plugin_candidates,
        next_action=next_action,
        workspace_signals=workspace_signals,
    )


# --- Internals --------------------------------------------------------------


def _collect_python_files(
    inventory: list[Path], *, max_files: int
) -> tuple[list[Path], bool]:
    """Python files to parse, and whether the cap cut the list short.

    The caller needs the second half of that answer: a scope verdict
    computed from a truncated parse is a verdict about part of the
    repository, and saying "one project" about part of a repository is
    exactly the mistake this module exists to prevent.
    """

    files: list[Path] = []
    for path in inventory:
        if path.suffix != ".py":
            continue
        if len(files) >= max_files:
            return files, True
        files.append(path)
    return files, False


@dataclass
class _PyFacts:
    path: Path
    rel_path: str
    imports: set[str] = field(default_factory=set)
    decorators: set[str] = field(default_factory=set)
    constructors: set[str] = field(default_factory=set)
    agent_name_literals: list[str] = field(default_factory=list)
    #: Whether this file carries a supported framework signal. A name
    #: literal only draws a project boundary when it does — an unrelated
    #: module defining its own ``Agent`` class is not an agent project.
    framework: bool = False


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
    """Score one file, and record whether any framework claimed it.

    ``fact.framework`` is set here rather than recomputed elsewhere so the
    "is this an agent file" question has exactly one answer in this module.
    """

    before = {
        framework: len(state.candidate_files) for framework, state in scores.items()
    }
    _score_python_signals_inner(fact, scores)
    fact.framework = any(
        len(state.candidate_files) > before[framework]
        for framework, state in scores.items()
    )


def _score_python_signals_inner(
    fact: _PyFacts, scores: dict[str, _FrameworkScore]
) -> None:
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
    #: Workspace-relative path that produced the signal. Artifact-defined
    #: frameworks have no Python file to attribute a project from, so this
    #: is the only evidence that says which directory they live in.
    path: str


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
        "n8n": [],
        "conductor": [],
        "openai_api": [],
    }
    for path in _discover_patterns(workspace, ANTHROPIC_TOOL_PATTERNS):
        hits["anthropic"].append(
            _GlobHit(2.0, "strong", f"anthropic tool file: {path}", path)
        )
    for path in _discover_patterns(workspace, ANTHROPIC_POLICY_PATTERNS):
        hits["anthropic"].append(
            _GlobHit(2.0, "strong", f"anthropic policy file: {path}", path)
        )
    # openai-config.json is the OpenAI Messages API model-config marker —
    # belongs to openai_api, not the agents SDK (manifest.openai_api.model_config).
    for path in _discover_patterns(workspace, MODEL_CONFIG_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-config marker: {path}", path)
        )
    for path in _discover_patterns(workspace, OPENAI_TOOL_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai tool file: {path}", path)
        )
    for path in _discover_patterns(workspace, POLICY_RULE_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-api policy file: {path}", path)
        )
    for path in _discover_patterns(workspace, TEST_CASE_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-api test cases: {path}", path)
        )
    for path in _discover_patterns(workspace, N8N_WORKFLOW_PATTERNS):
        full_path = (workspace / path).resolve()
        if _looks_like_n8n_workflow(full_path):
            hits["n8n"].append(_GlobHit(2.0, "strong", f"n8n workflow: {path}", path))
    for path in _discover_patterns(workspace, CONDUCTOR_WORKFLOW_PATTERNS):
        full_path = (workspace / path).resolve()
        try:
            data = json.loads(full_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        markers = conductor_agent_task_types(data)
        if markers:
            hits["conductor"].append(
                _GlobHit(
                    2.0,
                    "strong",
                    f"Conductor AI/MCP workflow: {path} ({', '.join(sorted(markers))})",
                    path,
                )
            )
    return hits


def _collect_dir_hits(workspace: Path) -> dict[str, list[str]]:
    present = [d for d in CONVENTIONAL_DIRS if (workspace / d).is_dir()]
    if not present:
        return {f: [] for f in (
            "langchain", "crewai", "google_adk", "anthropic", "openai_agents_sdk",
            "n8n",
            "conductor",
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
            "n8n",
            "conductor",
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


def _agent_scope(
    candidates: list[AgentProjectCandidate],
    *,
    parse_truncated: bool,
    project_roots: int,
) -> str:
    """Whether one manifest can describe this workspace.

    ``"unknown"`` is not a softer ``"single"``. Python parsing stops at
    ``max_python_files``, so on a large repository the evidence behind a
    ``"single"`` verdict may simply be the part of the tree that got read
    first — and filesystem ordering is not a safety property. When the
    parse was cut short *and* the workspace holds more than one project
    root, a second agent project could be sitting in the unread remainder,
    so the answer is that no answer was established.

    Truncation alone is not enough to say that: a repository with one
    project root has nowhere for a second project to hide, however many
    files it holds, so large single-project repositories keep their
    ``"single"`` verdict and their working ``init``.
    """

    if len(candidates) > 1:
        return "ambiguous"
    if parse_truncated and project_roots > 1:
        return "unknown"
    return "single"


def _nested_manifest_paths(inventory: list[Path], workspace: Path) -> list[str]:
    """Manifests below the workspace root, as workspace-relative paths.

    A `shipgate.yaml` in a sub-directory is a manifest scope somebody has
    already drawn. Two of them mean the workspace is demonstrably not one
    scope, whatever the framework signals say — and unlike every other
    signal here, that conclusion needs no heuristic at all.
    """

    return [
        _relative(path, workspace)
        for path in inventory
        if path.name == "shipgate.yaml" and path.parent != workspace
    ]


def _project_root_count(inventory: list[Path], workspace: Path) -> int:
    """How many project roots the workspace holds, counted from the walk.

    Filename matching over the inventory the walk already produced — no
    parsing, no cap — so this stays trustworthy exactly where the AST pass
    stops being trustworthy.
    """

    markers = (*PROJECT_MARKERS, *WEAK_PROJECT_MARKERS)
    # Weak markers are counted here even though they need evidence to draw a
    # boundary: this number only decides whether a *second* project could be
    # hiding in the part of the tree the parse never reached, so counting one
    # that turns out not to be a project fails closed.
    roots = {path.parent for path in inventory if path.name in markers}
    if project_marker(workspace, extra=WEAK_PROJECT_MARKERS) is not None:
        roots.add(workspace)
    return len(roots)


def _agent_project_candidates(
    facts: list[_PyFacts],
    detections: list[FrameworkDetection],
    artifact_paths: list[str],
    workspace: Path,
) -> list[AgentProjectCandidate]:
    """Group the agent evidence in this workspace by the project it sits in.

    A file's project is the nearest directory at or above it that carries a
    project marker, bounded by the workspace; a file with no marker above it
    belongs to the workspace itself. Several agents inside *one* project are
    one manifest's business — a crew, a router and its sub-agents. Agents in
    *separate* projects are not: one ``agent.name``, one ``declared_purpose``,
    and one ``tool_sources`` list cannot describe both, which is what makes
    the scope ambiguous (#363).

    Evidence is everything ``init`` would turn into a manifest: the file set
    the frameworks fired on, every framework-attributed ``Agent(name=…)``
    literal, and the artifact paths (``suggested_sources``, Codex plugin
    packages and marketplaces) that become ``tool_sources`` on their own. No
    single one of those is enough. The literal is the value ``init`` adopts
    for ``agent.name`` without asking — but the #363 agent is constructed as
    ``LlmAgent(name=CONFIG.agent_name)`` and has none. The framework file
    set covers that — but an OpenAPI- or MCP-only project fires no framework
    detection at all, and two of those under one root is the same
    one-manifest-for-two-agents outcome with none of the Python evidence.

    *Framework-attributed* is the operative word for the literals. A file
    with no supported framework import that happens to construct its own
    ``Agent(name="crm")`` is not an agent project, and reading it as one
    would refuse ``init`` on a repository that has exactly one (#363
    review). Those literals stay in ``agent_name_candidates`` as name
    suggestions; they just do not draw a boundary.
    """

    # Directories holding agent evidence, which is what lets a weak marker
    # (a bare ``requirements.txt``) count as a project root there and only
    # there. Collected before attribution because the walk up needs it.
    evidence_paths: list[Path] = [
        workspace / relative
        for detection in detections
        for relative in detection.candidate_files
    ]
    evidence_paths.extend(workspace / relative for relative in artifact_paths)
    evidence_paths.extend(
        fact.path for fact in facts if fact.agent_name_literals and fact.framework
    )
    evidence_dirs = frozenset(
        path if path.is_dir() else path.parent for path in evidence_paths
    )

    names: dict[Path, set[str]] = {}
    markers: dict[Path, str | None] = {}

    def _project_of(path: Path) -> Path:
        # A Codex plugin package is named by its directory, not by a file
        # inside it; everything else is a file whose directory we want.
        directory = path if path.is_dir() else path.parent
        try:
            directory.relative_to(workspace)
        except ValueError:
            # A source reached through a symlink out of the workspace cannot
            # name a project inside it, so attribute it to the workspace
            # rather than to a directory nobody asked about.
            found = None
        else:
            found = find_project_root(
                directory, root=workspace, evidence_dirs=evidence_dirs
            )
        project = found.directory if found is not None else workspace
        if project not in markers:
            markers[project] = (
                found.marker if found is not None else project_marker(workspace)
            )
            names.setdefault(project, set())
        return project

    for path in evidence_paths:
        _project_of(path)
    for fact in facts:
        if fact.agent_name_literals and fact.framework:
            names[_project_of(fact.path)].update(fact.agent_name_literals)

    candidates = [
        AgentProjectCandidate(
            path=(
                project.relative_to(workspace).as_posix()
                if project != workspace
                else "."
            ),
            marker=markers[project],
            agent_names=sorted(found_names),
        )
        for project, found_names in names.items()
    ]
    return sorted(candidates, key=lambda candidate: candidate.path)


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


def _suggested_sources(
    workspace: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Suggest OpenAPI/MCP artifact files the real input adapters accept.

    Returns ``(suggested, excluded)``. A glob match that fails the adapter
    parse probe lands in ``excluded`` as ``{type, path, reason}`` instead
    of ``suggested`` — ``init`` consumes ``suggested`` verbatim, and a
    manifest pointing at an unparseable file fails ``scan`` out of the box.
    The literal ``.mcp.json`` skip below is the same rule for the one host
    config filename known in advance; the probe generalizes it to any
    ``mcpServers``-shaped or otherwise unparseable file (silently for
    ``.mcp.json``, with a visible reason for everything else).
    """
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern in OPENAPI_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            rel = _relative(path, workspace)
            if ("openapi", rel) in seen:
                continue
            seen.add(("openapi", rel))
            candidates.append(("openapi", rel))
    for pattern in MCP_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            if path.name == ".mcp.json":
                continue
            rel = _relative(path, workspace)
            if ("mcp", rel) in seen:
                continue
            seen.add(("mcp", rel))
            candidates.append(("mcp", rel))
    for pattern in CONDUCTOR_WORKFLOW_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            rel = _relative(path, workspace)
            if ("conductor", rel) in seen:
                continue
            seen.add(("conductor", rel))
            candidates.append(("conductor", rel))

    suggested: list[dict[str, str]] = []
    suggested_paths: set[str] = set()
    failures: list[dict[str, str]] = []
    for source_type, rel in candidates:
        if rel in suggested_paths:
            continue
        reason = probe_suggested_source(workspace, rel, source_type)
        if reason is None:
            suggested.append({"type": source_type, "path": rel})
            suggested_paths.add(rel)
        else:
            failures.append({"type": source_type, "path": rel, "reason": reason})
    # A path can match both pattern families (e.g. ``*openapi*mcp*.json``);
    # only report it excluded when no type accepted it.
    excluded = [entry for entry in failures if entry["path"] not in suggested_paths]
    return suggested, excluded


def _codex_plugin_candidates(
    workspace: Path, inventory: list[Path]
) -> list[CodexPluginCandidate]:
    files = inventory
    covered_roots: set[Path] = set()
    for path in files:
        if not (
            path.name == "marketplace.json"
            and path.parent.as_posix().endswith(".agents/plugins")
        ):
            continue
        try:
            covered_roots.update(
                resolve_local_codex_marketplace_roots(
                    marketplace_path=path,
                    base_dir=workspace,
                )
            )
        except (InputParseError, OSError, RuntimeError, UnicodeError):
            continue

    candidates: list[CodexPluginCandidate] = []
    seen: set[tuple[str, str]] = set()
    for path in files:
        if path.name == "plugin.json" and path.parent.name == ".codex-plugin":
            root = path.parent.parent
            try:
                if root.resolve() in covered_roots:
                    continue
            except (OSError, RuntimeError):
                pass
            rel = _relative(root, workspace)
            key = ("package", rel)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                CodexPluginCandidate(
                    mode="package",
                    path=rel,
                    evidence=f"Codex plugin manifest: {_relative(path, workspace)}",
                )
            )
        elif path.name == "marketplace.json" and path.parent.as_posix().endswith(
            ".agents/plugins"
        ):
            rel = _relative(path, workspace)
            key = ("marketplace", rel)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                CodexPluginCandidate(
                    mode="marketplace",
                    path=rel,
                    evidence=f"Codex plugin marketplace: {rel}",
                )
            )
    return sorted(candidates, key=lambda item: (item.mode, item.path))
