from __future__ import annotations

from pathlib import Path

import typer

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.baseline import write_baseline
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.logging import configure_logging


def register(app: typer.Typer) -> None:
    baseline_app = typer.Typer(help="Manage local finding baselines.")

    @baseline_app.command("save")
    def baseline_save(
        config: Path = typer.Option(
            Path("shipgate.yaml"),
            "--config",
            "-c",
            help="Manifest path used to create the baseline.",
        ),
        out: Path = typer.Option(
            Path(".agents-shipgate/baseline.json"),
            "--out",
            help="Baseline JSON path to write.",
        ),
        verbose: bool = typer.Option(False, "--verbose", help="Enable debug logs."),
    ) -> None:
        """Save active unsuppressed findings as the current accepted baseline."""
        try:
            configure_logging(verbose=verbose)
            report, _ = run_scan(
                config_path=config,
                formats=["json"],
                ci_mode="advisory",
                verbose=verbose,
            )
            baseline = write_baseline(report, out)
        except ConfigError as exc:
            typer.echo(f"Config error: {exc}", err=True)
            raise typer.Exit(2) from exc
        except InputParseError as exc:
            typer.echo(f"Input parsing error: {exc}", err=True)
            raise typer.Exit(3) from exc
        except AgentsShipgateError as exc:
            typer.echo(f"Agents Shipgate error: {exc}", err=True)
            raise typer.Exit(4) from exc
        typer.echo(f"Wrote {out}")
        typer.echo(f"Findings saved: {len(baseline.findings)}")

    app.add_typer(baseline_app, name="baseline")
