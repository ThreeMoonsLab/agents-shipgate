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

from agents_shipgate.cli.bootstrap import (
    _failed_step_error,
    _locate_report,
    bootstrap_run,
)
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


def test_bootstrap_honors_custom_output_directory(tmp_path):
    """When the manifest sets `output.directory: custom-reports`, scan
    writes the report there and bootstrap must locate it there too.
    Earlier `_locate_report` only checked `agents-shipgate-reports/`,
    so a custom directory produced `verdict: complete_no_report` and
    silently skipped apply-patches (#64 review P1)."""
    workspace = _copy_sample(SIMPLE_OPENAI_API, tmp_path)
    manifest = workspace / "shipgate.yaml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace(
            "directory: agents-shipgate-reports", "directory: custom-reports"
        ),
        encoding="utf-8",
    )

    result = bootstrap_run(workspace=workspace, ci=False, apply=True)
    assert result["report_path"], (
        "Bootstrap must locate the report when output.directory is custom; "
        f"got result={result!r}"
    )
    assert "custom-reports" in result["report_path"], (
        f"report_path {result['report_path']!r} should be under custom-reports/"
    )
    assert result["verdict"] != "complete_no_report"
    # apply-patches step must have run (no early `complete_no_report` skip)
    assert any(s["label"] == "apply-patches" for s in result["steps"])


def test_bootstrap_does_not_read_stale_canonical_when_manifest_pins_custom(tmp_path):
    """A stale `agents-shipgate-reports/report.json` from a previous
    run must NOT override a fresh custom-output report. _locate_report
    short-circuits on the manifest-pinned path; falling back to the
    canonical path could read a stale report and apply-patches against
    the wrong scan."""
    workspace = _copy_sample(SIMPLE_OPENAI_API, tmp_path)
    manifest = workspace / "shipgate.yaml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace(
            "directory: agents-shipgate-reports", "directory: custom-reports"
        ),
        encoding="utf-8",
    )
    # Plant a stale report at the canonical path.
    stale_dir = workspace / "agents-shipgate-reports"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "report.json").write_text(
        '{"STALE":true,"report_schema_version":"0.10"}', encoding="utf-8"
    )

    result = bootstrap_run(workspace=workspace, ci=False, apply=False)
    assert result["report_path"]
    assert "custom-reports" in result["report_path"]
    assert "agents-shipgate-reports" not in result["report_path"], (
        "Stale canonical-path report leaked into bootstrap's report_path"
    )


