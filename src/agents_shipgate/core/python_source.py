"""Decode and parse Python source without executing it."""

from __future__ import annotations

import ast
import io
import tokenize
import warnings
from pathlib import Path
from typing import Literal, overload


def decode_python_source(data: bytes) -> str:
    """Read a PEP 263 encoding cookie or BOM under Python's source rules."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    except SyntaxError as exc:
        raise UnicodeError(str(exc)) from exc
    return data.decode(encoding)


@overload
def parse_python(
    source: str | bytes, *, filename: str | Path = "<unknown>",
    mode: Literal["exec"] = "exec",
) -> ast.Module: ...


@overload
def parse_python(
    source: str | bytes, *, filename: str | Path = "<unknown>", mode: Literal["eval"],
) -> ast.Expression: ...


def parse_python(
    source: str | bytes, *, filename: str | Path = "<unknown>",
    mode: Literal["exec", "eval"] = "exec",
) -> ast.Module | ast.Expression:
    """Static readers report parse errors without inheriting warning filters."""
    if isinstance(source, bytes):
        source = decode_python_source(source)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            return ast.parse(source, filename=str(filename), mode=mode)
        except RecursionError as exc:
            raise SyntaxError("Python source exceeds the parser's recursion limit") from exc
