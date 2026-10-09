"""A sharded suite cannot pass on a partial run.

Release verification used to run the whole suite in one job, so "the suite
passed" was one job's status. It now runs in six, as CI's does, and the gate
that follows them is the only place the six become one claim. These tests hold
the ways that claim could quietly become smaller than it says:

* a shard that fails or never runs, behind a gate that is simply skipped;
* a gate that combines five of six coverage fragments and passes;
* a shard that tested a different commit than the one being sealed;
* a coverage floor measured on one shard instead of on all of them.

The first three are shell the gate runs, so they are run here, against built
evidence, rather than read for the right words. The structure around them (what
the gate waits for, and what waits for it) is read from the workflows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github/workflows"
RELEASE_VERIFICATIONS = ("release-verify.yml", "release-advisory-verify.yml")

#: Every job that gates on a sharded suite: each release verification's `tests`
#: and CI's `coverage`.
RESULT_GATES = (
    ("release-verify.yml", "tests"),
    ("release-advisory-verify.yml", "tests"),
    ("ci.yml", "coverage"),
)

SHARDS = 6
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"

REPORTED = "Require every shard to have reported, and to have tested this commit"
COMBINED = "Combine the shards' coverage and enforce the floor"
RESULT_CHECK = "Require every suite shard to have passed"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="the gate is a bash script")


def _jobs(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]


def _script(name: str, step: str, job: str = "tests") -> str:
    for candidate in _jobs(name)[job]["steps"]:
        if candidate.get("name") == step:
            return str(candidate["run"])
    raise AssertionError(f"{name} {job} has no gate step named {step!r}")


def _clean_environment() -> dict[str, str]:
    """This process's environment, minus whatever is measuring this process.

    The suite runs under ``--cov`` in CI, and pytest-cov hands its settings to
    every subprocess so they are measured too. A subprocess here is measuring
    something of its own, in a temporary directory, and must not also write into
    the shard's real coverage data.
    """

    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COV_CORE_", "COVERAGE_"))
    }


def _bash(script: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a step the way GitHub does: `bash -e`, with `python` on the PATH."""

    shim = cwd / ".shim"
    shim.mkdir(exist_ok=True)
    python = shim / "python"
    if not python.exists():
        # A script rather than a symlink: an interpreter finds its virtual
        # environment from the path it was started by, which a link elsewhere
        # would not carry.
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        python.chmod(0o755)
    base = _clean_environment()
    environment = {**base, "PATH": f"{shim}{os.pathsep}{base['PATH']}", **env}
    return subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


# --- the gate starts after a failed shard, so that it can fail ----------------


@pytest.mark.parametrize(("name", "job"), RESULT_GATES)
def test_the_gate_waits_for_the_suite_and_still_starts_after_a_failed_shard(
    name: str, job: str
) -> None:
    """`needs` alone would skip the gate, and a skipped job is not a failed one.

    The gate's own `if` lets it start whatever the shards did (bar
    cancellation), so its first step can fail it by name. In CI that matters
    because a skipped required check counts as passing, and the ruleset requires
    only the first three shards. In release verification the sealer gets no such
    latitude: it needs the gate and runs only if the gate succeeded.
    """

    jobs = _jobs(name)
    gate = jobs[job]

    assert gate["needs"] in ("suite", ["suite"])
    assert "!cancelled()" in gate["if"]
    first = gate["steps"][0]
    assert first["name"] == RESULT_CHECK
    assert first["env"]["SUITE_RESULT"] == "${{ needs.suite.result }}"
    if name != "ci.yml":
        assert jobs["artifact"]["needs"] == "tests"
        assert "if" not in jobs["artifact"]


@pytest.mark.parametrize("name", RELEASE_VERIFICATIONS)
def test_no_shard_is_allowed_to_fail_quietly(name: str) -> None:
    suite = _jobs(name)["suite"]

    assert "continue-on-error" not in suite
    assert "if" not in suite
    for step in suite["steps"]:
        assert "continue-on-error" not in step, step["name"]
        assert "if" not in step, step["name"]


@pytest.mark.parametrize(("name", "job"), RESULT_GATES)
@pytest.mark.parametrize(
    ("result", "passes"),
    [
        ("success", True),
        ("failure", False),
        ("cancelled", False),
        # What a shard job reports when something upstream of it was skipped,
        # or when an `if` is added to it later. The check must refuse it.
        ("skipped", False),
        ("", False),
    ],
)
def test_only_a_fully_successful_suite_opens_the_gate(
    name: str, job: str, result: str, passes: bool, tmp_path: Path
) -> None:
    run = _bash(_script(name, RESULT_CHECK, job), tmp_path, {"SUITE_RESULT": result})

    assert (run.returncode == 0) is passes, run.stdout + run.stderr
    if not passes:
        assert "::error::" in run.stdout
        assert "refusing to gate" in run.stdout


# --- the gate holds the shards to what they claim -----------------------------


def _evidence(
    root: Path,
    *,
    shards: int = SHARDS,
    wrong_commit: int | None = None,
    no_fragment: int | None = None,
    no_record: int | None = None,
) -> None:
    for index in range(1, shards + 1):
        shard = root / "suite-evidence" / f"release-suite-evidence-{index}"
        shard.mkdir(parents=True)
        if index != no_fragment:
            (shard / "coverage-fragment.dat").write_bytes(b"fragment")
        if index != no_record:
            commit = OTHER_SHA if index == wrong_commit else SHA
            (shard / "source-sha.txt").write_text(f"{commit}\n", encoding="utf-8")


def _reported(name: str, root: Path) -> subprocess.CompletedProcess[str]:
    return _bash(
        _script(name, REPORTED),
        root,
        {"EXPECTED_SHARDS": str(SHARDS), "TESTED_SHA": SHA},
    )


