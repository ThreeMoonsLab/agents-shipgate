from __future__ import annotations

import logging
from difflib import get_close_matches
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
import typer.core
import typer.main

from agents_shipgate import __version__
from agents_shipgate.core.logging import configure_logging

if TYPE_CHECKING:
    # Typer vendors Click; these are the classes its groups are typed with.
    from typer._click import Command, Context

# Root commands, in `--help` order, each imported only when it is resolved
# (#661). Importing every command module cost 0.67 s before any command ran,
# against 0.19 s for `diff` alone, and the Stop hook runs `diff` once per
# change with a 1.5 s budget. A row is (name, kind, options), and
# `_root_callable` imports what the row names: `command` registers the callable
# as ``app.command(name, **options)``, `group` adds the sub-app with
# ``app.add_typer(..., name=name, **options)``, and `register` calls the
# module's ``register`` on a scratch app and keeps the entry named ``name``.
#
# Visibility policy: root --help shows the six commands a reader needs to
# get from a fresh checkout to an answer — `diff`, `check`, `verify`,
# `audit`, `init`, `doctor`. Three of 53 was not a menu, it was a keyhole:
# every documented first step (`init`, `doctor`, `fixture run`) was hidden
# from the one place a stranger looks (#652). Supporting and compatibility
# commands stay fully invokable and documented through their own --help,
# and `--help-all` lists every one of them; hiding is presentation, not
# deprecation.
_ROOT_COMMANDS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("self-check", "command", {
        "hidden": True,
        "help": "Verify install and bundled fixtures. Run this first in a fresh environment.",
    }),
    ("detect", "command", {
        "hidden": True,
        "help": "Classify a workspace: which agent framework(s), if any. Read-only.",
    }),
    ("diff", "command", {
        "help": (
            "Show what this change does to the agent's authority: one row per "
            "host grant, with before, after and why it matters."
        ),
    }),
    ("check", "command", {
        "help": "Run the fast local agent-boundary check and emit its JSON result.",
    }),
    ("preflight", "command", {
        "hidden": True,
        "help": (
            "Run proactive static preflight: protected surfaces, forbidden edits, "
            "and high-risk capability evidence requirements."
        ),
    }),
    ("apply-patches", "command", {
        "hidden": True,
        "help": (
            "Apply patches from a scan JSON report. Dry-run by default; pass "
            "--apply to mutate. Containment-checked against the report's "
            "manifest_dir."
        ),
    }),
    # Hidden from --help (niche re-render utility), still fully invokable.
    ("evidence-packet", "command", {
        "hidden": True,
        "help": (
            "Re-render a Release Evidence Packet from an existing packet.json "
            "into md, html, and/or pdf."
        ),
    }),
    ("bootstrap", "command", {
        "hidden": True,
        "help": (
            "Run the canonical 4-call adoption flow in one command: "
            "detect → init --write --ci → scan --suggest-patches → "
            "apply-patches --confidence high."
        ),
    }),
    ("explain-finding", "command", {
        "hidden": True,
        "help": (
            "Explain a specific finding from a `report.json`, with evidence "
            "and a 3–5 sentence prose summary. Companion to `explain "
            "<check-id>`."
        ),
    }),
    ("findings", "command", {
        "hidden": True,
        "help": "Filter findings from a `report.json` by provenance kind for reviewer triage.",
    }),
    ("trigger", "command", {
        "hidden": True,
        "help": (
            "Evaluate the trigger catalog against a diff and emit a run/skip "
            "verdict. Reads --changed-files / --diff, or --base/--head (git)."
        ),
    }),
    ("verify", "command", {
        "help": (
            "Run the canonical PR gate: trigger evaluation, optional base scan, "
            "and one authoritative head scan."
        ),
    }),
    ("attest", "command", {
        "hidden": True,
        "help": (
            "Derive a deterministic local release attestation from verifier.json "
            "(verdict, capability delta, human-ack state, policy + artifact hashes)."
        ),
    }),
    ("install-hooks", "command", {
        "hidden": True,
        "help": "Install advisory local coding-agent hooks. Currently supports --target claude-code.",
    }),
    ("audit", "command", {
        "help": (
            "Run `shipgate audit --host` for a zero-config permission inventory. "
            "`audit --host` inventories "
            "coding-agent host grants (MCP servers, permission rules, hooks, "
            "workflow scopes) without requiring shipgate.yaml; `--save-baseline` "
            "records the acknowledged state (writes one JSON file under "
            ".agents-shipgate/) and `--drift [--fail-on-drift]` reports when "
            "current grants no longer match it."
        ),
    }),
    ("mcp-serve", "command", {"hidden": True}),
    ("scan", "register", {}),
    ("list-checks", "register", {}),
    ("contract", "register", {}),
    ("explain", "register", {}),
    ("init", "register", {}),
    ("doctor", "register", {}),
    ("baseline", "register", {}),
    ("fixture", "group", {"hidden": True}),
    ("feedback", "group", {"hidden": True}),
    ("scenario", "group", {"hidden": True}),
    ("skill", "group", {"hidden": True}),
    ("capability", "group", {"hidden": True}),
    ("agent", "group", {"hidden": True}),
    ("mcp", "group", {"hidden": True}),
    ("org", "group", {"hidden": True}),
    ("registry", "group", {"hidden": True}),
    ("verification", "group", {"hidden": True}),
    ("authorization", "group", {"hidden": True}),
)


