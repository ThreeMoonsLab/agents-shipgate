"""CI observations preserve selection and never turn partial files into weights."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ci_timing import PREFIX, FileTimings

ROOT = Path(__file__).resolve().parent.parent


def _finish(timings, node, *, setup="passed", call="passed", teardown="passed"):
    events = timings.report(node, "setup", 1.0, setup)
    if setup == "passed":
        events += timings.report(node, "call", 2.0, call)
    return events + timings.report(node, "teardown", 3.0, teardown)


def test_interleaved_reports_require_every_selected_teardown():
    timings = FileTimings()
    ids = ["tests/test_a.py::test_one", "tests/test_a.py::test_two", "tests/test_b.py::test_three"]
    (census,) = timings.collection(ids)
    assert census["selected"] == 3
    assert timings.collection(ids) == []  # Two workers share one census.
    assert _finish(timings, ids[0]) == []
    (other,) = _finish(timings, ids[2])
    assert other["file"] == "tests/test_b.py" and other["seconds"] == 6.0
    assert timings.report(ids[1], "setup", 1.0, "passed") == []
    assert timings.report(ids[1], "call", 2.0, "passed") == []
    (complete,) = timings.report(ids[1], "teardown", 3.0, "passed")
    assert complete["selected"] == complete["completed"] == 2
    assert complete["seconds"] == 12.0


@pytest.mark.parametrize("setup,call,teardown,seconds,failed", [
    ("skipped", "passed", "passed", 4.0, 0),
    ("failed", "passed", "passed", 4.0, 1),
    ("passed", "failed", "passed", 6.0, 1),
    ("passed", "passed", "failed", 6.0, 1),
])
def test_failure_and_skip_timings_remain_outcome_bearing(setup, call, teardown, seconds, failed):
    timings = FileTimings()
    node = "tests/test_a.py::test_one"
    timings.collection([node])
    (event,) = _finish(timings, node, setup=setup, call=call, teardown=teardown)
    assert event["kind"] == "complete_file" and event["seconds"] == seconds
    assert event["phase_outcomes"].get("failed", 0) == failed


@pytest.mark.parametrize("phase,duration,outcome", [
    ("???", 0.0, "failed"),
    ("call", float("nan"), "passed"),
    ("call", -1.0, "passed"),
    ("call", True, "passed"),
    ("call", 1.0, "rerun"),
    ("setup", 1.0, "passed"),
])
def test_unsupported_reports_cannot_establish_a_complete_file(phase, duration, outcome):
    timings = FileTimings()
    node = "tests/test_a.py::test_one"
    timings.collection([node])
    timings.report(node, "setup", 1.0, "passed")
    assert timings.report(node, phase, duration, outcome)[0]["kind"] == "invalidate_file"
    assert timings.report(node, "teardown", 1.0, "passed") == []


def test_retry_after_emission_and_collection_mismatch_revoke_prior_measurements():
    timings = FileTimings()
    node = "tests/test_a.py::test_one"
    timings.collection([node])
    assert _finish(timings, node)[0]["kind"] == "complete_file"
    assert timings.report(node, "setup", 1.0, "passed")[0]["kind"] == "invalidate_file"
    assert timings.collection(["tests/test_b.py::test_other"])[0]["kind"] == "invalidate_session"


def test_cancellation_preserves_only_completed_files_and_omits_parameter_payloads():
    timings = FileTimings()
    ids = ["tests/test_a.py::test_one[PRIVATE_PARAMETER]", "tests/test_b.py::test_unfinished"]
    census = timings.collection(ids)
    completed = _finish(timings, ids[0])
    assert timings.report(ids[1], "setup", 1.0, "passed") == []
    assert timings.report(ids[1], "call", 1.0, "passed") == []
    assert [event["file"] for event in completed] == ["tests/test_a.py"]
    assert "PRIVATE_PARAMETER" not in json.dumps(census + completed)
    assert census[0]["files"]["tests/test_b.py"] == 1


def _run(tmp_path, code, *, hosted="true", distributed=True, extra_args=()):
    for name in ("conftest.py", "ci_sharding.py", "ci_timing.py"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_probe.py").write_text(code)
    env = {**os.environ, "GITHUB_ACTIONS": hosted}
    for key in ("SHIPGATE_TEST_SHARD", "SHIPGATE_TEST_SHARDS", "PYTEST_ADDOPTS", "GITHUB_STEP_SUMMARY"):
        env.pop(key, None)
    command = [sys.executable, "-m", "pytest", "-q", "-o", "junit_duration_report=total",
               "--junitxml=" + str(tmp_path / "result.xml")]
    if distributed:
        command += ["-n", "2", "--max-worker-restart=0"]
    command += list(extra_args)
    result = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60)
    records = [line for line in result.stdout.splitlines() if PREFIX in line]
    assert all(line.startswith(PREFIX) for line in records), result.stdout
    events = [json.loads(line.removeprefix(PREFIX)) for line in records]
    return result, events


def test_actual_xdist_phase_timings_match_explicit_total_junit_and_selected_cases(tmp_path):
    result, events = _run(tmp_path, """import pytest
