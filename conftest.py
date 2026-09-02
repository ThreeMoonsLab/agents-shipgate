"""Root pytest conftest.

Ensures the local repo root (for ``harness.*``) and ``src/`` (for
``agents_shipgate.*``) are first on ``sys.path`` so tests resolve modules
from THIS worktree rather than any editable install from another worktree.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"

for path in (REPO_ROOT, SRC_DIR):
    string = str(path)
    if string not in sys.path:
        sys.path.insert(0, string)

existing_pythonpath = os.environ.get("PYTHONPATH", "")
parts = [str(REPO_ROOT), str(SRC_DIR)]
if existing_pythonpath:
    parts.append(existing_pythonpath)
# Subprocess smoke tests must import this worktree, not another editable install.
os.environ["PYTHONPATH"] = os.pathsep.join(parts)

import pytest  # noqa: E402

from ci_sharding import shard_assignment  # noqa: E402


@pytest.fixture(autouse=True)
def _scrub_agent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite hermetic when it runs inside a coding agent.

    Claude Code exports ``CLAUDECODE=1`` and Cursor ``CURSOR_TRACE_ID`` in
    every shell they spawn, which auto-enables agent mode (see
    ``agents_shipgate.cli.agent_mode.is_agent_mode``). Tests that want
    agent mode set ``AGENTS_SHIPGATE_AGENT_MODE`` explicitly.
    """
    for var in ("CLAUDECODE", "CURSOR_TRACE_ID", "AGENTS_SHIPGATE_AGENT_MODE"):
        monkeypatch.delenv(var, raising=False)


#: How the suite is split across parallel CI jobs. ``SHIPGATE_TEST_SHARDS`` is
#: the number of shards and ``SHIPGATE_TEST_SHARD`` the 1-based index of this
#: one. Unset means "run everything", which is what a local run does.
_SHARD_COUNT_VAR = "SHIPGATE_TEST_SHARDS"
_SHARD_INDEX_VAR = "SHIPGATE_TEST_SHARD"


def _shard_selection() -> tuple[int, int] | None:
    """Read the requested shard, or ``None`` for an unsharded run.

    Fails loudly on a half-configured or out-of-range request. A shard that
    quietly selects nothing is the worst outcome available here: the job goes
    green having run no tests, and nothing says so.
    """

    raw_count = os.environ.get(_SHARD_COUNT_VAR, "").strip()
    raw_index = os.environ.get(_SHARD_INDEX_VAR, "").strip()
    if not raw_count and not raw_index:
        return None
    if not raw_count or not raw_index:
        raise RuntimeError(
            f"{_SHARD_COUNT_VAR} and {_SHARD_INDEX_VAR} must be set together; "
            f"got {_SHARD_COUNT_VAR}={raw_count!r} {_SHARD_INDEX_VAR}={raw_index!r}"
        )
    try:
        count, index = int(raw_count), int(raw_index)
    except ValueError as exc:
        raise RuntimeError(f"test shard selection must be integers: {exc}") from exc
    if count < 1 or not (1 <= index <= count):
        raise RuntimeError(
            f"test shard {index} of {count} is not a shard that exists"
        )
    return count, index


def pytest_collection_modifyitems(config, items) -> None:  # noqa: ANN001
    """Keep only this shard's files when a shard is requested."""

    selection = _shard_selection()
    if selection is None:
        return
    shards, index = selection
    if shards == 1:
        return
    counts: dict[str, int] = {}
    for item in items:
        counts[item.location[0]] = counts.get(item.location[0], 0) + 1
    owner = shard_assignment(counts, shards)
    keep = [item for item in items if owner[item.location[0]] == index - 1]
    dropped = [item for item in items if owner[item.location[0]] != index - 1]
    if not keep:
        raise RuntimeError(
            f"shard {index} of {shards} selected no tests from "
            f"{len(counts)} file(s); a shard that runs nothing must not pass"
        )
    items[:] = keep
    if dropped:
        config.hook.pytest_deselected(items=dropped)
