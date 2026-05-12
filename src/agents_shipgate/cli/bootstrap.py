"""``shipgate bootstrap`` — chain detect → init → scan → apply-patches in one call.

The strategy doc's "Single-turn agent flow" calls four commands in
sequence; that's the canonical 4-call adoption flow and most coding
agents type it out manually. ``bootstrap`` is the keyboard shortcut:
one command, sensible defaults, structured per-step output.

This is intentionally a thin orchestrator. Each step shells out to
the same ``agents-shipgate`` binary so the underlying behaviour
(error messages, exit codes, ``--json`` shape) stays identical to a
manual invocation. No reimplementation, no behavior fork — that's
the contract.

Stop conditions:

- ``detect`` says no agent surface AND no manifest already exists
  AND ``suggested_sources`` is empty. Bootstrap exits 0 with a
  ``stop`` action — the workspace isn't an agent project; there's
  nothing to do.
- Any step exits with a non-zero, non-recoverable code.
  ``init`` returning ``manifest_status: "skipped_existing"`` is
  recoverable (we move on with the existing manifest); a missing
  manifest after init is not.
- ``scan`` raises a config or input error: surface the error, stop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.cli.agent_mode import emit_agent_mode_error

_ENV_VAR = "AGENTS_SHIPGATE_AGENT_MODE"


def _binary_command(extra_args: list[str], *, json_output: bool = True) -> list[str]:
    """Build the argv for invoking the same `agents-shipgate` binary
    via ``python -m agents_shipgate``. Path-independent — works inside
    a venv, a pipx install, or a source checkout."""
    cmd = [sys.executable, "-m", "agents_shipgate", *extra_args]
    if json_output and "--json" not in extra_args:
        cmd.append("--json")
    return cmd


def _run_step(
    *,
    label: str,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    parse_json: bool,
) -> dict[str, Any]:
    """Run one step in the chain and return a structured result.

    The returned dict always carries:
      - ``label`` — human-readable step name (``detect``, ``init``, …)
      - ``exit_code`` — subprocess exit code
      - ``argv`` — exact argv used (debuggable)
      - ``stdout`` — captured stdout (decoded; may be empty)
      - ``stderr`` — captured stderr (decoded; may be empty)
      - ``payload`` — parsed JSON dict when ``parse_json=True`` and
        stdout looked like JSON; otherwise None.
    """
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    payload: dict[str, Any] | None = None
    if parse_json and completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = None
    return {
        "label": label,
        "exit_code": completed.returncode,
        "argv": argv,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "payload": payload,
    }


def bootstrap_run(
    *,
    workspace: Path,
    confidence: str = "high",
    ci: bool = True,
    apply: bool = True,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the bootstrap chain and return a structured result.

    Pure function: takes a workspace and config, returns a dict
    summarizing every step. Importable from tests; the typer command
    is a thin wrapper.

    Returns ``{"verdict": str, "stopped": bool, "stop_reason": str,
    "steps": [...], "release_decision": ... | None}``.
    """
    if env is None:
        env = dict(os.environ)
    # Pre-flight: workspace must exist and be a directory. Without
    # this, _run_step's subprocess.run(cwd=...) blows up with
    # FileNotFoundError before bootstrap can produce a structured
    # result, leaving callers with a traceback instead of a
    # routable verdict (#64 review P2).
    if not workspace.exists() or not workspace.is_dir():
        return {
            "verdict": "failed_at_preflight",
            "stopped": True,
            "stop_reason": (
                f"workspace {workspace} does not exist or is not a "
                "directory. Bootstrap cannot run."
            ),
            "steps": [],
            "release_decision": None,
        }
    workspace = workspace.resolve()
    manifest_path = workspace / "shipgate.yaml"

    steps: list[dict[str, Any]] = []

    # --- 1. detect ---------------------------------------------------
    detect_step = _run_step(
        label="detect",
        argv=_binary_command(
            ["detect", "--workspace", str(workspace), "--json"],
            json_output=False,  # already set above
        ),
        cwd=workspace,
        env=env,
        parse_json=True,
    )
    steps.append(detect_step)

    # Check detect exit code BEFORE the no-agent-surface heuristic.
    # A nonzero detect with empty stdout yields detect_payload == {},
    # which trips the heuristic (is_agent_project=false, suggested=[])
    # and gets reported as "nothing to do" — masking the real failure
    # (#64 review P2). Check the exit code first so genuine detect
    # failures route to failed_at_detect.
    if detect_step["exit_code"] != 0:
        return _stop_with_failure(steps, detect_step, "detect")

    detect_payload = detect_step["payload"] or {}
    is_agent_project = bool(detect_payload.get("is_agent_project"))
    suggested = detect_payload.get("suggested_sources") or []
    manifest_already = manifest_path.is_file()

    if (
        not is_agent_project
        and not suggested
        and not manifest_already
    ):
        return {
            "verdict": "no_agent_surface",
            "stopped": True,
            "stop_reason": (
                "detect says is_agent_project=false, suggested_sources=[], "
                "and no shipgate.yaml exists. Bootstrap has nothing to do."
            ),
            "steps": steps,
            "release_decision": None,
        }

    # --- 2. init -----------------------------------------------------
    init_argv = ["init", "--workspace", str(workspace), "--write", "--json"]
    if ci:
        init_argv.append("--ci")
    init_step = _run_step(
        label="init",
        argv=_binary_command(init_argv, json_output=False),
        cwd=workspace,
        env=env,
        parse_json=True,
    )
    steps.append(init_step)

    init_payload = init_step["payload"] or {}
    manifest_status = init_payload.get("manifest_status")
    # `skipped_existing` is fine — it means a manifest was already
    # present; we'll scan against it. `not_attempted` shouldn't
    # happen with --write but we tolerate it.
    if init_step["exit_code"] != 0 and manifest_status not in (
        "skipped_existing",
        "not_attempted",
    ):
        return _stop_with_failure(steps, init_step, "init")

    if not manifest_path.is_file():
        return _stop_with_failure(
            steps,
            init_step,
            "init",
            override_reason=(
                f"init returned exit {init_step['exit_code']} but "
                f"{manifest_path} is still missing. Bootstrap can't continue."
            ),
        )

    # --- 3. scan -----------------------------------------------------
    scan_step = _run_step(
        label="scan",
        argv=_binary_command(
            [
                "scan",
                "-c",
                str(manifest_path),
                "--suggest-patches",
                "--format",
                "json",
                "--ci-mode",
                "advisory",
            ],
            json_output=False,  # scan emits a status string on stdout, not JSON
        ),
        cwd=workspace,
        env=env,
        parse_json=False,
    )
    steps.append(scan_step)

    if scan_step["exit_code"] not in (0, 20):
        # 20 = strict-mode gate failure; we ran in advisory so any
        # 20 here is unusual but routable. Other non-zero codes
        # (config, input, internal) are hard stops.
        return _stop_with_failure(steps, scan_step, "scan")

    # The report.json lands in `manifest.output.directory` when set, or
    # the conventional `agents-shipgate-reports/` otherwise. Read the
    # manifest first so custom output directories are honored AND a
    # stale `agents-shipgate-reports/report.json` from a previous run
    # doesn't pre-empt the fresh report (#64 review P1).
    report_path = _locate_report(workspace, manifest_path)
    release_decision = _read_release_decision(report_path) if report_path else None

    # --- 4. apply-patches -------------------------------------------
    if apply and report_path is not None:
        apply_step = _run_step(
            label="apply-patches",
            argv=_binary_command(
                [
                    "apply-patches",
                    "--from",
                    str(report_path),
                    "--confidence",
                    confidence,
                    "--apply",
                    "--json",
                ],
                json_output=False,
            ),
            cwd=workspace,
            env=env,
            parse_json=True,
        )
        steps.append(apply_step)
        if apply_step["exit_code"] != 0:
            # Any non-zero apply-patches exit is a stop. Exit 5 in
            # particular is a containment violation — the safety layer
            # refused to mutate files (e.g. a patch targeted a path
            # outside the workspace). Letting bootstrap proceed to a
            # `complete_*` verdict would let an agent claim completion
            # after the safety gate said NO (#64 review P2).
            return _stop_with_failure(steps, apply_step, "apply-patches")

    # --- Verdict -----------------------------------------------------
    verdict = "complete"
    if release_decision is None:
        verdict = "complete_no_report"
    elif release_decision.get("decision") == "blocked":
        verdict = "complete_blocked"
    elif release_decision.get("decision") == "review_required":
        verdict = "complete_review_required"
    elif release_decision.get("decision") == "passed":
        verdict = "complete_passed"

    return {
        "verdict": verdict,
        "stopped": False,
        "stop_reason": "",
        "steps": steps,
        "release_decision": release_decision,
        "report_path": str(report_path) if report_path else None,
    }