class _OnDemandGroup(typer.core.TyperGroup):
    """The root group: lists every command, imports one only when resolved.

    Click resolves a subcommand through `get_command`, and help, `--help-all`
    and completion walk `list_commands` then `get_command`, so those two are
    the whole seam. A resolved command is cached on this group instance.
    """

    def list_commands(self, ctx: Context) -> list[str]:
        return [row[0] for row in _ROOT_COMMANDS]

    def get_command(self, ctx: Context, cmd_name: str) -> Command | None:
        command = self.commands.get(cmd_name)
        if command is None and cmd_name in _ROWS:
            command = _load_root_command(cmd_name)
            self.commands[cmd_name] = command
        return command

    def resolve_command(
        self, ctx: Context, args: list[str]
    ) -> tuple[str | None, Command | None, list[str]]:
        # Typer suggests a close name from the commands resolved so far, so a
        # typo resolves its close names first or it gets no "Did you mean".
        if args and args[0] not in _ROWS and self.suggest_commands:
            for name in get_close_matches(args[0], list(_ROWS)):
                self.get_command(ctx, name)
        return super().resolve_command(ctx, args)


def _load_root_command(name: str) -> Command:
    _name, kind, options = _ROWS[name]
    target = _root_callable(name)
    scratch = typer.Typer()
    if kind == "command":
        scratch.command(name, **options)(target)
    elif kind == "group":
        scratch.add_typer(target, name=name, **options)
    else:
        target(scratch)
    for command_info in scratch.registered_commands:
        command = typer.main.get_command_from_info(
            command_info,
            pretty_exceptions_short=app.pretty_exceptions_short,
            rich_markup_mode=app.rich_markup_mode,
        )
        if command.name == name:
            return command
    for group_info in scratch.registered_groups:
        if group_info.name == name:
            return typer.main.get_group_from_info(
                group_info,
                pretty_exceptions_short=app.pretty_exceptions_short,
                suggest_commands=app.suggest_commands,
                rich_markup_mode=app.rich_markup_mode,
            )
    raise RuntimeError(f"the loader for {name!r} does not define that root command")


