#!/usr/bin/env python3
"""Zero-install Agents Shipgate detector.

Replicates the structural output of ``agents-shipgate detect --json`` for
the most common decision a coding agent needs to make — *is this an agent
project, and which framework(s)?* — without requiring a local install of
the ``agents-shipgate`` package. Stdlib-only, one file.

Usage::

    python3 tools/shipgate-detect.py [--workspace PATH] [--json]
    curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/shipgate-detect.py \\
        | python3 - --workspace . --json

Output mirrors :class:`agents_shipgate.cli.discovery.signals.DetectResult`
plus a ``script_version`` field. It is a **structural subset** of the
canonical ``agents-shipgate detect --json`` output, NOT a drop-in
replacement: the CLI also emits ``diagnostics[]`` and ``next_actions[]``
arrays (the diagnostic engine), which are intentionally out of scope for
the zero-install path. The contract test pins the verdict — ``is_agent_project``,
fired frameworks, suggested sources, excluded sources — against the CLI on
every sample in ``samples/``, so the two cannot drift on the load-bearing
fields.

Both this script and the canonical CLI silently skip common fixture corpus
directories (for example ``fixtures/``, ``testdata/``, and ``golden/``) when
those directories are below the selected workspace. Point ``--workspace``
directly at a fixture project to detect that fixture itself.

The workspace inventory matches the canonical CLI's: ``git ls-files`` when
the workspace is a repository Git can read, a contained filesystem walk
otherwise. That is not a performance detail — a ``.gitignore``d module is
invisible to ``init``, so a script that walked it anyway could name an agent
``init`` will never write. Paths that escape the workspace through a symlink
are dropped for the same reason. The bound is on Python *parses*
(``MAX_PYTHON_FILES``), never on the inventory, so an asset-heavy repository
cannot exhaust the budget before the walk reaches any source.

Like the canonical CLI, glob-matched MCP/OpenAPI candidates are
parse-probed before they are suggested: a filename is a glob match, not a
guarantee. A Cursor plugin ``mcp.json`` is an ``mcpServers``-style host
config, not an MCP tools-array export — suggesting it would make the very
next ``agents-shipgate init --write`` → ``scan`` step fail. Rejected
candidates move to ``excluded_sources[]`` (``{type, path, reason}``)
instead of ``suggested_sources``.

Intentional simplifications vs. the canonical CLI:

- No ``diagnostics[]`` / ``next_actions[]`` (the diagnostic engine is
  not in scope for stdlib-only / zero-install).
- ``agent_scope`` / ``agent_scope_truncated`` / ``python_parse_truncated`` /
  ``agent_project_candidates[]``
  are carried, and the contract test pins them against the CLI: an agent that
  consults the zero-install path must not adopt a manifest scope the CLI
  refuses, nor read a candidate list the cap cut short as an enumeration.
- Descriptive (not byte-identical) ``evidence`` / ``reason`` strings.
- Absolute scores may differ by ±0.5 in edge cases.
- The parse probe is **JSON-only** (stdlib has no YAML parser). A
  ``.json`` candidate the input adapters would reject is excluded here
  too; a ``.yaml`` / ``.yml`` OpenAPI spec is kept as a suggestion unless
  it trips the size bound below (never wrongly dropped). The real-world
  miss this guards against — ``mcpServers``-style host configs — is always
  JSON, so the probe is exact where it matters.
- Every **suggestion candidate** — the MCP/OpenAPI/Conductor glob hits — and
  every n8n/Conductor workflow read for framework scoring is size-gated at
  ``MAX_STRUCTURED_FILE_BYTES`` before it is read, matching the
  ``MAX_INPUT_FILE_BYTES`` refusal the input adapters apply ahead of their own
  parse. This is both a bound on what an unknown workspace can make this script
  allocate and a parity rule: an oversized ``*mcp*.json`` is excluded by the
  CLI, so suggesting it here would send an agent to write a manifest entry
  ``scan`` rejects. The MCP registration-site walk applies its own, smaller
  ``MAX_SOURCE_FILE_BYTES``. Reads that feed neither surface are *not* covered:
  ``package.json`` / ``go.mod`` language evidence and the ``pyproject.toml`` /
  ``requirements.txt`` package tokens are still read whole, exactly as the CLI
  reads them, so the two stay in step — do not bound one without the other.

``mcp_server_source`` — an MCP server whose tool surface exists only as
TypeScript or Go registration sites (#431) — **is** detected here, through a
port of the CLI's masking reader and its idiom registry (#485). That port is a
second implementation of a load-bearing matcher, so it is held to the CLI's
answers by a shared conformance corpus rather than by inspection: every
positive sample, the whole adversarial sweep, the path predicate and both
escape grammars in ``tests/mcp_idiom_corpus.py`` are driven through both
readers and compared site by site, span included.

The verdict, detected framework set, suggested/excluded source split, and
the ranked ``agent_name_candidates`` all match. The name ranking is pinned
rather than simplified: it decides which agent a manifest declares as the
reviewed identity, and a script that ranked differently from ``init`` would
send an agent to fix the wrong one.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT_VERSION = "0.6.0"
MAX_STRUCTURED_FILE_BYTES = 10 * 1024 * 1024
# Matches ``detect_workspace``'s ``max_python_files``. The bound is on
# parses, not on the inventory: capping the inventory lets an asset-heavy
# repository exhaust the budget before the walk reaches any source.
MAX_PYTHON_FILES = 1000

# Framework signal vocabulary (mirror of cli/discovery/signals.py).
LANGCHAIN_IMPORTS = {
    "langchain", "langchain.agents", "langchain.tools", "langchain_core",
    "langchain_core.tools", "langchain_core.agents", "langgraph",
    "langgraph.graph", "langgraph.prebuilt",
}
LANGCHAIN_DECORATOR_MODULES = {"langchain.tools", "langchain_core.tools"}
LANGCHAIN_AGENT_CALLS = {"create_agent", "create_react_agent", "AgentExecutor"}
CREWAI_IMPORTS = {"crewai", "crewai.tools", "crewai_tools"}
CREWAI_DECORATOR_MODULES = {"crewai.tools"}
CREWAI_CLASSES = {"Agent", "Crew", "Task"}
GOOGLE_ADK_CLASSES = {
    "Agent", "LlmAgent", "FunctionTool", "LongRunningFunctionTool",
    "OpenAPIToolset", "McpToolset", "MCPToolset",
}
ANTHROPIC_IMPORTS = {"anthropic"}
OPENAI_AGENTS_SDK_IMPORTS = {"agents", "openai_agents"}
OPENAI_AGENTS_SDK_DECORATORS = {
    "function_tool", "agents.function_tool", "openai_agents.function_tool",
}
PACKAGE_HINTS: dict[str, tuple[str, ...]] = {
    "langchain": ("langchain", "langchain-core", "langchain_core", "langgraph"),
    "crewai": ("crewai", "crewai-tools"),
    "google_adk": ("google-adk", "google_adk", "google-genai"),
    "anthropic": ("anthropic",),
    "openai_agents_sdk": ("openai-agents", "openai_agents", "agents"),
    "n8n": ("n8n", "@n8n/n8n-nodes-langchain"),
    "conductor": ("conductor-client", "conductor-server", "conductor-oss"),
    "openai_api": (),
}
#: The tool-source type the MCP registration-site route suggests, and the
#: framework key it scores under. Mirrors
#: ``agents_shipgate.inputs.mcp_server_source.SOURCE_TYPE``.
MCP_SOURCE_TYPE = "mcp_server_source"
FRAMEWORKS = (
    "langchain", "crewai", "google_adk", "anthropic",
    "openai_agents_sdk", "n8n", "conductor", "openai_api",
    # Not a Python framework and scored from neither the AST pass nor a
    # filename glob: it is the workspace's own TypeScript or Go registration
    # sites, scored by `_discover_mcp_server_source` (#431, ported by #485).
    MCP_SOURCE_TYPE,
)
#: The frameworks a conventional directory is weak evidence for. Deliberately
#: not every entry in ``FRAMEWORKS`` — mirrors
#: ``signals.CONVENTIONAL_DIR_FRAMEWORKS``, and pinned against it.
#:
#: ``mcp_server_source`` is the one absentee. Its evidence is already a
#: conjunction — a declared MCP dependency *and* a tool name resolved at a
#: registration site — so a ``tools/`` directory adds nothing it does not
#: already have, and adding it would let two conventional directories carry
#: the published confidence to ``high`` for a route the engine caps at
#: ``medium``.
CONVENTIONAL_DIR_FRAMEWORKS = (
    "langchain", "crewai", "google_adk", "anthropic",
    "openai_agents_sdk", "n8n", "conductor", "openai_api",
)
OPENAPI_PATTERNS = (
    "*openapi*.yaml", "*openapi*.yml", "*openapi*.json",
    "*swagger*.yaml", "*swagger*.yml", "*swagger*.json",
)
MCP_PATTERNS = ("*mcp*.json", ".agents-shipgate/*.json")
ANTHROPIC_TOOL_PATTERNS = ("tools/*anthropic*tools*.json", "tools/anthropic-tools.json")
ANTHROPIC_POLICY_PATTERNS = ("policies/*anthropic*.yaml", "policies/anthropic-policy.yaml")
N8N_WORKFLOW_PATTERNS = (
    "workflows/*.json", "workflows/**/*.json",
    "n8n/*.json", "n8n/**/*.json",
    "*workflow*.json",
)
CONDUCTOR_WORKFLOW_PATTERNS = (
    "workflows/*.json", "workflows/**/*.json",
    "conductor/*.json", "conductor/**/*.json",
    "ai/examples/*.json", "ai/examples/**/*.json",
    "*workflow*.json",
)
OPENAI_API_PATTERNS = (
    ("openai-config.json", "openai-config marker"),
    ("tools/*openai*tools*.json", "openai tool file"),
    ("policies/*openai*.yaml", "openai-api policy file"),
    ("policies/*api*.yaml", "openai-api policy file"),
    ("tests/*openai*cases*.json", "openai-api test cases"),
    ("tests/*api*cases*.json", "openai-api test cases"),
)
CONVENTIONAL_DIRS = ("prompts", "tools", ".agents-shipgate")
# Agent-name evidence vocabulary (mirror of cli/discovery/signals.py). The
# ranking below is pinned to the CLI's by tests/test_zero_install_detector.py:
# two rankings that disagree would have `init` and this script name different
# agents as the reviewed identity.
AGENT_NAME_CLASSES = {"Agent", "LlmAgent"}
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
CHILD_AGENT_KEYWORDS = ("sub_agents", "handoffs")
# Origin is meant to dominate hierarchy and corroboration, so the
# non-product penalty is strictly greater than their whole spread
# (3.0 + 1.0 + 1.5).
# Conventional test module filenames that carry no `test_` prefix.
TEST_MODULE_NAMES = frozenset({"conftest.py", "test.py", "tests.py"})
# Consecutive directory names that hold code shipped as material rather than
# as the running application: a `templates/` directory nested in a
# `resources/` one holds what a generator copies. The pair is required, not a
# bare `templates/` — every name added here widens what fails open (#398).
# Entries must be lowercase: the path is case-folded before the comparison.
NON_PRODUCT_DIR_SEQUENCES: tuple[tuple[str, ...], ...] = (("resources", "templates"),)
ROOT_AGENT_BONUS = 3.0
SUB_AGENT_PENALTY = 1.5
CORROBORATION_BONUS = 1.0
ORIGIN_NON_PRODUCT_PENALTY = 6.0
QUALITY_FLOOR_PENALTY = 3.0
AGENT_NAME_MIN_LENGTH = 3
GENERIC_AGENT_NAME_VALUES = frozenset({
    "agent", "agents", "bar", "baz", "changeme", "dummy", "example", "foo",
    "myagent", "name", "placeholder", "qux", "sample", "temp", "test",
    "tests", "tmp", "todo", "untitled",
})

# Files that mark a self-contained project root. Mirrors
# ``agents_shipgate.cli.discovery.scope.PROJECT_MARKERS`` — the canonical CLI
# and this script must agree on which directory a manifest describes, or an
# agent that consults the zero-install path adopts a scope the CLI refuses.
PROJECT_MARKERS = (
    "shipgate.yaml",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "Gemfile",
)

# Markers that name a project only where agent evidence sits in the same
# directory. Mirrors ``scope.WEAK_PROJECT_MARKERS``.
WEAK_PROJECT_MARKERS = (
    "requirements.txt",
    "requirements.in",
)

SKIP_DIRS = {
    ".agents-private", ".cache", ".claude", ".direnv", ".git", ".hg",
    ".nox", ".svn", ".mypy_cache", ".next", ".pnpm-store", ".pytest_cache",
    ".ruff_cache", ".turbo", ".tox", ".venv", "__pycache__",
    "agents-shipgate-reports", "build", "dist", "env", "node_modules",
    "target", "venv", "fixtures", "_fixtures", "__fixtures__", "golden",
    "goldens", "test-fixtures", "test_fixtures", "test_data", "testdata",
}
PYPROJECT_NAME_RE = re.compile(r'^\s*name\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
REQ_TOKEN_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)", re.MULTILINE)


MAX_GIT_INVENTORY_BYTES = 16 * 1024 * 1024
# Ceiling for the non-Git fallback walk. Deliberately far above any real
# agent repository: it is a refusal threshold for pathological inputs, not
# a working limit, and exceeding it raises rather than truncating.
MAX_WALK_FILES = 200_000


def _contained(path: Path, workspace: Path) -> Path | None:
    """``path`` itself when it lives in the workspace, else ``None``.

    Resolution proves containment and nothing more. Returning the resolved
    path instead would *rename* the entry: with ``agent.py -> source.txt``
    both inventory entries collapse onto ``source.txt``, the ``.py`` suffix
    disappears, and the script reports zero Python files where canonical
    detection reports an agent project. A symlink pointing outside is still
    dropped — it is not part of the workspace whatever its name suggests,
    and ranking a name out of one also leaks the outside absolute path.
    """
    try:
        resolved = path.resolve()
        rel = resolved.relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        return None
    if any(p in SKIP_DIRS or p.startswith(".venv") for p in rel.parts):
        return None
    try:
        if not resolved.is_file():
            return None
    except OSError:
        return None
    return path


class DiscoveryError(RuntimeError):
    """The workspace inventory could not be collected safely.

    Mirrors ``core.errors.DiscoveryError``. Canonical discovery *raises*
    when the bounded Git inventory overruns rather than falling back to an
    unbounded walk, and so does this: the fallback would do the very work
    the bound exists to refuse.
    """


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_ATTR_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat",
        "GIT_PROTOCOL_FROM_USER": "0", "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git_inventory_bounded(workspace: Path, args: list[str], *,
                           env: dict[str, str],
                           max_output_bytes: int) -> bytes | None:
    """Read Git's output incrementally, never buffering more than the cap.

    ``capture_output=True`` would materialise the whole inventory before any
    size check could reject it, which makes the cap decorative. Reading in
    chunks and killing the child on overrun is what actually bounds memory.
    """
    try:
        process = subprocess.Popen(
            ["git", "--no-replace-objects", "-C", str(workspace), *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    output = bytearray()
    exceeded = False
    failed = False

    def drain() -> None:
        nonlocal exceeded, failed
        assert process.stdout is not None
        try:
            while chunk := process.stdout.read(64 * 1024):
                remaining = max_output_bytes + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > max_output_bytes:
                    exceeded = True
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
        except OSError:
            failed = True

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        return None
    reader.join()
    if returncode != 0 or exceeded or failed:
        return None
    return bytes(output)


def _git_files(workspace: Path) -> list[Path] | None:
    """The workspace inventory as Git sees it, or ``None`` if Git cannot.

    Canonical detection prefers this, and matching it is what makes the
    ranking parity claim true: without it a `.gitignore`d file is invisible
    to `init` but visible here, so the two would name different agents.

    ``None`` means "Git cannot answer" (not installed, not a repository) and
    the caller falls back to a contained walk. An inventory that overruns
    the bound raises instead, matching canonical discovery.
    """
    env = _git_env()
    try:
        root = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(workspace),
             "rev-parse", "--show-toplevel"],
            check=False, capture_output=True, env=env, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if root.returncode != 0:
        return None
    try:
        git_root = Path(root.stdout.decode("utf-8").strip()).resolve()
    except (UnicodeDecodeError, OSError, RuntimeError, ValueError):
        return None
    listed = _git_inventory_bounded(
        workspace,
        [
            "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false",
            "-c", "core.quotePath=false",
            "ls-files", "-co", "--exclude-standard", "--full-name", "-z", "--", ".",
        ],
        env=env,
        max_output_bytes=MAX_GIT_INVENTORY_BYTES,
    )
    if listed is None:
        raise DiscoveryError(
            "Git candidate-file inventory exceeded static output bounds or "
            "could not be collected safely."
        )
    out: list[Path] = []
    for raw in listed.split(b"\0"):
        if not raw:
            continue
        try:
            rel = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        contained = _contained(git_root / rel, workspace)
        if contained is not None:
            out.append(contained)
    return sorted(set(out))


def _walk_files(workspace: Path) -> list[Path]:
    """Fallback inventory when Git cannot answer. Uncapped, and contained.

    Never *silently* truncated: the manifest-scope verdict is computed from
    where project markers sit, so dropping entries drops that verdict too —
    with enough filler ahead of them two nested agent projects vanish and
    this script reports one scope where the CLI reports ambiguity (#363).
    The bound that shapes the work is on Python *parses*
    (``MAX_PYTHON_FILES``), which is where the canonical CLI puts it.

    ``MAX_WALK_FILES`` is a ceiling, not a cap: a workspace past it raises
    rather than returning a partial inventory, because a verdict computed
    from part of a repository is a verdict about part of a repository. It
    exists so a downloaded tree of millions of unrelated assets cannot
    consume unbounded time and memory before detection sees any source.
    """
    out: list[Path] = []
    seen = 0
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".venv")
        ]
        for fn in files:
            # Counted per entry, not per directory: one directory holding
            # the whole tree would otherwise sail past the ceiling.
            seen += 1
            if seen > MAX_WALK_FILES:
                raise DiscoveryError(
                    f"Workspace inventory exceeds {MAX_WALK_FILES} files without "
                    "Git to bound it. Run inside the Git repository, or point "
                    "--workspace at the project directory you are adopting."
                )
            contained = _contained(Path(root) / fn, workspace)
            if contained is not None:
                out.append(contained)
    return sorted(set(out))


def _inventory(workspace: Path) -> list[Path]:
    """Mirror of ``artifacts._candidate_files``: Git when it can answer,
    a contained filesystem walk otherwise. Deliberately uncapped — the cap
    that matters is on Python *parses* (see ``MAX_PYTHON_FILES``), and a
    global file cap could exhaust itself on assets before reaching any
    source at all."""
    git_files = _git_files(workspace)
    if git_files is not None:
        return git_files
    return _walk_files(workspace)


def _project_marker(directory: Path, extra: tuple[str, ...] = ()) -> str | None:
    for name in (*PROJECT_MARKERS, *extra):
        candidate = directory / name
        # A symlink is not a marker: the verifier refuses a manifest path
        # with symlink components, so accepting one here would name a
        # directory whose scoped command cannot run.
        if candidate.is_symlink():
            continue
        if candidate.is_file():
            return name
    return None


def _project_of(
    path: Path,
    workspace: Path,
    evidence_dirs: frozenset[Path] = frozenset(),
) -> tuple[str, str | None] | None:
    """Nearest project root at or above ``path``, as (relative, marker)."""
    directory = path if path.is_dir() else path.parent
    while True:
        extra = WEAK_PROJECT_MARKERS if directory in evidence_dirs else ()
        marker = _project_marker(directory, extra)
        if marker is not None:
            rel = _rel(directory, workspace) if directory != workspace else "."
            return rel, marker
        if directory == workspace:
            return None
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


class _ProjectAttribution:
    """Which project each path in the workspace belongs to — mirror of
    ``cli/discovery/signals.py:_ProjectAttribution``.

    One rule, two readers. ``_agent_project_candidates`` groups agent
    evidence by project to decide whether one manifest can describe the
    workspace; ``_rank_agent_names`` asks the same question because an
    application root whose identity cannot be established disqualifies the
    names declared *in that project* and no others (#398).
    """

    def __init__(self, workspace: Path, evidence_paths: list[str]) -> None:
        self._workspace = workspace
        self._evidence_dirs = frozenset(
            (workspace / rel) if (workspace / rel).is_dir() else (workspace / rel).parent
            for rel in evidence_paths
        )
        self._by_directory: dict[Path, tuple[str, str | None]] = {}

    def of(self, path: Path) -> tuple[str, str | None]:
        """``(project relative path, marker)``; ``"."`` for the workspace."""
        directory = path if path.is_dir() else path.parent
        cached = self._by_directory.get(directory)
        if cached is not None:
            return cached
        found = _project_of(path, self._workspace, self._evidence_dirs)
        # No marker anywhere above: the workspace is the project by default,
        # not because a marker said so. The fallback marker is therefore the
        # workspace's *strong* marker only, matching
        # `signals.py:_ProjectAttribution.of`. Passing WEAK_PROJECT_MARKERS
        # here reported `marker: "requirements.txt"` where the CLI reported
        # `marker: null` — a weak marker that unlocks nowhere it was found
        # is not the boundary this project rests on.
        resolved = (
            found
            if found is not None
            else (".", _project_marker(self._workspace))
        )
        self._by_directory[directory] = resolved
        return resolved


def _agent_project_candidates(
    workspace: Path,
    evidence_paths: list[str],
    literals_by_path: dict[str, list[str]],
    attribution: _ProjectAttribution,
) -> list[dict[str, Any]]:
    """Group agent evidence by the project each piece of it sits in.

    Same rule as the canonical CLI: a workspace whose agents live in more
    than one self-contained project is not one manifest's scope.
    """
    names: dict[str, set[str]] = {}
    markers: dict[str, str | None] = {}
    for rel in evidence_paths:
        project, marker = attribution.of(workspace / rel)
        names.setdefault(project, set()).update(literals_by_path.get(rel, []))
        markers.setdefault(project, marker)
    return [
        {"path": project, "marker": markers[project], "agent_names": sorted(found)}
        for project, found in sorted(names.items())
    ]


def _rel(path: Path, workspace: Path) -> str:
    """Workspace-relative path, by its logical name.

    The logical name is tried first on purpose: re-resolving here would
    undo `_contained`'s guarantee and rename a symlinked ``agent.py`` to
    its target, which is how the inventory lost its Python files.
    """
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        return path.as_posix()


def _matches(rel: str, basename: str, pattern: str) -> bool:
    if fnmatch.fnmatch(rel, pattern):
        return True
    if "/" not in pattern:
        return fnmatch.fnmatch(basename, pattern)
    return fnmatch.fnmatch(rel, f"*/{pattern}")


def _glob(workspace: Path, files: list[Path], patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for p in files:
            rel = _rel(p, workspace)
            if rel in seen or not _matches(rel, p.name, pattern):
                continue
            seen.add(rel)
            found.append(rel)
    return sorted(found)


def _oversized(path: Path) -> bool:
    """Whether ``path`` exceeds the input adapters' pre-parse size bound.

    Every adapter reads through ``core.static_inputs.read_static_input_bytes``,
    which refuses an input larger than ``inputs.common.MAX_INPUT_FILE_BYTES``
    *before* the loader sees a byte. So an oversized candidate cannot become a
    tool source or a workflow whatever it contains, and answering that needs no
    parse — which is the point: this script runs against repositories it knows
    nothing about, over ``curl | python3``, and must not pull a
    several-hundred-megabyte glob hit into memory to learn it is too big.

    A ``stat`` that fails is not an oversize answer; the caller's existing
    ``OSError`` handling on the read is what reports an unreadable candidate.
    """
    try:
        return path.stat().st_size > MAX_STRUCTURED_FILE_BYTES
    except OSError:
        return False


def _oversize_reason(rel: str) -> str:
    """Spell the CLI's refusal for an oversized suggestion candidate.

    ``load_structured_file`` re-raises the size refusal as ``Unable to read
    input file <path>: <error>`` and ``probe_suggested_source`` rewrites the
    absolute path back to the manifest-relative one. Reproduced here so the
    ``excluded_sources`` reason an agent reads from the zero-install script is
    the reason ``detect --json`` would have given it — excluding the same file
    under a different explanation only moves the divergence.
    """
    return (
        f"Unable to read input file {rel}: Input file too large "
        f"(limit: {MAX_STRUCTURED_FILE_BYTES} bytes): {rel}"
    )


def _looks_like_n8n_workflow(path: Path) -> bool:
    """Match the CLI heuristic in cli/discovery/artifacts.py: a JSON file
    is an n8n workflow when it (or any element in a list) is a dict with
    a ``nodes`` list and ``connections`` dict, and at least one node has
    a ``type`` starting with ``n8n-nodes-`` or ``@n8n/n8n-nodes-``."""
    if path.suffix.lower() != ".json":
        return False
    if _oversized(path):
        # The n8n adapter loads workflows through ``load_structured_file``,
        # so an oversized file cannot be scanned as one; scoring `n8n` off a
        # file `scan` refuses would name a framework nobody can verify.
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    candidates = data if isinstance(data, list) else [data]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        nodes = item.get("nodes")
        connections = item.get("connections")
        if not isinstance(nodes, list) or not isinstance(connections, dict):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type")
            if isinstance(node_type, str) and (
                node_type.startswith("n8n-nodes-")
                or node_type.startswith("@n8n/n8n-nodes-")
            ):
                return True
    return False


def _conductor_agent_markers(data: Any) -> set[str]:
    candidates = data if isinstance(data, list) else [data]
    if not candidates:
        return set()
    for item in candidates:
        if not (
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and bool(item["name"].strip())
            and isinstance(item.get("tasks"), list)
            and bool(item["tasks"])
            and item.get("schemaVersion", 2) == 2
        ):
            return set()
    markers: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            task_type = value.get("type")
            if task_type in {"CALL_MCP_TOOL", "LIST_MCP_TOOLS", "LLM_CHAT_COMPLETE"}:
                markers.add(str(task_type))
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(data)
    return markers


def _probe_suggested(workspace: Path, rel: str, kind: str) -> str | None:
    """Return ``None`` if the input adapters would accept ``rel`` as a
    ``kind`` tool source, else a one-line reason ``scan`` would reject it.

    Stdlib mirror of
    :func:`agents_shipgate.cli.discovery.artifacts.probe_suggested_source`
    (which calls the real ``load_mcp_tools`` / ``load_openapi_tools``).
    The probe is JSON-only — see the module docstring — so an unparseable
    or YAML candidate is kept as a suggestion rather than wrongly dropped.
    The MCP suggestion globs are all ``*.json`` / ``.agents-shipgate/*.json``,
    so the load-bearing ``mcpServers``-host-config case is always covered.

    The one rejection this mirror can make without a parser is the size
    bound: it applies to every candidate, JSON or YAML, and it is checked
    before the file is read at all.
    """
    path = workspace / rel
    if _oversized(path):
        # Asked before the YAML early return and before any read. The size
        # refusal is content-independent — the adapters apply it to a .yaml
        # spec exactly as to a .json one — so it is the one exclusion this
        # script can make without a parser, and skipping it would leave an
        # oversized spec suggested here and excluded by the CLI.
        return _oversize_reason(rel)
    if kind == "openapi" and path.suffix.lower() in (".yaml", ".yml"):
        return None  # No stdlib YAML parser — keep, never wrongly exclude.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        if path.suffix.lower() == ".json":
            # A .json the adapter would also fail to parse (exit 3).
            return f"Unable to parse input file: {rel}"
        return None  # Non-JSON we can't read — conservative keep.
    if kind == "mcp":
        return _probe_mcp(data)
    if kind == "conductor":
        return None if _conductor_agent_markers(data) else (
            "not a Conductor AI/MCP workflow JSON document"
        )
    return _probe_openapi(data)


def _probe_mcp(data: Any) -> str | None:
    """Mirror ``load_mcp_tools``'s accept rule (inputs/mcp.py)."""
    if isinstance(data, list):
        return None  # Top-level tools array.
    if not isinstance(data, dict):
        return "MCP tools file must be an object or array"
    if isinstance(data.get("mcpServers"), dict) or isinstance(data.get("servers"), dict):
        # Host MCP *configuration* (e.g. a Cursor/Claude plugin manifest),
        # which the mcp adapter never accepts as a tools export.
        return (
            "mcpServers-style MCP server config (host configuration), "
            "not an MCP tools-array export"
        )
    raw_tools = data.get("tools")
    if data.get("wildcard") is True or raw_tools == "*":
        if isinstance(raw_tools, list) and raw_tools:
            return "MCP source declares wildcard tool exposure and an explicit tools array"
        return None  # Wildcard exposure.
    if not isinstance(raw_tools, list):
        return "MCP tools file must contain a tools array"
    return None


def _probe_openapi(data: Any) -> str | None:
    """Mirror ``load_openapi_tools``'s accept rule (inputs/openapi.py)."""
    if not isinstance(data, dict):
        return "OpenAPI file must contain an object"
    if "openapi" not in data:
        # Catches Swagger 2.0 (keyed ``swagger:``) and non-OpenAPI JSON.
        return "OpenAPI file missing 'openapi' version"
    if not isinstance(data.get("paths"), dict):
        return "OpenAPI file missing paths object"
    return None


# --- MCP registration idioms (mirror of inputs/mcp_idioms.py) ---------------
#
# Most MCP servers never emit their tool surface: `mongodb-js/mongodb-mcp-server`
# and `grafana/mcp-grafana` publish `drop-database`, `delete-many` and
# `update_incident` and commit no export at all. What every one of them does do
# is write the tool's name as a **string literal at a registration site**. The
# installed CLI reads that literal through a built-in registry of named idioms
# (`agents_shipgate.inputs.mcp_idioms`, #431); until #485 this script did not,
# so the documented first-contact detector answered "Stop, not an agent
# project" on exactly the repositories the installed CLI had just learned to
# read.
#
# This is a second implementation of a load-bearing matcher, which is the
# recurring bug class in this repository. What makes it affordable is that it
# is not allowed to become a *different* one: `tests/mcp_idiom_corpus.py` holds
# one conformance corpus — every idiom's positive sample, the whole adversarial
# sweep, the path predicate's cases and both escape grammars — and
# `tests/test_zero_install_detector.py` drives it through both readers,
# comparing every field of every site including its span. Neither reader can
# change its answer on any case either of them has ever been asked about
# without the other following.
#
# Reading is done over a **masked** copy of the source, in which comments and
# string bodies have been overwritten. A registration site can therefore never
# be found inside a comment or inside another string, and a name is a name only
# when the masking pass recorded a real literal at that offset.

#: File suffixes each language's idioms are read from. TypeScript's list covers
#: JavaScript too. ``.tsx``/``.jsx`` are deliberately absent: JSX puts prose in
#: code position, so an apostrophe in ``<p>don't</p>`` would open a string that
#: never closes and hold a whole repository's surface at partial.
LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "typescript": (".ts", ".mts", ".cts", ".js", ".mjs", ".cjs"),
    "go": (".go",),
}

#: Declared-dependency tokens that establish an MCP framework for a language.
#: The provenance gate: an idiom hit in a repository that declares no MCP
#: dependency is a coincidence of spelling until something says otherwise.
TYPESCRIPT_FRAMEWORK_PACKAGES: tuple[str, ...] = (
    "@modelcontextprotocol/",
    "fastmcp",
    "mcp-framework",
    "@mcp-ui/",
    "xmcp",
)
GO_FRAMEWORK_MODULES: tuple[str, ...] = (
    "github.com/modelcontextprotocol/go-sdk",
    "github.com/mark3labs/mcp-go",
    "github.com/metoro-io/mcp-golang",
    "github.com/thinkinaixyz/go-mcp",
    "github.com/ktr0731/go-mcp",
)

#: Directory names never walked for registration sites. Distinct from this
#: script's own inventory-level ``SKIP_DIRS`` on purpose: this is the CLI
#: reader's list, and the two detectors have to skip the same directories *for
#: registrations* whatever each one's inventory already dropped.
SKIP_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", ".next", ".nuxt", ".turbo", ".venv",
        "__pycache__", "bin", "build", "coverage", "dist", "node_modules",
        "obj", "out", "target", "vendor", "venv",
    }
)

#: Path segments whose files declare tools for a test, not for the server.
TEST_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {
        "__mocks__", "__tests__", "e2e", "fixtures", "test", "test-fixtures",
        "testdata", "tests",
    }
)
_TEST_FILE_SUFFIXES: tuple[str, ...] = (
    "_test.go",
    ".test.ts", ".test.js", ".test.mts", ".test.mjs",
    ".spec.ts", ".spec.js", ".spec.mts", ".spec.mjs",
)

#: Every idiom's pattern requires these four characters, in some case. A file
#: that does not contain them cannot hold a registration, so ``scan_source``
#: answers it without masking. It lives *inside* ``scan_source`` rather than at
#: the call sites: a caller's own copy is a second, weaker matcher, and the one
#: written against the trigger catalog's diff tokens missed
#: ``public static readonly toolName``.
PREFILTER_TOKEN = "tool"

#: The shape a tool name has to have to be read as one.
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: The largest source file this reader opens. Deliberately below the input
#: loader's 10 MB bound, so an oversized file is recorded as "too large"
#: rather than as a decoding failure.
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024

#: The idioms this reader implements, by id. Pinned from both sides by the
#: conformance test: equal to the CLI registry's ids — an idiom the CLI gains
#: and this script does not is the #485 divergence happening again — and
#: covering every id ``scan_source`` actually emits, so the constant cannot
#: describe a reader it has drifted from.
IDIOM_IDS: frozenset[str] = frozenset(
    {
        "ts_static_tool_name",
        "ts_sdk_register_tool",
        "go_must_tool",
        "go_new_tool",
        "go_tool_struct",
    }
)


@dataclass(frozen=True)
class RegistrationSite:
    """One registration this reader found, resolved or not.

    ``span`` is the byte range of the construct that matched — the whole call
    including its argument list, or the whole composite literal, never a
    lookup scope. Containment of one span in another is what lets a wrapper
    call whose own first argument is not a literal
    (``NewTool(meta, mcp.Tool{Name: "issue_read"})``) stay silent instead of
    reporting an omission for a tool that was, in fact, named.
    """

    idiom: str
    name: str | None
    line: int
    column: int
    span: tuple[int, int]
    description: str | None = None
    operation_type: str | None = None
    unresolved_reason: str | None = None


@dataclass(frozen=True)
class SourceScanResult:
    """What one file yielded.

    ``anomalies`` are masking failures. They are separate from an unresolved
    site because they are a fact about the *file*: past the anomaly this reader
    cannot tell code from content, so a site it did not find there proves
    nothing.
    """

    sites: tuple[RegistrationSite, ...] = ()
    anomalies: tuple[str, ...] = ()


def language_for_path(path: Any) -> str | None:
    """The language whose idioms apply to ``path``, or ``None``."""
    suffix = PurePosixPath(str(path)).suffix.lower()
    for language, extensions in LANGUAGE_EXTENSIONS.items():
        if suffix in extensions:
            return language
    return None


def is_scannable_path(relative_path: Any) -> bool:
    """Whether a workspace-relative path is read for registration sites.

    One predicate on the CLI side, used by both its adapter's walk and its
    discovery probe. This is its mirror, and the pair that disagrees is the
    pair where one detector promises tools the other refuses to enumerate.
    """
    path = PurePosixPath(str(relative_path).replace("\\", "/"))
    if language_for_path(path) is None:
        return False
    parts = path.parts
    if any(part in SKIP_DIRECTORY_NAMES for part in parts):
        return False
    if any(part.lower() in TEST_DIRECTORY_NAMES for part in parts[:-1]):
        return False
    name = path.name.lower()
    return not name.endswith(_TEST_FILE_SUFFIXES)


# Masking. Comments become spaces so a token cannot span the hole they leave;
# string literals become NULs so a literal's *position* stays findable while
# its content can never be matched as code.
_COMMENT_FILL = " "
_STRING_FILL = "\x00"

# Characters after which a `/` opens a regular expression rather than dividing.
_REGEX_PRECEDING_CHARS = frozenset("(,=:[!&|?{};+-*%~^<>")
#: Keywords whose parenthesised condition can be followed directly by a regex
#: that begins the statement's body. `)` alone cannot decide: `foo(a) / 2`
#: divides and `if (a) /re/.test(b)` does not.
_REGEX_PRECEDING_STATEMENTS = frozenset({"if", "for", "while", "switch", "catch", "with"})
_REGEX_PRECEDING_WORDS = frozenset(
    {
        "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
        "throw", "case", "do", "else", "yield", "await",
    }
)

#: The only characters that can begin a comment or a string in either language.
#: The masking loop jumps between them instead of visiting every character.
_INTERESTING = re.compile(r"""[/'"`]""")

#: Escapes both languages spell the same way and mean the same thing.
_SHARED_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v",
    "\\": "\\", "'": "'", '"': '"',
}
#: JavaScript adds a backtick, ``\0`` for NUL, and a line continuation.
_TYPESCRIPT_ESCAPES = {**_SHARED_ESCAPES, "`": "`"}
#: Go adds the bell and has no line continuation and no bare ``\0``.
_GO_ESCAPES = {**_SHARED_ESCAPES, "a": "\a"}

#: The line terminators a backslash can continue a line across. ``\r`` is here
#: because a CRLF checkout spells the same continuation with two characters,
#: and JavaScript reads both files identically — so a reader that lost the
#: registration on one of them would answer "not an agent project" for a
#: line-ending translation (#485 review).
_TYPESCRIPT_LINE_TERMINATORS = frozenset("\n\r")
#: Go adds the bell and has no line continuation and no bare ``\0``.
_GO_ESCAPES = {**_SHARED_ESCAPES, "a": "\a"}


_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_OCTAL_DIGITS = frozenset("01234567")


@dataclass(frozen=True)
class MaskedSource:
    """``text`` with comments and string bodies overwritten.

    ``literals`` maps the offset of a string literal's opening quote to its
    decoded value (``None`` when the literal is not a constant) and the offset
    just past its closing quote. The end offset is recorded rather than
    recovered by scanning the fill characters, because masking preserves
    newlines: a multi-line template literal's fill run stops at its first line
    break, and a caller asking what follows the literal would be looking
    inside it.
    """

    text: str
    masked: str
    literals: dict[int, tuple[str | None, int]]
    anomalies: tuple[str, ...]

    def skip_space(self, index: int) -> int:
        length = len(self.masked)
        while index < length and self.masked[index].isspace():
            index += 1
        return index

    def literal_at(self, index: int) -> tuple[bool, str | None, int]:
        """Resolve a string literal starting at ``index`` (whitespace skipped)."""
        start = self.skip_space(index)
        record = self.literals.get(start)
        if record is None:
            return False, None, start
        value, end = record
        return True, value, end

    def line_column(self, index: int) -> tuple[int, int]:
        prefix = self.text[:index]
        line = prefix.count("\n") + 1
        column = index - (prefix.rfind("\n") + 1) + 1
        return line, column


def mask_source(text: str, language: str) -> MaskedSource:
    """Overwrite comments and string bodies, recording every string literal."""
    if language == "go":
        return _mask_go(text)
    return _mask_typescript(text)


def decode_literal(body: str, language: str) -> str | None:
    """The literal's value, or ``None`` when it cannot be decoded exactly.

    Escape grammars are per language, and one decoder shared between them is a
    silent mistranslation rather than a parse error: Go writes an octal escape
    as three digits, so ``MustTool("delete\\137all", …)`` registers
    ``delete_all`` and a JavaScript-shaped decoder produced ``delete137all`` —
    the real action absent and an id nobody serves in its place. Anything
    either grammar does not define is refused, and a refusal becomes a
    recorded omission instead of a guessed name.
    """
    if "\\" not in body:
        return body
    if language == "go":
        return _decode_go(body)
    return _decode_typescript(body)


def _hex_value(body: str, start: int, width: int) -> int | None:
    digits = body[start : start + width]
    if len(digits) != width or any(char not in _HEX_DIGITS for char in digits):
        return None
    return int(digits, 16)


def _decode_typescript(body: str) -> str | None:
    out: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= length:
            return None
        marker = body[index + 1]
        if marker in _TYPESCRIPT_LINE_TERMINATORS:
            # A LineContinuation contributes nothing to the value. CRLF is one
            # terminator sequence: reading it as `\r` plus a stray line break
            # both mangles the value and, in the scanner, ends the string.
            index += 3 if marker == "\r" and body[index + 2 : index + 3] == "\n" else 2
            continue
        if marker in _TYPESCRIPT_ESCAPES:
            out.append(_TYPESCRIPT_ESCAPES[marker])
            index += 2
            continue
        if marker == "0" and (index + 2 >= length or body[index + 2] not in "0123456789"):
            out.append("\0")
            index += 2
            continue
        if marker == "x":
            value = _hex_value(body, index + 2, 2)
            if value is None:
                return None
            out.append(chr(value))
            index += 4
            continue
        if marker == "u":
            if index + 2 < length and body[index + 2] == "{":
                close = body.find("}", index + 3)
                digits = body[index + 3 : close] if close != -1 else ""
                if not digits or any(char not in _HEX_DIGITS for char in digits):
                    return None
                point = int(digits, 16)
                if point > 0x10FFFF:
                    return None
                out.append(chr(point))
                index = close + 1
                continue
            value = _hex_value(body, index + 2, 4)
            if value is None:
                return None
            out.append(chr(value))
            index += 6
            continue
        if marker.isdigit():
            # Legacy octal (`\1`-`\7`) is a syntax error under `use strict`
            # and in a template literal, and octal *elsewhere*; `\8`/`\9` are
            # their own special case. Which one a file means depends on a mode
            # this reader does not track, so it refuses rather than pick.
            return None
        out.append(marker)
        index += 2
    return "".join(out)


def _decode_go(body: str) -> str | None:
    out: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= length:
            return None
        marker = body[index + 1]
        if marker in _GO_ESCAPES:
            out.append(_GO_ESCAPES[marker])
            index += 2
            continue
        if marker in _OCTAL_DIGITS:
            digits = body[index + 1 : index + 4]
            if len(digits) != 3 or any(char not in _OCTAL_DIGITS for char in digits):
                return None
            value = int(digits, 8)
            if value > 255:
                return None
            out.append(chr(value))
            index += 4
            continue
        widths = {"x": 2, "u": 4, "U": 8}
        if marker in widths:
            value = _hex_value(body, index + 2, widths[marker])
            if value is None or value > 0x10FFFF:
                return None
            out.append(chr(value))
            index += 2 + widths[marker]
            continue
        # Every other escape is a Go compile error, so the file this reader is
        # looking at is not the file that built the server.
        return None
    return "".join(out)


class _Masker:
    """Shared bookkeeping for the two language maskers."""

    def __init__(self, text: str, language: str) -> None:
        self.text = text
        self.language = language
        self.out: list[str] = list(text)
        self.literals: dict[int, tuple[str | None, int]] = {}
        self.anomalies: list[str] = []

    def blank(self, start: int, end: int, fill: str) -> None:
        end = min(end, len(self.out))
        if end <= start:
            return
        segment = self.text[start:end]
        # Newlines survive so line numbers stay the file's own. Slice
        # assignment rather than a per-character loop: the latter cost more
        # than a second on a 1.4 MB module that registers nothing.
        if "\n" in segment:
            self.out[start:end] = ["\n" if char == "\n" else fill for char in segment]
        else:
            self.out[start:end] = fill * (end - start)

    def record(self, start: int, end: int, value: str | None) -> None:
        self.blank(start, end, _STRING_FILL)
        self.literals[start] = (value, end)

    def result(self) -> MaskedSource:
        return MaskedSource(
            text=self.text,
            masked="".join(self.out),
            literals=self.literals,
            anomalies=tuple(self.anomalies),
        )


def _previous_significant(masked: list[str], index: int) -> tuple[str, int]:
    while index >= 0 and masked[index].isspace():
        index -= 1
    return (masked[index], index) if index >= 0 else ("", -1)


def _preceding_word(masked: list[str], index: int) -> str:
    """The identifier ending at or before ``index``, read from the mask.

    The mask, not the raw text: comments have been overwritten with spaces
    there, so `if /*why*/ (ok) /re/` still finds `if`. Reading the raw text
    found `/` — the tail of the comment — decided the slash was division, and
    scanned the regex body as code, which reported a tool invented out of a
    pattern. That is the one outcome masking exists to make impossible.
    """

    while index >= 0 and masked[index].isspace():
        index -= 1
    end = index + 1
    while index >= 0 and (masked[index].isalnum() or masked[index] in "_$"):
        index -= 1
    return "".join(masked[index + 1 : end])


def _mask_typescript(text: str) -> MaskedSource:
    masker = _Masker(text, "typescript")
    index = 0
    length = len(text)
    while index < length:
        found = _INTERESTING.search(text, index)
        if found is None:
            break
        index = found.start()
        char = text[index]
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            end = text.find("\n", index)
            end = length if end == -1 else end
            masker.blank(index, end, _COMMENT_FILL)
            index = end
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end == -1:
                masker.blank(index, length, _COMMENT_FILL)
                masker.anomalies.append("unterminated_block_comment")
                break
            masker.blank(index, end + 2, _COMMENT_FILL)
            index = end + 2
            continue
        if char in {"'", '"'}:
            index = _consume_quoted(masker, index, char, allow_newline=False)
            continue
        if char == "`":
            index = _consume_template(masker, index)
            continue
        if char == "/" and _opens_regex(masker.out, index):
            index = _consume_regex(masker, index)
            continue
        index += 1
    return masker.result()


def _opens_regex(out: list[str], index: int) -> bool:
    previous, previous_index = _previous_significant(out, index - 1)
    if previous == "" or previous in _REGEX_PRECEDING_CHARS:
        return True
    if previous == ")":
        # A `)` is usually the end of a call or a parenthesised expression, and
        # `foo(a) / 2` divides. But it is also the end of a control statement's
        # condition, and there a regex validly *begins the body*:
        # `if (ok) /\.registerTool("fake", handler)/.test(value);` is
        # JavaScript, and reading its `/` as division scanned the pattern as
        # code and reported a `fake` tool — a registration invented out of a
        # regex body, which is the one thing this module's masking exists to
        # make impossible. Which of the two it is, is decided by the keyword in
        # front of the matching `(`.
        opener = _matching_open(out, previous_index)
        if opener is None:
            return False
        return _preceding_word(out, opener - 1) in _REGEX_PRECEDING_STATEMENTS
    if previous.isalnum() or previous in "_$":
        return _preceding_word(out, index - 1) in _REGEX_PRECEDING_WORDS
    return False


def _matching_open(out: list[str], close_index: int) -> int | None:
    depth = 0
    for index in range(close_index, -1, -1):
        char = out[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                return index
    return None


def _past_escape(text: str, index: int, language: str) -> int:
    """The index just past the escape whose backslash sits at ``index``.

    Two characters, except for a JavaScript line continuation spelled with
    CRLF, which is three: the backslash and one *line terminator sequence*.
    Stepping over two of them leaves the ``\n`` behind, and the scanner then
    ends the string there — so the identical file lost its registration on a
    Git-for-Windows checkout while resolving it on a Unix one.

    Go has no line continuation, and its scanner must keep treating a newline
    as the end of an interpreted string, so this is TypeScript's rule only.
    """

    if language == "typescript" and text[index + 1 : index + 3] == "\r\n":
        return index + 3
    return index + 2


def _consume_quoted(
    masker: _Masker, start: int, quote: str, *, allow_newline: bool
) -> int:
    text = masker.text
    length = len(text)
    index = start + 1
    while index < length:
        char = text[index]
        if char == "\\":
            index = _past_escape(text, index, masker.language)
            continue
        if char == quote:
            masker.record(
                start, index + 1, decode_literal(text[start + 1 : index], masker.language)
            )
            return index + 1
        if char == "\n" and not allow_newline:
            break
        index += 1
    # Unterminated. Blank to the resync point but record no literal, and say so:
    # past here this reader cannot tell code from content.
    end = text.find("\n", start)
    end = length if end == -1 or allow_newline else end
    masker.blank(start, end, _STRING_FILL)
    masker.anomalies.append("unterminated_string")
    return max(end, start + 1)


def _consume_template(masker: _Masker, start: int) -> int:
    """Consume a backtick template literal, tracking ``${…}`` substitutions."""

    text = masker.text
    length = len(text)
    end, substituted = _template_end(text, masker.out, start)
    if end is None:
        masker.blank(start, length, _STRING_FILL)
        masker.anomalies.append("unterminated_string")
        return length
    body = text[start + 1 : end - 1]
    masker.record(
        start, end, None if substituted else decode_literal(body, masker.language)
    )
    return end


def _template_end(
    text: str, out: list[str], start: int
) -> tuple[int | None, bool]:
    """Where the template literal at ``start`` ends, and whether it substitutes.

    ``None`` when it never closes. The second value says whether the *outer*
    template carries a ``${…}``, which is what makes its value non-constant.

    **A `${…}` holds code, so a brace inside a string, a comment, a regex or a
    nested template is not a structural brace.** Counting them made
    ``const msg = `Literal brace: ${"{"}`;`` leave the substitution open, and
    from there the rest of the file was consumed as one unterminated template
    — every registration after that line silently gone, and a workspace that
    declares an MCP dependency reported as "not an agent project" over a brace
    in a string (#485 review).

    Iterative, with one stack entry per open template, because a nested
    template is reached through a substitution and recursion on attacker-shaped
    input is a crash rather than a wrong answer.
    """

    length = len(text)
    index = start + 1
    # One entry per open template: its `${…}` brace depth, 0 in template text.
    depths: list[int] = [0]
    substituted = False
    while index < length and depths:
        char = text[index]
        if char == "\\":
            index = _past_escape(text, index, "typescript")
            continue
        if depths[-1] == 0:
            if char == "$" and text[index + 1 : index + 2] == "{":
                substituted = substituted or len(depths) == 1
                depths[-1] = 1
                index += 2
                continue
            if char == "`":
                depths.pop()
                index += 1
                continue
            index += 1
            continue
        if char in {"'", '"'}:
            index = _skip_quoted(text, index)
            continue
        if char == "`":
            depths.append(0)
            index += 1
            continue
        if char == "/" and text[index + 1 : index + 2] == "/":
            line_end = text.find("\n", index)
            line_end = length if line_end == -1 else line_end
            # Blanked as it is walked, not merely stepped over: the regex
            # heuristic below reads the mask to find the keyword in front of a
            # slash, and a comment still spelled out there hides it.
            out[index:line_end] = _COMMENT_FILL * (line_end - index)
            index = line_end
            continue
        if char == "/" and text[index + 1 : index + 2] == "*":
            close = text.find("*/", index + 2)
            block_end = length if close == -1 else close + 2
            out[index:block_end] = [
                "\n" if character == "\n" else _COMMENT_FILL
                for character in text[index:block_end]
            ]
            index = block_end
            continue
        if char == "/" and _opens_regex(out, index):
            index = _skip_regex(text, index)
            continue
        if char == "{":
            depths[-1] += 1
        elif char == "}":
            depths[-1] -= 1
        index += 1
    return (index if not depths else None), substituted


def _skip_quoted(text: str, start: int) -> int:
    """Index just past a quoted string this reader only needs to walk over."""

    quote = text[start]
    length = len(text)
    index = start + 1
    while index < length:
        char = text[index]
        if char == "\\":
            index = _past_escape(text, index, "typescript")
            continue
        if char == quote:
            return index + 1
        if char == "\n":
            # Unterminated on its line. Resync there rather than swallowing the
            # rest of the substitution.
            return index
        index += 1
    return length


def _skip_regex(text: str, start: int) -> int:
    """Index just past a regex literal, or one past the slash if it is not one."""

    length = len(text)
    index = start + 1
    in_class = False
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n":
            return start + 1
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return index + 1
        index += 1
    return start + 1


def _consume_regex(masker: _Masker, start: int) -> int:
    text = masker.text
    length = len(text)
    index = start + 1
    in_class = False
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n":
            # Not a regex after all — a lone `/` on a line. Leave it as code.
            return start + 1
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            masker.blank(start, index + 1, _COMMENT_FILL)
            return index + 1
        index += 1
    return start + 1


def _mask_go(text: str) -> MaskedSource:
    masker = _Masker(text, "go")
    index = 0
    length = len(text)
    while index < length:
        found = _INTERESTING.search(text, index)
        if found is None:
            break
        index = found.start()
        char = text[index]
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            end = text.find("\n", index)
            end = length if end == -1 else end
            masker.blank(index, end, _COMMENT_FILL)
            index = end
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end == -1:
                masker.blank(index, length, _COMMENT_FILL)
                masker.anomalies.append("unterminated_block_comment")
                break
            masker.blank(index, end + 2, _COMMENT_FILL)
            index = end + 2
            continue
        if char == '"':
            index = _consume_quoted(masker, index, '"', allow_newline=False)
            continue
        if char == "'":
            index = _consume_quoted(masker, index, "'", allow_newline=False)
            continue
        if char == "`":
            end = text.find("`", index + 1)
            if end == -1:
                masker.blank(index, length, _STRING_FILL)
                masker.anomalies.append("unterminated_string")
                break
            masker.record(index, end + 1, text[index + 1 : end])
            index = end + 1
            continue
        index += 1
    return masker.result()


# Idiom matchers.

_TS_MODIFIERS = r"(?:(?:public|private|protected|readonly|override|declare|abstract)\s+)*"
_TS_STATIC_TOOL_NAME_RE = re.compile(
    rf"(?<![\w$])static\s+{_TS_MODIFIERS}toolName\s*(?::[^=;\n]*)?=\s*"
)
_TS_STATIC_OPERATION_TYPE_RE = re.compile(
    rf"(?<![\w$])static\s+{_TS_MODIFIERS}operationType\s*(?::[^=;\n]*)?=\s*"
)
# ``(?<![\w$.])`` and not ``(?<![\w$])``: the character before ``description``
# in ``this.description = "…"`` is a dot, which the narrower lookbehind admits,
# so an assignment inside a method read as the class's description field.
_TS_DESCRIPTION_RE = re.compile(
    rf"(?<![\w$.])(?:static\s+)?{_TS_MODIFIERS}description\s*(?::[^=;\n]*)?=\s*"
)
_TS_REGISTER_TOOL_RE = re.compile(r"\.\s*(?:registerTool|tool)\s*\(\s*")
_GO_MUST_TOOL_RE = re.compile(r"(?<![\w])MustTool\s*\(\s*")
_GO_NEW_TOOL_RE = re.compile(r"(?<![\w])NewTool\s*\(\s*")
_GO_TOOL_STRUCT_RE = re.compile(r"(?<![\w])Tool\s*\{")
_GO_STRUCT_NAME_FIELD_RE = re.compile(r"(?<![\w])Name\s*:\s*")
_GO_KEYED_FIELD_RE = re.compile(r"(?<![\w])[A-Za-z_]\w*\s*:")
_GO_STRUCT_DESCRIPTION_FIELD_RE = re.compile(r"(?<![\w])Description\s*:\s*")


def _matching_close(masked: str, open_index: int, opener: str, closer: str) -> int | None:
    depth = 0
    for index in range(open_index, len(masked)):
        char = masked[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _brace_pairs(masked: str) -> list[tuple[int, int]]:
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for match in re.finditer(r"[{}]", masked):
        if match.group() == "{":
            stack.append(match.start())
        elif stack:
            pairs.append((stack.pop(), match.start() + 1))
    return pairs


def _enclosing_block(pairs: list[tuple[int, int]], index: int) -> tuple[int, int] | None:
    """The innermost ``{…}`` containing ``index``, as ``(open, close_exclusive)``."""
    best: tuple[int, int] | None = None
    for start, end in pairs:
        if start <= index < end and (best is None or start > best[0]):
            best = (start, end)
    return best


def _resolve_name(value: str | None, found: bool) -> tuple[str | None, str | None]:
    if not found or value is None:
        return None, "name_not_literal"
    if not TOOL_NAME_RE.match(value):
        return None, "implausible_tool_name"
    return value, None


#: Characters that continue an expression rather than beginning a statement.
#: Consulted only *after* a line break, and only for a character the caller's
#: own terminators do not claim: Go ends a struct field with `,` on the next
#: line, and that comma ends the value rather than continuing it.
_EXPRESSION_CONTINUATION = frozenset("+-*/%&|^<>=!?.,([")


def _literal_is_whole_value(
    source: MaskedSource, end: int, terminators: str
) -> bool:
    """Whether the literal ending at ``end`` is the entire value, not part of one.

    ``static toolName = "backup" + SUFFIX`` resolves to a literal this reader
    can see, and reading it as the tool name would publish ``backup`` for a
    tool the server registers under some other name — a fail-open of exactly
    the shape #393 catalogues, where the proof rests on a spelling. The literal
    counts only when the expression ends there: at one of ``terminators``, at
    the end of input, or at a line break (JavaScript inserts the semicolon).
    """

    masked = source.masked
    length = len(masked)
    index = end
    while index < length and masked[index] in " \t\r":
        index += 1
    if index >= length:
        return True
    if masked[index] in terminators:
        return True
    if masked[index] != "\n":
        return False
    # A line break ends the statement only when what follows cannot continue
    # the expression. `static toolName = "safe"` followed by `+ "_delete"` on
    # the next line is one value spelled across two lines, and accepting the
    # first literal publishes `safe` for a tool the server registers as
    # `safe_delete` — a name nobody serves, at `medium` confidence, which is
    # worse than the omission refusing it produces. Comments are already
    # spaces in the mask, so skipping whitespace skips them too.
    while index < length and masked[index].isspace():
        index += 1
    if index >= length:
        return True
    following = masked[index]
    return following in terminators or following not in _EXPRESSION_CONTINUATION


def _call_sites(
    source: MaskedSource, pattern: re.Pattern[str], idiom: str
) -> list[RegistrationSite]:
    """Sites for a ``Name(<literal>, …)`` idiom."""
    sites: list[RegistrationSite] = []
    for match in pattern.finditer(source.masked):
        open_paren = source.masked.rfind("(", match.start(), match.end())
        if open_paren == -1:
            continue
        close = _matching_close(source.masked, open_paren, "(", ")")
        span = (match.start(), close if close is not None else match.end())
        found, value, end = source.literal_at(match.end())
        name, unresolved = _resolve_name(value, found)
        if name is not None:
            # A registration passes the name *and* what to do with it, so the
            # first argument is followed by a comma. `)` means a one-argument
            # call — `map.tool("issues")` is a lookup, and reading it as a
            # registration is how an accessor becomes a phantom tool. Anything
            # else (`+`) means the name is not this literal.
            after = source.skip_space(end)
            following = source.masked[after] if after < len(source.masked) else ""
            if following == ")":
                continue
            if following != ",":
                name, unresolved = None, "name_not_literal"
        if name is None and (
            close is None or not _has_second_argument(source.masked, open_paren, close)
        ):
            # An unresolved site needs the same second argument before it is
            # reported: it is what keeps `map.tool(key)` out of the ledger.
            continue
        line, column = source.line_column(match.start())
        sites.append(
            RegistrationSite(
                idiom=idiom,
                name=name,
                line=line,
                column=column,
                span=span,
                unresolved_reason=unresolved,
            )
        )
    return sites


def _has_second_argument(masked: str, open_paren: int, close: int) -> bool:
    depth = 0
    for index in range(open_paren, close):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 1:
            return True
    return False


def _ts_static_tool_name_sites(source: MaskedSource) -> list[RegistrationSite]:
    sites: list[RegistrationSite] = []
    pairs = _brace_pairs(source.masked)
    for match in _TS_STATIC_TOOL_NAME_RE.finditer(source.masked):
        found, value, end = source.literal_at(match.end())
        name, unresolved = _resolve_name(value, found)
        if name is not None and not _literal_is_whole_value(source, end, ";}"):
            name, unresolved = None, "name_not_literal"
        line, column = source.line_column(match.start())
        block = _enclosing_block(pairs, match.start())
        operation_type: str | None = None
        description: str | None = None
        if block is not None and name is not None:
            operation_type = _first_literal_in(source, _TS_STATIC_OPERATION_TYPE_RE, block)
            description = _first_literal_in(source, _TS_DESCRIPTION_RE, block)
        # The construct, never the enclosing class body. `block` is the scope
        # the sibling literals are looked up in; using it as the span made the
        # containment rule read *any* registration written inside the class as
        # "the same registration", so a class whose `toolName` is built at
        # runtime lost its omission the moment it also called `.registerTool(`.
        sites.append(
            RegistrationSite(
                idiom="ts_static_tool_name",
                name=name,
                line=line,
                column=column,
                span=(match.start(), max(end, match.end())),
                description=description,
                operation_type=operation_type,
                unresolved_reason=unresolved,
            )
        )
    return sites


def _first_literal_in(
    source: MaskedSource, pattern: re.Pattern[str], block: tuple[int, int]
) -> str | None:
    start, end = block
    for match in pattern.finditer(source.masked, start, end):
        found, value, literal_end = source.literal_at(match.end())
        if found and value and _literal_is_whole_value(source, literal_end, ";}"):
            return value
    return None


def _go_tool_struct_sites(source: MaskedSource) -> list[RegistrationSite]:
    sites: list[RegistrationSite] = []
    for match in _GO_TOOL_STRUCT_RE.finditer(source.masked):
        open_brace = source.masked.index("{", match.start())
        close = _matching_close(source.masked, open_brace, "{", "}")
        if close is None:
            continue
        if _has_keyed_field(source.masked, open_brace, close):
            sites.extend(_go_tool_struct_site(source, match.start(), open_brace, close))
            continue
        # No keyed field at this literal's own level, so it is a composite of
        # elements — `[]mcp.Tool{{Name: "a"}, {Name: "b"}}`. Reading only the
        # outer brace would find the first element's `Name:` two levels down,
        # reject it as nested, and report nothing at all.
        for child_open, child_close in _child_braces(source.masked, open_brace, close):
            sites.extend(_go_tool_struct_site(source, child_open, child_open, child_close))
    return sites


def _go_tool_struct_site(
    source: MaskedSource, start: int, open_brace: int, close: int
) -> list[RegistrationSite]:
    field = _GO_STRUCT_NAME_FIELD_RE.search(source.masked, open_brace + 1, close)
    # Only the literal's own `Name:` field, never one belonging to something
    # nested inside it: `mcp.Tool{Annotations: &mcp.ToolAnnotations{Name: …}}`
    # names the annotation, not the tool.
    while field is not None and _brace_depth(source.masked, open_brace, field.start()) != 1:
        field = _GO_STRUCT_NAME_FIELD_RE.search(source.masked, field.end(), close)
    if field is None:
        return []
    found, value, literal_end = source.literal_at(field.end())
    name, unresolved = _resolve_name(value, found)
    if name is not None and not _literal_is_whole_value(source, literal_end, ",}"):
        name, unresolved = None, "name_not_literal"
    line, column = source.line_column(start)
    return [
        RegistrationSite(
            idiom="go_tool_struct",
            name=name,
            line=line,
            column=column,
            span=(start, close),
            description=_go_struct_description(source, open_brace, close),
            unresolved_reason=unresolved,
        )
    ]


def _has_keyed_field(masked: str, open_brace: int, close: int) -> bool:
    """Whether the literal names fields at its own level (a struct, not a list)."""
    for match in _GO_KEYED_FIELD_RE.finditer(masked, open_brace + 1, close):
        if _brace_depth(masked, open_brace, match.start()) == 1:
            return True
    return False


def _child_braces(masked: str, open_brace: int, close: int) -> list[tuple[int, int]]:
    children: list[tuple[int, int]] = []
    index = open_brace + 1
    while index < close - 1:
        if masked[index] == "{":
            child_close = _matching_close(masked, index, "{", "}")
            if child_close is None or child_close > close:
                break
            children.append((index, child_close))
            index = child_close
            continue
        index += 1
    return children


def _go_struct_description(source: MaskedSource, open_brace: int, close: int) -> str | None:
    for match in _GO_STRUCT_DESCRIPTION_FIELD_RE.finditer(
        source.masked, open_brace + 1, close
    ):
        if _brace_depth(source.masked, open_brace, match.start()) != 1:
            continue
        found, value, literal_end = source.literal_at(match.end())
        if found and value and _literal_is_whole_value(source, literal_end, ",}"):
            return value
    return None


def _brace_depth(masked: str, open_brace: int, index: int) -> int:
    depth = 0
    for position in range(open_brace, index):
        char = masked[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    return depth


def scan_source(text: str, language: str) -> SourceScanResult:
    """Find every registration site in one file.

    An unresolved site is dropped when a resolved one sits inside it. The
    wrapper shape is real and common — ``NewTool(metadata, mcp.Tool{Name:
    "issue_read"}, …)`` is 132 of them in ``github/github-mcp-server`` — and
    reporting the wrapper's non-literal first argument as an unenumerated tool
    would fill the ledger with omissions for tools the very same call names one
    argument later.
    """
    if PREFILTER_TOKEN not in text.lower():
        return SourceScanResult()
    source = mask_source(text, language)
    sites: list[RegistrationSite] = []
    if language == "typescript":
        sites.extend(_ts_static_tool_name_sites(source))
        sites.extend(_call_sites(source, _TS_REGISTER_TOOL_RE, "ts_sdk_register_tool"))
    else:
        sites.extend(_call_sites(source, _GO_MUST_TOOL_RE, "go_must_tool"))
        sites.extend(_call_sites(source, _GO_NEW_TOOL_RE, "go_new_tool"))
        sites.extend(_go_tool_struct_sites(source))

    kept = [
        site
        for site in sites
        if site.name is not None or not _contains_another_site(site, sites)
    ]
    kept.sort(key=lambda site: (site.line, site.column, site.idiom))
    return SourceScanResult(sites=tuple(kept), anomalies=source.anomalies)


def _contains_another_site(site: RegistrationSite, sites: list[RegistrationSite]) -> bool:
    """Whether a nested site describes the same registration as ``site``.

    Sound only because every ``span`` is the *construct* that registers. A span
    standing for a lookup *scope* would make any registration written inside
    that scope suppress the site, which is a different relationship entirely.
    """
    start, end = site.span
    return any(
        start < other.span[0] and other.span[1] <= end
        for other in sites
        if other is not site
    )


# --- MCP source discovery (mirror of cli/discovery/mcp_source.py) -----------
#
# Every function below is byte-for-byte the CLI's, with two deliberate
# exceptions a reviewer diffing the two files should expect:
#
#   `_mcp_export_tool_names` — the CLI probes an export by calling the real
#   `load_mcp_tools`, which is a pydantic-backed adapter. Here it is the same
#   accept rule read with `json`, which is also what `_probe_mcp` above already
#   mirrors for the same file.
#
#   `_read_mcp_source_text` — the CLI's read is `inputs.common.load_text_file`
#   (a regular file, at most 10 MB, decoded strict). It is factored out here so
#   the contract is visible in one place: discovery that decoded leniently was
#   a shipped defect, and a port that shared the path predicate but not the
#   read would reintroduce it.

#: How many source files discovery reads before it stops. Truncation is
#: reported, never silent.
DEFAULT_MAX_SOURCE_FILES = 1500

#: How many tool names the evidence names before it summarises. The line is
#: read by a human deciding whether to adopt, and 110 names is not evidence.
_EVIDENCE_NAME_LIMIT = 5

#: Dependency sections of a ``package.json`` that count. ``devDependencies`` is
#: included on purpose: the question is "was this repository written against
#: MCP", not "does it ship the SDK at runtime".
_PACKAGE_JSON_DEPENDENCY_KEYS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


@dataclass
class _FrameworkEvidence:
    """Declared MCP dependencies, per language."""

    languages: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class McpSourceDiscovery:
    """What discovery concluded about the workspace's own registration sites."""

    path: str | None = None
    languages: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    unresolved_count: int = 0
    evidence: tuple[str, ...] = ()
    #: The declared-dependency reasons behind the language gate, on their own.
    #: Scoring reads this rather than indexing into ``evidence``: the rendered
    #: lines are ordered for a human and gain conditional entries at the end.
    framework_evidence: tuple[str, ...] = ()
    candidate_files: tuple[str, ...] = ()
    excluded: tuple[dict[str, str], ...] = ()
    truncated: bool = False

    @property
    def detected(self) -> bool:
        return self.path is not None


def _discover_mcp_server_source(
    workspace: Path,
    files: list[Path],
    exported_source_paths: list[str],
    max_source_files: int = DEFAULT_MAX_SOURCE_FILES,
) -> McpSourceDiscovery:
    """Decide whether this workspace registers MCP tools in its own source.

    Two facts have to hold together, and the pairing is the whole design. A
    **declared MCP dependency** turns a spelling into provenance — a repository
    that declares none is not an MCP server because a class of its own happens
    to spell a field ``toolName``. A **resolved registration** is the other
    half: the dependency alone says the repository uses MCP, which every client
    does too.

    ``exported_source_paths`` are the workspace-relative paths of MCP exports
    already accepted as suggestions. An export that names every registration
    withholds this route: it is the server's own published contract, carries
    the input schemas this route does not read, and is read at high confidence
    against medium.
    """
    workspace = workspace.resolve()
    framework = _mcp_framework_evidence(workspace, files)
    if not framework.languages:
        return McpSourceDiscovery()

    # Paired with the workspace-relative path once, here: a file that is not
    # under the workspace at all (a symlink out of the tree) has no relative
    # form, and inventing one from its basename would put it in the wrong
    # directory for both the skip rules and the route's common ancestor.
    scannable = [
        pair
        for pair in ((path, _mcp_relative(path, workspace)) for path in files)
        if pair[1] is not None
        and _mcp_language_in_scope(pair[1], framework.languages)
    ]
    # Sorted before the cap, so which files are read is a property of the
    # workspace and not of the walk order — and capped where the flag is set,
    # rather than beside it.
    scannable.sort(key=lambda pair: pair[1])
    truncated = len(scannable) > max_source_files
    names: set[str] = set()
    languages: set[str] = set()
    unresolved_by_file: dict[str, int] = {}
    candidate_files: list[str] = []
    for path, relative in scannable[:max_source_files]:
        language = language_for_path(path)
        if language is None:  # pragma: no cover - filtered above
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                continue
        except OSError:
            continue
        text = _read_mcp_source_text(path)
        if text is None:
            continue
        result = scan_source(text, language)
        resolved = [site.name for site in result.sites if site.name is not None]
        opaque = sum(1 for site in result.sites if site.name is None)
        if opaque:
            unresolved_by_file[relative] = opaque
        if not resolved:
            continue
        languages.add(language)
        names.update(resolved)
        candidate_files.append(relative)

    if not names:
        return McpSourceDiscovery(
            unresolved_count=sum(unresolved_by_file.values()), truncated=truncated
        )

    root = _mcp_common_directory(candidate_files)
    # Counted over the directory the route actually points at, so the number
    # this publishes is the number the adapter will report once the route is
    # configured.
    unresolved = sum(
        count
        for relative, count in unresolved_by_file.items()
        if root == "." or PurePosixPath(relative).is_relative_to(root)
    )
    evidence = _mcp_evidence_lines(
        framework, languages, names, root, truncated, unresolved
    )
    covering_export, uncovered = _mcp_covering_export(
        workspace, exported_source_paths, names
    )
    if covering_export is not None:
        return McpSourceDiscovery(
            unresolved_count=unresolved,
            truncated=truncated,
            excluded=(
                {
                    "type": MCP_SOURCE_TYPE,
                    "path": root,
                    "reason": (
                        f"An MCP tool export ({covering_export}) already names "
                        f"every one of these {len(names)} registrations, and an "
                        "export is read at high confidence with its input "
                        "schemas; reading them in source would restate it at "
                        "medium."
                    ),
                },
            ),
        )
    if uncovered:
        # An export exists and does not account for the whole surface. It used
        # to withhold this route anyway, which in a workspace holding two
        # servers meant an export for one erased every source-only registration
        # of the other. Both routes are suggested instead, and the overlap is
        # named.
        sample = ", ".join(sorted(uncovered)[:_EVIDENCE_NAME_LIMIT])
        if len(uncovered) > _EVIDENCE_NAME_LIMIT:
            sample += ", …"
        evidence = (
            *evidence,
            f"An MCP tool export is also present and does not name "
            f"{len(uncovered)} of these registrations ({sample}); both routes "
            "are suggested, and a reviewed tool_identity binding is what joins "
            "the two surfaces",
        )

    return McpSourceDiscovery(
        path=root,
        languages=tuple(sorted(languages)),
        tool_names=tuple(sorted(names)),
        unresolved_count=unresolved,
        evidence=evidence,
        framework_evidence=tuple(sorted(framework.reasons)),
        candidate_files=tuple(sorted(candidate_files)),
        truncated=truncated,
    )


def _read_mcp_source_text(path: Path) -> str | None:
    """The adapter's own read, not a lenient copy of it.

    Decoding with ``errors="replace"`` let ``detect`` resolve a registration
    out of a file the scan-time loader then refuses as ``unreadable_file``, so
    the route promised more tools than it could enumerate. Sharing the path
    predicate is not enough — the read has to be shared too.
    """
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _mcp_framework_evidence(workspace: Path, files: list[Path]) -> _FrameworkEvidence:
    evidence = _FrameworkEvidence()
    for path in files:
        name = path.name
        if name not in {"package.json", "go.mod"}:
            continue
        relative = _mcp_relative(path, workspace)
        if relative is None:
            continue
        # The same skip set the source walk uses, not a narrower one of its
        # own: two lists would let a directory be skipped for registrations
        # while still granting the language gate that admits them.
        parts = PurePosixPath(relative).parts
        if any(part in SKIP_DIRECTORY_NAMES for part in parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name == "go.mod":
            lowered = text.lower()
            for module in GO_FRAMEWORK_MODULES:
                if module in lowered:
                    evidence.languages.add("go")
                    evidence.reasons.append(f"{relative} requires {module}")
                    break
            continue
        package = _mcp_package_dependencies(text)
        for dependency in sorted(package):
            if any(
                dependency.lower().startswith(token)
                for token in TYPESCRIPT_FRAMEWORK_PACKAGES
            ):
                evidence.languages.add("typescript")
                evidence.reasons.append(f"{relative} depends on {dependency}")
                break
    return evidence


def _mcp_package_dependencies(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    names: set[str] = set()
    for key in _PACKAGE_JSON_DEPENDENCY_KEYS:
        section = data.get(key)
        if isinstance(section, dict):
            names.update(str(name) for name in section)
    return names


def _mcp_language_in_scope(relative: str, languages: set[str]) -> bool:
    language = language_for_path(relative)
    if language is None or language not in languages:
        return False
    return is_scannable_path(relative)


def _mcp_relative(path: Path, workspace: Path) -> str | None:
    """The CLI's own relative-path rule for this route, not this script's `_rel`.

    ``_rel`` prefers the *logical* name and falls back to the path itself, which
    is right for the inventory it serves. Here the answer decides the route's
    common ancestor and which skip rules apply, and the CLI resolves — so this
    resolves too. Two spellings of one path is how the two detectors would
    disagree about which directory a manifest should point at.
    """
    try:
        return path.resolve().relative_to(workspace).as_posix()
    except (OSError, ValueError):
        return None


def _mcp_common_directory(relative_files: list[str]) -> str:
    """The deepest directory containing every file that registered a tool.

    Not a scope decision — #363 settled that a deepest-common-ancestor is the
    wrong answer for *which project a manifest describes*. This is the narrower
    question of which subtree the adapter walks, where widening only costs a
    longer walk.
    """
    parts_list = [PurePosixPath(name).parent.parts for name in relative_files]
    if not parts_list:
        return "."
    common = parts_list[0]
    for parts in parts_list[1:]:
        limit = min(len(common), len(parts))
        index = 0
        while index < limit and common[index] == parts[index]:
            index += 1
        common = common[:index]
    return PurePosixPath(*common).as_posix() if common else "."


def _mcp_covering_export(
    workspace: Path, exported_source_paths: list[str], names: set[str]
) -> tuple[str | None, set[str]]:
    """The export that names *every* registration, and what no export names.

    Location is not the test, and neither is mere existence: "any export
    anywhere wins" let an export for one server erase another server's
    source-only registrations, and let a partial export erase the remainder of
    a single one. Only containment makes withholding lossless.
    """
    candidates = sorted(exported_source_paths)
    if not candidates:
        # No export at all — the common case for a source-only server. Without
        # this the caller would report "an MCP tool export is also present and
        # does not name them" about a file that does not exist.
        return None, set()
    covered: set[str] = set()
    for candidate in candidates:
        exported = _mcp_export_tool_names(workspace, candidate)
        if exported is None:
            continue
        covered |= exported
        if names <= covered:
            return candidate, set()
    return None, names - covered


def _mcp_export_tool_names(workspace: Path, relative: str) -> set[str] | None:
    """Tool names an accepted MCP export publishes. Mirrors ``load_mcp_tools``.

    ``None`` when the export declines to name them — a wildcard export claims a
    surface without enumerating it, so it can never be shown to contain
    anything, and the source route is the more informative of the two.
    """
    export = workspace / relative
    try:
        # Bounded like the loader this mirrors: `load_mcp_tools` refuses a file
        # over `MAX_INPUT_FILE_BYTES` before parsing it, so an export past the
        # bound names nothing on either side — and this reader is reached with
        # a path chosen by the workspace, in a script that is curled onto an
        # unknown repository.
        if export.stat().st_size > MAX_STRUCTURED_FILE_BYTES:
            return None
        data = json.loads(export.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if isinstance(data, list):
        raw_tools: Any = data
    elif isinstance(data, dict):
        raw_tools = data.get("tools")
        if data.get("wildcard") is True or raw_tools == "*":
            # Wildcard exposure, and the wildcard-plus-array contradiction the
            # loader refuses outright, both reach the caller the same way:
            # there are no names here to contain anything.
            #
            # No fixture can distinguish this branch from the fall-through
            # below, and that is a fact about the *caller*, not a gap: every
            # wildcard shape `_probe_mcp` accepts also has no usable `tools`
            # array, and the one that does — wildcard plus a populated array —
            # is refused at the probe and never reaches this function. The
            # branch stays because it mirrors `load_mcp_tools`, and resting on
            # the probe to make it redundant would couple this reader to a
            # gate that is not its own.
            return None
    else:
        return None
    if not isinstance(raw_tools, list):
        return None
    return {
        str(entry["name"])
        for entry in raw_tools
        if isinstance(entry, dict) and entry.get("name")
    }


def _mcp_evidence_lines(
    framework: _FrameworkEvidence,
    languages: set[str],
    names: set[str],
    root: str,
    truncated: bool,
    unresolved: int,
) -> tuple[str, ...]:
    sample = sorted(names)[:_EVIDENCE_NAME_LIMIT]
    shown = ", ".join(sample)
    if len(names) > len(sample):
        shown += ", …"
    lines = [
        f"MCP tool registrations in {'/'.join(sorted(languages))} source under "
        f"{root}/: {len(names)} tool name(s) — {shown}",
    ]
    lines.extend(sorted(framework.reasons)[:_EVIDENCE_NAME_LIMIT])
    if unresolved:
        # Named here because this is what a human reads when deciding whether
        # to adopt, and "61 tools" without "and 3 more this reader cannot name"
        # is the over-claim the whole input is built to avoid.
        lines.append(
            f"{unresolved} registration(s) name themselves at runtime and are "
            "not enumerated"
        )
    if truncated:
        lines.append(
            "Discovery stopped at the source-file cap, so this count is a "
            "lower bound."
        )
    return tuple(lines)


def _score_mcp_server_source(
    discovery: McpSourceDiscovery, scores: dict[str, dict[str, Any]]
) -> None:
    """Score the workspace's own MCP registration sites (mirror of signals.py).

    The registration evidence reaches the detection threshold on its own,
    because the fact behind it is already a conjunction: a declared MCP
    dependency *and* a tool name resolved at a registration site. The
    dependency then adds the same point a dependency adds for every other
    framework.

    The candidate file is the **route directory**, not the registration files
    under it: an MCP server is one thing however many packages its tools are
    spread across, and contributing each file made `mongodb-js/mongodb-mcp-server`
    look like six separate projects.
    """
    if not discovery.detected or discovery.path is None:
        return
    _add(scores, MCP_SOURCE_TYPE, 2.0, "strong", discovery.evidence[0])
    # The declared dependency is identified by *value* — the line is one of the
    # discovery result's `framework_evidence` entries. Awarding it to
    # `evidence[1]` read the point off a list position instead, in a list that
    # is ordered for a human and gains conditional entries at the end.
    reasons = set(discovery.framework_evidence)
    awarded = False
    for line in discovery.evidence[1:]:
        dependency = not awarded and line in reasons
        awarded = awarded or dependency
        _add(
            scores,
            MCP_SOURCE_TYPE,
            1.0 if dependency else 0.0,
            "medium" if dependency else "supporting",
            line,
        )
    # `_add`'s candidate argument would attribute the directory to whichever
    # evidence line happened to be last; the route directory belongs to the
    # detection, not to a line of prose about it.
    if discovery.path not in scores[MCP_SOURCE_TYPE]["candidate_files"]:
        scores[MCP_SOURCE_TYPE]["candidate_files"].append(discovery.path)


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _parse_py(path: Path) -> dict[str, Any] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None
    imports, decos, ctors, names = set(), set(), set(), []
    constant_imports: dict[tuple[int, str], tuple[str, int, str]] = {}
    plain_imports: dict[str, str] = {}
    writes: dict[str, list[dict[str, Any]]] = {}
    scopes = _walk_scoped(tree)
    nodes = scopes["nodes"]
    scope_parents, class_scopes = scopes["parents"], scopes["class_scopes"]
    hierarchy = _new_hierarchy(scope_parents, class_scopes, writes)
    attribute_writes: set[str] = set()
    star_linenos: list[int] = []
    agent_calls: list[tuple[ast.Call, int, bool]] = []
    agent_targets: dict[int, ast.Call] = {}
    write_by_node: dict[int, dict[str, Any]] = {}

    global_decls: dict[int, set[str]] = {}
    nonlocal_decls: dict[int, set[str]] = {}
    star_import = False
    pending_calls: list[tuple[ast.Call, str, int, bool]] = []

    def _write(name: str, scope: int, lineno: int, conditional: bool,
               kind: str = "assignment") -> dict[str, Any]:
        entry = {
            "scope": scope, "lineno": lineno, "conditional": conditional,
            "call_id": None, "kind": kind,
        }
        writes.setdefault(name, []).append(entry)
        return entry

    for node, scope, conditional in nodes:
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.add(a.name)
                bound = (a.asname or a.name).split(".")[0]
                _write(bound, scope, node.lineno, conditional, "import")
                # `import a.b.c` binds `a` denoting `a`; `import a.b.c as x`
                # binds `x` denoting `a.b.c`.
                plain_imports[bound] = (
                    a.name if a.asname else a.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
                for a in node.names:
                    imports.add(f"{node.module}.{a.name}")
            for a in node.names:
                if a.name == "*":
                    star_import = True
                    star_linenos.append(node.lineno)
                    continue
                bound = a.asname or a.name
                _write(bound, scope, node.lineno, conditional, "import")
                if node.module or node.level:
                    constant_imports[(scope, bound)] = (
                        node.module or "", node.level, a.name,
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _write(node.name, scope, node.lineno, conditional, "definition")
            for d in node.decorator_list:
                n = _name(d)
                if n:
                    decos.add(n)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            write_by_node[id(node)] = _write(node.id, scope, node.lineno, conditional)
        elif isinstance(node, ast.Attribute) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            dotted = _name(node)
            if dotted:
                attribute_writes.add(dotted)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
            _write(node.id, scope, node.lineno, conditional, "delete")
        elif isinstance(node, ast.arg):
            _write(node.arg, scope, node.lineno, conditional, "parameter")
        elif isinstance(node, ast.ExceptHandler) and node.name:
            _write(node.name, scope, node.lineno, conditional, "except")
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            _write(node.name, scope, node.lineno, conditional, "match")
        elif isinstance(node, ast.MatchMapping) and node.rest:
            _write(node.rest, scope, node.lineno, conditional, "match")
        elif isinstance(node, ast.Global):
            global_decls.setdefault(scope, set()).update(node.names)
        elif isinstance(node, ast.Nonlocal):
            nonlocal_decls.setdefault(scope, set()).update(node.names)
        elif isinstance(node, ast.Call):
            ctor = _name(node.func)
            if ctor:
                ctors.add(ctor)
                pending_calls.append((node, ctor, scope, conditional))

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    agent_targets[id(target)] = node.value

    facts: dict[str, Any] = {
        "imports": imports,
        "decorators": decos,
        "constructors": ctors,
        "names": names,
        "constant_imports": constant_imports,
        "plain_imports": plain_imports,
        "writes": writes,
        "scope_parents": scope_parents,
        "class_scopes": class_scopes,
        "star_import": star_import,
        "star_imports": star_linenos,
        "attribute_writes": attribute_writes,
    }
    hierarchy["star_import"] = star_import
    _apply_scope_declarations(facts, global_decls, nonlocal_decls)

    roles = [
        (call, _constructor_role(ctor, scope, call.lineno, facts), scope, conditional)
        for call, ctor, scope, conditional in pending_calls
    ]
    for call, role, scope, conditional in roles:
        if role == "agent":
            agent_calls.append((call, scope, conditional))
            hierarchy["agent_call_ids"].add(id(call))
    for call, role, scope, conditional in roles:
        if role is not None:
            _observe_call(
                hierarchy, call, role, scope,
                conditional or scopes["declaration_conditional"].get(scope, False),
            )

    for node_id, call in agent_targets.items():
        entry = write_by_node.get(node_id)
        if entry is not None and id(call) in hierarchy["agent_call_ids"]:
            entry["call_id"] = id(call)

    # Roles are assigned only once the whole module has been seen: an
    # App(root_agent=…) binding can appear after the construction it names.
    _resolve_references(hierarchy)
    agent_calls.sort(key=lambda item: (item[0].lineno, item[0].col_offset))
    unresolved_root = ""
    for call, scope, _conditional in agent_calls:
        evidence = _agent_name_evidence(call, scope, hierarchy)
        if evidence is not None:
            names.append(evidence)
        elif (id(call) in hierarchy["root_calls"]
              or id(call) in hierarchy["resolved_root_calls"]):
            unresolved_root = "the application root's name is not a static value"
    if hierarchy["unresolved_root"]:
        unresolved_root = hierarchy["unresolved_root"]
    facts["constants"] = _module_constants(tree, facts)
    facts["unresolved_root"] = unresolved_root
    return facts


_MODULE_SCOPE = 0
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
# Comprehensions have their own scope in Python 3.
_COMPREHENSION_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# Constructs whose bodies may or may not run. An assignment under one of
# these is not provably the binding a later reference sees.
_BRANCH_NODES = tuple(node for node in (
    ast.If, ast.IfExp, ast.Try, getattr(ast, "TryStar", ast.Try),
    ast.While, ast.For, ast.AsyncFor, ast.Match,
))


def _binding_count(facts: dict[str, Any], name: str) -> int:
    return len(facts["writes"].get(name, []))


def _walk_scoped(tree: ast.AST) -> dict[str, Any]:
    """Nodes paired with their lexical scope and whether they run conditionally.

    Definition *headers* — decorators, defaults, annotations, class bases and
    keywords — are walked in the enclosing scope, because that is where Python
    evaluates them. Comprehensions get their own scope, with the outermost
    iterable evaluated outside it. `declaration_conditional` says whether the
    `def`/`class` introducing a scope was itself conditional: the body is
    straight-line relative to itself, but everything it claims is contingent
    on that branch having run.
    """
    out: list[tuple[ast.AST, int, bool]] = []
    parents: dict[int, int] = {}
    class_scopes: set[int] = set()
    declaration_conditional: dict[int, bool] = {_MODULE_SCOPE: False}

    def open_scope(node: ast.AST, scope: int, conditional: bool) -> int:
        inner = id(node)
        parents[inner] = scope
        declaration_conditional[inner] = conditional or declaration_conditional.get(
            scope, False
        )
        return inner

    def visit(node: ast.AST, scope: int, conditional: bool) -> None:
        out.append((node, scope, conditional))
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
            class_scopes.add(inner)
            for header in _definition_header(node):
                visit(header, scope, conditional)
            for statement in node.body:
                visit(statement, inner, False)
            return
        if isinstance(node, _COMPREHENSION_NODES):
            inner = open_scope(node, scope, conditional)
            generators = node.generators
            if generators:
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
    return {
        "nodes": out, "parents": parents, "class_scopes": class_scopes,
        "declaration_conditional": declaration_conditional,
    }


def _definition_header(node: ast.AST) -> list[ast.expr]:
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
        header.extend(d for d in args.kw_defaults if d is not None)
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


def _new_hierarchy(scope_parents: dict[int, int], class_scopes: set[int],
                   writes: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Structural relationships between agent constructions in one module,
    accumulated during the single parse walk, then resolved once the module
    is fully seen. References are matched to the binding that reaches them —
    nearest enclosing scope, latest unconditional assignment before the
    reference — not to every assignment sharing the identifier. ``writes``
    is shared with the caller and holds *every* binding, not just agent
    constructions: a later `root_agent = build_root()` has to be visible or
    a stale construction keeps the role."""
    return {
        "scope_parents": scope_parents,
        # Class bodies are scopes for binding but not for closure lookup.
        "class_scopes": class_scopes,
        "writes": writes,
        "root_refs": [],
        "child_refs": [],
        "root_calls": set(),
        "child_calls": set(),
        "resolved_root_calls": {},
        "resolved_child_calls": {},
        "unresolved_root": "",
        # Calls proven to construct an agent, filled before any App is read.
        "agent_call_ids": set(),
        "star_import": False,
    }


def _observe_call(hierarchy: dict[str, Any], call: ast.Call, role: str,
                  scope: int, conditional: bool) -> None:
    """Record what one call says about the agents around it.

    ``role`` comes from `_constructor_role`, which resolves the callee
    through its binding — a call is only "an agent" or "an app" when the
    spelling provably is one.
    """
    is_app = role == "app"
    is_agent = role == "agent"
    for kw in call.keywords:
        if is_app and kw.arg == ROOT_AGENT_SYMBOL:
            if conditional:
                # Which branch built the app decides which agent is the root,
                # and that is a runtime fact.
                hierarchy["unresolved_root"] = (
                    f"App({ROOT_AGENT_SYMBOL}=…) is constructed under a "
                    "conditional or loop, so which agent is the root is not "
                    "provable statically"
                )
            elif isinstance(kw.value, ast.Name):
                hierarchy["root_refs"].append(
                    (kw.value.id, scope, getattr(kw.value, "lineno", 0))
                )
            elif isinstance(kw.value, ast.Call) and id(kw.value) in hierarchy["agent_call_ids"]:
                hierarchy["root_calls"].add(id(kw.value))
            else:
                # A factory call or any other expression: the root is
                # declared but not statically readable, and dropping that
                # silently is how a sub-agent becomes the declared identity.
                hierarchy["unresolved_root"] = (
                    f"App({ROOT_AGENT_SYMBOL}=…) is given an expression that "
                    "does not statically construct an agent"
                )
        elif is_agent and kw.arg in CHILD_AGENT_KEYWORDS and isinstance(
            kw.value, (ast.List, ast.Tuple, ast.Set)
        ):
            for element in kw.value.elts:
                if isinstance(element, ast.Name):
                    hierarchy["child_refs"].append(
                        (element.id, scope, getattr(element, "lineno", 0))
                    )
                elif isinstance(element, ast.Call) and id(element) in hierarchy["agent_call_ids"]:
                    hierarchy["child_calls"].add(id(element))


def _scope_lookup_chain(scope_parents: dict[int, int], class_scopes: set[int],
                        scope: int) -> list[int]:
    """`scope` and every scope a name lookup falls through to. Class bodies
    are excluded from the ancestors: a method referencing a name bound in its
    class body raises NameError rather than seeing it."""
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


def _reaching_write(writes: dict[str, list[dict[str, Any]]], chain: list[int],
                    name: str, lineno: int) -> tuple[dict[str, Any] | None, str]:
    """The binding a reference at ``lineno`` sees, or why it is unprovable.

    One implementation for both questions that ask it — which agent a symbol
    holds, and whether a constructor spelling is the framework's.

    Within the reference's own scope the latest binding *before* it wins; a
    later one cannot reach backwards, which is what let a framework import at
    the bottom of a file retroactively validate a call above it. In an
    enclosing or module scope the line comparison does not apply — a function
    body executes when called, not where written — so only a single
    unconditional binding is provable there.
    """
    for index, candidate_scope in enumerate(chain):
        found = [w for w in writes.get(name, []) if w["scope"] == candidate_scope]
        if not found:
            continue
        if index == 0:
            reaching = [w for w in found if w["lineno"] < lineno]
            if not reaching:
                continue
            latest = max(reaching, key=lambda w: w["lineno"])
            if latest["conditional"]:
                return None, (
                    f"`{name}` is bound under a conditional or loop, so which "
                    "value reaches this reference is not provable statically"
                )
            return latest, ""
        if any(w["conditional"] for w in found):
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


def _reaching(hierarchy: dict[str, Any], name: str, scope: int,
              lineno: int) -> tuple[dict[str, Any] | None, str]:
    if hierarchy["star_import"]:
        return None, (
            f"a wildcard import can rebind `{name}`, so its value here is not "
            "provable statically"
        )
    chain = _scope_lookup_chain(
        hierarchy["scope_parents"], hierarchy["class_scopes"], scope
    )
    return _reaching_write(hierarchy["writes"], chain, name, lineno)


def _apply_scope_declarations(facts: dict[str, Any], global_decls: dict[int, set[str]],
                              nonlocal_decls: dict[int, set[str]]) -> None:
    """Route writes declared `global`/`nonlocal` to the scope they bind.

    The redirected write is marked conditional: whether the function runs,
    and when relative to the module body, is not something this file says.
    """
    if not (global_decls or nonlocal_decls):
        return
    for name, entries in facts["writes"].items():
        for w in entries:
            if name in global_decls.get(w["scope"], set()):
                w["scope"] = _MODULE_SCOPE
                w["conditional"] = True
            elif name in nonlocal_decls.get(w["scope"], set()):
                enclosing = _enclosing_binding_scope(facts, name, w["scope"])
                if enclosing is not None:
                    w["scope"] = enclosing
                w["conditional"] = True


def _enclosing_binding_scope(facts: dict[str, Any], name: str,
                             scope: int) -> int | None:
    seen = {scope}
    current = facts["scope_parents"].get(scope, _MODULE_SCOPE)
    while current not in seen:
        seen.add(current)
        if current == _MODULE_SCOPE:
            return None
        if any(w["scope"] == current for w in facts["writes"].get(name, [])):
            return current
        current = facts["scope_parents"].get(current, _MODULE_SCOPE)
    return None


def _constructor_role(ctor: str, scope: int, lineno: int,
                      facts: dict[str, Any]) -> str | None:
    """Whether ``ctor`` names an agent constructor, an app, or neither.

    The binding that reaches the *call site* decides. A spelling bound to a
    local `def`/`class`, bound conditionally, or bound only after the call is
    not the framework's. Dotted spellings are held to the same standard: the
    head must prove a framework module, so a local `class fake: class Agent`
    cannot borrow the terminal name. The terminal-name reading survives only
    for a genuinely unbound head.
    """
    parts = ctor.split(".")
    role = _class_role(parts[-1])
    head = parts[0]
    chain = _scope_lookup_chain(
        facts["scope_parents"], facts["class_scopes"], scope
    )
    binding, _reason = _reaching_write(facts["writes"], chain, head, lineno)

    # A wildcard import may replace any name it does not shadow, so a
    # spelling whose binding predates one is no longer proven.
    for star_lineno in facts["star_imports"]:
        if star_lineno < lineno and (
            binding is None or binding["lineno"] < star_lineno
        ):
            return None
    # `adk.Agent = fake` rebinds through the module object, which no name
    # binding records.
    if _attribute_rebound(ctor, facts):
        return None
    if binding is None:
        return role
    if binding["kind"] != "import":
        return None
    module = _imported_module(head, binding, facts)
    if module is None:
        return None
    resolved = ".".join([module, *parts[1:-1]]) if len(parts) > 2 else module
    if len(parts) == 1:
        imported = facts["constant_imports"].get((binding["scope"], head))
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


def _imported_module(head: str, binding: dict[str, Any],
                     facts: dict[str, Any]) -> str | None:
    plain = facts["plain_imports"].get(head)
    if plain is not None:
        return plain
    imported = facts["constant_imports"].get((binding["scope"], head))
    if imported is None:
        return None
    module, _level, original = imported
    return f"{module}.{original}" if module else original


def _attribute_rebound(ctor: str, facts: dict[str, Any]) -> bool:
    return any(
        ctor == path or ctor.startswith(f"{path}.")
        for path in facts["attribute_writes"]
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


def _resolve_references(hierarchy: dict[str, Any]) -> None:
    for name, scope, lineno in hierarchy["root_refs"]:
        write, reason = _reaching(hierarchy, name, scope, lineno)
        if write is None:
            hierarchy["unresolved_root"] = reason
        elif write["call_id"] is None:
            hierarchy["unresolved_root"] = (
                f"`{name}` last binds a value that is not a statically "
                "readable agent construction"
            )
        else:
            hierarchy["resolved_root_calls"].setdefault(
                write["call_id"], f"bound as App({ROOT_AGENT_SYMBOL}={name})"
            )
    for name, scope, lineno in hierarchy["child_refs"]:
        write, _reason = _reaching(hierarchy, name, scope, lineno)
        if write is not None and write["call_id"] is not None:
            hierarchy["resolved_child_calls"].setdefault(
                write["call_id"], f"listed in another agent's children as `{name}`"
            )
    # The ADK convention: the `root_agent` that `adk run`/`adk web` discover
    # is the *module* symbol. A function-local of that name is just a local.
    module_level = [
        w for w in hierarchy["writes"].get(ROOT_AGENT_SYMBOL, [])
        if w["scope"] == _MODULE_SCOPE
    ]
    if not module_level:
        return
    if hierarchy["star_import"]:
        hierarchy["unresolved_root"] = (
            f"a wildcard import can rebind `{ROOT_AGENT_SYMBOL}`, so which agent "
            "it holds is not provable statically"
        )
        return
    if any(w["conditional"] for w in module_level):
        hierarchy["unresolved_root"] = (
            f"`{ROOT_AGENT_SYMBOL}` is assigned conditionally, so which agent "
            "it holds is not provable statically"
        )
        return
    last = max(module_level, key=lambda w: w["lineno"])
    if last["call_id"] is None:
        hierarchy["unresolved_root"] = (
            f"`{ROOT_AGENT_SYMBOL}` is last assigned a value that is not a "
            "statically readable agent construction"
        )
        return
    hierarchy["resolved_root_calls"].setdefault(
        last["call_id"],
        f"assigned to the conventional `{ROOT_AGENT_SYMBOL}` module symbol",
    )


def _agent_name_evidence(call: ast.Call, scope: int,
                         hierarchy: dict[str, Any]) -> dict[str, Any] | None:
    value = None
    for kw in call.keywords:
        if kw.arg == "name":
            value = kw.value
            break
    if value is None:
        return None
    if id(call) in hierarchy["root_calls"]:
        role, why = "root_agent", f"constructed inline as App({ROOT_AGENT_SYMBOL}=…)"
    elif id(call) in hierarchy["resolved_root_calls"]:
        role, why = "root_agent", hierarchy["resolved_root_calls"][id(call)]
    elif id(call) in hierarchy["child_calls"]:
        role, why = "sub_agent", "constructed inline inside another agent's children"
    elif id(call) in hierarchy["resolved_child_calls"]:
        role, why = "sub_agent", hierarchy["resolved_child_calls"][id(call)]
    else:
        role, why = "agent", ""
    if isinstance(value, ast.Constant):
        if not isinstance(value.value, str) or not value.value.strip():
            return None
        return {
            "literal": value.value.strip(), "symbol": None,
            "role": role, "why": why, "scope": scope,
        }
    if isinstance(value, ast.Name):
        return {
            "literal": None, "symbol": value.id,
            "role": role, "why": why, "scope": scope,
        }
    return None


def _is_stdlib_env_lookup(callee: str, facts: dict[str, Any]) -> bool:
    """Whether ``callee`` provably names ``os.getenv``/``os.environ.get``.

    Matching the spelling is not proof of provenance: a module defining its
    own ``getenv(key, fallback)`` would have its fallback lifted out as the
    agent identity. The binding must be an unshadowed stdlib import."""
    parts = callee.split(".")
    head = parts[0]
    if _binding_count(facts, head) != 1:
        return False
    # `os.getenv = fake` replaces the lookup without rebinding any name.
    if _attribute_rebound(callee, facts) or facts["star_import"]:
        return False
    if facts["plain_imports"].get(head) == "os":
        return parts[1:] in (["getenv"], ["environ", "get"])
    imported = facts["constant_imports"].get((_MODULE_SCOPE, head))
    if imported is None:
        return False
    module, level, original = imported
    if module != "os" or level != 0:
        return False
    return (original == "getenv" and not parts[1:]) or (
        original == "environ" and parts[1:] == ["get"]
    )


def _module_constants(tree: ast.AST,
                      facts: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Module-level ``NAME = <str>`` and ``NAME = os.environ.get(…, <str>)``.

    Module level, these two forms, and only for names bound exactly once in
    the whole file. That last rule is what makes the other two safe: any
    second write — later, conditional, computed, or in a function — means
    the value Python passes is not the one visible here, so the name stays
    unresolved and the caller fails closed.
    """
    constants: dict[str, tuple[str, str]] = {}
    if facts["star_import"]:
        return constants
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        constant = _static_string(value, facts)
        if constant is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and _binding_count(facts, target.id) == 1:
                constants[target.id] = constant
    return constants


def _static_string(node: ast.AST, facts: dict[str, Any]) -> tuple[str, str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip():
        return (node.value.strip(), "module_constant")
    if isinstance(node, ast.Call):
        if _is_stdlib_env_lookup(_name(node.func) or "", facts) and len(node.args) == 2:
            default = node.args[1]
            if (isinstance(default, ast.Constant) and isinstance(default.value, str)
                    and default.value.strip()):
                return (default.value.strip(), "env_default")
    return None


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_test_path(rel: str) -> bool:
    parts = Path(rel).parts
    if any(part in {"test", "tests"} for part in parts[:-1]):
        return True
    stem = Path(rel).name
    return (stem in TEST_MODULE_NAMES or stem.startswith("test_")
            or stem.endswith("_test.py"))


def _non_product_origin(rel: str) -> str | None:
    """Why ``rel`` is not the product's own code, or ``None`` — mirror of
    ``cli/discovery/signals.py:_non_product_origin``.

    One predicate, two uses: the score penalty below and the
    application-root block. A test fixture that builds an ``App`` is a
    fixture, and a file under a scaffolding ``resources/templates/``
    directory is what a generator copies. The returned string is the
    rationale line the penalty publishes.
    """
    if _is_test_path(rel):
        return "declared in test code, which names fixtures rather than the product"
    # Case-folded: `Resources/Templates` is the same directory on a
    # case-insensitive checkout, and an exact match let the symptom survive
    # the spelling difference.
    parts = tuple(part.lower() for part in Path(rel).parts[:-1])
    if any(
        parts[index:index + len(sequence)] == sequence
        for sequence in NON_PRODUCT_DIR_SEQUENCES
        for index in range(len(parts))
    ):
        return ("declared under a scaffolding template directory, which names "
                "an example rather than the product")
    return None


def _can_declare_application_root(rel: str) -> bool:
    """Whether a root declared in ``rel`` speaks for the product.

    Neither a fixture nor a template is the application whose identity the
    project ships, and reading either as one rejected every real agent in
    the repository (#398).
    """
    return _non_product_origin(rel) is None


def _unresolvable_root_rejection(scope: str, detail: str, *,
                                 declared_elsewhere: bool) -> str:
    """Why no name a project declares may be written as its identity.

    ``declared_elsewhere`` covers a value whose best-ranked site sits in a
    clean project while a blocked project also declares it: every other
    published field points at the clean project, so the relationship that
    justifies the rejection has to be stated.
    """
    where = "this workspace" if scope == "." else f"project `{scope}`"
    opening = (f"this name is also declared in {where}, which"
               if declared_elsewhere else where)
    return (
        f"{opening} declares an application root whose name is not statically "
        f"resolvable ({detail}); every other name it declares is by "
        "construction not that root, so none of them can be the reviewed "
        "identity"
    )


def _constant_module_paths(importer: Path, module: str, level: int,
                           workspace: Path) -> list[Path]:
    """Every in-workspace file the import could refer to. All of them, not
    the first hit: the caller has to see a disagreement to fail closed on
    it. Packages precede modules, as Python's own finder orders them."""
    parts = [p for p in module.split(".") if p]
    bases: list[Path] = []
    if level:
        base = importer.parent
        for _ in range(level - 1):
            base = base.parent
        bases.append(base)
    else:
        bases.extend((importer.parent, workspace))
    resolved: list[Path] = []
    for base in bases:
        suffixes = [(*parts, "__init__.py")]
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


def _resolve_agent_name(evidence: dict[str, Any], path: Path, facts: dict[str, Any],
                        by_path: dict[Path, dict[str, Any]],
                        workspace: Path) -> tuple[str, str, str] | None:
    """Resolve one evidence site to ``(value, provenance, detail)``.

    A bare symbol resolves through **one** hop: a module-level constant in
    the same file, or one in a module this file imports the symbol from
    directly. The hop never chains, the target module must already be a
    parsed file inside the workspace, and every rule fails to ``None``
    rather than guessing.
    """
    if evidence["literal"] is not None:
        return evidence["literal"], "literal", ""
    symbol = evidence["symbol"]
    if not symbol:
        return None
    if _binding_count(facts, symbol) != 1:
        return None
    local = facts["constants"].get(symbol)
    if local is not None:
        return local[0], local[1], _rel(path, workspace)
    imported = (
        facts["constant_imports"].get((evidence["scope"], symbol))
        or facts["constant_imports"].get((_MODULE_SCOPE, symbol))
    )
    if imported is None:
        return None
    module, level, original = imported
    found: list[tuple[str, str, str]] = []
    for candidate in _constant_module_paths(path, module, level, workspace):
        target = by_path.get(candidate)
        if target is None:
            continue
        constant = target["constants"].get(original)
        if constant is not None:
            found.append((constant[0], constant[1], _rel(candidate, workspace)))
    if not found or len({v for v, _, _ in found}) > 1:
        # Nothing found, or two supported execution roots disagree: which
        # one Python picks depends on sys.path, which is not ours to assume.
        return None
    return found[0]


def _rank_agent_names(py_facts: list[tuple[Path, dict[str, Any]]], workspace: Path,
                      project_names: list[dict[str, str]],
                      attribution: _ProjectAttribution) -> list[dict[str, Any]]:
    """Rank ``Agent(name=…)`` evidence best-first — mirror of
    ``cli/discovery/signals.py:_rank_agent_name_candidates``.

    Source order is not a ranking. Hierarchy (an application root outranks a
    declared sub-agent), origin (product code outranks test code), and
    corroboration by the project name decide the order; a value too short or
    too generic to be an identity is ranked last and made unselectable so
    ``init`` writes CHANGE_ME rather than asserting something unreliable.

    A project whose application root cannot be resolved has nothing
    selectable, and that rule is scoped to the project: with repository
    scope one unresolvable root in a scaffolding template rejected every
    real agent in a monorepo (#398). A name several projects declare is
    rejected when any of them is blocked — the fail-closed direction.
    """
    by_path: dict[Path, dict[str, Any]] = {}
    for path, facts in py_facts:
        try:
            by_path[path.resolve()] = facts
        except (OSError, RuntimeError):
            by_path[path] = facts
    project_forms = {_normalise_name(c["value"]) for c in project_names if c["value"]}
    project_forms.add(_normalise_name(workspace.name))
    project_forms.discard("")

    # Project → the first unreadable application root found in it. Only a
    # root the product itself declares blocks a project.
    blocked_projects: dict[str, str] = {}

    def _block(path: Path, rel: str, detail: str) -> None:
        if not _can_declare_application_root(rel):
            return
        blocked_projects.setdefault(attribution.of(path)[0], detail)

    for path, facts in py_facts:
        if facts["unresolved_root"]:
            rel = _rel(path, workspace)
            _block(path, rel, f"{rel}: {facts['unresolved_root']}")

    best: dict[str, dict[str, Any]] = {}
    # Every project a value is declared in, not only its best-scoring site.
    # `best_project` is the project of the site `best` kept — the one every
    # other published field of the candidate points at.
    declared_in: dict[str, list[str]] = {}
    best_project: dict[str, str] = {}
    order = 0
    for path, facts in py_facts:
        if not facts["names"]:
            # Most files declare no agent; attributing them to a project is
            # a stat and a walk for an answer nothing below reads.
            continue
        rel = _rel(path, workspace)
        # Invariant across the evidence in one file, so it is asked once
        # per file rather than once per name.
        project = attribution.of(path)[0]
        for evidence in facts["names"]:
            resolved = _resolve_agent_name(evidence, path, facts, by_path, workspace)
            if resolved is None:
                continue
            value, provenance, detail = resolved
            score = 1.0
            rationale = [f"declared as Agent(name=…) in {rel}"]
            if provenance == "module_constant":
                rationale.append(f"resolved from a module constant in {detail}")
            elif provenance == "env_default":
                rationale.append(
                    f"resolved from the static default of an environment lookup in "
                    f"{detail} — overridable at runtime"
                )
            role = evidence["role"]
            if role == "root_agent":
                score += ROOT_AGENT_BONUS
                rationale.append(evidence["why"] or "bound as the application root")
            elif role == "sub_agent":
                score -= SUB_AGENT_PENALTY
                rationale.append(
                    evidence["why"] or "declared as a child of another agent, not the root"
                )
            origin = _non_product_origin(rel)
            if origin is not None:
                # Larger than every other signal combined: a fixture or a
                # scaffolding template that happens to build an App root is
                # still not the product.
                score -= ORIGIN_NON_PRODUCT_PENALTY
                rationale.append(origin)
            normalised = _normalise_name(value)
            if normalised and normalised in project_forms:
                score += CORROBORATION_BONUS
                rationale.append("corroborated by the project name")
            selectable = True
            if len(normalised) < AGENT_NAME_MIN_LENGTH:
                score -= QUALITY_FLOOR_PENALTY
                selectable = False
                rationale.append(
                    f"rejected: fewer than {AGENT_NAME_MIN_LENGTH} significant "
                    "characters, too context-poor to assert as an identity"
                )
            elif normalised in GENERIC_AGENT_NAME_VALUES:
                score -= QUALITY_FLOOR_PENALTY
                selectable = False
                rationale.append("rejected: generic scaffolding name, not an identity")
            ranked = {
                "value": value,
                "source": "Agent_name_literal",
                "role": role,
                "path": rel,
                "rank_score": round(score, 2),
                "selectable": selectable,
                "rationale": rationale,
                "_order": order,
            }
            order += 1
            projects = declared_in.setdefault(value, [])
            if project not in projects:
                projects.append(project)
            previous = best.get(value)
            if previous is None or ranked["rank_score"] > previous["rank_score"]:
                best[value] = ranked
                best_project[value] = project

    # A root whose *name* is a symbol that fails cross-module resolution is
    # just as unresolved as one whose name is an f-string; it surfaces here
    # because resolution needs every file's constants.
    for path, facts in py_facts:
        for evidence in facts["names"]:
            if evidence["role"] != "root_agent" or evidence["literal"] is not None:
                continue
            if _resolve_agent_name(evidence, path, facts, by_path, workspace) is None:
                rel = _rel(path, workspace)
                _block(
                    path,
                    rel,
                    f"{rel}: the application root's name comes from "
                    f"`{evidence['symbol']}`, which does not resolve to a "
                    "static value",
                )
    for value, ranked in best.items():
        # The candidate's own project first when it is blocked, so the
        # sentence names the project every other field already points at.
        blocking = sorted(
            (p for p in declared_in[value] if p in blocked_projects),
            key=lambda p: p != best_project[value],
        )
        if not blocking:
            continue
        ranked["selectable"] = False
        ranked["rationale"].append(
            "rejected: "
            + _unresolvable_root_rejection(
                blocking[0],
                blocked_projects[blocking[0]],
                declared_elsewhere=blocking[0] != best_project[value],
            )
        )

    ordered = sorted(
        best.values(),
        key=lambda r: (not r["selectable"], -r["rank_score"], r["_order"]),
    )
    candidates = [{k: v for k, v in r.items() if k != "_order"} for r in ordered]
    if workspace.name and workspace.name not in best:
        candidates.append({
            "value": workspace.name,
            "source": "workspace_dir",
            "role": "workspace_dir",
            "path": None,
            "rank_score": 0.0,
            "selectable": False,
            "rationale": [
                "directory name, not a declared agent identity — reported for "
                "reference, never written as agent.name"
            ],
        })
    return candidates


def _package_tokens(workspace: Path) -> list[str]:
    tokens: list[str] = []
    for fname in ("pyproject.toml", "requirements.txt"):
        path = workspace / fname
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if fname == "pyproject.toml":
            for line in content.splitlines():
                s = line.strip().strip(",").strip("'\"")
                for sep in ("==", ">=", "<=", "~=", ">", "<"):
                    if sep in s:
                        s = s.split(sep, 1)[0]
                        break
                s = s.strip().strip('"\'')
                if s and re.fullmatch(r"[A-Za-z0-9_.\-]+", s):
                    tokens.append(s)
        else:
            tokens.extend(m.group(1) for m in REQ_TOKEN_RE.finditer(content))
    return tokens


def _add(scores: dict[str, dict[str, Any]], fw: str, pts: float, cls: str,
         evidence: str, candidate: str | None = None) -> None:
    s = scores[fw]
    s["score"] += pts
    if cls == "strong":
        s["has_strong"] = True
    s["evidence"].append(evidence)
    if candidate and candidate not in s["candidate_files"]:
        s["candidate_files"].append(candidate)


def _confidence(score: float) -> str:
    return "high" if score >= 4.0 else "medium" if score >= 2.5 else "low"


def _local_marketplace_roots(workspace: Path, paths: list[Path]) -> set[Path]:
    """Resolve contained local plugin roots without loading plugin contents."""
    roots: set[Path] = set()
    for path in paths:
        try:
            path.resolve().relative_to(workspace)
            if path.stat().st_size > MAX_STRUCTURED_FILE_BYTES:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        entries = payload.get("plugins") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            source = entry.get("source") if isinstance(entry, dict) else None
            name = entry.get("name") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(source, dict) or source.get("source") != "local":
                continue
            raw_path = source.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            candidate = Path(raw_path)
            try:
                root = (
                    candidate if candidate.is_absolute() else workspace / candidate
                ).resolve()
                root.relative_to(workspace)
            except (OSError, RuntimeError, ValueError):
                continue
            try:
                plugin_manifest = (root / ".codex-plugin" / "plugin.json").resolve()
                plugin_manifest.relative_to(workspace)
            except (OSError, RuntimeError, ValueError):
                continue
            if plugin_manifest.is_file():
                roots.add(root)
    return roots


def _conventional_dir_locations(
    workspace: Path, files: list[Path]
) -> dict[str, str]:
    """Mirror of ``signals._conventional_dir_locations``.

    Byte parity of *behaviour* with the canonical implementation, which is what
    ``tests/test_zero_install_detector.py`` pins: a conventional directory is
    located anywhere in the tree, one entry per directory *name*, shallowest
    occurrence, reported as a workspace-relative POSIX path. A root directory
    is spelled as its bare name.

    The root ``is_dir`` check stays because the inventory is a list of *files*
    and an empty ``prompts/`` has no entry in it. Parents are deduplicated and
    compared as strings for the same reason the canonical version does it:
    ``relative_to`` per inventory entry cost 4.4 s on 120k files (#441).
    """

    located: dict[str, str] = {
        name: name for name in CONVENTIONAL_DIRS if (workspace / name).is_dir()
    }
    wanted = {name for name in CONVENTIONAL_DIRS if name not in located}
    if not wanted:
        return located
    prefix = f"{str(workspace).rstrip(os.sep)}{os.sep}"
    prefix_length = len(prefix)
    seen_directories: set[str] = set()
    shallowest: dict[str, tuple[int, str]] = {}
    for path in files:
        text = str(path)
        if not text.startswith(prefix):
            continue
        cut = text.rfind(os.sep)
        if cut < prefix_length:
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


def detect(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    files = _inventory(workspace)
    all_py = [p for p in files if p.suffix == ".py"]
    py_files = all_py[:MAX_PYTHON_FILES]
    # Whether the cap cut the parse short. A scope verdict computed from part
    # of a repository is a verdict about part of a repository.
    py_truncated = len(all_py) > len(py_files)
    py_facts = [(p, f) for p in py_files if (f := _parse_py(p)) is not None]

    scores = {fw: {"score": 0.0, "has_strong": False, "evidence": [], "candidate_files": []}
              for fw in FRAMEWORKS}

    for path, f in py_facts:
        rel = _rel(path, workspace)
        imp, dec, ctr = f["imports"], f["decorators"], f["constructors"]
        if imp & LANGCHAIN_IMPORTS:
            _add(scores, "langchain", 2.0, "strong", f"{rel}: langchain import", rel)
        if "tool" in dec and any(m in imp for m in LANGCHAIN_DECORATOR_MODULES):
            _add(scores, "langchain", 2.0, "strong", f"{rel}: @tool from langchain", rel)
        if ctr & LANGCHAIN_AGENT_CALLS:
            _add(scores, "langchain", 2.0, "strong", f"{rel}: langchain agent call", rel)
        if imp & CREWAI_IMPORTS:
            _add(scores, "crewai", 2.0, "strong", f"{rel}: crewai import", rel)
        if "tool" in dec and any(m in imp for m in CREWAI_DECORATOR_MODULES):
            _add(scores, "crewai", 2.0, "strong", f"{rel}: @tool from crewai", rel)
        if any(c.split(".")[-1] in CREWAI_CLASSES for c in ctr) and (imp & CREWAI_IMPORTS):
            _add(scores, "crewai", 2.0, "strong", f"{rel}: crewai class call", rel)
        if any(m.startswith("google.adk") for m in imp):
            _add(scores, "google_adk", 2.0, "strong", f"{rel}: google.adk import", rel)
            if any(c.split(".")[-1] in GOOGLE_ADK_CLASSES for c in ctr):
                _add(scores, "google_adk", 2.0, "strong",
                     f"{rel}: google.adk class call", rel)
        if imp & ANTHROPIC_IMPORTS or any(m.startswith("anthropic.") for m in imp):
            _add(scores, "anthropic", 2.0, "strong", f"{rel}: anthropic import", rel)
        if imp & OPENAI_AGENTS_SDK_IMPORTS:
            _add(scores, "openai_agents_sdk", 2.0, "strong",
                 f"{rel}: openai-agents import", rel)
        if dec & OPENAI_AGENTS_SDK_DECORATORS:
            _add(scores, "openai_agents_sdk", 2.0, "strong",
                 f"{rel}: @function_tool decorator", rel)

    for token in _package_tokens(workspace):
        for fw, hints in PACKAGE_HINTS.items():
            if token.lower() in {h.lower() for h in hints}:
                _add(scores, fw, 1.0, "medium", f"dependency declared: {token}")

    for p in _glob(workspace, files, ANTHROPIC_TOOL_PATTERNS):
        _add(scores, "anthropic", 2.0, "strong", f"anthropic tool file: {p}", p)
    for p in _glob(workspace, files, ANTHROPIC_POLICY_PATTERNS):
        _add(scores, "anthropic", 2.0, "strong", f"anthropic policy file: {p}", p)
    for pattern, label in OPENAI_API_PATTERNS:
        for p in _glob(workspace, files, (pattern,)):
            _add(scores, "openai_api", 2.0, "strong", f"{label}: {p}", p)
    for p in _glob(workspace, files, N8N_WORKFLOW_PATTERNS):
        if _looks_like_n8n_workflow(workspace / p):
            _add(scores, "n8n", 2.0, "strong", f"n8n workflow: {p}", p)
    for p in _glob(workspace, files, CONDUCTOR_WORKFLOW_PATTERNS):
        workflow_path = workspace / p
        # Same bound as the Conductor adapter's ``load_structured_file``:
        # a workflow `scan` refuses to read must not score `conductor` here.
        if _oversized(workflow_path):
            continue
        try:
            data = json.loads(workflow_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        markers = _conductor_agent_markers(data)
        if markers:
            _add(
                scores,
                "conductor",
                2.0,
                "strong",
                f"Conductor AI/MCP workflow: {p} ({', '.join(sorted(markers))})",
                p,
            )

    conventional_locations = _conventional_dir_locations(workspace, files)
    present_dirs = [
        conventional_locations[d] for d in CONVENTIONAL_DIRS if d in conventional_locations
    ]
    for fw in CONVENTIONAL_DIR_FRAMEWORKS:
        for d in present_dirs:
            _add(scores, fw, 0.5, "weak", f"conventional dir: {d}/")

    # Glob candidates, then keep only the ones the input adapters accept —
    # a glob hit (e.g. an mcpServers-style host config matching *mcp*.json)
    # that fails the probe would make the next init->scan step fail, so it
    # is reported under excluded_sources instead. Mirrors signals.py.
    candidates: list[tuple[str, str]] = []
    seen_cand: set[tuple[str, str]] = set()
    for kind, patterns in (
        ("openapi", OPENAPI_PATTERNS),
        ("mcp", MCP_PATTERNS),
        ("conductor", CONDUCTOR_WORKFLOW_PATTERNS),
    ):
        for p in _glob(workspace, files, patterns):
            if kind == "mcp" and Path(p).name == ".mcp.json":
                continue
            if (kind, p) in seen_cand:
                continue
            seen_cand.add((kind, p))
            candidates.append((kind, p))

    suggested: list[dict[str, str]] = []
    suggested_paths: set[str] = set()
    failures: list[dict[str, str]] = []
    for kind, p in candidates:
        if p in suggested_paths:
            continue
        reason = _probe_suggested(workspace, p, kind)
        if reason is None:
            suggested.append({"type": kind, "path": p})
            suggested_paths.add(p)
        else:
            failures.append({"type": kind, "path": p, "reason": reason})
    excluded = [e for e in failures if e["path"] not in suggested_paths]

    # The artifact probe runs before the detection loop because the source
    # route below is scored from its result: an MCP export is the better route
    # to the same server, so this one stands down wherever one exists (#431).
    mcp_source = _discover_mcp_server_source(
        workspace,
        files,
        [s["path"] for s in suggested if s["type"] == "mcp"],
    )
    _score_mcp_server_source(mcp_source, scores)
    excluded.extend(mcp_source.excluded)
    if mcp_source.path is not None:
        suggested.append({"type": MCP_SOURCE_TYPE, "path": mcp_source.path})

    detections: list[dict[str, Any]] = [
        {
            "type": fw,
            "score": round(st["score"], 2),
            "confidence": _confidence(st["score"]),
            "evidence": st["evidence"],
            "candidate_files": st["candidate_files"],
        }
        for fw, st in scores.items()
        if st["score"] >= 2.0 and st["has_strong"]
    ]
    detections.sort(key=lambda d: (-d["score"], d["type"]))

    project_names: list[dict[str, str]] = []
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        m = PYPROJECT_NAME_RE.search(text)
        if m:
            project_names.append({"value": m.group(1).strip(), "source": "pyproject"})
    project_names.append({"value": workspace.name, "source": "workspace_dir"})

    marketplace_paths = [
        path
        for path in files
        if path.name == "marketplace.json"
        and path.parent.as_posix().endswith(".agents/plugins")
    ]
    marketplace_roots = _local_marketplace_roots(workspace, marketplace_paths)
    codex_plugin_candidates: list[dict[str, str]] = []
    seen_codex: set[tuple[str, str]] = set()
    for path in files:
        rel = _rel(path, workspace)
        if path.name == "plugin.json" and path.parent.name == ".codex-plugin":
            try:
                path.resolve().relative_to(workspace)
            except (OSError, RuntimeError, ValueError):
                continue
            root = path.parent.parent
            try:
                if root.resolve() in marketplace_roots:
                    continue
            except (OSError, RuntimeError):
                pass
            root_rel = _rel(root, workspace)
            key = ("package", root_rel)
            if key not in seen_codex:
                seen_codex.add(key)
                codex_plugin_candidates.append(
                    {
                        "mode": "package",
                        "path": root_rel,
                        "evidence": f"Codex plugin manifest: {rel}",
                    }
                )
        elif path.name == "marketplace.json" and path.parent.as_posix().endswith(
            ".agents/plugins"
        ):
            key = ("marketplace", rel)
            if key not in seen_codex:
                seen_codex.add(key)
                codex_plugin_candidates.append(
                    {
                        "mode": "marketplace",
                        "path": rel,
                        "evidence": f"Codex plugin marketplace: {rel}",
                    }
                )

    is_agent = bool(detections)
    codex_plugin_candidates = sorted(
        codex_plugin_candidates, key=lambda item: (item["mode"], item["path"])
    )

    # Manifest scope: which directory one shipgate.yaml describes. Evidence is
    # everything init would turn into a manifest — the files the frameworks
    # fired on, the artifact sources, nested manifests somebody already
    # scoped by hand — grouped by the project each one sits in.
    # Literals only, matching `_PyFacts.name_literals()` on the CLI side. A
    # name that needs cross-module resolution is not available per-file, and
    # a project boundary is about *where* agents live, not what they are
    # called.
    literals_by_path = {
        rel: literals
        for rel, literals in (
            (
                _rel(p, workspace),
                list(
                    dict.fromkeys(
                        e["literal"] for e in f["names"] if e["literal"] is not None
                    )
                ),
            )
            for p, f in py_facts
        )
        if literals
    }
    evidence_paths = list(
        dict.fromkeys(
            [rel for d in detections for rel in d["candidate_files"]]
            + [s["path"] for s in suggested]
            + [c["path"] for c in codex_plugin_candidates]
            + [
                _rel(p, workspace)
                for p in files
                if p.name == "shipgate.yaml" and p.parent != workspace
            ]
            + list(literals_by_path)
        )
    )
    attribution = _ProjectAttribution(workspace, evidence_paths)
    agent_project_candidates = _agent_project_candidates(
        workspace, evidence_paths, literals_by_path, attribution
    )
    # Ranking runs after the grouping because it reads the same attribution:
    # an application root it cannot resolve disqualifies the names in *that
    # project* and no others (#398).
    name_candidates = _rank_agent_names(
        py_facts, workspace, project_names, attribution
    )
    # The workspace is always a candidate scope, marker or not: agent evidence
    # under no marker is attributed to it as ".", so an unmarked root agent in
    # the part of the tree the parse never reached is a project this census has
    # to leave room for (#399 review).
    project_roots = {
        p.parent for p in files
        if p.name in PROJECT_MARKERS or p.name in WEAK_PROJECT_MARKERS
    } | {workspace}
    # Truncation is decided before ambiguity and reported alongside it. Two
    # projects found is an ambiguous scope however much of the tree was read;
    # what a cut-short parse changes is that the candidate list is a lower
    # bound, not an enumeration. Folding the two into one value made the cap
    # warning unreachable on the repositories the cap had actually cut (#395).
    python_file_total = sum(1 for p in files if p.suffix == ".py")
    agent_scope_truncated = py_truncated and len(project_roots) > 1
    if len(agent_project_candidates) > 1:
        agent_scope = "ambiguous"
    elif agent_scope_truncated:
        agent_scope = "unknown"
    else:
        agent_scope = "single"

    if agent_scope == "ambiguous":
        next_action = (
            f"Agents were found in {len(agent_project_candidates)} separate "
            "projects; this workspace is not one manifest's scope. Run "
            "`init --workspace <agent_project_candidates[].path> --write` for "
            "the project you are changing."
        )
        if agent_scope_truncated:
            next_action += (
                " That list may be incomplete: discovery stopped at the "
                f"Python-file cap in a workspace holding {len(project_roots)} "
                "candidate project scopes, so any project in the part of the "
                "tree that was not read is missing from it. Run "
                "`agents-shipgate detect --max-python-files <n> --json` "
                "before concluding a project is absent."
            )
    elif agent_scope == "unknown":
        next_action = (
            "Discovery stopped at the Python-file cap in a workspace holding "
            f"{len(project_roots)} candidate project scopes, so whether one "
            "manifest describes it was not established. Run `agents-shipgate "
            "detect --max-python-files <n> --json` for the full picture, or "
            "init in the project directory you are changing."
        )
    elif py_truncated:
        # A settled scope is not a complete classification: a one-project
        # workspace is "single" however early the parse stopped, so a capped
        # run fell through to init or to the flat negative — the terminal
        # false answer for an agent sitting past the cap (#399 review).
        next_action = (
            "Discovery stopped at the Python-file cap, so this classification "
            "describes the part of the workspace that was read. Re-run "
            f"`agents-shipgate detect --max-python-files {python_file_total} "
            "--json` — a bound that covers every Python file — before treating "
            "any verdict here as complete."
        )
    elif is_agent or suggested or codex_plugin_candidates:
        next_action = f"agents-shipgate init --workspace {workspace}"
    else:
        next_action = "Workspace does not appear to be an agent project. No action."

    return {
        "is_agent_project": is_agent,
        "frameworks": detections,
        "agent_name_candidates": name_candidates,
        "project_name_candidates": project_names,
        "agent_scope": agent_scope,
        "agent_project_candidates": agent_project_candidates,
        "agent_scope_truncated": agent_scope_truncated,
        # The raw parse-completeness fact, independent of how many scopes the
        # workspace holds. Whole-workspace negatives gate on this, not on the
        # scope flag: a single-scope repository whose only agent sits past the
        # cap has `agent_scope_truncated: false` and an unread agent.
        "python_parse_truncated": py_truncated,
        "suggested_sources": suggested,
        "excluded_sources": excluded,
        "codex_plugin_candidates": codex_plugin_candidates,
        "next_action": next_action,
        "workspace_signals": {
            "python_file_count": len(py_facts),
            "python_file_total": python_file_total,
            "project_root_count": len(project_roots),
            "has_pyproject_or_requirements": (
                (workspace / "pyproject.toml").is_file()
                or (workspace / "requirements.txt").is_file()
            ),
            "has_prompts_dir": "prompts" in conventional_locations,
            "has_tools_dir": "tools" in conventional_locations,
            "conventional_dirs": present_dirs,
        },
        "script_version": SCRIPT_VERSION,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="shipgate-detect",
        description="Zero-install Agents Shipgate detector.",
    )
    parser.add_argument("--workspace", default=".", type=Path)
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON. Default: human-readable summary.")
    args = parser.parse_args(argv)
    try:
        result = detect(args.workspace)
    except DiscoveryError as exc:
        # Canonical discovery fails here rather than falling back to an
        # unbounded walk; a "successful" verdict built from a different
        # inventory would be worse than no verdict.
        print(f"shipgate-detect: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if result["python_parse_truncated"]:
        # Before any verdict, because every verdict below it is a claim about
        # the whole workspace and the parse read part of one (#399 review).
        print("Classification incomplete — the Python parse stopped at its cap.")
        print(f"Next: {result['next_action']}")
        return 0
    if result["agent_scope"] != "single":
        candidates = result["agent_project_candidates"]
        print(f"Agent scope: {result['agent_scope']} — one shipgate.yaml describes")
        print("one agent surface, and this workspace is not one scope:")
        for c in candidates[:10]:
            detail = ", ".join(c["agent_names"]) or (c["marker"] or "project root")
            print(f"- {c['path']} ({detail})")
        if len(candidates) > 10:
            print(f"- ... ({len(candidates) - 10} more; see --json)")
        print(f"\nNext: {result['next_action']}")
        return 0
    if not result["is_agent_project"]:
        print("Workspace does not appear to be an agent project.")
        if result["suggested_sources"]:
            print("Suggested sources (artifact-only):")
            for s in result["suggested_sources"]:
                print(f"- {s['type']}: {s['path']}")
        _print_excluded(result["excluded_sources"])
        if result["codex_plugin_candidates"]:
            print("Codex plugin candidates:")
            for c in result["codex_plugin_candidates"]:
                print(f"- {c['mode']}: {c['path']}")
        return 0
    print("Detected agent project. Frameworks:")
    for fw in result["frameworks"]:
        print(f"- {fw['type']} (score={fw['score']}, confidence={fw['confidence']})")
    if result["suggested_sources"]:
        print("\nSuggested sources:")
        for s in result["suggested_sources"]:
            print(f"- {s['type']}: {s['path']}")
    _print_excluded(result["excluded_sources"])
    print(f"\nNext: pipx install agents-shipgate && {result['next_action']}")
    return 0


def _print_excluded(excluded: list[dict[str, str]]) -> None:
    if not excluded:
        return
    print("\nExcluded sources (scan cannot parse these as tool sources):")
    for s in excluded:
        print(f"- {s['type']}: {s['path']} — {s['reason']}")


if __name__ == "__main__":
    sys.exit(main())
