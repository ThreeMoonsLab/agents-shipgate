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
    #: The permission list this rule is declared under — ``allow``, ``ask`` or
    #: ``deny`` — read from the grant, and ``None`` for every other grant kind.
    #: A permission grant's identity is its disposition and its rule text, so a
    #: row that has both sides has one disposition. The text has named it since
    #: #795 (`allow: Bash(npm *)`); it is published so a machine consumer reads
    #: the same fact, including on the routes that redact the rule's arguments.
    disposition: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