@pytest.mark.parametrize('value', [1, 2], ids=['PRIVATE_ONE', 'PRIVATE_TWO'])
def test_pass(value):
    assert value
@pytest.fixture
def skip_setup():
    pytest.skip('fixture skip')
def test_skip(skip_setup):
    assert False
@pytest.fixture
def broken_setup():
    raise RuntimeError('fixture error')
def test_error(broken_setup):
    assert False
""")
    assert result.returncode == 1, result.stdout + result.stderr
    census = [event for event in events if event["kind"] == "census"]
    complete = [event for event in events if event["kind"] == "complete_file"]
    assert len(census) == len(complete) == 1
    assert census[0]["selected"] == complete[0]["completed"] == 4
    assert complete[0]["phase_outcomes"]["failed"] == 1
    assert complete[0]["phase_outcomes"]["skipped"] == 1
    assert complete[0]["workers_observed"] == 2
    cases = list(ET.parse(tmp_path / "result.xml").getroot().iter("testcase"))
    assert len(cases) == 4
    assert complete[0]["seconds"] == pytest.approx(sum(float(case.get("time")) for case in cases), abs=0.01)
    assert "PRIVATE_ONE" not in json.dumps(events) and "PRIVATE_TWO" not in json.dumps(events)


@pytest.mark.parametrize("hosted,distributed", [("false", True), ("true", False)])
def test_non_hosted_and_serial_pytest_emit_no_timing_records(tmp_path, hosted, distributed):
    result, events = _run(tmp_path, "def test_pass():\n    assert True\n", hosted=hosted, distributed=distributed)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not events


def test_actual_worker_crash_does_not_publish_a_complete_file(tmp_path):
    result, events = _run(tmp_path, "import os\ndef test_crash():\n    os._exit(99)\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert any(event["kind"] in {"invalidate_file", "invalidate_session"} for event in events)
    assert not any(event["kind"] == "complete_file" for event in events)


def test_passed_setup_without_a_call_cannot_complete_the_file():
    timings = FileTimings()
    node = "tests/test_a.py::test_one"
    timings.collection([node])
    timings.report(node, "setup", 1.0, "passed")
    assert timings.report(node, "teardown", 1.0, "passed")[0]["kind"] == "invalidate_file"


@pytest.mark.parametrize("setup", ["skipped", "failed"])
def test_a_call_after_unexecuted_setup_invalidates_timing(setup):
    timings = FileTimings()
    node = "tests/test_a.py::test_one"
    timings.collection([node])
    timings.report(node, "setup", 1.0, setup)
    assert timings.report(node, "call", 1.0, "passed")[0]["kind"] == "invalidate_file"
    assert timings.report(node, "teardown", 1.0, "passed") == []


def test_actual_setup_only_run_is_inert_and_never_claims_execution(tmp_path):
    result, events = _run(tmp_path, "def test_not_run():\n    raise AssertionError('body must remain unexecuted')\n", extra_args=("--setup-only",))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not events
