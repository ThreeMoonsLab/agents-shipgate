"""End-to-end gating tests for the ``run`` command.

Pins three round-eight regressions:
- empty-run guard exits non-zero (P1.1)
- out-of-repo ``--out`` is rejected before any cell runs (P1.3)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from harness.adoption import cli as cli_mod

REPO_ROOT = Path(cli_mod._repo_root())


def _run_harness(*args: str) -> subprocess.CompletedProcess[str]:
    """Spawn ``python -m harness.adoption`` as a real subprocess.

    A real process exit code is required for these tests; ``typer``'s
    in-process testing utilities mask the actual exit semantics.
    """
    cmd = [sys.executable, "-m", "harness.adoption", *args]
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_empty_run_exits_nonzero(repo_tmp_path: Path) -> None:
    """``--budget-usd 0`` aborts before any cell completes. The matrix is
    non-empty but ``scorecards`` ends empty — this must exit non-zero
    (not silently pass as 'green')."""
    csv_path = repo_tmp_path / "empty.csv"
    result = _run_harness(
        "run",
        "--matrix=benchmark/matrix.yaml",
        "--agent=cursor-static",
        "--budget-usd=0",
        f"--results-csv={csv_path}",
    )
    assert result.returncode != 0, (
        f"expected non-zero exit on empty run; stdout={result.stdout!r}"
    )
    combined = result.stdout + result.stderr
    assert "0 scorecards" in combined


def test_out_of_repo_out_dir_rejected_before_any_cell(tmp_path: Path) -> None:
    """``--out=/tmp/...`` must be rejected by preflight BEFORE any cell
    runs. Without preflight, a paid Claude cell would complete and only
    then crash inside ``_relative_artifacts_path``."""
    out_dir = tmp_path / "harness-out-test"
    out_dir.mkdir()
    result = _run_harness(
        "run",
        "--matrix=benchmark/matrix.yaml",
        "--agent=cursor-static",
        "--budget-usd=1",
        f"--out={out_dir}",
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not under repo root" in combined
    assert "FAIL" in combined
    # No "[1/12]" cell progress line should appear — preflight must fire
    # before the loop starts.
    assert "[1/" not in combined, "cell loop ran despite invalid --out"
