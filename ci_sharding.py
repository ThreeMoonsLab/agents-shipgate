"""How the test suite is split across parallel CI jobs.

A module of its own, rather than a helper inside ``conftest.py``, for one
practical reason: ``conftest`` is not an importable name — a test that writes
``from conftest import …`` gets whichever conftest is nearest on ``sys.path``,
which in this repository is ``tests/harness/conftest.py``. The repository root
is on ``sys.path`` for the whole suite, so this module is reachable by name
from anywhere and has one definition.

Pure. It reads nothing, writes nothing, and takes no environment: the caller
supplies the collection (and the measured seconds, when it has them) and gets
back an assignment. That is what lets ``tests/test_shard_partition.py`` assert
the properties directly.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path

#: Measured seconds per test file, written by
#: ``scripts/measure_shard_seconds.py`` from a full ``--junitxml`` run.
SECONDS_FILE = Path(__file__).resolve().parent / "tests" / "shard_seconds.json"


def load_seconds(path: Path = SECONDS_FILE) -> dict[str, float]:
    """The measured seconds per file, or nothing when there is no measurement.

    A missing or unreadable file balances on item counts alone, as before:
    the measurement only ever improves the balance, never the coverage.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return {}
    return {
        str(name): float(value)
        for name, value in files.items()
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
    }


def shard_assignment(
    paths: Mapping[str, int],
    shards: int,
    seconds: Mapping[str, float] | None = None,
) -> dict[str, int]:
    """Assign whole test *files* to shards, balancing their expected time.

    Two properties, and both are load-bearing.

    **Whole files, never individual tests.** Module- and session-scoped
    fixtures are per file, and this repository has expensive ones — a wheel
    build, a materialized git fixture, a cached adapter pass. Splitting one
    file across shards would pay for those in every shard that got a piece.

    **Deterministic from the collection alone.** Every shard collects the whole
    suite and computes this same assignment, then keeps its own slice. Nothing
    is exchanged between jobs, so the union of the shards is exactly the suite
    and no test can fall between two of them — which a test asserts.

    **Measured time where there is one.** Item count alone was a poor proxy:
    a file of forty git-fixture tests costs more than a file of four hundred
    pure ones. Balancing counts left one shard at 13 of its 15 minutes on
    ``main``, while another took 8. Adding any test file reshuffled most files
    between shards, so an unrelated PR could tip a shard past its cap (#904).
    ``seconds`` holds each file's measured time. A file without a measurement
    (new, or renamed since) costs its item count times the measured seconds
    per item. A stale measurement only unbalances; it never drops a file.
    Balance is checked in ``tests/test_shard_partition.py`` rather than
    assumed.
    """

    cost = _costs(paths, seconds or {})
    load = [0.0] * shards
    owner: dict[str, int] = {}
    for path in sorted(paths, key=lambda item: (-cost[item], item)):
        target = min(range(shards), key=lambda index: (load[index], index))
        owner[path] = target
        load[target] += cost[path]
    return owner


def _costs(paths: Mapping[str, int], seconds: Mapping[str, float]) -> dict[str, float]:
    known = {path: seconds[path] for path in sorted(paths) if path in seconds}
    known_items = sum(paths[path] for path in known)
    # ``fsum`` over a sorted order: every shard computes the same rate, bit
    # for bit, whatever order its collection listed the files in.
    rate = math.fsum(known.values()) / known_items if known_items else 1.0
    return {path: known.get(path, paths[path] * rate) for path in paths}
