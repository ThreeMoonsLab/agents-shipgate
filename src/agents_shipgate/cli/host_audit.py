"""``agents-shipgate audit --host`` — zero-config host-grant inventory.

Reads the coding-agent host configuration a repo already contains —
project MCP server declarations, Claude Code permission rules and hooks,
GitHub workflow permissions — and prints a one-page Markdown (or JSON)
inventory. Read-only: no ``shipgate.yaml`` required, nothing written,
nothing executed. The point is a first-touch answer to "what is my
coding agent currently allowed to do in this repo?" before any Shipgate
adoption decision. See ``docs/mcp-governance.md``.

Parsing helpers are shared with :mod:`agents_shipgate.core.host_boundary`
so the audit and the diff-aware ``SHIP-HOST-BOUNDARY-*`` checks classify
the same way (wildcard shapes, transport hints, write scopes).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
import yaml

from agents_shipgate.core.host_boundary import (
    _command_or_url,
    _is_wildcard_allow,
    _is_write,
    _normalize_workflow_keys,
    _server_map,
    _string_entries,
    _transport_hint,
    _trigger_names,
)

MCP_FILES: tuple[tuple[str, str], ...] = (
    (".mcp.json", "claude-code (project)"),
    (".cursor/mcp.json", "cursor"),
    (".vscode/mcp.json", "vscode"),
)
CLAUDE_SETTINGS_FILES: tuple[str, ...] = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)
CODEX_FILES: tuple[str, ...] = (".codex/config.toml", ".codex/hooks.json")


def host_audit_inventory(workspace: Path) -> dict[str, Any]:
    """Build the deterministic host-grant inventory for a workspace."""
    root = workspace.resolve()
    inventory: dict[str, Any] = {
        "workspace": str(root),
        "mcp_servers": [],
        "permission_rules": [],
        "hooks": [],
        "workflows": [],
        "codex_config_present": [],
        "parse_warnings": [],
    }

    for relative, host in MCP_FILES:
        path = root / relative
        if not path.is_file():
            continue
        data = _load_json(path, inventory)
        if data is None:
            continue
        for name, server in sorted(_server_map(data).items()):
            env_keys = sorted(server.get("env", {}) or {}) if isinstance(server, dict) else []
            inventory["mcp_servers"].append(
                {
                    "host": host,
                    "file": relative,
                    "server": name,
                    "transport": _transport_hint(server),
                    "command_or_url": _command_or_url(server),
                    "env_keys": env_keys,
                }
            )

    for relative in CLAUDE_SETTINGS_FILES:
        path = root / relative
        if not path.is_file():
            continue
        data = _load_json(path, inventory)
        if not isinstance(data, dict):
            continue
        permissions = data.get("permissions") or {}
        if isinstance(permissions, dict):
            for kind in ("allow", "ask", "deny"):
                for rule in _string_entries(permissions.get(kind)):
                    inventory["permission_rules"].append(
                        {
                            "file": relative,
                            "kind": kind,
                            "rule": rule,
                            "wildcard": kind == "allow" and _is_wildcard_allow(rule),
                        }
                    )
        hooks = data.get("hooks")
        if isinstance(hooks, dict):
            for event in sorted(hooks):
                inventory["hooks"].append({"file": relative, "event": str(event)})

    workflows_dir = root / ".github" / "workflows"
    if workflows_dir.is_dir():
        for path in sorted(workflows_dir.glob("*.yml")) + sorted(
            workflows_dir.glob("*.yaml")
        ):
            entry = _workflow_entry(path, root, inventory)
            if entry is not None:
                inventory["workflows"].append(entry)

    for relative in CODEX_FILES:
        if (root / relative).is_file():
            inventory["codex_config_present"].append(relative)

    return inventory


def _workflow_entry(
    path: Path, root: Path, inventory: dict[str, Any]
) -> dict[str, Any] | None:
    relative = path.relative_to(root).as_posix()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        inventory["parse_warnings"].append(f"{relative}: {exc}")
        return None
    if not isinstance(data, dict):
        return None
    data = _normalize_workflow_keys(data)
    triggers = sorted(_trigger_names(data.get("on")))
    write_scopes: list[str] = []
    write_all = False

    def collect(perms: Any, where: str) -> None:
        nonlocal write_all
        if perms == "write-all":
            write_all = True
            write_scopes.append(f"{where}: write-all")
            return
        if isinstance(perms, dict):
            for scope, value in sorted(perms.items()):
                if _is_write(value):
                    write_scopes.append(f"{where}: {scope}: {value}")

    collect(data.get("permissions"), "<top-level>")
    jobs = data.get("jobs")
    if isinstance(jobs, dict):
        for job_name, job in sorted(jobs.items()):
            if isinstance(job, dict):
                collect(job.get("permissions"), str(job_name))
    return {
        "file": relative,
        "triggers": triggers,
        "pull_request_target": "pull_request_target" in triggers,
        "write_all": write_all,
        "write_scopes": write_scopes,
    }


def _load_json(path: Path, inventory: dict[str, Any]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        relative = path.name
        inventory["parse_warnings"].append(f"{relative}: {exc}")
        return None


def render_host_audit_markdown(inventory: dict[str, Any]) -> str:
    lines: list[str] = ["# Host Capability Audit", ""]
    lines.append(
        "What coding agents are currently granted in this repo, from "
        "declared host configuration. Read-only snapshot; see "
        "`docs/mcp-governance.md` for the review guidance."
    )
    lines.append("")

    servers = inventory["mcp_servers"]
    lines.append(f"## MCP servers ({len(servers)})")
    lines.append("")
    if servers:
        lines.append("| Host | Server | Transport | Command / URL | Env keys |")
        lines.append("|---|---|---|---|---|")
        for item in servers:
            env = ", ".join(item["env_keys"]) or "—"
            lines.append(
                f"| {item['host']} | `{item['server']}` | {item['transport']} "
                f"| `{item['command_or_url'] or '—'}` | {env} |"
            )
    else:
        lines.append("None declared.")
    lines.append("")

    rules = inventory["permission_rules"]
    wildcard_rules = [r for r in rules if r["wildcard"]]
    lines.append(f"## Claude Code permission rules ({len(rules)})")
    lines.append("")
    if rules:
        lines.append("| File | Kind | Rule | Wildcard |")
        lines.append("|---|---|---|---|")
        for item in rules:
            flag = "**yes**" if item["wildcard"] else ""
            lines.append(
                f"| {item['file']} | {item['kind']} | `{item['rule']}` | {flag} |"
            )
        if wildcard_rules:
            lines.append("")
            lines.append(
                f"⚠ {len(wildcard_rules)} wildcard-shaped allow rule(s) — these "
                "grant broad tool access and would block a Shipgate-verified PR "
                "(`SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW`)."
            )
    else:
        lines.append("None declared.")
    lines.append("")

    hooks = inventory["hooks"]
    lines.append(f"## Claude Code hooks ({len(hooks)})")
    lines.append("")
    for item in hooks:
        lines.append(f"- `{item['file']}` → `{item['event']}`")
    if not hooks:
        lines.append("None declared.")
    lines.append("")

    workflows = inventory["workflows"]
    risky = [w for w in workflows if w["write_scopes"] or w["pull_request_target"]]
    lines.append(f"## GitHub workflows ({len(workflows)}; {len(risky)} with write scopes or pull_request_target)")
    lines.append("")
    for item in workflows:
        marks: list[str] = []
        if item["write_all"]:
            marks.append("**write-all**")
        if item["pull_request_target"]:
            marks.append("**pull_request_target**")
        suffix = f" — {', '.join(marks)}" if marks else ""
        lines.append(f"- `{item['file']}`{suffix}")
        for scope in item["write_scopes"]:
            lines.append(f"  - write scope: `{scope}`")
    if not workflows:
        lines.append("None found.")
    lines.append("")

    if inventory["codex_config_present"]:
        lines.append("## Codex configuration")
        lines.append("")
        for relative in inventory["codex_config_present"]:
            lines.append(
                f"- `{relative}` present — diff-time semantics are covered by "
                "the `SHIP-CODEX-BOUNDARY-*` checks."
            )
        lines.append("")

    if inventory["parse_warnings"]:
        lines.append("## Parse warnings")
        lines.append("")
        for warning in inventory["parse_warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("---")
    lines.append(
        "Next: `agents-shipgate verify --preview --json` to check whether "
        "Shipgate should gate this repo's PRs."
    )
    return "\n".join(lines) + "\n"


def audit(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace to inventory.",
    ),
    host: bool = typer.Option(
        False,
        "--host",
        help="Inventory coding-agent host grants (MCP servers, permission rules, hooks, workflow scopes).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the inventory as JSON instead of Markdown.",
    ),
) -> None:
    """Zero-config, read-only audits. Currently supports --host."""
    if not host:
        typer.echo(
            "Nothing to audit: pass --host for the host-capability inventory.",
            err=True,
        )
        raise typer.Exit(2)
    inventory = host_audit_inventory(workspace)
    if json_output:
        typer.echo(json.dumps(inventory, indent=2, sort_keys=True))
        return
    typer.echo(render_host_audit_markdown(inventory), nl=False)
