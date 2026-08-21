"""Zero-config "first look" for a bare ``shipgate`` invocation.

Running ``shipgate`` with no subcommand prints a short, read-only
orientation: the working-tree boundary verdict (the instant-value moment),
a repo classification, a host-grant summary, and the single best next
command. It requires no ``shipgate.yaml`` and writes nothing.

This is a router, not a gate — the promoted gates remain ``shipgate check``
(fast local agent loop), ``agents-shipgate verify`` (committed PR), and
``shipgate audit --host`` (zero-config permission inventory). It composes three
existing read-only surfaces (``detect``, the agent-native boundary ``check``,
and ``audit --host``) so it adds no new analysis and no new public command.
Every step is wrapped so a partial environment (no git, unreadable config)
degrades to a shorter summary rather than an error: a first look must never
hard-fail.
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

    classification, has_agent_surface, settle_command = _classification_line(root)
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
    typer.echo(
        "→ " + _next_command(has_manifest, has_agent_surface, has_host, settle_command)
    )


def _working_tree_line(workspace: Path) -> str:
    """Boundary verdict for the local uncommitted diff, or a clean/skip note.

    Uses the same ``git diff HEAD`` body that ``shipgate check`` evaluates, but
    also keeps the changed-path list so an untracked-only working tree (common
    on first adoption, when new tool files have not been ``git add``-ed) is not
    misreported as clean — their content is intentionally absent from the diff
    body, so they surface as "stage and run check" rather than a verdict.
    """

    from agents_shipgate.cli.agent_result import (
        build_agent_boundary_result,
        git_boundary_change_set,
    )

    try:
        change_set = git_boundary_change_set(workspace=workspace, base=None, head=None)
    except Exception:  # noqa: BLE001 - not a git checkout / no HEAD to diff.
        return "not a git checkout — skipped"
    if not change_set.diff_text.strip():
        if change_set.changed_paths:
            count = len(change_set.changed_paths)
            return (
                f"{count} untracked file{'s' if count != 1 else ''} found, but no "
                "recognized boundary content changed"
            )
        return "clean — no uncommitted changes to check"
    try:
        result = build_agent_boundary_result(
            agent="claude-code",
            workspace=workspace,
            diff_text=change_set.diff_text,
            config=workspace / "shipgate.yaml",
            policy=None,
            input_mode="worktree",
            input_issues=list(change_set.issues),
        )
    except Exception:  # noqa: BLE001 - a first look must never hard-fail.
        return "uncommitted changes present (run `shipgate check` for the verdict)"
    count = len(change_set.changed_paths)
    files = f"{count} changed file{'s' if count != 1 else ''}"
    if result.decision == "allow":
        return f"allow — {files}, no boundary issues"
    return f"{result.decision} — {files}; {result.summary}"


def _classification_line(workspace: Path) -> tuple[str, bool, str | None]:
    """Repo classification, whether a tool surface was found, and the command
    that settles the classification when this one could not.

    A capped parse never reports "no signals": that is a claim about the whole
    workspace made from the part of it that was read, and a first look is
    exactly where an adopter acts on it. The third value carries that state
    through to the single ``Next:`` line, because printing the retry here and
    then routing the reader to ``verify --preview`` — which does not complete
    the parse either — advertises a best next step that walks past the
    recovery one line above it (#399 review).
    """

    from agents_shipgate.cli.discovery import DEFAULT_MAX_PYTHON_FILES, detect_workspace

    try:
        result = detect_workspace(workspace, max_python_files=DEFAULT_MAX_PYTHON_FILES)
    except Exception:  # noqa: BLE001 - classification is best-effort.
        return "classification unavailable", False, None
    if result.python_parse_truncated:
        total = result.workspace_signals.python_file_total
        settle = f"agents-shipgate detect --max-python-files {total} --json"
        return (
            "classification incomplete — the Python parse stopped at its cap",
            True,
            settle,
        )
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
    return label, has_surface, None


def _host_grants_line(workspace: Path) -> tuple[str | None, bool]:
    """Summarize grants without ever presenting incomplete coverage as empty."""

    from agents_shipgate.cli.host_audit import host_audit_inventory

    try:
        inventory = host_audit_inventory(workspace)
    except Exception:  # noqa: BLE001 - host audit is best-effort here.
        return (
            "inventory unavailable (run `shipgate audit --host` for the coverage issue)",
            True,
        )

    if inventory.get("host_grants_inventory_schema_version") == "0.2":
        grants = inventory.get("grants") or []
        kind_labels = {
            "mcp_server": "MCP server",
            "permission_rule": "permission rule",
            "permission_mode": "permission mode",
            "hook": "hook",
            "sandbox": "sandbox setting",
            "additional_path": "additional path",
            "plugin_or_app": "plugin/app",
            "workflow": "workflow",
            "instruction_trust_root": "instruction trust root",
        }
        parts: list[str] = []
        for kind in sorted(kind_labels):
            count = sum(1 for grant in grants if grant.get("kind") == kind)
            if count:
                label = kind_labels[kind]
                parts.append(f"{count} {label}{'s' if count != 1 else ''}")

        incomplete_hosts = sorted(
            {
                f"{coverage.get('host')}={coverage.get('status')}"
                for coverage in inventory.get("host_coverage") or []
                if coverage.get("status") != "complete"
            }
        )
        blocking_issues = [
            issue for issue in inventory.get("issues") or [] if issue.get("blocking")
        ]
        if incomplete_hosts or blocking_issues:
            detail = ", ".join(incomplete_hosts) or (
                f"{len(blocking_issues)} blocking issue"
                f"{'s' if len(blocking_issues) != 1 else ''}"
            )
            parts.append(f"coverage incomplete ({detail})")

        if not parts:
            return None, False
        return ", ".join(parts) + " (run `shipgate audit --host` for details)", True

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
    warnings = inventory.get("parse_warnings") or []
    if warnings:
        parts.append(
            f"coverage incomplete ({len(warnings)} parse warning"
            f"{'s' if len(warnings) != 1 else ''})"
        )
    if not parts:
        return None, False
    return ", ".join(parts) + " (run `shipgate audit --host` for the inventory)", True


def _next_command(
    has_manifest: bool,
    has_agent_surface: bool,
    has_host: bool,
    settle_command: str | None = None,
) -> str:
    """The single highest-value next command for this repo's state."""

    if settle_command is not None and not has_manifest:
        # Nothing below can be the best next step while the classification the
        # choices rest on is incomplete, and `verify --preview` does not
        # complete it either.
        return (
            f"Next: `{settle_command}` — the classification above stopped at "
            "its Python-file cap, and this bound covers every Python file here."
        )
    if has_manifest:
        return "Next: `agents-shipgate verify --base origin/main --head HEAD` to gate your PR."
    if has_agent_surface:
        return (
            "Next: `agents-shipgate verify --preview --json` to confirm the setup path, "
            "or `shipgate check` to check your working tree now."
        )
    if has_host:
        return (
            "Next: `shipgate audit --host` to inventory what your coding agent "
            "can do here."
        )
    return (
        "Next: no agent tool surface or host config detected — Shipgate may not "
        "apply here. Run `shipgate audit --host` if you need a host-grant "
        "inventory."
    )


__all__ = ["run_first_look"]