@pytest.mark.parametrize("name", RELEASE_VERIFICATIONS)
def test_every_shard_reporting_the_gated_commit_passes(name: str, tmp_path: Path) -> None:
    _evidence(tmp_path)

    run = _reported(name, tmp_path)

    assert run.returncode == 0, run.stdout + run.stderr
    assert f"{SHARDS} shards" in run.stdout


@pytest.mark.parametrize("name", RELEASE_VERIFICATIONS)
@pytest.mark.parametrize(
    "case",
    [
        {"shards": SHARDS - 1},
        {"shards": SHARDS + 1},
        {"no_fragment": 3},
        {"no_record": 4},
        {"wrong_commit": 2},
    ],
    ids=["a-shard-missing", "an-unexpected-shard", "no-fragment", "no-record", "wrong-commit"],
)
def test_a_partial_or_mixed_set_of_shards_is_refused(
    name: str, case: dict[str, int], tmp_path: Path
) -> None:
    _evidence(tmp_path, **case)

    run = _reported(name, tmp_path)

    assert run.returncode != 0, run.stdout
    assert "::error::" in run.stdout


@pytest.mark.parametrize("name", RELEASE_VERIFICATIONS)
def test_nothing_downloaded_is_refused_rather_than_combined(name: str, tmp_path: Path) -> None:
    (tmp_path / "suite-evidence").mkdir()

    run = _reported(name, tmp_path)

    assert run.returncode != 0, run.stdout


# --- the floor is measured over every shard, not any one ----------------------

_MODULE = """\
import sys


def one():
    value = 1
    value += 1
    value += 1
    value += 1
    return value


def two():
    value = 2
    value += 1
    value += 1
    value += 1
    return value


{call}()
"""


def _fragment(root: Path, index: int, call: str) -> None:
    """Coverage data from a run that executed only one of the module's halves."""

    module = root / "measured.py"
    module.write_text(_MODULE.format(call=call), encoding="utf-8")
    shard = root / "suite-evidence" / f"release-suite-evidence-{index}"
    shard.mkdir(parents=True)
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", str(module)],
        cwd=root,
        env={**_clean_environment(), "COVERAGE_FILE": str(shard / "coverage-fragment.dat")},
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize("name", RELEASE_VERIFICATIONS)
def test_the_floor_holds_for_the_combined_shards_and_not_for_one(
    name: str, tmp_path: Path
) -> None:
    """Each fragment alone measures about 60%, which is below 85; together the
    two halves cover the module. A gate that measured the last fragment, or the
    first, would fail a run that passes, and one that reported only whichever
    fragment it saw would pass nothing worth the name."""

    both = tmp_path / "both"
    only_one = tmp_path / "only_one"
    for root in (both, only_one):
        root.mkdir()
    _fragment(both, 1, "one")
    _fragment(both, 2, "two")
    _fragment(only_one, 1, "one")

    combined = _bash(_script(name, COMBINED), both, {})
    alone = _bash(_script(name, COMBINED), only_one, {})

    assert combined.returncode == 0, combined.stdout + combined.stderr
    assert "combining 2 coverage fragment(s)" in combined.stdout
    assert alone.returncode != 0, alone.stdout


# --- CI's coverage job holds the same line ------------------------------------

CI_COMBINE = "Combine and enforce the threshold"


def _ci_fragments(root: Path, *, halves: tuple[str, ...]) -> None:
    """One CI-shaped fragment directory per entry, each from a real coverage run.

    The module that was measured is left at ``root/measured.py``, where
    ``coverage report`` looks for its source.
    """

    runs: dict[str, bytes] = {}
    for call in sorted(set(halves)):
        _fragment(root, 1, call)
        evidence = root / "suite-evidence"
        runs[call] = (evidence / "release-suite-evidence-1" / "coverage-fragment.dat").read_bytes()
        shutil.rmtree(evidence)
    for index, call in enumerate(halves, start=1):
        shard = root / "coverage-fragments" / f"coverage-{index}"
        shard.mkdir(parents=True)
        (shard / "coverage-fragment.dat").write_bytes(runs[call])


def _ci_combine(root: Path) -> subprocess.CompletedProcess[str]:
    step = next(
        candidate
        for candidate in _jobs("ci.yml")["coverage"]["steps"]
        if candidate.get("name") == CI_COMBINE
    )
    return _bash(
        str(step["run"]), root, {"EXPECTED_FRAGMENTS": str(step["env"]["EXPECTED_FRAGMENTS"])}
    )


def test_ci_combines_every_shard_and_refuses_to_combine_fewer(tmp_path: Path) -> None:
    """Six fragments that together cover the module pass the floor; five do not
    get that far, though five of them cover it just as well, because the
    measurement would then be of five sixths of the suite."""

    whole, partial = tmp_path / "whole", tmp_path / "partial"
    whole.mkdir()
    partial.mkdir()
    halves = ("one", "two", "one", "two", "one", "two")
    _ci_fragments(whole, halves=halves)
    _ci_fragments(partial, halves=halves[:-1])

    combined = _ci_combine(whole)
    refused = _ci_combine(partial)

    assert combined.returncode == 0, combined.stdout + combined.stderr
    assert "combining 6 coverage fragment(s)" in combined.stdout
    assert refused.returncode != 0
    assert "expected 6 coverage fragments, found 5" in refused.stderr


def test_ci_measures_the_floor_over_the_combined_fragments(tmp_path: Path) -> None:
    """Six fragments that all ran the same half measure about 60%: below 85."""

    _ci_fragments(tmp_path, halves=("one",) * 6)

    run = _ci_combine(tmp_path)

    assert run.returncode != 0, run.stdout
    assert "combining 6 coverage fragment(s)" in run.stdout
