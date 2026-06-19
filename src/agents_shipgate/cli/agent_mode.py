from __future__ import annotations

import json
import os
import sys
from typing import Any


def emit_agent_mode_error(
    error_kind: str,
    *,
    message: str | None = None,
    exit_code: int | None = None,
    command: str | None = None,
    next_action: str | None = None,
    next_actions: list[dict[str, Any]] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    artifacts: dict[str, Any] | None = None,
    **fields: object,
) -> None:
    """Emit a structured one-line error for coding-agent callers."""
    if os.environ.get("AGENTS_SHIPGATE_AGENT_MODE", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    payload: dict[str, object] = {"error": error_kind, **fields}
    if message is not None:
        payload["message"] = message
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if command is not None:
        payload["command"] = command
    if next_action is not None:
        payload["next_action"] = next_action
    if next_actions is not None:
        payload["next_actions"] = next_actions
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    if artifacts is not None:
        payload["artifacts"] = artifacts
    print(json.dumps(payload, default=str), file=sys.stderr)
