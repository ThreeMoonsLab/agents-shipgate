"""Shared glob matcher with ``**`` (globstar) semantics.

Extracted from :mod:`agents_shipgate.triggers` so the trigger evaluator
and the verify/trust-root check classify paths against the exact same
rules. Path separators are forward slashes; backslashes are normalized.

``**/foo`` matches ``foo`` at any depth (including the repo root);
``dir/**`` matches ``dir`` and anything below it; bare ``**`` matches
zero or more characters across path segments. ``*`` and ``?`` are
segment-local (do not cross ``/``).

:func:`glob_match_ci` is the case-tolerant form every path-classifying
surface must use. Keep the two in the same module so a caller cannot pick
the case-sensitive one by accident and quietly open a spelling bypass.
"""

from __future__ import annotations

import re


def glob_match(pattern: str, path: str) -> bool:
    """Return whether ``path`` matches the ``**``-extended ``pattern``."""
    pattern = pattern.replace("\\", "/")
    path = path.replace("\\", "/")
    if not any(token in pattern for token in ("*", "?", "[")):
        return path == pattern

    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            parts.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("/**", i):
            parts.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                parts.append(re.escape(pattern[i]))
                i += 1
            else:
                parts.append(pattern[i : close + 1])
                i = close + 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(parts), path) is not None


def glob_match_ci(pattern: str, path: str) -> bool:
    """Return whether ``path`` matches ``pattern``, tolerating case variants.

    Git can carry a lowercase spelling that becomes the canonical host file
    on a case-insensitive filesystem (``agents.md`` resolving as
    ``AGENTS.md``, ``Policies/`` as ``policies/``). Every surface that
    classifies a path against a governance surface treats those variants
    conservatively on every platform, so a PR cannot be routed one way on
    Linux and acquire a privileged meaning when cloned elsewhere.

    Used by the trust-root classifier, the boundary-adapter registry, and
    the trigger evaluator, which must agree about the same path.
    """

    return glob_match(pattern, path) or glob_match(pattern.casefold(), path.casefold())
