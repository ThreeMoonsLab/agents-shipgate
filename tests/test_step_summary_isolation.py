"""The suite must not write into the hosted runner's step summary or outputs (#617).

On a hosted runner ``GITHUB_STEP_SUMMARY`` and ``GITHUB_OUTPUT`` point at the
job's real files, and fixture scans that inherit them append their reports
there. The root conftest removes both at import time, so this runs a few real
fixture tests in a fresh pytest session that starts with both variables set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# A module-scoped in-process verify and a module-scoped CLI scan: both write
# the step summary whenever the variable reaches them.
FIXTURE_TESTS = (
    "tests/test_human_review_presentation.py::"
    "test_real_verify_publishes_the_question_in_the_existing_pr_comment",
    "tests/test_report_1_0_compatibility_fixtures.py::"
    "test_the_cli_emits_the_frozen_schema_and_says_so_in_its_contract",
)


def test_fixture_scans_leave_an_inherited_summary_and_output_untouched(tmp_path: Path) -> None:
    sentinel = b"written by the runner before pytest started\n"
    summary = tmp_path / "step-summary.md"
    output = tmp_path / "github-output"
    summary.write_bytes(sentinel)
    output.write_bytes(sentinel)

    # Shard settings would deselect the named tests, and PYTEST_ADDOPTS would
    # carry the outer run's flags (xdist, coverage) into the inner session.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTEST_ADDOPTS", "SHIPGATE_TEST_SHARD", "SHIPGATE_TEST_SHARDS"}
    }
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    env["GITHUB_OUTPUT"] = str(output)

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--basetemp", str(tmp_path / "inner"), *FIXTURE_TESTS,
        ],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )

    inner = result.stdout[-3000:] + result.stderr[-3000:]
    assert summary.read_bytes() == sentinel, inner
    assert output.read_bytes() == sentinel, inner
    assert result.returncode == 0, inner
