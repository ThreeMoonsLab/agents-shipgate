from __future__ import annotations

import json
import os
import sys


def is_agent_mode() -> bool:
    """True when a coding agent set AGENTS_SHIPGATE_AGENT_MODE."""
    return os.environ.get("AGENTS_SHIPGATE_AGENT_MODE", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def emit_agent_mode_error(error_kind: str, **fields: object) -> None:
    """Emit a structured one-line error for coding-agent callers."""
    if not is_agent_mode():
        return
    payload = {"error": error_kind, **fields}
    print(json.dumps(payload, default=str), file=sys.stderr)

