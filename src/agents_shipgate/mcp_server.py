from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.capability import build_capability_lock_from_config
from agents_shipgate.cli.explain_finding import explain_finding_payload
from agents_shipgate.core.capability_lock import (
    diff_capability_locks,
    load_capability_lock,
    render_capability_lock_diff_json,
    render_capability_lock_json,
)
from agents_shipgate.core.codex_boundary import parse_unified_diff
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.schemas.preflight import CapabilityRequestV1


def shipgate_preflight(
    *,
    workspace: str = ".",
    config: str = "shipgate.yaml",
    changed_files: list[str] | None = None,
    diff_text: str | None = None,
    capability_request: dict[str, Any] | None = None,
    base_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only MCP handler for the preflight contract."""

    changed = list(changed_files or [])
    if diff_text:
        changed = sorted({*changed, *(item.path for item in parse_unified_diff(diff_text) if item.path)})
    request = (
        CapabilityRequestV1.model_validate(capability_request)
        if capability_request is not None
        else None
    )
    result = build_preflight_result(
        workspace=Path(workspace),
        config=Path(config),
        changed_files=changed,
        capability_request=request,
        base_preflight=base_preflight,
    )
    return result.model_dump(mode="json")


def shipgate_explain(
    *,
    check_id: str | None = None,
    fingerprint: str | None = None,
    report_path: str | None = None,
    no_plugins: bool = False,
) -> dict[str, Any]:
    """Read-only MCP handler for static check or contextual finding explanation."""

    if fingerprint:
        if not report_path:
            raise ValueError("report_path is required when fingerprint is supplied")
        return explain_finding_payload(
            fingerprint=fingerprint,
            report_path=Path(report_path),
            plugins_enabled=False if no_plugins else None,
        )
    if not check_id:
        raise ValueError("check_id or fingerprint is required")
    checks = check_catalog(plugins_enabled=False if no_plugins else None)
    match = next((item for item in checks if item.id == check_id), None)
    if match is None:
        raise ValueError(f"Unknown check id: {check_id}")
    return match.model_dump(mode="json")


def shipgate_capabilities(
    *,
    config: str = "shipgate.yaml",
    base_lock: str | None = None,
    head_lock: str | None = None,
    no_plugins: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Read-only MCP handler for capability lock export or lock diff."""

    if base_lock or head_lock:
        if not (base_lock and head_lock):
            raise ValueError("base_lock and head_lock must be supplied together")
        diff = diff_capability_locks(
            load_capability_lock(Path(base_lock)),
            load_capability_lock(Path(head_lock)),
            base_path=Path(base_lock),
            head_path=Path(head_lock),
        )
        return json.loads(render_capability_lock_diff_json(diff))

    lock = build_capability_lock_from_config(
        config=Path(config),
        no_plugins=no_plugins,
        verbose=verbose,
    )
    return json.loads(render_capability_lock_json(lock))


def run_mcp_server() -> None:
    """Start the optional read-only MCP server.

    The import is intentionally lazy so the core CLI does not depend on MCP
    packages unless this explicit command is used.
    """

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional package.
        raise RuntimeError(
            "MCP server support requires the optional `mcp` package. Install "
            "an environment that provides `mcp.server.fastmcp` before running "
            "`shipgate mcp-serve`."
        ) from exc

    mcp = FastMCP("agents-shipgate")

    @mcp.tool(name="shipgate.preflight")
    def _preflight_tool(
        workspace: str = ".",
        config: str = "shipgate.yaml",
        changed_files: list[str] | None = None,
        diff_text: str | None = None,
        capability_request: dict[str, Any] | None = None,
        base_preflight: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return shipgate_preflight(
            workspace=workspace,
            config=config,
            changed_files=changed_files,
            diff_text=diff_text,
            capability_request=capability_request,
            base_preflight=base_preflight,
        )

    @mcp.tool(name="shipgate.explain")
    def _explain_tool(
        check_id: str | None = None,
        fingerprint: str | None = None,
        report_path: str | None = None,
        no_plugins: bool = False,
    ) -> dict[str, Any]:
        return shipgate_explain(
            check_id=check_id,
            fingerprint=fingerprint,
            report_path=report_path,
            no_plugins=no_plugins,
        )

    @mcp.tool(name="shipgate.capabilities")
    def _capabilities_tool(
        config: str = "shipgate.yaml",
        base_lock: str | None = None,
        head_lock: str | None = None,
        no_plugins: bool = False,
    ) -> dict[str, Any]:
        return shipgate_capabilities(
            config=config,
            base_lock=base_lock,
            head_lock=head_lock,
            no_plugins=no_plugins,
        )

    mcp.run()


__all__ = [
    "run_mcp_server",
    "shipgate_capabilities",
    "shipgate_explain",
    "shipgate_preflight",
]
