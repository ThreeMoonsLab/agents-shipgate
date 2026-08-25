from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class AgentsShipgateError(Exception):
    """Base exception for expected Agents Shipgate failures.

    ``details`` carries the diagnostic identifiers a machine consumer or a bug
    report needs — the internal identity triple, a source id, a check id —
    *out of* the message. Adopter-facing text names files, symbols, and
    manifest keys (see :mod:`agents_shipgate.core.adopter_text`); the precise
    internal spelling still has to reach ``report.json`` consumers and issue
    reports, and this is the channel for the failures that abort before any
    report is written. Empty for the many failures that need no such payload.
    """

    def __init__(self, *args: Any, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(*args)
        self.details: dict[str, Any] = dict(details or {})


class ConfigError(AgentsShipgateError):
    """Raised when the manifest is missing or invalid."""


class InputParseError(AgentsShipgateError):
    """Raised when a declared input source cannot be parsed."""


class DiscoveryError(AgentsShipgateError):
    """Raised when workspace discovery cannot establish bounded input coverage."""
