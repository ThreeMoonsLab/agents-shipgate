from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TextIO

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.trust_roots import read_identity_bound_text

MAX_EXPLICIT_DIFF_BYTES = 32 * 1024 * 1024
MAX_EXPLICIT_JSON_BYTES = 40 * 1024 * 1024
MAX_EXPLICIT_PATH_LIST_BYTES = 8 * 1024 * 1024


def ensure_bounded_utf8_text(
    text: str,
    *,
    max_bytes: int,
    label: str,
) -> str:
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise InputParseError(
            f"{label} exceeds the {max_bytes}-byte static input limit"
        )
    return text


def read_bounded_utf8_file(
    path: Path,
    *,
    max_bytes: int,
    label: str,
) -> str:
    try:
        lexical = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
        return read_identity_bound_text(
            lexical.parent,
            Path(lexical.name),
            max_bytes=max_bytes,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise InputParseError(f"Could not read {label} {path}: {exc}") from exc


def read_bounded_utf8_stdin(
    *,
    max_bytes: int,
    label: str,
    stream: TextIO | None = None,
) -> str:
    source = stream or sys.stdin
    binary = getattr(source, "buffer", None)
    if binary is not None:
        raw = binary.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise InputParseError(
                f"{label} exceeds the {max_bytes}-byte static input limit"
            )
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise InputParseError(f"{label} is not valid UTF-8: {exc}") from exc
    # Test/embedded streams may expose text only. Bound characters first, then
    # enforce the authoritative UTF-8 byte ceiling.
    text = source.read(max_bytes + 1)
    return ensure_bounded_utf8_text(text, max_bytes=max_bytes, label=label)


__all__ = [
    "MAX_EXPLICIT_DIFF_BYTES",
    "MAX_EXPLICIT_JSON_BYTES",
    "MAX_EXPLICIT_PATH_LIST_BYTES",
    "ensure_bounded_utf8_text",
    "read_bounded_utf8_file",
    "read_bounded_utf8_stdin",
]
