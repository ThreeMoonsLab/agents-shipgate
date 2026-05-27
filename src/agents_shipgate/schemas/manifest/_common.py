from __future__ import annotations

from pydantic import ConfigDict

# Every manifest section uses ``extra="forbid"`` so typos at any level
# raise a Pydantic validation error rather than silently no-op'ing. The
# config loader translates those errors into ``ConfigError`` (exit 2)
# with a close-match suggestion for the offending field name.
STRICT_MODEL_CONFIG = ConfigDict(extra="forbid")
