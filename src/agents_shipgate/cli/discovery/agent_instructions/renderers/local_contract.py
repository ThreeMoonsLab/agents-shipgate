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
    # v7 final, before local contract v8 added the current-control pointer
    "e20840ce698c5bd81289de04f79674e4cac9dc670ae41d6e2580f13a38897b69",
    # v8 as shipped by the current-control pointer change; never appended at the
    # time, so a repo on v8 could not be upgraded in place.
    "e4580d8f55c54157745cdf86066190080a11cf0a3c6201608c6342990b4e81ee",
    # v9 as shipped by the publish-vs-merge permission change.
    "8aa6004936f19c31607498eb7d603b5f42ee55443e31ccc8b8099b14196eb8ab",
    # v10 before trigger catalog 0.3 -> 0.4, and the outgoing render this
    # revision replaces. A repo pinned to it must upgrade in place, or its
    # agents keep reading "no rule matched means skip" from a stale contract.
    "c63117b02849e6e7400f366bd32c42c978db1f6c7a1bb3d9f64c125a9a1d4e47",
    # v10 before manifest-provenance binding moved the verifier, verify-run,
    # verification plan/receipt, and handoff schemas together.
    "6694dc6ae3d1ea5f7c585d261443b5b8f5e8dd11eb031c0ad8b8176c0a308b76",
)


__all__ = ["PRIOR_RENDER_SHA256", "render_file"]
