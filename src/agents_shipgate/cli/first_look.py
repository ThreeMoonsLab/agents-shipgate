"""Zero-config "first look" for a bare ``shipgate`` invocation.

Running ``shipgate`` with no subcommand prints a short, read-only
orientation: the working-tree boundary verdict (the instant-value moment),
a repo classification, a host-grant summary, and the single best next
command. It requires no ``shipgate.yaml`` and writes nothing.

This is a router, not a gate — the gates remain ``check`` (local diff) and
``verify`` (committed PR). It composes three existing read-only surfaces
(``detect``, the agent-native boundary ``check``, and ``audit --host``) so
it adds no new analysis and no new public command. Every step is wrapped so
a partial environment (no git, unreadable config) degrades to a shorter
summary rather than an error: a first look must never hard-fail.
"""

from __future__ import annotations

from pathlib import Path

import typer

from agents_shipgate import __version__


def run_first_look(workspace: Path) -> None:
    """Print the zero-config orientation summary for ``workspace``."""

    root = workspace.resolve()
    typer.echo(f"Agents Shipgate {__version__} · first look (read-only, no manifest needed)")

    typer.echo(f"  working tree:  {_working_tree_line(root)}")

    classification, has_agent_surface = _classification_line(root)
    typer.echo(f"  repo:          {classification}")

    host_line, has_host = _host_grants_line(root)
    if host_line is not None:
        typer.echo(f"  host grants:   {host_line}")

    has_manifest = (root / "shipgate.yaml").is_file()
    typer.echo(
        "  manifest:      "
        + ("shipgate.yaml present" if has_manifest else "none (Shipgate runs without one)")
    )

    typer.echo("")
    typer.echo("→ " + _next_command(has_manifest, has_agent_surface, has_host))


def _working_tree_line(workspace: Path) -> str:
    """Boundary verdict for the local uncommitted diff, or a clean/skip note."""

    from agents_shipgate.cli.agent_result import build_codex_agent_result, git_diff_text

    try:
        diff_text = git_diff_text(workspace=workspace, base=None, head=None)
    except (OSError, RuntimeError):
        return "not a git checkout — skipped"
    if not diff_text.strip():
        return "clean — no uncommitted changes to check"
    try:
        result = build_codex_agent_result(
            agent="claude-code",
            workspace=workspace,
            diff_text=diff_text,
            config=workspace / "shipgate.yaml",
            policy=None,
        )
    except Exception:  # noqa: BLE001 - a first look must never hard-fail.
        return "uncommitted changes present (run `shipgate check` for the verdict)"
    count = len(result.changed_files)
    files = f"{count} changed file{'s' if count != 1 else ''}"
    if result.decision == "allow":
        return f"allow — {files}, no boundary issues"
    return f"{result.decision} — {files}; {result.summary}"


def _classification_line(workspace: Path) -> tuple[str, bool]:
    """Repo classification plus whether any agent tool surface was found."""

    from agents_shipgate.cli.discovery import detect_workspace

    try:
        result = detect_workspace(workspace, max_python_files=1000)
    except Exception:  # noqa: BLE001 - classification is best-effort.
        return "classification unavailable", False
    has_surface = bool(
        result.is_agent_project
        or result.suggested_sources
        or result.codex_plugin_candidates
    )
    if result.is_agent_project:
        frameworks = ", ".join(f.type for f in result.frameworks[:3])
        label = f"agent project ({frameworks})" if frameworks else "agent project"
    elif result.suggested_sources or result.codex_plugin_candidates:
        label = "Shipgate-compatible tool artifacts found"
    else:
        label = "no strong agent-framework signals"
    return label, has_surface


def _host_grants_line(workspace: Path) -> tuple[str | None, bool]:
    """One-line count of coding-agent host grants, or ``None`` when there are none."""

    from agents_shipgate.cli.host_audit import host_audit_inventory

    try:
        inventory = host_audit_inventory(workspace)
    except Exception:  # noqa: BLE001 - host audit is best-effort here.
        return None, False
    parts: list[str] = []
    for key, label in (
        ("mcp_servers", "MCP server"),
        ("permission_rules", "permission rule"),
        ("hooks", "hook"),
        ("workflows", "workflow"),
    ):
        count = len(inventory.get(key) or [])
        if count:
            parts.append(f"{count} {label}{'s' if count != 1 else ''}")
    if inventory.get("codex_config_present"):
        parts.append("codex config")
    if not parts:
        return None, False
    return ", ".join(parts) + " (run `shipgate audit --host` for the inventory)", True


def _next_command(has_manifest: bool, has_agent_surface: bool, has_host: bool) -> str:
    """The single highest-value next command for this repo's state."""

    if has_manifest:
        return "Next: `shipgate verify --base origin/main --head HEAD` to gate your PR."
    if has_agent_surface:
        return (
            "Next: `shipgate init` to set up the gate, or `shipgate check` to "
            "check your working tree now."
        )
    if has_host:
        return (
            "Next: `shipgate audit --host` to inventory what your coding agent "
            "can do here."
        )
    return (
        "Next: no agent tool surface or host config detected — Shipgate may not "
        "apply here. Run `shipgate detect` for the full classification."
    )


__all__ = ["run_first_look"]
