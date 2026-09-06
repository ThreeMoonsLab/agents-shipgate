from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import threading
from pathlib import Path

from agents_shipgate.cli.discovery.manifest_scaffold import (
    MINIMAL_SCAFFOLD_DETAIL,
    MINIMAL_SCAFFOLD_SUMMARY,
    RenderedManifest,
    ToolSurfaceOrigin,
    scaffold_tool_sources_block,
)
from agents_shipgate.cli.discovery.source_ids import assign_source_ids
from agents_shipgate.core.control_packs import (
    DEFAULT_CONTROL_PACK_ID,
    manifest_control_pack_block,
)
from agents_shipgate.core.errors import DiscoveryError

OPENAPI_PATTERNS = (
    "*openapi*.yaml",
    "*openapi*.yml",
    "*openapi*.json",
    "*swagger*.yaml",
    "*swagger*.yml",
    "*swagger*.json",
)
MCP_PATTERNS = (
    "*mcp*.json",
    ".agents-shipgate/*.json",
)
PROMPT_PATTERNS = ("prompts/*.md", "prompts/*.txt")
OPENAI_TOOL_PATTERNS = ("tools/*openai*tools*.json",)
RESPONSE_SCHEMA_PATTERNS = ("schemas/*.schema.json",)
MODEL_CONFIG_PATTERNS = ("openai-config.json",)
TEST_CASE_PATTERNS = ("tests/*openai*cases*.json", "tests/*api*cases*.json")
TRACE_SAMPLE_PATTERNS = ("traces/*.json", "traces/*.jsonl")
POLICY_RULE_PATTERNS = ("policies/*openai*.yaml", "policies/*api*.yaml")
ANTHROPIC_TOOL_PATTERNS = (
    "tools/*anthropic*tools*.json",
    "tools/anthropic-tools.json",
)
ANTHROPIC_POLICY_PATTERNS = (
    "policies/*anthropic*.yaml",
    "policies/anthropic-policy.yaml",
)
N8N_WORKFLOW_PATTERNS = (
    "workflows/*.json",
    "workflows/**/*.json",
    "n8n/*.json",
    "n8n/**/*.json",
    "*workflow*.json",
)
CONDUCTOR_WORKFLOW_PATTERNS = (
    "workflows/*.json",
    "workflows/**/*.json",
    "conductor/*.json",
    "conductor/**/*.json",
    "ai/examples/*.json",
    "ai/examples/**/*.json",
    "*workflow*.json",
)
N8N_CREDENTIAL_STUB_PATTERNS = (
    "credentials/*.json",
    "credentials/**/*.json",
    "n8n/credentials/*.json",
    "n8n/credentials/**/*.json",
)
N8N_VARIABLE_STUB_PATTERNS = (
    "variables.json",
    "variables/*.json",
    "variables/**/*.json",
    "n8n/variables.json",
    "n8n/variables/*.json",
    "n8n/variables/**/*.json",
)
N8N_DATA_TABLE_SCHEMA_PATTERNS = (
    "data-tables/*.json",
    "data-tables/**/*.json",
    "n8n/data-tables/*.json",
    "n8n/data-tables/**/*.json",
)
N8N_EVAL_SET_PATTERNS = (
    "evaluations/*.json",
    "evaluations/**/*.json",
    "n8n/evaluations/*.json",
    "n8n/evaluations/**/*.json",
)
SKIP_DIRS = {
    ".agents-private",
    ".cache",
    ".claude",
    ".direnv",
    ".git",
    ".hg",
    ".nox",
    ".svn",
    ".mypy_cache",
    ".next",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".tox",
    ".venv",
    "__pycache__",
    "agents-shipgate-reports",
    "build",
    "dist",
    "env",
    "fixtures",
    "_fixtures",
    "__fixtures__",
    "golden",
    "goldens",
    "node_modules",
    "target",
    "test-fixtures",
    "test_fixtures",
    "test_data",
    "testdata",
    "venv",
}
SKIP_DIR_PREFIXES = (".venv",)

#: Pre-parse size refusal, mirroring ``inputs.common.MAX_INPUT_FILE_BYTES``.
#: Every adapter reads through ``read_static_input_bytes``, which refuses an
#: oversized input before the loader sees a byte, so a file above this bound
#: can be neither a tool source nor an n8n/Conductor workflow whatever it
#: contains. Discovery must answer that the same way — without reading the
#: file whole to find out, and without suggesting or scoring off it.
#: Re-declared rather than imported so discovery stays loader-free for
#: framework scoring (see ``probe_suggested_source``);
#: ``test_zero_install_detector`` pins this constant, ``MAX_INPUT_FILE_BYTES``
#: and the zero-install script's copy to one value.
MAX_STRUCTURED_FILE_BYTES = 10 * 1024 * 1024

