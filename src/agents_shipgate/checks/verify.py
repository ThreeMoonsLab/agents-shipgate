"""Verify category — trust-root protection (the cheap reward-hacking guard).

``SHIP-VERIFY-TRUST-ROOT-TOUCHED`` is Tier A of trust-root protection
(docs/engineering/ai-coding-workflow-verifier.md §5.1): pure path/glob
classification of the PR's changed files against the release gate's
trust spine. It is fully deterministic, needs no base scan, and fires
only when a :class:`VerificationContext` is present — plain ``scan``
(``context.verification is None``) emits nothing.

Reward hacking is the coding-agent threat model: an optimizer told to
"make CI green" may edit the gate instead of fixing the readiness issue.
Touching a trust root requires at least human review, so the finding is
emitted at ``medium`` severity and routes to ``release_decision``'s
review tier by default. Strict CI / severity overrides can escalate it
through the existing decision machinery — it stays one ordinary
``Finding`` through the one decision engine; it is never a second
verdict.
"""

from __future__ import annotations

from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.globbing import glob_match
from agents_shipgate.schemas.common import (
    SourceReference,
    parse_confidence,
    parse_severity,
)
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"

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


def run(context: ScanContext) -> list[Finding]:
    verification = context.verification
    if verification is None:
        return []
    findings: list[Finding] = []
    seen: set[str] = set()
    for raw in verification.changed_files:
        path = raw.replace("\\", "/").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        classification = _classify(path)
        if classification is None:
            continue
        trust_root_class, matched_glob = classification
        findings.append(
            _finding(context, path, trust_root_class, matched_glob)
        )
    return findings


def _classify(path: str) -> tuple[str, str] | None:
    for trust_root_class, pattern in TRUST_ROOT_SURFACES:
        if glob_match(pattern, path):
            return trust_root_class, pattern
    return None


def _finding(
    context: ScanContext,
    path: str,
    trust_root_class: str,
    matched_glob: str,
) -> Finding:
    return Finding(
        check_id=CHECK_ID,
        title=f"Release trust root touched: {path}",
        severity=parse_severity("medium"),
        category="verify",
        agent_id=context.agent.id,
        evidence={
            "changed_file": path,
            "trust_root_class": trust_root_class,
            "matched_glob": matched_glob,
        },
        confidence=parse_confidence("high"),
        provenance_kind="static_declaration",
        source=SourceReference(type="changed_file", path=path),
        recommendation=(
            "This PR changes a file that defines the release gate's trust "
            "spine. A human must review the change before merge; do not "
            "weaken the gate to make CI pass."
        ),
    )
