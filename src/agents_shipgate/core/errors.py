from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import ValidationError


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


def format_validation_error(
    exc: ValidationError,
    *,
    label: str,
    suggest: Callable[[dict[str, Any]], str | None] | None = None,
) -> str:
    """Describe invalid fields without serializing Pydantic's original inputs."""

    lines = [f"{label}:"]
    for error in exc.errors(include_input=False, include_context=False, include_url=False):
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        message = error["msg"]
        if suggest is not None and (suggestion := suggest(error)):
            message = f"{message}. Did you mean {suggestion}?"
        lines.append(f"- {location}: {message}")
    return "\n".join(lines)