#: The keys that anchor an ``openai_api:`` block. A bare ``prompts/`` directory
#: does not: an Anthropic-only project has one too, so emitting the block on it
#: would declare a framework nobody uses. The auto renderer has always gated on
#: exactly these four; the ``--minimal`` renderer now asks the same question
#: through the same tuple instead of testing the artifact *dict*, which has
#: fixed keys and is therefore always truthy — so its ``CHANGE_ME`` fallback
#: was unreachable and every source-less workspace got an empty ``openai_api:``
#: block, producing a manifest the schema rejects (found by #441's scaffold
#: tests).
OPENAI_API_ANCHOR_KEYS: tuple[str, ...] = (
    "tools",
    "model_config",
    "test_cases",
    "policy_rules",
)



def discover_manifest_paths(workspace: Path) -> list[Path]:
    return _candidate_files_matching(workspace, ("shipgate.yaml",))


def probe_suggested_source(workspace: Path, rel_path: str, source_type: str) -> str | None:
    """Parse-check a suggested tool source with the real input adapter.

    Returns ``None`` when the adapter accepts the file, else a one-line
    reason why ``scan`` would reject it with an input-parse error (exit 3).
    Suggestion rules are filename globs, and filenames lie: a Cursor plugin
    ``mcp.json`` is an ``mcpServers``-style host config, not an MCP
    tools-array export. ``init`` must never write a ``tool_sources`` entry
    that fails this probe — the documented cold-start flow is
    ``init --write`` → ``scan``, and one unparseable entry breaks it out of
    the box. Probing with the adapters themselves (rather than a parallel
    shape classifier) keeps the gate exactly as strict as ``scan``.
    """
    # Lazy imports: discovery stays loader-free for framework *scoring*
    # (see signals.py module docstring); the probe is the one deliberate
    # adapter touchpoint, used only on glob-matched candidate files.
    from agents_shipgate.core.errors import InputParseError
    from agents_shipgate.schemas.manifest import ToolSourceConfig

    if source_type == "mcp":
        from agents_shipgate.inputs.mcp import load_mcp_tools as loader
    elif source_type == "openapi":
        from agents_shipgate.inputs.openapi import load_openapi_tools as loader
    elif source_type == "conductor":
        from agents_shipgate.inputs.common import load_structured_file
        from agents_shipgate.inputs.conductor import conductor_agent_task_types

        try:
            data = load_structured_file((workspace / rel_path).resolve())
        except InputParseError as exc:
            return _probe_failure_reason(workspace, rel_path, source_type, str(exc))
        if not conductor_agent_task_types(data):
            return "not a Conductor AI/MCP workflow JSON document"
        return None
    else:
        return None
    source = ToolSourceConfig(id=f"probe_{source_type}", type=source_type, path=rel_path)
    try:
        loader(source, workspace)
    except InputParseError as exc:
        return _probe_failure_reason(workspace, rel_path, source_type, str(exc))
    except Exception as exc:  # noqa: BLE001 - a loader bug must downgrade the
        # suggestion, not crash detect/init; scan would crash on it anyway.
        return f"{type(exc).__name__}: {exc}"
    return None


def _oversized_structured_input(path: Path) -> bool:
    """Whether ``path`` is above the adapters' pre-parse size bound.

    A ``stat`` that fails is not an oversize answer — an unreadable candidate
    is reported by the caller's own read-error handling, which says what is
    actually wrong with it.
    """

    try:
        return path.stat().st_size > MAX_STRUCTURED_FILE_BYTES
    except OSError:
        return False