def _stop_with_failure(
    steps: list[dict[str, Any]],
    step: dict[str, Any],
    step_label: str,
    *,
    override_reason: str | None = None,
) -> dict[str, Any]:
    reason = override_reason or (
        f"{step_label} exited {step['exit_code']}; bootstrap stopped. "
        f"See stderr for details."
    )
    return {
        "verdict": f"failed_at_{step_label}",
        "stopped": True,
        "stop_reason": reason,
        "steps": steps,
        "release_decision": None,
    }


def _locate_report(workspace: Path, manifest_path: Path) -> Path | None:
    """Find the JSON report scan emitted.

    Priority order matches scan's actual write path:

    1. The directory the manifest declares as `output.directory`.
       Relative paths resolve against the manifest's parent directory
       (the standard scan resolves there too).
    2. The conventional `agents-shipgate-reports/` under the workspace.

    The manifest-aware path comes first because the alternative —
    checking the conventional path first — would silently pre-empt a
    fresh custom-output report with a stale one from a prior run
    (#64 review P1).
    """
    manifest_dir = manifest_path.resolve().parent
    manifest_output_dir = _read_manifest_output_dir(manifest_path)
    if manifest_output_dir is not None:
        if not manifest_output_dir.is_absolute():
            manifest_output_dir = manifest_dir / manifest_output_dir
        candidate = manifest_output_dir / "report.json"
        if candidate.is_file():
            return candidate
        # When the manifest pins a directory but the file isn't there,
        # do NOT fall back to the canonical path. The fresh scan should
        # have written to the pinned dir; a missing file there is a
        # real signal (e.g. the scan failed silently or wrote elsewhere
        # because of a --out override). Falling back risks reading a
        # stale report from the default location.
        return None
    canonical = workspace / "agents-shipgate-reports" / "report.json"
    if canonical.is_file():
        return canonical
    return None


