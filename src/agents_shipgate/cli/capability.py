from __future__ import annotations

from pathlib import Path

import typer

from agents_shipgate import __version__
from agents_shipgate.cli.scan.inputs import _load_inputs
from agents_shipgate.cli.scan.prepare import _prepare_scan
from agents_shipgate.cli.scan.tools_agent import _build_tools_and_agent
from agents_shipgate.core.capability_lock import (
    DEFAULT_CAPABILITY_LOCK_PATH,
    DEFAULT_CAPABILITY_LOCK_REPORT_PATH,
    build_capability_lock,
    diff_capability_locks,
    load_capability_lock,
    render_capability_lock_diff_json,
    render_capability_lock_json,
)
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging

capability_app = typer.Typer(
    help=(
        "Experimental capability lock commands. Local-only and non-gating; "
        "release_decision.decision remains the only gate."
    )
)


@capability_app.command("export")
def capability_export(
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Manifest path used to build the capability lock.",
    ),
    out: Path = typer.Option(
        DEFAULT_CAPABILITY_LOCK_PATH,
        "--out",
        help="Capability lock JSON path to write.",
    ),
    report_out: Path = typer.Option(
        DEFAULT_CAPABILITY_LOCK_REPORT_PATH,
        "--report-out",
        help="Generated report-copy path to write when report copy is enabled.",
    ),
    report_copy: bool = typer.Option(
        True,
        "--report-copy/--no-report-copy",
        help="Also write a byte-identical generated copy under agents-shipgate-reports.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the lock JSON to stdout.",
    ),
    no_plugins: bool = typer.Option(
        False,
        "--no-plugins",
        help="Do not load third-party adapters while exporting the lock.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs."),
) -> None:
    """Export the current static capability envelope as an experimental lock."""

    try:
        configure_logging(verbose=verbose)
        lock = _build_lock_from_config(
            config=config,
            no_plugins=no_plugins,
            verbose=verbose,
        )
        rendered = render_capability_lock_json(lock)
        _write_text(out, rendered)
        if report_copy and report_out != out:
            _write_text(report_out, rendered)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        raise typer.Exit(4) from exc

    if json_output:
        typer.echo(rendered.rstrip())
        return
    typer.echo(f"Wrote capability lock to {out}")
    if report_copy and report_out != out:
        typer.echo(f"Report copy: {report_out}")
    typer.echo(f"Capabilities: {lock.summary.capability_count}")


@capability_app.command("diff")
def capability_diff(
    base: Path = typer.Option(
        ...,
        "--base",
        help="Base capability lock JSON path.",
    ),
    head: Path = typer.Option(
        ...,
        "--head",
        help="Head capability lock JSON path.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Write the capability lock diff JSON to this path.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the diff JSON to stdout.",
    ),
) -> None:
    """Compare two experimental capability locks.

    Capability differences are reported in the JSON payload and do not
    change the process exit code. Nonzero exits are reserved for malformed
    or missing inputs.
    """

    try:
        diff = diff_capability_locks(
            load_capability_lock(base),
            load_capability_lock(head),
            base_path=base,
            head_path=head,
        )
        rendered = render_capability_lock_diff_json(diff)
        if out is not None:
            _write_text(out, rendered)
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except AgentsShipgateError as exc:
        typer.echo(f"Agents Shipgate error: {exc}", err=True)
        raise typer.Exit(4) from exc

    if json_output or out is None:
        typer.echo(rendered.rstrip())
        return
    summary = diff.summary
    typer.echo(f"Wrote capability lock diff to {out}")
    typer.echo(
        "Added: "
        f"{summary.added}  Changed: {summary.changed}  "
        f"Evidence-only: {summary.evidence_changed}  Removed: {summary.removed}  "
        f"Unchanged: {summary.unchanged}"
    )


def _build_lock_from_config(
    *,
    config: Path,
    no_plugins: bool,
    verbose: bool,
):
    resolved = _prepare_scan(
        config_path=config,
        ci_mode=None,
        fail_on=None,
        output_dir=None,
        formats=None,
        packet_enabled=False,
        packet_formats=None,
        baseline_mode="new-findings",
    )
    inputs = _load_inputs(
        manifest=resolved.manifest,
        base_dir=resolved.base_dir,
        config_path=config,
        policy_pack_paths=None,
        verbose=verbose,
        plugins_enabled=not no_plugins,
    )
    tools_and_agent = _build_tools_and_agent(
        manifest=resolved.manifest,
        inputs=inputs,
    )
    return build_capability_lock(
        resolved.manifest,
        agent=tools_and_agent.agent,
        tools=tools_and_agent.tools,
        config_path=config,
        manifest_dir=resolved.base_dir,
        cli_version=__version__,
        source_count=len(inputs.loaded_sources),
        source_warning_count=len(tools_and_agent.warnings),
        plugins_enabled=not no_plugins,
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


__all__ = ["capability_app"]
