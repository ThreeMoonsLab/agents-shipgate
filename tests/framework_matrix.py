"""Which framework-parametrized cases run on every PR and which run nightly (#964).

The OpenAI Agents SDK reader and the Google ADK reader share one
constructor-ownership engine: a test's ``framework`` (or ``family``) argument
only selects the reader and the import root, so crossing it with a large matrix
of routes runs the same engine twice over the same routes.

Per test function, the ADK matrix stays whole in the per-PR run (the engine has
more ADK-specific branches, ``family == "google.adk"``). The Agents SDK keeps a
**covering subset**: every value of every parameter is still met at least once
in its reader, so each route (patch, owner, layout, change, ...) and each
reader-specific branch is exercised there too. The remaining SDK cases are
marked ``slow``, which the per-PR suite excludes (``-m "not slow"``) and the
nightly job runs (``-m slow``). Nothing is deleted: a run with no ``-m``, and
``pytest -m slow``, still execute every case.

The marks are added at collection (``conftest.py``), not in each test, so the
parameter ids that other files key on stay as they are.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

#: The test files whose framework matrices are thinned: the constructor-ownership
#: engine's tests, where ``framework`` / ``family`` selects a reader over one engine.
THINNED_FILES = frozenset({
    "tests/test_builder_bindings.py",
    "tests/test_builder_calls.py",
    "tests/test_builtin_namespace_ownership.py",
    "tests/test_constructor_dependency_ownership.py",
    "tests/test_constructor_dependency_values.py",
    "tests/test_constructor_foreign_dependencies.py",
    "tests/test_constructor_typing_dependencies.py",
    "tests/test_constructor_unread_imports.py",
    "tests/test_direct_operator_constructor_ownership.py",
    "tests/test_eager_binding_ownership.py",
    "tests/test_external_helper_constructor_ownership.py",
    "tests/test_framework_constructor_bindings.py",
    "tests/test_framework_constructor_imports.py",
    "tests/test_framework_constructor_retention.py",
    "tests/test_implicit_child_constructor_ownership.py",
    "tests/test_import_search_ownership.py",
    "tests/test_imported_tool_review.py",
    "tests/test_returned_agent_lists.py",
    "tests/test_saved_vars_repository_context.py",
    "tests/test_type_checking_constructor_ownership.py",
})

#: The framework whose cases are thinned, under every spelling the tests use.
THINNED_VALUES = frozenset({"sdk", "agents", "openai_agents"})

_FRAMEWORK_ARGUMENTS = ("framework", "family")


def mark_nightly_framework_cases(items: list[pytest.Item]) -> int:
    """Mark the redundant cases of the thinned framework ``slow``; return how many."""

    groups: dict[tuple[str, str, str], list[pytest.Item]] = {}
    for item in items:
        callspec = getattr(item, "callspec", None)
        path = _relative_path(item)
        if callspec is None or path not in THINNED_FILES:
            continue
        argument = next((name for name in _FRAMEWORK_ARGUMENTS if name in callspec.params), None)
        if argument is None or callspec.params[argument] not in THINNED_VALUES:
            continue
        groups.setdefault((path, getattr(item, "originalname", item.name), argument), []).append(item)
    marked = 0
    for group in groups.values():
        kept = {id(item) for item in covering_subset(group)}
        for item in group:
            if id(item) not in kept:
                item.add_marker(pytest.mark.slow)
                marked += 1
    return marked


def covering_subset(group: list[pytest.Item]) -> list[pytest.Item]:
    """The fewest cases (greedy, in collection order) meeting every parameter value.

    Every parameter is a route, the framework argument's own value included
    (``agents`` and ``openai_agents`` are two import spellings of the SDK). The
    subset has at least one case for each ``(parameter, value)`` that any case
    in the group has. Ties go to the earliest case, so the choice is
    deterministic.
    """

    needs = [_pairs(item) for item in group]
    uncovered: set[tuple[str, str]] = set().union(*needs) if needs else set()
    chosen: list[int] = []
    while uncovered:
        best = max(range(len(group)), key=lambda index: (len(needs[index] & uncovered), -index))
        gained = needs[best] & uncovered
        if not gained:
            break
        chosen.append(best)
        uncovered -= gained
    if not chosen and group:
        chosen = [0]  # A case with no parameter to cover is its own route.
    return [group[index] for index in sorted(chosen)]


def _pairs(item: pytest.Item) -> set[tuple[str, str]]:
    params: dict[str, Any] = item.callspec.params  # type: ignore[attr-defined]
    return {(name, repr(value)) for name, value in params.items()}


def _relative_path(item: pytest.Item) -> str:
    path = Path(str(item.path))
    try:
        return path.relative_to(item.config.rootpath).as_posix()
    except ValueError:
        return path.as_posix()
