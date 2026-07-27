"""Classification of a repository's release trust roots.

This is domain data, not check logic: the local boundary evaluator, the
preflight router, the installed hooks, and the verify-mode
``SHIP-VERIFY-TRUST-ROOT-TOUCHED`` check must all classify a changed path
identically.  It lives in :mod:`agents_shipgate.core` so every one of those
consumers can import it without a cycle;
:mod:`agents_shipgate.checks.verify` re-exports the names it has always
exported.
"""

from __future__ import annotations

from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.globbing import glob_match

# Ordered (class, glob) classification of a repo's release trust roots —
# the surfaces that define the gate in any repo that has adopted
# Shipgate (target-repo trust roots, §5.2). First match wins; one
# finding per changed file. ``**/`` prefixes match at any depth
# (including the repo root) per agents_shipgate.core.globbing.
_LEGACY_TRUST_ROOT_SURFACES: tuple[tuple[str, str], ...] = (
    ("manifest", "**/shipgate.yaml"),
    ("shipgate_state", "**/.agents-shipgate/**"),
    ("policy", "**/policies/**"),
    ("prompts", "**/prompts/**"),
    ("ci_gate", "**/.github/workflows/agents-shipgate.yml"),
    ("ci_gate", "**/.github/workflows/agents-shipgate.yaml"),
    ("agent_instructions", "**/AGENTS.md"),
    ("agent_instructions", "**/CLAUDE.md"),
    ("agent_instructions", "**/.claude/**"),
    ("agent_instructions", "**/.cursor/rules/**"),
    ("agent_instructions", "**/.agents/skills/**"),
    ("agent_instructions", "**/.codex/**"),
    ("codex_plugin", "**/.codex-plugin/**"),
    ("tool_surface_decl", "**/.app.json"),
    ("tool_surface_decl", "**/.mcp.json"),
    # Host-boundary MCP declarations (Cursor / VS Code project servers).
    # Claude Code settings (.claude/settings.json, .claude/settings.local.json)
    # are already covered by the agent_instructions "**/.claude/**" glob above.
    ("tool_surface_decl", "**/.cursor/mcp.json"),
    ("tool_surface_decl", "**/.vscode/mcp.json"),
    ("tool_surface_decl", "**/SKILL.md"),
)


def _registry_trust_root_surfaces() -> tuple[tuple[str, str], ...]:
    """Project registry paths not already covered by a legacy trust-root glob.

    Existing classifications stay stable for finding fingerprints and reviewer
    copy. Newly registered boundary paths automatically become trust roots,
    which prevents trigger/check/preflight coverage from drifting apart.
    """

    existing_patterns = tuple(pattern for _kind, pattern in _LEGACY_TRUST_ROOT_SURFACES)
    additions: list[tuple[str, str]] = []
    seen: set[str] = set(existing_patterns)
    for adapter in BOUNDARY_ADAPTERS:
        for pattern in (*adapter.exact_paths, *adapter.globs):
            if pattern in seen:
                continue
            representative = pattern.replace("**", "nested").replace("*", "item")
            if any(glob_match(existing, representative) for existing in existing_patterns):
                continue
            seen.add(pattern)
            additions.append(("host_boundary", pattern))
    return tuple(additions)


TRUST_ROOT_SURFACES: tuple[tuple[str, str], ...] = (
    *_LEGACY_TRUST_ROOT_SURFACES,
    *_registry_trust_root_surfaces(),
)

# The deny-list of trust-root files a coding agent must never edit *to make a
# verdict pass*, derived from ``TRUST_ROOT_SURFACES`` (single source of truth),
# restricted to the classes whose trust boundary is the WHOLE FILE: the
# Shipgate CI gate, the agent-instruction surfaces, and policy packs.
#
# Deliberately EXCLUDES:
#   * ``shipgate.yaml`` and ``.agents-shipgate/**`` — their boundary is
#     *key-level* (editing an action's scope is a legitimate mechanical fix; a
#     ``checks.ignore`` / baseline / waiver expansion is reward-hacking). A
#     path-level deny cannot express that, so they are covered by
#     ``forbidden_actions`` (``FORBIDDEN_SHORTCUTS``) instead.
#   * the tool-surface declarations (``.mcp.json``, ``SKILL.md``, ``.app.json``,
#     ``.codex-plugin/**``) — those are the capability surface UNDER review,
#     which a PR may legitimately edit.
#
# Single home so the verifier and the ``agent_handoff`` preview fallback emit
# the IDENTICAL
# standing deny-list — a passing/preview verdict never reads as "anything goes".
_FORBIDDEN_EDIT_CLASSES = frozenset(
    {"ci_gate", "agent_instructions", "policy", "host_boundary"}
)
PROTECTED_FILE_EDITS: tuple[str, ...] = tuple(
    pattern for kind, pattern in TRUST_ROOT_SURFACES if kind in _FORBIDDEN_EDIT_CLASSES
)


def trust_root_class_for(path: str) -> str | None:
    """Classify ``path`` against the ordered trust-root table, first match wins."""

    for trust_root_class, pattern in TRUST_ROOT_SURFACES:
        if glob_match(pattern, path):
            return trust_root_class
    return None


__all__ = [
    "PROTECTED_FILE_EDITS",
    "TRUST_ROOT_SURFACES",
    "trust_root_class_for",
]
