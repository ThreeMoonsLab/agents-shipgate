"""#698: phase timing for host-mode `diff` is opt-in and never changes output.

The measurement exists so the first-run latency of `agents-shipgate diff` can
be attributed to a phase instead of guessed at (docs/engineering/
diff-first-run-latency.md). Two properties matter more than the numbers: the
instrumentation must not alter what the command prints, and the harness that
reads it must keep naming the phases the document reports.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate import _perf
from agents_shipgate.cli.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "scripts" / "measure_diff_phases.py"

runner = CliRunner()

BASE_SETTINGS = '{"permissions": {"allow": ["Bash(pytest *)"]}}'
WIDE_SETTINGS = '{"permissions": {"allow": ["Bash(*)"]}}'

#: Phases a host-mode `diff` of a repository with no hook script must record.
EXPECTED_PHASES = {
    "diff.resolve_base",
    "diff.head_snapshot",
    "diff.base_materialize",
    "diff.changed_inputs",
    "diff.compare",
    "diff.render",
    "archive.rev_parse_tree",
    "archive.copy_verified_graph",
    "archive.copy.pack_objects",
    "archive.copy.index_pack",
    "archive.copy.fsck",
    "archive.materialize_scoped",
    "archive.tree.list",
    "archive.tree.scope_through_links",
    "archive.tree.classify",
    "archive.tree.blobs_and_write",
    "archive.tree.links",
    "archive.tree.link_target_types",
    "archive.tree.verify_rglob",
    "host.inventory_walk",
    "host.collect_sources",
    "host.cache_finish",
    "host.validate_inventory",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "repo"
    (workspace / ".claude").mkdir(parents=True)
    (workspace / ".claude" / "settings.json").write_text(BASE_SETTINGS, encoding="utf-8")
    (workspace / "README.md").write_text("# app\n", encoding="utf-8")
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "config", "user.name", "Test")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    base = _git(workspace, "rev-parse", "HEAD")
    (workspace / ".claude" / "settings.json").write_text(WIDE_SETTINGS, encoding="utf-8")
    _git(workspace, "commit", "-qam", "widen")
    return workspace, base


@pytest.fixture
def perf_session() -> Iterator[None]:
    _perf.reset()
    _perf.enable()
    try:
        yield
    finally:
        _perf.disable()


def test_a_host_diff_records_the_phases_the_document_reports(
    repo: tuple[Path, str], perf_session: None
) -> None:
    workspace, base = repo

    result = runner.invoke(app, ["diff", "--workspace", str(workspace), "--base", base, "--json"])

    assert result.exit_code == 0, result.output
    recorded = set(_perf.snapshot())
    assert EXPECTED_PHASES <= recorded, EXPECTED_PHASES - recorded
    # Head, then the base's dependency-discovery read, each walk once.
    assert _perf.counts()["host.inventory_walk"] == 2
    assert all(seconds >= 0 for seconds in _perf.snapshot().values())


def test_phase_timing_changes_neither_stdout_nor_the_exit_code(
    repo: tuple[Path, str],
) -> None:
    workspace, base = repo
    outputs = {}
    for enabled in (False, True):
        _perf.disable()
        if enabled:
            _perf.reset()
            _perf.enable()
        try:
            for flag in (["--json"], []):
                result = runner.invoke(
                    app, ["diff", "--workspace", str(workspace), "--base", base, *flag]
                )
                outputs[(enabled, tuple(flag))] = (result.exit_code, result.output)
        finally:
            _perf.disable()

    assert outputs[(True, ("--json",))] == outputs[(False, ("--json",))]
    assert outputs[(True, ())] == outputs[(False, ())]
    assert outputs[(False, ("--json",))][0] == 0


def test_the_env_var_enables_timing_without_printing_it(repo: tuple[Path, str]) -> None:
    workspace, base = repo
    command = [
        sys.executable, "-m", "agents_shipgate", "diff",
        "--workspace", str(workspace), "--base", base, "--json",
    ]
    plain_env = {k: v for k, v in os.environ.items() if k != "AGENTS_SHIPGATE_PERF"}

    plain = subprocess.run(command, capture_output=True, text=True, env=plain_env, check=False)
    timed = subprocess.run(
        command, capture_output=True, text=True,
        env={**plain_env, "AGENTS_SHIPGATE_PERF": "1"}, check=False,
    )

    assert plain.returncode == timed.returncode == 0, timed.stderr
    assert timed.stdout == plain.stdout
    assert timed.stderr == plain.stderr


def test_the_harness_reports_nonnegative_exclusive_phases(repo: tuple[Path, str]) -> None:
    workspace, base = repo

    done = subprocess.run(
        [sys.executable, str(HARNESS), "--workspace", str(workspace), "--base", base,
         "--repeats", "1"],
        capture_output=True, text=True, check=False,
    )

    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["exit_code"] == 0
    # host.* phases are reported per snapshot read, below, not here.
    assert {n for n in EXPECTED_PHASES if not n.startswith("host.")} <= set(
        report["median_inclusive"]
    )
    # Children are timed inside their parents, so a derived exclusive time can
    # go negative only by clock resolution.
    assert min(report["median_exclusive"].values()) > -0.01
    assert {"0:diff", "1:base_dependency"} <= set(report["median_snapshot_reads"])
    assert report["median_snapshot_reads"]["0:diff"]["host.inventory_walk"] >= 0


def test_the_harness_times_the_620_walk_pair_and_records_a_refusal(
    repo: tuple[Path, str],
) -> None:
    workspace, _base = repo

    done = subprocess.run(
        [sys.executable, str(HARNESS), "--workspace", str(workspace), "--detect",
         "--repeats", "1"],
        capture_output=True, text=True, check=False,
    )

    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert set(report["median"]) == {
        "detect.host_boundary_walk",
        "detect.framework_inventory_walk",
    }
    assert report["errors"] == {}
