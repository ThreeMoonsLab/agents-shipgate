"""Declared human-acknowledgement entries (roadmap §5.4).

Within Shipgate's static / no-network boundary, human acknowledgement of a
trust-root weakening can only be *declared* evidence — it is never
inferred (Principle 4: human authority cannot be synthesized). A coding
agent must not be able to manufacture approval; the acknowledgement is a
manifest declaration naming a human ``owner``, a ``reason``, the
``affected_surface`` it covers, and an optional ``expires`` date.

Because these entries live in ``shipgate.yaml`` — itself a release trust
root — adding or editing one already trips
``SHIP-VERIFY-TRUST-ROOT-TOUCHED``, so a coding agent cannot silently add
its own acknowledgement without that edit becoming a release signal.

``affected_surface`` is matched against the canonical surface keys the
``human_ack`` report builder derives from active trust-root weakening
findings (e.g. ``policy``, ``ci_gate``, ``baseline_or_waiver``,
``agent_instructions``). It may also be a concrete changed-file path.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class HumanAckDeclaration(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    affected_surface: str
    owner: str
    reason: str
    # ISO date string copied verbatim; never generated. Recorded in the
    # report's HumanAck block. Wall-clock expiry enforcement is the job of
    # the clock-bearing verify orchestration, not the deterministic report
    # builder (which must stay reproducible).
    expires: str | None = None

    @field_validator("affected_surface", "owner", "reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "human_ack entries require non-empty affected_surface, "
                "owner, and reason (declared human authority cannot be blank)"
            )
        return value
