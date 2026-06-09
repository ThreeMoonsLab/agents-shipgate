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
_RISKY_ACTION_TOKENS = {
    "apply",
    "approve",
    "cancel",
    "commit",
    "edit",
    "create",
    "delete",
    "deploy",
    "destroy",
    "drop",
    "execute",
    "exec",
    "grant",
    "kill",
    "merge",
    "overwrite",
    "patch",
    "post",
    "publish",
    "purge",
    "push",
    "put",
    "release",
    "remove",
    "revoke",
    "run",
    "send",
    "terminate",
    "transfer",
    "truncate",
    "update",
    "wipe",
    "write",
}
_RISKY_NOUN_TOKENS = {"payment", "refund"}
_INFLECTED_RISKY_ACTION_TOKENS = {
    "approves": "approve",
    "cancels": "cancel",
    "commits": "commit",
    "creates": "create",
    "deletes": "delete",
    "deploys": "deploy",
    "destroys": "destroy",
    "drops": "drop",
    "edits": "edit",
    "executes": "execute",
    "grants": "grant",
    "kills": "kill",
    "merges": "merge",
    "overwrites": "overwrite",
    "patches": "patch",
    "posts": "post",
    "publishes": "publish",
    "purges": "purge",
    "pushes": "push",
    "puts": "put",
    "releases": "release",
    "removes": "remove",
    "revokes": "revoke",
    "sends": "send",
    "terminates": "terminate",
    "transfers": "transfer",
    "truncates": "truncate",
    "updates": "update",
    "wipes": "wipe",
    "writes": "write",
}
_SAFE_READ_PREFIXES = {
    "compute",
    "describe",
    "fetch",
    "get",
    "list",
    "lookup",
    "read",
    "search",
    "show",
}
_WEAKENING_TERMS = (
    "advisory",
    "at your discretion",
    "bypass",
    "can be skipped",
    "disable",
    "disabled",
    "do not run",
    "don't run",
    "ignore",
    "no need",
    "not required",
    "optional",
    "skip",
    "unnecessary",
)
_REQUIREMENT_MARKER_RE = re.compile(
    r"\b(?:always|must|required|requires?|shall)\b|"
    r"\bbefore\b.{0,80}\b(?:complet\w*|finish\w*|report\w*)\b",
    re.IGNORECASE,
)
_SHIPGATE_INVOCATION_RE = re.compile(
    r"^\s*(?:-\s*)?(?:run:\s*)?"
    r"(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+|env)\s+)*"
    r"(?:(?:python|python3)\s+-m\s+agents_shipgate|agents-shipgate|shipgate)"
    r"\s+(?:verify|scan|check)\b"
)
_SHIPGATE_ACTION_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s+ThreeMoonsLab/agents-shipgate(?:@|\b)",
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
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DiffFile:
    old_path: str | None
    new_path: str | None
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    hunks: list[DiffHunk] = field(default_factory=list)
    is_deleted: bool = False
    is_new: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass(frozen=True)
