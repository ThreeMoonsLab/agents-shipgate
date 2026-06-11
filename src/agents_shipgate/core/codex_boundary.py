from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The shared unified-diff plumbing moved to
# ``agents_shipgate.core.boundary_diff``. These re-exports preserve the
# pre-split import surface of this module: existing imports such as
# ``from agents_shipgate.core.codex_boundary import parse_unified_diff``
# (tests, checks, CLI) keep working unchanged.
from agents_shipgate.core.boundary_diff import (  # noqa: F401
    DiffFile,
    DiffHunk,
    ResolvedFileText,
    _apply_hunks,
    _canonical_json,
    _content_source_diagnostic,
    _evaluated_file_record,
    _is_insertion_only_change,
    _join_lines,
    _new_text_from_hunks,
    _parse_hunk_header,
    _resolve_changed_file_text,
    _safe_workspace_path,
    _sha256_text,
    _strip_diff_prefix,
    _unresolved_text,
    parse_unified_diff,
)
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultAffectedFile,
    AgentResultDiagnostic,
    AgentResultHumanReview,
    AgentResultNextAction,
    AgentResultPolicy,
    AgentResultRepair,
    AgentResultRiskLevel,
    AgentResultSubject,
    AgentResultTraceEvent,
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
_AGENT_SAFE_REPAIR_RULE_IDS = frozenset({"CODEX-MCP-AUTO-APPROVE-WRITE"})

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
_SHIPGATE_CLI_COMMAND_RE = re.compile(
    r"^\s*(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+|env)\s+)*"
    r"(?:(?:python|python3)\s+-m\s+agents_shipgate|agents-shipgate|shipgate)"
    r"(?:\s|$)"
)
_SHIPGATE_ACTION_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s+ThreeMoonsLab/agents-shipgate(?:@|\b)",
    re.IGNORECASE,
)
_LOCAL_ACTION_RE = re.compile(r"^\s*(?:-\s*)?uses:\s+\./?\s*(?:#.*)?$")
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
    source: str = "packaged_default"
    path: str | None = None
    snapshot_sha256: str | None = None
    discovery: tuple[str, ...] = ()


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
    "CODEX-POLICY-WEAKENED": CodexBoundaryRule(
        id="CODEX-POLICY-WEAKENED",
        check_id="SHIP-CODEX-BOUNDARY-POLICY-WEAKENED",
        title="Codex boundary policy was weakened",
        action="block",
        risk_level="critical",
        recommendation="Do not weaken Shipgate policy to pass the gate; restore the stricter policy or get human approval.",
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
    agent: str = "codex",
    policy_path: Path | None = None,
    trigger: dict[str, Any] | None = None,
    release_decision: dict[str, Any] | None = None,
    capability_surfaces_changed: list[str] | None = None,
) -> AgentResultV1:
    """Return the local Codex agent-result projection for a unified diff.

    ``capability_surfaces_changed`` lists changed files that the manifest
    declares as tool sources. The boundary evaluator does not inspect tool
    surfaces (only ``verify`` computes the capability delta), so when one
    changed and the boundary result is otherwise a clean ``allow``, the
    result is escalated to ``warn`` and routed to ``verify`` rather than
    green-lighting a capability change ``check`` never evaluated. This keeps
    ``check`` from disagreeing with the ``release_decision.decision`` gate.
    """

    # Keep this local diff projector aligned with
    # agents_shipgate.ci.agent_result.build_agent_result; both produce
    # agent_result_v1 routing fields for different substrates.
    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    changed_files = sorted({item.path for item in diff_files if item.path})
    policy, diagnostics = load_codex_boundary_policy(
        workspace=workspace,
        policy_path=policy_path,
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

    for diagnostic in diagnostics:
        if diagnostic.level == "error" and diagnostic.code.startswith("policy_"):
            add(
                "CODEX-CONFIG-PARSE-FAILED",
                path=diagnostic.path,
                evidence={
                    "kind": "codex_boundary_policy_invalid",
                    "code": diagnostic.code,
                    "message": diagnostic.message,
                },
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
            _evaluate_shipgate_workflow(diff_file, resolved, add, workspace=workspace)
        if _is_codex_boundary_policy_path(normalized):
            resolved = _resolve_changed_file_text(workspace, diff_file, diagnostics)
            evaluated_files.append(_evaluated_file_record(path, resolved))
            _evaluate_codex_boundary_policy(diff_file, resolved, add)
        if _is_codex_skill_path(normalized):
            _evaluate_skill(diff_file, add)

    violations = _dedupe_violations(violations)
    decision = _decision_for(violations, release_decision=release_decision)

    # Coverage gap: check is boundary-only, so a clean ``allow`` over a diff
    # that touches a manifest-declared tool surface would silently green-light
    # a capability change that only ``verify`` gates. Escalate to ``warn`` and
    # route to verify instead. Gated on ``release_decision is None`` because a
    # provided release decision means the full capability scan already ran.
    coverage_surfaces = sorted(dict.fromkeys(capability_surfaces_changed or []))
    coverage_gap = decision == "allow" and release_decision is None and bool(coverage_surfaces)
    if coverage_gap:
        decision = "warn"

    risk_level = _risk_for(violations)
    repair = _repair_for(decision, violations, agent)
    human_review = _human_review_for(decision, violations, repair)
    finding_fingerprints = [_violation_fingerprint(item) for item in violations]
    audit_id = _audit_id(
        changed_files=changed_files,
        diff_files=diff_files,
        policy=policy,
        finding_fingerprints=finding_fingerprints,
        evaluated_files=evaluated_files,
    )
    if coverage_gap:
        first_next_action = _coverage_next_action()
        summary = _coverage_summary(coverage_surfaces)
        diagnostics = [*diagnostics, _coverage_diagnostic(coverage_surfaces)]
        trace = [*_trace_for(policy, decision, violations), _coverage_trace(coverage_surfaces)]
        suggested_fixes = [_VERIFY_COMMAND]
    else:
        first_next_action = _next_action_for(decision, violations, repair)
        summary = _summary_for(decision, violations)
        trace = _trace_for(policy, decision, violations)
        suggested_fixes = [item.recommendation for item in violations[:5]]
    return AgentResultV1(
        agent=agent,  # type: ignore[arg-type]
        subject=AgentResultSubject(agent=agent),
        decision=decision,  # type: ignore[arg-type]
        risk_level=risk_level,
        audit_id=audit_id,
        policy_version=policy.version,
        summary=summary,
        changed_files=changed_files,
        completion_allowed=decision in {"allow", "warn"},
        must_stop=decision in {"require_review", "block"} and not repair.safe_to_attempt,
        first_next_action=first_next_action,
        human_review=human_review,
        repair=repair,
        policy=_policy_result(policy),
        violated_rules=violations,
        affected_files=_affected_files_for(violations, changed_files),
        required_reviewers=human_review.required_reviewers,
        explanation=summary,
        suggested_fixes=suggested_fixes,
        agent_repair_instructions=_agent_repair_instructions(decision, violations),
        diagnostics=diagnostics,
        trace=trace,
        release_decision=release_decision,
        trigger=trigger,
        finding_fingerprints=finding_fingerprints,
        policy_snapshot_sha256=policy.snapshot_sha256,
        exit_code_hint=20 if decision == "block" else 0,
    )


def load_codex_boundary_policy(
    *,
    workspace: Path,
    policy_path: Path | None,
) -> tuple[CodexBoundaryPolicy, list[AgentResultDiagnostic]]:
    diagnostics: list[AgentResultDiagnostic] = []
    discovery: list[str] = []
    explicit = policy_path is not None
    if explicit:
        candidate = policy_path if policy_path.is_absolute() else workspace / policy_path
        source = "explicit"
        display_path = _display_path(candidate, workspace)
        discovery.append(f"explicit:{display_path}")
    else:
        workspace_candidate = workspace / DEFAULT_POLICY_PATH
        if workspace_candidate.is_file():
            candidate = workspace_candidate
            source = "workspace"
            display_path = _display_path(candidate, workspace)
            discovery.append(f"workspace:{display_path}")
        else:
            candidate = None
            source = "packaged_default"
            display_path = None
            discovery.append(f"workspace_missing:{DEFAULT_POLICY_PATH.as_posix()}")
            discovery.append("packaged_default")
    data: dict[str, Any] | None = None
    raw_text: str | None = None
    if candidate is not None and candidate.is_file():
        try:
            raw_text = candidate.read_text(encoding="utf-8")
            loaded = yaml.safe_load(raw_text) or {}
            if isinstance(loaded, dict):
                data = loaded
            else:
                diagnostics.append(
                    AgentResultDiagnostic(
                        level="error" if explicit else "warning",
                        code="policy_load_failed",
                        message="Codex boundary policy did not contain a mapping; using defaults.",
                        path=display_path,
                    )
                )
        except (OSError, yaml.YAMLError) as exc:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="error" if explicit else "warning",
                    code="policy_load_failed",
                    message=f"Could not load Codex boundary policy: {exc}",
                    path=display_path,
                )
            )
            if explicit:
                return _default_policy(
                    source="invalid",
                    path=display_path,
                    discovery=discovery,
                ), diagnostics
    elif candidate is None:
        data = _load_packaged_default_policy()
        raw_text = _packaged_policy_text()
    else:
        diagnostics.append(
            AgentResultDiagnostic(
                level="error" if explicit else "warning",
                code="policy_missing",
                message="Codex boundary policy file was not found; using defaults.",
                path=display_path or str(policy_path),
            )
        )
        if explicit:
            return _default_policy(
                source="missing",
                path=display_path,
                discovery=discovery,
            ), diagnostics
        data = _load_packaged_default_policy()
        raw_text = _packaged_policy_text()
    if data is None:
        return _default_policy(source=source, path=display_path, discovery=discovery), diagnostics
    rules = dict(DEFAULT_RULES)
    unknown_fields = sorted(set(data) - {"id", "name", "version", "rules"})
    if unknown_fields:
        diagnostics.append(
            AgentResultDiagnostic(
                level="error" if explicit else "warning",
                code="policy_unknown_fields",
                message=(
                    "Codex boundary policy contains unknown top-level fields: "
                    + ", ".join(unknown_fields)
                ),
                path=display_path,
            )
        )
        if explicit:
            return _default_policy(
                source="invalid",
                path=display_path,
                discovery=discovery,
            ), diagnostics
    invalid_rule = False
    for raw_rule in data.get("rules") or []:
        if not isinstance(raw_rule, dict) or not isinstance(raw_rule.get("id"), str):
            invalid_rule = True
            continue
        unknown_rule_fields = sorted(
            set(raw_rule)
            - {"id", "check_id", "title", "action", "risk_level", "recommendation"}
        )
        if unknown_rule_fields:
            invalid_rule = True
            diagnostics.append(
                AgentResultDiagnostic(
                    level="error" if explicit else "warning",
                    code="policy_unknown_fields",
                    message=(
                        f"Codex boundary policy rule {raw_rule['id']!r} contains "
                        "unknown fields: "
                        + ", ".join(unknown_rule_fields)
                    ),
                    path=display_path,
                )
            )
        rule_id = raw_rule["id"]
        base = rules.get(rule_id)
        if base is None:
            invalid_rule = True
            continue
        action = str(raw_rule.get("action", base.action))
        if action not in _DECISION_RANK:
            invalid_rule = True
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
    if invalid_rule and explicit:
        return _default_policy(
            source="invalid",
            path=display_path,
            discovery=discovery,
        ), diagnostics
    snapshot = _policy_snapshot_sha256(data, raw_text)
    return CodexBoundaryPolicy(
        id=str(data.get("id") or "codex-boundary"),
        version=str(data.get("version") or DEFAULT_POLICY_VERSION),
        rules=rules,
        source=source,
        path=display_path,
        snapshot_sha256=snapshot,
        discovery=tuple(discovery),
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
    *,
    workspace: Path,
) -> None:
    path = diff_file.path
    invocation_present = bool(
        resolved.new_text
        and _has_shipgate_gate_invocation(resolved.new_text, workspace=workspace)
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


def _evaluate_codex_boundary_policy(
    diff_file: DiffFile,
    resolved: ResolvedFileText,
    add,
) -> None:
    weakened_action, weakened_risk, weakened_rules = _policy_weakening_from_diff(
        diff_file,
        resolved,
    )
    if diff_file.is_deleted or weakened_action or weakened_risk:
        evidence: dict[str, Any] = {
            "kind": "codex_boundary_policy_weakened",
            "deleted": diff_file.is_deleted,
            "weakened_action": weakened_action,
            "weakened_risk": weakened_risk,
        }
        if weakened_rules:
            evidence["weakened_rules"] = weakened_rules[:5]
        add(
            "CODEX-POLICY-WEAKENED",
            path=diff_file.path,
            evidence=evidence,
        )


def _policy_weakening_from_diff(
    diff_file: DiffFile,
    resolved: ResolvedFileText,
) -> tuple[bool, bool, list[dict[str, Any]]]:
    if diff_file.is_deleted:
        return False, False, []

    old_rules = _policy_rules_from_text(diff_file, resolved, side="old")
    new_rules = _policy_rules_from_text(diff_file, resolved, side="new")
    weakened_action = False
    weakened_risk = False
    weakened_rules: list[dict[str, Any]] = []

    for rule_id in sorted(old_rules):
        old_rule = old_rules[rule_id]
        new_rule = new_rules.get(rule_id)
        if new_rule is None:
            old_action = old_rule.get("action")
            old_risk = old_rule.get("risk_level")
            if old_action in _DECISION_RANK or old_risk in _RISK_RANK:
                weakened_action = old_action in _DECISION_RANK
                weakened_risk = old_risk in _RISK_RANK
                weakened_rules.append(
                    {
                        "id": rule_id,
                        "removed": True,
                        "old_action": old_action,
                        "new_action": None,
                        "old_risk_level": old_risk,
                        "new_risk_level": None,
                    }
                )
            continue

        old_action = old_rule.get("action")
        new_action = new_rule.get("action")
        action_weakened = (
            old_action in _DECISION_RANK
            and new_action in _DECISION_RANK
            and _DECISION_RANK[new_action] < _DECISION_RANK[old_action]
        )
        old_risk = old_rule.get("risk_level")
        new_risk = new_rule.get("risk_level")
        risk_weakened = (
            old_risk in _RISK_RANK
            and new_risk in _RISK_RANK
            and _RISK_RANK[new_risk] < _RISK_RANK[old_risk]
        )
        if not action_weakened and not risk_weakened:
            continue
        weakened_action = weakened_action or action_weakened
        weakened_risk = weakened_risk or risk_weakened
        weakened_rules.append(
            {
                "id": rule_id,
                "old_action": old_action,
                "new_action": new_action,
                "old_risk_level": old_risk,
                "new_risk_level": new_risk,
            }
        )
    return weakened_action, weakened_risk, weakened_rules


def _policy_rules_from_text(
    diff_file: DiffFile,
    resolved: ResolvedFileText,
    *,
    side: str,
) -> dict[str, dict[str, str]]:
    resolved_text = resolved.old_text if side == "old" else resolved.new_text
    text = (
        resolved_text
        if resolved_text is not None
        else _policy_side_text_from_diff(diff_file, side=side)
    )
    return _policy_rules_from_yaml_text(text)


def _policy_side_text_from_diff(diff_file: DiffFile, *, side: str) -> str:
    kinds = {"old": {" ", "-"}, "new": {" ", "+"}}[side]
    return "\n".join(
        text
        for hunk in diff_file.hunks
        for kind, text in hunk.lines
        if kind in kinds
    )


def _policy_rules_from_yaml_text(text: str) -> dict[str, dict[str, str]]:
    if not text.strip():
        return {}
    candidates = [text]
    stripped = text.lstrip()
    if stripped.startswith("- "):
        candidates.append("rules:\n" + text)

    for candidate in candidates:
        try:
            loaded = yaml.safe_load(candidate) or {}
        except yaml.YAMLError:
            continue
        raw_rules: Any
        if isinstance(loaded, dict):
            if isinstance(loaded.get("rules"), list):
                raw_rules = loaded["rules"]
            elif isinstance(loaded.get("id"), str):
                raw_rules = [loaded]
            else:
                raw_rules = []
        elif isinstance(loaded, list):
            raw_rules = loaded
        else:
            raw_rules = []

        rules: dict[str, dict[str, str]] = {}
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, dict):
                continue
            rule_id = raw_rule.get("id")
            if not isinstance(rule_id, str) or not rule_id.strip():
                continue
            rule: dict[str, str] = {}
            for key in ("action", "risk_level"):
                value = raw_rule.get(key)
                if isinstance(value, str):
                    rule[key] = value.strip()
            rules[rule_id.strip()] = rule
        if rules:
            return rules
    return {}


