from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.agent_result import agent_result_json_payload, build_agent_boundary_result
from agents_shipgate.cli.capability import build_capability_lock_from_config
from agents_shipgate.cli.explain_finding import explain_finding_payload
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.bounded_io import (
    MAX_EXPLICIT_DIFF_BYTES,
    MAX_EXPLICIT_JSON_BYTES,
    ensure_bounded_utf8_text,
)
from agents_shipgate.core.capability_lock import (
    diff_capability_locks,
    load_capability_lock,
    render_capability_lock_diff_json,
    render_capability_lock_json,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.preflight import build_preflight_result
from agents_shipgate.schemas.agent_boundary import AGENT_BOUNDARY_RESULT_SCHEMA_VERSION
from agents_shipgate.schemas.agent_handoff import AGENT_HANDOFF_SCHEMA_VERSION
from agents_shipgate.schemas.preflight import PREFLIGHT_SCHEMA_VERSION, CapabilityRequestV1


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
    ``shipgate check --format agent-boundary-json``.
    """

    ensure_bounded_utf8_text(
        diff_text,
        max_bytes=MAX_EXPLICIT_DIFF_BYTES,
        label="diff_text",
    )
    result = build_agent_boundary_result(
        agent=agent,
        workspace=Path(workspace),
        diff_text=diff_text,
        config=Path(config),
        policy=Path(policy) if policy else None,
        input_mode="provided_diff",
        # MCP callers supply detached diff text, not a checkout/ref identity
        # that a later verify command can reproduce.
        verification_replayable=False,
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

    if diff_text is not None:
        ensure_bounded_utf8_text(
            diff_text,
            max_bytes=MAX_EXPLICIT_DIFF_BYTES,
            label="diff_text",
        )
    if len(changed_files or []) > 100_000:
        raise ConfigError("changed_files exceeds the 100000-entry static input limit")
    for label, payload in (
        ("plan", plan),
        ("capability_request", capability_request),
        ("base_preflight", base_preflight),
    ):
        if payload is not None:
            ensure_bounded_utf8_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                max_bytes=MAX_EXPLICIT_JSON_BYTES,
                label=label,
            )
    if plan is not None:
        mixed = [
            name
            for name, value in (
                ("changed_files", changed_files),
                ("diff_text", diff_text),
                ("capability_request", capability_request),
                ("base_preflight", base_preflight),
            )
            if value is not None
        ]
        if mixed:
            raise ConfigError(
                "plan cannot be combined with "
                + ", ".join(mixed)
                + "; put those inputs in the plan object or omit plan."
            )

    request = (
        CapabilityRequestV1.model_validate(capability_request)
        if capability_request is not None
        else None
    )
    result = build_preflight_result(
        workspace=Path(workspace),
        config=Path(config),
        changed_files=changed_files,
        diff_text=diff_text,
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


# The SDK range the ``[mcp]`` extra declares in ``pyproject.toml``. The server
# names it when the installed SDK lacks the API below, and a test holds the two
# spellings equal.
MCP_SDK_REQUIREMENT = "mcp>=2.0.0,<3"


def _installed_mcp_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"mcp {version('mcp')}"
    except PackageNotFoundError:
        return "version unknown"


def _load_mcp_sdk() -> tuple[Any, Any]:
    """Import the SDK 2.x server API, saying which of two problems stops it.

    A missing SDK is cured by installing the extra. An installed SDK without
    ``mcp.server.mcpserver`` is not: SDK 2.0 renamed ``FastMCP`` to
    ``MCPServer`` and left ``mcp.server.fastmcp`` as a module that raises, and
    an environment holding the SDK at 1.x has only the old name. Telling that
    user to install the extra again repeats a step that cannot help (#713).
    """

    try:
        import mcp  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without extra.
        raise ConfigError(
            "The MCP server requires the optional [mcp] extra. Install it "
            'with: pip install "agents-shipgate[mcp]"'
        ) from exc
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.types import ToolAnnotations
    except ImportError as exc:
        raise ConfigError(
            f"The installed MCP Python SDK ({_installed_mcp_version()}) has no "
            "mcp.server.mcpserver.MCPServer, which this server is built on. "
            f"The [mcp] extra requires {MCP_SDK_REQUIREMENT}. Upgrade with: "
            'pip install --upgrade "agents-shipgate[mcp]", and if the SDK stays '
            "at an older major version, find the requirement in this "
            "environment that pins it."
        ) from exc
    return MCPServer, ToolAnnotations


# What an agent host shows a model when it chooses among these tools: the task
# first, then the inputs the handler accepts, what it returns and what it cannot
# do. Without them ``tools/list`` advertised five bare names (#800). Each one
# restates its handler above and docs/mcp-server.md; none may promise a Git
# comparison, a scan or verify run, or an authority the tool does not have.
_TOOL_DESCRIPTIONS = {
    "shipgate.check": (
        "Check a coding agent's change against the agent-boundary policy for "
        "host configuration and other boundary files (for example "
        "`.codex/config.toml`, `.claude/settings.json`, `.mcp.json`), from a "
        "unified diff you already have. Pass the change as `diff_text`; "
        "optional `agent` (codex, claude-code or cursor; default codex), "
        "`workspace` (holding the tree before or after the diff), `config` and "
        "`policy`. The diff is detached: this tool runs no git and does not "
        "obtain a base or head ref, so it is not the CLI's Git-backed host "
        "comparison (`agents-shipgate diff --base <ref>`). Returns "
        f"`{AGENT_BOUNDARY_RESULT_SCHEMA_VERSION}`: decision, affected hosts, "
        "violations and advisory rows for only the host files the diff "
        "changes. Because the diff is bound to no checkout, every result stops "
        "for human routing (`control.state` `human_review_required`), "
        "authorizes no verify command and grants no permission, even when "
        "`decision` is `allow`. It runs no scan or verify and is not a release "
        "or merge verdict."
    ),
    "shipgate.preflight": (
        "Before editing, route a planned change: does it touch a protected "
        "release surface, need a human, need capability evidence, or need "
        "verify? Pass either `plan` (preferred: a `PreflightPlanV1` object "
        "with `changed_files`, `diff_text`, `capability_requests` and "
        "`host_permission_requests`) or the direct inputs `changed_files`, "
        "`diff_text`, `capability_request` "
        "and `base_preflight` (an earlier preflight result to compare policy "
        "and trust-root hashes against), never both: a mixed request is "
        "rejected. Optional `workspace` and `config`. Returns a preflight "
        f"result (`preflight_schema_version` `{PREFLIGHT_SCHEMA_VERSION}`): "
        "protected-surface touches, signals, required evidence, "
        "`requires_human_review`, `requires_verify` and a routing `control`. "
        "Projection only: it is not a release verdict, every permission in its "
        "`control` is false and its `control.state` is never `complete`; when "
        "verification is owed it names a CLI `verify` command but runs "
        "nothing. It runs no git, host comparison, scan or verify."
    ),
    "shipgate.explain": (
        "Explain an Agents Shipgate check, or one finding in an existing "
        "report. Pass `check_id` (for example `SHIP-POLICY-APPROVAL-MISSING`) "
        "for that check's catalog entry: what it checks, when it fires, its "
        "default severity and recommendation. Or pass `fingerprint` with "
        "`report_path` (a `report.json` that `scan` or `verify` already "
        "wrote) for that finding's fields plus a deterministic explanation; "
        "`fingerprint` requires `report_path` and takes precedence over "
        "`check_id`. `no_plugins` keeps third-party check plugins out even "
        "when AGENTS_SHIPGATE_ENABLE_PLUGINS is set. An unknown check ID or "
        "fingerprint is an error. Lookup only: it produces no findings and "
        "runs no scan, verify, git or host comparison."
    ),
    "shipgate.capabilities": (
        "Export or compare static capability locks: the tools a Shipgate "
        "manifest declares for an agent, with their typed actions. Pass "
        "`config` (manifest path, default shipgate.yaml) to build a lock from "
        "the manifest's declared tool sources, or both `base_lock` and "
        "`head_lock` (existing capability lock JSON files, as `verify` or "
        "`capability export` write them) to diff them; one lock alone is "
        "rejected, and a lock pair ignores `config`. `no_plugins` keeps "
        "third-party adapters out even when AGENTS_SHIPGATE_ENABLE_PLUGINS is "
        "set. Returns capability lock JSON or capability lock diff JSON; "
        "neither is a release gate. It writes no lock and runs no findings, "
        "scan or verify. The diff reads only the two files given: this tool "
        "runs no git and checks out no base ref, so it is not the CLI's "
        "Git-backed host comparison (`agents-shipgate diff --base <ref>`)."
    ),
    "shipgate.handoff": (
        "Read the compact agent handoff of a `verify` run that already "
        "happened. Pass `verifier_path` (default "
        "agents-shipgate-reports/verifier.json, which must exist); "
        "`report_path` and `verify_run_path` default to `report.json` and "
        "`verify-run.json` beside it and are read when present. Returns "
        f"`{AGENT_HANDOFF_SCHEMA_VERSION}`, the projection `verify` writes: "
        "release gate, control, blockers, remediation plan and "
        "reproducibility identities. It is not a new verdict and grants "
        "nothing the verifier did not; artifacts that disagree with each "
        "other are refused. It runs no scan, verify, git or host comparison, "
        "and does not check that the artifacts still match the working tree: "
        "they describe only the tree that run verified, so re-run "
        "`agents-shipgate verify` after any change."
    ),
}


def create_server():
    MCPServer, ToolAnnotations = _load_mcp_sdk()

    # Every tool is a deterministic, local, static projection — it does not
    # modify its environment (``readOnlyHint``) and never reaches an external
    # system: no git, no network, no tool execution, no outbound MCP
    # (``openWorldHint=False``). Advertise that in the machine-readable contract
    # the agent host reads, not just the prose ``instructions`` below.
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    server = MCPServer(
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

    @server.tool(
        name="shipgate.check",
        description=_TOOL_DESCRIPTIONS["shipgate.check"],
        annotations=read_only,
    )
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

    @server.tool(
        name="shipgate.preflight",
        description=_TOOL_DESCRIPTIONS["shipgate.preflight"],
        annotations=read_only,
    )
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

    @server.tool(
        name="shipgate.explain",
        description=_TOOL_DESCRIPTIONS["shipgate.explain"],
        annotations=read_only,
    )
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

    @server.tool(
        name="shipgate.capabilities",
        description=_TOOL_DESCRIPTIONS["shipgate.capabilities"],
        annotations=read_only,
    )
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

    @server.tool(
        name="shipgate.handoff",
        description=_TOOL_DESCRIPTIONS["shipgate.handoff"],
        annotations=read_only,
    )
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
