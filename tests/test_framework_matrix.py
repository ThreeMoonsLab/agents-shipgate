"""The per-PR run keeps every route on both readers, and the rest runs nightly (#964)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from tests.framework_matrix import (
    THINNED_FILES,
    THINNED_VALUES,
    covering_subset,
    mark_nightly_framework_cases,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Item:
    """The parts of a collected parametrized test the policy reads."""

    def __init__(self, function: str, params: dict, *, path: str = "tests/test_builder_bindings.py"):
        self.callspec = SimpleNamespace(params=params)
        self.originalname = function
        self.name = f"{function}[{'-'.join(str(value) for value in params.values())}]"
        self.path = REPO_ROOT / path
        self.config = SimpleNamespace(rootpath=REPO_ROOT)
        self.marks: list[str] = []

    def add_marker(self, mark) -> None:  # noqa: ANN001
        self.marks.append(mark.name)


def _matrix(function="test_x", routes=("a", "b", "c"), changes=("added", "removed"), **kwargs):
    return [
        _Item(function, {"framework": framework, "route": route, "change": change}, **kwargs)
        for framework in ("sdk", "adk")
        for route in routes
        for change in changes
    ]


def test_the_sdk_keeps_a_covering_subset_and_adk_stays_whole() -> None:
    items = _matrix()
    marked = mark_nightly_framework_cases(items)
    sdk = [item for item in items if item.callspec.params["framework"] == "sdk"]
    adk = [item for item in items if item.callspec.params["framework"] == "adk"]
    assert all(not item.marks for item in adk)
    kept = [item for item in sdk if not item.marks]
    assert marked == len(sdk) - len(kept) and marked > 0
    # Every route and every change value is still met on the SDK reader.
    assert {item.callspec.params["route"] for item in kept} == {"a", "b", "c"}
    assert {item.callspec.params["change"] for item in kept} == {"added", "removed"}
    assert len(kept) == 3 < len(sdk)
    assert all(item.marks in ([], ["slow"]) for item in sdk)


def test_a_function_with_one_case_per_value_loses_none() -> None:
    items = [_Item("test_y", {"framework": "sdk", "patch": patch}) for patch in ("one", "two", "three")]
    assert mark_nightly_framework_cases(items) == 0


def test_both_import_spellings_of_the_sdk_stay_distinct_routes() -> None:
    items = [
        _Item("test_z", {"family": family, "route": route}, path="tests/test_builder_calls.py")
        for family in ("agents", "openai_agents", "google.adk")
        for route in ("x", "y")
    ]
    mark_nightly_framework_cases(items)
    kept = {item.callspec.params["family"] for item in items if not item.marks}
    assert {"agents", "openai_agents", "google.adk"} == kept
    assert all(not item.marks for item in items if item.callspec.params["family"] == "google.adk")


def test_only_the_named_files_and_framework_arguments_are_thinned() -> None:
    other_file = _matrix(path="tests/test_scan.py")
    no_framework = [_Item("test_w", {"route": value}) for value in "abcd"]
    neither_value = _matrix(routes=("a",))
    for item in neither_value:
        item.callspec.params["framework"] = "claude" if item.callspec.params["framework"] == "sdk" else "other"
    assert mark_nightly_framework_cases([*other_file, *no_framework, *neither_value]) == 0


def test_the_choice_is_deterministic_and_greedy() -> None:
    first = covering_subset(_matrix(routes=("a", "b", "c", "d"), changes=("added", "changed", "removed"))[::2])
    again = covering_subset(_matrix(routes=("a", "b", "c", "d"), changes=("added", "changed", "removed"))[::2])
    assert [item.name for item in first] == [item.name for item in again]


def test_every_thinned_file_exists_and_the_values_are_the_sdk_spellings() -> None:
    assert all((REPO_ROOT / path).is_file() for path in THINNED_FILES)
    assert THINNED_VALUES == {"sdk", "agents", "openai_agents"}


def _collect(*args: str) -> list[str]:
    env = dict(os.environ)
    env.pop("SHIPGATE_TEST_SHARDS", None)
    env.pop("SHIPGATE_TEST_SHARD", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    return [line for line in result.stdout.splitlines() if re.match(r"tests/\S+::", line)]


def test_the_real_collection_moves_cases_to_slow_and_deletes_none() -> None:
    path = "tests/test_framework_constructor_imports.py"
    everything = _collect(path)
    slow = _collect(path, "-m", "slow")
    default = _collect(path, "-m", "not slow")
    assert everything and slow and default
    assert sorted([*slow, *default]) == sorted(everything)
    assert all(case.endswith("-sdk]") for case in slow)
    assert sum(case.endswith("-adk]") for case in default) == sum(case.endswith("-adk]") for case in everything)
    # Every route is still met on the SDK reader in the default run.
    routes = {re.search(r"\[(\w+)-", case).group(1) for case in everything if case.endswith("-sdk]")}
    kept = {re.search(r"\[(\w+)-", case).group(1) for case in default if case.endswith("-sdk]")}
    assert kept == routes