def _evaluate_skill(diff_file: DiffFile, add) -> None:
    if any(_COMMAND_SKILL_RE.search(line) for line in diff_file.added_lines):
        add(
            "CODEX-SKILL-COMMAND-CHANGED",
            path=diff_file.path,
            evidence={"kind": "command_bearing_skill_change"},
        )


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


# Canonical capability gate. check is boundary-only; verify computes the
# capability delta and owns release_decision.decision. Bare ``verify --json``
# auto-detects the base (v0.13) and emits the agent_result_v1 surface, so it
# works for both the local working tree and committed refs.
_VERIFY_COMMAND = "agents-shipgate verify --json"


def _coverage_next_action() -> AgentResultNextAction:
    return AgentResultNextAction(
        actor="coding_agent",
        kind="warn",
        command=_VERIFY_COMMAND,
        why=(
            "shipgate check is boundary-only and did not evaluate the changed tool "
            "surface; run verify for the capability merge gate before completing."
        ),
    )


def _coverage_summary(surfaces: list[str]) -> str:
    return (
        "No Codex boundary rule fired, but the diff changes a declared tool surface "
        f"({', '.join(surfaces[:5])}) that only verify gates. Run verify before "
        "reporting completion."
    )


def _coverage_diagnostic(surfaces: list[str]) -> AgentResultDiagnostic:
    return AgentResultDiagnostic(
        level="warning",
        code="capability_change_requires_verify",
        message=(
            "shipgate check is boundary-only; the changed tool surface(s) "
            f"{', '.join(surfaces[:5])} are gated by verify, not check."
        ),
    )


