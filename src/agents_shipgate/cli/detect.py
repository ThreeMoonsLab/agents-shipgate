"""``shipgate detect`` — classify a workspace as an agent project.

Read-only. Walks the workspace, scores per-framework signals, and emits a
:class:`agents_shipgate.cli.discovery.signals.DetectResult` payload. Useful
for AI coding agents deciding whether to run ``init`` next; also exposed as
a library function so ``init`` Pass B can reuse the detection results.

Negative case (``is_agent_project=false``) is informational, not an error
— exit code 0 with payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from agents_shipgate.cli.discovery import detect_workspace


def detect(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace to inspect.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON. Default: human-readable summary.",
    ),
    max_python_files: int = typer.Option(
        1000,
        "--max-python-files",
        help="Cap on .py files to AST-parse. Defends against large monorepos.",
        hidden=True,
    ),
) -> None:
    """Classify a workspace: which agent framework(s), if any."""
    result = detect_workspace(workspace.resolve(), max_python_files=max_python_files)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
        return

    if not result.is_agent_project:
        typer.echo("Workspace does not appear to be an agent project.")
        typer.echo("No agent framework signals matched the strong-signal threshold.")
        return

    typer.echo("Detected agent project.")
    typer.echo("")
    typer.echo("Frameworks:")
    for framework in result.frameworks:
        typer.echo(
            f"- {framework.type} (score={framework.score}, "
            f"confidence={framework.confidence})"
        )
        for line in framework.evidence[:5]:
            typer.echo(f"    · {line}")
        if len(framework.evidence) > 5:
            typer.echo(f"    · ... ({len(framework.evidence) - 5} more)")
    typer.echo("")
    if result.agent_name_candidates:
        primary = result.agent_name_candidates[0]
        typer.echo(f"Agent name candidate: {primary.value} (source: {primary.source})")
    if result.project_name_candidates:
        primary = result.project_name_candidates[0]
        typer.echo(f"Project name candidate: {primary.value} (source: {primary.source})")
    if result.suggested_sources:
        typer.echo("")
        typer.echo("Suggested tool sources:")
        for source in result.suggested_sources:
            typer.echo(f"- {source['type']}: {source['path']}")
    typer.echo("")
    typer.echo(f"Next: {result.next_action}")
