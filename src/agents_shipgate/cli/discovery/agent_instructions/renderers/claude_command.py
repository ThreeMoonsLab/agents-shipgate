"""Render the Claude Code ``/shipgate`` command from the checked-in source."""

from __future__ import annotations

from pathlib import Path

RESOURCE_PATH = "_meta/claude-command/shipgate.md"


def render_file() -> str:
    """Return the full ``.claude/commands/shipgate.md`` file body."""

    # Wheels get this file via pyproject force-include under the package root.
    package_root = Path(__file__).resolve().parents[4]
    packaged = package_root / RESOURCE_PATH
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")

    # Source-checkout fallback.
    repo_root = Path(__file__).resolve().parents[6]
    return (repo_root / ".claude/commands/shipgate.md").read_text(encoding="utf-8")


PRIOR_RENDER_SHA256: tuple[str, ...] = (
    # Before contract v20 added the current-control refresh rule.
    "199ad507acde2c3fed69abf0f54891d8f9b2fd9a218b5fd9999f4329461ed871",
    # 1.0.0 through 1.2.0: told check runs to parse `agent_boundary_result/v1`
    # and promised a `next_action` line on every agent-mode error (#1039, #1040).
    "107e60198df59f7622bd8e416f1997cfbbb2e0a5206762944322b6ba36ce1b03",
)


__all__ = ["PRIOR_RENDER_SHA256", "render_file"]
