from __future__ import annotations

import glob
import logging
import re
from pathlib import Path

import typer

from agents_shipgate import __version__
from agents_shipgate.checks.plugin_validation import strict_failure_messages
from agents_shipgate.cli.diagnostics import (
    diagnose_invalid_manifest,
    diagnose_missing_manifest,
)
from agents_shipgate.cli.discovery import discover_manifest_paths
from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.findings.constants import SEVERITY_ORDER
from agents_shipgate.report.summary_text import (
    evidence_coverage_text,
    primary_evidence_remediation_text,
)

logger = logging.getLogger(__name__)


# Exit code for ``--strict-plugins`` failures. Reuses the documented
# ``other_error`` slot (per ``.well-known/agents-shipgate.json``) so the
# stable exit-code surface stays narrow — strict-plugins is plugin
# infrastructure failure, not gate failure.
_STRICT_PLUGINS_EXIT_CODE = 4


def _apply_strict_plugins(
    report,
    exit_code: int,
    *,
    strict_plugins: bool,
    label: str | None = None,
) -> int:
    """Apply ``--strict-plugins`` policy after a single scan completes.

    Returns the (possibly elevated) exit code. Emits one human-readable
    line per failure to stderr. ``label`` prefixes each line in
    multi-config scans so the operator can tell which manifest tripped.
    """

    if not strict_plugins:
        return exit_code
    # v0.20: --strict-plugins covers BOTH check-plugin failures AND
    # third-party adapter failures. Both surface through the same
    # extension entry-point trust model (M5 for checks, v0.20 for
    # adapters) and a CI step that opts into strictness wants to fail
    # on either. Adapter messages carry an ``adapter`` prefix in the
    # human-readable line; plugin messages carry a ``plugin`` prefix.
    from agents_shipgate.inputs.adapter_validation import (
        strict_adapter_failure_messages,
    )

    plugin_messages = strict_failure_messages(report.loaded_plugins)
    adapter_messages = strict_adapter_failure_messages(
        getattr(report, "loaded_adapters", []) or []
    )
    messages = plugin_messages + adapter_messages
    if not messages:
        return exit_code
    prefix = f"{label}: " if label else ""
    issue_kind = (
        "plugin/adapter issue(s)"
        if plugin_messages and adapter_messages
        else ("adapter issue(s)" if adapter_messages else "plugin issue(s)")
    )
    typer.echo(
        f"{prefix}--strict-plugins: {len(messages)} {issue_kind} detected; "
        "scan failed under strict-plugins policy.",
        err=True,
    )
    for message in messages:
        typer.echo(f"{prefix}--strict-plugins: {message}", err=True)
    return max(exit_code, _STRICT_PLUGINS_EXIT_CODE)


def _parse_formats(value: str) -> list[str]:
    formats = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in formats if item not in {"markdown", "json", "sarif"}]
    if invalid:
        raise ConfigError(f"Unsupported report format(s): {', '.join(invalid)}")
    if not formats:
        raise ConfigError("At least one report format is required")
    return formats


def _parse_packet_formats(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parts = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in parts if item not in {"md", "json", "html", "pdf"}]
    if invalid:
        raise ConfigError(
            f"Unsupported packet format(s): {', '.join(invalid)}; "
            "expected a subset of md,json,html,pdf"
        )
    if not parts:
        raise ConfigError(
            "--packet-format must contain at least one of md,json,html,pdf"
        )
    return parts


def _parse_fail_on(value: str | None) -> list[str] | None:
    if value is None:
        return None
    severities = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [
        severity
        for severity in severities
        if severity not in {"info", "low", "medium", "high", "critical"}
    ]
    if invalid:
        raise ConfigError(f"Unsupported fail-on severity: {', '.join(invalid)}")
    return severities


def _resolve_config_paths(*, config: str, workspace: Path | None) -> list[Path]:
    if workspace:
        paths = discover_manifest_paths(workspace)
    elif any(char in config for char in "*?[]"):
        paths = sorted(Path(path) for path in glob.glob(config, recursive=True))
    else:
        paths = [Path(config)]
    if not paths:
        raise ConfigError("No shipgate.yaml files matched")
    return paths


