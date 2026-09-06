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
- **No ``mcp_server_source`` detection.** The installed CLI reads an MCP
  server's tool names out of TypeScript or Go registration sites through a
  built-in idiom registry (#431); this script does not, so a repository whose
  tool surface exists only as code is reported here as *not* an agent project
  while the CLI reports it as one. That is the largest divergence in this list
  and the only one that changes ``is_agent_project``. No sample exercises it
  today, so the parity test cannot see it; ``test_framework_vocabulary_names_every_cli_omission``
  pins it instead, and it is filed as #485.
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
- Every structured candidate is size-gated at ``MAX_STRUCTURED_FILE_BYTES``
  before it is read, matching the ``MAX_INPUT_FILE_BYTES`` refusal the input
  adapters apply ahead of their own parse. This is both a bound on what an
  unknown workspace can make this script allocate and a parity rule: an
  oversized ``*mcp*.json`` is excluded by the CLI, so suggesting it here
  would send an agent to write a manifest entry ``scan`` rejects.

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
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "0.4.0"
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
FRAMEWORKS = (
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
# Origin is meant to dominate hierarchy and corroboration, so the test
# penalty is strictly greater than their whole spread (3.0 + 1.0 + 1.5).
# Conventional test module filenames that carry no `test_` prefix.
TEST_MODULE_NAMES = frozenset({"conftest.py", "test.py", "tests.py"})
ROOT_AGENT_BONUS = 3.0
SUB_AGENT_PENALTY = 1.5
CORROBORATION_BONUS = 1.0
ORIGIN_TEST_PENALTY = 6.0
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


def _agent_project_candidates(
    workspace: Path,
    evidence_paths: list[str],
    literals_by_path: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Group agent evidence by the project each piece of it sits in.

    Same rule as the canonical CLI: a workspace whose agents live in more
    than one self-contained project is not one manifest's scope.
    """
    names: dict[str, set[str]] = {}
    markers: dict[str, str | None] = {}
    evidence_dirs = frozenset(
        (workspace / rel) if (workspace / rel).is_dir() else (workspace / rel).parent
        for rel in evidence_paths
    )
    for rel in evidence_paths:
        found = _project_of(workspace / rel, workspace, evidence_dirs)
        project, marker = (
            found
            if found is not None
            else (".", _project_marker(workspace, WEAK_PROJECT_MARKERS))
        )
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
                      project_names: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Rank ``Agent(name=…)`` evidence best-first — mirror of
    ``cli/discovery/signals.py:_rank_agent_name_candidates``.

    Source order is not a ranking. Hierarchy (an application root outranks a
    declared sub-agent), origin (product code outranks test code), and
    corroboration by the project name decide the order; a value too short or
    too generic to be an identity is ranked last and made unselectable so
    ``init`` writes CHANGE_ME rather than asserting something unreliable.
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

    best: dict[str, dict[str, Any]] = {}
    order = 0
    for path, facts in py_facts:
        rel = _rel(path, workspace)
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
            if _is_test_path(rel):
                # Larger than every other signal combined: a fixture that
                # happens to build an App root is still a fixture.
                score -= ORIGIN_TEST_PENALTY
                rationale.append(
                    "declared in test code, which names fixtures rather than the product"
                )
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
            previous = best.get(value)
            if previous is None or ranked["rank_score"] > previous["rank_score"]:
                best[value] = ranked

    unresolved = [
        f"{_rel(p, workspace)}: {f['unresolved_root']}"
        for p, f in py_facts
        if f["unresolved_root"]
    ]
    # A root whose *name* is a symbol that fails cross-module resolution is
    # just as unresolved as one whose name is an f-string; it surfaces here
    # because resolution needs every file's constants.
    for path, facts in py_facts:
        for evidence in facts["names"]:
            if evidence["role"] != "root_agent" or evidence["literal"] is not None:
                continue
            if _resolve_agent_name(evidence, path, facts, by_path, workspace) is None:
                unresolved.append(
                    f"{_rel(path, workspace)}: the application root's name comes "
                    f"from `{evidence['symbol']}`, which does not resolve to a "
                    "static value"
                )
    if unresolved:
        # A declared application root whose identity is not statically
        # resolvable. Anything still standing is by construction not the
        # root, so nothing may be selected.
        blocked = (
            "an application root is declared here but its name is not statically "
            f"resolvable ({unresolved[0]}); any other name would declare a worker "
            "as the reviewed identity"
        )
        for ranked in best.values():
            ranked["selectable"] = False
            ranked["rationale"].append(f"rejected: {blocked}")

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
    for fw in FRAMEWORKS:
        for d in present_dirs:
            _add(scores, fw, 0.5, "weak", f"conventional dir: {d}/")

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

    name_candidates = _rank_agent_names(py_facts, workspace, project_names)

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
    agent_project_candidates = _agent_project_candidates(
        workspace, evidence_paths, literals_by_path
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