def _coverage_trace(surfaces: list[str]) -> AgentResultTraceEvent:
    return AgentResultTraceEvent(
        step="coverage",
        summary=(
            f"boundary_only: capability gating for {len(surfaces)} changed tool "
            "surface(s) deferred to verify."
        ),
    )


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
    repair: AgentResultRepair,
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
    if repair.safe_to_attempt:
        first = (
            repair.instructions[0]
            if repair.instructions
            else "Repair the Codex boundary change and rerun Shipgate."
        )
        return AgentResultNextAction(
            actor="coding_agent",
            kind="repair",
            command=repair.command,
            why=first,
        )
    first = violations[0].title if violations else "Codex boundary blocked"
    return AgentResultNextAction(actor="human", kind="stop", command=None, why=first)


def _human_review_for(
    decision: str,
    violations: list[AgentResultViolatedRule],
    repair: AgentResultRepair,
) -> AgentResultHumanReview:
    required = decision in {"require_review", "block"} and not repair.safe_to_attempt
    return AgentResultHumanReview(
        required=required,
        why=(violations[0].title if required and violations else None),
        required_reviewers=_required_reviewers_for(decision, violations) if required else [],
    )


def _repair_for(
    decision: str,
    violations: list[AgentResultViolatedRule],
    agent: str,
) -> AgentResultRepair:
    forbidden = (
        "Do not suppress the finding (checks.ignore in shipgate.yaml).",
        "Do not lower severity or add a waiver just to pass the gate.",
        "Do not invent or assume approval, idempotency, or audit evidence you cannot prove from the code.",
        "Do not weaken the release policy, CI gate, or agent instructions that evaluate this change.",
    )
    safe_to_attempt = _agent_safe_repairable(decision, violations)
    command = (
        f"shipgate check --agent {agent} --workspace . --format agent-json"
        if safe_to_attempt
        else None
    )
    instructions = [item.recommendation for item in violations[:5]]
    if safe_to_attempt and command:
        instructions.append(f"Rerun: {command}")
    return AgentResultRepair(
        actor="coding_agent" if safe_to_attempt or decision in {"allow", "warn"} else "human",
        safe_to_attempt=safe_to_attempt,
        instructions=instructions,
        command=command,
        forbidden_shortcuts=list(forbidden),
    )


