from __future__ import annotations

import logging

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
from agents_shipgate.cli.apply_patches import apply_patches as _apply_patches_command
from agents_shipgate.cli.bootstrap import bootstrap as _bootstrap_command
from agents_shipgate.cli.detect import detect as _detect_command
from agents_shipgate.cli.evidence_packet import evidence_packet as _evidence_packet_command
from agents_shipgate.cli.explain_finding import explain_finding as _explain_finding_command
from agents_shipgate.cli.fixture import fixture_app
from agents_shipgate.cli.scenario import scenario_app
from agents_shipgate.cli.self_check import self_check

app = typer.Typer(
    name="agents-shipgate",
    help="Local-first, static Tool-Use Readiness release gate for AI agent tool surfaces.",
    no_args_is_help=True,
    invoke_without_command=True,
)
app.command(
    "self-check",
    help="Verify install and bundled fixtures. Run this first in a fresh environment.",
)(self_check)
app.command(
    "detect",
    help="Classify a workspace: which agent framework(s), if any. Read-only.",
)(_detect_command)
app.command(
    "apply-patches",
    help=(
        "Apply patches from a scan JSON report. Dry-run by default; pass "
        "--apply to mutate. Containment-checked against the report's "
        "manifest_dir."
    ),
)(_apply_patches_command)
app.command(
    "evidence-packet",
    help=(
        "Re-render a Release Evidence Packet from an existing packet.json "
        "into md, html, and/or pdf."
    ),
)(_evidence_packet_command)
app.command(
    "bootstrap",
    help=(
        "Run the canonical 4-call adoption flow in one command: "
        "detect → init --write --ci → scan --suggest-patches → "
        "apply-patches --confidence high."
    ),
)(_bootstrap_command)
app.command(
    "explain-finding",
    help=(
        "Explain a specific finding from a `report.json`, with evidence "
        "and a 3–5 sentence prose summary. Companion to `explain "
        "<check-id>`."
    ),
)(_explain_finding_command)
_register_scan.register(app)
_register_list_checks.register(app)
_register_contract.register(app)
_register_explain.register(app)
_register_init.register(app)
_register_doctor.register(app)
_register_baseline.register(app)
app.add_typer(fixture_app, name="fixture")
app.add_typer(scenario_app, name="scenario")
logger = logging.getLogger(__name__)


@app.callback()
def _version(
    version: bool = typer.Option(False, "--version", help="Show version and exit.")
) -> None:
    if version:
        typer.echo(f"Agents Shipgate {__version__}")
        raise typer.Exit(0)
