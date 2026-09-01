"""Acyclic primitives shared by content-addressed schema models."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel

CONTENT_ID_PATTERN = r"^sha256:[0-9a-f]{64}$"
GIT_OBJECT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"


def canonical_json(value: Any) -> bytes:
    """Return the one wire representation used by every identity hash."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def content_id(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value)).hexdigest()}"


def validate_portable_path(value: str) -> str:
    """Reject absolute, escaping, or non-normalized artifact/input paths."""

    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise ValueError("artifact and input paths must be normalized portable relative paths")
    return value


__all__ = [
    "CONTENT_ID_PATTERN",
    "GIT_OBJECT_PATTERN",
    "canonical_json",
    "content_id",
    "validate_portable_path",
]
