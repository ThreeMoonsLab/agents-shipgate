"""The `slow` marker moves a test off the pull-request path and never off the gate.

Marking a test `slow` is a decision to run it later than a pull request would:
nightly, and in release verification. If the nightly workflow stopped running,
or release verification started excluding the marker too, a `slow` test would
simply never run, and every job would stay green. These tests hold the three
sides of that bargain: pull-request CI leaves `slow` out, the nightly workflow
runs it, and release verification still does.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github/workflows"
NIGHTLY = "slow-tests.yml"
RUN_STEP = "Run the slow tests"


def _workflow(name: str) -> dict[str, Any]:
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1).
    if True in document:
        document["on"] = document.pop(True)
    return document


def _steps(name: str, job: str) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in _workflow(name)["jobs"][job]["steps"] if "name" in step}


def test_pull_request_ci_leaves_slow_tests_out() -> None:
    ci_steps = _steps("ci.yml", "suite")
    ci_test = next(step for name, step in ci_steps.items() if name.startswith("Test (shard"))
    assert '-m "not perf and not slow"' in ci_test["run"]


def test_the_nightly_workflow_runs_exactly_the_slow_tests() -> None:
    run = _steps(NIGHTLY, "slow")[RUN_STEP]["run"]

    assert "python -m pytest -n auto -m slow" in run


def test_the_nightly_workflow_is_scheduled_and_dispatchable_and_read_only() -> None:
    workflow = _workflow(NIGHTLY)

    assert set(workflow["on"]) == {"schedule", "workflow_dispatch"}
    assert len(workflow["on"]["schedule"]) == 1
    assert re.fullmatch(r"\d+ \d+ \* \* \*", workflow["on"]["schedule"][0]["cron"])
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["slow"]  # nothing broader than the file's
    assert "environment" not in workflow["jobs"]["slow"]
    assert "secrets" not in (WORKFLOWS / NIGHTLY).read_text(encoding="utf-8")


def test_every_action_the_nightly_workflow_uses_is_pinned_to_a_commit() -> None:
    uses = [
        step["uses"]
        for step in _workflow(NIGHTLY)["jobs"]["slow"]["steps"]
        if "uses" in step
    ]

    assert uses
    for reference in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", reference), reference


def test_the_nightly_workflow_installs_what_the_suite_job_installs() -> None:
    """Same checkout (with tags), same interpreter, same hash-locked closure.

    "Passed on `main` last night" is only a statement about the packages CI
    approved if the install is the one CI performs.
    """

    nightly = _steps(NIGHTLY, "slow")
    suite = _steps("ci.yml", "suite")

    assert nightly["Install"]["run"] == suite["Install"]["run"]
    assert nightly["Set up Python"] == suite["Set up Python"]
    assert nightly["Checkout"]["with"]["fetch-tags"] is True
    assert (
        nightly["Verify the dependency locks match the declared requirements"]["run"]
        == suite["Verify the dependency locks match the declared requirements"]["run"]
    )


@pytest.mark.skipif(shutil.which("sh") is None or shutil.which("bash") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize(
    ("pytest_status", "expected"),
    [
        (0, 0),  # the slow tests ran and passed
        (5, 0),  # nothing is marked slow yet: pytest's "no tests collected"
        (1, 1),  # a slow test failed
        (2, 2),  # interrupted, or a collection error
        (3, 3),  # an internal error
        (4, 4),  # a usage error, such as a mistyped option
    ],
)
def test_only_pytests_no_tests_collected_status_is_treated_as_success(
    pytest_status: int, expected: int, tmp_path: Path
) -> None:
    """A fake `python` answers with each pytest exit status.

    Status 5 is not an error here: an empty selection is the nightly run's
    state until the first test is marked. Every other non-zero status keeps its
    meaning, so a failure, a crash and a mistyped option all still fail the job.
    """

    shim = tmp_path / "bin"
    shim.mkdir()
    record = tmp_path / "argv.txt"
    python = shim / "python"
    python.write_text(
        f'#!/bin/sh\nprintf "%s " "$@" > "{record}"\nexit {pytest_status}\n', encoding="utf-8"
    )
    python.chmod(0o755)
    script = _steps(NIGHTLY, "slow")[RUN_STEP]["run"]

    run = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert run.returncode == expected, run.stdout + run.stderr
    assert record.read_text(encoding="utf-8").strip() == "-m pytest -n auto -m slow"
