from __future__ import annotations

import json
import os
import sys


def emit_agent_mode_error(error_kind: str, **fields: object) -> None:
    """Emit a structured one-line error for coding-agent callers."""
    if os.environ.get("AGENTS_SHIPGATE_AGENT_MODE", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    payload = {"error": error_kind, **fields}
    print(json.dumps(payload, default=str), file=sys.stderr)

