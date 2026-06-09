"""Host-boundary governance for coding-agent HOST configuration changes.

Mirrors the ``agents_shipgate.core.codex_boundary`` diff-aware check
architecture for the host side of the boundary: Claude Code settings
(``.claude/settings.json`` / ``.claude/settings.local.json``), project
MCP server declarations (``.mcp.json``, ``.cursor/mcp.json``,
``.vscode/mcp.json``), and GitHub workflow permission expansion
(``.github/workflows/*.yml|yaml``).

NOTE: this module is the second consumer of the codex boundary diff
plumbing (``parse_unified_diff``, ``_resolve_changed_file_text``, the
canonical-JSON and dedupe helpers). Importing those private helpers from
the sibling module is deliberate for now; a future split will move the
shared diff plumbing into a common module.

Static-only contract: pure parsing via ``json.loads`` / ``yaml.safe_load``
only — no subprocess, no network, no exec/eval/importlib. Enforced by
``tests/test_adapter_static_only.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from agents_shipgate.core.codex_boundary import (
    _DECISION_RANK,
    _RISK_BY_ACTION,
    _RISK_RANK,
    DEFAULT_POLICY_VERSION,
    _canonical_json,
    _dedupe_violations,
    _display_path,
    _resolve_changed_file_text,
    parse_unified_diff,
)
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultDiagnostic,
    AgentResultRiskLevel,
    AgentResultViolatedRule,
)

DEFAULT_POLICY_PATH = Path("policies/host-boundary.shipgate.yaml")

# Server-config keys whose change re-shapes what the host will execute or
# connect to. Env var VALUES never reach evidence — only the key name
# ``env`` appears in ``changed_keys``.
_MCP_SERVER_KEYS = ("args", "command", "env", "serverUrl", "url")


@dataclass(frozen=True)
class HostBoundaryRule:
    id: str
    check_id: str
    title: str
    action: str
    risk_level: AgentResultRiskLevel
    recommendation: str


@dataclass(frozen=True)
class HostBoundaryPolicy:
    id: str
    version: str
    rules: dict[str, HostBoundaryRule]


DEFAULT_RULES: dict[str, HostBoundaryRule] = {
    "HOST-CONFIG-PARSE-FAILED": HostBoundaryRule(
        id="HOST-CONFIG-PARSE-FAILED",
        check_id="SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED",
        title="Host configuration could not be parsed",
        action="require_review",
        risk_level="medium",
        recommendation="Fix the malformed host config or have a human review the change.",
    ),
    "HOST-MCP-SERVER-ADDED": HostBoundaryRule(
        id="HOST-MCP-SERVER-ADDED",
        check_id="SHIP-HOST-BOUNDARY-MCP-SERVER-ADDED",
        title="New MCP server declared for the coding-agent host",
        action="require_review",
        risk_level="high",
        recommendation="Have a human review the new MCP server before the agent can use it.",
    ),
    "HOST-MCP-SERVER-CHANGED": HostBoundaryRule(
        id="HOST-MCP-SERVER-CHANGED",
        check_id="SHIP-HOST-BOUNDARY-MCP-SERVER-CHANGED",
        title="Existing MCP server declaration changed",
        action="require_review",
        risk_level="high",
        recommendation="Review the changed MCP server command, URL, args, or env keys.",
    ),
    "HOST-PERMISSION-WILDCARD-ALLOW": HostBoundaryRule(
        id="HOST-PERMISSION-WILDCARD-ALLOW",
        check_id="SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW",
        title="Claude Code allow rule grants a wildcard tool surface",
        action="block",
        risk_level="critical",
        recommendation="Do not allow wildcard tool permissions; scope the rule to specific commands.",
    ),
    "HOST-PERMISSION-ALLOW-EXPANDED": HostBoundaryRule(
        id="HOST-PERMISSION-ALLOW-EXPANDED",
        check_id="SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED",
        title="Claude Code permission allowlist expanded",
        action="require_review",
        risk_level="high",
        recommendation="Have a human approve the new permission allow rule.",
    ),
    "HOST-PERMISSION-DENY-REMOVED": HostBoundaryRule(
        id="HOST-PERMISSION-DENY-REMOVED",
        check_id="SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED",
        title="Claude Code permission deny rule removed",
        action="require_review",
        risk_level="high",
        recommendation="Have a human confirm the removed deny rule is no longer needed.",
    ),
    "HOST-HOOK-CHANGED": HostBoundaryRule(
        id="HOST-HOOK-CHANGED",
        check_id="SHIP-HOST-BOUNDARY-HOOK-CHANGED",
        title="Claude Code hooks changed",
        action="require_review",
        risk_level="high",
        recommendation="Review executable hook changes before the agent relies on them.",
    ),
    "HOST-WORKFLOW-WRITE-ALL": HostBoundaryRule(
        id="HOST-WORKFLOW-WRITE-ALL",
        check_id="SHIP-HOST-BOUNDARY-WORKFLOW-WRITE-ALL",
        title="GitHub workflow grants write-all permissions",
        action="block",
        risk_level="critical",
        recommendation="Replace write-all with the minimal explicit permission scopes.",
    ),
    "HOST-WORKFLOW-PERMISSIONS-EXPANDED": HostBoundaryRule(
        id="HOST-WORKFLOW-PERMISSIONS-EXPANDED",
        check_id="SHIP-HOST-BOUNDARY-WORKFLOW-PERMISSIONS-EXPANDED",
        title="GitHub workflow permissions expanded",
        action="require_review",
        risk_level="high",
        recommendation="Have a human approve the expanded workflow write permission.",
    ),
    "HOST-WORKFLOW-PULL-REQUEST-TARGET-ADDED": HostBoundaryRule(
        id="HOST-WORKFLOW-PULL-REQUEST-TARGET-ADDED",
        check_id="SHIP-HOST-BOUNDARY-PULL-REQUEST-TARGET-ADDED",
        title="GitHub workflow gained a pull_request_target trigger",
        action="require_review",
        risk_level="critical",
        recommendation="Review the pull_request_target trigger; it runs with secrets on fork PRs.",
    ),
}


def evaluate_host_boundary(
    *,
    workspace: Path,
    diff_text: str,
    policy_path: Path | None = None,
) -> tuple[list[AgentResultViolatedRule], list[AgentResultDiagnostic]]:
    """Evaluate host-boundary rules for a unified diff.

    Returns ``(violations, diagnostics)``. Deterministic: diff files are
    iterated in input order, sub-items in sorted order, and the violation
    list is deduped and sorted exactly like the codex boundary evaluator.
    """

    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    policy, diagnostics = load_host_boundary_policy(
        workspace=workspace,
        policy_path=policy_path or DEFAULT_POLICY_PATH,
    )
    violations: list[AgentResultViolatedRule] = []

    def add(rule_id: str, *, path: str | None, evidence: dict[str, Any]) -> None:
        rule = policy.rules.get(rule_id) or DEFAULT_RULES[rule_id]
        violations.append(
            AgentResultViolatedRule(
                id=rule.id,
                check_id=rule.check_id,
                action=rule.action,  # type: ignore[arg-type]
                risk_level=rule.risk_level,
                title=rule.title,
                path=path,
                evidence=evidence,
                recommendation=rule.recommendation,
            )
        )

    for diff_file in diff_files:
        path = diff_file.path
        if not path:
            continue
        normalized = path.replace("\\", "/")
        if _is_mcp_server_path(normalized):
            if diff_file.is_deleted:
                # Removing the declaration shrinks the tool surface; the
                # trust-root touch is still surfaced by SHIP-VERIFY checks.
                continue
            resolved = _resolve_changed_file_text(workspace, diff_file, diagnostics)
            _evaluate_mcp_file(diff_file, resolved, add)
        if _is_claude_settings_path(normalized):
            if diff_file.is_deleted:
                continue
            resolved = _resolve_changed_file_text(workspace, diff_file, diagnostics)
            _evaluate_claude_settings(diff_file, resolved, add)
        if _is_workflow_path(normalized):
            if diff_file.is_deleted:
                # Gate removal is covered by SHIP-VERIFY-CI-GATE-REMOVED;
                # do not duplicate.
                continue
            resolved = _resolve_changed_file_text(workspace, diff_file, diagnostics)
            _evaluate_workflow(diff_file, resolved, add)

    return _dedupe_violations(violations), diagnostics


def load_host_boundary_policy(
    *,
    workspace: Path,
    policy_path: Path,
) -> tuple[HostBoundaryPolicy, list[AgentResultDiagnostic]]:
    diagnostics: list[AgentResultDiagnostic] = []
    candidate = policy_path if policy_path.is_absolute() else workspace / policy_path
    data: dict[str, Any] | None = None
    if candidate.is_file():
        try:
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, yaml.YAMLError) as exc:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="warning",
                    code="policy_load_failed",
                    message=f"Could not load host boundary policy: {exc}",
                    path=_display_path(candidate, workspace),
                )
            )
    elif policy_path == DEFAULT_POLICY_PATH:
        data = _load_packaged_default_policy()
    else:
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code="policy_missing",
                message="Host boundary policy file was not found; using defaults.",
                path=str(policy_path),
            )
        )
    if data is None:
        return HostBoundaryPolicy(
            id="host-boundary-default",
            version=DEFAULT_POLICY_VERSION,
            rules=dict(DEFAULT_RULES),
        ), diagnostics
    rules = dict(DEFAULT_RULES)
    for raw_rule in data.get("rules") or []:
        if not isinstance(raw_rule, dict) or not isinstance(raw_rule.get("id"), str):
            continue
        rule_id = raw_rule["id"]
        base = rules.get(rule_id)
        if base is None:
            continue
        action = str(raw_rule.get("action", base.action))
        if action not in _DECISION_RANK:
            action = "require_review"
        raw_risk = str(raw_rule.get("risk_level", base.risk_level))
        risk: AgentResultRiskLevel = (
            raw_risk if raw_risk in _RISK_RANK else _RISK_BY_ACTION[action]
        )  # type: ignore[assignment]
        rules[rule_id] = HostBoundaryRule(
            id=rule_id,
            check_id=str(raw_rule.get("check_id", base.check_id)),
            title=str(raw_rule.get("title", base.title)),
            action=action,
            risk_level=risk,
            recommendation=str(raw_rule.get("recommendation", base.recommendation)),
        )
    return HostBoundaryPolicy(
        id=str(data.get("id") or "host-boundary"),
        version=str(data.get("version") or DEFAULT_POLICY_VERSION),
        rules=rules,
    ), diagnostics


# --- MCP server declarations -------------------------------------------------


def _evaluate_mcp_file(diff_file, resolved, add) -> None:
    path = diff_file.path
    pair = _parse_json_pair(resolved, path, add)
    if pair is None:
        return
    old_data, new_data = pair
    old_servers = _server_map(old_data)
    new_servers = _server_map(new_data)
    for server_name in sorted(new_servers, key=str):
        server = new_servers[server_name]
        old_server = old_servers.get(server_name)
        if old_server is None:
            add(
                "HOST-MCP-SERVER-ADDED",
                path=path,
                evidence={
                    "kind": "mcp_server_added",
                    "server": str(server_name),
                    "transport_hint": _transport_hint(server),
                    "command_or_url": _command_or_url(server),
                },
            )
            continue
        changed_keys = sorted(
            key
            for key in _MCP_SERVER_KEYS
            if _canonical_json(_config_key(server, key))
            != _canonical_json(_config_key(old_server, key))
        )
        if changed_keys:
            add(
                "HOST-MCP-SERVER-CHANGED",
                path=path,
                evidence={
                    "kind": "mcp_server_changed",
                    "server": str(server_name),
                    "changed_keys": changed_keys,
                },
            )


def _server_map(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    for key in ("mcpServers", "servers"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _config_key(server: Any, key: str) -> Any:
    return server.get(key) if isinstance(server, dict) else None


def _transport_hint(server: Any) -> str:
    if isinstance(server, dict) and ("url" in server or "serverUrl" in server):
        return "url"
    return "stdio"


def _command_or_url(server: Any) -> str | None:
    """First token of the command, or the URL host only — never args/env."""

    if not isinstance(server, dict):
        return None
    url = server.get("url") or server.get("serverUrl")
    if isinstance(url, str) and url.strip():
        return urlsplit(url.strip()).hostname
    command = server.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip().split()[0]
    if isinstance(command, list) and command and isinstance(command[0], str):
        return command[0]
    return None


# --- Claude Code settings ----------------------------------------------------


def _evaluate_claude_settings(diff_file, resolved, add) -> None:
    path = diff_file.path
    pair = _parse_json_pair(resolved, path, add)
    if pair is None:
        return
    old_data, new_data = pair
    permissions = _dict_value(new_data, "permissions")
    old_permissions = _dict_value(old_data, "permissions")
    old_allow = set(_string_entries(old_permissions.get("allow")))
    for rule in sorted(set(_string_entries(permissions.get("allow"))) - old_allow):
        if _is_wildcard_allow(rule):
            add(
                "HOST-PERMISSION-WILDCARD-ALLOW",
                path=path,
                evidence={"kind": "permission_wildcard_allow", "rule": rule},
            )
        else:
            add(
                "HOST-PERMISSION-ALLOW-EXPANDED",
                path=path,
                evidence={"kind": "permission_allow_expanded", "rule": rule},
            )
    new_deny = set(_string_entries(permissions.get("deny")))
    for rule in sorted(set(_string_entries(old_permissions.get("deny"))) - new_deny):
        add(
            "HOST-PERMISSION-DENY-REMOVED",
            path=path,
            evidence={"kind": "permission_deny_removed", "rule": rule},
        )
    hooks = new_data.get("hooks") if isinstance(new_data, dict) else None
    old_hooks = old_data.get("hooks") if isinstance(old_data, dict) else None
    if _canonical_json(hooks) != _canonical_json(old_hooks):
        evidence: dict[str, Any] = {"kind": "hook_changed"}
        events = _changed_hook_events(old_hooks, hooks)
        if events:
            evidence["events"] = events
        add("HOST-HOOK-CHANGED", path=path, evidence=evidence)


def _dict_value(data: Any, key: str) -> dict[str, Any]:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def _string_entries(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _is_wildcard_allow(rule: str) -> bool:
    """Classify wildcard-shaped allow rules (whole-tool grants).

    Wildcard-shaped: ``*``, a bare tool name (``Bash``), or an argument
    that *starts* with ``*`` (``Bash(*)``, ``Bash(*:*)``). A scoped
    prefix rule like ``Bash(npm test:*)`` is the recommended narrow form
    and is NOT wildcard-shaped — the ``*`` only widens within an explicit
    command prefix.
    """
    stripped = rule.strip()
    if stripped == "*":
        return True
    open_paren = stripped.find("(")
    if open_paren == -1:
        # A bare tool name ("Bash", "WebFetch") allows the whole tool.
        return True
    argument = stripped[open_paren + 1 :].lstrip()
    return argument.startswith("*")


def _changed_hook_events(old_hooks: Any, hooks: Any) -> list[str]:
    if not isinstance(hooks, dict) and not isinstance(old_hooks, dict):
        return []
    old_map = old_hooks if isinstance(old_hooks, dict) else {}
    new_map = hooks if isinstance(hooks, dict) else {}
    return sorted(
        str(event)
        for event in {*old_map, *new_map}
        if _canonical_json(old_map.get(event)) != _canonical_json(new_map.get(event))
    )


# --- GitHub workflows ---------------------------------------------------------


def _evaluate_workflow(diff_file, resolved, add) -> None:
    path = diff_file.path
    if resolved.new_text is None:
        add(
            "HOST-CONFIG-PARSE-FAILED",
            path=path,
            evidence={
                "kind": "host_config_content_unresolved",
                "source": resolved.source,
            },
        )
        return
    try:
        new_loaded = yaml.safe_load(resolved.new_text)
    except yaml.YAMLError as exc:
        add(
            "HOST-CONFIG-PARSE-FAILED",
            path=path,
            evidence={"kind": "workflow_yaml_parse_failed", "error": str(exc)},
        )
        return
    if not isinstance(new_loaded, dict):
        add(
            "HOST-CONFIG-PARSE-FAILED",
            path=path,
            evidence={"kind": "workflow_not_a_mapping"},
        )
        return
    if not resolved.old_text:
        old_loaded: Any = {}
    else:
        try:
            old_loaded = yaml.safe_load(resolved.old_text)
        except yaml.YAMLError as exc:
            add(
                "HOST-CONFIG-PARSE-FAILED",
                path=path,
                evidence={"kind": "old_workflow_yaml_parse_failed", "error": str(exc)},
            )
            return
    new_map = _normalize_workflow_keys(new_loaded)
    old_map = _normalize_workflow_keys(old_loaded if isinstance(old_loaded, dict) else {})
    new_triggers = _trigger_names(new_map.get("on"))
    old_triggers = _trigger_names(old_map.get("on"))
    if "pull_request_target" in new_triggers and "pull_request_target" not in old_triggers:
        add(
            "HOST-WORKFLOW-PULL-REQUEST-TARGET-ADDED",
            path=path,
            evidence={"kind": "workflow_pull_request_target_added"},
        )
    _evaluate_workflow_permissions(old_map, new_map, path, add)


def _normalize_workflow_keys(data: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 parses a bare ``on:`` key as boolean True — normalize it
    # back to the literal trigger key name.
    return {("on" if key is True else str(key)): value for key, value in data.items()}


def _trigger_names(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    if isinstance(value, dict):
        return {("on" if key is True else str(key)) for key in value}
    return set()


def _evaluate_workflow_permissions(
    old_map: dict[str, Any],
    new_map: dict[str, Any],
    path: str,
    add,
) -> None:
    contexts: list[tuple[str, Any, Any]] = [
        ("<top-level>", old_map.get("permissions"), new_map.get("permissions")),
    ]
    new_jobs = new_map.get("jobs")
    new_jobs = new_jobs if isinstance(new_jobs, dict) else {}
    old_jobs = old_map.get("jobs")
    old_jobs = old_jobs if isinstance(old_jobs, dict) else {}
    for job_name in sorted(new_jobs, key=str):
        job = new_jobs[job_name]
        if not isinstance(job, dict):
            continue
        old_job = old_jobs.get(job_name)
        old_job = old_job if isinstance(old_job, dict) else {}
        job_perms = job.get("permissions")
        old_job_perms = old_job.get("permissions")
        if job_perms is None and old_job_perms is None:
            # Purely inherited on both sides — the top-level comparison
            # above already covers it; avoid one duplicate per job.
            continue
        new_effective = job_perms if job_perms is not None else new_map.get("permissions")
        old_effective = (
            old_job_perms if old_job_perms is not None else old_map.get("permissions")
        )
        contexts.append((str(job_name), old_effective, new_effective))
    for job_label, old_perms, new_perms in contexts:
        _compare_permissions(old_perms, new_perms, job_label, path, add)


def _compare_permissions(
    old_perms: Any,
    new_perms: Any,
    job: str,
    path: str,
    add,
) -> None:
    if new_perms is None or _canonical_json(new_perms) == _canonical_json(old_perms):
        return
    if isinstance(new_perms, str):
        if new_perms.strip() == "write-all" and not (
            isinstance(old_perms, str) and old_perms.strip() == "write-all"
        ):
            add(
                "HOST-WORKFLOW-WRITE-ALL",
                path=path,
                evidence={"kind": "workflow_write_all", "job": job},
            )
        return
    if not isinstance(new_perms, dict):
        return
    for scope in sorted(new_perms, key=str):
        value = new_perms[scope]
        if not _is_write(value):
            continue
        old_value = _scope_value(old_perms, scope)
        if old_value == "write":
            continue
        add(
            "HOST-WORKFLOW-PERMISSIONS-EXPANDED",
            path=path,
            evidence={
                "kind": "workflow_permissions_expanded",
                "job": job,
                "scope": str(scope),
                "old": old_value,
                "new": str(value).strip(),
            },
        )


def _is_write(value: Any) -> bool:
    return isinstance(value, str) and value.strip() == "write"


def _scope_value(old_perms: Any, scope: Any) -> str | None:
    if isinstance(old_perms, dict):
        value = old_perms.get(scope)
        return value.strip() if isinstance(value, str) else None
    if isinstance(old_perms, str):
        stripped = old_perms.strip()
        if stripped == "write-all":
            return "write"
        if stripped == "read-all":
            return "read"
    return None


# --- Shared helpers ------------------------------------------------------------


def _parse_json_pair(resolved, path: str, add) -> tuple[Any, Any] | None:
    """Parse old/new JSON for a changed host config file.

    Returns ``(old_data, new_data)``; emits ``HOST-CONFIG-PARSE-FAILED``
    and returns ``None`` when either side cannot be parsed or the new
    content cannot be resolved.
    """

    if resolved.new_text is None:
        add(
            "HOST-CONFIG-PARSE-FAILED",
            path=path,
            evidence={
                "kind": "host_config_content_unresolved",
                "source": resolved.source,
            },
        )
        return None
    try:
        new_data = json.loads(resolved.new_text)
    except json.JSONDecodeError as exc:
        add(
            "HOST-CONFIG-PARSE-FAILED",
            path=path,
            evidence={"kind": "json_parse_failed", "error": str(exc)},
        )
        return None
    if not resolved.old_text:
        old_data: Any = {}
    else:
        try:
            old_data = json.loads(resolved.old_text)
        except json.JSONDecodeError as exc:
            add(
                "HOST-CONFIG-PARSE-FAILED",
                path=path,
                evidence={"kind": "old_json_parse_failed", "error": str(exc)},
            )
            return None
    return old_data, new_data


def _load_packaged_default_policy() -> dict[str, Any] | None:
    candidate = (
        Path(__file__).resolve().parents[1]
        / "_meta"
        / "policies"
        / "host-boundary.shipgate.yaml"
    )
    try:
        if candidate.is_file():
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            return loaded if isinstance(loaded, dict) else None
    except (OSError, yaml.YAMLError):
        return None
    return None


# --- Path classification --------------------------------------------------------
#
# Host-boundary semantics evaluate REPO-ROOT host configuration only. A
# copy nested under samples/, fixtures/, or another package directory is
# not a grant the host actually loads — GitHub executes only the root
# `.github/workflows/`, and Claude Code reads the root `.mcp.json` /
# `.claude/settings*.json` for the opened workspace. Nested copies stay
# covered by the *coarse* trust-root layer (`checks/verify.py` uses
# `**/`-wide globs and routes to review); only the fine, block-capable
# layer here is root-anchored. The repo's own dogfood verifier caught
# exactly this false positive on the fixture sample workflow.


def _is_mcp_server_path(path: str) -> bool:
    return path in (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json")


def _is_claude_settings_path(path: str) -> bool:
    return path in (".claude/settings.json", ".claude/settings.local.json")


def _is_workflow_path(path: str) -> bool:
    if not (path.endswith(".yml") or path.endswith(".yaml")):
        return False
    if not path.startswith(".github/workflows/"):
        # Nested copies (samples/x/.github/workflows/…) never execute;
        # see the root-anchoring note above.
        return False
    remainder = path[len(".github/workflows/") :]
    # GitHub only runs workflows directly inside .github/workflows/.
    return bool(remainder) and "/" not in remainder
