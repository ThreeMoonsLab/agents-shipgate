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

Agent-name candidate ranking: see :func:`_rank_agent_name_candidates`.
``pyproject.[project].name`` seeds ``project.name``, NOT ``agent.name``.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Sequence
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
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.codex_plugin import resolve_local_codex_marketplace_roots
from agents_shipgate.inputs.conductor import conductor_agent_task_types
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.detect import (
    AgentNameCandidate,
    AgentNameRole,
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

# --- Agent-name evidence vocabulary -----------------------------------------

# Constructions whose ``name=`` keyword names an agent. Deliberately not
# :data:`GOOGLE_ADK_AGENT_CLASSES`, which those two names coincide with:
# this set is framework-agnostic (it also catches the OpenAI Agents SDK's
# and CrewAI's ``Agent``) and is about extracting an identity, not about
# scoring a framework. ``App`` is absent on purpose: ``App(name=…)`` names
# the *application*, not the agent, and only its ``root_agent=`` binding is
# read (below).
AGENT_NAME_CLASSES = {"Agent", "LlmAgent"}
# Classes that bind an agent as the application root. Google ADK's
# ``App(root_agent=…)`` is the explicit form; ``root_agent`` as a module
# symbol is the conventional one ``adk run``/``adk web`` discover.
APP_ROOT_CLASSES = {"App"}
ROOT_AGENT_SYMBOL = "root_agent"
# Keywords whose list elements are children of the surrounding agent.
# ``sub_agents`` is Google ADK; ``handoffs`` is the OpenAI Agents SDK.
CHILD_AGENT_KEYWORDS = ("sub_agents", "handoffs")
# Callables whose second positional argument is a static default an agent
# name can be resolved through without importing user code.
ENV_LOOKUP_CALLS = {"os.environ.get", "os.getenv", "environ.get", "getenv"}

# Quality floor for a value that may be written as ``agent.name``. A
# one-character loop-variable-grade identifier and a scaffolding placeholder
# are both context-poor enough that a CHANGE_ME placeholder plus an explicit
# review action is the more honest output. Both are compared against the
# value's normalised form, so ``my_agent``/``My-Agent`` collapse onto the
# same entry.
AGENT_NAME_MIN_LENGTH = 3
GENERIC_AGENT_NAME_VALUES = frozenset(
    {
        "agent",
        "agents",
        "bar",
        "baz",
        "changeme",
        "dummy",
        "example",
        "foo",
        "myagent",
        "name",
        "placeholder",
        "qux",
        "sample",
        "temp",
        "test",
        "tests",
        "tmp",
        "todo",
        "untitled",
    }
)


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

    project_name_candidates = _project_name_candidates(workspace)
    agent_name_candidates = _rank_agent_name_candidates(
        py_facts, workspace, project_name_candidates
    )
    suggested_sources, excluded_sources = _suggested_sources(workspace)
    codex_plugin_candidates = _codex_plugin_candidates(workspace)

    is_agent_project = bool(detections)
    next_action = (
        render_command(["init", "--workspace", str(workspace)])
        if is_agent_project or suggested_sources or codex_plugin_candidates
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
        excluded_sources=excluded_sources,
        codex_plugin_candidates=codex_plugin_candidates,
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
class _Constant:
    """A module-level string constant an agent name can resolve through."""

    value: str
    # "module_constant" for ``NAME = "…"``; "env_default" for
    # ``NAME = os.environ.get("…", "…")``, whose value is the *declared
    # default* and can be overridden at runtime.
    provenance: str


@dataclass
class _AgentNameEvidence:
    """One ``Agent(name=…)`` site, with the hierarchy it sits in.

    Exactly one of ``literal``/``symbol`` is set: a string constant resolves
    immediately, a bare name needs the cross-module pass in
    :func:`_resolve_agent_name_evidence`.
    """

    role: AgentNameRole
    rel_path: str
    literal: str | None = None
    symbol: str | None = None
    root_evidence: str = ""


@dataclass
class _PyFacts:
    path: Path
    rel_path: str
    imports: set[str] = field(default_factory=set)
    decorators: set[str] = field(default_factory=set)
    constructors: set[str] = field(default_factory=set)
    agent_names: list[_AgentNameEvidence] = field(default_factory=list)
    module_constants: dict[str, _Constant] = field(default_factory=dict)
    # Symbol → (module, level) for ``from <module> import <symbol>``. Level
    # is the relative-import dot count (0 for absolute).
    constant_imports: dict[str, tuple[str, int]] = field(default_factory=dict)


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
    hierarchy = _AgentHierarchy()
    agent_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                facts.imports.add(node.module)
                for alias in node.names:
                    facts.imports.add(f"{node.module}.{alias.name}")
            if node.module or node.level:
                for alias in node.names:
                    bound = alias.asname or alias.name
                    facts.constant_imports[bound] = (node.module or "", node.level)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                name = _decorator_name(decorator)
                if name:
                    facts.decorators.add(name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    hierarchy.assign_targets[id(node.value)] = target.id
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
                hierarchy.assign_targets[id(node.value)] = node.target.id
        elif isinstance(node, ast.Call):
            ctor = _call_name(node.func)
            if ctor:
                facts.constructors.add(ctor)
                tail = ctor.split(".")[-1]
                hierarchy.observe(node, tail)
                if tail in AGENT_NAME_CLASSES:
                    agent_calls.append(node)

    # Roles are assigned only once the whole module has been seen: the
    # ``App(root_agent=…)`` binding that identifies a coordinator can appear
    # after the construction it names, and reading it early is exactly the
    # source-order dependence these roles exist to remove.
    for call in agent_calls:
        evidence = _agent_name_evidence(call, hierarchy, facts.rel_path)
        if evidence is not None:
            facts.agent_names.append(evidence)

    for name, constant in _module_constants(tree).items():
        facts.module_constants[name] = constant
    return facts


@dataclass
class _AgentHierarchy:
    """Structural relationships between agent constructions in one module.

    Accumulated during the single parse walk; call nodes are keyed by
    ``id()``, and the tree stays alive for the duration of the parse.
    """

    assign_targets: dict[int, str] = field(default_factory=dict)
    root_symbols: set[str] = field(default_factory=set)
    root_calls: set[int] = field(default_factory=set)
    child_symbols: set[str] = field(default_factory=set)
    child_calls: set[int] = field(default_factory=set)

    def observe(self, call: ast.Call, tail: str) -> None:
        """Record what one call says about the agents around it."""
        for keyword in call.keywords:
            if tail in APP_ROOT_CLASSES and keyword.arg == ROOT_AGENT_SYMBOL:
                if isinstance(keyword.value, ast.Name):
                    self.root_symbols.add(keyword.value.id)
                elif isinstance(keyword.value, ast.Call):
                    self.root_calls.add(id(keyword.value))
            elif keyword.arg in CHILD_AGENT_KEYWORDS:
                for element in _sequence_elements(keyword.value):
                    if isinstance(element, ast.Name):
                        self.child_symbols.add(element.id)
                    elif isinstance(element, ast.Call):
                        self.child_calls.add(id(element))

    def role_for(self, call: ast.Call) -> tuple[AgentNameRole, str]:
        """Return ``(role, evidence)`` for one agent construction."""
        target = self.assign_targets.get(id(call))
        if id(call) in self.root_calls:
            return "root_agent", f"constructed inline as App({ROOT_AGENT_SYMBOL}=…)"
        if target and target in self.root_symbols:
            return "root_agent", f"bound as App({ROOT_AGENT_SYMBOL}={target})"
        if target == ROOT_AGENT_SYMBOL:
            return "root_agent", f"assigned to the conventional `{ROOT_AGENT_SYMBOL}` symbol"
        if id(call) in self.child_calls:
            return "sub_agent", "constructed inline inside another agent's children"
        if target and target in self.child_symbols:
            return "sub_agent", f"listed in another agent's children as `{target}`"
        return "agent", ""


def _sequence_elements(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return list(node.elts)
    return []


def _agent_name_evidence(
    call: ast.Call, hierarchy: _AgentHierarchy, rel_path: str
) -> _AgentNameEvidence | None:
    value = _name_keyword_node(call)
    if value is None:
        return None
    role, root_evidence = hierarchy.role_for(call)
    if isinstance(value, ast.Constant):
        literal = value.value
        if not isinstance(literal, str) or not literal.strip():
            return None
        return _AgentNameEvidence(
            role=role,
            rel_path=rel_path,
            literal=literal.strip(),
            root_evidence=root_evidence,
        )
    if isinstance(value, ast.Name):
        return _AgentNameEvidence(
            role=role, rel_path=rel_path, symbol=value.id, root_evidence=root_evidence
        )
    return None


def _module_constants(tree: ast.AST) -> dict[str, _Constant]:
    """Module-level ``NAME = <str>`` and ``NAME = os.environ.get(…, <str>)``.

    Module level only, and only these two forms. Anything conditional,
    computed, or f-string-interpolated is left unresolved so the caller
    fails closed to a placeholder rather than partially evaluating user
    code — the static-only boundary erodes one convenience at a time.
    """
    constants: dict[str, _Constant] = {}
    body = getattr(tree, "body", [])
    for node in body:
        targets: list[ast.expr]
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        constant = _static_string(value)
        if constant is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants.setdefault(target.id, constant)
    return constants


def _static_string(node: ast.expr) -> _Constant | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip():
        return _Constant(value=node.value.strip(), provenance="module_constant")
    if isinstance(node, ast.Call):
        callee = _call_name(node.func) or ""
        if callee in ENV_LOOKUP_CALLS and len(node.args) == 2:
            default = node.args[1]
            if (
                isinstance(default, ast.Constant)
                and isinstance(default.value, str)
                and default.value.strip()
            ):
                return _Constant(value=default.value.strip(), provenance="env_default")
    return None


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


def _name_keyword_node(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "name":
            return keyword.value
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
        "n8n": [],
        "conductor": [],
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
    for path in _discover_patterns(workspace, N8N_WORKFLOW_PATTERNS):
        full_path = (workspace / path).resolve()
        if _looks_like_n8n_workflow(full_path):
            hits["n8n"].append(_GlobHit(2.0, "strong", f"n8n workflow: {path}"))
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


def select_agent_name(
    candidates: Sequence[AgentNameCandidate],
) -> AgentNameCandidate | None:
    """The one candidate ``init`` may write as ``agent.name``.

    Single implementation on purpose. This decision previously lived
    inline in both the manifest renderer and the ``init`` JSON summary as
    a ``source in {…}`` set literal, which is two chances to disagree about
    the identity the manifest declares. ``None`` means every candidate
    failed the quality floor and the manifest keeps its CHANGE_ME
    placeholder.
    """
    for candidate in candidates:
        if candidate.selectable:
            return candidate
    return None


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def _is_test_path(rel_path: str) -> bool:
    """Whether ``rel_path`` is test code rather than product code.

    A name declared only in a test is a fixture name. It stays a candidate —
    it is often the real one — but it must not outrank a name the shipped
    code declares.
    """
    parts = Path(rel_path).parts
    if any(part in {"test", "tests"} for part in parts[:-1]):
        return True
    stem = Path(rel_path).name
    return stem == "conftest.py" or stem.startswith("test_") or stem.endswith("_test.py")


def _resolve_agent_name_evidence(
    evidence: _AgentNameEvidence,
    fact: _PyFacts,
    by_path: dict[Path, _PyFacts],
    workspace: Path,
) -> tuple[str, str, str] | None:
    """Resolve one evidence site to ``(value, provenance, detail)``.

    A literal resolves to itself. A bare symbol resolves through **one** hop:
    a module-level constant in the same file, or a module-level constant in a
    module this file imports the symbol from directly. The hop never chains —
    a constant that is itself a name stays unresolved — and the target module
    must be a file already parsed inside the workspace, so no path outside
    the walk is ever read.
    """
    if evidence.literal is not None:
        return evidence.literal, "literal", ""
    symbol = evidence.symbol
    if not symbol:
        return None
    local = fact.module_constants.get(symbol)
    if local is not None:
        return local.value, local.provenance, fact.rel_path
    imported = fact.constant_imports.get(symbol)
    if imported is None:
        return None
    module, level = imported
    for candidate_path in _constant_module_paths(fact.path, module, level, workspace):
        target = by_path.get(candidate_path)
        if target is None:
            continue
        constant = target.module_constants.get(symbol)
        if constant is not None:
            return constant.value, constant.provenance, target.rel_path
    return None


def _constant_module_paths(
    importer: Path, module: str, level: int, workspace: Path
) -> list[Path]:
    """Files ``from <module> import <symbol>`` could refer to, workspace-only.

    Two spellings reach the adjacent config module that agent packages use:
    the relative ``from .config import AGENT_NAME`` and the absolute
    ``from config import AGENT_NAME`` that works because the framework puts
    the agent directory on ``sys.path``. Both resolve to a sibling file.
    """
    parts = [part for part in module.split(".") if part]
    bases: list[Path] = []
    if level:
        base = importer.parent
        for _ in range(level - 1):
            base = base.parent
        bases.append(base)
    else:
        bases.append(importer.parent)
        bases.append(workspace)
    resolved: list[Path] = []
    for base in bases:
        for suffix in ((*parts[:-1], f"{parts[-1]}.py") if parts else (), (*parts, "__init__.py")):
            if not suffix:
                continue
            candidate = base.joinpath(*suffix)
            try:
                candidate = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            if candidate.is_relative_to(workspace) and candidate not in resolved:
                resolved.append(candidate)
    return resolved


@dataclass
class _RankedName:
    value: str
    role: AgentNameRole
    rel_path: str
    score: float
    selectable: bool
    rationale: list[str]
    order: int


def _rank_agent_name_candidates(
    facts: list[_PyFacts], workspace: Path, project_names: Sequence[NameCandidate]
) -> list[AgentNameCandidate]:
    """Rank ``Agent(name=…)`` evidence best-first.

    Source order is not a ranking. It used to be the whole policy, which is
    how a one-character test literal became a repository's declared identity
    (#320) and how a Salesforce worker outranked the coordinator that owns it
    (#324). Four signals decide the order instead:

    - **Hierarchy.** An application root outranks an unqualified agent, which
      outranks a declared sub-agent. This is the only signal that can reorder
      two equally plausible names.
    - **Origin.** Product code outranks test code.
    - **Corroboration.** A name the project name independently agrees with
      is two sources, not one.
    - **Quality floor.** A value too short or too generic to be an identity
      is ranked last and made unselectable, so ``init`` writes CHANGE_ME and
      asks for review rather than asserting something unreliable.

    Scores are published so a reordering regression is visible in
    ``detect --json`` instead of silently changing what the manifest claims.
    """
    by_path = {_safe_resolve(fact.path): fact for fact in facts}
    project_forms = {
        _normalise_name(candidate.value) for candidate in project_names if candidate.value
    }
    project_forms.add(_normalise_name(workspace.name))
    project_forms.discard("")

    best: dict[str, _RankedName] = {}
    order = 0
    for fact in facts:
        for evidence in fact.agent_names:
            resolved = _resolve_agent_name_evidence(evidence, fact, by_path, workspace)
            if resolved is None:
                continue
            value, provenance, detail = resolved
            ranked = _score_agent_name(
                value=value,
                role=evidence.role,
                rel_path=evidence.rel_path,
                root_evidence=evidence.root_evidence,
                provenance=provenance,
                detail=detail,
                project_forms=project_forms,
                order=order,
            )
            order += 1
            previous = best.get(value)
            if previous is None or ranked.score > previous.score:
                best[value] = ranked

    ordered = sorted(best.values(), key=lambda r: (not r.selectable, -r.score, r.order))
    candidates = [
        AgentNameCandidate(
            value=ranked.value,
            source="Agent_name_literal",
            role=ranked.role,
            path=ranked.rel_path,
            rank_score=round(ranked.score, 2),
            selectable=ranked.selectable,
            rationale=ranked.rationale,
        )
        for ranked in ordered
    ]

    workspace_name = workspace.name
    if workspace_name and workspace_name not in best:
        candidates.append(
            AgentNameCandidate(
                value=workspace_name,
                source="workspace_dir",
                role="workspace_dir",
                path=None,
                rank_score=0.0,
                selectable=False,
                rationale=[
                    "directory name, not a declared agent identity — reported "
                    "for reference, never written as agent.name"
                ],
            )
        )
    return candidates


def _score_agent_name(
    *,
    value: str,
    role: AgentNameRole,
    rel_path: str,
    root_evidence: str,
    provenance: str,
    detail: str,
    project_forms: set[str],
    order: int,
) -> _RankedName:
    score = 1.0
    rationale: list[str] = [f"declared as Agent(name=…) in {rel_path}"]

    if provenance == "module_constant":
        rationale.append(f"resolved from a module constant in {detail}")
    elif provenance == "env_default":
        rationale.append(
            f"resolved from the static default of an environment lookup in "
            f"{detail} — overridable at runtime"
        )

    if role == "root_agent":
        score += 3.0
        rationale.append(root_evidence or "bound as the application root")
    elif role == "sub_agent":
        score -= 1.5
        rationale.append(
            root_evidence or "declared as a child of another agent, not the root"
        )

    if _is_test_path(rel_path):
        score -= 2.0
        rationale.append("declared in test code, which names fixtures rather than the product")

    normalised = _normalise_name(value)
    if normalised and normalised in project_forms:
        score += 1.0
        rationale.append("corroborated by the project name")

    selectable = True
    if len(normalised) < AGENT_NAME_MIN_LENGTH:
        score -= 3.0
        selectable = False
        rationale.append(
            f"rejected: fewer than {AGENT_NAME_MIN_LENGTH} significant characters, "
            "too context-poor to assert as an identity"
        )
    elif normalised in GENERIC_AGENT_NAME_VALUES:
        score -= 3.0
        selectable = False
        rationale.append("rejected: generic scaffolding name, not an identity")

    return _RankedName(
        value=value,
        role=role,
        rel_path=rel_path,
        score=score,
        selectable=selectable,
        rationale=rationale,
        order=order,
    )


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


def _codex_plugin_candidates(workspace: Path) -> list[CodexPluginCandidate]:
    files = _candidate_files(workspace)
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
