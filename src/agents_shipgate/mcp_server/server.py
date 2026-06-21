from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.agent_result import agent_result_json_payload, build_codex_agent_result
from agents_shipgate.cli.capability import build_capability_lock_from_config
from agents_shipgate.cli.explain_finding import explain_finding_payload
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.capability_lock import (
    diff_capability_locks,
    load_capability_lock,
    render_capability_lock_diff_json,
    render_capability_lock_json,
)
from agents_shipgate.core.codex_boundary import parse_unified_diff
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.schemas.preflight import CapabilityRequestV1


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
    ``shipgate check --format codex-boundary-json``.
    """

    result = build_codex_agent_result(
        agent=agent,
        workspace=Path(workspace),
        diff_text=diff_text,
        config=Path(config),
        policy=Path(policy) if policy else None,
    )
    return agent_result_json_payload(result)


def shipgate_preflight(
    *,
    workspace: str = ".",
    config: str = "shipgate.yaml",
    changed_files: list[str] | None = None,
    diff_text: str | None = None,
    capability_request: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    base_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only MCP tool implementation for ``shipgate.preflight``."""

    changed = list(changed_files or [])
    if diff_text:
        changed = sorted(
            {
                *changed,
                *(item.path for item in parse_unified_diff(diff_text) if item.path),
            }
        )
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
        plan=plan,
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
    """Read-only MCP tool implementation for deterministic explanations."""

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
    """Read-only MCP tool implementation for capability lock export or diff."""

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


def shipgate_handoff(
    *,
    verifier_path: str = "agents-shipgate-reports/verifier.json",
    report_path: str | None = None,
    verify_run_path: str | None = None,
) -> dict[str, Any]:
    """Read-only MCP tool implementation for agent handoff projection."""

    verifier = _load_json_object(Path(verifier_path), "verifier.json")
    base_dir = Path(verifier_path).parent
    report = _load_optional_json_object(
        Path(report_path) if report_path else base_dir / "report.json",
    )
    verify_run = _load_optional_json_object(
        Path(verify_run_path) if verify_run_path else base_dir / "verify-run.json",
    )
    return build_agent_handoff(
        verifier=verifier,
        report=report,
        verify_run=verify_run,
    ).model_dump(mode="json")


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
            "Read-only static adapter for Agents Shipgate. Exposes only "
            "deterministic projection tools: shipgate.check, "
            "shipgate.preflight, shipgate.explain, shipgate.capabilities, "
            "and shipgate.handoff. The server never starts implicitly, shells "
            "out to git, writes artifacts, calls tools, or accesses the "
            "network."
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

    @server.tool(name="shipgate.preflight")
    def _shipgate_preflight(
        workspace: str = ".",
        config: str = "shipgate.yaml",
        changed_files: list[str] | None = None,
        diff_text: str | None = None,
        capability_request: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        base_preflight: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return shipgate_preflight(
            workspace=workspace,
            config=config,
            changed_files=changed_files,
            diff_text=diff_text,
            capability_request=capability_request,
            plan=plan,
            base_preflight=base_preflight,
        )

    @server.tool(name="shipgate.explain")
    def _shipgate_explain(
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

    @server.tool(name="shipgate.capabilities")
    def _shipgate_capabilities(
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

    @server.tool(name="shipgate.handoff")
    def _shipgate_handoff(
        verifier_path: str = "agents-shipgate-reports/verifier.json",
        report_path: str | None = None,
        verify_run_path: str | None = None,
    ) -> dict[str, Any]:
        return shipgate_handoff(
            verifier_path=verifier_path,
            report_path=report_path,
            verify_run_path=verify_run_path,
        )

    return server


build_server = create_server


def serve_stdio() -> None:
    create_server().run(transport="stdio")


def main() -> None:
    serve_stdio()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain an object: {path}")
    return payload


def _load_optional_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _load_json_object(path, path.name)


__all__ = [
    "build_server",
    "create_server",
    "main",
    "serve_stdio",
    "shipgate_capabilities",
    "shipgate_check",
    "shipgate_explain",
    "shipgate_handoff",
    "shipgate_preflight",
]
