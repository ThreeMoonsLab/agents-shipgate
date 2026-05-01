"""Tests for ``shipgate init --ci`` per the v0.6 orthogonal-flag matrix.

The matrix (plan §2):
    --ci is orthogonal to --write. Each gets its own overwrite-refusal
    check. Exit code is the max of per-action outcomes. JSON output
    reports each action's outcome separately.

Plus the cross-workflow detection (v4 should-fix).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.discovery.ci_workflow import (
    WORKFLOW_RELATIVE_PATH,
    write_ci_workflow,
)
from agents_shipgate.cli.main import app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
runner = CliRunner()


def _seed_workspace(tmp_path: Path, sample: str) -> Path:
    dst = tmp_path / "ws"
    shutil.copytree(SAMPLES / sample, dst)
    target = dst / "shipgate.yaml"
    if target.exists():
        target.unlink()
    reports = dst / "agents-shipgate-reports"
    if reports.exists():
        shutil.rmtree(reports)
    return dst


# --- write_ci_workflow library tests ---------------------------------------


def test_write_ci_workflow_writes_to_fresh_workspace(tmp_path: Path) -> None:
    result = write_ci_workflow(tmp_path)
    assert result.status == "written"
    target = tmp_path / WORKFLOW_RELATIVE_PATH
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    # Generated workflow pins to the current package version (per v0.6
    # reviewer feedback: @main is unpinned and breaks reproducibility).
    from agents_shipgate import __version__
    assert f"ThreeMoonsLab/agents-shipgate@v{__version__}" in content
    assert "ci_mode: advisory" in content


def test_write_ci_workflow_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / WORKFLOW_RELATIVE_PATH
    target.parent.mkdir(parents=True)
    target.write_text("# user-edited workflow\n", encoding="utf-8")
    result = write_ci_workflow(tmp_path)
    assert result.status == "skipped_existing_target"
    # File untouched.
    assert target.read_text(encoding="utf-8") == "# user-edited workflow\n"


def test_write_ci_workflow_skips_when_other_workflow_calls_action(
    tmp_path: Path,
) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    other = workflows / "release.yml"
    other.write_text(
        "name: Release\n"
        "on: [push]\n"
        "jobs:\n"
        "  s:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: ThreeMoonsLab/agents-shipgate@v0.5.0\n",
        encoding="utf-8",
    )
    result = write_ci_workflow(tmp_path)
    assert result.status == "skipped_cross_reference"
    assert result.cross_reference_path is not None
    assert result.cross_reference_path.endswith("release.yml")
    # Did NOT write our workflow.
    assert not (tmp_path / WORKFLOW_RELATIVE_PATH).exists()


def test_write_ci_workflow_does_not_self_match(tmp_path: Path) -> None:
    """An existing agents-shipgate.yml must trigger
    ``skipped_existing_target``, not ``skipped_cross_reference``."""
    target = tmp_path / WORKFLOW_RELATIVE_PATH
    target.parent.mkdir(parents=True)
    target.write_text(
        "uses: ThreeMoonsLab/agents-shipgate@main\n",
        encoding="utf-8",
    )
    result = write_ci_workflow(tmp_path)
    assert result.status == "skipped_existing_target"


# --- init --ci CLI matrix tests --------------------------------------------


def test_matrix_init_write_ci_fresh_workspace(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--ci", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "written"
    assert payload["workflow"]["status"] == "written"
    assert (workspace / "shipgate.yaml").exists()
    assert (workspace / WORKFLOW_RELATIVE_PATH).exists()


def test_matrix_init_write_ci_manifest_exists(tmp_path: Path) -> None:
    """`init --write --ci` against a workspace with an existing manifest:
    manifest action errors (exit 2) but workflow still writes (per
    orthogonal-flag rule). Exit code is max — i.e., 2."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    (workspace / "shipgate.yaml").write_text("# user manifest\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--ci", "--json"],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "skipped_existing"
    assert payload["workflow"]["status"] == "written"
    # Manifest untouched.
    assert (workspace / "shipgate.yaml").read_text(encoding="utf-8") == "# user manifest\n"
    # Workflow written.
    assert (workspace / WORKFLOW_RELATIVE_PATH).exists()


def test_matrix_init_write_ci_workflow_exists(tmp_path: Path) -> None:
    """Manifest fresh, workflow exists: manifest writes, workflow skips
    with ``skipped_existing_target``. Exit code 0."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    target = workspace / WORKFLOW_RELATIVE_PATH
    target.parent.mkdir(parents=True)
    target.write_text("# user workflow\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--ci", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "written"
    assert payload["workflow"]["status"] == "skipped_existing_target"
    assert target.read_text(encoding="utf-8") == "# user workflow\n"


def test_matrix_init_write_ci_both_exist(tmp_path: Path) -> None:
    """Both exist: manifest errors, workflow skips, exit code 2."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    (workspace / "shipgate.yaml").write_text("# user manifest\n", encoding="utf-8")
    target = workspace / WORKFLOW_RELATIVE_PATH
    target.parent.mkdir(parents=True)
    target.write_text("# user workflow\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--ci", "--json"],
    )
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "skipped_existing"
    assert payload["workflow"]["status"] == "skipped_existing_target"


def test_matrix_init_ci_without_write(tmp_path: Path) -> None:
    """`init --ci` without `--write`: print template, write workflow."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--ci", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "not_attempted"
    assert payload["workflow"]["status"] == "written"
    assert "template" in payload  # dry-run includes the template
    assert not (workspace / "shipgate.yaml").exists()
    assert (workspace / WORKFLOW_RELATIVE_PATH).exists()


def test_matrix_init_ci_cross_reference_skip(tmp_path: Path) -> None:
    """A pre-existing release.yml that already calls the action triggers
    cross_reference skip. Manifest still writes."""
    workspace = _seed_workspace(tmp_path, "simple_langchain_agent")
    workflows = workspace / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "release.yml").write_text(
        "uses: ThreeMoonsLab/agents-shipgate@v0.5.0\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--write", "--ci", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "written"
    assert payload["workflow"]["status"] == "skipped_cross_reference"
    assert payload["workflow"]["cross_reference_path"].endswith("release.yml")
    assert not (workspace / WORKFLOW_RELATIVE_PATH).exists()


def test_workflow_template_references_action_yml_input_names(tmp_path: Path) -> None:
    """Snapshot guard: when action.yml gains/renames inputs, this test
    flags the template generator for review."""
    write_ci_workflow(tmp_path)
    content = (tmp_path / WORKFLOW_RELATIVE_PATH).read_text(encoding="utf-8")
    # Required inputs from action.yml that appear in the template:
    for token in ("config:", "ci_mode:"):
        assert token in content
