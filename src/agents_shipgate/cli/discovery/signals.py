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
  (``prompts/``, ``tools/``, ``.agents-shipgate/``), wherever in the tree it
  sits — a Python distribution puts its tools under the import package
  (``awslabs/billing_cost_management_mcp_server/tools/``), and reading only
  the workspace root missed the one structural signal such a repository
  offers (#441).

A framework is *detected* when its score ≥ 2.0 AND it accumulated at
least one strong signal.

Agent-name candidate ranking: see :func:`_rank_agent_name_candidates`.
``pyproject.[project].name`` seeds ``project.name``, NOT ``agent.name``.
"""

from __future__ import annotations

import ast
import json
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

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
    _matches_pattern,
    _relative,
    _skip_part,
    probe_suggested_source,
)
from agents_shipgate.cli.discovery.mcp_source import (
    McpSourceDiscovery,
    discover_mcp_server_source,
)
from agents_shipgate.cli.discovery.scope import (
    PROJECT_MARKERS,
    WEAK_PROJECT_MARKERS,
    find_project_root,
    project_marker,
)
from agents_shipgate.core.errors import DiscoveryError, InputParseError
from agents_shipgate.core.surface_exclusions import build_detect_exclusions
from agents_shipgate.inputs.codex_plugin import resolve_local_codex_marketplace_roots
from agents_shipgate.inputs.conductor import conductor_agent_task_types
from agents_shipgate.inputs.mcp_server_source import SOURCE_TYPE
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.detect import (
    AgentNameCandidate,
    AgentNameRole,
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

#: The frameworks a conventional directory is weak evidence for. Deliberately
#: *not* every key in :func:`_initial_framework_scores` — and named here rather
#: than spelled out inside :func:`_collect_dir_hits`, so the difference reads as
#: a decision instead of as a second copy that drifted.
#:
#: ``mcp_server_source`` is the one absentee. Its evidence is already a
#: conjunction — a declared MCP dependency *and* a tool name resolved at a
#: registration site — so a ``tools/`` directory adds nothing it does not
#: already have, and adding it would let two conventional directories carry the
#: published detection confidence to ``high`` for a route the engine caps at
#: ``medium`` (#431).
CONVENTIONAL_DIR_FRAMEWORKS: tuple[str, ...] = (
    "langchain",
    "crewai",
    "google_adk",
    "anthropic",
    "openai_agents_sdk",
    "n8n",
    "conductor",
    "openai_api",
)

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
# Modules an aliased agent/app constructor may legitimately come from. An
# alias is only read as a framework constructor when its module is one of
# these; otherwise `X as Agent`-style renames of unrelated classes would
# widen recognition instead of sharpening it.
AGENT_FRAMEWORK_MODULE_PREFIXES = (
    "google.adk",
    "agents",
    "openai_agents",
    "crewai",
    "langchain",
    "langchain_core",
    "langgraph",
)
ROOT_AGENT_SYMBOL = "root_agent"
# Keywords whose list elements are children of the surrounding agent.
# ``sub_agents`` is Google ADK; ``handoffs`` is the OpenAI Agents SDK.
CHILD_AGENT_KEYWORDS = ("sub_agents", "handoffs")
# Score adjustments. Hierarchy and corroboration move a candidate within
# its origin; origin itself is meant to dominate, because the documented
# contract is that product code outranks test code — full stop, not "unless
# the test one happens to be a root". ORIGIN_TEST_PENALTY is therefore
# strictly greater than the whole spread of the other signals
# (ROOT_BONUS + CORROBORATION_BONUS − SUB_AGENT_PENALTY = 5.5), which keeps
# the published rank_score the single ordering key instead of needing a
# separate tier the score cannot explain.
# Conventional test module filenames that carry no ``test_`` prefix, so the
# prefix rule alone reads them as product code.
TEST_MODULE_NAMES = frozenset({"conftest.py", "test.py", "tests.py"})
ROOT_AGENT_BONUS = 3.0
SUB_AGENT_PENALTY = 1.5
CORROBORATION_BONUS = 1.0
ORIGIN_TEST_PENALTY = 6.0
QUALITY_FLOOR_PENALTY = 3.0

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

#: How many ``.py`` files one discovery pass will AST-parse. Shared by
#: :func:`detect_workspace` and :func:`weak_marker_evidence_dirs` so "the same
#: budget" is one number rather than two that agree today.
DEFAULT_MAX_PYTHON_FILES = 1000



def detect_workspace(
    workspace: Path, *, max_python_files: int = DEFAULT_MAX_PYTHON_FILES
) -> DetectResult:
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
    glob_hits = _collect_glob_hits(workspace, files=inventory)
    conventional_locations = _conventional_dir_locations(workspace, files=inventory)
    dir_hits = _collect_dir_hits(conventional_locations)

    scores = _initial_framework_scores()

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

    # The artifact probe runs before the detection loop because the source
    # route below is scored from its result: an MCP export is the better route
    # to the same server, so this one stands down wherever one exists (#431).
    suggested_sources, excluded_sources = _suggested_sources(
        workspace, files=inventory
    )
    mcp_source = discover_mcp_server_source(
        workspace,
        files=inventory,
        exported_source_paths=[
            source["path"] for source in suggested_sources if source["type"] == "mcp"
        ],
    )
    _score_mcp_server_source(mcp_source, scores)
    excluded_sources.extend(mcp_source.excluded)
    if mcp_source.path is not None:
        suggested_sources.append({"type": SOURCE_TYPE, "path": mcp_source.path})

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
    project_root_count = _project_root_count(inventory, workspace)
    python_file_total = sum(1 for path in inventory if path.suffix == ".py")
    agent_scope, agent_scope_truncated = _agent_scope(
        agent_project_candidates,
        parse_truncated=parse_truncated,
        project_roots=project_root_count,
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
        if agent_scope_truncated:
            # The list is what the caller is told to choose from, so it has
            # to say when it is a lower bound. Naming the uncapped project
            # root count bounds the claim with a number the cap never
            # touched, and the remedy makes the full list reachable (#395).
            next_action += (
                " That list may be incomplete: discovery stopped at "
                f"{max_python_files} Python files in a workspace holding "
                f"{project_root_count} candidate project scopes, so any "
                "project in the part of the tree that was not read is "
                "missing from it. Re-run with a higher --max-python-files "
                "before concluding a project is absent."
            )
    elif agent_scope == "unknown":
        next_action = (
            f"Discovery stopped at {max_python_files} Python files in a "
            f"workspace holding {project_root_count} candidate project "
            "scopes, so whether one manifest describes it was not "
            "established. Re-run with a higher --max-python-files, or run "
            "init in the project directory you are changing."
        )
    elif parse_truncated:
        # A settled scope is not a complete classification. On a workspace with
        # one candidate scope the branches above never fire, so a capped parse
        # fell straight through to `init` — which would adopt a tool surface
        # read from part of the tree — or to the flat negative, which was the
        # terminal false answer for an agent sitting past the cap (#399 review).
        next_action = (
            f"Discovery stopped at {max_python_files} Python files, so this "
            "classification describes the part of the workspace that was read. "
            "Re-run `detect --max-python-files "
            f"{python_file_total} --json` — a bound that covers every Python "
            "file — before treating any verdict here as complete."
        )
    elif is_agent_project or suggested_sources or codex_plugin_candidates:
        next_action = render_command(["init", "--workspace", str(workspace)])
    else:
        next_action = "Workspace does not appear to be an agent project. No action."

    # Workspace-relative *paths*, in `CONVENTIONAL_DIRS` order — not the bare
    # names this held while the check read only the root. Once it reads the
    # whole tree a name is no longer a location: the reproduction in #441 has
    # its only `tools/` at `awslabs/billing_cost_management_mcp_server/tools/`,
    # and reporting `tools` for it sent every reader of this field, and of the
    # negative-control message rendered from it, to a directory that does not
    # exist. A path at the root is still spelled as the bare name, which is
    # what `has_prompts_dir`-style *root* questions test against.
    present_dirs = [
        conventional_locations[d] for d in CONVENTIONAL_DIRS if d in conventional_locations
    ]
    workspace_signals = WorkspaceSignals(
        python_file_count=len(py_facts),
        python_file_total=python_file_total,
        project_root_count=project_root_count,
        has_pyproject_or_requirements=(
            (workspace / "pyproject.toml").is_file()
            or (workspace / "requirements.txt").is_file()
        ),
        # "somewhere in this workspace", which is the question #441 asked. Where
        # exactly is in `conventional_dirs`.
        has_prompts_dir="prompts" in conventional_locations,
        has_tools_dir="tools" in conventional_locations,
        conventional_dirs=present_dirs,
    )

    result = DetectResult(
        is_agent_project=is_agent_project,
        frameworks=detections,
        agent_name_candidates=agent_name_candidates,
        project_name_candidates=project_name_candidates,
        agent_scope=agent_scope,
        agent_project_candidates=agent_project_candidates,
        agent_scope_truncated=agent_scope_truncated,
        python_parse_truncated=parse_truncated,
        suggested_sources=suggested_sources,
        excluded_sources=excluded_sources,
        codex_plugin_candidates=codex_plugin_candidates,
        next_action=next_action,
        workspace_signals=workspace_signals,
    )
    result.surface_exclusions = build_detect_exclusions(result)
    return result


# --- Internals --------------------------------------------------------------


def _score_mcp_server_source(
    discovery: McpSourceDiscovery, scores: dict[str, _FrameworkScore]
) -> None:
    """Score the workspace's own MCP registration sites.

    The registration evidence is strong and reaches the detection threshold on
    its own, because the fact behind it is already a conjunction: a declared
    MCP dependency *and* a tool name resolved at a registration site. Splitting
    it into halves would let either alone reach the threshold with a
    conventional directory beside it, which is the coincidence the pairing
    exists to reject. The dependency then adds the same point a dependency adds
    for every other framework, so a route backed by both facts publishes
    `medium` rather than reading as the weakest thing discovery can say.

    The candidate file is the **route directory**, not the registration files
    under it, and that is a scope decision rather than a cosmetic one.
    `_agent_project_candidates` reads `candidate_files` to ask how many things
    one manifest would have to describe, and an MCP server is one thing however
    many packages its tools are spread across: contributing each registration
    file made `mongodb-js/mongodb-mcp-server` look like six separate projects,
    and the per-project `init` that split then published leads to a package
    whose own `package.json` declares no MCP dependency — a next step that
    cannot change the answer (#399).

    A monorepo publishing *several* servers therefore gets one route covering
    all of them, which is over-broad. Splitting on which package declares the
    MCP dependency was tried and rejected: in `mongodb-js/mongodb-mcp-server`
    the SDK is declared by an eval-harness package and by three packages that
    only support the one server, so the split returned four scopes and withheld
    the route from the repository this input exists to reach. The over-broad
    route is visible in the manifest `init` writes and an adopter narrows it;
    the withheld one leaves them where they started. See #431 for the
    measurement.
    """

    if not discovery.detected or discovery.path is None:
        return
    state = scores[SOURCE_TYPE]
    state.add(2.0, "strong", discovery.evidence[0])
    for index, line in enumerate(discovery.evidence[1:]):
        state.add(1.0 if index == 0 else 0.0, "medium", line)
    state.add_file(discovery.path)


def _initial_framework_scores() -> dict[str, _FrameworkScore]:
    """A fresh, empty score sheet — one entry per framework this pass knows.

    One list, because :func:`_score_python_signals` indexes it by name and a
    second copy that missed a framework would silently stop attributing that
    framework's files.
    """

    return {
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
        # mcp_server_source is not a Python framework and scores from neither
        # the AST pass nor a filename glob: it is the workspace's own
        # TypeScript or Go registration sites, scored by
        # :func:`discover_mcp_server_source` (#431). It keeps an entry here
        # because the detection loop reads this sheet, and a framework absent
        # from it is a framework that can never be reported.
        SOURCE_TYPE: _FrameworkScore(),
    }


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
    scope: int
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
    # (scope, bound name) → (module, level, imported name) for
    # ``from <module> import <imported> as <bound>``. Keyed by scope because
    # a helper-local import must not stand in for the module-level one, and
    # the imported name is kept because that — not the alias — is what the
    # target module actually defines.
    constant_imports: dict[tuple[int, str], tuple[str, int, str]] = field(
        default_factory=dict
    )
    # Every binding of every name, in scope and source order. A symbol bound
    # more than once is never resolved to a constant: the second write may be
    # conditional, computed, or in another scope, and picking either one
    # asserts a value Python may not produce.
    writes: dict[str, list[_Write]] = field(default_factory=dict)
    # ``import os``-style bindings: bound name → module. Used to prove that
    # an ``os.environ.get`` spelling really is the stdlib one.
    plain_imports: dict[str, str] = field(default_factory=dict)
    # ``global``/``nonlocal`` names per scope. These are declarations, not
    # bindings: they say which scope a later store in this scope lands in.
    global_declarations: dict[int, set[str]] = field(default_factory=dict)
    nonlocal_declarations: dict[int, set[str]] = field(default_factory=dict)
    # ``from x import *`` binds an unknowable set of names, so nothing in
    # this file can be shown to be un-rebound afterwards. Line numbers are
    # kept because a binding established *after* one restores provenance.
    star_import: bool = False
    star_imports: list[int] = field(default_factory=list)
    # Dotted paths mutated through attribute assignment or deletion
    # (``adk.Agent = fake``, ``del os.getenv``). No name binding records
    # these, and they can replace a constructor or a stdlib lookup.
    attribute_writes: set[str] = field(default_factory=set)
    # Lexical scope structure, shared with the hierarchy resolver.
    scope_parents: dict[int, int] = field(default_factory=dict)
    class_scopes: set[int] = field(default_factory=set)
    # An application root exists in this file but its identity could not be
    # established statically. Selection must then decline entirely rather
    # than fall through to whatever worker happens to rank next.
    unresolved_root: str = ""
    #: Whether this file carries a supported framework signal. A name
    #: literal only draws a project boundary when it does — an unrelated
    #: module defining its own ``Agent`` class is not an agent project.
    framework: bool = False

    def binding_count(self, name: str) -> int:
        return len(self.writes.get(name, []))

    def name_literals(self) -> list[str]:
        """Distinct ``Agent(name="…")`` string literals declared here.

        Project scoping (#363) draws boundaries from literals only. A name
        that needs cross-module resolution is not available per-file, and a
        boundary is about *where* agents live, not what they are called.
        """
        seen: list[str] = []
        for evidence in self.agent_names:
            if evidence.literal is not None and evidence.literal not in seen:
                seen.append(evidence.literal)
        return seen


_MODULE_SCOPE = 0
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
# Comprehensions have their own scope in Python 3: `[App for App in ()]`
# does not rebind a module-level `App`. Treating the target as a module
# write shadowed the real import and lost the application root.
_COMPREHENSION_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# Constructs whose bodies may or may not run. An assignment under one of
# these is not provably the binding a later reference sees, so the reference
# fails closed instead of guessing a branch.
_BRANCH_NODES = tuple(
    node
    for node in (
        ast.If,
        ast.IfExp,
        ast.Try,
        getattr(ast, "TryStar", ast.Try),
        ast.While,
        ast.For,
        ast.AsyncFor,
        ast.Match,
    )
)


@dataclass
class _ScopeMap:
    """Where each node sits, and what that says about certainty."""

    nodes: list[tuple[ast.AST, int, bool]] = field(default_factory=list)
    parents: dict[int, int] = field(default_factory=dict)
    class_scopes: set[int] = field(default_factory=set)
    # Whether the *definition* that introduces a scope is itself conditional.
    # A function body is straight-line relative to itself, but if the `def`
    # only runs in one arm of a branch, everything the body claims is
    # contingent on that arm — including which agent is the application root.
    declaration_conditional: dict[int, bool] = field(default_factory=dict)


def _walk_scoped(tree: ast.AST) -> _ScopeMap:
    """Nodes paired with their lexical scope and whether they run conditionally.

    ``ast.walk`` flattens both, which is wrong for anything that models name
    binding: a helper's local ``root_agent`` is not the module's, a helper's
    local import is not the one a module-level construction reads, and an
    assignment inside an ``if`` is not the binding a later line is guaranteed
    to see. Scopes are identified by the ``id()`` of the node that introduces
    them; the module is :data:`_MODULE_SCOPE`.

    Definition *headers* — decorators, default values, annotations, class
    bases and keywords — are walked in the **enclosing** scope, because that
    is where Python evaluates them. Treating them as part of the body let a
    parameter shadow a constructor the default expression had already used.
    Comprehensions get their own scope for the same reason a function does,
    with the outermost iterable evaluated outside it.
    """
    scopes = _ScopeMap()
    scopes.declaration_conditional[_MODULE_SCOPE] = False

    def open_scope(node: ast.AST, scope: int, conditional: bool) -> int:
        inner = id(node)
        scopes.parents[inner] = scope
        scopes.declaration_conditional[inner] = conditional or (
            scopes.declaration_conditional.get(scope, False)
        )
        return inner

    def visit(node: ast.AST, scope: int, conditional: bool) -> None:
        scopes.nodes.append((node, scope, conditional))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            inner = open_scope(node, scope, conditional)
            for header in _definition_header(node):
                visit(header, scope, conditional)
            for arg in _argument_nodes(node.args):
                visit(arg, inner, False)
            body = node.body if isinstance(node.body, list) else [node.body]
            for statement in body:
                visit(statement, inner, False)
            return
        if isinstance(node, ast.ClassDef):
            inner = open_scope(node, scope, conditional)
            scopes.class_scopes.add(inner)
            for header in _definition_header(node):
                visit(header, scope, conditional)
            for statement in node.body:
                visit(statement, inner, False)
            return
        if isinstance(node, _COMPREHENSION_NODES):
            inner = open_scope(node, scope, conditional)
            generators = node.generators
            if generators:
                # Only the outermost iterable is evaluated eagerly, in the
                # enclosing scope; everything else runs per item, if at all.
                visit(generators[0].iter, scope, conditional)
            for index, generator in enumerate(generators):
                visit(generator.target, inner, False)
                if index:
                    visit(generator.iter, inner, True)
                for guard in generator.ifs:
                    visit(guard, inner, True)
            for element in _comprehension_elements(node):
                visit(element, inner, True)
            return
        inner_conditional = conditional or isinstance(node, _BRANCH_NODES)
        for child in ast.iter_child_nodes(node):
            visit(child, scope, inner_conditional)

    visit(tree, _MODULE_SCOPE, False)
    return scopes


def _definition_header(node: ast.AST) -> list[ast.expr]:
    """Expressions a ``def``/``class``/``lambda`` evaluates before its body."""
    header: list[ast.expr] = []
    header.extend(getattr(node, "decorator_list", []))
    bases = getattr(node, "bases", None)
    if bases:
        header.extend(bases)
    for keyword in getattr(node, "keywords", []) or []:
        header.append(keyword.value)
    returns = getattr(node, "returns", None)
    if returns is not None:
        header.append(returns)
    args = getattr(node, "args", None)
    if isinstance(args, ast.arguments):
        header.extend(args.defaults)
        header.extend(default for default in args.kw_defaults if default is not None)
        for arg in _argument_nodes(args):
            if arg.annotation is not None:
                header.append(arg.annotation)
    return header


def _argument_nodes(args: ast.arguments) -> list[ast.arg]:
    collected = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg is not None:
        collected.append(args.vararg)
    if args.kwarg is not None:
        collected.append(args.kwarg)
    return collected


def _comprehension_elements(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.DictComp):
        return [node.key, node.value]
    return [node.elt]


@dataclass
class _Write:
    """One binding of a name: where it happens and what it binds.

    Every binding is recorded, not just the ones that construct agents.
    A later ``root_agent = build_root()`` overwrites an earlier
    ``root_agent = Agent(name="Stale")``, and a model that only knows about
    agent constructions cannot see that the earlier one stopped being the
    root.
    """

    scope: int
    lineno: int
    conditional: bool
    # Set only when this binding's value is a verified agent construction.
    call_id: int | None = None
    # How the name was bound. "import" is the only kind that can make a
    # spelling a framework constructor; every other kind shadows it.
    kind: str = "assignment"


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
    scopes = _walk_scoped(tree)
    nodes = scopes.nodes
    facts.scope_parents = scopes.parents
    facts.class_scopes = scopes.class_scopes
    hierarchy = _AgentHierarchy(
        scope_parents=scopes.parents,
        class_scopes=scopes.class_scopes,
        writes=facts.writes,
    )
    agent_calls: list[tuple[ast.Call, int, bool]] = []
    # Store-context Name nodes are visited independently of the assignment
    # they belong to, so the value each one binds is attached afterwards.
    agent_construction_targets: dict[int, int] = {}
    write_by_node: dict[int, _Write] = {}
    pending_calls: list[tuple[ast.Call, str, int, bool]] = []
    for node, scope, conditional in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imports.add(alias.name)
                bound = (alias.asname or alias.name).split(".")[0]
                _record_write(facts, bound, scope, node.lineno, conditional, "import")
                # `import a.b.c` binds `a` and `a` denotes `a`; `import a.b.c
                # as x` binds `x` and `x` denotes `a.b.c`. Storing the full
                # path under the head made `a.b.c.Agent` resolve to
                # `a.b.c.b.c`, so the dotted check never matched.
                facts.plain_imports[bound] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                facts.imports.add(node.module)
                for alias in node.names:
                    facts.imports.add(f"{node.module}.{alias.name}")
            for alias in node.names:
                if alias.name == "*":
                    # A star import binds an unknown set of names. Nothing in
                    # this file can be shown to be un-rebound afterwards.
                    facts.star_import = True
                    facts.star_imports.append(node.lineno)
                    continue
                bound = alias.asname or alias.name
                _record_write(facts, bound, scope, node.lineno, conditional, "import")
                if node.module or node.level:
                    facts.constant_imports[(scope, bound)] = (
                        node.module or "",
                        node.level,
                        alias.name,
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A `class` statement binds its name in the enclosing scope just
            # as `def` does, and either one retires an agent bound there.
            _record_write(facts, node.name, scope, node.lineno, conditional, "definition")
            for decorator in node.decorator_list:
                name = _decorator_name(decorator)
                if name:
                    facts.decorators.add(name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            write_by_node[id(node)] = _record_write(
                facts, node.id, scope, node.lineno, conditional
            )
        elif isinstance(node, ast.Attribute) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            dotted = _call_name(node)
            if dotted:
                facts.attribute_writes.add(dotted)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
            # `del root_agent` removes the binding. Recording it as a write
            # that holds no agent is what retires the previous construction.
            _record_write(facts, node.id, scope, node.lineno, conditional, "delete")
        elif isinstance(node, ast.arg):
            _record_write(facts, node.arg, scope, node.lineno, conditional, "parameter")
        elif isinstance(node, ast.ExceptHandler) and node.name:
            _record_write(facts, node.name, scope, node.lineno, conditional, "except")
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            _record_write(facts, node.name, scope, node.lineno, conditional, "match")
        elif isinstance(node, ast.MatchMapping) and node.rest:
            _record_write(facts, node.rest, scope, node.lineno, conditional, "match")
        elif isinstance(node, ast.Global):
            # A declaration, not a binding: it says where later stores in
            # this scope land. Recording it as a write hid the store it
            # redirects, which is how a stale module root kept the role.
            facts.global_declarations.setdefault(scope, set()).update(node.names)
        elif isinstance(node, ast.Nonlocal):
            facts.nonlocal_declarations.setdefault(scope, set()).update(node.names)
        elif isinstance(node, ast.Call):
            ctor = _call_name(node.func)
            if ctor:
                facts.constructors.add(ctor)
                pending_calls.append((node, ctor, scope, conditional))

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    # Whether the value really constructs an agent depends on
                    # bindings this walk has not finished collecting, so the
                    # decision is deferred with the rest of them.
                    agent_construction_targets[id(target)] = node.value

    hierarchy.star_import = facts.star_import
    _apply_scope_declarations(facts)

    # Constructor provenance needs the completed binding table: a bare
    # ``Agent`` is only a framework constructor when nothing in this file
    # rebound the spelling, and an aliased ``RealAgent`` is one when the
    # import says so. Deciding during the walk would read a half-built table.
    roles = [
        (call, _constructor_role(ctor, scope, call.lineno, facts), scope, conditional)
        for call, ctor, scope, conditional in pending_calls
    ]
    # Agent constructions are identified before any App is read, because an
    # ``App(root_agent=Agent(…))`` can be reached before the inner call.
    for call, role, scope, conditional in roles:
        if role == "agent":
            agent_calls.append((call, scope, conditional))
            hierarchy.agent_call_ids.add(id(call))
    agent_call_ids = hierarchy.agent_call_ids
    for call, role, scope, conditional in roles:
        if role is not None:
            hierarchy.observe(
                call,
                role,
                scope,
                conditional or scopes.declaration_conditional.get(scope, False),
            )

    for node_id, call in agent_construction_targets.items():
        write = write_by_node.get(node_id)
        if write is not None and id(call) in agent_call_ids:
            write.call_id = id(call)

    # Roles are assigned only once the whole module has been seen: the
    # ``App(root_agent=…)`` binding that identifies a coordinator can appear
    # after the construction it names, and reading it early is exactly the
    # source-order dependence these roles exist to remove.
    hierarchy.resolve_references()
    # Source order, not traversal order. Equal-scoring candidates are broken
    # by first appearance, and that tie-break should mean what a reader would
    # mean by it rather than depending on how the walk happens to enumerate
    # siblings.
    agent_calls.sort(key=lambda item: (item[0].lineno, item[0].col_offset))
    for call, scope, _conditional in agent_calls:
        evidence = _agent_name_evidence(call, scope, hierarchy, facts.rel_path)
        if evidence is not None:
            facts.agent_names.append(evidence)
        elif id(call) in hierarchy.root_calls or id(call) in hierarchy.resolved_root_calls:
            # An explicit root whose name is dynamic. #324 requires this to
            # produce a placeholder, not a fallback to some other agent.
            facts.unresolved_root = (
                f"{facts.rel_path}: the application root's name is not a static value"
            )
    if hierarchy.unresolved_root:
        facts.unresolved_root = f"{facts.rel_path}: {hierarchy.unresolved_root}"

    facts.module_constants.update(_module_constants(tree, facts))
    return facts


def _apply_scope_declarations(facts: _PyFacts) -> None:
    """Route writes declared ``global``/``nonlocal`` to the scope they bind.

    ``global root_agent`` inside a function means a store there rebinds the
    *module* symbol. Left in the function's scope it was invisible to
    module-level resolution, so a stale module-level root kept the role
    while the runtime had been rebound underneath it.

    The redirected write is marked conditional: whether the function ever
    runs — and when, relative to the module body — is not something this
    file can establish, so the module symbol's value stops being provable
    rather than becoming the redirected one.
    """
    if not (facts.global_declarations or facts.nonlocal_declarations):
        return
    for name, writes in facts.writes.items():
        for write in writes:
            if name in facts.global_declarations.get(write.scope, set()):
                write.scope = _MODULE_SCOPE
                write.conditional = True
            elif name in facts.nonlocal_declarations.get(write.scope, set()):
                # The nearest enclosing function scope that binds the name.
                # Without one the declaration is a SyntaxError, so a missing
                # target means the file will not run as written; leaving the
                # write where it is keeps the lookup unprovable either way.
                enclosing = _enclosing_binding_scope(facts, name, write.scope)
                if enclosing is not None:
                    write.scope = enclosing
                write.conditional = True


def _enclosing_binding_scope(facts: _PyFacts, name: str, scope: int) -> int | None:
    seen: set[int] = {scope}
    current = facts.scope_parents.get(scope, _MODULE_SCOPE)
    while current not in seen:
        seen.add(current)
        if current == _MODULE_SCOPE:
            return None
        if any(write.scope == current for write in facts.writes.get(name, [])):
            return current
        current = facts.scope_parents.get(current, _MODULE_SCOPE)
    return None


def _constructor_role(
    ctor: str, scope: int, lineno: int, facts: _PyFacts
) -> str | None:
    """Whether ``ctor`` names an agent constructor, an app, or neither.

    The binding that reaches the *call site* decides. A spelling bound to a
    local ``def``/``class``, bound conditionally, or bound only after the
    call is not the framework's — the last of those is why the binding has
    to be located rather than merely found. Dotted spellings are held to the
    same standard: their head must prove a framework module, so a local
    ``class fake: class Agent`` cannot borrow the terminal name.

    The terminal-name reading survives only for a genuinely unbound head,
    where the file says nothing to contradict it — that is how a third-party
    re-export stays recognised.
    """
    parts = ctor.split(".")
    role = _class_role(parts[-1])
    head = parts[0]
    binding = _reaching_binding(facts, head, scope, lineno)

    # A wildcard import may legally replace any name it does not shadow, so
    # a spelling whose binding predates one is no longer proven. A binding
    # *after* the wildcard re-establishes provenance.
    for star_lineno in facts.star_imports:
        if star_lineno < lineno and (binding is None or binding.lineno < star_lineno):
            return None

    # `adk.Agent = fake` rebinds through the module object, which no name
    # binding records. Any such mutation of this path retires its provenance.
    if _attribute_rebound(ctor, facts):
        return None

    if binding is None:
        return role
    if binding.kind != "import":
        return None
    module = _imported_module(head, binding, facts)
    if module is None:
        return None
    resolved = ".".join([module, *parts[1:-1]]) if len(parts) > 2 else module
    if len(parts) == 1:
        # `from google.adk.agents import LlmAgent as RealAgent` — the class
        # itself was imported, so its original name is what counts.
        imported = facts.constant_imports.get((binding.scope, head))
        if imported is None:
            return None
        origin_module, _level, original = imported
        origin_role = _class_role(original)
        if origin_role is None:
            return None
        if head == original or _is_framework_module(origin_module):
            return origin_role
        return None
    if role is None or not _is_framework_module(resolved):
        return None
    return role


def _imported_module(head: str, binding: _Write, facts: _PyFacts) -> str | None:
    """The module a bound name denotes, for an ``import``-kind binding."""
    plain = facts.plain_imports.get(head)
    if plain is not None:
        return plain
    imported = facts.constant_imports.get((binding.scope, head))
    if imported is None:
        return None
    module, _level, original = imported
    return f"{module}.{original}" if module else original


def _attribute_rebound(ctor: str, facts: _PyFacts) -> bool:
    return any(
        ctor == path or ctor.startswith(f"{path}.") for path in facts.attribute_writes
    )


def _class_role(name: str) -> str | None:
    if name in AGENT_NAME_CLASSES:
        return "agent"
    if name in APP_ROOT_CLASSES:
        return "app"
    return None


def _is_framework_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in AGENT_FRAMEWORK_MODULE_PREFIXES
    )


def _scope_lookup_chain(
    scope_parents: dict[int, int], class_scopes: set[int], scope: int
) -> list[int]:
    """``scope`` and every scope a name lookup falls through to.

    Python resolves a free name against the enclosing *function* scopes
    before the module. Class bodies are excluded from the ancestors: a
    method referencing a name bound in its class body raises ``NameError``
    rather than seeing it.
    """
    chain: list[int] = [scope]
    seen: set[int] = {scope}
    current = scope_parents.get(scope, _MODULE_SCOPE)
    while current not in seen:
        seen.add(current)
        if current not in class_scopes:
            chain.append(current)
        if current == _MODULE_SCOPE:
            break
        current = scope_parents.get(current, _MODULE_SCOPE)
    return chain


def _reaching_write(
    writes: dict[str, list[_Write]], chain: list[int], name: str, lineno: int
) -> tuple[_Write | None, str]:
    """The binding a reference at ``lineno`` sees, or why it is unprovable.

    One implementation for both questions that ask it — which agent a symbol
    holds, and whether a constructor spelling is the framework's — because
    two copies of this rule would be two chances to disagree about the
    identity a manifest declares.

    Within the reference's own scope the latest binding *before* it wins;
    a later one cannot reach backwards, which is what let a framework import
    at the bottom of a file retroactively validate a call above it. In an
    enclosing or module scope the line comparison does not apply at all — a
    function body executes when called, not where written — so only a single
    unconditional binding is provable there. A conditional binding is
    unprovable in either: the branch may not have run.
    """
    for index, candidate_scope in enumerate(chain):
        found = [write for write in writes.get(name, []) if write.scope == candidate_scope]
        if not found:
            continue
        if index == 0:
            reaching = [write for write in found if write.lineno < lineno]
            if not reaching:
                continue
            latest = max(reaching, key=lambda write: write.lineno)
            if latest.conditional:
                return None, (
                    f"`{name}` is bound under a conditional or loop, so which "
                    "value reaches this reference is not provable statically"
                )
            return latest, ""
        if any(write.conditional for write in found):
            return None, (
                f"`{name}` is bound under a conditional or loop, so which value "
                "reaches this reference is not provable statically"
            )
        if len(found) > 1:
            return None, (
                f"`{name}` is rebound in an enclosing scope, so which value a "
                "nested reference sees depends on when it runs"
            )
        return found[0], ""
    return None, f"`{name}` has no binding that reaches this reference"


def _reaching_binding(
    facts: _PyFacts, name: str, scope: int, lineno: int
) -> _Write | None:
    chain = _scope_lookup_chain(facts.scope_parents, facts.class_scopes, scope)
    write, _reason = _reaching_write(facts.writes, chain, name, lineno)
    return write


def _record_write(
    facts: _PyFacts,
    name: str,
    scope: int,
    lineno: int,
    conditional: bool,
    kind: str = "assignment",
) -> _Write:
    write = _Write(scope=scope, lineno=lineno, conditional=conditional, kind=kind)
    facts.writes.setdefault(name, []).append(write)
    return write


@dataclass
class _AgentHierarchy:
    """Structural relationships between agent constructions in one module.

    Accumulated during the single parse walk, then resolved once the module
    is fully seen. References are matched to the binding that actually
    reaches them — nearest enclosing scope, latest unconditional assignment
    before the reference — rather than to every assignment that happens to
    share the identifier. Call nodes are keyed by ``id()``, and the tree
    stays alive for the duration of the parse.
    """

    scope_parents: dict[int, int] = field(default_factory=dict)
    # Class bodies are scopes for binding but not for closure lookup.
    class_scopes: set[int] = field(default_factory=set)
    # Shared with the owning _PyFacts: every binding of every name, not only
    # the ones that construct agents. A later non-agent write to a root
    # symbol has to be visible here, or a stale construction keeps the role.
    writes: dict[str, list[_Write]] = field(default_factory=dict)
    # (name, scope, lineno) references collected during the walk.
    root_refs: list[tuple[str, int, int]] = field(default_factory=list)
    child_refs: list[tuple[str, int, int]] = field(default_factory=list)
    root_calls: set[int] = field(default_factory=set)
    child_calls: set[int] = field(default_factory=set)
    # Calls proven to construct an agent, filled before any App is read.
    agent_call_ids: set[int] = field(default_factory=set)
    # A wildcard import can rebind anything, so no lookup in this file is
    # provable once one is present.
    star_import: bool = False
    # Filled by resolve_references().
    resolved_root_calls: dict[int, str] = field(default_factory=dict)
    resolved_child_calls: dict[int, str] = field(default_factory=dict)
    unresolved_root: str = ""

    def observe(self, call: ast.Call, role: str, scope: int, conditional: bool) -> None:
        """Record what one call says about the agents around it.

        ``role`` comes from :func:`_constructor_role`, which resolves the
        callee through its binding — a call is only "an agent" or "an app"
        when the spelling provably is one.
        """
        is_app = role == "app"
        is_agent = role == "agent"
        for keyword in call.keywords:
            if is_app and keyword.arg == ROOT_AGENT_SYMBOL:
                if conditional:
                    # Which branch built the app decides which agent is the
                    # root, and that is a runtime fact. Recording the first
                    # one seen picks a branch and calls it the identity.
                    self.unresolved_root = (
                        f"App({ROOT_AGENT_SYMBOL}=…) is constructed under a "
                        "conditional or loop, so which agent is the root is not "
                        "provable statically"
                    )
                elif isinstance(keyword.value, ast.Name):
                    self.root_refs.append(
                        (keyword.value.id, scope, getattr(keyword.value, "lineno", 0))
                    )
                elif isinstance(keyword.value, ast.Call) and id(
                    keyword.value
                ) in self.agent_call_ids:
                    self.root_calls.add(id(keyword.value))
                else:
                    # A factory call or any other expression. The root is
                    # declared but its identity is not readable statically,
                    # which has to be recorded — dropping it silently is how
                    # a sub-agent ends up declared as the reviewed identity.
                    self.unresolved_root = (
                        f"App({ROOT_AGENT_SYMBOL}=…) is given an expression that "
                        "does not statically construct an agent"
                    )
            elif is_agent and keyword.arg in CHILD_AGENT_KEYWORDS:
                for element in _sequence_elements(keyword.value):
                    if isinstance(element, ast.Name):
                        self.child_refs.append(
                            (element.id, scope, getattr(element, "lineno", 0))
                        )
                    elif isinstance(element, ast.Call) and id(element) in (
                        self.agent_call_ids
                    ):
                        self.child_calls.add(id(element))

    def resolve_references(self) -> None:
        """Match each reference to the binding that actually reaches it."""
        for name, scope, lineno in self.root_refs:
            write, reason = self._reaching(name, scope, lineno)
            if write is None:
                self.unresolved_root = reason
            elif write.call_id is None:
                # The name reaches something that is not an agent
                # construction — a factory result, an import, a parameter.
                # Whatever agent it used to hold is no longer the root.
                self.unresolved_root = (
                    f"`{name}` last binds a value that is not a statically "
                    "readable agent construction"
                )
            else:
                self.resolved_root_calls.setdefault(
                    write.call_id, f"bound as App({ROOT_AGENT_SYMBOL}={name})"
                )
        for name, scope, lineno in self.child_refs:
            write, _reason = self._reaching(name, scope, lineno)
            if write is not None and write.call_id is not None:
                self.resolved_child_calls.setdefault(
                    write.call_id, f"listed in another agent's children as `{name}`"
                )
        self._resolve_conventional_root()

    def _resolve_conventional_root(self) -> None:
        """The ADK convention: the ``root_agent`` that ``adk run``/``adk web``
        discover is the *module* symbol. A function-local of the same name is
        a local variable and carries no such meaning."""
        module_level = [
            write
            for write in self.writes.get(ROOT_AGENT_SYMBOL, [])
            if write.scope == _MODULE_SCOPE
        ]
        if not module_level:
            return
        if self.star_import:
            self.unresolved_root = (
                f"a wildcard import can rebind `{ROOT_AGENT_SYMBOL}`, so which "
                "agent it holds is not provable statically"
            )
            return
        if any(write.conditional for write in module_level):
            self.unresolved_root = (
                f"`{ROOT_AGENT_SYMBOL}` is assigned conditionally, so which "
                "agent it holds is not provable statically"
            )
            return
        last = max(module_level, key=lambda write: write.lineno)
        if last.call_id is None:
            self.unresolved_root = (
                f"`{ROOT_AGENT_SYMBOL}` is last assigned a value that is not a "
                "statically readable agent construction"
            )
            return
        self.resolved_root_calls.setdefault(
            last.call_id,
            f"assigned to the conventional `{ROOT_AGENT_SYMBOL}` module symbol",
        )

    def _reaching(
        self, name: str, scope: int, lineno: int
    ) -> tuple[_Write | None, str]:
        """The binding a reference to ``name`` sees, or why it is unprovable.

        Delegates to :func:`_reaching_write`, the single implementation of
        that rule; the only thing owned here is the wildcard-import guard,
        which makes every lookup in such a file unprovable outright.
        """
        if self.star_import:
            return None, (
                f"a wildcard import can rebind `{name}`, so its value here is "
                "not provable statically"
            )
        chain = _scope_lookup_chain(self.scope_parents, self.class_scopes, scope)
        return _reaching_write(self.writes, chain, name, lineno)

    def role_for(self, call: ast.Call) -> tuple[AgentNameRole, str]:
        """Return ``(role, evidence)`` for one agent construction."""
        if id(call) in self.root_calls:
            return "root_agent", f"constructed inline as App({ROOT_AGENT_SYMBOL}=…)"
        root_evidence = self.resolved_root_calls.get(id(call))
        if root_evidence is not None:
            return "root_agent", root_evidence
        if id(call) in self.child_calls:
            return "sub_agent", "constructed inline inside another agent's children"
        child_evidence = self.resolved_child_calls.get(id(call))
        if child_evidence is not None:
            return "sub_agent", child_evidence
        return "agent", ""


def _sequence_elements(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return list(node.elts)
    return []


def _agent_name_evidence(
    call: ast.Call, scope: int, hierarchy: _AgentHierarchy, rel_path: str
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
            scope=scope,
            literal=literal.strip(),
            root_evidence=root_evidence,
        )
    if isinstance(value, ast.Name):
        return _AgentNameEvidence(
            role=role,
            rel_path=rel_path,
            scope=scope,
            symbol=value.id,
            root_evidence=root_evidence,
        )
    return None


def _is_stdlib_env_lookup(callee: str, facts: _PyFacts) -> bool:
    """Whether ``callee`` provably names ``os.getenv``/``os.environ.get``.

    Matching the spelling alone is not proof of provenance: a module that
    defines its own ``getenv(key, fallback)`` returning something else would
    have its fallback lifted out as the agent identity. The binding has to
    be an unshadowed import of the stdlib module.
    """
    parts = callee.split(".")
    head = parts[0]
    if facts.binding_count(head) != 1:
        return False
    # `os.getenv = fake` / `os.environ = {}` replaces the lookup without
    # rebinding any name, so the default below is no longer the value the
    # call returns.
    if _attribute_rebound(callee, facts) or facts.star_import:
        return False
    if facts.plain_imports.get(head) == "os":
        return parts[1:] in (["getenv"], ["environ", "get"])
    imported = facts.constant_imports.get((_MODULE_SCOPE, head))
    if imported is None:
        return False
    module, level, original = imported
    if module != "os" or level != 0:
        return False
    return (original == "getenv" and not parts[1:]) or (
        original == "environ" and parts[1:] == ["get"]
    )


def _module_constants(tree: ast.AST, facts: _PyFacts) -> dict[str, _Constant]:
    """Module-level ``NAME = <str>`` and ``NAME = os.environ.get(…, <str>)``.

    Module level only, these two forms only, and only for names bound
    exactly once in the whole file. The single-binding rule is what makes
    the other two safe: ``NAME = "Old"`` followed by any second write —
    later, conditional, computed, or inside a function — means the value
    Python passes is not the one visible here, so the name stays unresolved
    and the caller fails closed to a placeholder rather than partially
    evaluating user code.
    """
    constants: dict[str, _Constant] = {}
    if facts.star_import:
        # A wildcard import can rebind any of these after they are assigned.
        return constants
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
        constant = _static_string(value, facts)
        if constant is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and facts.binding_count(target.id) == 1:
                constants[target.id] = constant
    return constants


def _static_string(node: ast.expr, facts: _PyFacts) -> _Constant | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip():
        return _Constant(value=node.value.strip(), provenance="module_constant")
    if isinstance(node, ast.Call):
        callee = _call_name(node.func) or ""
        if _is_stdlib_env_lookup(callee, facts) and len(node.args) == 2:
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


def _collect_glob_hits(
    workspace: Path, *, files: list[Path] | None = None
) -> dict[str, list[_GlobHit]]:
    """Per-framework glob signals.

    Every hit here scores ``2.0`` ``strong``, so one hit on its own clears
    the detection threshold and puts its file in that framework's
    ``candidate_files``. That is what makes a single hit self-sufficient
    evidence, and why ``files`` — the inventory of one directory — answers
    "does this directory hold artifact evidence" with this exact rule rather
    than a lighter-weight copy of it.

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
    for path in _discover_patterns(workspace, files=files, patterns=ANTHROPIC_TOOL_PATTERNS):
        hits["anthropic"].append(
            _GlobHit(2.0, "strong", f"anthropic tool file: {path}", path)
        )
    for path in _discover_patterns(workspace, files=files, patterns=ANTHROPIC_POLICY_PATTERNS):
        hits["anthropic"].append(
            _GlobHit(2.0, "strong", f"anthropic policy file: {path}", path)
        )
    # openai-config.json is the OpenAI Messages API model-config marker —
    # belongs to openai_api, not the agents SDK (manifest.openai_api.model_config).
    for path in _discover_patterns(workspace, files=files, patterns=MODEL_CONFIG_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-config marker: {path}", path)
        )
    for path in _discover_patterns(workspace, files=files, patterns=OPENAI_TOOL_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai tool file: {path}", path)
        )
    for path in _discover_patterns(workspace, files=files, patterns=POLICY_RULE_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-api policy file: {path}", path)
        )
    for path in _discover_patterns(workspace, files=files, patterns=TEST_CASE_PATTERNS):
        hits["openai_api"].append(
            _GlobHit(2.0, "strong", f"openai-api test cases: {path}", path)
        )
    for path in _discover_patterns(workspace, files=files, patterns=N8N_WORKFLOW_PATTERNS):
        full_path = (workspace / path).resolve()
        if _looks_like_n8n_workflow(full_path):
            hits["n8n"].append(_GlobHit(2.0, "strong", f"n8n workflow: {path}", path))
    for path in _discover_patterns(workspace, files=files, patterns=CONDUCTOR_WORKFLOW_PATTERNS):
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


def _conventional_dir_locations(
    workspace: Path, *, files: Sequence[Path]
) -> dict[str, str]:
    """Where each conventional directory lives, as a workspace-relative path.

    Keyed by the *name* — ``prompts``, ``tools``, ``.agents-shipgate`` — so a
    repository with thirty ``tools/`` directories still contributes one weak
    signal for ``tools``, exactly as the root-only check did. The value is the
    shallowest occurrence, which is the one a reader would have looked for.

    Looking below the root is the whole point (#441): a Python distribution
    puts its tools inside the import package, so
    ``awslabs/billing_cost_management_mcp_server/tools/`` reported
    ``has_tools_dir: false`` and the one structural signal that repository
    offers went unread. The root is still checked separately because the
    inventory is a list of *files*: an empty ``prompts/`` has no entry in it,
    and it was a signal before this looked deeper.

    Bounded by the inventory walk, which already drops ``.git``, ``node_modules``,
    virtualenvs, and everything else in ``SKIP_DIRS``.

    Walks *directories*, not files, and on strings rather than ``Path``
    objects. The inventory is a file list on which many entries share a parent,
    and the obvious spelling — ``path.relative_to(workspace).parts`` per file —
    cost 4.4 seconds on a 120k-file monorepo, which is a whole-workspace scan
    ``detect`` already runs on exactly the repositories #363 and #395 are about.
    Deduplicating parents first makes the chain walk proportional to the number
    of distinct directories; ``str(path)`` is cached on ``PurePath``, so the
    prefix test is a slice comparison and not a second traversal.
    """

    located: dict[str, str] = {
        name: name for name in CONVENTIONAL_DIRS if (workspace / name).is_dir()
    }
    wanted = {name for name in CONVENTIONAL_DIRS if name not in located}
    if not wanted:
        return located
    # ``rstrip`` so a workspace that is already a filesystem or drive root —
    # ``/`` renders as ``"/"``, ``C:\\`` as ``"C:\\"`` — does not produce a
    # doubled separator that matches no path under it.
    prefix = f"{str(workspace).rstrip(os.sep)}{os.sep}"
    prefix_length = len(prefix)
    seen_directories: set[str] = set()
    shallowest: dict[str, tuple[int, str]] = {}
    for path in files:
        text = str(path)
        if not text.startswith(prefix):
            # Not under the workspace at all — a resolved symlink pointing out
            # of the tree. `relative_to` raised for these; this skips them.
            continue
        cut = text.rfind(os.sep)
        if cut < prefix_length:
            # A file directly in the workspace root has no parent directory
            # inside it, so nothing here can be a conventional dir.
            continue
        directory = text[prefix_length:cut]
        if directory in seen_directories:
            continue
        seen_directories.add(directory)
        parts = directory.split(os.sep)
        for depth, part in enumerate(parts):
            if part not in wanted:
                continue
            candidate = (depth, "/".join(parts[: depth + 1]))
            current = shallowest.get(part)
            if current is None or candidate < current:
                shallowest[part] = candidate
    located.update({name: rel for name, (_, rel) in shallowest.items()})
    return located


def _collect_dir_hits(locations: dict[str, str]) -> dict[str, list[str]]:
    present = [locations[d] for d in CONVENTIONAL_DIRS if d in locations]
    if not present:
        return {framework: [] for framework in CONVENTIONAL_DIR_FRAMEWORKS}
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
    return (
        stem in TEST_MODULE_NAMES
        or stem.startswith("test_")
        or stem.endswith("_test.py")
    )


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
    a constant that is itself a name stays unresolved — the target module
    must be a file already parsed inside the workspace, so no path outside
    the walk is ever read, and every rule below fails to ``None`` rather than
    guessing.
    """
    if evidence.literal is not None:
        return evidence.literal, "literal", ""
    symbol = evidence.symbol
    if not symbol:
        return None
    # A name bound more than once anywhere in the file is not resolvable
    # here: the binding this site sees may not be the one we can read.
    if fact.binding_count(symbol) != 1:
        return None
    local = fact.module_constants.get(symbol)
    if local is not None:
        return local.value, local.provenance, fact.rel_path
    # Imports are matched in the reference's own scope first, then the
    # module scope it falls through to — a helper's local import never
    # stands in for the one a module-level construction reads.
    imported = fact.constant_imports.get(
        (evidence.scope, symbol)
    ) or fact.constant_imports.get((_MODULE_SCOPE, symbol))
    if imported is None:
        return None
    module, level, original = imported
    found: list[tuple[str, str, str]] = []
    for candidate_path in _constant_module_paths(fact.path, module, level, workspace):
        target = by_path.get(candidate_path)
        if target is None:
            continue
        constant = target.module_constants.get(original)
        if constant is not None:
            found.append((constant.value, constant.provenance, target.rel_path))
    if not found:
        return None
    # More than one supported execution root resolves this import, and they
    # disagree. Which one Python picks depends on sys.path, which is not
    # ours to assume — so the identity stays unresolved.
    if len({value for value, _, _ in found}) > 1:
        return None
    return found[0]


def _constant_module_paths(
    importer: Path, module: str, level: int, workspace: Path
) -> list[Path]:
    """Files ``from <module> import <symbol>`` could refer to, workspace-only.

    Two spellings reach the adjacent config module that agent packages use:
    the relative ``from .config import AGENT_NAME`` and the absolute
    ``from config import AGENT_NAME`` that works because the framework puts
    the agent directory on ``sys.path``. Both resolve to a sibling file.

    All plausible targets are returned rather than the first hit, because
    the caller has to see a disagreement to fail closed on it. Packages come
    before modules, matching how Python's own finder orders them.
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
        suffixes: list[tuple[str, ...]] = [(*parts, "__init__.py")]
        if parts:
            suffixes.append((*parts[:-1], f"{parts[-1]}.py"))
        for suffix in suffixes:
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

    One rule overrides all four: if a workspace declares an application root
    whose identity cannot be established statically, *nothing* is
    selectable. Any name still standing is by construction not the root, so
    writing it would declare a worker as the reviewed identity — the exact
    failure #324 asks to fail closed on.

    Scores are published so a reordering regression is visible in
    ``detect --json`` instead of silently changing what the manifest claims.
    """
    by_path = {_safe_resolve(fact.path): fact for fact in facts}
    unresolved_roots = [fact.unresolved_root for fact in facts if fact.unresolved_root]
    # A root whose *name* is a symbol that fails the cross-module resolution
    # below is just as unresolved as one whose name is an f-string. The
    # failure surfaces here rather than at parse time because resolution
    # needs every file's constants, so it has to be folded in before ranking.
    for fact in facts:
        for evidence in fact.agent_names:
            if evidence.role != "root_agent" or evidence.literal is not None:
                continue
            if _resolve_agent_name_evidence(evidence, fact, by_path, workspace) is None:
                unresolved_roots.append(
                    f"{evidence.rel_path}: the application root's name comes from "
                    f"`{evidence.symbol}`, which does not resolve to a static value"
                )
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

    if unresolved_roots:
        blocked = (
            "an application root is declared here but its name is not statically "
            f"resolvable ({unresolved_roots[0]}); any other name would declare a "
            "worker as the reviewed identity"
        )
        for ranked in best.values():
            ranked.selectable = False
            ranked.rationale.append(f"rejected: {blocked}")

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
        score += ROOT_AGENT_BONUS
        rationale.append(root_evidence or "bound as the application root")
    elif role == "sub_agent":
        score -= SUB_AGENT_PENALTY
        rationale.append(
            root_evidence or "declared as a child of another agent, not the root"
        )

    if _is_test_path(rel_path):
        # Deliberately larger than every other signal combined: a fixture
        # that happens to build an App root is still a fixture, and must not
        # outrank a plain agent the shipped code declares.
        score -= ORIGIN_TEST_PENALTY
        rationale.append("declared in test code, which names fixtures rather than the product")

    normalised = _normalise_name(value)
    if normalised and normalised in project_forms:
        score += CORROBORATION_BONUS
        rationale.append("corroborated by the project name")

    selectable = True
    if len(normalised) < AGENT_NAME_MIN_LENGTH:
        score -= QUALITY_FLOOR_PENALTY
        selectable = False
        rationale.append(
            f"rejected: fewer than {AGENT_NAME_MIN_LENGTH} significant characters, "
            "too context-poor to assert as an identity"
        )
    elif normalised in GENERIC_AGENT_NAME_VALUES:
        score -= QUALITY_FLOOR_PENALTY
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

def _agent_scope(
    candidates: list[AgentProjectCandidate],
    *,
    parse_truncated: bool,
    project_roots: int,
) -> tuple[str, bool]:
    """Whether one manifest can describe this workspace, and how far the
    walk behind that answer got.

    Python parsing stops at ``max_python_files``, so on a large repository
    the evidence behind the verdict may simply be the part of the tree that
    got read first — and filesystem ordering is not a safety property. When
    the parse was cut short *and* the workspace holds more than one project
    root, a project could be sitting in the unread remainder. That is the
    ``truncated`` half of the answer, and it is computed first because it
    is true regardless of how many candidates were found.

    Truncation alone is not enough to say it: a repository with one project
    root has nowhere for a second project to hide, however many files it
    holds, so large single-project repositories keep their ``"single"``
    verdict and their working ``init``.

    The two halves are independent, and folding them into one value hid the
    honest one. ``"ambiguous"`` short-circuited, so ``"unknown"`` — the
    state whose entire purpose is to say the parse was cut short — was
    reachable only when one or fewer candidates were found, and the cap
    warning went unprinted on exactly the repositories the cap had cut
    (#395). Two candidates found *is* an ambiguous scope whatever the cap
    did; what truncation changes is that the candidate list is a lower
    bound rather than an enumeration, which is what ``truncated`` says.
    """

    truncated = parse_truncated and project_roots > 1
    if len(candidates) > 1:
        return "ambiguous", truncated
    if truncated:
        return "unknown", True
    return "single", False


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
    """How many scopes the workspace could hold, counted from the walk.

    Filename matching over the inventory the walk already produced — no
    parsing, no cap — so this stays trustworthy exactly where the AST pass
    stops being trustworthy.

    Weak markers are counted even though they need evidence to draw a
    boundary: this number only decides whether *another* project could be
    hiding in the part of the tree the parse never reached, so counting one
    that turns out not to be a project fails closed.

    The workspace itself is always counted, marker or not.
    :func:`_agent_project_candidates` attributes agent evidence that sits
    under no marker to the workspace as ``"."``, so an unmarked root is a
    candidate scope like any other — and counting only marker directories
    missed exactly that one. A repository with one marked sub-project and an
    unmarked agent at its root past the cap censused a single root, kept its
    ``"single"`` verdict with no truncation warning, and let ``init --write``
    write a root manifest carrying the sub-project's agent name while
    silently covering the root agent nobody had read (#399 review).
    """

    markers = (*PROJECT_MARKERS, *WEAK_PROJECT_MARKERS)
    roots = {path.parent for path in inventory if path.name in markers}
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
        fact.path for fact in facts if fact.name_literals() and fact.framework
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
        literals = fact.name_literals()
        if literals and fact.framework:
            names[_project_of(fact.path)].update(literals)

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


#: Filename patterns that could make a file agent evidence for the directory
#: it sits in — every family :func:`_collect_glob_hits` and
#: :func:`_suggested_sources` read. Used to decide whether a *deleted* path
#: could have been the evidence a weak marker rested on, which is a question
#: only its name can answer: the file is gone, so nothing can be parsed.
_EVIDENCE_FILE_PATTERNS: tuple[str, ...] = (
    *ANTHROPIC_TOOL_PATTERNS,
    *ANTHROPIC_POLICY_PATTERNS,
    *MODEL_CONFIG_PATTERNS,
    *OPENAI_TOOL_PATTERNS,
    *POLICY_RULE_PATTERNS,
    *TEST_CASE_PATTERNS,
    *N8N_WORKFLOW_PATTERNS,
    *CONDUCTOR_WORKFLOW_PATTERNS,
    *OPENAPI_PATTERNS,
    *MCP_PATTERNS,
)


@dataclass(frozen=True)
class UndeterminedDirectory:
    """A weak-marker directory the probe could not settle, and why.

    ``cause`` is what the routing surface reads. Collapsing all three into
    one "unresolved" loses the only thing that decides what to do next: a
    budget the caller can raise is a mechanical retry, while evidence this
    change deleted is a question the head tree cannot answer at all, and
    handing the second one a command that reruns the first is a recovery
    that returns the same wrong answer (#399 review).
    """

    #: Workspace-relative POSIX path.
    path: str
    cause: Literal["parse_budget", "deleted_evidence", "unreadable_inventory"]
    reason: str


@dataclass(frozen=True)
class WeakMarkerEvidence:
    """What the preview path could establish about weak project markers.

    ``undetermined`` is the load-bearing half. A probe that cannot settle a
    directory has not shown there is no project there, and reporting that as
    "no evidence" is how a scope resolver ends up naming the workspace root
    for a change it could not attribute (#399 review).
    """

    #: Directories whose weak marker is backed by agent evidence.
    directories: frozenset[Path] = frozenset()
    #: Directories the probe could not settle, sorted by path.
    undetermined: tuple[UndeterminedDirectory, ...] = ()
    #: ``.py`` files in the workspace inventory, uncapped — the cap that
    #: reaches every one of them, for a retry command to name. ``0`` when no
    #: inventory was read.
    python_file_total: int = 0

    @property
    def causes(self) -> frozenset[str]:
        return frozenset(entry.cause for entry in self.undetermined)

    @property
    def detail(self) -> str:
        """One line naming what was unsettled, for a routing surface to quote."""

        first = self.undetermined[0]
        more = (
            f" (and {len(self.undetermined) - 1} more)"
            if len(self.undetermined) > 1
            else ""
        )
        return (
            f"whether {first.path} is a self-contained project could not be "
            f"established: {first.reason}{more}"
        )


def weak_marker_evidence_dirs(
    root: Path,
    changed_files: Iterable[str],
    *,
    max_python_files: int = DEFAULT_MAX_PYTHON_FILES,
) -> WeakMarkerEvidence:
    """Which weak project markers above the changed paths are real projects.

    :func:`find_project_root` unlocks
    :data:`~agents_shipgate.cli.discovery.scope.WEAK_PROJECT_MARKERS` for
    exactly the directories the caller has already found agent evidence in,
    and ``detect`` establishes that with a whole-workspace walk — seconds on
    a large monorepo, which the ``verify --preview`` routing path cannot
    spend. Passing nothing instead is not a cheaper approximation, it is a
    different answer: a project whose only boundary is ``requirements.txt``
    beside ``agent.py`` disappears, the walk climbs to the repository root,
    and preview emits a root ``init`` that ``init`` then refuses (#394).

    So answer the same question, but only where the answer can change
    anything. A directory that already carries a strong marker is a project
    root without any evidence, and one that carries no weak marker cannot
    become one because of it; both are skipped, which on most repositories
    leaves nothing to probe and costs a handful of ``stat`` calls. For the
    rest, the evidence is what ``detect`` would find *directly in* that
    directory — its own ``evidence_dirs`` are the immediate parents of the
    evidence files it found, so this asks the same question of the same
    files, through every rule that can put a file in ``evidence_paths``:

    * :func:`_score_python_signals` for framework-attributed Python;
    * :func:`_collect_glob_hits` for the artifact-glob frameworks
      (Anthropic, OpenAI API, n8n, Conductor) — every hit there scores
      ``2.0`` ``strong``, so one hit clears the detection threshold on its
      own and its file is in that framework's ``candidate_files``;
    * :func:`_suggested_sources` for OpenAPI/MCP/Conductor sources;
    * :func:`_codex_plugin_candidates` for plugin packages and marketplaces.

    Reading a subset was not a smaller version of the same answer: a
    ``requirements.txt`` beside an ``openai-config.json`` is a project full
    discovery reports and a subset does not, and preview then recommends the
    root ``init`` that the canonical result refuses (#399 review).

    The inventory comes from :func:`_candidate_files`, the git-aware walk
    ``detect`` reads, so an ignored or untracked-ignored file cannot make
    preview narrow to a directory ``detect`` never saw — routing must not
    depend on local ignore state, least of all in a way that disagrees with
    the command it recommends.

    Three things leave a directory *undetermined* rather than negative, and
    each is returned with its cause so the caller can route to a recovery
    that actually advances it:

    * ``parse_budget`` — the shared ``max_python_files`` budget, the same one
      ``detect`` spends, ran out with a ``.py`` file still unread;
    * ``unreadable_inventory`` — the workspace inventory could not be read;
    * ``deleted_evidence`` — the only file that could have been the evidence
      is a changed path this pull request deletes. The head tree cannot
      answer for a project whose evidence the diff removed, and which
      families count is decided by name, because there is nothing left to
      parse.

    The repository root is never reported undetermined: a claim on it
    resolves to ``not_narrowed`` either way, so it can only add noise.
    """

    try:
        root_resolved = root.resolve()
    except OSError:  # pragma: no cover - unreadable workspace
        return WeakMarkerEvidence()

    # Every directory at or above a changed path, bounded by the root, built
    # the way `resolve_change_scope` builds them so the two sets compare.
    candidates: set[Path] = set()
    changed: list[PurePosixPath] = []
    for entry in changed_files:
        if not entry:
            continue
        path = PurePosixPath(entry)
        if path.is_absolute() or ".." in path.parts:
            continue
        changed.append(path)
        current = root_resolved.joinpath(*path.parts[:-1])
        while current not in candidates:
            candidates.add(current)
            if current == root_resolved:
                break
            parent = current.parent
            if parent == current:  # pragma: no cover - defensive
                break
            current = parent

    # Boundaries the change *removes*, computed before anything reads the head
    # tree — because the head tree is where they are missing. A directory whose
    # project marker this pull request deletes is not a directory that was
    # never a project, and it never reaches the marker filter below (#399
    # review).
    undetermined: list[UndeterminedDirectory] = list(
        _removed_boundaries(root_resolved, changed)
    )
    removed_dirs = {entry.path for entry in undetermined}

    # `project_marker` reports strong markers ahead of weak ones, so one call
    # separates all three cases: no marker (nothing to unlock), a strong
    # marker (already a project root, and evidence cannot change that), and a
    # weak marker (the only case worth reading files for).
    probe = sorted(
        directory
        for directory in candidates
        # `_skip_part` is the rule `_skip` applies; applied to the relative
        # parts directly, because every candidate was built by joining them
        # onto the resolved root and re-resolving each one to rediscover that
        # is two syscalls per directory on the hot path.
        if not any(
            _skip_part(part) for part in directory.relative_to(root_resolved).parts
        )
        and project_marker(directory, extra=WEAK_PROJECT_MARKERS)
        in WEAK_PROJECT_MARKERS
    )
    if not probe:
        # The common case, and the reason nothing above this line reads a
        # file beyond a `stat`: no weak marker means no inventory and no parse.
        return WeakMarkerEvidence(undetermined=_sorted(undetermined))

    try:
        inventory = _candidate_files(root_resolved)
    except (DiscoveryError, OSError):
        # Fail closed. Without the inventory every candidate is unsettled,
        # not settled negative.
        undetermined.extend(
            UndeterminedDirectory(
                path=_relative(directory, root_resolved),
                cause="unreadable_inventory",
                reason="the workspace inventory could not be read",
            )
            for directory in probe
            if directory != root_resolved
        )
        return WeakMarkerEvidence(undetermined=_sorted(undetermined))

    by_directory: dict[Path, list[Path]] = {}
    for path in inventory:
        by_directory.setdefault(path.parent, []).append(path)
    plugin_dirs = _codex_plugin_evidence_dirs(root_resolved, inventory)
    python_file_total = sum(1 for path in inventory if path.suffix == ".py")

    found: set[Path] = set()
    budget = max_python_files
    for directory in probe:
        held = by_directory.get(directory, [])
        relative = _relative(directory, root_resolved)
        if (
            directory in plugin_dirs
            or any(_collect_glob_hits(root_resolved, files=held).values())
            or _suggested_sources(root_resolved, files=held)[0]
        ):
            found.add(directory)
            continue
        # Only the Python files a `detect` *of this directory* would reach.
        # The command preview recommends is scoped to it, and that command
        # spends the cap over the directory's whole subtree in inventory
        # order — so a direct `agent.py` sorting after a thousand inert
        # modules is evidence this probe can see and the command it names
        # cannot (#399 review).
        readable, beyond_cap = _within_parse_budget(
            directory, held, inventory, max_python_files
        )
        agent_python, budget, unread = _holds_agent_python(
            readable, root_resolved, budget=budget
        )
        if agent_python:
            found.add(directory)
            continue
        if directory == root_resolved:
            continue
        # Causes accumulate. A budget that ran out does not answer a deletion,
        # and reporting only the first one routed a deleted-evidence case to a
        # retry that cannot see what was deleted (#399 review).
        if unread or beyond_cap:
            undetermined.append(
                UndeterminedDirectory(
                    path=relative,
                    cause="parse_budget",
                    reason=(
                        f"a {max_python_files}-file parse does not reach every "
                        "Python file that could be its evidence"
                    ),
                )
            )
        if relative not in removed_dirs and _deletes_possible_evidence(
            directory, root_resolved, changed
        ):
            undetermined.append(
                UndeterminedDirectory(
                    path=relative,
                    cause="deleted_evidence",
                    reason=(
                        "this change deletes the file beside its requirements "
                        "file that could have been the evidence, and the head "
                        "tree cannot say whether what it removed was this "
                        "project's agent surface"
                    ),
                )
            )
    return WeakMarkerEvidence(
        directories=frozenset(found),
        undetermined=_sorted(undetermined),
        python_file_total=python_file_total,
    )


def _sorted(
    entries: list[UndeterminedDirectory],
) -> tuple[UndeterminedDirectory, ...]:
    return tuple(sorted(entries, key=lambda entry: (entry.path, entry.cause)))


def _within_parse_budget(
    directory: Path, held: list[Path], inventory: list[Path], max_python_files: int
) -> tuple[list[Path], bool]:
    """The files in ``held`` a capped ``detect`` *of ``directory``* would read.

    Returns them, plus whether any direct Python file falls outside that
    bound. The inventory is already in the order discovery consumes it, so
    the bound is the first ``max_python_files`` Python paths under
    ``directory`` — exactly the set the recommended scoped command reaches.
    """

    reachable: set[Path] = set()
    seen = 0
    for path in inventory:
        if path.suffix != ".py":
            continue
        if path.parent != directory and directory not in path.parents:
            continue
        if seen >= max_python_files:
            break
        seen += 1
        reachable.add(path)
    readable = [path for path in held if path.suffix != ".py" or path in reachable]
    beyond = any(
        path.suffix == ".py" and path not in reachable for path in held
    )
    return readable, beyond


def _removed_boundaries(
    workspace: Path, changed: list[PurePosixPath]
) -> list[UndeterminedDirectory]:
    """Directories whose project boundary this change removes.

    Every other question here is asked of the head tree, which is the one
    place a removed boundary is guaranteed not to be. A pull request deleting
    ``services/gone/{pyproject.toml,agent.py}`` leaves nothing for the marker
    filter to find, so the directory is silently not a project and the change
    is attributed to whatever survives — on the reported repository, a root
    ``init`` for an unrelated agent (#399 review).

    A removed *strong* marker is a removed boundary outright: a
    ``pyproject.toml`` draws a project root with no evidence needed. A removed
    *weak* marker only drew one beside agent evidence, so it counts when the
    change also touches something in that directory that could have been the
    evidence — which keeps a deleted ``tests/requirements.txt`` from reading
    as a lost project.
    """

    removed: dict[str, UndeterminedDirectory] = {}
    gone = [path for path in changed if not (workspace / path).exists()]
    for path in gone:
        name = path.parts[-1]
        strong = name in PROJECT_MARKERS
        if not strong and name not in WEAK_PROJECT_MARKERS:
            continue
        directory = workspace.joinpath(*path.parts[:-1])
        if directory == workspace:
            continue
        relative = _relative(directory, workspace)
        if any(_skip_part(part) for part in PurePosixPath(relative).parts):
            continue
        if not strong and not any(
            _could_be_evidence(other, workspace)
            for other in gone
            if workspace.joinpath(*other.parts[:-1]) == directory
        ):
            continue
        removed[relative] = UndeterminedDirectory(
            path=relative,
            cause="deleted_evidence",
            reason=(
                f"this change removes {name} from it, so the boundary that "
                "made it a self-contained project is not in the tree being "
                "evaluated"
            ),
        )
    return list(removed.values())


def _could_be_evidence(path: PurePosixPath, workspace: Path) -> bool:
    """Whether a file at ``path`` could have been agent evidence, by name."""

    return path.parts[-1].endswith(".py") or any(
        _matches_pattern(workspace / path, workspace, pattern)
        for pattern in _EVIDENCE_FILE_PATTERNS
    )


def _deletes_possible_evidence(
    directory: Path, workspace: Path, changed: list[PurePosixPath]
) -> bool:
    """Whether the change removes a file that could have been ``directory``'s.

    Decided by name, because the file is gone: every family that can put a
    path in ``evidence_paths`` is a filename rule except the Python parse,
    and a ``.py`` file cannot be ruled out without reading it. A Codex plugin
    manifest counts for the *package* directory above its ``.codex-plugin``
    folder, which is the directory the candidate names.
    """

    plugin_manifest = PurePosixPath(_relative(directory, workspace)) / ".codex-plugin"
    for path in changed:
        parent = workspace.joinpath(*path.parts[:-1])
        if parent == directory:
            if _could_be_evidence(path, workspace) and not (workspace / path).exists():
                return True
        elif (
            path.parts[-1] == "plugin.json"
            and PurePosixPath(*path.parts[:-1]) == plugin_manifest
            and not (workspace / path).exists()
        ):
            return True
    return False


def _codex_plugin_evidence_dirs(
    workspace: Path, inventory: list[Path]
) -> frozenset[Path]:
    """Directories a Codex plugin candidate would put in ``evidence_dirs``.

    Read off :func:`_codex_plugin_candidates` rather than off the filenames
    again, so marketplace-covered packages are deduplicated here exactly as
    they are there. A package candidate names a directory; a marketplace
    candidate names a file, whose directory is what ``detect`` records.
    """

    dirs: set[Path] = set()
    for candidate in _codex_plugin_candidates(workspace, inventory):
        path = workspace if candidate.path == "." else workspace / candidate.path
        dirs.add(path if candidate.mode == "package" else path.parent)
    return frozenset(dirs)


def _holds_agent_python(
    files: list[Path], workspace: Path, *, budget: int
) -> tuple[bool, int, bool]:
    """Whether one of ``files`` is a framework-attributed agent file.

    ``files`` are the inventory entries of a single directory, which is the
    point: ``detect`` derives an evidence directory from an evidence file's
    own parent, so a framework import two levels down names that
    sub-directory, not this one.

    Returns ``(found, remaining budget, stopped with a file unread)``. The
    third value is reported rather than inferred from a zero remainder: a
    directory holding exactly ``max_python_files`` benign modules spends the
    last unit on its last file and was read completely, which is the same
    thing ``detect`` would say about it (#399 review).
    """

    for path in files:
        if path.suffix != ".py":
            continue
        if budget <= 0:
            return False, 0, True
        budget -= 1
        fact = _parse_python_facts(path, workspace)
        if fact is None:
            continue
        _score_python_signals(fact, _initial_framework_scores())
        if fact.framework:
            return True, budget, False
    return False, budget, False


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
    workspace: Path, *, files: list[Path] | None = None
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

    ``files`` narrows the inventory this reads. Passing the walk's own
    inventory keeps one pass from re-running the git walk once per pattern;
    passing the files of a single directory asks this exact rule — the same
    globs, the same ``.mcp.json`` skip, the same adapter probe — about that
    directory, which is what the preview path needs to decide whether a
    directory holds artifact evidence without a second copy of the rule.
    """
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern in OPENAPI_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,), files=files):
            rel = _relative(path, workspace)
            if ("openapi", rel) in seen:
                continue
            seen.add(("openapi", rel))
            candidates.append(("openapi", rel))
    for pattern in MCP_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,), files=files):
            if path.name == ".mcp.json":
                continue
            rel = _relative(path, workspace)
            if ("mcp", rel) in seen:
                continue
            seen.add(("mcp", rel))
            candidates.append(("mcp", rel))
    for pattern in CONDUCTOR_WORKFLOW_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,), files=files):
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
