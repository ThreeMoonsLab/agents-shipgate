from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_shipgate.cli.agent_result import agent_result_json_payload, build_codex_agent_result


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
        raise RuntimeError(
            "The optional MCP server requires `agents-shipgate[mcp]`."
        ) from exc

    server = FastMCP("agents-shipgate")

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


def main() -> None:
    create_server().run()


__all__ = ["create_server", "main", "shipgate_check"]
