"""Render and apply agent-instruction snippets to a target repo.

Public surface used by ``agents-shipgate init --agent-instructions=...``:

- :data:`BLOCK_VERSION` — current renderer-format version.
- :data:`TARGETS` — ordered tuple of selectable target names.
- :func:`parse_selector` — parse the comma-separated selector value.
- :func:`apply_agent_instructions` — apply the per-target decision tree
  against a workspace.
- :class:`TargetOutcome` — per-target result returned by ``apply``.
"""

from __future__ import annotations

from agents_shipgate.cli.discovery.agent_instructions.apply import (
    TargetOutcome,
    apply_agent_instructions,
    render_targets,
)
from agents_shipgate.cli.discovery.agent_instructions.targets import (
    BLOCK_VERSION,
    TARGETS,
    InvalidSelector,
    parse_selector,
)

__all__ = [
    "BLOCK_VERSION",
    "InvalidSelector",
    "TARGETS",
    "TargetOutcome",
    "apply_agent_instructions",
    "parse_selector",
    "render_targets",
]
