"""The one hardened environment every Git child Shipgate starts runs with.

Shipgate runs Git against repositories it is reviewing, so nothing the
repository, the operator's global or system configuration, or an inherited
``GIT_*`` variable says may change what Git executes, contacts, or prints.
This used to be spelled out by hand at each place that started Git, and the
copies drifted: discovery had no ``GIT_ALLOW_PROTOCOL``, the guarded
authorization executor had no ``GIT_ATTR_NOSYSTEM``, ``GIT_OPTIONAL_LOCKS`` or
``GIT_PAGER``, and the fixture ran Git with the operator's whole configuration
(#1031). A variable added after a Git advisory — as ``GIT_NO_LAZY_FETCH`` once
was — reached only the copy it was added to.

Every Git child in ``src/`` now takes its environment from
:func:`hardened_git_env`, and the Claude Code hook runner, which cannot import
Shipgate, has the same overrides rendered into it at install time. The
zero-install ``tools/shipgate-detect.py`` cannot import it either and keeps a
copy. ``tests/test_git_env.py`` holds all three to this module.

A leaf module: standard library only, so every layer can import it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: The overrides, as ``(name, value)``. ``None`` stands for :data:`os.devnull`,
#: resolved on the machine the child starts on — the hook runner renders it
#: as that expression rather than as this machine's spelling of it.
#:
#: The union of every hand-written copy this module replaced, so no Git child
#: lost a variable it was started with before (#1031).
_HARDENING: tuple[tuple[str, str | None], ...] = (
    # No transport at all: a promisor remote, a submodule URL or an ``ext::``
    # helper in repository config cannot make a read reach the network.
    ("GIT_ALLOW_PROTOCOL", ""),
    ("GIT_ATTR_NOSYSTEM", "1"),
    ("GIT_CONFIG_GLOBAL", None),
    ("GIT_CONFIG_NOSYSTEM", "1"),
    ("GIT_CONFIG_SYSTEM", None),
    # A partial clone missing an object fails instead of fetching it.
    ("GIT_NO_LAZY_FETCH", "1"),
    ("GIT_NO_REPLACE_OBJECTS", "1"),
    ("GIT_OPTIONAL_LOCKS", "0"),
    ("GIT_PAGER", "cat"),
    ("GIT_PROTOCOL_FROM_USER", "0"),
    ("GIT_TERMINAL_PROMPT", "0"),
)


def hardened_git_overrides() -> dict[str, str]:
    """The ``GIT_*`` variables every Git child is started with, and their values."""

    return {name: os.devnull if value is None else value for name, value in _HARDENING}


def hardened_git_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A Git child's environment: ``base`` without any ``GIT_*`` variable, hardened.

    ``base`` defaults to this process's environment. A caller that starts Git
    from a deliberately small allowlist (the guarded authorization executor)
    passes that allowlist instead, and adds its own non-``GIT_`` settings —
    ``HOME``, ``PATH`` — to the result.

    Every inherited ``GIT_*`` variable is dropped, not only the ones this
    module sets: ``GIT_DIR``, ``GIT_WORK_TREE``, ``GIT_INDEX_FILE``,
    ``GIT_CONFIG_COUNT`` and their kind would otherwise redirect a read to
    another repository or inject configuration.
    """

    source = os.environ if base is None else base
    env = {key: value for key, value in source.items() if not key.startswith("GIT_")}
    env.update(hardened_git_overrides())
    return env


def with_allowed_git_protocols(env: Mapping[str, str], protocols: str) -> dict[str, str]:
    """A copy of a hardened ``env`` whose Git may use ``protocols``, and only those.

    The one sanctioned widening of :func:`hardened_git_env`: the guarded
    authorization executor's single push, which must reach its HTTPS remote.
    Named here so the exception is visible beside the rule it relaxes, rather
    than written as a bare assignment at the call site.
    """

    widened = dict(env)
    widened["GIT_ALLOW_PROTOCOL"] = protocols
    return widened


__all__ = [
    "hardened_git_env",
    "hardened_git_overrides",
    "with_allowed_git_protocols",
]