class ResolvedFileText:
    old_text: str | None
    new_text: str | None
    source: str
    old_sha256: str | None
    new_sha256: str | None


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
    evaluated_files: list[dict[str, Any]] = []

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
            resolved = _resolve_changed_file_text(workspace, diff_file, diagnostics)
            evaluated_files.append(_evaluated_file_record(path, resolved))
            _evaluate_config_file(diff_file, resolved, add)
        if _is_codex_hooks_path(normalized):
            resolved = _resolve_changed_file_text(workspace, diff_file, diagnostics)
            evaluated_files.append(_evaluated_file_record(path, resolved))
            _evaluate_hooks_json(diff_file, resolved, add)
        if _is_agent_instructions_path(normalized):
            _evaluate_agent_instructions(diff_file, add)
        if _is_shipgate_workflow_path(normalized):
            resolved = _resolve_changed_file_text(workspace, diff_file, diagnostics)
            evaluated_files.append(_evaluated_file_record(path, resolved))
            _evaluate_shipgate_workflow(diff_file, resolved, add)
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
        evaluated_files=evaluated_files,
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
    current_hunk: DiffHunk | None = None

    def finish() -> None:
        nonlocal current, current_hunk
        if current is None:
            return
        if current_hunk is not None:
            current.setdefault("hunks", []).append(current_hunk)
            current_hunk = None
        files_out.append(
            DiffFile(
                old_path=current.get("old_path"),
                new_path=current.get("new_path"),
                added_lines=current.get("added_lines", []),
                removed_lines=current.get("removed_lines", []),
                hunks=current.get("hunks", []),
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
                "hunks": [],
                "is_deleted": False,
                "is_new": False,
            }
            current_hunk = None
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
        elif raw_line.startswith("@@ "):
            if current_hunk is not None:
                current.setdefault("hunks", []).append(current_hunk)
            current_hunk = _parse_hunk_header(raw_line)
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            current["added_lines"].append(raw_line[1:])
            if current_hunk is not None:
                current_hunk.lines.append(("+", raw_line[1:]))
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            current["removed_lines"].append(raw_line[1:])
            if current_hunk is not None:
                current_hunk.lines.append(("-", raw_line[1:]))
        elif raw_line.startswith(" ") and current_hunk is not None:
            current_hunk.lines.append((" ", raw_line[1:]))
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
    diff_file: DiffFile,
    resolved: ResolvedFileText,
    add,
) -> None:
    path = diff_file.path
    if resolved.new_text is None:
        if diff_file.is_deleted:
            add(
                "CODEX-UNKNOWN-PERMISSION-KEY",
                path=path,
                evidence={"kind": "codex_config_deleted"},
            )
        else:
            add(
                "CODEX-CONFIG-PARSE-FAILED",
                path=path,
                evidence={
                    "kind": "codex_config_content_unresolved",
                    "source": resolved.source,
                },
            )
        return
    try:
        data = tomllib.loads(resolved.new_text)
    except tomllib.TOMLDecodeError as exc:
        add(
            "CODEX-CONFIG-PARSE-FAILED",
            path=path,
            evidence={"kind": "toml_parse_failed", "error": str(exc)},
        )
        return
    try:
        old_data = tomllib.loads(resolved.old_text or "")
    except tomllib.TOMLDecodeError as exc:
        add(
            "CODEX-CONFIG-PARSE-FAILED",
            path=path,
            evidence={"kind": "old_toml_parse_failed", "error": str(exc)},
        )
        return
    if _changed_to(old_data, data, ("sandbox_mode",), "danger-full-access"):
        add(
            "CODEX-DANGER-FULL-ACCESS",
            path=path,
            evidence={"kind": "sandbox_mode", "value": "danger-full-access"},
        )
    if _changed_to(
        old_data,
        data,
        ("default_permissions",),
        ":danger-full-access",
    ):
        add(
            "CODEX-DANGER-FULL-ACCESS",
            path=path,
            evidence={"kind": "default_permissions", "value": ":danger-full-access"},
        )
    if _changed_to(
        old_data,
        data,
        ("sandbox_workspace_write", "network_access"),
        True,
    ):
        add(
            "CODEX-NETWORK-EXPANDED",
            path=path,
            evidence={"kind": "workspace_write_network_access", "value": True},
        )
    _evaluate_permission_profiles(
        old_data.get("permissions"),
        data.get("permissions"),
        path,
        add,
    )
    _evaluate_mcp_servers(
        old_data.get("mcp_servers"),
        data.get("mcp_servers"),
        path,
        "mcp_servers",
        add,
    )
    _evaluate_plugin_mcp_servers(old_data.get("plugins"), data.get("plugins"), path, add)
    _evaluate_hooks(old_data.get("hooks"), data.get("hooks"), path, add)
    _evaluate_apps(old_data.get("apps"), data.get("apps"), path, add)