def _root_callable(name: str) -> Any:
    """Import what one root command row names.

    Every import is a static ``from ... import``, so the static-only scanner
    check (`tests/test_adapter_static_only.py`) sees each module the CLI can
    load, with no dynamic import to allowlist. Nothing here runs until Click
    resolves the command.
    """

    match name:
        case "self-check":
            from agents_shipgate.cli.self_check import self_check as target
        case "detect":
            from agents_shipgate.cli.detect import detect as target
        case "diff":
            from agents_shipgate.cli.diff import diff as target
        case "check":
            from agents_shipgate.cli.check import check as target
        case "preflight":
            from agents_shipgate.cli.preflight import preflight as target
        case "apply-patches":
            from agents_shipgate.cli.apply_patches import apply_patches as target
        case "evidence-packet":
            from agents_shipgate.cli.evidence_packet import evidence_packet as target
        case "bootstrap":
            from agents_shipgate.cli.bootstrap import bootstrap as target
        case "explain-finding":
            from agents_shipgate.cli.explain_finding import explain_finding as target
        case "findings":
            from agents_shipgate.cli.findings import findings as target
        case "trigger":
            from agents_shipgate.cli.trigger import trigger as target
        case "verify":
            from agents_shipgate.cli.verify import verify as target
        case "attest":
            from agents_shipgate.cli.attest import _attest_command as target
        case "install-hooks":
            from agents_shipgate.cli.install_hooks import install_hooks as target
        case "audit":
            from agents_shipgate.cli.host_audit import audit as target
        case "mcp-serve":
            target = _mcp_serve_command
        case "scan":
            from agents_shipgate.cli._register_scan import register as target
        case "list-checks":
            from agents_shipgate.cli._register_list_checks import register as target
        case "contract":
            from agents_shipgate.cli._register_contract import register as target
        case "explain":
            from agents_shipgate.cli._register_explain import register as target
        case "init":
            from agents_shipgate.cli._register_init import register as target
        case "doctor":
            from agents_shipgate.cli._register_doctor import register as target
        case "baseline":
            from agents_shipgate.cli._register_baseline import register as target
        case "fixture":
            from agents_shipgate.cli.fixture import fixture_app as target
        case "feedback":
            from agents_shipgate.cli.feedback import feedback_app as target
        case "scenario":
            from agents_shipgate.cli.scenario import scenario_app as target
        case "skill":
            from agents_shipgate.cli.skill import skill_app as target
        case "capability":
            from agents_shipgate.cli.capability import capability_app as target
        case "agent":
            from agents_shipgate.cli.agent_interface import agent_app as target
        case "mcp":
            from agents_shipgate.cli.mcp import mcp_app as target
        case "org":
            from agents_shipgate.cli.org import org_app as target
        case "registry":
            from agents_shipgate.cli.registry import registry_app as target
        case "verification":
            from agents_shipgate.cli.verification import verification_app as target
        case "authorization":
            from agents_shipgate.cli.authorization import authorization_app as target
        case _:
            raise KeyError(name)
    return target


_ROWS = {row[0]: row for row in _ROOT_COMMANDS}

app = typer.Typer(
    name="agents-shipgate",
    help="The deterministic merge gate for AI-generated agent capability changes.",
    # Bare `shipgate` runs a zero-config first look (see the root callback),
    # not a help dump — so off, not True.
    no_args_is_help=False,
    invoke_without_command=True,
    cls=_OnDemandGroup,
)


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


logger = logging.getLogger(__name__)


def _help_with_every_command() -> str:
    """Root help with nothing hidden.

    Prominence is a reading aid, not a list of what exists, so there has to
    be one command that shows the rest. Rendered from the same Click
    command the real help comes from, with `hidden` cleared, so it cannot
    fall out of step with what is registered.
    """

    import typer.main

    command = typer.main.get_command(app)
    for name in command.list_commands(typer.Context(command)):  # type: ignore[attr-defined]
        sub = command.get_command(typer.Context(command), name)  # type: ignore[attr-defined]
        if sub is not None:
            sub.hidden = False
    return command.get_help(typer.Context(command, info_name="agents-shipgate"))


@app.callback()
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
    help_all: bool = typer.Option(
        False,
        "--help-all",
        help="Show every command, including the supporting ones.",
    ),
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
    if help_all:
        typer.echo(_help_with_every_command())
        raise typer.Exit(0)
    # Bare `shipgate` (no subcommand) runs a zero-config, read-only first
    # look instead of dumping --help, so a fresh repo gets a verdict and a
    # next step without a manifest. Named subcommands and `--help` are
    # unaffected: this branch only fires when no subcommand was matched.
    if ctx.invoked_subcommand is None:
        from agents_shipgate.cli.first_look import run_first_look

        run_first_look(Path("."))
        raise typer.Exit(0)
