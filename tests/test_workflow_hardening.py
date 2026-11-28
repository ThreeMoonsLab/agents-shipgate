"""Every workflow is held to the same four rules (#1033).

`adoption-harness.yml` was the one workflow that broke all of them at once: a
`workflow_dispatch` input expanded straight into a `run:` script, in a job that
held `ANTHROPIC_API_KEY` in its job-level environment, with actions pinned by
tag, an unhashed install and no timeout. A dispatcher could put shell syntax in
`matrix_file` and run it beside the key. Nothing noticed, because each rule was
a convention the other workflows happened to follow rather than a check.

These tests read every workflow under `.github/workflows/`, and the composite
actions under `.github/actions/`, and hold each one to the rules:

* No `${{ ... }}` expression inside a `run:` script. GitHub substitutes it into
  the script text before the shell starts, so an input, an event field or a
  step output becomes shell syntax. Values reach a script through `env:`.
* Every action and reusable workflow from another repository is pinned to a
  full commit SHA. A tag or branch can be moved to different code.
* A credential (anything read from `secrets`, however it is spelled, or
  `github.token`) is given to a single step, through that step's `env:` or
  `with:`, never to a whole workflow, job, job container or service.
* Every job that runs steps has a `timeout-minutes`.

The last section holds the other half of #1033: in `ci.yml`, a failing
`mcp-extra` job fails `coverage`, a check the ruleset requires.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((REPO_ROOT / ".github/workflows").glob("*.yml"))
COMPOSITE_ACTIONS = sorted((REPO_ROOT / ".github/actions").glob("*/action.yml"))
HARNESS = REPO_ROOT / ".github/workflows/adoption-harness.yml"

_PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
# `secrets` as a word, so `secrets.X`, `secrets['X']` and `toJSON(secrets)` all
# count.
_CREDENTIAL = re.compile(r"\bsecrets\b|\bgithub\.token\b")
_FROM_INPUTS = re.compile(r"\$\{\{\s*(github\.event\.)?inputs\.")
_EXPRESSION = re.compile(r"\$\{\{")


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), path
    return document


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _jobs(path: Path) -> dict[str, dict[str, Any]]:
    return _load(path).get("jobs") or {}


def _steps(path: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every step in a workflow or composite action, labelled for messages."""

    document = _load(path)
    if "runs" in document:
        for index, step in enumerate(document["runs"].get("steps") or []):
            yield f"step {step.get('name', index)!r}", step
        return
    for job_name, job in (document.get("jobs") or {}).items():
        for index, step in enumerate(job.get("steps") or []):
            yield f"job {job_name!r}, step {step.get('name', index)!r}", step


def test_the_rules_have_something_to_read() -> None:
    names = {path.name for path in WORKFLOWS}

    assert {"adoption-harness.yml", "ci.yml", "release.yml"} <= names
    assert [path.parent.name for path in COMPOSITE_ACTIONS] == ["install-release-toolchain"]


@pytest.mark.parametrize("path", WORKFLOWS + COMPOSITE_ACTIONS, ids=_relative)
def test_no_run_script_contains_an_expression(path: Path) -> None:
    offenders = [
        label for label, step in _steps(path) if _EXPRESSION.search(str(step.get("run", "")))
    ]

    assert not offenders, (
        f"{_relative(path)}: a `${{{{ }}}}` expression inside `run:` is substituted into the "
        f"script before the shell starts; pass it through the step's `env:` instead: {offenders}"
    )


@pytest.mark.parametrize("path", WORKFLOWS + COMPOSITE_ACTIONS, ids=_relative)
def test_every_remote_action_is_pinned_to_a_commit(path: Path) -> None:
    references = [(label, str(step["uses"])) for label, step in _steps(path) if "uses" in step]
    references += [
        (f"job {name!r}", str(job["uses"])) for name, job in _jobs(path).items() if "uses" in job
    ]

    unpinned = [
        (label, reference)
        for label, reference in references
        if not reference.startswith("./") and not _PINNED.match(reference)
    ]

    assert not unpinned, f"{_relative(path)}: pin these to a full commit SHA: {unpinned}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_relative)
def test_credentials_are_given_to_single_steps(path: Path) -> None:
    document = _load(path)
    broad = []
    if _CREDENTIAL.search(str(document.get("env") or "")):
        broad.append("workflow env")
    for name, job in (document.get("jobs") or {}).items():
        if _CREDENTIAL.search(str(job.get("env") or "")):
            broad.append(f"job {name!r} env")
        container = job.get("container")
        if isinstance(container, dict) and _CREDENTIAL.search(str(container.get("env") or "")):
            broad.append(f"job {name!r} container env")
        for service, definition in (job.get("services") or {}).items():
            if isinstance(definition, dict) and _CREDENTIAL.search(str(definition.get("env") or "")):
                broad.append(f"job {name!r} service {service!r} env")

    assert not broad, (
        f"{_relative(path)}: a credential in a workflow, job, container or service environment "
        f"reaches more than the step that needs it; give it to that one step: {broad}"
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=_relative)
def test_every_job_that_runs_steps_has_a_timeout(path: Path) -> None:
    missing = [
        name
        for name, job in _jobs(path).items()
        if "steps" in job and not (isinstance(job.get("timeout-minutes"), int) and job["timeout-minutes"] > 0)
    ]

    assert not missing, f"{_relative(path)}: jobs without a positive `timeout-minutes`: {missing}"


