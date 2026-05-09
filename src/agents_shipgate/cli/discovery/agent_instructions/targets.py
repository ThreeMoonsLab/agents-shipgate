"""Target registry and selector parsing for ``--agent-instructions=``.

The selector accepts:

- ``all``  — every registered target.
- ``none`` — no targets (rare; mirrors ``--minimal`` as an explicit opt-out).
- A comma-separated list of target names, e.g. ``agents-md,cursor``.

Unknown names raise :class:`InvalidSelector`. The CLI converts that into a
``config_error`` agent-mode error JSON line + a ``next_action`` pointing at
the valid set.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bumped only on incompatible content changes (e.g., the report.json contract
# evolves and prior blocks would mislead). Renderer-format version, NOT the
# package version — package version stamping would rotate hashes every release.
BLOCK_VERSION: int = 1

# Order is the order targets are applied and printed. AGENTS.md first because
# it's the agent-facing entry point; CLAUDE.md mirrors it for Claude users;
# Cursor is a separate IDE rule file; PR template is reviewer-facing.
TARGETS: tuple[str, ...] = ("agents-md", "claude-md", "cursor", "pr-template")


class InvalidSelector(ValueError):
    """Raised when ``--agent-instructions=<value>`` contains an unknown name."""


@dataclass(frozen=True)
class TargetSpec:
    name: str
    relative_path: str  # default; PR template may resolve a different casing
    is_full_file: bool  # True for cursor (we own the whole file), False for managed-block targets


SPECS: dict[str, TargetSpec] = {
    "agents-md": TargetSpec(name="agents-md", relative_path="AGENTS.md", is_full_file=False),
    "claude-md": TargetSpec(name="claude-md", relative_path="CLAUDE.md", is_full_file=False),
    "cursor": TargetSpec(
        name="cursor",
        relative_path=".cursor/rules/agents-shipgate.mdc",
        is_full_file=True,
    ),
    "pr-template": TargetSpec(
        name="pr-template",
        relative_path=".github/pull_request_template.md",
        is_full_file=False,
    ),
}


def parse_selector(value: str) -> list[str]:
    """Parse a selector string into an ordered, deduplicated list of target names.

    Empty selector is rejected — the CLI must pass ``all`` or ``none`` explicitly.
    """
    raw = (value or "").strip()
    if not raw:
        raise InvalidSelector(
            "Empty selector. Pass --agent-instructions=all, --agent-instructions=none, "
            "or a comma-separated list of: " + ", ".join(TARGETS)
        )
    if raw == "all":
        return list(TARGETS)
    if raw == "none":
        return []
    requested = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [name for name in requested if name not in SPECS]
    if unknown:
        raise InvalidSelector(
            f"Unknown agent-instruction target(s): {', '.join(unknown)}. "
            f"Valid targets: {', '.join(TARGETS)}."
        )
    # Preserve the order TARGETS declares so output is stable across selector permutations.
    return [name for name in TARGETS if name in requested]
