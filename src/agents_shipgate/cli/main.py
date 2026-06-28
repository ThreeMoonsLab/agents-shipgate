from __future__ import annotations

import logging
from pathlib import Path

import typer

from agents_shipgate import __version__
from agents_shipgate.cli import (
    _register_baseline,
    _register_contract,
    _register_doctor,
    _register_explain,
    _register_init,
    _register_list_checks,
    _register_scan,
)
from agents_shipgate.cli.agent_interface import agent_app
from agents_shipgate.cli.apply_patches import apply_patches as _apply_patches_command
from agents_shipgate.cli.attest import _attest_command
from agents_shipgate.cli.bootstrap import bootstrap as _bootstrap_command
from agents_shipgate.cli.capability import capability_app
from agents_shipgate.cli.check import check as _check_command
from agents_shipgate.cli.detect import detect as _detect_command
from agents_shipgate.cli.evidence_packet import evidence_packet as _evidence_packet_command
from agents_shipgate.cli.explain_finding import explain_finding as _explain_finding_command
from agents_shipgate.cli.feedback import feedback_app
from agents_shipgate.cli.findings import findings as _findings_command
from agents_shipgate.cli.fixture import fixture_app
from agents_shipgate.cli.host_audit import audit as _audit_command
from agents_shipgate.cli.install_hooks import install_hooks as _install_hooks_command
from agents_shipgate.cli.mcp import mcp_app
from agents_shipgate.cli.org import org_app
from agents_shipgate.cli.preflight import preflight as _preflight_command
from agents_shipgate.cli.registry import registry_app
from agents_shipgate.cli.scenario import scenario_app
from agents_shipgate.cli.self_check import self_check
from agents_shipgate.cli.skill import skill_app
from agents_shipgate.cli.trigger import trigger as _trigger_command
from agents_shipgate.cli.verify import verify as _verify_command
from agents_shipgate.core.logging import configure_logging

app = typer.Typer(
    name="agents-shipgate",
    help="The deterministic merge gate for AI-generated agent capability changes.",
    # Bare `shipgate` runs a zero-config first look (see the root callback),
    # not a help dump — so off, not True.
    no_args_is_help=False,
    invoke_without_command=True,
)
app.command(
    "self-check",
    hidden=True,
    help="Verify install and bundled fixtures. Run this first in a fresh environment.",
)(self_check)
app.command(
    "detect",
    hidden=True,
    help="Classify a workspace: which agent framework(s), if any. Read-only.",
)(_detect_command)
app.command(
    "check",
    help="Run the local Codex boundary check and emit codex boundary JSON.",
)(_check_command)
app.command(
    "preflight",
    hidden=True,
    help=(
        "Run proactive static preflight: protected surfaces, forbidden edits, "
        "and high-risk capability evidence requirements."
    ),
)(_preflight_command)
app.command(
    "apply-patches",
    hidden=True,
    help=(
        "Apply patches from a scan JSON report. Dry-run by default; pass "
        "--apply to mutate. Containment-checked against the report's "
        "manifest_dir."
    ),
)(_apply_patches_command)
app.command(
    "evidence-packet",
    # Hidden from --help (niche re-render utility), still fully invokable —
    # see the visibility policy note above the sub-app block below.
    hidden=True,
    help=(
        "Re-render a Release Evidence Packet from an existing packet.json "
        "into md, html, and/or pdf."
    ),
)(_evidence_packet_command)
app.command(
    "bootstrap",
    hidden=True,
    help=(
        "Run the canonical 4-call adoption flow in one command: "
        "detect → init --write --ci → scan --suggest-patches → "
        "apply-patches --confidence high."
    ),
)(_bootstrap_command)
app.command(
    "explain-finding",
    hidden=True,
    help=(
        "Explain a specific finding from a `report.json`, with evidence "
        "and a 3–5 sentence prose summary. Companion to `explain "
        "<check-id>`."
    ),
)(_explain_finding_command)
app.command(
    "findings",
    hidden=True,
    help=(
        "Filter findings from a `report.json` by provenance kind for "
        "reviewer triage."
    ),
)(_findings_command)
app.command(
    "trigger",
    hidden=True,
    help=(
        "Evaluate the trigger catalog against a diff and emit a run/skip "
        "verdict. Reads --changed-files / --diff, or --base/--head (git)."
    ),
)(_trigger_command)
app.command(
    "verify",
    help=(
        "Run the canonical ongoing-PR verifier: trigger evaluation, optional "
        "base scan, and one authoritative head scan."
    ),
)(_verify_command)
app.command(
    "attest",
    hidden=True,
    help=(
        "Derive a deterministic local release attestation from verifier.json "
        "(verdict, capability delta, human-ack state, policy + artifact hashes)."
    ),
)(_attest_command)
app.command(
    "install-hooks",
    hidden=True,
    help=(
        "Install advisory local coding-agent hooks. Currently supports "
        "--target claude-code."
    ),
)(_install_hooks_command)