def _read_manifest_output_dir(manifest_path: Path) -> Path | None:
    """Read `output.directory` from the manifest if set.

    Uses PyYAML (already a runtime dep). Returns ``None`` when the
    manifest is unreadable, malformed, or doesn't set the field;
    callers then fall back to the canonical path.
    """
    import yaml

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    output = data.get("output")
    if not isinstance(output, dict):
        return None
    directory = output.get("directory")
    if isinstance(directory, str) and directory.strip():
        return Path(directory.strip())
    return None


def _failed_step_error(step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract the structured `error` kind and forward fields from a
    failing step's stderr.

    When ``AGENTS_SHIPGATE_AGENT_MODE=1`` is set in the inherited env
    (bootstrap inherits the parent env by default), every child step
    emits a single-line JSON object on stderr describing its error
    kind. Bootstrap captures that stderr and forwards the underlying
    kind PLUS the routable fields (`message`, `next_action`,
    `next_actions`, and kind-specific extras like `suggestion`,
    `source_report`, `path`, `fingerprint`, `check_id`).

    Returns ``(kind, forward_fields)``. Falls back to
    ``("other_error", {})`` when no structured JSON line is found
    (e.g. the env var wasn't set or the step printed prose only).
    """
    stderr = step.get("stderr") or ""
    if not stderr.strip():
        return "other_error", {}
    # Scan stderr bottom-up because the structured line is emitted
    # last; a prose error line may precede it.
    for line in reversed(stderr.splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = payload.get("error")
        if not isinstance(kind, str):
            continue
        # Forward every routable field except the kind itself (which
        # we re-emit explicitly) and `verdict` (which bootstrap owns).
        forward = {
            k: v
            for k, v in payload.items()
            if k not in {"error", "verdict"}
        }
        return kind, forward
    return "other_error", {}


def _read_release_decision(report_path: Path) -> dict[str, Any] | None:
    """Pull just the `release_decision` block from a report.json so
    bootstrap can summarize without round-tripping the whole report."""
    try:
        text = report_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload.get("release_decision")


def bootstrap(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace to bootstrap. Defaults to the current directory.",
    ),
    confidence: str = typer.Option(
        "high",
        "--confidence",
        help=(
            "Confidence threshold for apply-patches. Default is 'high', "
            "which only mutates auto-safe patches. Use 'medium' to also "
            "apply scope-coverage and similar mid-confidence fixes."
        ),
    ),
    no_ci: bool = typer.Option(
        False,
        "--no-ci",
        help="Skip writing .github/workflows/agents-shipgate.yml.",
    ),
    no_apply: bool = typer.Option(
        False,
        "--no-apply",
        help=(
            "Run scan but skip apply-patches. Useful when you want to "
            "preview what would change before mutating."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a structured per-step JSON summary on stdout.",
    ),
) -> None:
    """Run the canonical 4-call adoption flow in one command.

    Chains: ``detect → init --write --ci → scan --suggest-patches →
    apply-patches --confidence high``. Each step shells out to the
    same agents-shipgate binary, so behavior is identical to a manual
    invocation. Stops on the first non-recoverable error.

    Use this when adopting Shipgate in a fresh repo. For ongoing CI,
    keep using the GitHub Action — bootstrap is the one-shot
    convenience layer for first-time setup.
    """
    result = bootstrap_run(
        workspace=workspace,
        confidence=confidence,
        ci=not no_ci,
        apply=not no_apply,
    )

    if json_output:
        typer.echo(json.dumps(result, default=str, indent=2))
    else:
        _emit_human_summary(result)

    if result["stopped"] and result["verdict"] != "no_agent_surface":
        # Forward the underlying step's structured error so coding
        # agents get the same routing they'd get from a manual
        # invocation (#64 review P2). Bootstrap previously re-emitted
        # a generic `other_error`, hiding kind-specific next_actions
        # like input_parse_error's "re-run scan with --suggest-patches"
        # or unknown_check_id's `suggestion`.
        failing = next(
            (s for s in reversed(result["steps"]) if s["exit_code"] != 0),
            None,
        )
        kind, forwarded = (
            _failed_step_error(failing) if failing else ("other_error", {})
        )
        emit_agent_mode_error(
            kind,
            failing_step=failing["label"] if failing else None,
            verdict=result["verdict"],
            stop_reason=result["stop_reason"],
            **forwarded,
        )
        # Match the failing step's exit code when possible
        for step in reversed(result["steps"]):
            if step["exit_code"] != 0:
                raise typer.Exit(step["exit_code"])
        raise typer.Exit(4)


def _emit_human_summary(result: dict[str, Any]) -> None:
    typer.echo("Agents Shipgate · bootstrap")
    typer.echo("")
    for step in result["steps"]:
        status = "OK" if step["exit_code"] == 0 else f"exit={step['exit_code']}"
        typer.echo(f"  - {step['label']}: {status}")
    typer.echo("")
    if result["stopped"]:
        typer.echo(f"Stopped: {result['stop_reason']}")
        return
    rd = result.get("release_decision")
    if rd:
        typer.echo(f"Release decision: {rd['decision']}")
        typer.echo(
            f"  blockers={len(rd.get('blockers', []))}, "
            f"review_items={len(rd.get('review_items', []))}"
        )
        if result.get("report_path"):
            typer.echo(f"  report: {result['report_path']}")
    else:
        typer.echo("Bootstrap completed but no report.json was found.")
