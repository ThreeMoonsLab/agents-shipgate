"""Bounded declaration-only MCP launch source pin facts (#825).

No shell parsing, registry lookup or executable resolution. Unknown grammar
abstains; it is not evidence of a mutable (or pinned) source.
"""
from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlsplit

Pin = Literal["pinned", "mutable"]
_NPM = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*(?:@([A-Za-z0-9.*^~+_-][A-Za-z0-9.*^~+_-]*))?")
_SEMVER = re.compile(r"v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
_PYPI = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:\[[A-Za-z0-9._,-]+\])?(?:(==|~=|>=|<=|!=|>|<)([A-Za-z0-9.*+!_-][A-Za-z0-9.*+!_-]*))?")
_PY_VERSION = re.compile(r"\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?(?:\+[A-Za-z0-9.]+)?")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9._-]*(?::\d+)?(?:/[a-z0-9][a-z0-9._-]*)*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:([0-9a-f]{64}))?")
_TOOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _python_pin(spec: str) -> Pin | None:
    if spec.startswith("git+"):
        try:
            url = urlsplit(spec[4:])
        except ValueError:
            return None
        if (
            url.scheme not in {"https", "http", "ssh"} or not url.hostname
            or url.password is not None or url.username not in {None, "git"}
            or url.query or url.fragment or not url.path.strip("/")
        ):
            return None
        _path, separator, ref = url.path.rpartition("@")
        if separator and (not ref or not _path.strip("/")):
            return None
        return "pinned" if separator and re.fullmatch(r"[0-9a-fA-F]{40}", ref) else "mutable"
    match = _PYPI.fullmatch(spec)
    if match is None:
        return None
    operator, version = match[1], match[2]
    if operator is None:
        return "mutable"
    if _PY_VERSION.fullmatch(version or ""):
        return "pinned" if operator == "==" else "mutable"
    if operator in {"==", "!="} and re.fullmatch(r"\d+(?:\.\d+)*\.\*", version or ""):
        return "mutable"
    return None


def launch_source_pin(command: Any, args: Any) -> tuple[Pin, int] | None:
    """Return pin state and source argument index for the documented grammar.

    No text from the selected spec is published here. Only exact launcher names are recognized:
    a path's basename cannot establish that it is not a repository wrapper.
    Every accepted argument is bounded, literal and free of interpolation.
    """
    if command not in ("npx", "bunx", "pnpm", "uvx", "pipx", "docker"):
        return None
    if (
        not isinstance(args, list) or not 1 <= len(args) <= 64
        or any(not isinstance(a, str) or len(a) > 2048 for a in args)
    ):
        return None
    if sum(map(len, args)) > 8192 or any(any(c in a for c in "$`{}\n\r") for a in args):
        return None
    index = 0
    family = "npm"
    flags: set[str] = set()
    if command == "npx":
        flags = {"-y", "--yes"}
    elif command == "bunx":
        flags = {"--bun"}
    elif command == "pnpm":
        if args[0] != "dlx":
            return None
        index = 1
    elif command == "uvx":
        family = "python"
        if args[0] == "--from":
            if len(args) < 3 or not _TOOL.fullmatch(args[2]):
                return None
            index = 1
    elif command == "pipx":
        family = "python"
        if args[0] != "run":
            return None
        index = 1
        if len(args) > index and args[index] == "--no-cache":
            index += 1
        if len(args) <= index + 2 or args[index] != "--spec" or not _TOOL.fullmatch(args[index + 2]):
            return None
        index += 1
    else:
        family = "image"
        if args[0] != "run":
            return None
        index = 1
        flags = {"-i", "--interactive", "-t", "--tty", "--rm", "--init"}
    while index < len(args) and args[index] in flags:
        index += 1
    if index >= len(args):
        return None
    spec = args[index]
    if family == "npm":
        match = _NPM.fullmatch(spec)
        if match is None:
            return None
        pin: Pin | None = "pinned" if _SEMVER.fullmatch(match[1] or "") else "mutable"
    elif family == "image":
        match = _IMAGE.fullmatch(spec)
        if match is None:
            return None
        pin = "pinned" if match[1] else "mutable"
    else:
        pin = _python_pin(spec)
    return (pin, index) if pin else None
