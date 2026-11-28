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


#: What final snapshot validation observed at one entry, as the identity
#: reader typed it (#588). ``inspection_unavailable`` claims no change.
SnapshotObservation = Literal[
    "identity_mismatch", "entries_changed", "kind_changed", "root_changed",
    "inspection_unavailable",
]

# What was observed comes before the entry, so a long entry cannot push it
# out of the bounded text.
_OBSERVED: dict[str, str] = {
    "identity_mismatch": "an entry no longer had the identity it was read with: {entry}",
    "entries_changed": "a directory's entries were no longer the ones read: {entry}",
    "kind_changed": "an entry was no longer the kind it was listed as: {entry}",
    "root_changed": "the read root no longer had the identity it was read with",
    "inspection_unavailable": (
        "an entry could not be inspected, and no change was established: {entry}"
    ),
}

#: How much of an entry's label prose repeats; the evidence keeps all of it.
PROSE_ENTRY_CHARS = 160


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
    # The one entry a failed final validation names, and what it observed
    # there, both from the reader's typed failure (#588), never parsed from a
    # message. ``source`` stays the read root: the failure discards every read
    # under it, so the entry locates the failure without narrowing its scope.
    entry: str | None = None
    observation: SnapshotObservation | None = None

    def evidence(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "phase": self.phase,
            "source": safe_failure_text(self.source),
            **({"configured_limits": dict(self.limits)} if self.limits else {}),
            **({"entry": self._entry_text()} if self.entry is not None else {}),
            **({"observation": self.observation} if self.observation is not None else {}),
        }

    def _entry_text(self) -> str:
        """The bounded, redacted label of the entry, from the spelling already held."""
        from agents_shipgate.core.host_grants import _issue_source_label

        return _issue_source_label(self.entry or ".")

    def _entry_label(self) -> str:
        if self.entry in {None, ".", "<repository>"}:
            return "repository root (.)"
        from agents_shipgate.core.host_grants import _issue_source_label

        # Prose names the entry by its trailing whole components, so the
        # entry's own name survives the bound. Each component is redacted on
        # its own, never cut first: a cut could split a secret before
        # redaction sees it. The evidence keeps the whole bounded label.
        parts = self.entry.split("/")
        kept = 1
        while kept < len(parts) and len(
            _issue_source_label("/".join(parts[-(kept + 1):]))
        ) <= PROSE_ENTRY_CHARS - 2:
            kept += 1
        label = _issue_source_label("/".join(parts[-kept:]))
        if kept < len(parts):
            label = "…/" + label
        if len(label) > PROSE_ENTRY_CHARS:
            label = label[:PROSE_ENTRY_CHARS - 1] + "…"
        return label

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
        observed = (
            ": " + _OBSERVED[self.observation].format(entry=self._entry_label())
            if self.observation is not None else ""
        )
        return safe_failure_text(
            f"{phase.capitalize()} {failure} at {self._source_label()}{observed}."
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
            if self.observation == "inspection_unavailable":
                return (
                    f"Make {self._entry_label()} accessible to this reader, then rerun "
                    f"for a new coherent read of {source}. Final validation could not "
                    "inspect it; the reader did not establish that it changed."
                )
            if self.observation is not None:
                return (
                    f"Keep the inputs under {source} unchanged while they are read, "
                    "then rerun for a new coherent read. Final validation observed "
                    f"that {self._entry_label()} differed from what was read, so "
                    f"nothing read under {source} is used."
                )
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
