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
- Descriptive (not byte-identical) ``evidence`` / ``reason`` strings.
- Absolute scores may differ by ±0.5 in edge cases.
- The parse probe is **JSON-only** (stdlib has no YAML parser). A
  ``.json`` candidate the input adapters would reject is excluded here
  too; a ``.yaml`` / ``.yml`` OpenAPI spec is kept as a suggestion
  unconditionally (never wrongly dropped). The real-world miss this
  guards against — ``mcpServers``-style host configs — is always JSON,
  so the probe is exact where it matters.

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
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "0.3.0"
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
ROOT_AGENT_SYMBOL = "root_agent"
CHILD_AGENT_KEYWORDS = ("sub_agents", "handoffs")
ENV_LOOKUP_CALLS = {"os.environ.get", "os.getenv", "environ.get", "getenv"}
AGENT_NAME_MIN_LENGTH = 3
GENERIC_AGENT_NAME_VALUES = frozenset({
    "agent", "agents", "bar", "baz", "changeme", "dummy", "example", "foo",
    "myagent", "name", "placeholder", "qux", "sample", "temp", "test",
    "tests", "tmp", "todo", "untitled",
})
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


def _contained(path: Path, workspace: Path) -> Path | None:
    """The resolved path, or ``None`` if it does not live in the workspace.

    A symlink pointing outside is not part of the workspace no matter what
    its name suggests. Canonical detection drops these; ranking a name out
    of one would also leak the outside absolute path into the output.
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
    return resolved


def _git_files(workspace: Path) -> list[Path] | None:
    """The workspace inventory as Git sees it, or ``None`` if Git cannot.

    Canonical detection prefers this, and matching it is what makes the
    ranking parity claim true: without it a `.gitignore`d file is invisible
    to `init` but visible here, so the two would name different agents.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_ATTR_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat",
        "GIT_PROTOCOL_FROM_USER": "0", "GIT_TERMINAL_PROMPT": "0",
    })
    def _run(args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
        try:
            return subprocess.run(
                ["git", "--no-replace-objects", "-C", str(workspace), *args],
                check=False, capture_output=True, env=env, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    root = _run(["rev-parse", "--show-toplevel"])
    if root is None or root.returncode != 0:
        return None
    try:
        git_root = Path(root.stdout.decode("utf-8").strip()).resolve()
    except (UnicodeDecodeError, OSError, RuntimeError, ValueError):
        return None
    listed = _run([
        "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false",
        "-c", "core.quotePath=false",
        "ls-files", "-co", "--exclude-standard", "--full-name", "-z", "--", ".",
    ])
    if listed is None or listed.returncode != 0:
        return None
    if len(listed.stdout) > MAX_GIT_INVENTORY_BYTES:
        return None
    out: list[Path] = []
    for raw in listed.stdout.split(b"\0"):
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
    out: list[Path] = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".venv")
        ]
        for fn in files:
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