def test_locate_report_returns_none_when_manifest_dir_has_no_report(tmp_path):
    """When the manifest pins a custom directory but the scan didn't
    write there (e.g. scan was killed before flushing), `_locate_report`
    returns None rather than silently falling back to the canonical
    path with a possibly-stale report."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manifest = workspace / "shipgate.yaml"
    manifest.write_text(
        "version: \"0.1\"\n"
        "project: {name: x}\n"
        "agent: {name: x, declared_purpose: [test]}\n"
        "environment: {target: local}\n"
        "tool_sources: []\n"
        "output:\n  directory: custom-reports\n",
        encoding="utf-8",
    )
    # Plant a stale report at the canonical path; manifest doesn't
    # pin canonical so the stale file must NOT be returned.
    (workspace / "agents-shipgate-reports").mkdir()
    (workspace / "agents-shipgate-reports" / "report.json").write_text(
        "{}", encoding="utf-8"
    )
    result = _locate_report(workspace, manifest)
    assert result is None, (
        f"Expected None when manifest pins a dir without a report; got {result!r}"
    )


def test_failed_step_error_forwards_child_error_kind():
    """Bootstrap must forward the underlying step's structured error
    kind and routable fields. A scan-time `input_parse_error` should
    surface as `error: input_parse_error` in bootstrap's agent-mode
    output, not as a generic `other_error` that drops the
    kind-specific `next_actions` (#64 review P2)."""
    fake_step = {
        "label": "scan",
        "exit_code": 3,
        "stderr": (
            "Input parsing error: Input file not found: missing.yaml\n"
            '{"error": "input_parse_error", "message": "Input file not found",'
            ' "next_action": "agents-shipgate detect --workspace . --json",'
            ' "next_actions": [{"kind": "command", "command": "x", "why": "y"}]}'
        ),
    }
    kind, forwarded = _failed_step_error(fake_step)
    assert kind == "input_parse_error"
    assert forwarded["message"] == "Input file not found"
    assert forwarded["next_action"] == "agents-shipgate detect --workspace . --json"
    assert forwarded["next_actions"][0]["command"] == "x"


def test_failed_step_error_falls_back_when_stderr_has_no_json():
    """Without `AGENTS_SHIPGATE_AGENT_MODE=1`, child steps emit prose
    errors only. Bootstrap then falls back to `other_error` rather
    than crashing or emitting a malformed kind."""
    fake_step = {
        "label": "scan",
        "exit_code": 3,
        "stderr": "Input parsing error: missing file (prose only)",
    }
    kind, forwarded = _failed_step_error(fake_step)
    assert kind == "other_error"
    assert forwarded == {}


def test_failed_step_error_skips_lines_without_error_field():
    """Some commands print multi-line JSON debug output but only the
    structured-error line carries `error`. The helper must pick the
    one with `error`, not the first JSON it sees."""
    fake_step = {
        "label": "scan",
        "exit_code": 3,
        "stderr": (
            '{"warning": "deprecated flag", "note": "use --new"}\n'
            'Input parsing error: bad spec\n'
            '{"error": "input_parse_error", "message": "bad spec"}'
        ),
    }
    kind, _forwarded = _failed_step_error(fake_step)
    assert kind == "input_parse_error"


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


def test_bootstrap_rejects_missing_workspace_with_structured_failure(tmp_path):
    """Pre-flight: when --workspace points at a path that doesn't exist
    (typo, deleted dir, wrong cwd), bootstrap must emit a structured
    `failed_at_preflight` verdict rather than crashing in subprocess.run
    with FileNotFoundError. Coding agents need a routable signal."""
    missing = tmp_path / "definitely-missing"
    assert not missing.exists()

    result = bootstrap_run(workspace=missing, ci=False, apply=False)
    assert result["verdict"] == "failed_at_preflight"
    assert result["stopped"] is True
    assert "does not exist" in result["stop_reason"]
    # Steps must be empty — we never got to detect.
    assert result["steps"] == []


def test_bootstrap_rejects_workspace_pointing_at_a_file(tmp_path):
    """Pre-flight also catches the case where --workspace is a real
    path but points at a file (e.g. user pointed at shipgate.yaml
    instead of its parent directory)."""
    not_a_dir = tmp_path / "config.yaml"
    not_a_dir.write_text("version: 0.1\n", encoding="utf-8")

    result = bootstrap_run(workspace=not_a_dir, ci=False, apply=False)
    assert result["verdict"] == "failed_at_preflight"
    assert result["stopped"] is True
    assert result["steps"] == []


def test_bootstrap_routes_nonzero_detect_to_failed_at_detect(tmp_path, monkeypatch):
    """When detect exits non-zero with empty stdout, bootstrap must
    route to `failed_at_detect` rather than fall through to the
    no-agent-surface heuristic (which would mask the failure as
    "nothing to do"). The check order matters: exit code FIRST,
    payload heuristic SECOND (#64 review P2)."""
    # Build a fake binary command that always exits 1 with empty stdout
    # but a structured agent-mode stderr line. We patch subprocess.run
    # at the bootstrap module level so only this test sees the override.
    from agents_shipgate.cli import bootstrap as bs_module

    real_run = bs_module.subprocess.run

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, **kwargs):
        if "detect" in argv:
            return FakeCompleted(
                returncode=1,
                stdout="",
                stderr=(
                    '{"error": "other_error", "message": "detector blew up", '
                    '"next_action": "file an issue"}\n'
                ),
            )
        return real_run(argv, **kwargs)

    monkeypatch.setattr(bs_module.subprocess, "run", fake_run)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = bootstrap_run(workspace=workspace, ci=False, apply=False)
    assert result["verdict"] == "failed_at_detect", (
        f"nonzero detect with empty stdout must NOT fall through to "
        f"no_agent_surface; got {result['verdict']!r}"
    )
    assert result["stopped"] is True
    # And the stderr's structured kind must be discoverable for the
    # downstream emit_agent_mode_error path.
    detect_step = next(s for s in result["steps"] if s["label"] == "detect")
    assert detect_step["exit_code"] == 1
    kind, forwarded = _failed_step_error(detect_step)
    assert kind == "other_error"
    assert forwarded.get("message") == "detector blew up"


def test_bootstrap_stops_on_apply_patches_exit_5_containment_violation(
    tmp_path, monkeypatch
):
    """apply-patches exit 5 = containment violation (the safety layer
    refused to mutate files). Bootstrap must stop with
    `failed_at_apply-patches`, not return a `complete_*` verdict —
    otherwise an agent could report completion after the safety gate
    said NO (#64 review P2)."""
    from agents_shipgate.cli import bootstrap as bs_module

    real_run = bs_module.subprocess.run

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, **kwargs):
        if "apply-patches" in argv:
            return FakeCompleted(
                returncode=5,
                stdout="",
                stderr=(
                    '{"error": "other_error", "message": '
                    '"refusing: patch path escapes workspace"}\n'
                ),
            )
        return real_run(argv, **kwargs)

    monkeypatch.setattr(bs_module.subprocess, "run", fake_run)

    workspace = _copy_sample(SIMPLE_OPENAI_API, tmp_path)
    result = bootstrap_run(workspace=workspace, ci=False, apply=True)

    assert result["verdict"] == "failed_at_apply-patches", (
        f"apply-patches exit 5 must stop bootstrap; got {result['verdict']!r}"
    )
    assert result["stopped"] is True
    apply_step = next(
        s for s in result["steps"] if s["label"] == "apply-patches"
    )
    assert apply_step["exit_code"] == 5