def _missing_manifest_workspace(
    *, config: str, workspace: Path | None
) -> Path:
    """Pick the workspace path used by the missing-manifest diagnostic.

    Routes recovery to the directory the user pointed scan/doctor at
    (``-c <path>`` or ``--workspace <dir>``), not whichever directory
    they happen to be invoking the CLI from. For glob inputs, walks the
    path components and uses the longest non-glob prefix — so an
    invocation like ``scan -c /tmp/repo/*/shipgate.yaml`` from another
    cwd still routes the agent to ``/tmp/repo``.
    """
    if workspace is not None:
        return workspace.resolve()
    if any(char in config for char in "*?[]"):
        return _glob_non_glob_prefix(config)
    config_path = Path(config)
    parent = config_path.parent
    if not str(parent) or str(parent) == ".":
        return Path.cwd()
    # `Path.resolve()` works on non-existent paths — and the manifest
    # parent often exists even when the manifest itself is missing.
    return parent.resolve()


def _glob_non_glob_prefix(config: str) -> Path:
    """Return the longest leading path component sequence with no glob
    metacharacters, falling back to ``cwd`` for purely-relative globs.
    """
    parts = Path(config).parts
    safe: list[str] = []
    for part in parts:
        if any(char in part for char in "*?[]"):
            break
        safe.append(part)
    if not safe:
        return Path.cwd()
    candidate = Path(*safe)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _candidate_manifest_paths(
    *, config: str, workspace: Path | None
) -> list[Path]:
    """Enumerate the manifest paths the user pointed scan/doctor at.

    Mirrors ``_resolve_config_paths`` but does not raise — it's called
    from inside the ``ConfigError`` handler, where re-raising would
    obscure the original failure. Returns an empty list when nothing
    resolves; the dispatcher then falls back to ``MISSING-MANIFEST``.
    """
    try:
        if workspace is not None:
            return list(discover_manifest_paths(workspace))
        if any(char in config for char in "*?[]"):
            return sorted(Path(p) for p in glob.glob(config, recursive=True))
        return [Path(config)]
    except Exception:  # noqa: BLE001 — diagnostic dispatch must not fail
        return []


def _diagnose_config_error(
    *,
    config: str,
    workspace: Path | None,
    exc: ConfigError,
    plugins_enabled: bool | None = None,
) -> list:
    """Pick the right diagnostic for a ``ConfigError``.

    ``ConfigError`` covers three distinct failure shapes:

    - the manifest file does not exist (``MISSING-MANIFEST``)
    - one or more candidate manifest files exist but the loader
      rejected them — invalid YAML, schema validation failure,
      unsupported version (``INVALID-MANIFEST``)
    - the manifest is well-formed and references a
      ``tool_sources[].type`` that resolves to no registered adapter
      (``UNKNOWN-ADAPTER-SOURCE-TYPE``, v0.20 PR #111 review
      follow-up #5)

    Disambiguate by walking every candidate path the CLI invocation
    points at (direct ``-c <file>``, ``--workspace`` discovery, or a
    glob pattern). If any candidate is a real file, the loader is
    choking on it — but first check whether the error is an
    unknown-adapter error (which means the manifest itself is fine
    and the right rank-1 action is to install/enable the adapter,
    not edit the YAML).
    """
    unknown_adapter_diag = _maybe_diagnose_unknown_adapter(
        config=config, workspace=workspace, exc=exc, plugins_enabled=plugins_enabled
    )
    if unknown_adapter_diag is not None:
        return unknown_adapter_diag
    for candidate in _candidate_manifest_paths(
        config=config, workspace=workspace
    ):
        if candidate.is_file():
            return diagnose_invalid_manifest(candidate, message=str(exc))
    return diagnose_missing_manifest(
        _missing_manifest_workspace(config=config, workspace=workspace)
    )


def _echo_next_action_hint(actions: list, *, limit: int = 1) -> None:
    """Print the top ranked recovery step(s) for a human reader.

    Coding agents get the same actions as one structured JSON line via
    ``emit_agent_mode_error``; these prose lines are suppressed in agent
    mode so that JSON line stays the only machine-relevant stderr output
    (per the ``docs/errors.json`` single-JSON-line contract).
    """
    from agents_shipgate.cli.agent_mode import is_agent_mode

    if is_agent_mode():
        return
    for action in actions[:limit]:
        typer.echo(f"next: {action.to_legacy_string()}", err=True)
        if action.kind in {"command", "edit"} and action.why:
            typer.echo(f"  why: {action.why}", err=True)


