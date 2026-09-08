"""Private facts for a failed static host read; never a control decision."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from agents_shipgate.core.privacy import redact_text

MAX_FAILURE_TEXT = 512


def safe_failure_text(value: str) -> str:
    """Redact before bounding, so truncation cannot expose part of a secret."""
    from agents_shipgate.core.host_grants import _sanitize_sensitive_string

    text = _sanitize_sensitive_string(redact_text(value) or "")
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)
    return text if len(text) <= MAX_FAILURE_TEXT else text[:MAX_FAILURE_TEXT - 1] + "…"


@dataclass(frozen=True)
class HostInputFailure:
    reason: Literal[
        "resource_bound_exceeded", "input_unreadable", "snapshot_validation_failed"
    ]
    phase: Literal[
        "inventory_enumeration", "entry_inspection", "source_read", "utf8_decode",
        "snapshot_validation",
    ]
    source: str
    # Known configured limits, not an inferred exceeded counter.
    limits: tuple[tuple[str, int], ...] = ()

    def evidence(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "phase": self.phase,
            "source": safe_failure_text(self.source),
            **({"configured_limits": dict(self.limits)} if self.limits else {}),
        }

    def summary(self) -> str:
        phase = {
            "inventory_enumeration": "directory inventory",
            "entry_inspection": "entry inspection",
            "source_read": "configuration read",
            "utf8_decode": "UTF-8 decoding",
            "snapshot_validation": "final snapshot validation",
        }[self.phase]
        failure = {
            "resource_bound_exceeded": "exceeded a static resource bound",
            "input_unreadable": "could not complete",
            "snapshot_validation_failed": "could not confirm a coherent read",
        }[self.reason]
        limits = "; ".join(f"{name}={value}" for name, value in self.limits)
        return safe_failure_text(
            f"{phase.capitalize()} {failure} at {self._source_label()}."
            + (f" Configured limits: {limits}." if limits else "")
        )

    def _source_label(self) -> str:
        return (
            "repository root (.)" if self.source in {".", "<repository>"}
            else safe_failure_text(self.source)
        )

    def recovery(self) -> str:
        return safe_failure_text(self._recovery())

    def _recovery(self) -> str:
        source = self._source_label()
        if self.reason == "resource_bound_exceeded":
            return (
                f"Review the inventory size at {source} against the configured limits "
                "in the evidence. Supply a complete supported scope before rerunning; "
                "do not omit required host inputs to clear this stop."
            )
        if self.reason == "snapshot_validation_failed":
            return (
                f"Ensure the inputs under {source} are accessible and stable, then "
                "rerun for a new coherent read. Final validation failed; the reader "
                "did not establish whether concurrent changes caused it."
            )
        if self.phase == "utf8_decode":
            return f"Make {source} a valid UTF-8 configuration file, then rerun."
        if self.phase in {"inventory_enumeration", "entry_inspection"}:
            return (
                f"Restore readable, stable directory entries at {source}, then rerun "
                "the complete inventory."
            )
        return (
            f"Make {source} available as one readable, singly-linked regular file within "
            "the static configuration root and configured limits, then rerun; symbolic "
            "links and filesystem aliases are not followed."
        )


class HostInventoryReadError(RuntimeError):
    def __init__(self, failure: HostInputFailure) -> None:
        self.failure = failure
        super().__init__(failure.summary())
