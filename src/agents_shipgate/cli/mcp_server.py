"""Optional MCP stdio server — a thin wrapper over the existing engine.

Exposes three tools (``shipgate_verify``, ``shipgate_explain``,
``shipgate_status``) for coding agents without comfortable shell access
(Cursor, restricted harnesses). Claude Code users should prefer the CLI +
hooks surface; this server adds no semantics — every tool is a direct
projection of the same engine the CLI calls, and the release gate stays
``report.json.release_decision.decision``.

The ``mcp`` dependency is an optional extra (``pip install
'agents-shipgate[mcp]'``) and is imported lazily inside :func:`serve` so the
core CLI never depends on it. Tool handlers are plain sync functions so they
are testable without the SDK installed.

Provisional surface: the tool names and argument shapes may change in a
minor release until promoted into STABILITY.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError

SERVER_NAME = "agents-shipgate"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "shipgate_verify",
        "description": (
            "Run the deterministic merge gate on this repo's agent capability "
            "changes. Call after modifying MCP servers, tool definitions, "
            "agent permissions, approval policies, or agent CI — and before "
            "creating a PR for such a change. Returns the compact agent "
            "result: merge_verdict, can_merge_without_human, suggested fixes, "
            "and repair instructions. Full artifacts land in "
            "agents-shipgate-reports/."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace path (default: current directory).",
                },
                "base": {
                    "type": "string",
                    "description": (
                        "Local base ref for the PR diff. Omit to auto-detect "
                        "the default branch; never fetched."
                    ),
                },
                "head": {
                    "type": "string",
                    "description": (
                        "Local head ref to scan from an isolated archive. "
                        "Omit to scan the working tree."
                    ),
                },
            },
        },
    },
    {
        "name": "shipgate_explain",
        "description": (
            "Explain a Shipgate check id (e.g. SHIP-POLICY-APPROVAL-MISSING) "
            "or a finding fingerprint (fp_...) with evidence, rationale, and "
            "the required fix."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Check id or finding fingerprint (fp_...).",
                },
                "workspace": {
                    "type": "string",
                    "description": (
                        "Workspace containing agents-shipgate-reports/ "
                        "(used for fingerprint lookups)."
                    ),
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "shipgate_status",
        "description": (
            "Report the last verify verdict for this repo: merge_verdict, "
            "can_merge_without_human, and whether human review is pending. "
            "Reads the existing agents-shipgate-reports/ artifacts without "
            "rescanning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace path (default: current directory).",
                },
            },
        },
    },
]


def dispatch(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Route one MCP tool call to the engine. Errors come back as payloads
    (never exceptions) so agent loops always get machine-readable output."""
    args = arguments or {}
    try:
        if name == "shipgate_verify":
            return _tool_verify(args)
        if name == "shipgate_explain":
            return _tool_explain(args)
        if name == "shipgate_status":
            return _tool_status(args)
    except ConfigError as exc:
        return {
            "error": "config_error",
            "message": str(exc),
            "next_action": "agents-shipgate init --workspace . --write",
        }
    except InputParseError as exc:
        return {"error": "input_parse_error", "message": str(exc)}
    except AgentsShipgateError as exc:
        return {"error": "other_error", "message": str(exc)}
    return {
        "error": "unknown_tool",
        "message": f"Unknown tool {name!r}.",
        "known_tools": [tool["name"] for tool in TOOLS],
    }


def _tool_verify(args: dict[str, Any]) -> dict[str, Any]:
    from agents_shipgate.ci.agent_result import build_agent_result
    from agents_shipgate.cli.verify.orchestrator import run_verify

    base = args.get("base") or None
    head = args.get("head") or None
    verifier, report, _exit_code = run_verify(
        workspace=Path(str(args.get("workspace") or ".")),
        config=Path(str(args.get("config") or "shipgate.yaml")),
        base=base,
        head=head or "HEAD",
        archive_head=head is not None,
        out=None,
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=None,
        strict_plugins=False,
        suggest_patches=True,
        no_heuristics=False,
        verbose=False,
        auto_base=base is None,
    )
    result = build_agent_result(verifier=verifier, report=report)
    return result.model_dump(mode="json")


def _tool_explain(args: dict[str, Any]) -> dict[str, Any]:
    identifier = str(args.get("id") or "").strip()
    if not identifier:
        return {"error": "config_error", "message": "Argument 'id' is required."}
    workspace = Path(str(args.get("workspace") or "."))
    if identifier.startswith("fp_"):
        from agents_shipgate.cli.explain_finding import (
            FingerprintNotFound,
            explain_finding_payload,
        )

        report_path = workspace / "agents-shipgate-reports" / "report.json"
        try:
            return explain_finding_payload(
                fingerprint=identifier, report_path=report_path
            )
        except FingerprintNotFound as exc:
            return {
                "error": "unknown_fingerprint",
                "message": str(exc),
                "suggestion": getattr(exc, "suggestion", None),
            }
        except ValueError as exc:
            return {
                "error": "input_parse_error",
                "message": str(exc),
                "next_action": "Run shipgate_verify first to produce report.json.",
            }

    from agents_shipgate.checks.registry import check_catalog

    checks = check_catalog(plugins_enabled=None)
    check = next((item for item in checks if item.id == identifier), None)
    if check is None:
        from difflib import get_close_matches

        matches = get_close_matches(identifier, [item.id for item in checks], n=1)
        return {
            "error": "unknown_check_id",
            "message": f"Unknown check id: {identifier}",
            "suggestion": matches[0] if matches else None,
        }
    return check.model_dump(mode="json")


def _tool_status(args: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(str(args.get("workspace") or "."))
    out_dir = workspace / "agents-shipgate-reports"
    agent_result_path = out_dir / "agent-result.json"
    if agent_result_path.is_file():
        try:
            payload = json.loads(agent_result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {
                "error": "input_parse_error",
                "message": f"Could not read {agent_result_path}: {exc}",
                "next_action": "Run shipgate_verify to regenerate the artifacts.",
            }
        if isinstance(payload, dict):
            payload.setdefault("status", "verified")
            return payload
    return {
        "status": "no_verify_run",
        "message": (
            "No verify artifacts found under agents-shipgate-reports/. "
            "Run shipgate_verify (or `agents-shipgate verify --json`) first."
        ),
    }


def serve() -> None:
    """Run the stdio MCP server. Requires the optional ``mcp`` extra."""
    try:
        import anyio
        import mcp.types as types
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise ConfigError(
            "The MCP server requires the optional 'mcp' extra: "
            "pip install 'agents-shipgate[mcp]'"
        ) from exc

    server: Any = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[Any]:
        return [
            types.Tool(
                name=tool["name"],
                description=tool["description"],
                inputSchema=tool["inputSchema"],
            )
            for tool in TOOLS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        payload = await anyio.to_thread.run_sync(dispatch, name, arguments)
        return [
            types.TextContent(
                type="text",
                text=json.dumps(payload, indent=2, sort_keys=True),
            )
        ]

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    anyio.run(_run)


def mcp_serve() -> None:
    """Serve agents-shipgate over MCP stdio (provisional; requires [mcp] extra)."""
    try:
        serve()
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc


__all__ = ["TOOLS", "dispatch", "mcp_serve", "serve"]
