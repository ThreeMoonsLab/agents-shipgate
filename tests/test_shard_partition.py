"""The CI shard partition: every test runs, in exactly one shard.

Sharding is the one CI change that can go wrong *silently*. A partition that
drops a file makes every job green while a test stops running, and nothing in
the output says so — which is why the properties below are asserted rather than
trusted, and why ``conftest.py`` raises instead of returning an empty shard.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from ci_sharding import SECONDS_FILE, load_seconds, shard_assignment

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


@pytest.mark.parametrize("shards", [2, 3, 4, 6, 7])
def test_every_file_lands_in_exactly_one_shard(shards: int) -> None:
    """The union of the shards is the suite, and nothing is in two of them."""

    owner = shard_assignment(_COLLECTION, shards)
    assert set(owner) == set(_COLLECTION)
    assert all(0 <= value < shards for value in owner.values())


@pytest.mark.parametrize("shards", [2, 3, 4, 6])
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


def test_measured_seconds_outweigh_item_counts() -> None:
    """A slow file of few tests is balanced by its time, not its count (#904).

    By count, the 20-item file below is a twentieth of the 400-item one and
    shares a shard. Measured, it takes most of the suite's time, and it gets a
    shard to itself.
    """

    def sharing(owner: dict[str, int]) -> list[str]:
        return [path for path, shard in owner.items() if shard == owner["tests/test_small_a.py"]]

    seconds = {"tests/test_small_a.py": 600.0, "tests/test_huge.py": 60.0, "tests/test_large.py": 60.0}
    assert sharing(shard_assignment(_COLLECTION, 2)) != ["tests/test_small_a.py"]
    assert sharing(shard_assignment(_COLLECTION, 2, seconds)) == ["tests/test_small_a.py"]


def test_an_unmeasured_file_costs_its_items_at_the_measured_rate() -> None:
    """A new file weighs what its items would at the suite's seconds per item."""

    seconds = {"tests/test_huge.py": 400.0}  # one second per item
    owner = shard_assignment({"tests/test_huge.py": 400, "tests/test_new.py": 400}, 2, seconds)
    assert owner["tests/test_huge.py"] != owner["tests/test_new.py"]


@pytest.mark.parametrize("shards", [2, 3, 4, 6])
def test_the_timed_assignment_is_deterministic(shards: int) -> None:
    seconds = {path: count * 0.37 for path, count in _COLLECTION.items() if "tiny" not in path}
    first = shard_assignment(_COLLECTION, shards, seconds)
    reordered = dict(reversed(list(_COLLECTION.items())))
    assert shard_assignment(reordered, shards, dict(reversed(list(seconds.items())))) == first
    assert set(first) == set(_COLLECTION)


def test_a_missing_or_malformed_measurement_balances_by_count(tmp_path: Path) -> None:
    assert load_seconds(tmp_path / "absent.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert load_seconds(broken) == {}
    odd = tmp_path / "odd.json"
    odd.write_text('{"files": {"tests/a.py": 3, "tests/b.py": -1, "tests/c.py": true, "tests/d.py": "x"}}')
    assert load_seconds(odd) == {"tests/a.py": 3.0}


def test_the_committed_measurement_names_test_files() -> None:
    """The measurement is a weight per test file, and every weight is usable."""

    seconds = load_seconds(SECONDS_FILE)
    assert seconds, f"{SECONDS_FILE} holds no measurement"
    assert all(re.fullmatch(r"tests/\S+\.py", path) for path in seconds)
    assert all(value >= 0 for value in seconds.values())


def _collect(shard: int | None, shards: int | None) -> dict[str, int]:
    """Collect the pull-request CI suite and return ``{file: item count}``.

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
            "not perf and not slow",
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


@functools.cache
def _whole_collection() -> dict[str, int]:
    """The unsharded CI collection, taken once per process."""

    return _collect(None, None)


#: Test files the sharded CI suite does not run, so they carry no weight: the
#: static-only adapter test is ignored by the CI command and the latency-budget
#: tests are ``perf``, which have their own step.
_OUTSIDE_THE_SHARDED_SUITE = frozenset({
    "tests/test_adapter_static_only.py",
    "tests/test_latency_budget.py",
})


def test_every_file_the_ci_suite_collects_has_a_measured_weight() -> None:
    """An unmeasured file is guessed at, and a wrong guess overruns a shard.

    A file without a weight costs its item count at the suite's average seconds
    per item, which is how a handful of git-fixture files weighed like pure
    ones and one shard ran past its cap (#904). New test files are the usual
    cause. Re-measure instead of guessing:

        python -m pytest -n auto -m "not perf and not slow" \\
            --ignore=tests/test_adapter_static_only.py --junitxml=junit.xml
        python scripts/measure_shard_seconds.py junit.xml
    """

    measured = load_seconds(SECONDS_FILE)
    unmeasured = sorted(set(_whole_collection()) - set(measured) - _OUTSIDE_THE_SHARDED_SUITE)
    assert not unmeasured, (
        f"{len(unmeasured)} collected test file(s) have no weight in "
        f"{SECONDS_FILE.name}: {unmeasured}"
    )


def test_the_files_outside_the_sharded_suite_are_not_in_it() -> None:
    """The allow-list names files CI really does not run in a shard."""

    assert not _OUTSIDE_THE_SHARDED_SUITE & set(_whole_collection())


def test_the_real_collection_partitions_exhaustively() -> None:
    """The property that matters, on the actual suite rather than a fixture.

    If the shards ever fail to cover the suite, a test stops running in CI
    while every job stays green — the one failure mode of sharding that says
    nothing at all.
    """

    whole = _whole_collection()
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


def test_which_shard_owns_a_file_does_not_depend_on_the_marker_selection(tmp_path: Path) -> None:
    """Release verification runs CI's shards, and the ``slow`` tests CI leaves out.

    ``conftest.py`` partitions the collection as it stands before ``-m`` drops
    anything, so a file lands in the same shard whether or not its tests are
    selected. That is what lets one ``tests/shard_seconds.json`` balance both
    pipelines, and what makes a release candidate's shard N the same files as
    CI's shard N, with the ``slow`` ones added. It rests on pytest calling a
    ``conftest`` hook before its own deselection, so it is checked here against
    a small project rather than taken from the documentation.

    The counts are chosen so that partitioning *after* deselection would move
    ``test_b.py``: ``test_a.py`` carries three ``slow`` items, which weigh in
    only when they are still in the collection.
    """

    shutil.copy(REPO_ROOT / "conftest.py", tmp_path / "conftest.py")
    shutil.copy(REPO_ROOT / "ci_sharding.py", tmp_path / "ci_sharding.py")
    tests = tmp_path / "tests"
    tests.mkdir()
    suites = {
        "test_a.py": "def test_fast():\n    pass\n"
        + "".join(f"\n@pytest.mark.slow\ndef test_slow_{n}():\n    pass\n" for n in range(3)),
        "test_b.py": "".join(f"def test_{n}():\n    pass\n\n" for n in range(3)),
        "test_c.py": "".join(f"def test_{n}():\n    pass\n\n" for n in range(2)),
        "test_d.py": "".join(f"\n@pytest.mark.slow\ndef test_{n}():\n    pass\n" for n in range(2)),
    }
    for name, body in suites.items():
        (tests / name).write_text("import pytest\n\n" + body, encoding="utf-8")

    def owners(selection: str) -> dict[str, int]:
        found: dict[str, int] = {}
        for shard in (1, 2):
            env = dict(os.environ)
            env["SHIPGATE_TEST_SHARDS"] = "2"
            env["SHIPGATE_TEST_SHARD"] = str(shard)
            result = subprocess.run(
                [
                    sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-W", "ignore",
                    "-o", "addopts=", "-m", selection, "--collect-only", "-q", "-q",
                ],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
            for line in result.stdout.splitlines():
                match = re.fullmatch(r"(tests/\S+\.py): (\d+)", line.strip())
                if match:
                    found[match.group(1)] = shard
        return found

    complete = owners("not perf")
    pull_request = owners("not perf and not slow")

    assert set(complete) == {f"tests/{name}" for name in suites}
    # A file whose tests are all `slow` has nothing left to run in CI...
    assert "tests/test_d.py" not in pull_request
    # ...and every other file is where it is in the complete selection.
    assert pull_request == {path: shard for path, shard in complete.items() if path in pull_request}
    assert set(pull_request) == set(complete) - {"tests/test_d.py"}


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


def test_remeasurement_records_provenance_from_the_same_report_bytes(tmp_path, monkeypatch):
    import hashlib
    import json

    from scripts import measure_shard_seconds as measure

    report = tmp_path / "junit.xml"
    original = b'<testsuite><testcase file="tests/test_a.py" time="1.2"/><testcase file="tests/test_b.py" time="2.3"/></testsuite>'
    report.write_bytes(original)
    output = tmp_path / "weights.json"
    monkeypatch.setattr(measure, "OUTPUT", output)
    read = measure.file_seconds

    def change_report_after_capture(path, root, *, report_bytes=None):
        path.write_bytes(b'<testsuite><testcase file="tests/test_c.py" time="999"/></testsuite>')
        return read(path, root, report_bytes=report_bytes)

    monkeypatch.setattr(measure, "file_seconds", change_report_after_capture)
    assert measure.main([str(report)]) == 0
    payload = json.loads(output.read_text())
    assert payload["files"] == {"tests/test_a.py": 1.2, "tests/test_b.py": 2.3}
    assert payload["measurement"]["report_sha256"] == hashlib.sha256(original).hexdigest()
    assert payload["measurement"]["case_count"] == payload["measurement"]["file_count"] == 2
    assert payload["measurement"]["source"] == "junit"
