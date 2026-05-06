"""Canonical "what Agents Shipgate did NOT prove" text.

Single source of truth so the markdown, JSON, and HTML renderers can
embed the same disclaimers verbatim. Every emitted packet, regardless
of run state, includes these four lines in §10.
"""

from __future__ import annotations

PACKET_NON_PROOF_HEADLINE = (
    "Agents Shipgate is a static release-readiness scanner. The packet "
    "below is derived from a scan; it does not, by itself, prove the "
    "following properties:"
)

PACKET_NON_PROOF: tuple[tuple[str, str], ...] = (
    (
        "Prompt robustness",
        "Whether the agent's prompt holds up under jailbreaks, persona "
        "drift, indirect prompt injection, or adversarial inputs.",
    ),
    (
        "Runtime behavior",
        "Whether the agent actually invokes only the declared tools, "
        "respects approval gates at runtime, or follows policy under "
        "load. Static config is not runtime evidence.",
    ),
    (
        "Model correctness",
        "Whether the underlying model produces correct outputs, calls "
        "the right tools, or stays within the declared scope. The "
        "packet does not benchmark the model.",
    ),
    (
        "Adversarial resistance",
        "Whether the agent withstands red-team or penetration testing. "
        "The packet does not run scenarios; it organizes evidence.",
    ),
)
"""Ordered (label, body) pairs. Renderers preserve the order so the
generated HTML, Markdown, and JSON forms stay byte-identical across
runs."""
