from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultDiagnostic,
    AgentResultNextAction,
    AgentResultRiskLevel,
    AgentResultV1,
    AgentResultViolatedRule,
)

DEFAULT_POLICY_PATH = Path("policies/codex-boundary.shipgate.yaml")
DEFAULT_POLICY_VERSION = "1"

_DECISION_RANK = {"allow": 0, "warn": 1, "require_review": 2, "block": 3}
_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_RISK_BY_ACTION: dict[str, AgentResultRiskLevel] = {
    "allow": "none",
    "warn": "low",
    "require_review": "medium",
    "block": "critical",
}

_SHIPGATE_TERMS = (
    "agents-shipgate",
    "agents_shipgate",
    "shipgate check",
    "shipgate verify",
    "shipgate scan",
)
_RISKY_TOOL_RE = re.compile(
    r"(write|delete|remove|update|create|edit|patch|apply|run|exec|execute|"
    r"send|post|put|merge|deploy|approve|commit|push|release|publish|refund|"
    r"cancel|transfer|payment)",
    re.IGNORECASE,
)
_COMMAND_SKILL_RE = re.compile(
    r"(exec_command|write_stdin|apply_patch|shell|subprocess|python\s|node\s|"
    r"bash\s|sh\s|scripts?/|command:|cmd:|run:)",
    re.IGNORECASE,
)

_PERMISSION_PROFILE_KEYS = {
    "description",
    "extends",
    "workspace_roots",
    "filesystem",
    "network",
}
_NETWORK_KEYS = {
    "enabled",
    "proxy_url",
    "admin_url",
    "enable_socks5",
    "socks_url",
    "enable_socks5_udp",
    "allow_upstream_proxy",
    "dangerously_allow_non_loopback_proxy",
    "dangerously_allow_non_loopback_admin",
    "dangerously_allow_all_unix_sockets",
    "mode",
    "allow_local_binding",
    "domains",
    "unix_sockets",
}


@dataclass(frozen=True)
class DiffFile:
    old_path: str | None
    new_path: str | None
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    is_deleted: bool = False
    is_new: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass(frozen=True)
class CodexBoundaryRule:
    id: str
    check_id: str
    title: str
    action: str
    risk_level: AgentResultRiskLevel
    recommendation: str


@dataclass(frozen=True)
class CodexBoundaryPolicy:
    id: str
    version: str
    rules: dict[str, CodexBoundaryRule]


