from __future__ import annotations

# Reward-hacking moves that are never acceptable for an autonomous coding
# agent. These strings are shared by verify and preflight surfaces.
FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "Do not suppress the finding (checks.ignore in shipgate.yaml).",
    "Do not lower severity or add a waiver just to pass the gate.",
    "Do not invent or assume approval, idempotency, or audit evidence you "
    "cannot prove from the code.",
    "Do not weaken the release policy, CI gate, or agent instructions that "
    "evaluate this change.",
)

__all__ = ["FORBIDDEN_SHORTCUTS"]
