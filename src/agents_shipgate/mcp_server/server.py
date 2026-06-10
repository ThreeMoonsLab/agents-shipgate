from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_shipgate.cli.agent_result import agent_result_json_payload, build_codex_agent_result
from agents_shipgate.core.errors import ConfigError


def shipgate_check(
    *,
    agent: str = "codex",
    workspace: str = ".",
    diff_text: str,
    config: str = "shipgate.yaml",
    policy: str | None = None,
) -> dict[str, Any]:
    """Read-only MCP tool implementation for ``shipgate.check``.

    This function intentionally accepts diff text from the caller and does not
    shell out to git, write reports, apply patches, call tools, or touch the
    network. It is an adapter over the same local static evaluator used by
    ``shipgate check --format agent-json``.
    """

    result = build_codex_agent_result(
        agent=agent,
        workspace=Path(workspace),
        diff_text=diff_text,
        config=Path(config),
        policy=Path(policy) if policy else None,
    )
    return agent_result_json_payload(result)


def create_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without extra.
        raise ConfigError(
            "The MCP server requires the optional [mcp] extra. Install it "
            'with: pip install "agents-shipgate[mcp]"'
        ) from exc

    server = FastMCP(
        "agents-shipgate",
        instructions=(
            "Read-only static adapter for Agents Shipgate agent_result_v1. "
            "Only shipgate.check is exposed. The tool accepts caller-provided "
            "diff text and never shells out to git, writes artifacts, calls "
            "tools, or accesses the network."
        ),
    )

    @server.tool(name="shipgate.check")
    def _shipgate_check(
        agent: str = "codex",
        workspace: str = ".",
        diff_text: str = "",
        config: str = "shipgate.yaml",
        policy: str | None = None,
    ) -> dict[str, Any]:
        return shipgate_check(
            agent=agent,
            workspace=workspace,
            diff_text=diff_text,
            config=config,
            policy=policy,
        )

    return server


build_server = create_server


def serve_stdio() -> None:
    create_server().run(transport="stdio")


def main() -> None:
    serve_stdio()


__all__ = ["build_server", "create_server", "main", "serve_stdio", "shipgate_check"]
