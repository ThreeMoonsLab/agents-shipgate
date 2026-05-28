"""Opt-in phase-timing instrumentation for ``run_scan``.

Off by default — when disabled, ``phase()`` returns a no-op context
manager and there is **zero measurement overhead** on the hot path
(one attribute lookup, one boolean check).

When enabled, each ``with phase("name"):`` block captures a wallclock
duration via ``time.perf_counter()`` and accumulates it under the
named phase. Repeated enter/exit on the same phase name sums.

Enable via ``AGENTS_SHIPGATE_PERF=1`` (read once per process at first
``is_enabled()`` call), or programmatically with ``enable()``. The
benchmark runner and ``tests/test_latency_budget.py`` use the
programmatic API.

This module is intentionally tiny (≈80 LOC) and free of external
imports — it must be importable from the ``run_scan`` hot path without
adding measurable cold-start cost. It is NOT part of the public stable
contract: callers outside the benchmark suite should not depend on
its shape.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field

_ENV_VAR = "AGENTS_SHIPGATE_PERF"


@dataclass
class _State:
    """Process-local enabled flag + accumulator.

    A single instance lives at module level. Tests and the benchmark
    runner own its lifecycle via ``enable()``/``disable()``/``reset()``;
    the scan pipeline only reads ``enabled`` and calls ``record()``.
    """

    enabled: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    """phase_name → accumulated seconds (sum across all enter/exit pairs)"""
    counts: dict[str, int] = field(default_factory=dict)
    """phase_name → number of enter/exit pairs (for sanity in nested scans)"""

    def record(self, name: str, seconds: float) -> None:
        self.timings[name] = self.timings.get(name, 0.0) + seconds
        self.counts[name] = self.counts.get(name, 0) + 1


_STATE = _State()


def is_enabled() -> bool:
    """Return True if phase timing is active for this process.

    Reads the env var on first call to support enabling via shell
    without requiring an explicit ``enable()`` call from a wrapper.
    Subsequent calls trust the in-memory flag — set programmatically
    via ``enable()`` / ``disable()``.
    """
    if not _STATE.enabled:
        value = os.environ.get(_ENV_VAR, "")
        if value.lower() in {"1", "true", "yes", "on"}:
            _STATE.enabled = True
    return _STATE.enabled


def enable() -> None:
    """Turn on phase timing for the current process."""
    _STATE.enabled = True


def disable() -> None:
    """Turn off phase timing and discard accumulator state.

    Intended for test teardown; the benchmark runner uses
    ``snapshot()`` then ``reset()`` instead so it can keep the flag on
    across iterations.
    """
    _STATE.enabled = False
    _STATE.timings.clear()
    _STATE.counts.clear()


def reset() -> None:
    """Clear accumulator state without changing the enabled flag."""
    _STATE.timings.clear()
    _STATE.counts.clear()


def snapshot() -> dict[str, float]:
    """Return a copy of the accumulated phase timings (seconds)."""
    return dict(_STATE.timings)


def counts() -> dict[str, int]:
    """Return a copy of the per-phase enter/exit counts.

    Useful for tests that want to assert a phase ran exactly once.
    """
    return dict(_STATE.counts)


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Time a phase of the scan pipeline.

    No-op (``nullcontext``-equivalent) when ``is_enabled()`` is False —
    so the production hot path pays nothing when perf measurement is
    off. The check is one boolean read.
    """
    if not is_enabled():
        with nullcontext():
            yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        _STATE.record(name, time.perf_counter() - start)
