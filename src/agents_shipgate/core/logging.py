from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any


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
