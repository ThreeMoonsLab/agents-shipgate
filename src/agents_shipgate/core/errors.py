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

    from agents_shipgate.core.agent_control_envelope import single_line_text
    from agents_shipgate.core.privacy import redact_text

    lines = [f"{label}:"]
    for error in exc.errors(include_input=False, include_context=False, include_url=False):
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        message = (
            "Unrecognized discriminator tag"
            if error["type"] == "union_tag_invalid"
            else error["msg"]
        )
        if suggest is not None and (suggestion := suggest(error)):
            message = f"{message}. Did you mean {suggestion}?"
        safe_location = single_line_text(redact_text(location) or "")
        safe_message = single_line_text(redact_text(message) or "")
        lines.append(f"- {safe_location}: {safe_message}")
    return "\n".join(lines)


def format_error_message(exc: BaseException, *, label: str) -> str:
    """``label: exc``, with a ``ValidationError`` described the way
    :func:`format_validation_error` does.

    For a handler that catches a ``ValidationError`` beside other exceptions,
    or through ``ValueError`` or ``Exception``. Pydantic's own text carries
    type codes, the invalid input values and documentation URLs; it is not a
    message (#1040), and ``tests/test_validation_error_messages.py`` keeps it
    out of every handler that interpolates the exception.
    """

    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        return format_validation_error(exc, label=label)
    return f"{label}: {exc}"


def format_exception_text(exc: BaseException, *, validation_label: str) -> str:
    """``str(exc)``, with a ``ValidationError`` described under
    ``validation_label`` the way :func:`format_validation_error` does.

    For a handler that shows the bare exception text, such as an internal-error
    boundary around code that can raise a ``ValidationError`` from deep inside.
    """

    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        return format_validation_error(exc, label=validation_label)
    return str(exc)