DEFAULT_RULES: dict[str, CodexBoundaryRule] = {
    "CODEX-CONFIG-PARSE-FAILED": CodexBoundaryRule(
        id="CODEX-CONFIG-PARSE-FAILED",
        check_id="SHIP-CODEX-BOUNDARY-CONFIG-PARSE-FAILED",
        title="Codex config could not be parsed",
        action="require_review",
        risk_level="medium",
        recommendation="Fix the TOML or have a human review the Codex boundary change.",
    ),
    "CODEX-UNKNOWN-PERMISSION-KEY": CodexBoundaryRule(
        id="CODEX-UNKNOWN-PERMISSION-KEY",
        check_id="SHIP-CODEX-BOUNDARY-UNKNOWN-PERMISSION-KEY",
        title="Unknown Codex permission key",
        action="require_review",
        risk_level="medium",
        recommendation="Review the unknown permission key before trusting the boundary.",
    ),
    "CODEX-NETWORK-WILDCARD": CodexBoundaryRule(
        id="CODEX-NETWORK-WILDCARD",
        check_id="SHIP-CODEX-BOUNDARY-NETWORK-WILDCARD",
        title="Codex network profile allows a wildcard domain",
        action="require_review",
        risk_level="high",
        recommendation="Replace wildcard network access with explicit allowed domains.",
    ),
    "CODEX-NETWORK-EXPANDED": CodexBoundaryRule(
        id="CODEX-NETWORK-EXPANDED",
        check_id="SHIP-CODEX-BOUNDARY-NETWORK-EXPANDED",
        title="Codex network access expanded",
        action="require_review",
        risk_level="high",
        recommendation="Have a human approve the expanded Codex network boundary.",
    ),
    "CODEX-DANGER-FULL-ACCESS": CodexBoundaryRule(
        id="CODEX-DANGER-FULL-ACCESS",
        check_id="SHIP-CODEX-BOUNDARY-DANGER-FULL-ACCESS",
        title="Codex full-access sandbox selected",
        action="require_review",
        risk_level="critical",
        recommendation="Use a narrower permission profile or get explicit human approval.",
    ),
    "CODEX-MCP-AUTO-APPROVE-WRITE": CodexBoundaryRule(
        id="CODEX-MCP-AUTO-APPROVE-WRITE",
        check_id="SHIP-CODEX-BOUNDARY-MCP-AUTO-APPROVE-WRITE",
        title="Codex MCP write tool is auto-approved",
        action="block",
        risk_level="critical",
        recommendation="Do not auto-approve write/destructive MCP tools.",
    ),
    "CODEX-MCP-AUTO-APPROVE-UNKNOWN": CodexBoundaryRule(
        id="CODEX-MCP-AUTO-APPROVE-UNKNOWN",
        check_id="SHIP-CODEX-BOUNDARY-MCP-AUTO-APPROVE-UNKNOWN",
        title="Codex MCP tool surface is auto-approved but not enumerable",
        action="require_review",
        risk_level="high",
        recommendation="Enumerate the MCP tools or require prompts before approval.",
    ),
    "CODEX-APP-AUTO-APPROVE": CodexBoundaryRule(
        id="CODEX-APP-AUTO-APPROVE",
        check_id="SHIP-CODEX-BOUNDARY-APP-AUTO-APPROVE",
        title="Codex app connector tool is auto-approved",
        action="require_review",
        risk_level="high",
        recommendation="Review app connector approval changes before local automation.",
    ),
    "CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED": CodexBoundaryRule(
        id="CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED",
        check_id="SHIP-CODEX-BOUNDARY-AGENTS-SHIPGATE-REQUIREMENT-REMOVED",
        title="AGENTS.md removed a Shipgate requirement",
        action="require_review",
        risk_level="medium",
        recommendation="Have a human confirm the agent instructions were not weakened.",
    ),
    "CODEX-CI-GATE-REMOVED": CodexBoundaryRule(
        id="CODEX-CI-GATE-REMOVED",
        check_id="SHIP-CODEX-BOUNDARY-CI-GATE-REMOVED",
        title="Shipgate GitHub Action no longer invokes the gate",
        action="block",
        risk_level="critical",
        recommendation="Restore the Shipgate workflow or get human approval to remove it.",
    ),
    "CODEX-HOOK-COMMAND-CHANGED": CodexBoundaryRule(
        id="CODEX-HOOK-COMMAND-CHANGED",
        check_id="SHIP-CODEX-BOUNDARY-HOOK-COMMAND-CHANGED",
        title="Codex executable hook changed",
        action="require_review",
        risk_level="high",
        recommendation="Review executable hooks before letting Codex rely on them.",
    ),
    "CODEX-SKILL-COMMAND-CHANGED": CodexBoundaryRule(
        id="CODEX-SKILL-COMMAND-CHANGED",
        check_id="SHIP-CODEX-BOUNDARY-SKILL-COMMAND-CHANGED",
        title="Codex skill gained command-bearing instructions",
        action="require_review",
        risk_level="medium",
        recommendation="Review command-bearing skill changes before local automation.",
    ),
}


