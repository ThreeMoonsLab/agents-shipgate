"""Render the downstream local agent contract full file."""

from __future__ import annotations

from agents_shipgate.cli.discovery.local_contract import render_local_agent_contract


def render_file() -> str:
    """Return the full ``.shipgate/agent-contract.json`` file body."""

    return render_local_agent_contract()


PRIOR_RENDER_SHA256: tuple[str, ...] = ()


__all__ = ["PRIOR_RENDER_SHA256", "render_file"]
