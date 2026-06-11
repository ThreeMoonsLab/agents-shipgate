from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping

AGENT_MODE_ENV_VAR = "AGENTS_SHIPGATE_AGENT_MODE"

# Environment variables that coding-agent harnesses export in every shell
# they spawn. Their presence auto-enables agent mode so agents get
# structured output without remembering to set AGENTS_SHIPGATE_AGENT_MODE.
# Claude Code sets CLAUDECODE=1; Cursor sets CURSOR_TRACE_ID.
AGENT_ENV_HINTS = ("CLAUDECODE", "CURSOR_TRACE_ID")

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def is_agent_mode(env: Mapping[str, str] | None = None) -> bool:
    """Whether agent mode is active for structured agent-facing output.

    An explicit ``AGENTS_SHIPGATE_AGENT_MODE`` value wins in both
    directions (truthy forces on, falsy forces off). Otherwise agent mode
    auto-enables when a known coding-agent harness variable from
    ``AGENT_ENV_HINTS`` is present and non-empty.
    """
    source = os.environ if env is None else env
    explicit = source.get(AGENT_MODE_ENV_VAR, "").strip().lower()
    if explicit in _TRUTHY:
        return True
    if explicit in _FALSY:
        return False
    return any(source.get(hint) for hint in AGENT_ENV_HINTS)


def emit_agent_mode_error(error_kind: str, **fields: object) -> None:
    """Emit a structured one-line error for coding-agent callers."""
    if not is_agent_mode():
        return
    payload = {"error": error_kind, **fields}
    print(json.dumps(payload, default=str), file=sys.stderr)
