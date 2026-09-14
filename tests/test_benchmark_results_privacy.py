"""Committed benchmark runs carry no machine-local home or temporary paths.

Each harness's `vendor.py` masks the case files it vendors, but nothing rewrote
`runs.json`: a live run records third-party configuration verbatim, and two
public repositories commit a home directory into theirs. The runs of record were
masked by hand. This keeps an unmasked run from being committed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmark"
UNMASKED = re.compile(r"/private/tmp/|/(?:Users|home)/(?!<user>/)[^/\s\"')]+/")
RUNS = sorted(BENCHMARK.glob("*/results/*/runs.json"))


def test_the_sweep_sees_every_harness() -> None:
    assert {path.relative_to(BENCHMARK).parts[0] for path in RUNS} >= {"cold-start", "host-config", "mcp-servers"}


@pytest.mark.parametrize("runs", RUNS, ids=lambda path: str(path.relative_to(BENCHMARK)))
def test_committed_runs_mask_local_paths(runs: Path) -> None:
    found = sorted(set(UNMASKED.findall(runs.read_text(encoding="utf-8"))))
    assert not found, f"{runs.relative_to(BENCHMARK)} carries unmasked local paths: {found[:5]}"
