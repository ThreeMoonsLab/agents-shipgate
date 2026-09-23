"""Host-boundary governance for coding-agent HOST configuration changes.

Mirrors the ``agents_shipgate.core.codex_boundary`` diff-aware check
architecture for the host side of the boundary: Claude Code settings
(``.claude/settings.json`` / ``.claude/settings.local.json``), project
MCP server declarations (``.mcp.json``, ``.cursor/mcp.json``,
``.vscode/mcp.json``), and GitHub workflow permission expansion
(``.github/workflows/*.yml|yaml``).

NOTE: the shared diff plumbing (``parse_unified_diff``,
``_resolve_changed_file_text``, the canonical-JSON helper) now lives in
``agents_shipgate.core.boundary_diff`` and is imported from there. Only
the policy/decision helpers (rank tables, dedupe, display path) are
still imported from the sibling ``codex_boundary`` module.

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

from agents_shipgate.core.boundary_diff import (
    DiffFile,
    ResolvedFileText,
    _canonical_json,
    _resolve_changed_file_text,
    parse_unified_diff,
)
from agents_shipgate.core.boundary_registry import (
    boundary_adapters_for_path,
    is_agent_boundary_path,
)
from agents_shipgate.core.codex_boundary import (
    _DECISION_RANK,
    _RISK_BY_ACTION,
    _RISK_RANK,
    DEFAULT_POLICY_VERSION,
    _dedupe_violations,
    _display_path,
)
from agents_shipgate.core.host_settings import (
    CLAUDE_SCALAR_SETTINGS,
    claude_setting_values,
    rate_claude_setting,
    read_at_top_level,
    setting_value_text,
)
from agents_shipgate.core.jsonc import is_vscode_mcp_path, loads_jsonc
from agents_shipgate.core.permission_lattice import (
    names_tools_within_one_mcp_server,
    subsumes,
    whole_tool_risk,
)
from agents_shipgate.core.trust_roots import read_absolute_identity_bound_text
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultDiagnostic,
    AgentResultRiskLevel,
    AgentResultViolatedRule,
)

DEFAULT_POLICY_PATH = Path("policies/host-boundary.shipgate.yaml")

# Server-config keys whose change re-shapes what the host will execute or
# connect to. Env var VALUES never reach evidence — only the key name
# ``env`` appears in ``changed_keys``.
_MCP_SERVER_KEYS = (
    "alwaysAllow",
    "args",
    "command",
    "disabled",
    "env",
    "excludeTools",
    "headers",
    "includeTools",
    "serverUrl",
    "tools",
    "url",
)


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
        title="Coding-agent allow rule grants a wildcard tool surface",
        action="block",
        risk_level="critical",
        recommendation="Do not allow wildcard tool permissions; scope the rule to specific commands.",
    ),
    "HOST-PERMISSION-ALLOW-EXPANDED": HostBoundaryRule(
        id="HOST-PERMISSION-ALLOW-EXPANDED",
        check_id="SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED",
        title="Coding-agent permission allowlist expanded",
        action="require_review",
        risk_level="high",
        recommendation="Have a human approve the new permission allow rule.",
    ),
    "HOST-PERMISSION-DENY-REMOVED": HostBoundaryRule(
        id="HOST-PERMISSION-DENY-REMOVED",
        check_id="SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED",
        title="Coding-agent permission deny rule removed",
        action="require_review",
        risk_level="high",
        recommendation="Have a human confirm the removed deny rule is no longer needed.",
    ),
    "HOST-HOOK-CHANGED": HostBoundaryRule(
        id="HOST-HOOK-CHANGED",
        check_id="SHIP-HOST-BOUNDARY-HOOK-CHANGED",
        title="Coding-agent executable hooks changed",
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
    policy_override: HostBoundaryPolicy | None = None,
    diff_files_override: list[DiffFile] | None = None,
    resolved_text_cache: dict[str, ResolvedFileText] | None = None,
    static_read_cache: Any | None = None,
) -> tuple[list[AgentResultViolatedRule], list[AgentResultDiagnostic]]:
    """Evaluate host-boundary rules for a unified diff.

    Returns ``(violations, diagnostics)``. Deterministic: diff files are
    iterated in input order, sub-items in sorted order, and the violation
    list is deduped and sorted exactly like the codex boundary evaluator.
    """

    workspace = workspace.resolve()
    diff_files = (
        diff_files_override
        if diff_files_override is not None
        else parse_unified_diff(diff_text)
    )
    if policy_override is None:
        policy, diagnostics = load_host_boundary_policy(
            workspace=workspace,
            policy_path=policy_path or DEFAULT_POLICY_PATH,
        )
    else:
        policy = policy_override
        diagnostics = []
    violations: list[AgentResultViolatedRule] = []
    resolved_text_cache = resolved_text_cache if resolved_text_cache is not None else {}

    def resolve(diff_file: DiffFile) -> ResolvedFileText:
        path = diff_file.path
        if path not in resolved_text_cache:
            resolved_text_cache[path] = _resolve_changed_file_text(
                workspace, diff_file, diagnostics, static_read_cache
            )
        return resolved_text_cache[path]

    def add(
        rule_id: str,
        *,
        path: str | None,
        evidence: dict[str, Any],
        rating: AgentResultRiskLevel | None = None,
    ) -> None:
        rule = policy.rules.get(rule_id) or DEFAULT_RULES[rule_id]
        violations.append(
            AgentResultViolatedRule(
                id=rule.id,
                check_id=rule.check_id,
                action=rule.action,  # type: ignore[arg-type]
                risk_level=_rated_risk(rule, rating),
                title=rule.title,
                path=path,
                evidence=evidence,
                recommendation=rule.recommendation,
            )
        )

    def note_narrowing(
        diff_file: DiffFile, resolved: ResolvedFileText, raised: int, *, cursor: bool
    ) -> None:
        # A settings change that raised nothing, read cleanly, and only takes
        # authority away has been evaluated completely, so neither the
        # protected-surface fallback nor the trust-root touch needs a human
        # for it (#661). The diagnostic keeps it on the record.
        if len(violations) != raised or any(
            item.path == diff_file.path and item.level != "info" for item in diagnostics
        ):
            return
        if settings_change_only_narrows(resolved, cursor=cursor):
            diagnostics.append(
                AgentResultDiagnostic(
                    level="info",
                    code=HOST_SETTINGS_NARROWED,
                    message=(
                        "This host settings change only narrows permissions: every "
                        "added allow rule is covered by a rule the file already had, "
                        "and no deny or ask rule was removed. It needs no human review."
                    ),
                    path=diff_file.path,
                )
            )

    for diff_file in diff_files:
        path = diff_file.path
        if not path:
            continue
        normalized = path.replace("\\", "/")
        if _is_mcp_server_path(normalized):
            resolved = resolve(diff_file)
            _evaluate_mcp_file(diff_file, resolved, add)
        if _is_claude_settings_path(normalized):
            resolved = resolve(diff_file)
            raised = len(violations)
            _evaluate_claude_settings(diff_file, resolved, add)
            note_narrowing(diff_file, resolved, raised, cursor=False)
        if _is_cursor_settings_path(normalized):
            resolved = resolve(diff_file)
            raised = len(violations)
            _evaluate_cursor_settings(diff_file, resolved, add)
            note_narrowing(diff_file, resolved, raised, cursor=True)
        if _is_workflow_path(normalized):
            resolved = resolve(diff_file)
            _evaluate_workflow(diff_file, resolved, add)

    return _dedupe_violations(violations), diagnostics


def _rated_risk(
    rule: HostBoundaryRule, rating: AgentResultRiskLevel | None
) -> AgentResultRiskLevel:
    """A violation's risk: the rule's, or the setting table's rating (#827).

    A modelled Claude Code setting carries the rating its grant and row carry,
    so `check` and `diff` rate one value alike. A policy may still only raise:
    where it raised the rule above the engine default, the higher of the two
    applies.
    """

    if rating is None:
        return rule.risk_level
    default = DEFAULT_RULES[rule.id].risk_level if rule.id in DEFAULT_RULES else rule.risk_level
    if _RISK_RANK[rule.risk_level] > _RISK_RANK[default]:
        return max(rating, rule.risk_level, key=_RISK_RANK.__getitem__)
    return rating


def load_host_boundary_policy(
    *,
    workspace: Path,
    policy_path: Path,
    policy_text: str | None = None,
) -> tuple[HostBoundaryPolicy, list[AgentResultDiagnostic]]:
    diagnostics: list[AgentResultDiagnostic] = []
    candidate = policy_path if policy_path.is_absolute() else workspace / policy_path
    data: dict[str, Any] | None = None
    if candidate.is_file():
        try:
            loaded = yaml.safe_load(
                policy_text
                if policy_text is not None
                else read_absolute_identity_bound_text(
                    candidate,
                    max_bytes=1024 * 1024,
                )
            ) or {}
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="warning",
                    code="policy_load_failed",
                    message=(
                        "Could not load host boundary policy "
                        f"({type(exc).__name__})."
                    ),
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
        if _DECISION_RANK[action] < _DECISION_RANK[base.action]:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="error",
                    code="policy_safety_floor_downgrade",
                    message=(
                        f"Host boundary policy rule {rule_id!r} cannot lower "
                        f"action below {base.action}."
                    ),
                    path=_display_path(candidate, workspace),
                )
            )
            action = base.action
        raw_risk = str(raw_rule.get("risk_level", base.risk_level))
        risk: AgentResultRiskLevel = (
            raw_risk if raw_risk in _RISK_RANK else _RISK_BY_ACTION[action]
        )  # type: ignore[assignment]
        if _RISK_RANK[risk] < _RISK_RANK[base.risk_level]:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="error",
                    code="policy_safety_floor_downgrade",
                    message=(
                        f"Host boundary policy rule {rule_id!r} cannot lower "
                        f"risk below {base.risk_level}."
                    ),
                    path=_display_path(candidate, workspace),
                )
            )
            risk = base.risk_level
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
    _evaluate_unknown_keys(
        old_data,
        new_data,
        path,
        allowed={
            "$schema",
            "apiKeyHelper",
            "attribution",
            "autoUpdatesChannel",
            "cleanupPeriodDays",
            "companyAnnouncements",
            "env",
            "forceLoginMethod",
            "forceLoginOrgUUID",
            "hooks",
            "includeCoAuthoredBy",
            "language",
            "model",
            "permissions",
            "plugins",
            "sandbox",
            "statusLine",
            # A setting the grant reader models is rated, not unknown (#827).
            # One also set under `permissions` is not read at the top level,
            # by the reader or here, so it stays an unknown key.
            *(
                str(key)
                for key in (new_data if isinstance(new_data, dict) else {})
                if read_at_top_level(new_data, str(key))
            ),
        },
        add=add,
    )
    permissions = _dict_value(new_data, "permissions")
    old_permissions = _dict_value(old_data, "permissions")
    _evaluate_unknown_permission_keys(old_permissions, permissions, path, add)
    _evaluate_claude_setting_values(old_data, new_data, path, add)
    old_allow = set(_string_entries(old_permissions.get("allow")))
    for rule in sorted(set(_string_entries(permissions.get("allow"))) - old_allow):
        if not _widens_allow(rule, old_allow):
            continue
        rule_id = _allow_rule_id(rule)
        add(
            rule_id,
            path=path,
            evidence={
                "kind": (
                    "permission_wildcard_allow"
                    if rule_id == "HOST-PERMISSION-WILDCARD-ALLOW"
                    else "permission_allow_expanded"
                ),
                "rule": _safe_rule(rule),
            },
        )
    new_deny = set(_string_entries(permissions.get("deny")))
    for rule in sorted(set(_string_entries(old_permissions.get("deny"))) - new_deny):
        add(
            "HOST-PERMISSION-DENY-REMOVED",
            path=path,
            evidence={"kind": "permission_deny_removed", "rule": _safe_rule(rule)},
        )
    hooks = new_data.get("hooks") if isinstance(new_data, dict) else None
    old_hooks = old_data.get("hooks") if isinstance(old_data, dict) else None
    if _canonical_json(hooks) != _canonical_json(old_hooks):
        evidence: dict[str, Any] = {"kind": "hook_changed"}
        events = _changed_hook_events(old_hooks, hooks)
        if events:
            evidence["events"] = events
        add("HOST-HOOK-CHANGED", path=path, evidence=evidence)

    for key in ("sandbox", "plugins"):
        if _canonical_json(new_data.get(key)) != _canonical_json(old_data.get(key)):
            add(
                "HOST-PERMISSION-ALLOW-EXPANDED",
                path=path,
                evidence={"kind": f"claude_{key}_changed"},
            )


def _evaluate_cursor_settings(diff_file, resolved, add) -> None:
    """Evaluate Cursor CLI grants using the shared permission lattice."""

    path = diff_file.path
    pair = _parse_json_pair(resolved, path, add)
    if pair is None:
        return
    old_data, new_data = pair
    _evaluate_unknown_keys(
        old_data,
        new_data,
        path,
        allowed={"$schema", "permissions", "shell", "read", "write", "network"},
        add=add,
    )
    permissions = _dict_value(new_data, "permissions")
    old_permissions = _dict_value(old_data, "permissions")
    for key in sorted(set(permissions) - {"allow", "deny"}):
        if _canonical_json(permissions.get(key)) != _canonical_json(
            old_permissions.get(key)
        ):
            add(
                "HOST-PERMISSION-ALLOW-EXPANDED",
                path=path,
                evidence={"kind": "cursor_permission_boundary_changed", "key": key},
            )
    if _canonical_json(new_data.get("network")) != _canonical_json(
        old_data.get("network")
    ):
        add(
            "HOST-PERMISSION-ALLOW-EXPANDED",
            path=path,
            evidence={"kind": "cursor_network_boundary_changed"},
        )
    # Cursor has shipped both nested permission arrays and category-specific
    # top-level arrays.  Normalize both without executing the host.
    for key in ("allow", "deny"):
        values = _string_entries(permissions.get(key))
        old_values = _string_entries(old_permissions.get(key))
        for category in ("shell", "read", "write"):
            values.extend(_string_entries(new_data.get(category)))
            old_values.extend(_string_entries(old_data.get(category)))
        if key == "allow":
            for rule in sorted(set(values) - set(old_values)):
                if not _widens_allow(rule, old_values):
                    continue
                add(
                    _allow_rule_id(rule),
                    path=path,
                    evidence={
                        "kind": "cursor_permission_allow_expanded",
                        "rule": _safe_rule(rule),
                    },
                )
        else:
            for rule in sorted(set(old_values) - set(values)):
                add(
                    "HOST-PERMISSION-DENY-REMOVED",
                    path=path,
                    evidence={
                        "kind": "cursor_permission_deny_removed",
                        "rule": _safe_rule(rule),
                    },
                )


def _evaluate_claude_setting_values(old_data, new_data, path: str, add) -> None:
    """One violation per modelled setting value the change sets, at its rating (#827).

    The rating is the one the grant and its row carry (``core.host_settings``):
    a ``critical`` value raises the wildcard rule, and any other value the
    allowlist rule at its own rating, so ``dontAsk`` is reviewed at
    ``medium`` rather than blocked, and ``enableAllProjectMcpServers: true``
    blocks rather than reading as a key that could not be parsed. A value the
    change leaves as it was raises nothing, and neither does a removal, as a
    removed ``defaultMode`` never did. ``defaultMode`` keeps the ``mode``
    evidence it always published, so its findings keep their fingerprints;
    every other setting names itself and its value. Either value is the one
    the grant publishes, redacted and bounded (`_setting_evidence_text`).

    Two sides compare by where the value is read as well as by its setting,
    so a value that moves between ``permissions`` and the top level is a
    value the change sets, in either direction, and raises at its rating.
    The reader accepts a scalar setting in either place, but Claude Code
    documents each in one of them (``defaultMode`` under ``permissions``,
    ``enableAllProjectMcpServers`` at the top level), so moving a
    ``bypassPermissions`` copy the base kept at the top level into
    ``permissions`` turns the mode on without changing its value.
    """

    old_values = {
        (item.container, item.key): item.value for item in claude_setting_values(old_data)
    }
    for item in claude_setting_values(new_data):
        compared = (item.container, item.key)
        if compared in old_values and _canonical_json(old_values[compared]) == _canonical_json(
            item.value
        ):
            continue
        rating = rate_claude_setting(item.setting, item.value)
        critical = rating.risk == "critical"
        evidence: dict[str, Any] = {
            "kind": "permission_mode_expanded" if critical else "permission_mode_changed"
        }
        if item.setting == "defaultMode":
            evidence["mode"] = _setting_evidence_text(item.setting, item.value)
        else:
            evidence["setting"] = item.setting
            evidence["value"] = _setting_evidence_text(item.setting, item.value)
        add(
            "HOST-PERMISSION-WILDCARD-ALLOW" if critical else "HOST-PERMISSION-ALLOW-EXPANDED",
            path=path,
            evidence=evidence,
            rating=rating.risk,  # type: ignore[arg-type]
        )


#: The longest setting value a violation's evidence publishes. A documented
#: value is a word or a server name; a longer one is a shape Claude Code does
#: not document, and its whole redacted value is its grant's (`audit --host`).
_MAX_SETTING_EVIDENCE_CHARS = 200


def _setting_evidence_text(setting: str, value: Any) -> str:
    """A setting value as ``check`` evidence publishes it: its grant's, redacted and bounded.

    The grant redacts a value before it renders it (``_setting_grant``).
    Rendering first put an object's credentials inside one string, where the
    evidence sanitizer's key-based redaction cannot see them, so an
    ``enabledMcpjsonServers`` object holding an ``env`` token published the
    token in ``check`` alone. The same redactor runs here first, with the same
    parent key, and the text is bounded only after it, so truncation never
    exposes part of a secret. ``defaultMode`` is rendered as its grant's
    ``value`` is, the way its ``mode`` evidence always was, so a documented
    mode keeps its fingerprint; every other setting is spelled as its row
    spells it.
    """

    # Imported here: host_grants imports this module.
    from agents_shipgate.core.host_grants import _redact_secret_values

    redacted = _redact_secret_values(value, parent_key=setting)
    if setting == "defaultMode" and not isinstance(redacted, (dict, list)):
        text = str(redacted)
    else:
        text = setting_value_text(setting, redacted)
    if len(text) > _MAX_SETTING_EVIDENCE_CHARS:
        text = text[: _MAX_SETTING_EVIDENCE_CHARS - 1] + "…"
    return text


def _evaluate_unknown_permission_keys(old_permissions, permissions, path: str, add) -> None:
    # A modelled setting under `permissions` is rated by
    # `_evaluate_claude_setting_values`, not reported as a boundary change.
    passive = {"allow", "ask", "deny", *CLAUDE_SCALAR_SETTINGS}
    for key in sorted(set(permissions) - passive):
        if _canonical_json(permissions.get(key)) == _canonical_json(
            old_permissions.get(key)
        ):
            continue
        add(
            "HOST-PERMISSION-ALLOW-EXPANDED",
            path=path,
            evidence={"kind": "claude_permission_boundary_changed", "key": key},
        )


def _evaluate_unknown_keys(
    old_data: Any,
    new_data: Any,
    path: str,
    *,
    allowed: set[str],
    add,
) -> None:
    if not isinstance(new_data, dict):
        return
    old_map = old_data if isinstance(old_data, dict) else {}
    for key in sorted(set(new_data) - allowed):
        if _canonical_json(new_data.get(key)) != _canonical_json(old_map.get(key)):
            add(
                "HOST-CONFIG-PARSE-FAILED",
                path=path,
                evidence={"kind": "unknown_host_config_key", "key": str(key)},
            )


def _dict_value(data: Any, key: str) -> dict[str, Any]:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def _string_entries(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _widens_allow(rule: str, old_rules) -> bool:
    """Whether an added allow rule grants anything the old list did not (#661).

    Rules are compared by text, so tightening `Bash(*)` to `Bash(git status)`
    arrives as an addition, and set difference alone called it an expanded
    allowlist. In an adopted repository that sent every narrowing to a human.
    An added rule that an old rule already subsumes grants nothing new. The
    drift reader and the audit table already read direction from this lattice
    (#657); the boundary check now does too. Only a decided ``True`` excuses a
    rule, so a pair the lattice cannot decide stays an expansion.
    """

    return not any(subsumes(old, rule) is True for old in old_rules)


HOST_SETTINGS_NARROWED = "host_settings_narrowed"
_CURSOR_RULE_CATEGORIES = ("shell", "read", "write")


def settings_change_only_narrows(resolved: ResolvedFileText, *, cursor: bool) -> bool:
    """Whether a host settings change can only take authority away (#661).

    True when the old and new JSON agree on everything except the rule lists,
    every added allow rule is subsumed by an old allow rule (a decided lattice
    ``True``), and no deny rule, nor for Claude Code an ask rule, was removed.
    Anything else is not a narrowing: a hook or any other key that changed, a
    rule list that is not a list of rule strings, a deleted or unresolved
    file, or an added rule the lattice cannot decide.
    """

    if resolved.new_text is None or resolved.source == "diff_deleted_file":
        return False
    try:
        new_data = json.loads(resolved.new_text)
        old_data = json.loads(resolved.old_text) if resolved.old_text else {}
    except json.JSONDecodeError:
        return False
    if not isinstance(old_data, dict) or not isinstance(new_data, dict):
        return False
    keys = ("allow", "deny") if cursor else ("allow", "ask", "deny")
    old_rules = _rule_lists(old_data, keys)
    new_rules = _rule_lists(new_data, keys)
    if old_rules is None or new_rules is None:
        return False
    if _canonical_json(_without_rule_lists(old_data, keys)) != _canonical_json(
        _without_rule_lists(new_data, keys)
    ):
        return False
    old_allow = list(old_rules["allow"])
    if cursor:
        # The Cursor evaluator reads top-level category arrays as allow rules
        # too; they are unchanged here, so they cover exactly as they did.
        for category in _CURSOR_RULE_CATEGORIES:
            old_allow.extend(_string_entries(old_data.get(category)))
    if any(
        _widens_allow(rule, old_allow)
        for rule in set(new_rules["allow"]) - set(old_rules["allow"])
    ):
        return False
    return all(set(old_rules[key]) <= set(new_rules[key]) for key in keys if key != "allow")


def _rule_lists(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, list[str]] | None:
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        return None
    lists: dict[str, list[str]] = {}
    for key in keys:
        value = permissions.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            return None
        lists[key] = value
    return lists


def _without_rule_lists(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    stripped = dict(data)
    permissions = stripped.pop("permissions", None)
    if isinstance(permissions, dict):
        rest = {key: value for key, value in permissions.items() if key not in keys}
        if rest:
            stripped["permissions"] = rest
    return stripped


def is_host_settings_path(path: str) -> bool:
    """A host settings file whose permission rules the lattice reads."""

    return _is_claude_settings_path(path) or _is_cursor_settings_path(path)


def _is_wildcard_allow(rule: str) -> bool:
    """Classify wildcard-shaped allow rules (whole-tool grants).

    Wildcard-shaped: ``*``, a bare tool name (``Bash``), or an argument
    that *starts* with ``*`` (``Bash(*)``, ``Bash(*:*)``). A scoped
    prefix rule like ``Bash(npm test:*)`` is the recommended narrow form
    and is NOT wildcard-shaped — the ``*`` only widens within an explicit
    command prefix. Nor is an MCP rule naming tools within one server
    (``mcp__github__get_issue``): it is a bare token, but not a whole
    tool surface. ``mcp__github`` and ``mcp__github__*`` still are (#816).
    """
    stripped = rule.strip()
    if stripped == "*":
        return True
    open_paren = stripped.find("(")
    if open_paren == -1:
        # A bare tool name ("Bash", "WebFetch") allows the whole tool. Every
        # MCP rule is spelled as a bare token too, and one naming a single
        # tool was rated `high`, worded "matches every target of this kind",
        # and blocked through the wildcard check (#816).
        return not names_tools_within_one_mcp_server(stripped)
    argument = stripped[open_paren + 1 :].lstrip()
    return argument.startswith("*")


def _allow_rule_id(rule: str) -> str:
    """Which finding a newly-added allow rule earns.

    `_is_wildcard_allow` answers *whether* a rule grants a whole tool, not
    *what that tool reaches*, and the blocking rule was wired straight to
    it. So adding `Read(**)` — the ordinary configuration for a coding
    agent that already has the workspace checked out — blocked the
    release at `critical`, exactly as `Bash(*)` does. A gate that stops
    the release over reading files is one a team turns off, and a gate
    that is off catches no `Bash(*)` either.

    The tool class is the discriminator, taken from the same lattice the
    audit table uses so the gate and the table cannot drift apart (#657).
    """

    if not _is_wildcard_allow(rule):
        return "HOST-PERMISSION-ALLOW-EXPANDED"
    _, risk = whole_tool_risk(rule)
    # Still an expansion, still reviewed — just not a release blocker.
    return (
        "HOST-PERMISSION-ALLOW-EXPANDED"
        if risk == "low"
        else "HOST-PERMISSION-WILDCARD-ALLOW"
    )


def _safe_rule(rule: str) -> str:
    stripped = rule.strip()
    open_paren = stripped.find("(")
    if open_paren == -1:
        return stripped
    tool = stripped[:open_paren]
    argument = stripped[open_paren + 1 :].rstrip(")").strip()
    if argument == "*":
        return f"{tool}(*)"
    if argument.startswith("*"):
        return f"{tool}(<wildcard>)"
    return f"{tool}(<redacted-arguments>)"


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
    if diff_file.is_deleted:
        new_loaded = {}
    else:
        try:
            new_loaded = yaml.safe_load(resolved.new_text)
        except yaml.YAMLError as exc:
            add(
                "HOST-CONFIG-PARSE-FAILED",
                path=path,
                evidence=_parser_error("workflow_yaml_parse_failed", exc),
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
                evidence=_parser_error("old_workflow_yaml_parse_failed", exc),
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


def _workflow_label(value: Any) -> str:
    """A job id or permission scope name as evidence may publish it (#802).

    The raw text still decides which jobs and scopes compare; only the
    evidence is redacted, by the rule host grants publish every job label with.
    Two jobs whose ids redact alike may then raise identical violations, which
    dedupe into one: the rule, path and decision they carry are the same.
    """

    # Imported here: host_grants imports this module.
    from agents_shipgate.core.host_grants import published_workflow_label

    return published_workflow_label(str(value))


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
                evidence={"kind": "workflow_write_all", "job": _workflow_label(job)},
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
                "job": _workflow_label(job),
                "scope": _workflow_label(scope),
                "old": old_value,
                "new": str(value).strip(),
            },
        )


def _is_write(value: Any) -> bool:
    return isinstance(value, str) and value.strip() == "write"


#: The access levels GitHub accepts for one permission scope.
_SCOPE_LEVELS = frozenset({"read", "write", "none"})


def _scope_value(old_perms: Any, scope: Any) -> str | None:
    """The earlier level of ``scope``, as evidence may publish it.

    Only a level GitHub accepts is published. Any other text, which GitHub
    rejects, is ``None``, as a non-string level is, so evidence never repeats
    free text from the declaration (#802).
    """
    if isinstance(old_perms, dict):
        value = old_perms.get(scope)
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped if stripped in _SCOPE_LEVELS else None
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

    # VS Code reads `mcp.json` as JSON with comments (#659).
    loads = loads_jsonc if is_vscode_mcp_path(path) else json.loads
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
    if resolved.source == "diff_deleted_file" and resolved.new_text == "":
        new_data = {}
    else:
        try:
            new_data = loads(resolved.new_text)
        except json.JSONDecodeError as exc:
            add(
                "HOST-CONFIG-PARSE-FAILED",
                path=path,
                evidence=_parser_error("json_parse_failed", exc),
            )
            return None
    if not resolved.old_text:
        old_data: Any = {}
    else:
        try:
            old_data = loads(resolved.old_text)
        except json.JSONDecodeError as exc:
            add(
                "HOST-CONFIG-PARSE-FAILED",
                path=path,
                evidence=_parser_error("old_json_parse_failed", exc),
            )
            return None
    return old_data, new_data


def _parser_error(kind: str, exc: Exception) -> dict[str, Any]:
    evidence: dict[str, Any] = {"kind": kind, "parser": type(exc).__name__}
    line = getattr(exc, "lineno", None)
    column = getattr(exc, "colno", None)
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        line = getattr(mark, "line", -1) + 1
        column = getattr(mark, "column", -1) + 1
    if isinstance(line, int) and line > 0:
        evidence["line"] = line
    if isinstance(column, int) and column > 0:
        evidence["column"] = column
    return evidence


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
    normalized = path.replace("\\", "/").removeprefix("./").casefold()
    return normalized in {".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json"}


def _is_claude_settings_path(path: str) -> bool:
    normalized = path.replace("\\", "/").removeprefix("./").casefold()
    return normalized in {
        ".claude/settings.json",
        ".claude/settings.local.json",
    }


def _is_cursor_settings_path(path: str) -> bool:
    normalized = path.replace("\\", "/").removeprefix("./").casefold()
    return normalized == ".cursor/cli.json"


def is_host_boundary_path(path: str) -> bool:
    """Compatibility predicate backed by the central adapter registry."""

    return is_agent_boundary_path(path)


def _has_boundary_adapter(path: str, adapter_id: str) -> bool:
    return any(item.id == adapter_id for item in boundary_adapters_for_path(path))


def _is_workflow_path(path: str) -> bool:
    if not (path.endswith(".yml") or path.endswith(".yaml")):
        return False
    if not _has_boundary_adapter(path, "shared"):
        # Nested copies (samples/x/.github/workflows/…) never execute;
        # see the root-anchoring note above.
        return False
    remainder = path[len(".github/workflows/") :]
    # GitHub only runs workflows directly inside .github/workflows/.
    return bool(remainder) and "/" not in remainder
