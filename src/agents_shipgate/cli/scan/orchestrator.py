from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agents_shipgate import __version__, _perf
from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.cli.discovery.placeholders import manifest_placeholder_fields
from agents_shipgate.core.capability_lock import build_capability_lock
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError
from agents_shipgate.schemas.capabilities import CapabilityLockFileV1
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.verification import VerificationContext

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
    no_heuristics: bool = False,
    packet_enabled: bool | None = None,
    packet_formats: list[str] | None = None,
    packet_generated_at: str | None = None,
    verification_context: VerificationContext | None = None,
    capability_lock_callback: Callable[[CapabilityLockFileV1], None] | None = None,
    manifest_text: str | None = None,
) -> tuple[ReadinessReport, int]:
    """Run a full scan pipeline. Returns ``(report, exit_code)``.

    Orchestrates nine sequential phases (see the phase helpers above).
    Public signature, exit-code contract, and ``_run_id`` hash inputs
    are stable across the v0.19 R-3 decomposition refactor.
    """
    if deep_import:
        raise ConfigError("Deep import is intentionally deferred and is not supported.")

    try:
        return _run_scan(
            config_path=config_path,
            output_dir=output_dir,
            formats=formats,
            ci_mode=ci_mode,
            fail_on=fail_on,
            baseline_path=baseline_path,
            diff_from_path=diff_from_path,
            baseline_mode=baseline_mode,
            policy_pack_paths=policy_pack_paths,
            plugins_enabled=plugins_enabled,
            verbose=verbose,
            suggest_patches=suggest_patches,
            no_heuristics=no_heuristics,
            packet_enabled=packet_enabled,
            packet_formats=packet_formats,
            packet_generated_at=packet_generated_at,
            verification_context=verification_context,
            capability_lock_callback=capability_lock_callback,
            manifest_text=manifest_text,
        )
    except AgentsShipgateError as exc:
        # Every recovery route that names a file to edit needs the manifest
        # this run actually read, and only this frame knows it: the CLI is
        # spelled `--workspace .` or `-c '*/shipgate.yaml'` as often as with a
        # literal path, and the recovery emitted from the caller's `except`
        # would otherwise name `shipgate.yaml` relative to the caller's CWD —
        # an unrelated trust root (#329 review). Recorded once here rather
        # than at each raise site, none of which knows the manifest either.
        exc.details.setdefault("manifest_path", str(config_path))
        # Typed placeholder state, read from the manifest rather than searched
        # for in the failure text (#329 review 3). Absent — not empty — when
        # the manifest could not be read at all, so the router can tell "no
        # placeholders" from "we never looked".
        placeholders = _manifest_placeholders(config_path, manifest_text)
        if placeholders is not None:
            exc.details.setdefault("manifest_placeholders", placeholders)
        raise


def _manifest_placeholders(
    config_path: Path, manifest_text: str | None
) -> list[str] | None:
    """Placeholder fields in the manifest this run read, or ``None``.

    Re-reads the file only when the caller supplied no text, and only on a
    failure path: the manifest was already read once by this point, so the
    cost is a second read of a file that is known small and known present.
    """

    text = manifest_text
    if text is None:
        try:
            text = config_path.read_text(encoding="utf-8")
        except OSError:
            return None
    return manifest_placeholder_fields(text)


def _run_scan(
    *,
    config_path: Path,
    output_dir: Path | None,
    formats: list[str] | None,
    ci_mode: str | None,
    fail_on: list[str] | None,
    baseline_path: Path | None,
    diff_from_path: Path | None,
    baseline_mode: str,
    policy_pack_paths: list[Path] | None,
    plugins_enabled: bool | None,
    verbose: bool,
    suggest_patches: bool,
    no_heuristics: bool,
    packet_enabled: bool | None,
    packet_formats: list[str] | None,
    packet_generated_at: str | None,
    verification_context: VerificationContext | None,
    capability_lock_callback: Callable[[CapabilityLockFileV1], None] | None,
    manifest_text: str | None,
) -> tuple[ReadinessReport, int]:
    """The pipeline itself. Split from :func:`run_scan` so the manifest the
    run read can be attached to any failure on the way out, in one place."""

    # Phase-timing instrumentation (`_perf`): opt-in, zero overhead when
    # off. Phase names are stable strings — benchmark snapshots and the
    # latency-budget test suite key off them. Don't rename without
    # updating benchmark/perf/README.md.
    with _perf.phase("prepare"):
        resolved = _prepare_scan(
            config_path=config_path,
            ci_mode=ci_mode,
            fail_on=fail_on,
            output_dir=output_dir,
            formats=formats,
            packet_enabled=packet_enabled,
            packet_formats=packet_formats,
            baseline_mode=baseline_mode,
            manifest_text=manifest_text,
        )
    with _perf.phase("load_inputs"):
        inputs = _load_inputs(
            manifest=resolved.manifest,
            base_dir=resolved.base_dir,
            config_path=config_path,
            policy_pack_paths=policy_pack_paths,
            verbose=verbose,
            plugins_enabled=plugins_enabled,
        )
    with _perf.phase("build_tools_and_agent"):
        tools_and_agent = _build_tools_and_agent(
            manifest=resolved.manifest,
            inputs=inputs,
        )
    if capability_lock_callback is not None:
        capability_lock_callback(
            build_capability_lock(
                resolved.manifest,
                agent=tools_and_agent.agent,
                tools=tools_and_agent.tools,
                config_path=config_path,
                manifest_dir=resolved.base_dir,
                cli_version=__version__,
                source_count=len(inputs.loaded_sources),
                source_warning_count=len(tools_and_agent.warnings),
                toolkit_bound_count=len(tools_and_agent.toolkit_bounds),
                plugins_enabled=plugins_enabled is not False,
            )
        )
    with _perf.phase("load_diff_references"):
        diffs = _load_diff_references(
            baseline_path=baseline_path,
            diff_from_path=diff_from_path,
            base_dir=resolved.base_dir,
        )
    with _perf.phase("run_checks_and_decide"):
        decision = _run_checks_and_decide(
            manifest=resolved.manifest,
            manifest_positions=resolved.manifest_positions,
            config_path=config_path,
            tools_and_agent=tools_and_agent,
            inputs=inputs,
            diffs=diffs,
            plugins_enabled=plugins_enabled,
            suggest_patches=suggest_patches,
            no_heuristics=no_heuristics,
            verification_context=verification_context,
            declared_ci=resolved.declared_ci,
        )
    with _perf.phase("plan_outputs"):
        plan = _plan_outputs(
            manifest=resolved.manifest,
            base_dir=resolved.base_dir,
        )
    with _perf.phase("sanitize_for_output"):
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
    with _perf.phase("build_final_report"):
        report, public_report_payload = _build_final_report(
            manifest=resolved.manifest,
            sanitized=sanitized,
            plan=plan,
            declared_ci=resolved.declared_ci,
        )
    with _perf.phase("write_outputs"):
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