def _maybe_diagnose_unknown_adapter(
    *,
    config: str,
    workspace: Path | None,
    exc: ConfigError,
    plugins_enabled: bool | None = None,
) -> list | None:
    """v0.20 (PR #111 review follow-up #5): detect the
    ``AdapterRegistry.require`` unknown-source-type error and route
    it to ``DIAG_UNKNOWN_ADAPTER_SOURCE_TYPE`` with install / enable
    / typo next_actions, instead of the legacy ``INVALID-MANIFEST``
    "edit shipgate.yaml" path.

    Returns ``None`` if the error is anything else, so the caller
    falls through to the existing manifest-missing / manifest-invalid
    diagnostics.

    Pattern-matches on the ``"No adapter registered for source type "``
    prefix produced by ``AdapterRegistry.require``. Brittle if the
    error text changes — there's a contract test asserting the prefix
    stays stable.
    """

    import re

    message = str(exc)
    match = re.match(
        r"No adapter registered for source type '([^']+)'\.", message
    )
    if match is None:
        return None
    source_type = match.group(1)
    # Use the same plugins-enabled logic the dispatcher does so the
    # diagnostic's next_action set matches the failure mode.
    from agents_shipgate.inputs.protocol import _adapter_plugins_enabled

    discovery_enabled = _adapter_plugins_enabled(plugins_enabled)
    # Locate a candidate manifest path to thread into the diagnostic's
    # `edit` action; fall back to the user-supplied config string if
    # nothing exists on disk (defensive — discovery shouldn't fire if
    # the manifest were missing).
    candidate_path = Path(config)
    for candidate in _candidate_manifest_paths(
        config=config, workspace=workspace
    ):
        if candidate.is_file():
            candidate_path = candidate
            break

    from agents_shipgate.cli.diagnostics import (
        diagnose_unknown_adapter_source_type,
    )

    return diagnose_unknown_adapter_source_type(
        candidate_path,
        source_type=source_type,
        plugins_enabled=discovery_enabled,
        message=message,
    )


def _run_multi_scan(
    *,
    config_paths: list[Path],
    out: Path | None,
    formats: list[str],
    ci_mode: str | None,
    fail_on: list[str] | None,
    baseline: Path | None,
    diff_from: Path | None,
    baseline_mode: str,
    deep_import: bool,
    policy_packs: list[Path],
    plugins_enabled: bool | None,
    verbose: bool,
    suggest_patches: bool = False,
    packet_enabled: bool | None = None,
    packet_formats: list[str] | None = None,
    strict_plugins: bool = False,
    no_heuristics: bool = False,
) -> int:
    typer.echo(f"Agents Shipgate {__version__}")
    typer.echo(f"Scanning {len(config_paths)} manifests")
    typer.echo("")
    exit_code = 0
    for config_path in config_paths:
        output_dir = None
        if out is not None:
            output_dir = out / _safe_output_name(config_path)
        try:
            report, scan_exit_code = run_scan(
                config_path=config_path,
                output_dir=output_dir,
                formats=formats,
                ci_mode=ci_mode,
                fail_on=fail_on,
                baseline_path=baseline,
                diff_from_path=diff_from,
                baseline_mode=baseline_mode,
                deep_import=deep_import,
                policy_pack_paths=policy_packs,
                plugins_enabled=plugins_enabled,
                verbose=verbose,
                suggest_patches=suggest_patches,
                packet_enabled=packet_enabled,
                packet_formats=packet_formats,
                no_heuristics=no_heuristics,
            )
        except ConfigError as exc:
            scan_exit_code = 2
            typer.echo(f"{config_path}: config_error - {exc}", err=True)
        except InputParseError as exc:
            scan_exit_code = 3
            typer.echo(f"{config_path}: input_parse_error - {exc}", err=True)
        except AgentsShipgateError as exc:
            scan_exit_code = 4
            typer.echo(f"{config_path}: agents_shipgate_error - {exc}", err=True)
        except Exception as exc:  # noqa: BLE001 - multi-scan boundary.
            scan_exit_code = 4
            if verbose:
                logger.exception("unhandled exception while scanning %s", config_path)
            typer.echo(f"{config_path}: internal_error - {exc}", err=True)
        else:
            # Apply --strict-plugins after the scan but before printing
            # the per-config summary so the operator sees the elevated
            # exit code reflected in the multi-scan tally.
            scan_exit_code = _apply_strict_plugins(
                report,
                scan_exit_code,
                strict_plugins=strict_plugins,
                label=str(config_path),
            )
            # v0.8: lead with release_decision.decision (baseline-aware,
            # the recommended release-gate signal). Fall back to the
            # legacy summary.status only if the report somehow lacks
            # release_decision (older baselines loaded for diff, etc.).
            decision = report.release_decision
            if decision is not None:
                typer.echo(
                    f"{config_path}: {decision.decision} "
                    f"(blockers={len(decision.blockers)}, "
                    f"review_items={len(decision.review_items)}, "
                    f"critical={report.summary.critical_count}, "
                    f"high={report.summary.high_count})"
                )
            else:
                typer.echo(
                    f"{config_path}: {report.summary.status} "
                    f"(critical={report.summary.critical_count}, "
                    f"high={report.summary.high_count})"
                )
        exit_code = max(exit_code, scan_exit_code)
    typer.echo("")
    typer.echo(f"Exit code: {exit_code}")
    return exit_code


