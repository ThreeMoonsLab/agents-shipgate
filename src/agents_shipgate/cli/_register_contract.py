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
        typer.echo(f"Verifier schema version: {payload.verifier_schema_version}")
        typer.echo(f"Verify-run schema version: {payload.verify_run_schema_version}")
        typer.echo(
            f"Agent handoff schema version: {payload.agent_handoff_schema_version}"
        )
        typer.echo(f"Agent handoff schema path: {payload.agent_handoff_schema_path}")
        typer.echo(f"Agent handoff artifact: {payload.agent_handoff_artifact}")
        typer.echo(
            "Codex boundary result schema version: "
            f"{payload.codex_boundary_result_schema_version}"
        )
        typer.echo(f"Capability lock schema version: {payload.capability_lock_schema_version}")
        typer.echo(
            f"Capability lock diff schema version: {payload.capability_lock_diff_schema_version}"
        )
        typer.echo(f"Preflight schema version: {payload.preflight_schema_version}")
        typer.echo(f"Capability standard version: {payload.capability_standard_version}")
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
        typer.echo(f"Agent result schema version: {payload.agent_result_schema_version}")
        typer.echo(f"Agent result schema path: {payload.agent_result_schema_path}")
        typer.echo("Agent result control fields:")
        for field in payload.agent_result_control_fields:
            typer.echo(f"  {field}")
        typer.echo("Manual review signals:")
        for signal in payload.manual_review_signals:
            typer.echo(f"  {signal}")
        typer.echo("Agent interface operations:")
        for operation in payload.agent_interface_operations:
            typer.echo(f"  {operation}")
        typer.echo("Exit code policy:")
        for code, meaning in payload.exit_code_policy.items():
            typer.echo(f"  {code}: {meaning}")
        typer.echo("MCP tools:")
        for tool in payload.mcp_tools:
            typer.echo(f"  {tool}")
        typer.echo("Commands:")
        for name, command in payload.commands.items():
            typer.echo(f"  {name}: {command}")
        typer.echo("Artifacts:")
        for name, path in payload.artifacts.items():
            typer.echo(f"  {name}: {path}")
        typer.echo("Agent read order:")
        for field in payload.agent_read_order:
            typer.echo(f"  {field}")
        typer.echo("Verifier read order:")
        for field in payload.verifier_read_order:
            typer.echo(f"  {field}")
