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
from agents_shipgate.published_release import ReleaseEngine, published_engine

_RECORD = Path(__file__).parent / "_meta" / "release-source.json"


def release_engine() -> ReleaseEngine:
    """Select the engine for every pin ``init`` writes, by one rule (#781).

    A valid record means this wheel is a final release build: it pins its own
    version, its immutable Action source and the contract it emits. Absence
    means an ordinary build, which keeps the published fallback (#506). A
    malformed record raises here, so no caller can fall back past it.
    """
    source_commit = candidate_action_ref()
    if source_commit is None:
        return published_engine()
    # Imported here: the contract module pulls in every schema, and this
    # function is reached from modules that every command imports.
    from agents_shipgate.schemas.contract import CONTRACT_VERSION

    return ReleaseEngine(
        package_version=__version__,
        action_ref=source_commit,
        contract_version=CONTRACT_VERSION,
        stamped=True,
    )


def candidate_action_ref() -> str | None:
    """Absent means an ordinary build; malformed provenance never falls back."""
    try:
        info = _RECORD.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise ValueError("Invalid candidate release-source record: expected a small regular file")
    # O_BINARY on Windows: os.open() otherwise opens in the C runtime's text
    # mode, which rewrites CRLF and stops at a 0x1A byte. The stdlib ORs it for
    # exactly this reason (`_pyio.FileIO`, `tempfile._bin_openflags`).
    descriptor = os.open(_RECORD, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
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
