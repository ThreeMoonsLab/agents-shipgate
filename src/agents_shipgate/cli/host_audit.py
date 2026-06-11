"""``agents-shipgate audit --host`` — zero-config host-grant inventory.

Reads the coding-agent host configuration a repo already contains —
project MCP server declarations, Claude Code permission rules and hooks,
GitHub workflow permissions — and prints a one-page Markdown (or JSON)
inventory. Read-only: no ``shipgate.yaml`` required, nothing written,
nothing executed. The point is a first-touch answer to "what is my
coding agent currently allowed to do in this repo?" before any Shipgate
adoption decision. See ``docs/mcp-governance.md``.

Drift detection builds on the same inventory: ``--save-baseline`` records
the current grants as the acknowledged state in
``.agents-shipgate/host-grants.json`` (committed; the directory is already
a verify trust-root surface, so PR edits to the snapshot are
release-visible), and ``--drift`` deterministically diffs the current
grants against that baseline so a scheduled run catches a coding agent
quietly expanding its own authority between reviews.

Parsing helpers are shared with :mod:`agents_shipgate.core.host_boundary`
so the audit and the diff-aware ``SHIP-HOST-BOUNDARY-*`` checks classify
the same way (wildcard shapes, transport hints, write scopes).
"""

from __future__ import annotations

import hashlib
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

HOST_GRANTS_SCHEMA_VERSION = "0.1"
DEFAULT_BASELINE_FILE = Path(".agents-shipgate/host-grants.json")

# Inventory categories carried in the baseline and diffed for drift, with the
# fields that identify an entry. Entries matching on identity but differing in
# the remaining fields land in the ``changed`` bucket; categories whose
# identity is the whole entry (or a plain string) are atomic add/remove.
_GRANT_CATEGORIES: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("mcp_servers", ("host", "file", "server")),
    ("permission_rules", ("file", "kind", "rule")),
    ("hooks", ("file", "event")),
    ("workflows", ("file",)),
    ("codex_config_present", None),
)


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


def normalized_host_grants(inventory: dict[str, Any]) -> dict[str, Any]:
    """Portable, hashable projection of the inventory for baseline/diff use.

    Drops ``workspace`` (a machine-specific absolute path) and
    ``parse_warnings`` (exception text that is not stable across Python
    versions); a file that becomes unparseable still shows up as drift
    because its entries disappear. Each category list is canonically
    sorted so semantically equal inventories serialize identically.
    """

    normalized: dict[str, Any] = {}
    for category, _identity in _GRANT_CATEGORIES:
        entries = inventory.get(category) or []
        normalized[category] = sorted(
            entries, key=lambda entry: json.dumps(entry, sort_keys=True)
        )
    return normalized


