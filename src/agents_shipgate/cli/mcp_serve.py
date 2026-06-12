from __future__ import annotations

import typer

from agents_shipgate.mcp_server import run_mcp_server


def mcp_serve() -> None:
    """Start the optional read-only MCP server."""

    try:
        run_mcp_server()
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


__all__ = ["mcp_serve"]
