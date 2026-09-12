"""Shared immutable row contract for host capability comparisons."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CapabilityDiffRow:
    subject: str
    before: str
    after: str
    direction: str
    why: str
    severity: str
    #: The engine called this change an expansion of authority. Kept apart
    #: from ``direction`` on purpose: presence is a fact this module can
    #: read off both sides, whereas widening is a judgement, and only the
    #: engine's `expansion_signals` may make it.
    expands: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

