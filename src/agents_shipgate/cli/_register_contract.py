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
        typer.echo(
            "Capability lock schema version: "
            f"{payload.capability_lock_schema_version}"
        )
        typer.echo(
            "Capability lock diff schema version: "
            f"{payload.capability_lock_diff_schema_version}"
        )
        typer.echo(
            "Capability standard version: "
            f"{payload.capability_standard_version}"
        )
        typer.echo(
            "Governance benchmark catalog schema version: "
            f"{payload.governance_benchmark_catalog_schema_version}"
        )
        typer.echo(
            "Governance benchmark result schema version: "
            f"{payload.governance_benchmark_result_schema_version}"
        )
        typer.echo("External integration surfaces:")
        for surface in payload.external_integration_surfaces:
            typer.echo(f"  {surface}")
        typer.echo(f"Gating signal: {payload.gating_signal}")
        typer.echo("Manual review signals:")
        for signal in payload.manual_review_signals:
            typer.echo(f"  {signal}")
