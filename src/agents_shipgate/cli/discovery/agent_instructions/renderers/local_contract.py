"""Render the downstream local agent contract full file."""

from __future__ import annotations

from agents_shipgate.cli.discovery.local_contract import render_local_agent_contract


def render_file() -> str:
    """Return the full ``.shipgate/agent-contract.json`` file body."""

    return render_local_agent_contract()


# Exact render shipped by local contract schema v6. Keeping this hash lets
# first-adoption reruns upgrade an untouched managed file to v7 without
# overwriting user-authored JSON.
PRIOR_RENDER_SHA256: tuple[str, ...] = (
    "85d33d005d35f933b72e32c2d370efc2680e09d2ebe0c9997931c8ab4f352738",
)


__all__ = ["PRIOR_RENDER_SHA256", "render_file"]
