from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from agents_shipgate.core.privacy import RedactionStats, redact_data

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
_SENSITIVE_VALUE_MARKERS = (
    "akia",
    "agpa",
    "aida",
    "aipa",
    "anpa",
    "aroa",
    "asia",
    "bearer ",
    "clickhouse://",
    "ghp_",
    "gho_",
    "ghr_",
    "ghs_",
    "ghu_",
    "github_pat_",
    "mssql://",
    "mysql://",
    "postgres://",
    "postgresql://",
    "redis://",
    "rediss://",
    "rk_live_",
    "rk_test_",
    "sk-",
    "sk_live_",
    "sk_test_",
    "sqlserver://",
    "pk_live_",
    "pk_test_",
    "whsec_",
    "xox",
)


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


def configure_logging(*, verbose: bool = False, force: bool = True) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
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
        return any(marker in lowered for marker in _SENSITIVE_VALUE_MARKERS)
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if any(part in lowered_key for part in _SENSITIVE_KEY_PARTS):
                return True
            if depth < 2 and _might_contain_sensitive_payload(item, depth=depth + 1):
                return True
        return False
    if isinstance(value, list | tuple):
        return depth < 2 and any(
            _might_contain_sensitive_payload(item, depth=depth + 1)
            for item in value
        )
    return False