def _rel(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
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


def _looks_like_n8n_workflow(path: Path) -> bool:
    """Match the CLI heuristic in cli/discovery/artifacts.py: a JSON file
    is an n8n workflow when it (or any element in a list) is a dict with
    a ``nodes`` list and ``connections`` dict, and at least one node has
    a ``type`` starting with ``n8n-nodes-`` or ``@n8n/n8n-nodes-``."""
    if path.suffix.lower() != ".json":
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
    """
    path = workspace / rel
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
    counts: dict[str, int] = {}
    hierarchy = _new_hierarchy()
    agent_calls: list[tuple[ast.Call, int]] = []

    def _count(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for node, scope in _walk_scoped(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.add(a.name)
                _count((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
                for a in node.names:
                    imports.add(f"{node.module}.{a.name}")
            for a in node.names:
                bound = a.asname or a.name
                _count(bound)
                if node.module or node.level:
                    constant_imports[(scope, bound)] = (
                        node.module or "", node.level, a.name,
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _count(node.name)
            for d in node.decorator_list:
                n = _name(d)
                if n:
                    decos.add(n)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            _count(node.id)
        elif isinstance(node, ast.arg):
            _count(node.arg)
        elif isinstance(node, ast.Global):
            for n in node.names:
                _count(n)
        elif isinstance(node, ast.Call):
            ctor = _name(node.func)
            if ctor:
                ctors.add(ctor)
                tail = ctor.split(".")[-1]
                _observe_call(hierarchy, node, tail, scope)
                if tail in AGENT_NAME_CLASSES:
                    agent_calls.append((node, scope))

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    hierarchy["bindings"].setdefault(target.id, []).append(
                        {
                            "call_id": id(node.value),
                            "scope": scope,
                            "lineno": node.value.lineno,
                        }
                    )

    # Roles are assigned only once the whole module has been seen: an
    # App(root_agent=…) binding can appear after the construction it names.
    _resolve_references(hierarchy)
    agent_calls.sort(key=lambda item: (item[0].lineno, item[0].col_offset))
    unresolved_root = ""
    for call, scope in agent_calls:
        evidence = _agent_name_evidence(call, scope, hierarchy)
        if evidence is not None:
            names.append(evidence)
        elif id(call) in hierarchy["resolved_root_calls"]:
            unresolved_root = "the application root's name is not a static value"
    if hierarchy["dangling_root"]:
        unresolved_root = (
            f"App({ROOT_AGENT_SYMBOL}=…) names `{hierarchy['dangling_root']}`, "
            "which no single agent construction defines"
        )
    return {
        "imports": imports,
        "decorators": decos,
        "constructors": ctors,
        "names": names,
        "constants": _module_constants(tree, counts),
        "constant_imports": constant_imports,
        "binding_counts": counts,
        "unresolved_root": unresolved_root,
    }


_MODULE_SCOPE = 0
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _walk_scoped(tree: ast.AST) -> list[tuple[ast.AST, int]]:
    """Every node paired with the lexical scope enclosing it.

    ``ast.walk`` flattens scopes, which is wrong for anything modelling name
    binding: a helper's local ``root_agent`` is not the module's, and a
    helper's local import is not the one a module-level construction reads.
    """
    out: list[tuple[ast.AST, int]] = []
    stack: list[tuple[ast.AST, int]] = [(tree, _MODULE_SCOPE)]
    while stack:
        node, scope = stack.pop()
        out.append((node, scope))
        inner = id(node) if isinstance(node, _SCOPE_NODES) else scope
        for child in ast.iter_child_nodes(node):
            stack.append((child, inner))
    return out


def _new_hierarchy() -> dict[str, Any]:
    """Structural relationships between agent constructions in one module,
    accumulated during the single parse walk, then resolved once the module
    is fully seen. References are matched to the *reaching* assignment —
    nearest scope, latest line before the reference — not to every
    assignment sharing the identifier. Call nodes are keyed by ``id()``."""
    return {
        "bindings": {},
        "root_refs": [],
        "child_refs": [],
        "root_calls": set(),
        "child_calls": set(),
        "resolved_root_calls": {},
        "resolved_child_calls": {},
        "dangling_root": "",
    }


def _observe_call(hierarchy: dict[str, Any], call: ast.Call, tail: str,
                  scope: int) -> None:
    """Record what one call says about the agents around it."""
    is_app = tail in APP_ROOT_CLASSES
    is_agent = tail in AGENT_NAME_CLASSES
    if not (is_app or is_agent):
        # A child list only means "these are my sub-agents" when the
        # surrounding call actually builds an agent; an unrelated
        # `configure(handoffs=[coordinator])` must not demote anything.
        return
    for kw in call.keywords:
        if is_app and kw.arg == ROOT_AGENT_SYMBOL:
            if isinstance(kw.value, ast.Name):
                hierarchy["root_refs"].append(
                    (kw.value.id, scope, getattr(kw.value, "lineno", 0))
                )
            elif isinstance(kw.value, ast.Call) and _constructs_agent(kw.value):
                hierarchy["root_calls"].add(id(kw.value))
            else:
                # A factory call or any other expression: the root is
                # declared but not statically readable, and dropping that
                # silently is how a sub-agent becomes the declared identity.
                hierarchy["dangling_root"] = "a non-static expression"
        elif is_agent and kw.arg in CHILD_AGENT_KEYWORDS and isinstance(
            kw.value, (ast.List, ast.Tuple, ast.Set)
        ):
            for element in kw.value.elts:
                if isinstance(element, ast.Name):
                    hierarchy["child_refs"].append(
                        (element.id, scope, getattr(element, "lineno", 0))
                    )
                elif isinstance(element, ast.Call):
                    hierarchy["child_calls"].add(id(element))


def _constructs_agent(call: ast.Call) -> bool:
    ctor = _name(call.func)
    return bool(ctor) and ctor.split(".")[-1] in AGENT_NAME_CLASSES


def _reaching(hierarchy: dict[str, Any], name: str, scope: int,
              lineno: int) -> dict[str, Any] | None:
    """The assignment a reference to ``name`` sees at ``lineno``: same scope
    first, then the module scope a nested reference falls through to; within
    a scope the latest assignment before the reference wins."""
    candidates = hierarchy["bindings"].get(name, [])
    for candidate_scope in (scope, _MODULE_SCOPE):
        reaching = [
            b for b in candidates
            if b["scope"] == candidate_scope and b["lineno"] < lineno
        ]
        if reaching:
            return max(reaching, key=lambda b: b["lineno"])
    return None


def _resolve_references(hierarchy: dict[str, Any]) -> None:
    for name, scope, lineno in hierarchy["root_refs"]:
        binding = _reaching(hierarchy, name, scope, lineno)
        if binding is None:
            hierarchy["dangling_root"] = name
            continue
        hierarchy["resolved_root_calls"].setdefault(
            binding["call_id"], f"bound as App({ROOT_AGENT_SYMBOL}={name})"
        )
    for name, scope, lineno in hierarchy["child_refs"]:
        binding = _reaching(hierarchy, name, scope, lineno)
        if binding is not None:
            hierarchy["resolved_child_calls"].setdefault(
                binding["call_id"], f"listed in another agent's children as `{name}`"
            )
    # The ADK convention: the `root_agent` that `adk run`/`adk web` discover
    # is the *module* symbol. A function-local of that name is just a local.
    module_level = [
        b for b in hierarchy["bindings"].get(ROOT_AGENT_SYMBOL, [])
        if b["scope"] == _MODULE_SCOPE
    ]
    if module_level:
        last = max(module_level, key=lambda b: b["lineno"])
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


def _module_constants(tree: ast.AST,
                      binding_counts: dict[str, int]) -> dict[str, tuple[str, str]]:
    """Module-level ``NAME = <str>`` and ``NAME = os.environ.get(…, <str>)``.

    Module level, these two forms, and only for names bound exactly once in
    the whole file. That last rule is what makes the other two safe: any
    second write — later, conditional, computed, or in a function — means
    the value Python passes is not the one visible here, so the name stays
    unresolved and the caller fails closed.
    """
    constants: dict[str, tuple[str, str]] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        constant = _static_string(value)
        if constant is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and binding_counts.get(target.id, 0) == 1:
                constants[target.id] = constant
    return constants


def _static_string(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip():
        return (node.value.strip(), "module_constant")
    if isinstance(node, ast.Call):
        if (_name(node.func) or "") in ENV_LOOKUP_CALLS and len(node.args) == 2:
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
    return stem == "conftest.py" or stem.startswith("test_") or stem.endswith("_test.py")


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
    if facts["binding_counts"].get(symbol, 0) != 1:
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
                score += 3.0
                rationale.append(evidence["why"] or "bound as the application root")
            elif role == "sub_agent":
                score -= 1.5
                rationale.append(
                    evidence["why"] or "declared as a child of another agent, not the root"
                )
            if _is_test_path(rel):
                score -= 2.0
                rationale.append(
                    "declared in test code, which names fixtures rather than the product"
                )
            normalised = _normalise_name(value)
            if normalised and normalised in project_forms:
                score += 1.0
                rationale.append("corroborated by the project name")
            selectable = True
            if len(normalised) < AGENT_NAME_MIN_LENGTH:
                score -= 3.0
                selectable = False
                rationale.append(
                    f"rejected: fewer than {AGENT_NAME_MIN_LENGTH} significant "
                    "characters, too context-poor to assert as an identity"
                )
            elif normalised in GENERIC_AGENT_NAME_VALUES:
                score -= 3.0
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


def detect(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    files = _inventory(workspace)
    py_files = [p for p in files if p.suffix == ".py"][:MAX_PYTHON_FILES]
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
        _add(scores, "anthropic", 2.0, "strong", f"anthropic tool file: {p}")
    for p in _glob(workspace, files, ANTHROPIC_POLICY_PATTERNS):
        _add(scores, "anthropic", 2.0, "strong", f"anthropic policy file: {p}")
    for pattern, label in OPENAI_API_PATTERNS:
        for p in _glob(workspace, files, (pattern,)):
            _add(scores, "openai_api", 2.0, "strong", f"{label}: {p}")
    for p in _glob(workspace, files, N8N_WORKFLOW_PATTERNS):
        if _looks_like_n8n_workflow(workspace / p):
            _add(scores, "n8n", 2.0, "strong", f"n8n workflow: {p}")
    for p in _glob(workspace, files, CONDUCTOR_WORKFLOW_PATTERNS):
        try:
            data = json.loads((workspace / p).read_text(encoding="utf-8"))
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
            )

    present_dirs = [d for d in CONVENTIONAL_DIRS if (workspace / d).is_dir()]
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
    return {
        "is_agent_project": is_agent,
        "frameworks": detections,
        "agent_name_candidates": name_candidates,
        "project_name_candidates": project_names,
        "suggested_sources": suggested,
        "excluded_sources": excluded,
        "codex_plugin_candidates": sorted(
            codex_plugin_candidates, key=lambda item: (item["mode"], item["path"])
        ),
        "next_action": (
            f"agents-shipgate init --workspace {workspace}"
            if is_agent or suggested or codex_plugin_candidates
            else "Workspace does not appear to be an agent project. No action."
        ),
        "workspace_signals": {
            "python_file_count": len(py_facts),
            "has_pyproject_or_requirements": (
                (workspace / "pyproject.toml").is_file()
                or (workspace / "requirements.txt").is_file()
            ),
            "has_prompts_dir": "prompts" in present_dirs,
            "has_tools_dir": "tools" in present_dirs,
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
    result = detect(args.workspace)
    if args.json:
        print(json.dumps(result, indent=2))
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
