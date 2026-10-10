"""The compiled-pattern cache matches exactly what the uncached matcher did (#698).

`glob_match` re-translated its pattern on every call, and a large base tree
paid that for every path against every boundary glob. The cache must change
cost only: the reference below is the pre-#698 implementation, verbatim, and
the matcher has to agree with it on every pattern and path drawn here.
"""

from __future__ import annotations

import random
import re

import pytest

from agents_shipgate.core.globbing import _compiled, glob_match, glob_match_ci


def _reference(pattern: str, path: str) -> bool:
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


_PATTERN_ATOMS = ["**/", "/**", "**", "*", "?", "[ab]", "[!x]", "[", ".", "a", "b", "AGENTS.md", ".claude/", "x\\y", "/"]
_PATH_ATOMS = ["a", "b", "x", "AGENTS.md", "agents.md", ".claude", "/", ".", "y", "\\", "settings.json"]


def test_the_cached_matcher_agrees_with_the_uncached_one() -> None:
    rng = random.Random(698)
    checked = 0
    for _ in range(3000):
        pattern = "".join(rng.choice(_PATTERN_ATOMS) for _ in range(rng.randint(1, 5)))
        try:
            re.compile(pattern.replace("[!", "[^"))
        except re.error:
            pass
        for _ in range(8):
            path = "".join(rng.choice(_PATH_ATOMS) for _ in range(rng.randint(1, 6)))
            try:
                expected = _reference(pattern, path)
            except re.error:
                with pytest.raises(re.error):
                    glob_match(pattern, path)
                continue
            assert glob_match(pattern, path) is expected, (pattern, path)
            checked += 1
    assert checked > 10_000


def test_the_case_tolerant_form_still_folds_both_sides() -> None:
    assert glob_match_ci("**/AGENTS.md", "docs/agents.md")
    assert not glob_match("**/AGENTS.md", "docs/agents.md")


def test_a_pattern_is_translated_once() -> None:
    _compiled.cache_clear()
    for index in range(1000):
        glob_match("**/.claude/settings*.json", f"pkg{index}/.claude/settings.json")
    info = _compiled.cache_info()
    assert info.misses == 1 and info.hits == 999