def _evaluate_permission_profiles(
    old_permissions: Any,
    permissions: Any,
    path: str,
    add,
) -> None:
    if not isinstance(permissions, dict):
        return
    old_profiles = old_permissions if isinstance(old_permissions, dict) else {}
    for profile, profile_data in sorted(permissions.items()):
        if not isinstance(profile_data, dict):
            continue
        old_profile = old_profiles.get(profile) if isinstance(old_profiles, dict) else None
        old_profile = old_profile if isinstance(old_profile, dict) else {}
        for key in sorted(profile_data):
            if key not in _PERMISSION_PROFILE_KEYS and old_profile.get(key) != profile_data[key]:
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
            old_network = old_profile.get("network")
            old_network = old_network if isinstance(old_network, dict) else {}
            for key in sorted(network):
                if key not in _NETWORK_KEYS and old_network.get(key) != network[key]:
                    add(
                        "CODEX-UNKNOWN-PERMISSION-KEY",
                        path=path,
                        evidence={
                            "kind": "unknown_permission_network_key",
                            "profile": profile,
                            "key": key,
                        },
                    )
            if network.get("mode") == "full" and old_network.get("mode") != "full":
                add(
                    "CODEX-NETWORK-EXPANDED",
                    path=path,
                    evidence={"kind": "network_mode_full", "profile": profile},
                )
            domains = network.get("domains")
            if isinstance(domains, dict):
                old_domains = old_network.get("domains")
                old_domains = old_domains if isinstance(old_domains, dict) else {}
                for domain, value in sorted(domains.items()):
                    if (
                        _is_allow(value)
                        and "*" in str(domain)
                        and not _is_allow(old_domains.get(domain))
                    ):
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


def _evaluate_mcp_servers(
    old_servers: Any,
    servers: Any,
    path: str,
    prefix: str,
    add,
) -> None:
    if not isinstance(servers, dict):
        return
    old_servers = old_servers if isinstance(old_servers, dict) else {}
    for server_name, server in sorted(servers.items()):
        if not isinstance(server, dict) or server.get("enabled") is False:
            continue
        old_server = old_servers.get(server_name) if isinstance(old_servers, dict) else None
        old_server = old_server if isinstance(old_server, dict) else {}
        server_ref = f"{prefix}.{server_name}"
        if (
            server.get("default_tools_approval_mode") == "approve"
            and old_server.get("default_tools_approval_mode") != "approve"
        ):
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
            old_tools = old_server.get("tools")
            old_tools = old_tools if isinstance(old_tools, dict) else {}
            for tool_name, config in sorted(tools.items()):
                old_tool = old_tools.get(tool_name) if isinstance(old_tools, dict) else None
                old_tool = old_tool if isinstance(old_tool, dict) else {}
                if (
                    isinstance(config, dict)
                    and config.get("approval_mode") == "approve"
                    and old_tool.get("approval_mode") != "approve"
                ):
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


def _evaluate_plugin_mcp_servers(
    old_plugins: Any,
    plugins: Any,
    path: str,
    add,
) -> None:
    if not isinstance(plugins, dict):
        return
    old_plugins = old_plugins if isinstance(old_plugins, dict) else {}
    for plugin_name, plugin in sorted(plugins.items()):
        if not isinstance(plugin, dict):
            continue
        old_plugin = old_plugins.get(plugin_name) if isinstance(old_plugins, dict) else None
        old_plugin = old_plugin if isinstance(old_plugin, dict) else {}
        mcp_servers = plugin.get("mcp_servers")
        old_mcp_servers = old_plugin.get("mcp_servers")
        _evaluate_mcp_servers(
            old_mcp_servers,
            mcp_servers,
            path,
            f"plugins.{plugin_name}.mcp_servers",
            add,
        )


def _evaluate_apps(old_apps: Any, apps: Any, path: str, add) -> None:
    if not isinstance(apps, dict):
        return
    old_apps = old_apps if isinstance(old_apps, dict) else {}
    for app_name, app in sorted(apps.items()):
        if not isinstance(app, dict):
            continue
        old_app = old_apps.get(app_name) if isinstance(old_apps, dict) else None
        old_app = old_app if isinstance(old_app, dict) else {}
        tools = app.get("tools")
        if not isinstance(tools, dict):
            continue
        old_tools = old_app.get("tools")
        old_tools = old_tools if isinstance(old_tools, dict) else {}
        for tool_name, tool_config in sorted(tools.items()):
            old_tool = old_tools.get(tool_name) if isinstance(old_tools, dict) else None
            old_tool = old_tool if isinstance(old_tool, dict) else {}
            if (
                isinstance(tool_config, dict)
                and tool_config.get("approval_mode") == "approve"
                and old_tool.get("approval_mode") != "approve"
            ):
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


