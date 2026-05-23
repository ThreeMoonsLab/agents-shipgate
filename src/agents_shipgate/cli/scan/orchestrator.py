from __future__ import annotations

from pathlib import Path

from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.schemas.report import ReadinessReport

from .decision import _run_checks_and_decide
from .diffs import _load_diff_references
from .final_report import _build_final_report
from .inputs import _load_inputs
from .output_planning import _plan_outputs
from .prepare import _prepare_scan
from .sanitization import _sanitize_for_output
from .tools_agent import _build_tools_and_agent
from .writing import _write_outputs


def run_scan(
    *,
    config_path: Path,
    output_dir: Path | None = None,
    formats: list[str] | None = None,
    ci_mode: str | None = None,
    fail_on: list[str] | None = None,
    baseline_path: Path | None = None,
    diff_from_path: Path | None = None,
    baseline_mode: str = "new-findings",
    deep_import: bool = False,
    policy_pack_paths: list[Path] | None = None,
    plugins_enabled: bool | None = None,
    verbose: bool = False,
    suggest_patches: bool = False,
    packet_enabled: bool | None = None,
    packet_formats: list[str] | None = None,
    packet_generated_at: str | None = None,
) -> tuple[ReadinessReport, int]:
    """Run a full scan pipeline. Returns ``(report, exit_code)``.

    Orchestrates nine sequential phases (see the phase helpers above).
    Public signature, exit-code contract, and ``_run_id`` hash inputs
    are stable across the v0.19 R-3 decomposition refactor.
    """
    if deep_import:
        raise ConfigError("Deep import is intentionally deferred and is not supported.")

    resolved = _prepare_scan(
        config_path=config_path,
        ci_mode=ci_mode,
        fail_on=fail_on,
        output_dir=output_dir,
        formats=formats,
        packet_enabled=packet_enabled,
        packet_formats=packet_formats,
        baseline_mode=baseline_mode,
    )
    inputs = _load_inputs(
        manifest=resolved.manifest,
        base_dir=resolved.base_dir,
        config_path=config_path,
        policy_pack_paths=policy_pack_paths,
        verbose=verbose,
        plugins_enabled=plugins_enabled,
    )
    tools_and_agent = _build_tools_and_agent(
        manifest=resolved.manifest,
        inputs=inputs,
    )
    diffs = _load_diff_references(
        baseline_path=baseline_path,
        diff_from_path=diff_from_path,
        base_dir=resolved.base_dir,
    )
    decision = _run_checks_and_decide(
        manifest=resolved.manifest,
        manifest_positions=resolved.manifest_positions,
        config_path=config_path,
        tools_and_agent=tools_and_agent,
        inputs=inputs,
        diffs=diffs,
        plugins_enabled=plugins_enabled,
        suggest_patches=suggest_patches,
    )
    plan = _plan_outputs(
        manifest=resolved.manifest,
        base_dir=resolved.base_dir,
    )
    sanitized = _sanitize_for_output(
        manifest=resolved.manifest,
        config_path=config_path,
        baseline_path=baseline_path,
        inputs=inputs,
        tools_and_agent=tools_and_agent,
        diffs=diffs,
        decision=decision,
        plan=plan,
        plugins_enabled=plugins_enabled,
    )
    report, public_report_payload = _build_final_report(
        manifest=resolved.manifest,
        sanitized=sanitized,
        plan=plan,
    )
    _write_outputs(
        report=report,
        public_report_payload=public_report_payload,
        sanitized=sanitized,
        plan=plan,
        manifest=resolved.manifest,
        config_path=config_path,
        packet_generated_at=packet_generated_at,
    )
    write_github_step_summary(report)
    assert report.release_decision is not None  # build_report always populates it
    return report, report.release_decision.fail_policy.exit_code
