from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from agents_shipgate.core.privacy import (
    BEARER_TOKEN_RE,
    SECRET_PRECHECK_MARKERS,
    RedactionStats,
    redact_data,
)

_LOGGING_PRECHECK_MAX_DEPTH = 2
# Bound JSON-log precheck traversal; emitted Shipgate log payloads are shallow.

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)
_SENSITIVE_VALUE_MARKERS = SECRET_PRECHECK_MARKERS


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key.startswith("agents_shipgate_"):
                payload[key.removeprefix("agents_shipgate_")] = value
        if _might_contain_sensitive_payload(payload):
            payload = redact_data(payload, stats=RedactionStats(), path="$")
        return json.dumps(payload, sort_keys=True, default=str)


class _CurrentStderrHandler(logging.StreamHandler):
    """A StreamHandler that resolves ``sys.stderr`` at emit time.

    ``logging.StreamHandler(sys.stderr)`` binds the stream object that
    exists at configure time. In-process embedders invoke the CLI many
    times per process — CliRunner-based tests swap and close
    ``sys.stderr`` per invocation, and the MCP server runs verify
    repeatedly — so a configure-time handler can outlive its stream and
    poison a *later* invocation's output with ``--- Logging error ---``
    tracebacks (observed as a flaky self-check JSON failure under
    pytest-xdist in CI on PR #192). Emit-time lookup always writes to
    the live stream.
    """

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:
        # The parent __init__/setStream assign the captured stream;
        # ignore it — this handler always follows the current sys.stderr.
        pass


def configure_logging(*, verbose: bool = False, force: bool = True) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    handler = _CurrentStderrHandler()
    if os.environ.get("AGENTS_SHIPGATE_LOG_FORMAT") == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("agents_shipgate")
    if root.handlers and not force:
        root.setLevel(level)
        return
    if force:
        root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def _might_contain_sensitive_payload(value: Any, *, depth: int = 0) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _SENSITIVE_VALUE_MARKERS) or bool(
            BEARER_TOKEN_RE.search(value)
        )
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if any(part in lowered_key for part in _SENSITIVE_KEY_PARTS):
                return True
            if depth < _LOGGING_PRECHECK_MAX_DEPTH and _might_contain_sensitive_payload(
                item, depth=depth + 1
            ):
                return True
        return False
    if isinstance(value, list | tuple):
        return depth < _LOGGING_PRECHECK_MAX_DEPTH and any(
            _might_contain_sensitive_payload(item, depth=depth + 1)
            for item in value
        )
    return False
