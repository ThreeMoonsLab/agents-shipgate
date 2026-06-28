from __future__ import annotations

import json
from difflib import get_close_matches

import typer

from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.agent_mode import emit_agent_mode_error as _emit_agent_mode_error
from agents_shipgate.schemas.diagnostics import NextAction


def register(app: typer.Typer) -> None:
    @app.command(hidden=True)
    def explain(
        check_id: str,
        no_plugins: bool = typer.Option(
            False,
            "--no-plugins",
            help="Do not load third-party check plugins even when AGENTS_SHIPGATE_ENABLE_PLUGINS is set.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
    ) -> None:
        """Explain why a check exists and when it fires."""
        checks = check_catalog(plugins_enabled=False if no_plugins else None)
        check = next((item for item in checks if item.id == check_id), None)
        if not check:
            matches = get_close_matches(check_id, [item.id for item in checks], n=1)
            suggestion = matches[0] if matches else None
            suffix = f". Did you mean {suggestion}?" if suggestion else ""
            typer.echo(f"Unknown check id: {check_id}{suffix}", err=True)
            _emit_agent_mode_error(
                "unknown_check_id",
                check_id=check_id,
                suggestion=suggestion,
                next_action="agents-shipgate list-checks --json",
                next_actions=[
                    NextAction(
                        kind="command",
                        command="agents-shipgate list-checks --json",
                        why=(
                            "Enumerate the full check catalog so the agent can "
                            "match by id."
                        ),
                        expects=(
                            "JSON array of CheckMetadata objects with stable ids."
                        ),
                    ).model_dump(mode="json")
                ],
            )
            raise typer.Exit(2)
        if json_output:
            typer.echo(json.dumps(check.model_dump(), indent=2, sort_keys=True))
            return
        typer.echo(check.id)
        typer.echo(f"Category: {check.category}")
        typer.echo(f"Default severity: {check.default_severity}")
        typer.echo("")
        typer.echo(check.description)
        if check.rationale:
            typer.echo("")
            typer.echo(f"Rationale: {check.rationale}")
        if check.fires_when:
            typer.echo(f"Fires when: {check.fires_when}")
        if check.evidence_fields:
            typer.echo(f"Evidence fields: {', '.join(check.evidence_fields)}")
        if check.recommendation:
            typer.echo(f"Recommendation: {check.recommendation}")
        if check.docs_url:
            typer.echo(f"Docs: {check.docs_url}")
