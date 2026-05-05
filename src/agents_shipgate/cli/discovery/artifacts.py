from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

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
    "node_modules",
    "target",
    "venv",
}
SKIP_DIR_PREFIXES = (".venv",)


def discover_manifest_paths(workspace: Path) -> list[Path]:
    return _candidate_files_matching(workspace, ("shipgate.yaml",))


def discover_tool_sources(workspace: Path) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[Path] = set()
    for pattern in OPENAPI_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            if path in seen:
                continue
            seen.add(path)
            sources.append(
                {
                    "id": _source_id(path, "openapi"),
                    "type": "openapi",
                    "path": _relative(path, workspace),
                }
            )
    for pattern in MCP_PATTERNS:
        for path in _candidate_files_matching(workspace, (pattern,)):
            if path in seen:
                continue
            seen.add(path)
            sources.append(
                {
                    "id": _source_id(path, "mcp"),
                    "type": "mcp",
                    "path": _relative(path, workspace),
                }
            )
    return sources


def render_manifest_template(workspace: Path) -> str:
    sources = discover_tool_sources(workspace)
    api_artifacts = discover_openai_api_artifacts(workspace)
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
    elif not api_artifacts:
        lines.append("tool_sources:")
        lines.extend(
            [
                "  - id: CHANGE_ME",
                "    type: openapi",
                "    path: CHANGE_ME.yaml",
            ]
        )
    if api_artifacts:
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
    return "\n".join(lines)


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


def _discover_patterns(workspace: Path, patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    seen: set[Path] = set()
    for path in _candidate_files_matching(workspace, patterns):
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


def _candidate_files_matching(workspace: Path, patterns: tuple[str, ...]) -> list[Path]:
    return sorted(
        path
        for path in _candidate_files(workspace)
        if any(_matches_pattern(path, workspace, pattern) for pattern in patterns)
    )


def _candidate_files(workspace: Path) -> list[Path]:
    workspace = workspace.resolve()
    git_files = _git_candidate_files(workspace)
    if git_files is not None:
        return git_files
    return _walk_candidate_files(workspace)


def _git_candidate_files(workspace: Path) -> list[Path] | None:
    try:
        root_result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
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

    try:
        files_result = subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "ls-files",
                "-co",
                "--exclude-standard",
                "--full-name",
                "-z",
                "--",
                ".",
            ],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if files_result.returncode != 0:
        return None

    candidates: list[Path] = []
    for raw_rel in files_result.stdout.split(b"\0"):
        if not raw_rel:
            continue
        try:
            rel = raw_rel.decode("utf-8")
        except UnicodeDecodeError:
            continue
        path = (git_root / rel).resolve()
        if path.is_file() and not _skip(path, workspace):
            candidates.append(path)
    return sorted(candidates)


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


def _source_id(path: Path, source_type: str) -> str:
    stem = path.stem.lower().replace("-", "_").replace(".", "_")
    return f"{source_type}_{stem}"
