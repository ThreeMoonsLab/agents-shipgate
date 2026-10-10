"""Bounded declaration-only MCP launch source pin facts (#825).

No shell parsing, registry lookup or executable resolution. Unknown grammar
abstains; it is not evidence of a mutable (or pinned) source.

A pin is a fact of the declaration. Where the launcher then finds the package
is a separate question the declaration does not answer for one form (#933):
``npx`` given a package name with no version specifier runs "whatever version
exists in the local project" when the project depends on it, and otherwise
installs it into the npm cache from the registry
(https://docs.npmjs.com/cli/v11/commands/npm-exec#description). That form is
published with :data:`LOCAL_PROJECT_OR_REGISTRY`, and no ``package.json``,
lockfile, ``node_modules`` or cache is read to decide which applies.
"""
from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlsplit

Pin = Literal["pinned", "mutable"]
Resolution = Literal["local_project_or_registry"]
#: ``npx NAME`` with no version specifier: npm matches a local project
#: dependency of that name first and falls back to the registry or its cache
#: (#933). Which one a launch uses is not established by the declaration.
LOCAL_PROJECT_OR_REGISTRY: Resolution = "local_project_or_registry"
_NPM = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*(?:@([A-Za-z0-9.*^~+_-][A-Za-z0-9.*^~+_-]*))?")
_SEMVER = re.compile(r"v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
_PYPI = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:\[[A-Za-z0-9._,-]+\])?(?:(==|~=|>=|<=|!=|>|<)([A-Za-z0-9.*+!_-][A-Za-z0-9.*+!_-]*))?")
_PY_VERSION = re.compile(r"\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?(?:\+[A-Za-z0-9.]+)?")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9._-]*(?::\d+)?(?:/[a-z0-9][a-z0-9._-]*)*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:([0-9a-f]{64}))?")
_TOOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
#: uv's ``uvx NAME@VERSION`` / ``uvx NAME@latest`` (an exact version or ``latest`` only).
_UV_AT = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:\[[A-Za-z0-9._,-]+\])?)@([^@]+)")
#: ``docker run`` flags that take no value; ``-it``/``-ti`` combine the short ones.
_DOCKER_SWITCHES = frozenset({"-i", "--interactive", "-t", "--tty", "--rm", "--init"})
_DOCKER_SHORT_SWITCHES = re.compile(r"-[it]+")
#: ``docker run`` flags whose value is the next argument, or follows ``=``.
#: The value is skipped: it is never the selected source and never published.
_DOCKER_VALUE_FLAGS = frozenset({
    "-e", "--env", "--env-file", "-v", "--volume", "--network", "--name",
    "-w", "--workdir", "-u", "--user", "-p", "--publish", "--mount",
    "--platform", "--entrypoint", "--pull",
})


def _docker_image_index(args: list[str], index: int) -> int | None:
    """The image's index after ``docker run``, or ``None`` at any flag outside the two tables."""
    while index < len(args):
        arg = args[index]
        if arg in _DOCKER_SWITCHES or _DOCKER_SHORT_SWITCHES.fullmatch(arg):
            index += 1
        elif arg in _DOCKER_VALUE_FLAGS:
            index += 2
        elif "=" in arg and arg.split("=", 1)[0] in _DOCKER_VALUE_FLAGS:
            index += 1
        elif arg.startswith("-"):
            return None
        else:
            return index
    return None


def _python_pin(spec: str, *, uv_at: bool = False) -> Pin | None:
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
    if uv_at and "@" in spec:
        at = _UV_AT.fullmatch(spec)
        if at is None:
            return None
        if at[2] == "latest":
            return "mutable"
        return "pinned" if _PY_VERSION.fullmatch(at[2]) else None
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
        docker_index = _docker_image_index(args, 1)
        if docker_index is None:
            return None
        index = docker_index
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
        pin = _python_pin(spec, uv_at=command == "uvx" and index == 0)
    return (pin, index) if pin else None


def launch_source_resolution(command: Any, args: Any, index: int) -> Resolution | None:
    """How the launcher resolves the source at ``index``, when the declaration leaves it open (#933).

    Only ``npx`` with an npm package name and no version specifier, which npm
    documents as matched against the local project's dependencies before the
    registry or cache. ``@latest``, a range, a tag or an exact version is a
    specifier and keeps #825's reading; every other launcher, ``bunx`` and
    ``pnpm dlx`` included, is not classified here. ``index`` is the one
    :func:`launch_source_pin` returned for the same arguments.
    """

    if command != "npx" or not isinstance(args, list) or not 0 <= index < len(args):
        return None
    match = _NPM.fullmatch(args[index]) if isinstance(args[index], str) else None
    if match is None or match[1] is not None:
        return None
    return LOCAL_PROJECT_OR_REGISTRY
