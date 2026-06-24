from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from agents_shipgate.ci.agent_result import build_agent_result
from agents_shipgate.cli._helpers import (
    _diagnose_config_error,
    _echo_next_action_hint,
    _parse_fail_on,
)
from agents_shipgate.cli.agent_mode import emit_agent_mode_error, is_agent_mode
from agents_shipgate.cli.diagnostics import top_next_actions
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging
from agents_shipgate.schemas.diagnostics import NextAction

from .orchestrator import run_preview, run_verify

logger = logging.getLogger(__name__)


def verify(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace/git checkout containing the head tree.",
    ),
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Path to shipgate.yaml, relative to --workspace unless absolute.",
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help=(
            "Local base ref/SHA for PR diff. Verify never fetches it. When "
            "omitted, verify auto-detects the default branch (origin/HEAD, "
            "origin/main, origin/master, main, master) if it points at a "
            "different commit than the head; --no-base disables that."
        ),
    ),
    no_base: bool = typer.Option(
        False,
        "--no-base",
        help=(
            "Disable base auto-detection when --base is omitted; scan only "
            "the working tree or explicit head."
        ),
    ),
    head: str | None = typer.Option(
        None,
        "--head",
        help=(
            "Local head ref/SHA to diff and scan from an isolated archive. "
            "Omit to scan the checked-out workspace."
        ),
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help=(
            "Lightweight relevance check: evaluate triggers and report "
            "whether Shipgate is relevant + what to run next, WITHOUT "
            "running a scan, requiring a manifest, or writing any files "
            "beyond the verifier artifacts. Always exits 0."
        ),
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory for verifier and scan artifacts.",
    ),
    format_: str | None = typer.Option(
        None,
        "--format",
        help=(
            "Verifier stdout format: text, json (full verifier artifact), or "
            "agent (compact agent result). Defaults to text, or agent when a "
            "coding-agent environment is detected. Scan artifacts are fixed."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help=(
            "Shortcut for the coding-agent surface: --format agent for "
            "verify runs, --format json for --preview (relevance data lives "
            "in the trigger block, which the compact form omits)."
        ),
    ),
    ci_mode: str | None = typer.Option(
        None,
        "--ci-mode",
        help="advisory or strict. Overrides manifest ci.mode for the head scan.",
    ),
    fail_on: str | None = typer.Option(
        None,
        "--fail-on",
        help="Comma-separated severities that fail CI.",
    ),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Path to a local baseline JSON for the head scan.",
    ),
    baseline_mode: str = typer.Option(
        "new-findings",
        "--baseline-mode",
        help="Baseline comparison mode. Supported value: new-findings.",
    ),
    diff_from: Path | None = typer.Option(
        None,
        "--diff-from",
        help="Explicit prior report.json or baseline JSON for head diff.",
    ),
    policy_packs: list[Path] | None = typer.Option(
        None,
        "--policy-pack",
        help="Additional declarative YAML policy pack path. May be repeated.",
    ),
    no_plugins: bool = typer.Option(
        False,
        "--no-plugins",
        help="Do not load third-party check plugins or adapters.",
    ),
    strict_plugins: bool = typer.Option(
        False,
        "--strict-plugins",
        help="Exit 4 if any loaded plugin or third-party adapter failed validation.",
    ),
    suggest_patches: bool = typer.Option(
        False,
        "--suggest-patches",
        help="Attach suggested patches to head scan findings.",
    ),
    no_heuristics: bool = typer.Option(
        False,
        "--no-heuristics",
        help="Filter heuristic findings before the head release decision.",
    ),
    pr_comment_style: str = typer.Option(
        "capability-review",
        "--pr-comment-style",
        help=(
            "PR comment renderer: capability-review (default) or findings "
            "(legacy v1 style, available for one minor release cycle)."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show debug details."),
) -> None:
    """Run the canonical ongoing-PR verifier around the existing scan engine."""

    # Flag parsing gets its own try block, mirroring scan: a ConfigError
    # raised here is about flag values, not the manifest — emitting a
    # manifest diagnostic ("run init") would route the caller to the
    # wrong fix.
    try:
        configure_logging(verbose=verbose)
        stdout_format = _resolve_verify_format(
            format_, json_output=json_output, preview=preview
        )
        if ci_mode and ci_mode not in {"advisory", "strict"}:
            raise ConfigError("--ci-mode must be advisory or strict")
        parsed_fail_on = _parse_fail_on(fail_on)
        parsed_pr_comment_style = _parse_pr_comment_style(pr_comment_style)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        guidance = (
            "Fix the invalid CLI flag value referenced in the error and "
            "re-run verify."
        )
        emit_agent_mode_error(
            "config_error",
            message=str(exc),
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review",
                    why=guidance,
                    expects=(
                        "Re-run with a flag value the option parser accepts."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(2) from exc

    try:
        if preview:
            verifier, _report, exit_code = run_preview(
                workspace=workspace,
                config=config,
                base=base,
                head=head,
                out=out,
                pr_comment_style=parsed_pr_comment_style,
            )
        else:
            head_ref = head or "HEAD"
            verifier, _report, exit_code = run_verify(
                workspace=workspace,
                config=config,
                base=base,
                head=head_ref,
                archive_head=head is not None,
                out=out,
                ci_mode=ci_mode,
                fail_on=parsed_fail_on,
                baseline=baseline,
                baseline_mode=baseline_mode,
                diff_from=diff_from,
                policy_packs=policy_packs,
                plugins_enabled=False if no_plugins else None,
                strict_plugins=strict_plugins,
                suggest_patches=suggest_patches,
                no_heuristics=no_heuristics,
                pr_comment_style=parsed_pr_comment_style,
                verbose=verbose,
                auto_base=base is None and not no_base,
            )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        diagnostics = _diagnose_config_error(
            config=str(config),
            workspace=workspace,
            exc=exc,
            plugins_enabled=False if no_plugins else None,
        )
        flattened = top_next_actions(diagnostics)
        _echo_next_action_hint(flattened)
        emit_agent_mode_error(
            "config_error",
            message=str(exc),
            next_action=flattened[0].to_legacy_string(),
            next_actions=[a.model_dump(mode="json") for a in flattened],
        )
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        guidance = (
            "Inspect the file referenced in the error; ensure it exists, "
            "is valid, and resolves under the manifest directory."
        )
        emit_agent_mode_error(
            "input_parse_error",
            message=str(exc),
            next_action=guidance,
            next_actions=[
                NextAction(
                    kind="review",
                    why=guidance,
                    expects=(
                        "Referenced file is present, parseable, and inside "
                        "the manifest directory."
                    ),
                ).model_dump(mode="json")
            ],
        )
        raise typer.Exit(3) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        guidance = (
            "Re-run with --verbose for a stack trace, then file an issue if "
            "the error is not actionable."
        )
        emit_agent_mode_error(
            "other_error",
            message=str(exc),
            next_action=guidance,
            next_actions=[
                NextAction(kind="review", why=guidance).model_dump(mode="json")
            ],
        )
        raise typer.Exit(4) from exc
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        if verbose:
            logger.exception("unhandled exception")
        typer.echo(f"Internal error: {exc}", err=True)
        raise typer.Exit(4) from exc

    if (
        stdout_format == "agent"
        and json_output
        and _is_missing_config_fail_closed(verifier)
    ):
        stdout_format = "json"

    if stdout_format == "agent":
        agent_result = build_agent_result(verifier=verifier, report=_report)
        typer.echo(
            json.dumps(agent_result.model_dump(mode="json"), indent=2, sort_keys=True)
        )
    elif stdout_format == "json":
        typer.echo(json.dumps(verifier.model_dump(mode="json"), indent=2))
    else:
        verdict = (
            verifier.release_decision.get("decision")
            if verifier.release_decision is not None
            else ("skipped" if verifier.head_status == "skipped" else "failed")
        )
        typer.echo(f"Agents Shipgate verify: {verdict}")
        typer.echo(f"Trigger: {verifier.trigger.get('rationale')}")
        typer.echo(f"Base status: {verifier.base_status}")
        typer.echo(f"Exit code: {exit_code}")
    raise typer.Exit(exit_code)


def _resolve_verify_format(
    value: str | None, *, json_output: bool, preview: bool
) -> str:
    """Resolve the stdout format from flags and the agent-mode environment.

    Precedence: explicit ``--format`` > ``--json`` shortcut > agent-mode
    auto-detection > text. ``--json`` maps to the compact agent result for
    verify runs but keeps the full verifier JSON for ``--preview``, whose
    relevance answer lives in the ``trigger`` block that the compact form
    omits. The same split applies when agent mode is auto-detected.
    """
    if value is not None:
        return _parse_verify_format(value)
    if json_output or is_agent_mode():
        return "json" if preview else "agent"
    return "text"


def _is_missing_config_fail_closed(verifier: object) -> bool:
    headline = str(getattr(verifier, "headline", "") or "").lower()
    return (
        getattr(verifier, "head_status", None) == "failed"
        and getattr(verifier, "head_exit_code", None) == 2
        and getattr(verifier, "release_decision", None) is None
        and getattr(verifier, "merge_verdict", None) == "unknown"
        and "config not found" in headline
    )


def _parse_verify_format(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"text", "human"}:
        return "text"
    if normalized == "json":
        return "json"
    if normalized == "agent":
        return "agent"
    raise ConfigError("--format must be text, json, or agent for verify")


def _parse_pr_comment_style(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"capability-review", "capability_review"}:
        return "capability-review"
    if normalized in {"findings", "v1-findings", "legacy"}:
        return "findings"
    raise ConfigError("--pr-comment-style must be capability-review or findings")


__all__ = ["verify"]
