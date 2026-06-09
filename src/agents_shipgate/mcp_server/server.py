"""Tool functions and (optional) FastMCP wiring for the Shipgate verifier.

The three tool functions are pure wrappers over existing orchestrators
and are unit-tested without the MCP SDK. ``build_server`` /
``serve_stdio`` import the SDK lazily so the core package keeps zero new
required dependencies; install with ``pip install "agents-shipgate[mcp]"``.

Trust model: identical to the CLI. Static-by-default, local-only — the
server speaks stdio to the host process, never the network. The verify
path reuses the same audited local-git calls the CLI uses, and every
verdict field returned here is a projection of
``report.json.release_decision.decision``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError

DEFAULT_CONFIG = "shipgate.yaml"
DEFAULT_REPORT_PATH = "agents-shipgate-reports/report.json"

_VERDICT_FIELDS = (
    "merge_verdict",
    "can_merge_without_human",
    "first_next_action",
    "fix_task",
    "capability_review",
    "release_decision",
    "human_review",
    "notes",
)


def _verifier_projection(verifier: Any) -> dict[str, Any]:
    """Project a VerifierArtifact onto the agent read-order fields.

    Field order follows the documented read order in
    docs/agent-contract-current.md: lead with ``merge_verdict``, then the
    routing fields, then the underlying release decision.
    """
    payload = verifier.model_dump(mode="json")
    projected = {key: payload.get(key) for key in _VERDICT_FIELDS if key in payload}
    # Always carry the verdict even if a future schema renames siblings.
    projected.setdefault("merge_verdict", payload.get("merge_verdict", "unknown"))
    return projected


def _error_payload(exc: Exception, *, kind: str) -> dict[str, Any]:
    return {
        "merge_verdict": "unknown",
        "error": kind,
        "message": str(exc),
        "next_action": (
            "Fix the reported issue, then call the tool again. The verifier "
            "never fetches; for committed refs make the base ref available "
            "first (e.g. `git fetch origin main`)."
        ),
    }


def preview_tool(
    workspace: str = ".",
    config: str = DEFAULT_CONFIG,
    base: str | None = None,
    head: str | None = None,
) -> dict[str, Any]:
    """Lightweight relevance check: should Shipgate gate this repo/diff?

    Mirrors ``agents-shipgate verify --preview --json``. Writes the
    standard preview artifacts into the workspace's reports directory and
    returns the verifier projection.
    """
    from agents_shipgate.cli.verify.orchestrator import run_preview

    try:
        verifier, _report, _exit_code = run_preview(
            workspace=Path(workspace),
            config=Path(config),
            base=base or None,
            head=head or None,
            out=None,
        )
    except (ConfigError, InputParseError, AgentsShipgateError) as exc:
        return _error_payload(exc, kind=type(exc).__name__)
    return _verifier_projection(verifier)


def verify_tool(
    workspace: str = ".",
    config: str = DEFAULT_CONFIG,
    base: str | None = None,
    head: str | None = None,
    ci_mode: str = "advisory",
) -> dict[str, Any]:
    """Run the deterministic verifier and return the merge verdict.

    Mirrors ``agents-shipgate verify --format json``. Omit ``base`` /
    ``head`` to verify local uncommitted work; pass both for a committed
    PR diff (the verifier never fetches). Artifacts (``verifier.json``,
    ``report.json``, ``pr-comment.md``) are written to the workspace's
    reports directory exactly as the CLI writes them.
    """
    from agents_shipgate.cli.verify.orchestrator import run_verify

    try:
        verifier, _report, _exit_code = run_verify(
            workspace=Path(workspace),
            config=Path(config),
            base=base or None,
            head=head or "",
            archive_head=bool(head),
            out=None,
            ci_mode=ci_mode,
            fail_on=None,
            baseline=None,
            baseline_mode="new-findings",
            diff_from=None,
            policy_packs=None,
            plugins_enabled=None,
            strict_plugins=False,
            suggest_patches=False,
            no_heuristics=False,
            verbose=False,
            pr_comment_style="capability-review",
        )
    except (ConfigError, InputParseError, AgentsShipgateError) as exc:
        return _error_payload(exc, kind=type(exc).__name__)
    return _verifier_projection(verifier)


def explain_finding_tool(
    fingerprint: str,
    report_path: str = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    """Explain one finding from an existing report by fingerprint.

    Mirrors ``agents-shipgate explain-finding --json``: check metadata,
    evidence, remediation, and the autofix boundary for the finding.
    """
    from agents_shipgate.cli.explain_finding import (
        FingerprintNotFound,
        explain_finding_payload,
    )

    try:
        return explain_finding_payload(
            fingerprint=fingerprint,
            report_path=Path(report_path),
        )
    except (
        ConfigError,
        InputParseError,
        AgentsShipgateError,
        FingerprintNotFound,
    ) as exc:
        return _error_payload(exc, kind=type(exc).__name__)


def build_server() -> Any:
    """Build the FastMCP server (requires the ``[mcp]`` extra)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised via CLI error path
        raise ConfigError(
            "The MCP server requires the optional [mcp] extra. Install it "
            'with: pip install "agents-shipgate[mcp]"'
        ) from exc

    server = FastMCP(
        "agents-shipgate",
        instructions=(
            "Deterministic merge gate for AI-generated agent capability "
            "changes. Call shipgate_preview first to learn whether Shipgate "
            "applies to the current diff; call shipgate_verify for the merge "
            "verdict (read merge_verdict, can_merge_without_human, "
            "first_next_action, fix_task); call shipgate_explain_finding to "
            "understand one finding before repairing it. Never weaken the "
            "manifest, policies, CI gate, or agent instructions to make a "
            "verdict pass."
        ),
    )

    @server.tool(name="shipgate_preview")
    def shipgate_preview(
        workspace: str = ".",
        config: str = DEFAULT_CONFIG,
        base: str | None = None,
        head: str | None = None,
    ) -> str:
        """Check whether Shipgate applies to this repo or diff (read-mostly preflight)."""
        return json.dumps(preview_tool(workspace, config, base, head), indent=2)

    @server.tool(name="shipgate_verify")
    def shipgate_verify(
        workspace: str = ".",
        config: str = DEFAULT_CONFIG,
        base: str | None = None,
        head: str | None = None,
        ci_mode: str = "advisory",
    ) -> str:
        """Run the deterministic verifier; returns merge_verdict and routing fields."""
        return json.dumps(
            verify_tool(workspace, config, base, head, ci_mode), indent=2
        )

    @server.tool(name="shipgate_explain_finding")
    def shipgate_explain_finding(
        fingerprint: str,
        report_path: str = DEFAULT_REPORT_PATH,
    ) -> str:
        """Explain one finding (by fingerprint) from an existing report.json."""
        return json.dumps(explain_finding_tool(fingerprint, report_path), indent=2)

    return server


def serve_stdio() -> None:
    """Run the MCP server over stdio (blocking)."""
    build_server().run(transport="stdio")
