"""What every shipped consumer path does with a frozen `1.0` report (#569).

The freeze is a promise made to consumers, so the fixtures that hold it have
to be about consumers -- not about the schema document, which
``tests/test_report_1_0_contract.py`` already covers.

Three paths, because those are the three a reader actually reaches:

* the **CLI as a process**, driven as a subprocess with every entry-point
  environment variable cleared, so what is exercised is the command an adopter
  types rather than an in-process call. It runs *this* working tree: a genuinely
  installed distribution is exercised by the RC exercise in
  ``scripts/release_engine_smoke.py``, which builds and installs a wheel, and
  these fixtures deliberately do not claim to replace it;
* the **generated Action workflow**, through the same
  ``scripts/github_action_outputs.py`` reader the workflow runs;
* the **machine control/report readers** -- the per-command boundaries that
  take a ``report.json`` back as input, in both directions: a current report is
  read, a pre-freeze one is refused by name.

The refusal half matters more than the acceptance half. Accepting the current
report is what any build does. Refusing a pre-freeze one, at every boundary, by
name, with a route, is the behavior the freeze added, and it is the behavior a
later reader is most likely to relax by accident.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agents_shipgate.schemas.report_compatibility import (
    LAST_PRE_FREEZE_REPORT_SCHEMA_VERSION,
    current_report_schema_version,
    report_schema_refusal_code,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
SAMPLE = REPO_ROOT / "samples/support_refund_agent/shipgate.yaml"
CURRENT = current_report_schema_version()


def _worktree_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run this working tree's CLI as a subprocess, with a pinned environment.

    Every environment variable that retargets the entry point is cleared --
    ``AGENTS_SHIPGATE_CLI`` above all, because a command is spelled for the
    entry point that produced it and an inherited value sends this into a
    different install.

    It then pins ``PYTHONPATH`` to this worktree's ``src/`` **on purpose**: the
    ``.venv`` editable install points at the main checkout, so without the pin
    a worktree run would test somebody else's source. That is also the honest
    limit of these fixtures. They exercise the process boundary, not a built
    distribution; a wheel is built and installed by the RC exercise in
    ``scripts/release_engine_smoke.py``.
    """

    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "AGENTS_SHIPGATE_CLI",
            "AGENTS_SHIPGATE_ENABLE_PLUGINS",
            "CLAUDECODE",
            "CURSOR_TRACE_ID",
        }
    }
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "agents_shipgate", *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real scan, driven through the CLI, reused by the readers below."""

    out = tmp_path_factory.mktemp("scan-1-0")
    result = _worktree_cli(
        "scan", "-c", str(SAMPLE), "--out", str(out),
        "--format", "json,markdown", "--ci-mode", "advisory",
    )
    assert (out / "report.json").is_file(), (
        f"the CLI wrote no report (exit {result.returncode}): {result.stderr[-2000:]}"
    )
    return out


# --------------------------------------------------------------------------
# Consumer path 1: the CLI as a process
# --------------------------------------------------------------------------


def test_the_cli_emits_the_frozen_schema_and_says_so_in_its_contract(scanned: Path) -> None:
    """One build, one answer: what it emits and what it advertises agree.

    A build whose ``contract --json`` names a schema its reports do not carry
    sends every consumer to the wrong document -- and the qualification gate,
    which compares that pin for exact equality, rejects every receipt for a
    reason that has nothing to do with safety (#416).
    """

    payload = json.loads((scanned / "report.json").read_text(encoding="utf-8"))
    contract = _worktree_cli("contract", "--json")
    assert contract.returncode == 0, contract.stderr[-2000:]
    advertised = json.loads(contract.stdout)["report_schema_version"]

    assert payload["report_schema_version"] == CURRENT
    assert advertised == CURRENT


def test_the_emitted_report_validates_against_the_schema_it_names(scanned: Path) -> None:
    """The published document is the contract; the emitted bytes must meet it."""

    payload = json.loads((scanned / "report.json").read_text(encoding="utf-8"))
    schema_path = DOCS / f"report-schema.v{payload['report_schema_version']}.json"
    assert schema_path.is_file(), f"{schema_path.name} is not published"
    Draft202012Validator(
        json.loads(schema_path.read_text(encoding="utf-8"))
    ).validate(payload)


# --------------------------------------------------------------------------
# Consumer path 2: the generated Action workflow
# --------------------------------------------------------------------------


def test_the_action_output_reader_reads_a_frozen_report(scanned: Path) -> None:
    """The workflow gates on these outputs; a schema move must not blank them.

    ``extract_outputs`` reads the report positionally -- ``summary``,
    ``release_decision``, ``baseline`` -- so a renamed or re-parented block
    would not raise here, it would quietly publish empty strings and a workflow
    would gate on ``''``. Asserting the *values* is what makes that visible.
    """

    from scripts.github_action_outputs import extract_outputs

    outputs = extract_outputs(scanned)
    assert outputs["decision"], "the Action's `decision` output is empty"
    assert outputs["status"], "the Action's `status` output is empty"
    assert outputs["report_json"] == scanned / "report.json"
    # Counts the workflow compares numerically. An absent or re-parented block
    # would publish `''` here and every `== '0'` condition would silently flip.
    for key in ("blocker_count", "review_item_count", "critical_count"):
        assert isinstance(outputs[key], int), f"{key} is not a number"


def test_the_generated_workflow_names_no_report_schema_version() -> None:
    """A workflow that hardcoded a schema version would break on every bump.

    The engine's contract is the place a version is stated. `init --ci` writes
    a workflow that reads whatever the installed build emits, so a freeze -- or
    any later `1.x` minor -- needs no adopter edit. This is a standing
    property, checked here because the freeze is exactly the change that would
    expose a hardcoded one.
    """

    from agents_shipgate.cli.discovery.ci_workflow import _render_workflow_template

    text = _render_workflow_template()
    assert "report-schema.v" not in text
    assert "report_schema_version" not in text


# --------------------------------------------------------------------------
# Consumer path 3: machine control / report readers
# --------------------------------------------------------------------------


def _pre_freeze_copy(scanned: Path, tmp_path: Path) -> Path:
    """The scanned report with *only* its version rewritten.

    A single-variable experiment: the bytes are otherwise this build's own
    output, so the only thing a refusal can be about is the version. It is also
    the exact shape of the mistake the freeze forbids -- relabelling an
    artifact instead of regenerating it.
    """

    payload = json.loads((scanned / "report.json").read_text(encoding="utf-8"))
    payload["report_schema_version"] = LAST_PRE_FREEZE_REPORT_SCHEMA_VERSION
    path = tmp_path / "pre-freeze-report.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("findings", ("findings", "--from", "{report}", "--json")),
        ("explain-finding", ("explain-finding", "fp_x", "--from", "{report}")),
        ("scenario suggest", ("scenario", "suggest", "--from", "{report}", "--out", "{out}")),
        ("evidence-packet", ("evidence-packet", "--from", "{report}")),
    ],
)
def test_every_report_input_boundary_refuses_a_relabelled_pre_freeze_report(
    scanned: Path, tmp_path: Path, name: str, argv: tuple[str, ...]
) -> None:
    """Same artifact, same refusal, at every command that takes one back."""

    report = _pre_freeze_copy(scanned, tmp_path)
    resolved = [
        part.format(report=str(report), out=str(tmp_path / f"{name.split()[0]}-out.yaml"))
        for part in argv
    ]
    result = _worktree_cli(*resolved)

    assert result.returncode != 0, f"{name} accepted a pre-freeze report"
    combined = result.stdout + result.stderr
    assert report_schema_refusal_code(combined) == "report_schema_pre_freeze", (
        f"{name} refused without a recognisable reason code:\n{combined[-1500:]}"
    )
    assert "agents-shipgate scan" in combined, (
        f"{name} refused without naming a route the reader can take"
    )


def test_the_diff_from_boundary_refuses_a_pre_freeze_base_without_aborting_the_scan(
    scanned: Path, tmp_path: Path
) -> None:
    """The comparison boundary withholds a verdict rather than failing the run.

    This is the one boundary that must *not* simply exit non-zero: a scan whose
    base cannot be compared still has a head to describe. It records the
    refusal as a `source_warning`, raises a `provide_source` evidence gap, and
    lands on `insufficient_evidence` -- so nothing downstream reads a verdict
    that was never established.
    """

    from agents_shipgate.cli.scan import run_scan

    base = _pre_freeze_copy(scanned, tmp_path)
    report, exit_code = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=base,
        packet_enabled=False,
    )

    assert exit_code == 0, "an incomparable base must not abort the scan"
    assert report.tool_surface_diff.enabled is False
    warning = next(
        item
        for item in report.source_warnings
        if report_schema_refusal_code(item) == "report_schema_pre_freeze"
    )
    gap = next(
        item
        for item in report.release_decision.evidence_coverage.evidence_gaps
        if item.subject == warning
    )
    assert gap.next_action.kind == "provide_source"
    # This sample has real blockers of its own, so the verdict is `blocked` --
    # a stronger outcome than the withheld one, not a weaker one. What must
    # hold here is that an incomparable base can never leave a `passed`.
    # `tests/test_scan.py::test_pre_freeze_diff_reference_requires_regeneration
    # _instead_of_effect_deltas` pins the withheld verdict on a sample whose
    # own findings do not already decide it.
    assert report.release_decision.decision != "passed"


def test_a_current_report_is_accepted_by_the_same_boundaries(scanned: Path, tmp_path: Path) -> None:
    """The refusals above must not be a boundary that refuses everything.

    A gate nothing passes is indistinguishable from a broken one, and it is the
    failure mode a fail-closed change actually ships with.
    """

    result = _worktree_cli(
        "findings", "--from", str(scanned / "report.json"), "--json"
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert report_schema_refusal_code(result.stdout + result.stderr) is None
