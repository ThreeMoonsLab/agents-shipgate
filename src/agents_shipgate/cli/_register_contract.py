from __future__ import annotations

import json

import typer

from agents_shipgate.schemas.contract import build_contract_payload


def register(app: typer.Typer) -> None:
    @app.command()
    def contract(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
    ) -> None:
        """Show the installed CLI contract for agent consumers."""
        payload = build_contract_payload()
        if json_output:
            typer.echo(json.dumps(payload.model_dump(mode="json"), indent=2))
            return

        typer.echo(f"Contract version: {payload.contract_version}")
        typer.echo(f"CLI version: {payload.cli_version}")
        typer.echo(f"Report schema version: {payload.report_schema_version}")
        typer.echo(f"Packet schema version: {payload.packet_schema_version}")
        typer.echo(f"Gating signal: {payload.gating_signal}")
        typer.echo("Manual review signals:")
        for signal in payload.manual_review_signals:
            typer.echo(f"  {signal}")
