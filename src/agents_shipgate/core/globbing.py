"""Shared glob matcher with ``**`` (globstar) semantics.

Extracted from :mod:`agents_shipgate.triggers` so the trigger evaluator
and the verify/trust-root check classify paths against the exact same
rules. Path separators are forward slashes; backslashes are normalized.

``**/foo`` matches ``foo`` at any depth (including the repo root);
``dir/**`` matches ``dir`` and anything below it; bare ``**`` matches
zero or more characters across path segments. ``*`` and ``?`` are
segment-local (do not cross ``/``).

:func:`glob_match_ci` is the case-tolerant form. Use it wherever a wider
match can only *add* evaluation — classifying a path as a trust root, a
boundary surface, or a reason to run. Use the case-sensitive
:func:`glob_match` where a wider match *subtracts* evaluation, which today
is the trigger catalog's ``every_file_matches`` predicate: it classifies a
change set as ignorable, and folding it would read ``src/TEST_agent.py`` as
a test file. Keep both in this module so that choice is made deliberately
rather than by whichever name a caller happened to import.
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

    Used by the trust-root classifier, the boundary-adapter registry, the
    preflight protected-surface specs, the Tier B verify checks' changed-file
    selection (``_verify_common.touched``), and the trigger evaluator's
    positive and guard predicates — the surfaces that must agree about a
    path. ``cli/install_hooks`` carries its own copy of this logic because
    it is rendered into a standalone hook script.
    """

    return glob_match(pattern, path) or glob_match(pattern.casefold(), path.casefold())