# --------------------------------------------------------------------------
# adoption-harness.yml, the workflow that prompted the rules
# --------------------------------------------------------------------------


def test_the_harness_api_key_reaches_only_the_step_that_runs_the_harness() -> None:
    holders = [
        label for label, step in _steps(HARNESS) if _CREDENTIAL.search(str(step.get("env") or ""))
    ]

    assert holders == ["job 'run', step 'Run harness'"]
    run_step = next(step for label, step in _steps(HARNESS) if label == holders[0])
    assert run_step["env"]["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"


def test_the_harness_expands_every_dispatch_input_only_inside_quotes() -> None:
    """Whatever variable a step's `env:` fills from a dispatch input, its
    script expands only as `"${NAME}"`, so an input is one argument and never
    shell syntax. The variables are read from the workflow, so an input added
    later is held to the same rule without a test edit."""

    declared = set(_load(HARNESS)[True]["workflow_dispatch"]["inputs"])
    used: set[str] = set()
    for label, step in _steps(HARNESS):
        for variable, value in (step.get("env") or {}).items():
            match = _FROM_INPUTS.search(str(value))
            if not match:
                continue
            used.add(str(value)[match.end() :].split()[0].rstrip("}"))
            script = str(step.get("run", ""))
            assert re.search(rf'"\$\{{{variable}\}}"', script), (label, variable)
            assert not re.search(rf'(?<!")\$\{{?{variable}\b(?!\}}")', script), (label, variable)

    assert used == declared


def test_the_harness_installs_only_hash_locked_closures() -> None:
    installs = [
        line.strip()
        for _, step in _steps(HARNESS)
        for line in str(step.get("run", "")).splitlines()
        if "pip install" in line
    ]

    assert installs
    for line in installs:
        if " -e " in line:
            # An editable install cannot be hashed; it brings nothing in with it.
            assert "--no-deps" in line and "--no-build-isolation" in line, line
        else:
            assert re.search(r"--require-hashes --requirement constraints/[a-z-]+\.txt$", line), line
    assert any("constraints/harness.txt" in line for line in installs)


def test_the_harness_pins_the_actions_ci_pins() -> None:
    def pins(path: Path) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for _, step in _steps(path):
            if "uses" in step and not str(step["uses"]).startswith("./"):
                action, _, ref = str(step["uses"]).partition("@")
                found.setdefault(action, set()).add(ref)
        return found

    harness = pins(HARNESS)
    ci = pins(REPO_ROOT / ".github/workflows/ci.yml")

    assert harness
    for action, refs in harness.items():
        assert refs == ci[action], action


# --------------------------------------------------------------------------
# ci.yml: a failing mcp-extra job fails a required check
# --------------------------------------------------------------------------
#
# The "Protect main" ruleset requires `coverage` but neither `mcp extra` leg,
# so a red mcp-extra job left a pull request mergeable (#1033). `coverage`
# now waits for it and fails unless it succeeded. The ruleset itself is the
# owner's to change; this is the in-repository half, and it holds without it.

CI = REPO_ROOT / ".github/workflows/ci.yml"
MCP_GATE = "Require both mcp-extra jobs to have passed"


def _mcp_gate() -> dict[str, Any]:
    [step] = [step for step in _jobs(CI)["coverage"]["steps"] if step.get("name") == MCP_GATE]
    return step


def test_coverage_waits_for_mcp_extra_and_runs_whatever_it_returned() -> None:
    jobs = _jobs(CI)
    coverage = jobs["coverage"]

    assert set(jobs["mcp-extra"]["strategy"]["matrix"]["sdk"]) == {"floor", "newest"}
    assert "mcp-extra" in coverage["needs"]
    # Without this, a failed dependency would skip `coverage`, and a skipped
    # required check counts as passing. `!cancelled()` is about the workflow
    # run, not the job it needs: a timed-out mcp-extra job still lets it run.
    assert coverage["if"] == "${{ !cancelled() }}"
    assert _mcp_gate()["env"] == {"MCP_EXTRA_RESULT": "${{ needs.mcp-extra.result }}"}
    steps = [step.get("name") for step in coverage["steps"]]
    # Before anything that could fail for another reason, so it fails by name.
    assert steps.index(MCP_GATE) < steps.index("Checkout")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    ("result", "passes"),
    [("success", True), ("failure", False), ("cancelled", False), ("skipped", False), ("", False)],
)
def test_the_mcp_gate_passes_only_a_successful_mcp_extra(result: str, passes: bool) -> None:
    completed = subprocess.run(
        ["bash", "-c", _mcp_gate()["run"]],
        env={"PATH": os.environ.get("PATH", ""), "MCP_EXTRA_RESULT": result},
        capture_output=True,
        text=True,
        check=False,
    )

    assert (completed.returncode == 0) is passes, completed.stdout + completed.stderr