def evaluate_codex_boundary_result(
    *,
    workspace: Path,
    diff_text: str,
    policy_path: Path | None = None,
    trigger: dict[str, Any] | None = None,
    release_decision: dict[str, Any] | None = None,
) -> AgentResultV1:
    """Return the local Codex agent-result projection for a unified diff."""

    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    changed_files = sorted({item.path for item in diff_files if item.path})
    policy, diagnostics = load_codex_boundary_policy(
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
        if _is_codex_config_path(normalized):
            _evaluate_config_file(workspace, diff_file, add)
        if _is_codex_hooks_path(normalized):
            _evaluate_hooks_json(workspace, diff_file, add)
        if _is_agent_instructions_path(normalized):
            _evaluate_agent_instructions(diff_file, add)
        if _is_shipgate_workflow_path(normalized):
            _evaluate_shipgate_workflow(workspace, diff_file, add)
        if _is_codex_skill_path(normalized):
            _evaluate_skill(diff_file, add)

    violations = _dedupe_violations(violations)
    decision = _decision_for(violations, release_decision=release_decision)
    risk_level = _risk_for(violations)
    finding_fingerprints = [_violation_fingerprint(item) for item in violations]
    audit_id = _audit_id(
        changed_files=changed_files,
        diff_files=diff_files,
        policy=policy,
        finding_fingerprints=finding_fingerprints,
    )
    return AgentResultV1(
        decision=decision,  # type: ignore[arg-type]
        risk_level=risk_level,
        audit_id=audit_id,
        policy_version=policy.version,
        summary=_summary_for(decision, violations),
        changed_files=changed_files,
        first_next_action=_next_action_for(decision, violations),
        violated_rules=violations,
        diagnostics=diagnostics,
        release_decision=release_decision,
        trigger=trigger,
        finding_fingerprints=finding_fingerprints,
    )


def parse_unified_diff(diff_text: str) -> list[DiffFile]:
    files_out: list[DiffFile] = []
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        files_out.append(
            DiffFile(
                old_path=current.get("old_path"),
                new_path=current.get("new_path"),
                added_lines=current.get("added_lines", []),
                removed_lines=current.get("removed_lines", []),
                is_deleted=bool(current.get("is_deleted")),
                is_new=bool(current.get("is_new")),
            )
        )
        current = None

    for raw_line in diff_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith("diff --git "):
            finish()
            parts = raw_line.split()
            old_path = _strip_diff_prefix(parts[2]) if len(parts) > 2 else None
            new_path = _strip_diff_prefix(parts[3]) if len(parts) > 3 else old_path
            current = {
                "old_path": old_path,
                "new_path": new_path,
                "added_lines": [],
                "removed_lines": [],
                "is_deleted": False,
                "is_new": False,
            }
            continue
        if current is None:
            continue
        if raw_line.startswith("deleted file mode"):
            current["is_deleted"] = True
        elif raw_line.startswith("new file mode"):
            current["is_new"] = True
        elif raw_line.startswith("--- "):
            value = raw_line[4:].strip()
            current["old_path"] = None if value == "/dev/null" else _strip_diff_prefix(value)
        elif raw_line.startswith("+++ "):
            value = raw_line[4:].strip()
            current["new_path"] = None if value == "/dev/null" else _strip_diff_prefix(value)
            if value == "/dev/null":
                current["is_deleted"] = True
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            current["added_lines"].append(raw_line[1:])
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            current["removed_lines"].append(raw_line[1:])
    finish()
    return files_out


def load_codex_boundary_policy(
    *,
    workspace: Path,
    policy_path: Path,
) -> tuple[CodexBoundaryPolicy, list[AgentResultDiagnostic]]:
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
                    message=f"Could not load Codex boundary policy: {exc}",
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
                message="Codex boundary policy file was not found; using defaults.",
                path=str(policy_path),
            )
        )
    if data is None:
        return CodexBoundaryPolicy(
            id="codex-boundary-default",
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
        rules[rule_id] = CodexBoundaryRule(
            id=rule_id,
            check_id=str(raw_rule.get("check_id", base.check_id)),
            title=str(raw_rule.get("title", base.title)),
            action=action,
            risk_level=risk,
            recommendation=str(raw_rule.get("recommendation", base.recommendation)),
        )
    return CodexBoundaryPolicy(
        id=str(data.get("id") or "codex-boundary"),
        version=str(data.get("version") or DEFAULT_POLICY_VERSION),
        rules=rules,
    ), diagnostics


def _evaluate_config_file(
    workspace: Path,
    diff_file: DiffFile,
    add,
) -> None:
    path = diff_file.path
    text = _head_text_or_added_lines(workspace, diff_file)
    if text is None:
        if diff_file.is_deleted:
            add(
                "CODEX-UNKNOWN-PERMISSION-KEY",
                path=path,
                evidence={"kind": "codex_config_deleted"},
            )
        return
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        add(
            "CODEX-CONFIG-PARSE-FAILED",
            path=path,
            evidence={"kind": "toml_parse_failed", "error": str(exc)},
        )
        return
    if data.get("sandbox_mode") == "danger-full-access":
        add(
            "CODEX-DANGER-FULL-ACCESS",
            path=path,
            evidence={"kind": "sandbox_mode", "value": "danger-full-access"},
        )
    if data.get("default_permissions") == ":danger-full-access":
        add(
            "CODEX-DANGER-FULL-ACCESS",
            path=path,
            evidence={"kind": "default_permissions", "value": ":danger-full-access"},
        )
    sandbox_write = data.get("sandbox_workspace_write")
    if isinstance(sandbox_write, dict) and sandbox_write.get("network_access") is True:
        add(
            "CODEX-NETWORK-EXPANDED",
            path=path,
            evidence={"kind": "workspace_write_network_access", "value": True},
        )
    _evaluate_permission_profiles(data.get("permissions"), path, add)
    _evaluate_mcp_servers(data.get("mcp_servers"), path, "mcp_servers", add)
    _evaluate_plugin_mcp_servers(data.get("plugins"), path, add)
    _evaluate_hooks(data.get("hooks"), path, add)
    _evaluate_apps(data.get("apps"), path, add)


def _evaluate_permission_profiles(permissions: Any, path: str, add) -> None:
    if not isinstance(permissions, dict):
        return
    for profile, profile_data in sorted(permissions.items()):
        if not isinstance(profile_data, dict):
            continue
        for key in sorted(profile_data):
            if key not in _PERMISSION_PROFILE_KEYS:
                add(
                    "CODEX-UNKNOWN-PERMISSION-KEY",
                    path=path,
                    evidence={
                        "kind": "unknown_permission_key",
                        "profile": profile,
                        "key": key,
                    },
                )
        network = profile_data.get("network")
        if isinstance(network, dict):
            for key in sorted(network):
                if key not in _NETWORK_KEYS:
                    add(
                        "CODEX-UNKNOWN-PERMISSION-KEY",
                        path=path,
                        evidence={
                            "kind": "unknown_permission_network_key",
                            "profile": profile,
                            "key": key,
                        },
                    )
            if network.get("mode") == "full":
                add(
                    "CODEX-NETWORK-EXPANDED",
                    path=path,
                    evidence={"kind": "network_mode_full", "profile": profile},
                )
            domains = network.get("domains")
            if isinstance(domains, dict):
                for domain, value in sorted(domains.items()):
                    if _is_allow(value) and "*" in str(domain):
                        add(
                            "CODEX-NETWORK-WILDCARD",
                            path=path,
                            evidence={
                                "kind": "network_domain_wildcard",
                                "profile": profile,
                                "domain": str(domain),
                                "value": value,
                            },
                        )


def _evaluate_mcp_servers(servers: Any, path: str, prefix: str, add) -> None:
    if not isinstance(servers, dict):
        return
    for server_name, server in sorted(servers.items()):
        if not isinstance(server, dict) or server.get("enabled") is False:
            continue
        server_ref = f"{prefix}.{server_name}"
        if server.get("default_tools_approval_mode") == "approve":
            tool_names = _server_tool_names(server)
            risky = sorted(name for name in tool_names if _is_risky_tool_name(name))
            add(
                "CODEX-MCP-AUTO-APPROVE-WRITE"
                if risky
                else "CODEX-MCP-AUTO-APPROVE-UNKNOWN",
                path=path,
                evidence={
                    "kind": "mcp_default_auto_approve",
                    "server": server_ref,
                    "risky_tools": risky,
                    "tool_names": sorted(tool_names),
                },
            )
        tools = server.get("tools")
        if isinstance(tools, dict):
            for tool_name, config in sorted(tools.items()):
                if isinstance(config, dict) and config.get("approval_mode") == "approve":
                    add(
                        "CODEX-MCP-AUTO-APPROVE-WRITE"
                        if _is_risky_tool_name(str(tool_name))
                        else "CODEX-MCP-AUTO-APPROVE-UNKNOWN",
                        path=path,
                        evidence={
                            "kind": "mcp_tool_auto_approve",
                            "server": server_ref,
                            "tool": str(tool_name),
                        },
                    )


def _evaluate_plugin_mcp_servers(plugins: Any, path: str, add) -> None:
    if not isinstance(plugins, dict):
        return
    for plugin_name, plugin in sorted(plugins.items()):
        if not isinstance(plugin, dict):
            continue
        mcp_servers = plugin.get("mcp_servers")
        _evaluate_mcp_servers(
            mcp_servers,
            path,
            f"plugins.{plugin_name}.mcp_servers",
            add,
        )


def _evaluate_apps(apps: Any, path: str, add) -> None:
    if not isinstance(apps, dict):
        return
    for app_name, app in sorted(apps.items()):
        if not isinstance(app, dict):
            continue
        tools = app.get("tools")
        if not isinstance(tools, dict):
            continue
        for tool_name, tool_config in sorted(tools.items()):
            if isinstance(tool_config, dict) and tool_config.get("approval_mode") == "approve":
                add(
                    "CODEX-MCP-AUTO-APPROVE-WRITE"
                    if _is_risky_tool_name(str(tool_name))
                    else "CODEX-APP-AUTO-APPROVE",
                    path=path,
                    evidence={
                        "kind": "app_tool_auto_approve",
                        "app": str(app_name),
                        "tool": str(tool_name),
                    },
                )


def _evaluate_hooks_json(workspace: Path, diff_file: DiffFile, add) -> None:
    path = diff_file.path
    text = _head_text_or_added_lines(workspace, diff_file)
    if text is None:
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        add(
            "CODEX-CONFIG-PARSE-FAILED",
            path=path,
            evidence={"kind": "hooks_json_parse_failed", "error": str(exc)},
        )
        return
    hooks = data.get("hooks") if isinstance(data, dict) else data
    _evaluate_hooks(hooks, path, add)


def _evaluate_hooks(hooks: Any, path: str, add) -> None:
    for event, group in _iter_hook_groups(hooks):
        for hook in _hook_handlers(group):
            if isinstance(hook, dict) and (hook.get("type") == "command" or hook.get("command")):
                add(
                    "CODEX-HOOK-COMMAND-CHANGED",
                    path=path,
                    evidence={
                        "kind": "command_hook",
                        "event": event,
                        "command_present": bool(hook.get("command")),
                    },
                )


def _evaluate_agent_instructions(diff_file: DiffFile, add) -> None:
    removed_shipgate = any(_contains_shipgate_term(line) for line in diff_file.removed_lines)
    added_shipgate = any(_contains_shipgate_term(line) for line in diff_file.added_lines)
    if diff_file.is_deleted or (removed_shipgate and not added_shipgate):
        add(
            "CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED",
            path=diff_file.path,
            evidence={
                "kind": "shipgate_instruction_removed",
                "deleted": diff_file.is_deleted,
                "removed_shipgate_lines": removed_shipgate,
            },
        )


def _evaluate_shipgate_workflow(workspace: Path, diff_file: DiffFile, add) -> None:
    path = diff_file.path
    head_path = _safe_workspace_path(workspace, path)
    text = None if head_path is None or not head_path.is_file() else head_path.read_text(
        encoding="utf-8",
        errors="replace",
    )
    if diff_file.is_deleted or text is None or not _contains_shipgate_term(text):
        add(
            "CODEX-CI-GATE-REMOVED",
            path=path,
            evidence={
                "kind": "shipgate_ci_gate_removed",
                "deleted": diff_file.is_deleted,
                "shipgate_invocation_present": bool(text and _contains_shipgate_term(text)),
            },
        )


def _evaluate_skill(diff_file: DiffFile, add) -> None:
    if any(_COMMAND_SKILL_RE.search(line) for line in diff_file.added_lines):
        add(
            "CODEX-SKILL-COMMAND-CHANGED",
            path=diff_file.path,
            evidence={"kind": "command_bearing_skill_change"},
        )


def _head_text_or_added_lines(workspace: Path, diff_file: DiffFile) -> str | None:
    head_path = _safe_workspace_path(workspace, diff_file.path)
    if head_path is not None and head_path.is_file():
        try:
            return head_path.read_text(encoding="utf-8")
        except OSError:
            return None
    if diff_file.added_lines:
        return "\n".join(diff_file.added_lines) + "\n"
    return None


def _iter_hook_groups(hooks: Any):
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if isinstance(groups, list):
            for group in groups:
                yield str(event), group
        elif isinstance(groups, dict):
            yield str(event), groups


def _hook_handlers(group: Any) -> list[Any]:
    if isinstance(group, dict):
        hooks = group.get("hooks")
        if isinstance(hooks, list):
            return hooks
        return [group]
    return []


def _server_tool_names(server: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    enabled = server.get("enabled_tools")
    if isinstance(enabled, list):
        names.update(str(item) for item in enabled if isinstance(item, str))
    tools = server.get("tools")
    if isinstance(tools, dict):
        names.update(str(item) for item in tools)
    return names


def _decision_for(
    violations: list[AgentResultViolatedRule],
    *,
    release_decision: dict[str, Any] | None,
) -> str:
    decision = "allow"
    for item in violations:
        if _DECISION_RANK[item.action] > _DECISION_RANK[decision]:
            decision = item.action
    if release_decision is None:
        return decision
    substrate = release_decision.get("decision")
    projection = {
        "passed": "allow",
        "review_required": "require_review",
        "insufficient_evidence": "require_review",
        "blocked": "block",
    }.get(substrate, "require_review")
    return projection if _DECISION_RANK[projection] > _DECISION_RANK[decision] else decision


def _risk_for(violations: list[AgentResultViolatedRule]) -> AgentResultRiskLevel:
    if not violations:
        return "none"
    max_item = max(violations, key=lambda item: _RISK_RANK[item.risk_level])
    return max_item.risk_level


def _summary_for(decision: str, violations: list[AgentResultViolatedRule]) -> str:
    if decision == "allow":
        return "No Codex boundary changes require action."
    if decision == "warn":
        return "Codex boundary check completed with warnings."
    if decision == "require_review":
        return f"{len(violations)} Codex boundary change(s) require human review."
    return f"{len(violations)} Codex boundary change(s) block local continuation."


def _next_action_for(
    decision: str,
    violations: list[AgentResultViolatedRule],
) -> AgentResultNextAction:
    if decision == "allow":
        return AgentResultNextAction(
            actor="coding_agent",
            kind="continue",
            command=None,
            why="No Codex boundary rule requires review or blocking.",
        )
    if decision == "warn":
        return AgentResultNextAction(
            actor="coding_agent",
            kind="warn",
            command=None,
            why="Continue, but surface the warning diagnostics in the task summary.",
        )
    if decision == "require_review":
        first = violations[0].title if violations else "Codex boundary review required"
        return AgentResultNextAction(
            actor="human",
            kind="review",
            command=None,
            why=first,
        )
    first = violations[0].title if violations else "Codex boundary blocked"
    return AgentResultNextAction(actor="human", kind="stop", command=None, why=first)


def _audit_id(
    *,
    changed_files: list[str],
    diff_files: list[DiffFile],
    policy: CodexBoundaryPolicy,
    finding_fingerprints: list[str],
) -> str:
    payload = {
        "schema_version": "agent_result_v1",
        "agent": "codex",
        "changed_files": changed_files,
        "diff": [
            {
                "old_path": item.old_path,
                "new_path": item.new_path,
                "added_lines": item.added_lines,
                "removed_lines": item.removed_lines,
                "is_deleted": item.is_deleted,
                "is_new": item.is_new,
            }
            for item in diff_files
        ],
        "policy_version": policy.version,
        "rule_ids": sorted(policy.rules),
        "finding_fingerprints": sorted(finding_fingerprints),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"codex_boundary_{digest}"


def _violation_fingerprint(item: AgentResultViolatedRule) -> str:
    payload = {
        "check_id": item.check_id,
        "path": item.path,
        "action": item.action,
        "evidence": item.evidence,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"fp_{digest}"


def _dedupe_violations(
    violations: list[AgentResultViolatedRule],
) -> list[AgentResultViolatedRule]:
    by_key = {
        json.dumps(item.model_dump(mode="json"), sort_keys=True): item
        for item in violations
    }
    return [by_key[key] for key in sorted(by_key)]


def _load_packaged_default_policy() -> dict[str, Any] | None:
    candidate = (
        Path(__file__).resolve().parents[1]
        / "_meta"
        / "policies"
        / "codex-boundary.shipgate.yaml"
    )
    try:
        if candidate.is_file():
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            return loaded if isinstance(loaded, dict) else None
    except (OSError, yaml.YAMLError):
        return None
    return None


def _safe_workspace_path(workspace: Path, value: str) -> Path | None:
    candidate = (workspace / value).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    return candidate


def _display_path(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)


def _strip_diff_prefix(value: str) -> str:
    value = value.strip()
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def _is_allow(value: Any) -> bool:
    return value is True or str(value).lower() == "allow"


def _is_risky_tool_name(value: str) -> bool:
    return bool(_RISKY_TOOL_RE.search(value))


def _contains_shipgate_term(value: str) -> bool:
    lowered = value.lower()
    return any(term in lowered for term in _SHIPGATE_TERMS)


def _is_codex_config_path(path: str) -> bool:
    return path == ".codex/config.toml" or path.endswith("/.codex/config.toml")


def _is_codex_hooks_path(path: str) -> bool:
    return path == ".codex/hooks.json" or path.endswith("/.codex/hooks.json")


def _is_agent_instructions_path(path: str) -> bool:
    name = Path(path).name
    return name in {"AGENTS.md", "AGENTS.override.md"}


def _is_shipgate_workflow_path(path: str) -> bool:
    return path in {
        ".github/workflows/agents-shipgate.yml",
        ".github/workflows/agents-shipgate.yaml",
    } or path.endswith("/.github/workflows/agents-shipgate.yml") or path.endswith(
        "/.github/workflows/agents-shipgate.yaml"
    )


def _is_codex_skill_path(path: str) -> bool:
    return (
        path.endswith("/SKILL.md")
        and (path.startswith(".agents/skills/") or "/.agents/skills/" in path)
    )