def _agent_safe_repairable(
    decision: str,
    violations: list[AgentResultViolatedRule],
) -> bool:
    if decision != "block" or not violations:
        return False
    return all(item.id in _AGENT_SAFE_REPAIR_RULE_IDS for item in violations)


def _policy_result(policy: CodexBoundaryPolicy) -> AgentResultPolicy:
    return AgentResultPolicy(
        id=policy.id,
        version=policy.version,
        source=policy.source,  # type: ignore[arg-type]
        snapshot_sha256=policy.snapshot_sha256,
        path=policy.path,
        discovery=list(policy.discovery),
    )


def _affected_files_for(
    violations: list[AgentResultViolatedRule],
    changed_files: list[str],
) -> list[AgentResultAffectedFile]:
    paths = [item.path for item in violations if item.path]
    if not paths:
        paths = changed_files
    return [AgentResultAffectedFile(path=path) for path in sorted(dict.fromkeys(paths))[:20]]


def _required_reviewers_for(
    decision: str,
    violations: list[AgentResultViolatedRule],
) -> list[str]:
    if decision == "allow" or decision == "warn":
        return []
    reviewers = {"agent-platform"}
    if decision == "block" or any(item.risk_level == "critical" for item in violations):
        reviewers.add("security")
    return sorted(reviewers)