def _evaluate_hooks_json(
    diff_file: DiffFile,
    resolved: ResolvedFileText,
    add,
) -> None:
    path = diff_file.path
    if resolved.new_text is None:
        add(
            "CODEX-CONFIG-PARSE-FAILED",
            path=path,
            evidence={
                "kind": "hooks_json_content_unresolved",
                "source": resolved.source,
            },
        )
        return
    try:
        data = json.loads(resolved.new_text)
    except json.JSONDecodeError as exc:
        add(
            "CODEX-CONFIG-PARSE-FAILED",
            path=path,
            evidence={"kind": "hooks_json_parse_failed", "error": str(exc)},
        )
        return
    hooks = data.get("hooks") if isinstance(data, dict) else data
    if not resolved.old_text:
        old_hooks = None
    else:
        try:
            old_data = json.loads(resolved.old_text)
        except json.JSONDecodeError as exc:
            add(
                "CODEX-CONFIG-PARSE-FAILED",
                path=path,
                evidence={"kind": "old_hooks_json_parse_failed", "error": str(exc)},
            )
            return
        old_hooks = old_data.get("hooks") if isinstance(old_data, dict) else old_data
    _evaluate_hooks(old_hooks, hooks, path, add)


def _evaluate_hooks(old_hooks: Any, hooks: Any, path: str, add) -> None:
    old_command_handlers = _hook_command_signatures_by_event(old_hooks)
    for event, group in _iter_hook_groups(hooks):
        for hook in _hook_handlers(group):
            if isinstance(hook, dict) and (hook.get("type") == "command" or hook.get("command")):
                signature = _canonical_json(hook)
                if signature in old_command_handlers.get(event, set()):
                    continue
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
    removed_requirement = any(
        _contains_shipgate_requirement(line) for line in diff_file.removed_lines
    )
    added_requirement = any(
        _contains_shipgate_requirement(line) for line in diff_file.added_lines
    )
    softened_shipgate = any(
        _contains_shipgate_term(line) and _contains_weakening_term(line)
        for line in diff_file.added_lines
    )
    if diff_file.is_deleted or (
        removed_shipgate
        and (not added_shipgate or softened_shipgate or (removed_requirement and not added_requirement))
    ):
        add(
            "CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED",
            path=diff_file.path,
            evidence={
                "kind": "shipgate_instruction_removed",
                "deleted": diff_file.is_deleted,
                "removed_shipgate_lines": removed_shipgate,
                "softened_shipgate_lines": softened_shipgate,
                "removed_requirement_lines": removed_requirement,
                "replacement_requirement_lines": added_requirement,
            },
        )


def _evaluate_shipgate_workflow(
    diff_file: DiffFile,
    resolved: ResolvedFileText,
    add,
) -> None:
    path = diff_file.path
    invocation_present = bool(
        resolved.new_text and _has_shipgate_gate_invocation(resolved.new_text)
    )
    if diff_file.is_deleted or resolved.new_text is None or not invocation_present:
        add(
            "CODEX-CI-GATE-REMOVED",
            path=path,
            evidence={
                "kind": "shipgate_ci_gate_removed",
                "deleted": diff_file.is_deleted,
                "source": resolved.source,
                "shipgate_invocation_present": invocation_present,
            },
        )


def _evaluate_skill(diff_file: DiffFile, add) -> None:
    if any(_COMMAND_SKILL_RE.search(line) for line in diff_file.added_lines):
        add(
            "CODEX-SKILL-COMMAND-CHANGED",
            path=diff_file.path,
            evidence={"kind": "command_bearing_skill_change"},
        )


