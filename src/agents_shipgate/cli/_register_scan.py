from __future__ import annotations

import logging
from pathlib import Path

import typer

from agents_shipgate.cli._helpers import (
    _apply_strict_plugins,
    _diagnose_config_error,
    _parse_fail_on,
    _parse_formats,
    _parse_packet_formats,
    _print_cli_summary,
    _resolve_config_paths,
    _run_multi_scan,
)
from agents_shipgate.cli.agent_mode import emit_agent_mode_error as _emit_agent_mode_error
from agents_shipgate.cli.diagnostics import NextAction, top_next_actions
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging

logger = logging.getLogger(__name__)


def register(app: typer.Typer) -> None:
    @app.command()
    def scan(
        config: str = typer.Option(
            "shipgate.yaml",
            "--config",
            "-c",
            help="Path or quoted glob for shipgate.yaml.",
        ),
        workspace: Path | None = typer.Option(
            None,
            "--workspace",
            help="Scan every shipgate.yaml below this workspace.",
        ),
        out: Path | None = typer.Option(
            None,
            "--out",
            help="Output directory for reports. Overrides manifest output.directory.",
        ),
        formats: str = typer.Option(
            "markdown,json",
            "--format",
            help="Comma-separated report formats: markdown,json,sarif.",
        ),
        ci_mode: str | None = typer.Option(
            None,
            "--ci-mode",
            help="advisory or strict. Overrides manifest ci.mode.",
        ),
        fail_on: str | None = typer.Option(
            None,
            "--fail-on",
            help="Comma-separated severities that fail CI, for example critical,high.",
        ),
        baseline: Path | None = typer.Option(
            None,
            "--baseline",
            help="Path to a local baseline JSON. Strict mode fails only on new findings.",
        ),
        diff_from: Path | None = typer.Option(
            None,
            "--diff-from",
            help="Prior report.json or v0.3 baseline JSON used for tool-surface diff.",
        ),
        baseline_mode: str = typer.Option(
            "new-findings",
            "--baseline-mode",
            help="Baseline comparison mode. Supported value: new-findings.",
        ),
        policy_packs: list[Path] | None = typer.Option(
            None,
            "--policy-pack",
            help="Additional declarative YAML policy pack path. May be supplied multiple times.",
        ),
        deep_import: bool = typer.Option(
            False,
            "--deep-import",
            help="Deferred. Explicit import execution is not supported yet.",
            hidden=True,
        ),
        no_plugins: bool = typer.Option(
            False,
            "--no-plugins",
            help="Do not load third-party check plugins even when AGENTS_SHIPGATE_ENABLE_PLUGINS is set.",
        ),
        strict_plugins: bool = typer.Option(
            False,
            "--strict-plugins",
            help=(
                "Exit non-zero (code 4) if any loaded plugin failed validation or "
                "produced runtime errors. Default lenient mode records the failure "
                "in report.loaded_plugins but proceeds with the scan."
            ),
        ),
        suggest_patches: bool = typer.Option(
            False,
            "--suggest-patches",
            help=(
                "Attach machine-applicable patches (or ManualPatch fallback) to "
                "every active finding. Use `agents-shipgate apply-patches` to "
                "apply them; the report stays read-only."
            ),
        ),
        packet: bool | None = typer.Option(
            None,
            "--packet/--no-packet",
            help=(
                "Emit the Release Evidence Packet alongside report.{md,json}. "
                "Defaults to manifest output.packet.enabled (true unless the "
                "manifest disables it). Use --no-packet to override."
            ),
        ),
        packet_format: str | None = typer.Option(
            None,
            "--packet-format",
            help=(
                "Comma-separated packet formats: md,json,html,pdf. "
                "Default from manifest output.packet.formats (md,json,html). "
                "PDF requires the [pdf] extras."
            ),
        ),
        verbose: bool = typer.Option(False, "--verbose", help="Show debug extraction details."),
    ) -> None:
        """Run the local-first, static Tool-Use Readiness release gate for AI agent tool surfaces."""
        # Parse CLI options first, in their own try block. ConfigError raised
        # here is about flag values, not the manifest — emitting a manifest
        # diagnostic ("edit shipgate.yaml") would route the agent to the
        # wrong fix.
        try:
            configure_logging(verbose=verbose)
            parsed_formats = _parse_formats(formats)
            parsed_packet_formats = _parse_packet_formats(packet_format)
            if ci_mode and ci_mode not in {"advisory", "strict"}:
                raise ConfigError("--ci-mode must be advisory or strict")
            parsed_fail_on = _parse_fail_on(fail_on)
        except ConfigError as exc:
            typer.echo(f"Config error: {exc}", err=True)
            guidance = (
                "Fix the invalid CLI flag value referenced in the error and "
                "re-run scan."
            )
            _emit_agent_mode_error(
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
            config_paths = _resolve_config_paths(config=config, workspace=workspace)
            if len(config_paths) == 1:
                report, exit_code = run_scan(
                    config_path=config_paths[0],
                    output_dir=out,
                    formats=parsed_formats,
                    ci_mode=ci_mode,
                    fail_on=parsed_fail_on,
                    baseline_path=baseline,
                    diff_from_path=diff_from,
                    baseline_mode=baseline_mode,
                    deep_import=deep_import,
                    policy_pack_paths=policy_packs,
                    plugins_enabled=False if no_plugins else None,
                    verbose=verbose,
                    suggest_patches=suggest_patches,
                    packet_enabled=packet,
                    packet_formats=parsed_packet_formats,
                )
                exit_code = _apply_strict_plugins(
                    report, exit_code, strict_plugins=strict_plugins
                )
                _print_cli_summary(report, ci_mode or "advisory", exit_code, verbose=verbose)
                raise typer.Exit(exit_code)
            exit_code = _run_multi_scan(
                config_paths=config_paths,
                out=out,
                formats=parsed_formats,
                ci_mode=ci_mode,
                fail_on=parsed_fail_on,
                baseline=baseline,
                diff_from=diff_from,
                baseline_mode=baseline_mode,
                deep_import=deep_import,
                policy_packs=policy_packs or [],
                plugins_enabled=False if no_plugins else None,
                verbose=verbose,
                suggest_patches=suggest_patches,
                packet_enabled=packet,
                packet_formats=parsed_packet_formats,
                strict_plugins=strict_plugins,
            )
        except ConfigError as exc:
            typer.echo(f"Config error: {exc}", err=True)
            diagnostics = _diagnose_config_error(
                config=config, workspace=workspace, exc=exc
            )
            flattened = top_next_actions(diagnostics)
            _emit_agent_mode_error(
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
            _emit_agent_mode_error(
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
            _emit_agent_mode_error(
                "other_error",
                message=str(exc),
                next_action=guidance,
                next_actions=[
                    NextAction(kind="review", why=guidance).model_dump(mode="json")
                ],
            )
            raise typer.Exit(4) from exc
        except typer.Exit:
            raise
        except Exception as exc:  # noqa: BLE001 - CLI boundary.
            if verbose:
                logger.exception("unhandled exception")
            typer.echo(f"Internal error: {exc}", err=True)
            guidance = (
                "Re-run with --verbose for a stack trace; this is a bug — please "
                "file an issue."
            )
            _emit_agent_mode_error(
                "internal_error",
                message=str(exc),
                next_action=guidance,
                next_actions=[
                    NextAction(kind="review", why=guidance).model_dump(mode="json")
                ],
            )
            raise typer.Exit(4) from exc

        raise typer.Exit(exit_code)