def _safe_output_name(config_path: Path) -> str:
    parent = config_path.parent
    try:
        display_parent = parent.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        display_parent = parent.resolve()
    raw = display_parent.as_posix()
    if raw in {"", "."}:
        return "root"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return safe or "root"


def _print_cli_summary(report, ci_mode: str, exit_code: int, *, verbose: bool = False) -> None:
    summary = report.summary
    decision = report.release_decision
    typer.echo(f"Agents Shipgate {__version__}")
    typer.echo("")
    typer.echo(f"Project: {report.project.get('name')}")
    typer.echo(f"Agent: {report.agent.get('name')}")
    typer.echo(f"Target: {report.environment.get('target')}")
    typer.echo("")
    if decision is not None:
        typer.echo(f"Decision: {decision.decision}")
        if report.agent_summary:
            typer.echo(f"Summary: {report.agent_summary.headline}")
        typer.echo(f"Reason: {decision.reason}")
        typer.echo(f"Blockers: {len(decision.blockers)}")
        typer.echo(f"Review items: {len(decision.review_items)}")
        ev = decision.evidence_coverage
        typer.echo(f"Evidence coverage: {evidence_coverage_text(ev)}")
        if decision.decision == "insufficient_evidence":
            typer.echo(f"Improve evidence: {primary_evidence_remediation_text(ev)}")
        if report.agent_summary and report.agent_summary.first_recommended_action:
            action = report.agent_summary.first_recommended_action
            if action.command:
                typer.echo(f"Next action: {action.command}")
            else:
                typer.echo(f"Next action: {action.why}")
        bd = decision.baseline_delta
        if bd.enabled:
            typer.echo(
                "Baseline delta: "
                f"matched={bd.matched_count}, new={bd.new_count}, "
                f"resolved={bd.resolved_count}"
            )
        else:
            typer.echo("Baseline delta: not enabled")
        fp = decision.fail_policy
        fail_on_text = ", ".join(fp.fail_on) if fp.fail_on else "none"
        typer.echo(
            f"Fail policy: ci_mode={fp.ci_mode}, fail_on=[{fail_on_text}], "
            f"new_findings_only={str(fp.new_findings_only).lower()}, "
            f"would_fail_ci={str(fp.would_fail_ci).lower()}"
        )
        typer.echo(f"Static-verdict boundary: {STATIC_VERDICT_DISCLAIMER}")
    else:
        typer.echo("Decision: (not recorded)")
    typer.echo("")
    typer.echo(
        f"Counts: critical={summary.critical_count}, high={summary.high_count}, "
        f"medium={summary.medium_count}, low={summary.low_count}, "
        f"suppressed={summary.suppressed_count}"
    )
    action_diff = report.action_surface_diff
    if action_diff.enabled:
        if _action_surface_has_signal(action_diff.summary):
            typer.echo(
                "Action-surface diff: "
                f"+{action_diff.summary.actions_added} actions, "
                f"-{action_diff.summary.actions_removed} actions, "
                f"{action_diff.summary.actions_modified} modified, "
                f"{action_diff.summary.blocking_findings} blocking finding(s)"
            )
        else:
            typer.echo("Action-surface diff: no changes")
    elif action_diff.notes:
        typer.echo(f"Action-surface diff: disabled ({action_diff.notes[0]})")
    diff = report.tool_surface_diff
    if diff.enabled:
        if _tool_surface_diff_has_changes(diff.summary):
            typer.echo(
                "Tool-surface diff: "
                f"+{diff.summary.tools_added} tools, "
                f"-{diff.summary.tools_removed} tools, "
                f"{diff.summary.tools_changed} changed, "
                f"{diff.summary.new_high_risk_effects} new high-risk effect(s), "
                f"{diff.summary.controls_removed} removed control(s)"
            )
        else:
            typer.echo("Tool-surface diff: no changes")
    elif diff.notes:
        typer.echo(f"Tool-surface diff: disabled ({diff.notes[0]})")
    if verbose:
        typer.echo(f"Tool count: {report.tool_surface.total_tools}")
        typer.echo(f"Source warnings: {len(report.source_warnings)}")
    typer.echo("")
    top = _top_cli_findings(report, limit=3)
    typer.echo("Top findings:")
    if top:
        for finding in top:
            target = f": {finding.tool_name}" if finding.tool_name else ""
            typer.echo(f"- {finding.check_id}{target} - {finding.title}")
    else:
        typer.echo("- none")
    typer.echo("")
    typer.echo("Reports:")
    for path in report.generated_reports.values():
        typer.echo(f"- {path}")
    if verbose and report.source_warnings:
        typer.echo("")
        typer.echo("Source warnings:")
        for warning in report.source_warnings:
            typer.echo(f"- {warning}")
    typer.echo("")
    typer.echo(f"CI mode: {ci_mode}")
    typer.echo(f"Exit code: {exit_code}")


