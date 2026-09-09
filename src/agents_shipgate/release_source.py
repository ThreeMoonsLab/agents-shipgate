"""Read the build's immutable Action source, without a network or tag lookup.

The optional record binds a candidate to source. It asserts neither publication
nor qualification; the same bytes survive both of those later operations.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from agents_shipgate import __version__

_RECORD = Path(__file__).parent / "_meta" / "release-source.json"


def candidate_action_ref() -> str | None:
    """Absent means an ordinary build; malformed provenance never falls back."""
    try:
        info = _RECORD.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise ValueError("Invalid candidate release-source record: expected a small regular file")
    descriptor = os.open(_RECORD, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as handle:
        current = os.fstat(handle.fileno())
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
            info.st_dev, info.st_ino
        ):
            raise ValueError("Invalid candidate release-source record: changed during read")
        raw = handle.read(4097)
    if len(raw) > 4096:
        raise ValueError("Invalid candidate release-source record: too large")
    try:
        record = json.loads(raw)
        valid = (
            isinstance(record, dict)
            and set(record) == {"schema_version", "source_commit", "package_version"}
            and record["schema_version"] == "shipgate.release_source/v1"
            and record["package_version"] == __version__
            and isinstance(record["source_commit"], str)
            and re.fullmatch(r"[0-9a-f]{40}", record["source_commit"])
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", record["package_version"])
        )
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise ValueError("Invalid candidate release-source record; reinstall the intact wheel")
    return record["source_commit"]
