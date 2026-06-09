"""Local MCP server exposing the Shipgate verifier to coding agents.

Optional surface behind the ``[mcp]`` extra. Everything here is a thin,
deterministic projection over the existing orchestrators — the server
introduces no second decision engine and no network access (stdio
transport only; the underlying verify path keeps its audited local-git
boundary). Agents without shell access can ask "may this diff merge?"
in-loop instead of shelling out to the CLI.
"""

from agents_shipgate.mcp_server.server import (
    build_server,
    explain_finding_tool,
    preview_tool,
    serve_stdio,
    verify_tool,
)

__all__ = [
    "build_server",
    "explain_finding_tool",
    "preview_tool",
    "serve_stdio",
    "verify_tool",
]