def _agent_repair_instructions(
    decision: str,
    violations: list[AgentResultViolatedRule],
) -> list[str]:
    if decision in {"allow", "warn"}:
        return []
    return [item.recommendation for item in violations[:5]]


def _trace_for(
    policy: CodexBoundaryPolicy,
    decision: str,
    violations: list[AgentResultViolatedRule],
) -> list[AgentResultTraceEvent]:
    return [
        AgentResultTraceEvent(
            step="policy_discovery",
            summary=(
                f"Loaded policy {policy.id} from {policy.source}"
                + (f" at {policy.path}" if policy.path else "")
                + "."
            ),
        ),
        AgentResultTraceEvent(
            step="decision",
            summary=f"Projected {len(violations)} violation(s) to {decision}.",
        ),
    ]


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
    text = _packaged_policy_text()
    if text is None:
        return None
    try:
        loaded = yaml.safe_load(text) or {}
        return loaded if isinstance(loaded, dict) else None
    except yaml.YAMLError:
        return None


def _packaged_policy_text() -> str | None:
    candidate = (
        Path(__file__).resolve().parents[1]
        / "_meta"
        / "policies"
        / "codex-boundary.shipgate.yaml"
    )
    try:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def _default_policy(
    *,
    source: str,
    path: str | None,
    discovery: list[str],
) -> CodexBoundaryPolicy:
    payload = {
        "id": "codex-boundary-default",
        "version": DEFAULT_POLICY_VERSION,
        "rules": [
            {
                "id": rule.id,
                "check_id": rule.check_id,
                "title": rule.title,
                "action": rule.action,
                "risk_level": rule.risk_level,
                "recommendation": rule.recommendation,
            }
            for rule in DEFAULT_RULES.values()
        ],
    }
    return CodexBoundaryPolicy(
        id="codex-boundary-default",
        version=DEFAULT_POLICY_VERSION,
        rules=dict(DEFAULT_RULES),
        source=source,
        path=path,
        snapshot_sha256=_policy_snapshot_sha256(payload, None),
        discovery=tuple(discovery),
    )


