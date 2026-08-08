"""Render the downstream local agent contract full file."""

from __future__ import annotations

from agents_shipgate.cli.discovery.local_contract import render_local_agent_contract


def render_file() -> str:
    """Return the full ``.shipgate/agent-contract.json`` file body."""

    return render_local_agent_contract()


# Exact renders shipped by earlier releases. Keeping these hashes lets a rerun
# upgrade an untouched managed file in place without overwriting user-authored
# JSON. The file body changes whenever any advertised sub-schema version moves,
# so every outgoing render is appended here — not only schema-version bumps of
# the contract file itself.
PRIOR_RENDER_SHA256: tuple[str, ...] = (
    # local contract schema v6
    "85d33d005d35f933b72e32c2d370efc2680e09d2ebe0c9997931c8ab4f352738",
    # v7 before verifier 0.6 -> 0.7 and trigger catalog 0.2 -> 0.3
    "6041d5fc42ee4be37596c9c13b9752a8a511bb18bc987b32b0ffb49160ee6d93",
    # v7 at runtime contract 19, before control permissions and
    # review_publishable moved the contract to 20
    "e20840ce698c5bd81289de04f79674e4cac9dc670ae41d6e2580f13a38897b69",
)


__all__ = ["PRIOR_RENDER_SHA256", "render_file"]
