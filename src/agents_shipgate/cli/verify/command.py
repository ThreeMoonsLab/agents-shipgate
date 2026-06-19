from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from agents_shipgate.cli._helpers import _parse_fail_on
from agents_shipgate.cli.agent_mode import emit_agent_mode_error
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging

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
        help="Local base ref/SHA for PR diff. Verify never fetches it.",
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
    format_: str = typer.Option(
        "text",
        "--format",
        help="Verifier stdout format: text or json. Scan artifacts are fixed.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Shortcut for --format json (the coding-agent surface).",
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

    try:
        configure_logging(verbose=verbose)
        stdout_format = "json" if json_output else _parse_verify_format(format_)
        if ci_mode and ci_mode not in {"advisory", "strict"}:
            raise ConfigError("--ci-mode must be advisory or strict")
        parsed_fail_on = _parse_fail_on(fail_on)
        parsed_pr_comment_style = _parse_pr_comment_style(pr_comment_style)
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
            )
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        kind = "verify_ref_error" if _looks_like_ref_error(str(exc)) else "config_error"
        emit_agent_mode_error(
            kind,
            message=str(exc),
            exit_code=2,
            command="agents-shipgate verify",
            next_action=_verify_next_action(kind),
            next_actions=_verify_next_actions(kind),
            artifacts=_verify_artifact_hints(out),
        )
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        emit_agent_mode_error(
            "input_parse_error",
            message=str(exc),
            exit_code=3,
            command="agents-shipgate verify",
            next_action="agents-shipgate doctor -c shipgate.yaml --json",
            next_actions=[
                {
                    "kind": "command",
                    "command": "agents-shipgate doctor -c shipgate.yaml --json",
                    "why": "Inspect unresolved sources before rerunning verify.",
                    "expects": "diagnostics and unresolved_sources",
                }
            ],
            artifacts=_verify_artifact_hints(out),
        )
        raise typer.Exit(3) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        emit_agent_mode_error(
            "other_error",
            message=str(exc),
            exit_code=4,
            command="agents-shipgate verify",
            next_action="rerun verify with --verbose",
            next_actions=[
                {
                    "kind": "command",
                    "command": "agents-shipgate verify --verbose --json",
                    "why": "Surface the underlying application-layer failure.",
                    "expects": "debug logging plus verifier artifacts when writable",
                }
            ],
            artifacts=_verify_artifact_hints(out),
        )
        raise typer.Exit(4) from exc
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        if verbose:
            logger.exception("unhandled exception")
        typer.echo(f"Internal error: {exc}", err=True)
        emit_agent_mode_error(
            "internal_error",
            message=str(exc),
            exit_code=4,
            command="agents-shipgate verify",
            next_action="rerun verify with --verbose and file a bug if it repeats",
            next_actions=[
                {
                    "kind": "command",
                    "command": "agents-shipgate verify --verbose --json",
                    "why": "Capture a traceback for the unexpected verifier failure.",
                    "expects": "debug logging",
                }
            ],
            artifacts=_verify_artifact_hints(out),
        )
        raise typer.Exit(4) from exc

    if stdout_format == "json":
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


def _parse_verify_format(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"text", "human"}:
        return "text"
    if normalized == "json":
        return "json"
    raise ConfigError("--format must be text or json for verify")


def _parse_pr_comment_style(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"capability-review", "capability_review"}:
        return "capability-review"
    if normalized in {"findings", "v1-findings", "legacy"}:
        return "findings"
    raise ConfigError("--pr-comment-style must be capability-review or findings")


def _looks_like_ref_error(message: str) -> bool:
    lowered = message.lower()
    return "ref" in lowered and ("locally" in lowered or "head" in lowered)


def _verify_next_action(kind: str) -> str:
    if kind == "verify_ref_error":
        return "fetch the missing base/head ref, then rerun agents-shipgate verify"
    return "agents-shipgate doctor -c shipgate.yaml --json"


def _verify_next_actions(kind: str) -> list[dict[str, str]]:
    if kind == "verify_ref_error":
        return [
            {
                "kind": "command",
                "command": "git fetch --all --prune",
                "why": "Make the base/head refs available locally; verify never fetches.",
                "expects": "local refs become resolvable",
            },
            {
                "kind": "command",
                "command": "agents-shipgate verify --workspace . --config shipgate.yaml --json",
                "why": "Rerun verifier after refs are available.",
                "expects": "verifier.json and verify-run.json",
            },
        ]
    return [
        {
            "kind": "command",
            "command": "agents-shipgate doctor -c shipgate.yaml --json",
            "why": "Inspect manifest/config diagnostics before rerunning verify.",
            "expects": "diagnostics and next_actions",
        }
    ]


def _verify_artifact_hints(out: Path | None) -> dict[str, str]:
    out_dir = out or Path("agents-shipgate-reports")
    return {
        "verifier_json": str(out_dir / "verifier.json"),
        "verify_run_json": str(out_dir / "verify-run.json"),
        "report_json": str(out_dir / "report.json"),
    }


__all__ = ["verify"]