app.command(
    "audit",
    help=(
        "Run `shipgate audit --host` for zero-config host-grant audits. "
        "`audit --host` inventories "
        "coding-agent host grants (MCP servers, permission rules, hooks, "
        "workflow scopes) without requiring shipgate.yaml; `--save-baseline` "
        "records the acknowledged state (writes one JSON file under "
        ".agents-shipgate/) and `--drift [--fail-on-drift]` reports when "
        "current grants no longer match it."
    ),
)(_audit_command)


@app.command("mcp-serve", hidden=True)
def _mcp_serve_command() -> None:
    """Serve the optional read-only MCP server over stdio.

    Requires the optional [mcp] extra: pip install "agents-shipgate[mcp]".
    Exposes static projection tools only; it never starts implicitly and does
    not connect to external MCP servers.
    """
    from agents_shipgate.core.errors import ConfigError as _ConfigError
    from agents_shipgate.mcp_server import serve_stdio

    try:
        serve_stdio()
    except _ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(2) from exc
_register_scan.register(app)
_register_list_checks.register(app)
_register_contract.register(app)
_register_explain.register(app)
_register_init.register(app)
_register_doctor.register(app)
_register_baseline.register(app)
# Visibility policy: root --help shows only the prominent flows:
# `shipgate check`, `shipgate verify`, and `shipgate audit --host`.
# Supporting/compatibility commands stay fully invokable and documented
# through their direct --help; hiding is presentation, not deprecation.
app.add_typer(fixture_app, name="fixture", hidden=True)
app.add_typer(feedback_app, name="feedback", hidden=True)
app.add_typer(scenario_app, name="scenario", hidden=True)
app.add_typer(skill_app, name="skill", hidden=True)
app.add_typer(capability_app, name="capability", hidden=True)
app.add_typer(agent_app, name="agent", hidden=True)
app.add_typer(mcp_app, name="mcp", hidden=True)
app.add_typer(org_app, name="org", hidden=True)
app.add_typer(registry_app, name="registry", hidden=True)
logger = logging.getLogger(__name__)


@app.callback()
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    # Logging state is per-invocation, not per-process: reset to the
    # default (WARNING, plain formatter) before every command so a
    # previous in-process invocation's --verbose / JSON-format handler
    # cannot leak into this one's output. Commands that accept --verbose
    # re-configure inside their body, which runs after this callback.
    # (Observed: a verbose scan on a shared pytest-xdist worker left a
    # DEBUG JsonFormatter handler that polluted self-check's JSON stdout.)
    configure_logging(force=True)
    if version:
        typer.echo(f"Agents Shipgate {__version__}")
        raise typer.Exit(0)
    # Bare `shipgate` (no subcommand) runs a zero-config, read-only first
    # look instead of dumping --help, so a fresh repo gets a verdict and a
    # next step without a manifest. Named subcommands and `--help` are
    # unaffected: this branch only fires when no subcommand was matched.
    if ctx.invoked_subcommand is None:
        from agents_shipgate.cli.first_look import run_first_look

        run_first_look(Path("."))
        raise typer.Exit(0)
