"""The CI shard partition: every test runs, in exactly one shard.

Sharding is the one CI change that can go wrong *silently*. A partition that
drops a file makes every job green while a test stops running, and nothing in
the output says so — which is why the properties below are asserted rather than
trusted, and why ``conftest.py`` raises instead of returning an empty shard.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from ci_sharding import shard_assignment

REPO_ROOT = Path(__file__).resolve().parent.parent

#: A stand-in for a real collection: uneven file sizes, including one file
#: several times larger than the rest, which is the shape that makes a naive
#: round-robin unbalanced.
_COLLECTION = {
    f"tests/test_{name}.py": count
    for name, count in (
        ("huge", 400),
        ("large", 180),
        ("medium_a", 90),
        ("medium_b", 85),
        ("small_a", 20),
        ("small_b", 17),
        ("small_c", 11),
        ("tiny_a", 3),
        ("tiny_b", 2),
        ("tiny_c", 1),
    )
}


@pytest.mark.parametrize("shards", [2, 3, 4, 7])
def test_every_file_lands_in_exactly_one_shard(shards: int) -> None:
    """The union of the shards is the suite, and nothing is in two of them."""

    owner = shard_assignment(_COLLECTION, shards)
    assert set(owner) == set(_COLLECTION)
    assert all(0 <= value < shards for value in owner.values())


@pytest.mark.parametrize("shards", [2, 3, 4])
def test_the_assignment_is_deterministic(shards: int) -> None:
    """Each shard computes the whole partition and keeps its slice.

    Nothing is exchanged between the parallel jobs, so two runs of this
    function — in two processes, on two machines — must agree, or a file
    would run twice or not at all.
    """

    first = shard_assignment(_COLLECTION, shards)
    reordered = dict(reversed(list(_COLLECTION.items())))
    assert shard_assignment(reordered, shards) == first


def test_a_dominant_file_does_not_leave_a_shard_idle() -> None:
    """Greedy descending placement, not round-robin.

    Round-robin on collection order puts the 400-item file and the 90-item
    file in the same shard whenever their indices agree modulo the shard
    count. Packing largest-first keeps the biggest bin under half the work.
    """

    owner = shard_assignment(_COLLECTION, 3)
    load = Counter(owner[path] for path in _COLLECTION)
    weighted = Counter()
    for path, count in _COLLECTION.items():
        weighted[owner[path]] += count
    assert len(load) == 3, "every shard must receive at least one file"
    total = sum(weighted.values())
    assert max(weighted.values()) / total < 0.55


def _collect(shard: int | None, shards: int | None) -> dict[str, int]:
    """Collect the CI suite and return ``{file: item count}``.

    ``-q`` twice is what makes ``--collect-only`` print the per-file summary
    rather than node ids, and ``addopts`` already supplies one. Counting items
    per file, not listing them, is the right granularity here: the partition
    assigns whole files, so a file-level union is the property, and the counts
    let the totals be compared as well as the names.
    """

    env = dict(os.environ)
    env.pop("SHIPGATE_TEST_SHARDS", None)
    env.pop("SHIPGATE_TEST_SHARD", None)
    if shard is not None and shards is not None:
        env["SHIPGATE_TEST_SHARDS"] = str(shards)
        env["SHIPGATE_TEST_SHARD"] = str(shard)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not perf",
            "--ignore=tests/test_adapter_static_only.py",
            "--collect-only",
            "-q",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    collected: dict[str, int] = {}
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"(tests/\S+\.py): (\d+)", line.strip())
        if match:
            collected[match.group(1)] = int(match.group(2))
    return collected


def test_the_real_collection_partitions_exhaustively() -> None:
    """The property that matters, on the actual suite rather than a fixture.

    If the shards ever fail to cover the suite, a test stops running in CI
    while every job stays green — the one failure mode of sharding that says
    nothing at all.
    """

    whole = _collect(None, None)
    assert whole, "the baseline collection found no tests"
    shards = [_collect(index, 3) for index in (1, 2, 3)]
    union: dict[str, int] = {}
    for index, files in enumerate(shards, start=1):
        assert files, f"shard {index} collected nothing"
        overlap = set(union) & set(files)
        assert not overlap, f"shard {index} repeats files from an earlier shard: {sorted(overlap)}"
        union.update(files)
    assert union == whole, {
        "missing_from_shards": sorted(set(whole) - set(union)),
        "not_in_the_suite": sorted(set(union) - set(whole)),
        "count_disagreements": sorted(
            path for path in set(whole) & set(union) if whole[path] != union[path]
        ),
    }
    assert sum(union.values()) == sum(whole.values())


@pytest.mark.parametrize(
    ("shards", "shard"),
    [("3", ""), ("", "2"), ("3", "0"), ("3", "4"), ("0", "1"), ("three", "1")],
)
def test_a_half_configured_shard_is_refused(shards: str, shard: str) -> None:
    """A shard that runs nothing must never report success.

    Every one of these used to be expressible: an unset half, an index outside
    the range, a zero count. Each would have produced a green job that ran a
    fraction of the suite, or none of it.
    """

    env = dict(os.environ)
    env["SHIPGATE_TEST_SHARDS"] = shards
    env["SHIPGATE_TEST_SHARD"] = shard
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_shard_partition.py", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0, result.stdout[-2000:]
