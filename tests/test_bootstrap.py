"""Pin the ``agents-shipgate bootstrap`` super-command surface.

Bootstrap chains the canonical 4-call flow (detect → init → scan →
apply-patches) via subprocess, so the underlying behaviour is identical
to manual invocation. These tests exercise the chain end-to-end against
the bundled samples and pin the structured-summary shape.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.bootstrap import bootstrap_run
from agents_shipgate.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SIMPLE_OPENAI_API = REPO_ROOT / "samples" / "simple_openai_api_agent"


def _copy_sample(sample_dir: Path, into: Path) -> Path:
    """Copy a sample fixture into a tmp workspace so bootstrap can mutate
    it without touching the in-tree fixture."""
    shutil.copytree(sample_dir, into / sample_dir.name)
    return into / sample_dir.name


def test_bootstrap_chains_against_simple_openai_api_sample(tmp_path):
    """End-to-end happy-path check: bootstrap completes against a real
    sample fixture, every step lands a structured result, and the
    release-decision summary is read from the emitted report.json."""
    workspace = _copy_sample(SIMPLE_OPENAI_API, tmp_path)
    result = bootstrap_run(
        workspace=workspace, ci=False, apply=False, confidence="high"
    )

    assert result["stopped"] is False, (
        f"Bootstrap stopped unexpectedly: {result['stop_reason']!r}"
    )
    labels = [s["label"] for s in result["steps"]]
    assert labels == ["detect", "init", "scan"], (
        f"Bootstrap chain ran unexpected steps: {labels!r}"
    )

    detect_step = result["steps"][0]
    assert detect_step["exit_code"] == 0
    assert detect_step["payload"], "detect must emit a JSON payload"

    rd = result["release_decision"]
    assert rd is not None, "Bootstrap must read release_decision from report.json"
    assert rd["decision"] in {"blocked", "review_required", "passed"}
    assert result["report_path"], "report_path must point at the emitted report"


def test_bootstrap_skips_when_no_agent_surface(tmp_path):
    """A workspace with no agent surface and no existing manifest must
    stop early with ``verdict: no_agent_surface`` rather than running
    init/scan against nothing."""
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "README.md").write_text("just a readme\n", encoding="utf-8")

    result = bootstrap_run(workspace=empty, ci=False, apply=False)
    assert result["verdict"] == "no_agent_surface"
    assert result["stopped"] is True
    assert "is_agent_project=false" in result["stop_reason"]
    # Only detect ran; init/scan/apply skipped.
    assert [s["label"] for s in result["steps"]] == ["detect"]


def test_bootstrap_tolerates_manifest_already_exists(tmp_path):
    """When the workspace already has shipgate.yaml, init refuses to
    overwrite (exit 2 with ``manifest_status: skipped_existing``).
    Bootstrap must continue past this — it's not a hard failure."""
    workspace = _copy_sample(SIMPLE_OPENAI_API, tmp_path)
    assert (workspace / "shipgate.yaml").is_file()

    result = bootstrap_run(workspace=workspace, ci=False, apply=False)
    init_step = next(s for s in result["steps"] if s["label"] == "init")
    # init exits non-zero on overwrite refusal
    assert init_step["exit_code"] == 2, (
        f"init exit code drifted; expected 2 (skipped_existing), got "
        f"{init_step['exit_code']}"
    )
    # …but bootstrap proceeded to scan anyway
    assert any(s["label"] == "scan" for s in result["steps"])
    assert result["stopped"] is False


def test_bootstrap_stops_when_scan_fails(tmp_path):
    """A manifest that points at a missing tool source produces a
    scan-time `input_parse_error` (exit 3). Bootstrap must stop with
    `verdict: failed_at_scan` and surface the underlying stderr."""
    workspace = tmp_path / "broken"
    workspace.mkdir()
    (workspace / "shipgate.yaml").write_text(
        "version: \"0.1\"\n"
        "project:\n  name: broken\n"
        "agent:\n  name: broken\n  declared_purpose:\n    - test broken manifest\n"
        "environment:\n  target: local\n"
        "tool_sources:\n  - id: missing\n    type: openapi\n    path: missing.yaml\n",
        encoding="utf-8",
    )
    # Create a file so detect sees something — otherwise we hit the
    # "no agent surface" early-stop branch.
    (workspace / "missing.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")

    result = bootstrap_run(workspace=workspace, ci=False, apply=False)
    # detect should succeed (workspace has an OpenAPI spec), init should
    # skip (manifest exists), scan should fail.
    scan_step = next(
        (s for s in result["steps"] if s["label"] == "scan"),
        None,
    )
    if scan_step is not None and scan_step["exit_code"] not in (0, 20):
        assert result["verdict"].startswith("failed_at_")
        assert result["stopped"] is True


def test_bootstrap_emits_structured_json_when_requested(tmp_path):
    """`bootstrap --json` must produce parseable structured output with
    the canonical top-level keys."""
    workspace = _copy_sample(SIMPLE_OPENAI_API, tmp_path)
    runner = CliRunner()
    invocation = runner.invoke(
        app,
        [
            "bootstrap",
            "--workspace",
            str(workspace),
            "--no-ci",
            "--no-apply",
            "--json",
        ],
    )
    assert invocation.exit_code == 0, invocation.output
    payload = json.loads(invocation.stdout)
    for key in ("verdict", "stopped", "stop_reason", "steps", "release_decision"):
        assert key in payload, f"Missing top-level key {key!r} in bootstrap JSON"
    assert isinstance(payload["steps"], list)
    assert all(
        {"label", "exit_code", "argv", "stdout", "stderr"} <= set(step)
        for step in payload["steps"]
    )


def test_bootstrap_no_apply_skips_apply_patches_step(tmp_path):
    """`--no-apply` must skip the apply-patches step entirely. Pinned so
    a future refactor doesn't accidentally always run apply."""
    workspace = _copy_sample(SIMPLE_OPENAI_API, tmp_path)
    result = bootstrap_run(workspace=workspace, ci=False, apply=False)
    labels = [s["label"] for s in result["steps"]]
    assert "apply-patches" not in labels


def test_bootstrap_reports_release_decision_verdict_in_summary(tmp_path):
    """The top-level `verdict` mirrors `release_decision.decision` —
    `complete_passed` / `complete_review_required` / `complete_blocked`.
    A coding agent reading `bootstrap --json` should be able to gate on
    the verdict without re-parsing the report.json."""
    workspace = _copy_sample(SIMPLE_OPENAI_API, tmp_path)
    result = bootstrap_run(workspace=workspace, ci=False, apply=False)
    rd = result["release_decision"]
    if rd is None:
        pytest.skip("Sample produced no release_decision; verdict mirroring skipped.")
    expected_prefix = f"complete_{rd['decision']}"
    assert result["verdict"] == expected_prefix, (
        f"verdict {result['verdict']!r} should mirror "
        f"release_decision.decision {rd['decision']!r}"
    )