def _resolve_changed_file_text(
    workspace: Path,
    diff_file: DiffFile,
    diagnostics: list[AgentResultDiagnostic],
) -> ResolvedFileText:
    path = diff_file.path
    if diff_file.is_deleted:
        resolved = ResolvedFileText(
            old_text=None,
            new_text=None,
            source="diff_deleted_file",
            old_sha256=None,
            new_sha256=None,
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved
    if diff_file.is_new:
        new_text = _new_text_from_hunks(diff_file)
        resolved = ResolvedFileText(
            old_text="",
            new_text=new_text,
            source="diff_new_file",
            old_sha256=_sha256_text(""),
            new_sha256=_sha256_text(new_text),
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved

    head_path = _safe_workspace_path(workspace, path)
    if head_path is None:
        resolved = _unresolved_text("path_outside_workspace")
        diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
        return resolved
    if not head_path.is_file():
        resolved = _unresolved_text("workspace_file_missing")
        diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
        return resolved
    try:
        workspace_text = head_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        resolved = _unresolved_text("workspace_read_failed")
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code="content_source",
                message=f"Could not read changed Codex boundary file: {exc}",
                path=path,
            )
        )
        return resolved

    if _is_insertion_only_change(diff_file):
        reversed_text = _apply_hunks(workspace_text, diff_file.hunks, direction="reverse")
        if reversed_text is not None:
            resolved = ResolvedFileText(
                old_text=reversed_text,
                new_text=workspace_text,
                source="workspace_already_contains_diff_head",
                old_sha256=_sha256_text(reversed_text),
                new_sha256=_sha256_text(workspace_text),
            )
            diagnostics.append(_content_source_diagnostic(path, resolved))
            return resolved

    applied = _apply_hunks(workspace_text, diff_file.hunks, direction="forward")
    if applied is not None:
        resolved = ResolvedFileText(
            old_text=workspace_text,
            new_text=applied,
            source="diff_applied_to_workspace_base",
            old_sha256=_sha256_text(workspace_text),
            new_sha256=_sha256_text(applied),
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved

    reversed_text = _apply_hunks(workspace_text, diff_file.hunks, direction="reverse")
    if reversed_text is not None:
        resolved = ResolvedFileText(
            old_text=reversed_text,
            new_text=workspace_text,
            source="workspace_already_contains_diff_head",
            old_sha256=_sha256_text(reversed_text),
            new_sha256=_sha256_text(workspace_text),
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved

    resolved = _unresolved_text("diff_workspace_mismatch")
    diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
    return resolved


def _unresolved_text(source: str) -> ResolvedFileText:
    return ResolvedFileText(
        old_text=None,
        new_text=None,
        source=source,
        old_sha256=None,
        new_sha256=None,
    )


def _content_source_diagnostic(
    path: str,
    resolved: ResolvedFileText,
    *,
    level: str = "info",
) -> AgentResultDiagnostic:
    return AgentResultDiagnostic(
        level=level,  # type: ignore[arg-type]
        code="content_source",
        message=f"Evaluated Codex boundary file from {resolved.source}.",
        path=path,
    )


def _evaluated_file_record(path: str, resolved: ResolvedFileText) -> dict[str, Any]:
    return {
        "path": path,
        "source": resolved.source,
        "old_sha256": resolved.old_sha256,
        "new_sha256": resolved.new_sha256,
    }


def _new_text_from_hunks(diff_file: DiffFile) -> str:
    if diff_file.hunks:
        lines = [
            text
            for hunk in diff_file.hunks
            for kind, text in hunk.lines
            if kind in {" ", "+"}
        ]
        return _join_lines(lines)
    if diff_file.added_lines:
        return _join_lines(diff_file.added_lines)
    return ""


def _apply_hunks(
    text: str,
    hunks: list[DiffHunk],
    *,
    direction: str,
) -> str | None:
    if not hunks:
        return text
    lines = text.splitlines()
    offset = 0
    for hunk in hunks:
        if direction == "forward":
            start = hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
            expected = [value for kind, value in hunk.lines if kind in {" ", "-"}]
            replacement = [value for kind, value in hunk.lines if kind in {" ", "+"}]
        else:
            start = hunk.new_start - 1 if hunk.new_count > 0 else hunk.new_start
            expected = [value for kind, value in hunk.lines if kind in {" ", "+"}]
            replacement = [value for kind, value in hunk.lines if kind in {" ", "-"}]
        index = start + offset
        if index < 0:
            return None
        if index > len(lines):
            return None
        if index + len(expected) > len(lines):
            return None
        if lines[index : index + len(expected)] != expected:
            return None
        lines[index : index + len(expected)] = replacement
        offset += len(replacement) - len(expected)
    return _join_lines(lines, final_newline=text.endswith("\n"))


def _is_insertion_only_change(diff_file: DiffFile) -> bool:
    return any(
        any(kind == "+" for kind, _ in hunk.lines)
        and not any(kind == "-" for kind, _ in hunk.lines)
        for hunk in diff_file.hunks
    )


def _join_lines(lines: list[str], *, final_newline: bool = True) -> str:
    if not lines:
        return ""
    text = "\n".join(lines)
    return f"{text}\n" if final_newline else text


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _changed_to(
    old_data: dict[str, Any],
    data: dict[str, Any],
    path: tuple[str, ...],
    value: Any,
) -> bool:
    return _nested_get(data, path) == value and _nested_get(old_data, path) != value


def _nested_get(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _parse_hunk_header(line: str) -> DiffHunk:
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
    if not match:
        return DiffHunk(old_start=0, old_count=0, new_start=0, new_count=0)
    old_start, old_count, new_start, new_count = match.groups()
    return DiffHunk(
        old_start=int(old_start),
        old_count=int(old_count or "1"),
        new_start=int(new_start),
        new_count=int(new_count or "1"),
    )


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


def _hook_command_signatures_by_event(hooks: Any) -> dict[str, set[str]]:
    signatures: dict[str, set[str]] = {}
    for event, group in _iter_hook_groups(hooks):
        for hook in _hook_handlers(group):
            if isinstance(hook, dict) and (hook.get("type") == "command" or hook.get("command")):
                signatures.setdefault(event, set()).add(_canonical_json(hook))
    return signatures


def _server_tool_names(server: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    enabled = server.get("enabled_tools")
    if isinstance(enabled, list):
        names.update(str(item) for item in enabled if isinstance(item, str))
    for key in ("allowed_tools", "tool_allowlist", "tools_allowlist"):
        value = server.get(key)
        if isinstance(value, list):
            names.update(str(item) for item in value if isinstance(item, str))
    tools = server.get("tools")
    if isinstance(tools, dict):
        for name, config in tools.items():
            if isinstance(config, dict) and config.get("enabled") is False:
                continue
            names.add(str(name))
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
    evaluated_files: list[dict[str, Any]],
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
        "evaluated_files": sorted(
            evaluated_files,
            key=lambda item: (
                str(item.get("path") or ""),
                str(item.get("source") or ""),
            ),
        ),
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
        "risk_level": item.risk_level,
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
    tokens = _name_tokens(value)
    action_tokens = {variant for token in tokens for variant in _token_variants(token)}
    if any(token in _RISKY_ACTION_TOKENS for token in action_tokens):
        return True
    first_token_variants = _token_variants(tokens[0]) if tokens else set()
    if not tokens or first_token_variants & _SAFE_READ_PREFIXES:
        return False
    return any(token in _RISKY_NOUN_TOKENS for token in tokens)


def _name_tokens(value: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", spaced)]


def _token_variants(token: str) -> set[str]:
    variants = {token}
    if token in _INFLECTED_RISKY_ACTION_TOKENS:
        variants.add(_INFLECTED_RISKY_ACTION_TOKENS[token])
    if token.endswith("ed") and len(token) > 4:
        variants.add(token[:-1])
        variants.add(token[:-2])
    if token.endswith("ing") and len(token) > 5:
        variants.add(token[:-3])
        variants.add(f"{token[:-3]}e")
    return variants


def _contains_shipgate_term(value: str) -> bool:
    lowered = value.lower()
    return any(term in lowered for term in _SHIPGATE_TERMS)


def _contains_shipgate_requirement(value: str) -> bool:
    return _contains_shipgate_term(value) and bool(_REQUIREMENT_MARKER_RE.search(value))


def _contains_weakening_term(value: str) -> bool:
    lowered = value.lower()
    return any(term in lowered for term in _WEAKENING_TERMS)


def _has_shipgate_gate_invocation(value: str) -> bool:
    return any(
        _SHIPGATE_INVOCATION_RE.search(line) or _SHIPGATE_ACTION_RE.search(line)
        for line in value.splitlines()
    )


def _is_codex_config_path(path: str) -> bool:
    return path == ".codex/config.toml" or path.endswith("/.codex/config.toml")


def is_codex_config_path(path: str) -> bool:
    return _is_codex_config_path(path)


def is_mcp_json_path(path: str) -> bool:
    return path == ".mcp.json" or path.endswith("/.mcp.json")


def resolve_changed_file_text(
    workspace: Path,
    diff_file: DiffFile,
    diagnostics: list[AgentResultDiagnostic],
) -> ResolvedFileText:
    return _resolve_changed_file_text(workspace, diff_file, diagnostics)


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
