from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import typer

from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.org_governance import (
    build_org_governance_status,
    render_org_status_markdown,
)

ORG_GOVERNANCE_EXIT_CODE = 20

org_app = typer.Typer(
    name="org",
    help="Organization governance status over local Shipgate artifacts.",
    no_args_is_help=True,
)


@org_app.command("status")
def org_status(
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Manifest path.",
    ),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root.",
    ),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Accepted-debt baseline path. Defaults to .agents-shipgate/baseline.json.",
    ),
    host_baseline: Path | None = typer.Option(
        None,
        "--host-baseline",
        help="Host-grants baseline path. Defaults to .agents-shipgate/host-grants.json.",
    ),
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="Reference date for expiry/age checks (YYYY-MM-DD; default today UTC).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Evaluate opt-in org governance gates without changing release verdicts."""

    try:
        as_of_date = date.fromisoformat(as_of) if as_of else datetime.now(UTC).date()
    except ValueError:
        typer.echo(f"--as-of must be an ISO date (YYYY-MM-DD), got {as_of!r}.", err=True)
        raise typer.Exit(2) from None

    root = workspace.resolve()
    config_path = config if config.is_absolute() else root / config
    try:
        manifest = load_manifest(config_path)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc

    baseline_path = None
    if baseline is not None:
        baseline_path = baseline if baseline.is_absolute() else root / baseline
    host_baseline_path = None
    if host_baseline is not None:
        host_baseline_path = (
            host_baseline if host_baseline.is_absolute() else root / host_baseline
        )

    payload = build_org_governance_status(
        manifest=manifest,
        config_path=config_path,
        workspace=root,
        as_of=as_of_date,
        baseline_path=baseline_path,
        host_baseline_path=host_baseline_path,
    )
    if json_output:
        typer.echo(payload.model_dump_json(indent=2, exclude_none=False))
    else:
        typer.echo(render_org_status_markdown(payload), nl=False)
    if payload.violations:
        raise typer.Exit(ORG_GOVERNANCE_EXIT_CODE)


__all__ = ["ORG_GOVERNANCE_EXIT_CODE", "org_app", "org_status"]