def _policy_snapshot_sha256(data: dict[str, Any], raw_text: str | None) -> str:
    if raw_text is not None:
        payload: object = yaml.safe_load(raw_text) if raw_text.strip() else data
        if not isinstance(payload, dict):
            payload = data
    else:
        payload = data
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _display_path(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)


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


def _has_shipgate_gate_invocation(value: str, *, workspace: Path | None = None) -> bool:
    local_action_is_shipgate = (
        _workspace_declares_shipgate_action(workspace) if workspace is not None else False
    )
    for line in value.splitlines():
        if _SHIPGATE_INVOCATION_RE.search(line) or _SHIPGATE_ACTION_RE.search(line):
            return True
        if local_action_is_shipgate and _LOCAL_ACTION_RE.search(line):
            return True
    return False


def _workspace_declares_shipgate_action(workspace: Path) -> bool:
    action_path = workspace / "action.yml"
    if not action_path.is_file():
        action_path = workspace / "action.yaml"
    if not action_path.is_file():
        return False
    try:
        payload = yaml.safe_load(action_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(payload, dict):
        return False
    name = str(payload.get("name") or "").strip().lower()
    return name == "agents shipgate" and _root_action_invokes_shipgate(payload)


def _root_action_invokes_shipgate(payload: dict[str, Any]) -> bool:
    runs = payload.get("runs")
    if not isinstance(runs, dict):
        return False
    steps = runs.get("steps")
    if not isinstance(steps, list):
        return False
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if isinstance(run, str) and _run_script_invokes_shipgate(run):
            return True
        uses = step.get("uses")
        if isinstance(uses, str) and _SHIPGATE_ACTION_RE.search(f"uses: {uses}"):
            return True
    return False


def _run_script_invokes_shipgate(value: str) -> bool:
    return any(_SHIPGATE_CLI_COMMAND_RE.search(line) for line in value.splitlines())


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


def _is_codex_boundary_policy_path(path: str) -> bool:
    return path == DEFAULT_POLICY_PATH.as_posix() or path.endswith(
        f"/{DEFAULT_POLICY_PATH.as_posix()}"
    )


def _is_codex_skill_path(path: str) -> bool:
    return (
        path.endswith("/SKILL.md")
        and (path.startswith(".agents/skills/") or "/.agents/skills/" in path)
    )
