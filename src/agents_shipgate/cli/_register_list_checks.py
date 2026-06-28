from __future__ import annotations

import json

import typer

from agents_shipgate.checks.registry import check_catalog


def register(app: typer.Typer) -> None:
    @app.command("list-checks", hidden=True)
    def list_checks(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
        no_plugins: bool = typer.Option(
            False,
            "--no-plugins",
            help="Do not load third-party check plugins even when AGENTS_SHIPGATE_ENABLE_PLUGINS is set.",
        ),
    ) -> None:
        """List the built-in check catalog."""
        checks = check_catalog(plugins_enabled=False if no_plugins else None)
        if json_output:
            typer.echo(json.dumps([check.model_dump() for check in checks], indent=2))
            return
        for check in checks:
            typer.echo(
                f"{check.id}\t{check.default_severity}\t{check.mvp_tier}\t"
                f"{check.category}\t{check.description}"
            )