def host_grants_sha256(grants: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(grants, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_host_grants_baseline(inventory: dict[str, Any]) -> dict[str, Any]:
    """Baseline payload. Deliberately content-only — no timestamp or CLI
    version — so re-saving an unchanged state is byte-identical and never
    produces commit noise."""

    grants = normalized_host_grants(inventory)
    return {
        "host_grants_schema_version": HOST_GRANTS_SCHEMA_VERSION,
        "inventory_sha256": host_grants_sha256(grants),
        "inventory": grants,
    }


def load_host_grants_baseline(path: Path) -> dict[str, Any]:
    """Load and validate a baseline file; raises ValueError with a
    routable message on any problem."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(
            f"No host-grants baseline at {path} ({exc}). Record one first: "
            "agents-shipgate audit --host --save-baseline"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Host-grants baseline {path} is not valid JSON ({exc}). "
            "Re-record it: agents-shipgate audit --host --save-baseline"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Host-grants baseline {path} must be a JSON object. "
            "Re-record it: agents-shipgate audit --host --save-baseline"
        )
    version = data.get("host_grants_schema_version")
    if version != HOST_GRANTS_SCHEMA_VERSION:
        raise ValueError(
            f"Host-grants baseline {path} has schema version {version!r}; "
            f"this CLI supports {HOST_GRANTS_SCHEMA_VERSION!r}. Upgrade "
            "agents-shipgate or re-record the baseline with this version."
        )
    inventory = data.get("inventory")
    if not isinstance(inventory, dict):
        raise ValueError(
            f"Host-grants baseline {path} is missing its inventory. "
            "Re-record it: agents-shipgate audit --host --save-baseline"
        )
    return data


def _entries_by_key(
    entries: list[dict[str, Any]], identity: tuple[str, ...]
) -> dict[tuple[str, ...], dict[str, Any]]:
    return {
        tuple(str(entry.get(field)) for field in identity): entry for entry in entries
    }


def diff_host_grants(
    baseline: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Deterministic per-category drift between two normalized grant sets."""

    drift: dict[str, Any] = {}
    for category, identity in _GRANT_CATEGORIES:
        base_entries = baseline.get(category) or []
        cur_entries = current.get(category) or []
        if identity is None:
            base_set = set(base_entries)
            cur_set = set(cur_entries)
            drift[category] = {
                "added": sorted(cur_set - base_set),
                "removed": sorted(base_set - cur_set),
                "changed": [],
            }
            continue

        base_by_key = _entries_by_key(base_entries, identity)
        cur_by_key = _entries_by_key(cur_entries, identity)
        added = [cur_by_key[key] for key in sorted(set(cur_by_key) - set(base_by_key))]
        removed = [
            base_by_key[key] for key in sorted(set(base_by_key) - set(cur_by_key))
        ]
        changed = [
            {"baseline": base_by_key[key], "current": cur_by_key[key]}
            for key in sorted(set(base_by_key) & set(cur_by_key))
            if base_by_key[key] != cur_by_key[key]
        ]
        drift[category] = {"added": added, "removed": removed, "changed": changed}
    return drift


def host_grant_expansion_signals(drift: dict[str, Any]) -> list[str]:
    """Name the drift entries that expand coding-agent authority.

    Presentation only — the drift gate is *any* drift, because direction is
    not a safe/unsafe oracle (note the asymmetries below: a **removed**
    ``deny`` rule and a **removed** ``ask`` rule both broaden authority).
    """

    signals: list[str] = []
    for server in drift["mcp_servers"]["added"]:
        signals.append(f"mcp_server_added: {server['host']}:{server['server']}")
    for rule in drift["permission_rules"]["added"]:
        if rule["kind"] == "allow":
            kind = "wildcard_allow_added" if rule.get("wildcard") else "allow_rule_added"
            signals.append(f"{kind}: {rule['rule']}")
    for rule in drift["permission_rules"]["removed"]:
        if rule["kind"] == "deny":
            signals.append(f"deny_rule_removed: {rule['rule']}")
        elif rule["kind"] == "ask":
            signals.append(f"ask_rule_removed: {rule['rule']}")
    for hook in drift["hooks"]["added"]:
        signals.append(f"hook_added: {hook['file']}:{hook['event']}")
    for workflow in drift["workflows"]["added"]:
        if workflow["write_scopes"] or workflow["pull_request_target"]:
            signals.append(f"workflow_write_added: {workflow['file']}")
    for change in drift["workflows"]["changed"]:
        before, after = change["baseline"], change["current"]
        grew_scopes = set(after["write_scopes"]) - set(before["write_scopes"])
        if (
            grew_scopes
            or (after["write_all"] and not before["write_all"])
            or (after["pull_request_target"] and not before["pull_request_target"])
        ):
            signals.append(f"workflow_write_expanded: {after['file']}")
    for path in drift["codex_config_present"]["added"]:
        signals.append(f"codex_config_added: {path}")
    return sorted(signals)


def build_host_drift_payload(
    *,
    baseline: dict[str, Any],
    inventory: dict[str, Any],
    baseline_file: str,
) -> dict[str, Any]:
    current = normalized_host_grants(inventory)
    baseline_grants = normalized_host_grants(baseline["inventory"])
    drift = diff_host_grants(baseline_grants, current)
    has_drift = any(
        bucket
        for category in drift.values()
        for bucket in (category["added"], category["removed"], category["changed"])
    )
    return {
        "host_grants_schema_version": HOST_GRANTS_SCHEMA_VERSION,
        "baseline_file": baseline_file,
        "baseline_sha256": host_grants_sha256(baseline_grants),
        "current_sha256": host_grants_sha256(current),
        "has_drift": has_drift,
        "drift": drift,
        "expansion_signals": host_grant_expansion_signals(drift),
        "parse_warnings": list(inventory.get("parse_warnings") or []),
    }


_CATEGORY_TITLES = {
    "mcp_servers": "MCP servers",
    "permission_rules": "Claude Code permission rules",
    "hooks": "Claude Code hooks",
    "workflows": "GitHub workflows",
    "codex_config_present": "Codex configuration files",
}


def _drift_entry_label(category: str, entry: Any) -> str:
    if category == "mcp_servers":
        return f"`{entry['host']}` server `{entry['server']}` ({entry['file']})"
    if category == "permission_rules":
        wildcard = " **(wildcard)**" if entry.get("wildcard") else ""
        return f"{entry['kind']} `{entry['rule']}`{wildcard} ({entry['file']})"
    if category == "hooks":
        return f"`{entry['event']}` ({entry['file']})"
    if category == "workflows":
        marks = []
        if entry.get("write_all"):
            marks.append("write-all")
        if entry.get("pull_request_target"):
            marks.append("pull_request_target")
        suffix = f" — {', '.join(marks)}" if marks else ""
        return f"`{entry['file']}`{suffix}"
    return f"`{entry}`"


def render_host_drift_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = ["# Host Grant Drift", ""]
    lines.append(
        f"Baseline: `{payload['baseline_file']}` "
        f"(sha256 `{payload['baseline_sha256'][:12]}…`) · "
        f"current sha256 `{payload['current_sha256'][:12]}…`"
    )
    lines.append("")
    if not payload["has_drift"]:
        lines.append("No drift — current host grants match the acknowledged baseline.")
        return "\n".join(lines) + "\n"

    lines.append("**Drift detected** — host grants differ from the acknowledged baseline.")
    lines.append("")

    signals = payload["expansion_signals"]
    if signals:
        lines.append(f"## Expansion signals ({len(signals)})")
        lines.append("")
        for signal in signals:
            lines.append(f"- ⚠ `{signal}`")
        lines.append("")

    for category, _identity in _GRANT_CATEGORIES:
        buckets = payload["drift"][category]
        if not (buckets["added"] or buckets["removed"] or buckets["changed"]):
            continue
        lines.append(f"## {_CATEGORY_TITLES[category]}")
        lines.append("")
        for entry in buckets["added"]:
            lines.append(f"- added: {_drift_entry_label(category, entry)}")
        for entry in buckets["removed"]:
            lines.append(f"- removed: {_drift_entry_label(category, entry)}")
        for change in buckets["changed"]:
            lines.append(
                f"- changed: {_drift_entry_label(category, change['current'])}"
            )
        lines.append("")

    if payload["parse_warnings"]:
        lines.append("## Parse warnings (current state)")
        lines.append("")
        for warning in payload["parse_warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("---")
    lines.append(
        "After a human reviews this drift, re-acknowledge the new state: "
        "`agents-shipgate audit --host --save-baseline`. Do not re-save to "
        "silence drift you have not reviewed."
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
    save_baseline: bool = typer.Option(
        False,
        "--save-baseline",
        help=(
            "Record the current host-grant inventory as the acknowledged "
            "baseline (writes the --baseline-file)."
        ),
    ),
    drift: bool = typer.Option(
        False,
        "--drift",
        help="Diff the current host grants against the saved baseline and report drift.",
    ),
    baseline_file: Path = typer.Option(
        DEFAULT_BASELINE_FILE,
        "--baseline-file",
        help="Host-grants baseline location (committed; default .agents-shipgate/host-grants.json).",
    ),
    fail_on_drift: bool = typer.Option(
        False,
        "--fail-on-drift",
        help="With --drift: exit 20 when any drift is found (for scheduled CI gates).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of Markdown.",
    ),
) -> None:
    """Zero-config, read-only audits. Currently supports --host."""
    if not host:
        typer.echo(
            "Nothing to audit: pass --host for the host-capability inventory.",
            err=True,
        )
        raise typer.Exit(2)
    if save_baseline and drift:
        typer.echo(
            "--save-baseline and --drift are mutually exclusive: record the "
            "acknowledged state or compare against it, not both.",
            err=True,
        )
        raise typer.Exit(2)
    if fail_on_drift and not drift:
        typer.echo("--fail-on-drift requires --drift.", err=True)
        raise typer.Exit(2)

    inventory = host_audit_inventory(workspace)
    resolved_baseline = (
        baseline_file
        if baseline_file.is_absolute()
        else workspace.resolve() / baseline_file
    )

    if save_baseline:
        payload = build_host_grants_baseline(inventory)
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if resolved_baseline.is_file() and resolved_baseline.read_text(
            encoding="utf-8"
        ) == text:
            status = "unchanged"
        else:
            status = "updated" if resolved_baseline.is_file() else "created"
            resolved_baseline.parent.mkdir(parents=True, exist_ok=True)
            resolved_baseline.write_text(text, encoding="utf-8")
        outcome = {
            "baseline_file": str(baseline_file),
            "inventory_sha256": payload["inventory_sha256"],
            "status": status,
        }
        if json_output:
            typer.echo(json.dumps(outcome, indent=2, sort_keys=True))
        else:
            typer.echo(
                f"Host-grants baseline {status}: {baseline_file} "
                f"(sha256 {payload['inventory_sha256'][:12]}…). Commit it; "
                "verify treats .agents-shipgate/ edits as trust-root changes."
            )
        return

    if drift:
        try:
            baseline = load_host_grants_baseline(resolved_baseline)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        payload = build_host_drift_payload(
            baseline=baseline,
            inventory=inventory,
            baseline_file=str(baseline_file),
        )
        if json_output:
            typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        else:
            typer.echo(render_host_drift_markdown(payload), nl=False)
        if fail_on_drift and payload["has_drift"]:
            raise typer.Exit(20)
        return

    if json_output:
        typer.echo(json.dumps(inventory, indent=2, sort_keys=True))
        return
    typer.echo(render_host_audit_markdown(inventory), nl=False)
