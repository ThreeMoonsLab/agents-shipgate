"""Parse and render the shipgate-managed block in a co-authored file.

Targets that share host files with the user (AGENTS.md, CLAUDE.md, PR template)
embed a fenced block:

    <!-- agents-shipgate:start v=1 -->
    ...rendered content...
    <!-- agents-shipgate:end -->

This module is byte-pure: callers pass ``bytes`` and receive ``bytes``. Newline
style (LF vs CRLF) is detected from the host and propagated into the block so
the rest of the file is preserved byte-for-byte.

The ``v=N`` token is the renderer-format version, not the package version. It
is bumped only on incompatible content changes; old blocks auto-upgrade on the
next ``init --write --agent-instructions=...`` run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

START_PATTERN = re.compile(rb"<!-- agents-shipgate:start v=(\d+) -->")
END_MARKER = b"<!-- agents-shipgate:end -->"
END_PATTERN = re.compile(rb"<!-- agents-shipgate:end -->")


class BlockState(StrEnum):
    """Result of parsing a host file for a managed block."""

    NO_MARKERS = "no_markers"
    PRESENT = "present"
    AMBIGUOUS = "ambiguous"


class UpsertStatus(StrEnum):
    """Result of computing the new host bytes."""

    APPENDED = "appended"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    MIGRATED = "migrated"
    NEWER_VERSION = "newer_version"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class BlockLocation:
    """Line-aligned byte offsets of a parsed block.

    ``line_start`` is the first byte of the start-marker LINE (so any
    indentation before the marker is owned by us, never the user).
    ``line_end`` is the first byte AFTER the end-marker line — i.e., the
    trailing newline that follows the end marker is included.
    """

    line_start: int
    line_end: int
    version: int


@dataclass(frozen=True)
class ParsedBlock:
    state: BlockState
    location: BlockLocation | None = None


@dataclass(frozen=True)
class UpsertResult:
    new_bytes: bytes
    status: UpsertStatus
    block_version: int  # version of the block that ended up in new_bytes


def detect_newline(host: bytes) -> bytes:
    """Return the host's newline style.

    LF unless any CRLF sequence appears in the host. Empty hosts default to LF.
    """
    return b"\r\n" if b"\r\n" in host else b"\n"


def parse(host: bytes) -> ParsedBlock:
    """Locate the managed block in ``host`` bytes."""
    start_matches = list(START_PATTERN.finditer(host))
    end_matches = list(END_PATTERN.finditer(host))

    if not start_matches and not end_matches:
        return ParsedBlock(state=BlockState.NO_MARKERS)

    if len(start_matches) != 1 or len(end_matches) != 1:
        return ParsedBlock(state=BlockState.AMBIGUOUS)

    start = start_matches[0]
    end = end_matches[0]
    if start.start() >= end.start():
        return ParsedBlock(state=BlockState.AMBIGUOUS)

    line_start = host.rfind(b"\n", 0, start.start()) + 1
    newline_after_end = host.find(b"\n", end.end())
    line_end = len(host) if newline_after_end == -1 else newline_after_end + 1

    return ParsedBlock(
        state=BlockState.PRESENT,
        location=BlockLocation(
            line_start=line_start,
            line_end=line_end,
            version=int(start.group(1)),
        ),
    )


def render_block(inner: str, version: int, newline: bytes) -> bytes:
    """Wrap rendered inner content with start/end markers.

    ``inner`` is the human-readable content between markers. It is normalized
    to use the host newline style and to end with exactly one newline before
    the end marker.
    """
    if version < 1:
        raise ValueError(f"block version must be >= 1, got {version}")
    nl = newline.decode("ascii")
    body = inner.replace("\r\n", "\n").replace("\n", nl)
    if not body.endswith(nl):
        body += nl
    return (
        f"<!-- agents-shipgate:start v={version} -->{nl}"
        f"{body}"
        f"<!-- agents-shipgate:end -->{nl}"
    ).encode()


def upsert(host: bytes, *, inner: str, version: int) -> UpsertResult:
    """Compute new host bytes containing the rendered block.

    Pure function. ``host`` may be empty (meaning "no host content yet" —
    callers writing a new file pass ``b""``). Bytes outside the block region
    are preserved exactly.
    """
    parsed = parse(host)
    if parsed.state is BlockState.AMBIGUOUS:
        return UpsertResult(new_bytes=host, status=UpsertStatus.AMBIGUOUS, block_version=version)

    nl = detect_newline(host)
    block = render_block(inner, version, nl)

    if parsed.state is BlockState.NO_MARKERS:
        if not host:
            return UpsertResult(new_bytes=block, status=UpsertStatus.APPENDED, block_version=version)
        # Ensure exactly one blank line between user content and our block.
        prefix = host
        if not prefix.endswith(nl):
            prefix += nl
        if not prefix.endswith(nl + nl):
            prefix += nl
        return UpsertResult(
            new_bytes=prefix + block,
            status=UpsertStatus.APPENDED,
            block_version=version,
        )

    assert parsed.location is not None  # type narrowing for the PRESENT branch
    loc = parsed.location
    if loc.version > version:
        return UpsertResult(
            new_bytes=host,
            status=UpsertStatus.NEWER_VERSION,
            block_version=loc.version,
        )

    new_bytes = host[: loc.line_start] + block + host[loc.line_end :]
    if new_bytes == host:
        return UpsertResult(new_bytes=host, status=UpsertStatus.UNCHANGED, block_version=version)
    if loc.version < version:
        return UpsertResult(new_bytes=new_bytes, status=UpsertStatus.MIGRATED, block_version=version)
    return UpsertResult(new_bytes=new_bytes, status=UpsertStatus.UPDATED, block_version=version)