def _probe_failure_reason(
    workspace: Path, rel_path: str, source_type: str, message: str
) -> str:
    if source_type == "mcp" and not _oversized_structured_input(workspace / rel_path):
        # Guarded by size because this sniff re-reads the whole file — the
        # very file the adapter may have just refused for being too large. An
        # oversized candidate keeps the size message: an ``mcpServers``-shaped
        # one would otherwise be excluded under a reason that hides the cause,
        # and the zero-install mirror, which cannot read it at all, would then
        # name a different one.
        try:
            data = json.loads((workspace / rel_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = None
        # Same key pair the host-boundary surface reads; these files are
        # host MCP *configuration*, which scan's mcp adapter never accepts.
        if isinstance(data, dict) and any(
            isinstance(data.get(key), dict) for key in ("mcpServers", "servers")
        ):
            return (
                "mcpServers-style MCP server config (host configuration), "
                "not an MCP tools-array export"
            )
    resolved = str((workspace / rel_path).resolve())
    return message.replace(resolved, rel_path)


def discover_tool_sources(workspace: Path) -> list[dict[str, str]]:
    """Glob OpenAPI/MCP candidates and keep only files the real input
    adapters accept. A glob hit that fails the parse probe (e.g. an
    ``mcpServers``-style host config matching ``*mcp*.json``) is dropped:
    writing it would guarantee a ``scan`` input-parse failure."""
    found: list[tuple[str, str]] = []  # (type, relative path)
    seen: set[Path] = set()
    rejected: set[Path] = set()
    for pattern in OPENAPI_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            if path in seen or path in rejected:
                continue
            rel = _relative(path, workspace)
            if probe_suggested_source(workspace, rel, "openapi") is not None:
                rejected.add(path)
                continue
            seen.add(path)
            found.append(("openapi", rel))
    for pattern in MCP_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            if path.name == ".mcp.json":
                continue
            if path in seen:
                continue
            rel = _relative(path, workspace)
            if probe_suggested_source(workspace, rel, "mcp") is not None:
                continue
            seen.add(path)
            found.append(("mcp", rel))
    for pattern in CONDUCTOR_WORKFLOW_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            if path in seen:
                continue
            rel = _relative(path, workspace)
            if probe_suggested_source(workspace, rel, "conductor") is not None:
                continue
            seen.add(path)
            found.append(("conductor", rel))
    # Ids come from the whole relative path and are assigned for the set,
    # so two services that both ship ``openapi.yaml`` no longer render one
    # id twice — a manifest the schema rejects (#307).
    return [
        {"id": source_id, "type": source_type, "path": rel}
        for (source_type, rel), source_id in zip(found, assign_source_ids(found), strict=True)
    ]


def render_manifest_template(
    workspace: Path,
    *,
    control_pack: str = DEFAULT_CONTROL_PACK_ID,
) -> RenderedManifest:
    """The pre-v0.6 ``--minimal`` template, and the provenance of its sources.

    Same contract as ``template.render_auto_manifest``: the caller is told
    whether the tool surface was read or scaffolded, because this renderer has
    the same fallback and had the same unflagged ``type: openapi`` in it. It is
    also the route ``init`` publishes when the auto render fails validation, so
    leaving the guess here would have handed the recovery path the very defect
    #441 reported.
    """

    sources = discover_tool_sources(workspace)
    api_artifacts = discover_openai_api_artifacts(workspace)
    has_api_artifacts = any(
        api_artifacts[key] for key in OPENAI_API_ANCHOR_KEYS
    )
    lines = [
        "# yaml-language-server: $schema=https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/manifest-v0.1.json",
        "# Agents Shipgate starter manifest.",
        "# Review CHANGE_ME values, then add policy entries for write/high-risk tools.",
        'version: "0.1"',
        "",
        "project:",
        f"  name: {workspace.name}",
        "",
        "agent:",
        "  name: CHANGE_ME",
        "  declared_purpose:",
        "    - CHANGE_ME",
        "  prohibited_actions: []",
        "",
        "environment:",
        "  target: local",
        "",
    ]
    tool_surface_origin: ToolSurfaceOrigin = "detected"
    if sources:
        lines.append("# Detected local MCP/OpenAPI sources:")
        lines.append("tool_sources:")
        for source in sources:
            lines.extend(
                [
                    f"  - id: {source['id']}",
                    f"    type: {source['type']}",
                    f"    path: {source['path']}",
                ]
            )
    elif not has_api_artifacts:
        tool_surface_origin = "scaffold"
        # This renderer probed artifact globs and nothing else. It has not
        # looked at a single Python import, so it may not report that none was
        # found: `detect` says `langchain` for a workspace this scaffolds.
        lines.extend(
            scaffold_tool_sources_block(
                summary=MINIMAL_SCAFFOLD_SUMMARY, detail=MINIMAL_SCAFFOLD_DETAIL
            )
        )
    if has_api_artifacts:
        lines.extend(["", "# Detected simple OpenAI API artifacts:", "openai_api:"])
        if api_artifacts["prompt_files"]:
            lines.append("  prompt_files:")
            lines.extend(f"    - {path}" for path in api_artifacts["prompt_files"])
        if api_artifacts["tools"]:
            lines.append("  tools:")
            lines.extend(f"    - path: {path}" for path in api_artifacts["tools"])
        if api_artifacts["response_formats"]:
            lines.append("  response_formats:")
            for path in api_artifacts["response_formats"]:
                lines.extend(
                    [
                        f"    - path: {path}",
                        "      downstream_critical_fields: []",
                    ]
                )
        if api_artifacts["model_config"]:
            lines.extend(
                [
                    "  model_config:",
                    f"    path: {api_artifacts['model_config'][0]}",
                ]
            )
        if api_artifacts["test_cases"]:
            lines.append("  test_cases:")
            lines.extend(f"    - path: {path}" for path in api_artifacts["test_cases"])
        if api_artifacts["trace_samples"]:
            lines.append("  trace_samples:")
            lines.extend(f"    - path: {path}" for path in api_artifacts["trace_samples"])
        if api_artifacts["policy_rules"]:
            lines.append("  policy_rules:")
            lines.extend(f"    - path: {path}" for path in api_artifacts["policy_rules"])
    lines.extend(
        [
            "",
            "# Suggested next edits:",
            "# - Add approval/confirmation/idempotency policies for write tools.",
            "# - Add permissions.scopes if your tool specs do not declare auth scopes.",
            "# - Add risk_overrides.tools.<tool>.owner for production high-risk tools.",
            "",
            "policies:",
            # Same one question the auto template asks, rendered by the same
            # function so the legacy path cannot describe the packs
            # differently from the path everyone actually uses (#410 §F).
            *manifest_control_pack_block(control_pack),
            "  require_approval_for_tools: []",
            "  require_confirmation_for_tools: []",
            "  require_idempotency_for_tools: []",
            "",
            "permissions:",
            "  scopes: []",
            "",
            "ci:",
            "  mode: advisory",
            "",
            "output:",
            "  directory: agents-shipgate-reports",
            "  formats:",
            "    - markdown",
            "    - json",
            "",
        ]
    )
    return RenderedManifest(
        "\n".join(lines),
        tool_surface_origin=tool_surface_origin,
        scaffold_summary=(
            MINIMAL_SCAFFOLD_SUMMARY if tool_surface_origin == "scaffold" else None
        ),
    )



def discover_openai_api_artifacts(workspace: Path) -> dict[str, list[str]]:
    return {
        "prompt_files": _discover_patterns(workspace, PROMPT_PATTERNS),
        "tools": _discover_patterns(workspace, OPENAI_TOOL_PATTERNS),
        "response_formats": _discover_patterns(workspace, RESPONSE_SCHEMA_PATTERNS),
        "model_config": _discover_patterns(workspace, MODEL_CONFIG_PATTERNS),
        "test_cases": _discover_patterns(workspace, TEST_CASE_PATTERNS),
        "trace_samples": _discover_patterns(workspace, TRACE_SAMPLE_PATTERNS),
        "policy_rules": _discover_patterns(workspace, POLICY_RULE_PATTERNS),
    }


def discover_anthropic_artifacts(workspace: Path) -> dict[str, list[str]]:
    """Glob Anthropic-shaped artifacts. Mirrors the OpenAI-API discovery shape.

    Anthropic adoption stores prompts under ``prompts/``, tool defs under
    ``tools/anthropic-tools.json`` (or ``*anthropic*tools*.json``), and policy
    rules under ``policies/anthropic-policy.yaml`` (or ``*anthropic*.yaml``).
    Auto-init feeds the result into the manifest's ``anthropic:`` block.
    """
    return {
        "prompt_files": _discover_patterns(workspace, PROMPT_PATTERNS),
        "tools": _discover_patterns(workspace, ANTHROPIC_TOOL_PATTERNS),
        "policy_rules": _discover_patterns(workspace, ANTHROPIC_POLICY_PATTERNS),
    }


def discover_n8n_artifacts(workspace: Path) -> dict[str, list[str]]:
    return {
        "workflows": _discover_n8n_workflows(workspace),
        "credential_stubs": _discover_patterns(workspace, N8N_CREDENTIAL_STUB_PATTERNS),
        "variable_stubs": _discover_patterns(workspace, N8N_VARIABLE_STUB_PATTERNS),
        "data_table_schemas": _discover_patterns(
            workspace,
            N8N_DATA_TABLE_SCHEMA_PATTERNS,
        ),
        "eval_sets": _discover_patterns(workspace, N8N_EVAL_SET_PATTERNS),
    }


def _discover_n8n_workflows(workspace: Path) -> list[str]:
    found: list[str] = []
    seen: set[Path] = set()
    for path in _candidate_files_matching(workspace, N8N_WORKFLOW_PATTERNS):
        if path in seen:
            continue
        seen.add(path)
        if _looks_like_n8n_workflow(path):
            found.append(_relative(path, workspace))
    return sorted(found)


def _looks_like_n8n_workflow(path: Path) -> bool:
    if path.suffix.lower() != ".json":
        return False
    if _oversized_structured_input(path):
        # The n8n adapter loads workflows through ``load_structured_file``,
        # which refuses this file. Listing it under ``n8n.workflows`` would
        # write a manifest entry ``scan`` fails on, and scoring ``n8n`` off it
        # would name a framework nobody can verify.
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


def _discover_patterns(
    workspace: Path, patterns: tuple[str, ...], *, files: list[Path] | None = None
) -> list[str]:
    found: list[str] = []
    seen: set[Path] = set()
    for path in _candidate_files_matching(workspace, patterns, files=files):
        if path in seen:
            continue
        seen.add(path)
        found.append(_relative(path, workspace))
    return sorted(found)


def _skip(path: Path, workspace: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(workspace.resolve()).parts
    except ValueError:
        return True
    return any(_skip_part(part) for part in rel_parts)


def _skip_part(part: str) -> bool:
    return part in SKIP_DIRS or any(
        part.startswith(prefix) for prefix in SKIP_DIR_PREFIXES
    )


def _candidate_files_matching(
    workspace: Path, patterns: tuple[str, ...], *, files: list[Path] | None = None
) -> list[Path]:
    """Inventory entries matching any of ``patterns``.

    ``files`` supplies an inventory the caller already built. Without it each
    call re-runs the whole git walk, which is how one ``_suggested_sources``
    pass came to pay for fifteen of them; and a caller that wants the rule
    applied to *part* of a workspace has no way to say so.
    """

    return sorted(
        path
        for path in (files if files is not None else _candidate_files(workspace))
        if any(_matches_pattern(path, workspace, pattern) for pattern in patterns)
    )


def _candidate_files(workspace: Path) -> list[Path]:
    workspace = workspace.resolve()
    git_files = _git_candidate_files(workspace)
    if git_files is not None:
        return git_files
    return _walk_candidate_files(workspace)


def _git_candidate_files(workspace: Path) -> list[Path] | None:
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        root_result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(workspace),
                "rev-parse",
                "--show-toplevel",
            ],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if root_result.returncode != 0:
        return None
    git_root = Path(root_result.stdout.strip()).resolve()
    if not git_root:
        return None

    files_output = _run_git_inventory_bounded(
        workspace,
        [
                "-c",
                "core.fsmonitor=false",
                "-c",
                "submodule.recurse=false",
                "-c",
                "core.quotePath=false",
                "ls-files",
                "-co",
                "--exclude-standard",
                "--full-name",
                "-z",
                "--",
                ".",
        ],
        env=env,
        max_output_bytes=16 * 1024 * 1024,
    )
    if files_output is None:
        raise DiscoveryError(
            "Git candidate-file inventory exceeded static output bounds or "
            "could not be collected safely."
        )

    candidates: list[Path] = []
    for raw_rel in files_output.split(b"\0"):
        if not raw_rel:
            continue
        try:
            rel = raw_rel.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            path = (git_root / rel).resolve()
            is_file = path.is_file()
        except (OSError, RuntimeError):
            # A symlink loop (ELOOP surfaces as RuntimeError from
            # Path.resolve on CPython) or an unreadable entry must skip
            # that entry, not crash discovery — `detect` is the advertised
            # safe first touch and real repos contain such paths
            # (found mining stripe/ai: llm/ai-sdk/LICENSE loop).
            continue
        if is_file and not _skip(path, workspace):
            candidates.append(path)
    return sorted(candidates)


def _run_git_inventory_bounded(
    workspace: Path,
    args: list[str],
    *,
    env: dict[str, str],
    max_output_bytes: int,
) -> bytes | None:
    """Collect Git discovery paths without buffering unbounded output."""

    try:
        process = subprocess.Popen(
            ["git", "--no-replace-objects", "-C", str(workspace), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
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
        returncode = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        return None
    reader.join()
    if returncode != 0 or exceeded or failed:
        return None
    return bytes(output)


def _walk_candidate_files(workspace: Path) -> list[Path]:
    candidates: list[Path] = []
    workspace = workspace.resolve()
    for root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [dirname for dirname in dirnames if not _skip_part(dirname)]
        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            if path.is_file() and not _skip(path, workspace):
                candidates.append(path)
    return sorted(candidates)


def _matches_pattern(path: Path, workspace: Path, pattern: str) -> bool:
    rel = _relative(path, workspace)
    if fnmatch.fnmatch(rel, pattern):
        return True
    if "/" not in pattern:
        return fnmatch.fnmatch(path.name, pattern)
    return fnmatch.fnmatch(rel, f"*/{pattern}")


def _relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()
