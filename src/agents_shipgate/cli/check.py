from __future__ import annotations

import sys
from pathlib import Path

import typer

from agents_shipgate.cli.agent_result import (
    agent_result_json,
    build_codex_agent_result,
)
from agents_shipgate.core.codex_boundary import DEFAULT_POLICY_PATH


def check(
    agent: str = typer.Option(
        "codex",
        "--agent",
        help="Agent runtime to check. Phase 1 supports only codex.",
    ),
    diff: str = typer.Option(
        "-",
        "--diff",
        help="Unified diff file to evaluate, or '-' to read stdin.",
    ),
    format_: str = typer.Option(
        "agent-json",
        "--format",
        help="Output format. Phase 1 supports only agent-json.",
    ),
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root containing the Codex-local surfaces.",
    ),
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Shipgate manifest path used for trigger context.",
    ),
    policy: Path = typer.Option(
        DEFAULT_POLICY_PATH,
        "--policy",
        help="Codex boundary policy file.",
    ),
) -> None:
    """Run the Codex-compatible local boundary check."""

    if agent != "codex":
        typer.echo("--agent must be 'codex' in Phase 1.", err=True)
        raise typer.Exit(2)
    if format_ != "agent-json":
        typer.echo("--format must be 'agent-json' in Phase 1.", err=True)
        raise typer.Exit(2)
    try:
        diff_text = sys.stdin.read() if diff == "-" else Path(diff).read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Could not read --diff input: {exc}", err=True)
        raise typer.Exit(2) from exc

    result = build_codex_agent_result(
        workspace=workspace,
        diff_text=diff_text,
        config=config,
        policy=policy,
    )
    typer.echo(agent_result_json(result))

