"""How the test suite is split across parallel CI jobs.

A module of its own, rather than a helper inside ``conftest.py``, for one
practical reason: ``conftest`` is not an importable name — a test that writes
``from conftest import …`` gets whichever conftest is nearest on ``sys.path``,
which in this repository is ``tests/harness/conftest.py``. The repository root
is on ``sys.path`` for the whole suite, so this module is reachable by name
from anywhere and has one definition.

Pure. It reads nothing, writes nothing, and takes no environment: the caller
supplies the collection and gets back an assignment. That is what lets
``tests/test_shard_partition.py`` assert the properties directly.
"""

from __future__ import annotations

from collections.abc import Mapping


def shard_assignment(paths: Mapping[str, int], shards: int) -> dict[str, int]:
    """Assign whole test *files* to shards, balancing collected item counts.

    Two properties, and both are load-bearing.

    **Whole files, never individual tests.** Module- and session-scoped
    fixtures are per file, and this repository has expensive ones — a wheel
    build, a materialized git fixture, a cached adapter pass. Splitting one
    file across shards would pay for those in every shard that got a piece.

    **Deterministic from the collection alone.** Every shard collects the whole
    suite and computes this same assignment, then keeps its own slice. Nothing
    is exchanged between jobs, so the union of the shards is exactly the suite
    and no test can fall between two of them — which a test asserts.

    Item count is a proxy for time, and an imperfect one; it is used because it
    is free and needs no stored measurements to go stale. Balance is checked in
    ``tests/test_shard_partition.py`` rather than assumed.
    """

    load = [0] * shards
    owner: dict[str, int] = {}
    for path, count in sorted(paths.items(), key=lambda item: (-item[1], item[0])):
        target = min(range(shards), key=lambda index: (load[index], index))
        owner[path] = target
        load[target] += count
    return owner