def _top_cli_findings(report, *, limit: int):
    active = [finding for finding in report.findings if not finding.suppressed]
    by_id = {finding.id: finding for finding in active if finding.id}
    by_fingerprint = {
        finding.fingerprint: finding for finding in active if finding.fingerprint
    }
    selected = []
    seen: set[str] = set()

    def add_finding(finding) -> None:
        key = finding.id or finding.fingerprint or f"{finding.check_id}:{finding.title}"
        if key in seen:
            return
        selected.append(finding)
        seen.add(key)

    if report.release_decision is not None:
        for item in [
            *report.release_decision.blockers,
            *report.release_decision.review_items,
        ]:
            finding = (
                by_id.get(item.id)
                or by_fingerprint.get(item.fingerprint)
                or _active_finding_for_item(active, item)
            )
            if finding is not None:
                add_finding(finding)
            if len(selected) >= limit:
                return selected

    severity_top = [
        finding
        for finding in active
        if finding.severity in {"critical", "high"}
    ]
    severity_top = sorted(
        severity_top,
        key=lambda finding: (SEVERITY_ORDER[finding.severity], finding.check_id),
    )
    for finding in severity_top:
        add_finding(finding)
        if len(selected) >= limit:
            break
    return selected


def _active_finding_for_item(active_findings, item):
    for finding in active_findings:
        if finding.check_id == item.check_id and finding.title == item.title:
            return finding
    return None


def _tool_surface_diff_has_changes(summary) -> bool:
    return any(
        (
            summary.tools_added,
            summary.tools_removed,
            summary.tools_changed,
            summary.new_scopes,
            summary.removed_scopes,
            summary.new_high_risk_effects,
            summary.removed_high_risk_effects,
            summary.controls_added,
            summary.controls_removed,
            summary.metadata_changes,
            summary.policy_drift_items,
            summary.new_findings,
            summary.resolved_findings,
            summary.unchanged_findings,
            summary.accepted_debt,
        )
    )


def _action_surface_has_signal(summary) -> bool:
    return any(
        (
            summary.actions_added,
            summary.actions_removed,
            summary.actions_modified,
            summary.scope_expansions,
            summary.effect_escalations,
            summary.risk_tags_added,
            summary.approvals_removed,
            summary.safeguards_removed,
            summary.input_schema_expansions,
            summary.blocking_findings,
        )
    )
