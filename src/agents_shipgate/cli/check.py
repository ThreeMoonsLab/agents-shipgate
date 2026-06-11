from __future__ import annotations

import sys
from pathlib import Path

import typer

from agents_shipgate.cli.agent_result import (
    agent_result_json,
    build_codex_agent_result,
    git_diff_text,
)


def check(
    agent: str = typer.Option(
        "codex",
        "--agent",
        help="Agent runtime to check: codex, claude-code, or cursor.",
    ),
    diff: str | None = typer.Option(
        None,
        "--diff",
        help=(
            "Unified diff file to evaluate, or '-' to read stdin. The workspace "
            "may contain either the base tree or the already-applied head tree; "
            "mismatched content fails closed."
        ),
    ),
    format_: str = typer.Option(
        "agent-json",
        "--format",
        help="Output format. Supports agent-json.",
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
    policy: Path | None = typer.Option(
        None,
        "--policy",
        help="Optional Codex boundary policy file. Defaults to workspace policy then packaged default.",
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help="Base git ref for diff resolution when --diff is omitted.",
    ),
    head: str | None = typer.Option(
        None,
        "--head",
        help="Head git ref for diff resolution when --diff is omitted.",
    ),
) -> None:
    """Run the agent-native local boundary check."""

    if agent not in {"codex", "claude-code", "cursor"}:
        typer.echo("--agent must be one of: codex, claude-code, cursor.", err=True)
        raise typer.Exit(2)
    if format_ != "agent-json":
        typer.echo("--format must be 'agent-json'.", err=True)
        raise typer.Exit(2)
    try:
        if diff == "-":
            diff_text = sys.stdin.read()
        elif diff:
            diff_text = Path(diff).read_text(encoding="utf-8")
        else:
            diff_text = git_diff_text(workspace=workspace, base=base, head=head)
    except (OSError, RuntimeError) as exc:
        typer.echo(f"Could not read --diff input: {exc}", err=True)
        raise typer.Exit(2) from exc

    result = build_codex_agent_result(
        agent=agent,
        workspace=workspace,
        diff_text=diff_text,
        config=config,
        policy=policy,
    )
    typer.echo(agent_result_json(result))
