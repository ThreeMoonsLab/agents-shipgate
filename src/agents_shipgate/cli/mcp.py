from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import typer
import yaml

from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.capabilities import build_capability_facts
from agents_shipgate.core.capability_delta import diff_capability_fact_sets
from agents_shipgate.core.capability_lattice import classify_tool_permission
from agents_shipgate.core.codex_boundary import (
    is_codex_config_path,
    is_mcp_json_path,
    parse_unified_diff,
    resolve_changed_file_text,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.inputs.mcp_manifest import (
    NormalizedMcpServer,
    normalize_codex_config_mcp_servers,
    normalize_mcp_json_servers,
    tools_from_normalized_mcp_servers,
)
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.agent_result import AgentResultV2
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultAffectedFile,
    AgentResultDiagnostic,
    AgentResultHumanReview,
    AgentResultPolicy,
    AgentResultRepair,
    AgentResultViolatedRule,
)
from agents_shipgate.schemas.common import parse_confidence

DEFAULT_MCP_POLICY_PATH = Path("policies/mcp-permissions.shipgate.yaml")

mcp_app = typer.Typer(help="Audit MCP capability and permission changes.")

_DECISION_RANK = {"allow": 0, "warn": 1, "require_review": 2, "block": 3}
_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_RULES: dict[str, dict[str, str]] = {
    "MCP-ENV-SECRET-PASSTHROUGH": {
        "check_id": "SHIP-MCP-ENV-SECRET-PASSTHROUGH",
        "title": "MCP server passes through secret environment variables",
        "action": "require_review",
        "risk_level": "high",
        "recommendation": "Review the MCP server secret boundary.",
    },
    "MCP-AUTO-APPROVE-SIDE-EFFECT": {
        "check_id": "SHIP-MCP-AUTO-APPROVE-SIDE-EFFECT",
        "title": "MCP side-effecting tool is auto-approved",
        "action": "block",
        "risk_level": "critical",
        "recommendation": "Do not auto-approve side-effecting MCP tools.",
    },
    "MCP-UNKNOWN-TOOL-SCHEMA": {
        "check_id": "SHIP-MCP-UNKNOWN-TOOL-SCHEMA",
        "title": "MCP tool side effect cannot be proven from static schema",
        "action": "require_review",
        "risk_level": "high",
        "recommendation": "Provide an explicit MCP tool inventory/schema.",
    },
    "MCP-PERMISSION-EXPANDED": {
        "check_id": "SHIP-MCP-PERMISSION-EXPANDED",
        "title": "MCP capability permissions expanded",
        "action": "require_review",
        "risk_level": "high",
        "recommendation": "Review the expanded MCP capability.",
    },
    "MCP-READONLY-SERVER-ADDED": {
        "check_id": "SHIP-MCP-READONLY-SERVER-ADDED",
        "title": "Read-only local documentation MCP server added",
        "action": "warn",
        "risk_level": "low",
        "recommendation": "Keep the MCP server read-only and local.",
    },
}


def _load_mcp_policy(
    policy: Path | None,
    workspace: Path,
    diagnostics: list[AgentResultDiagnostic],
) -> tuple[dict[str, dict[str, str]], str]:
    rules = {rule_id: dict(rule) for rule_id, rule in _RULES.items()}
    if policy is None:
        return rules, "builtin"

    policy_path = policy if policy.is_absolute() else workspace / policy
    raw: dict[str, Any] | None = None
    if policy_path.is_file():
        try:
            loaded = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, yaml.YAMLError) as exc:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="warning",
                    code="mcp_policy_parse_failed",
                    message=f"MCP permission policy could not be parsed; using built-in defaults: {exc}",
                    path=str(policy_path),
                )
            )
    elif policy == DEFAULT_MCP_POLICY_PATH:
        raw = _load_packaged_default_mcp_policy()
    else:
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code="mcp_policy_missing",
                message="MCP permission policy not found; using built-in defaults.",
                path=str(policy),
            )
        )

    if raw is None:
        return rules, "builtin"

    version = str(raw.get("version") or "1")
    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list):
        return rules, version

    check_id_to_rule = {rule["check_id"]: rule_id for rule_id, rule in rules.items()}
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            continue
        rule_id = str(raw_rule.get("id") or "")
        if rule_id not in rules:
            rule_id = check_id_to_rule.get(str(raw_rule.get("check_id") or ""), "")
        if rule_id not in rules:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="warning",
                    code="mcp_policy_unknown_rule",
                    message="Ignoring unknown MCP permission policy rule.",
                    path=str(policy_path),
                )
            )
            continue
        updated = dict(rules[rule_id])
        for field in ("check_id", "title", "recommendation"):
            value = raw_rule.get(field)
            if isinstance(value, str) and value:
                updated[field] = value
        action = str(raw_rule.get("action") or updated["action"])
        if action in _DECISION_RANK:
            updated["action"] = action
        else:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="warning",
                    code="mcp_policy_invalid_action",
                    message=f"Ignoring invalid MCP policy action {action!r}.",
                    path=str(policy_path),
                )
            )
        risk_level = str(raw_rule.get("risk_level") or updated["risk_level"])
        if risk_level in _RISK_RANK:
            updated["risk_level"] = risk_level
        else:
            diagnostics.append(
                AgentResultDiagnostic(
                    level="warning",
                    code="mcp_policy_invalid_risk_level",
                    message=f"Ignoring invalid MCP policy risk_level {risk_level!r}.",
                    path=str(policy_path),
                )
            )
        rules[rule_id] = updated
    return rules, version


def _load_packaged_default_mcp_policy() -> dict[str, Any] | None:
    candidate = (
        Path(__file__).resolve().parents[1] / "_meta" / "policies" / "mcp-permissions.shipgate.yaml"
    )
    try:
        if candidate.is_file():
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            return loaded if isinstance(loaded, dict) else None
    except (OSError, yaml.YAMLError):
        return None
    return None


@mcp_app.command("audit")
def mcp_audit(
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        help="Workspace root containing the Codex MCP config.",
    ),
    config: Path = typer.Option(
        Path("shipgate.yaml"),
        "--config",
        "-c",
        help="Shipgate manifest path, accepted for command symmetry.",
    ),
    diff: str = typer.Option(
        "-",
        "--diff",
        help="Unified diff file to audit, or '-' to read stdin.",
    ),
    policy: Path = typer.Option(
        DEFAULT_MCP_POLICY_PATH,
        "--policy",
        help="MCP permission policy file.",
    ),
    format_: str = typer.Option(
        "json",
        "--format",
        help="Output format: json or agent-json.",
    ),
) -> None:
    del config
    if format_ not in {"json", "agent-json"}:
        typer.echo("--format must be 'json' or 'agent-json'.", err=True)
        raise typer.Exit(2)
    try:
        if diff == "-" and sys.stdin.isatty():
            typer.echo("Reading unified diff from stdin; press Ctrl-D when done.", err=True)
        diff_text = sys.stdin.read() if diff == "-" else Path(diff).read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Could not read --diff input: {exc}", err=True)
        raise typer.Exit(2) from exc

    audit = build_mcp_audit(workspace=workspace, diff_text=diff_text, policy=policy)
    if format_ == "agent-json":
        typer.echo(_agent_result_json(_agent_result_from_audit(audit)))
        return
    typer.echo(json.dumps(audit, indent=2, sort_keys=False))


def build_mcp_audit(
    *,
    workspace: Path,
    diff_text: str,
    policy: Path | None = DEFAULT_MCP_POLICY_PATH,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    diagnostics: list[AgentResultDiagnostic] = []
    rules, policy_version = _load_mcp_policy(policy, workspace, diagnostics)
    base_tools: list[Tool] = []
    head_tools: list[Tool] = []
    servers: list[NormalizedMcpServer] = []
    changed_files = sorted(
        {
            path
            for item in diff_files
            for path in (item.new_path, item.old_path)
            if path
        }
    )

    for diff_file in diff_files:
        old_path = diff_file.old_path
        new_path = diff_file.new_path
        resolved = None
        old_is_codex = bool(old_path and is_codex_config_path(old_path))
        new_is_codex = bool(new_path and is_codex_config_path(new_path))
        if old_is_codex or new_is_codex:
            resolved = resolve_changed_file_text(
                workspace,
                diff_file,
                diagnostics,
                preserve_rename_source=True,
            )
            if old_is_codex and resolved.old_text and old_path:
                base_tools.extend(
                    _tools_from_toml(
                        resolved.old_text,
                        source_ref=old_path,
                        source_path=old_path,
                        diagnostics=diagnostics,
                    )
                )
            if new_is_codex and resolved.new_text and new_path:
                parsed_servers, tools = _servers_tools_from_toml(
                    resolved.new_text,
                    source_ref=new_path,
                    source_path=new_path,
                    diagnostics=diagnostics,
                )
                servers.extend(parsed_servers)
                head_tools.extend(tools)
        old_is_mcp = bool(old_path and is_mcp_json_path(old_path))
        new_is_mcp = bool(new_path and is_mcp_json_path(new_path))
        if old_is_mcp or new_is_mcp:
            if resolved is None:
                resolved = resolve_changed_file_text(
                    workspace,
                    diff_file,
                    diagnostics,
                    preserve_rename_source=True,
                )
            if old_is_mcp and resolved.old_text and old_path:
                base_tools.extend(
                    _tools_from_mcp_json(
                        resolved.old_text,
                        source_ref=old_path,
                        source_path=old_path,
                        diagnostics=diagnostics,
                    )
                )
            if new_is_mcp and resolved.new_text and new_path:
                parsed_servers, tools = _servers_tools_from_mcp_json(
                    resolved.new_text,
                    source_ref=new_path,
                    source_path=new_path,
                    diagnostics=diagnostics,
                )
                servers.extend(parsed_servers)
                head_tools.extend(tools)

    base_facts = build_capability_facts(
        _minimal_manifest(),
        agent_id="agent:mcp-audit",
        tools=base_tools,
    )
    head_facts = build_capability_facts(
        _minimal_manifest(),
        agent_id="agent:mcp-audit",
        tools=head_tools,
    )
    diff = diff_capability_fact_sets(base_facts, head_facts)
    violations = _audit_violations(head_tools, diff, rules=rules)
    decision = _decision(violations)
    risk_level = _risk_level(violations)
    payload = {
        "schema_version": "mcp_audit_v1",
        "decision": decision,
        "risk_level": risk_level,
        "audit_id": _audit_id(
            changed_files=changed_files,
            base_tools=base_tools,
            head_tools=head_tools,
            violations=violations,
        ),
        "policy_version": policy_version,
        "summary": _summary(decision, violations),
        "changed_files": changed_files,
        "servers": [_server_record(server) for server in servers],
        "capability_delta": {
            "added": [fact.fact.id for fact in diff.added],
            "removed": [fact.fact.id for fact in diff.removed],
            "reidentified": [_row_record(row) for row in diff.reidentified],
            "changed": [_row_record(row) for row in diff.changed],
            "evidence_changed": [_row_record(row) for row in diff.evidence_changed],
            "unchanged": diff.unchanged_count,
        },
        "risk_scores": [
            _risk_record(tool) for tool in sorted(head_tools, key=lambda item: item.name)
        ],
        "violated_rules": violations,
        "diagnostics": [diag.model_dump(mode="json", exclude_none=True) for diag in diagnostics],
    }
    return payload


def _servers_tools_from_toml(
    text: str,
    *,
    source_ref: str,
    source_path: str,
    diagnostics: list[AgentResultDiagnostic],
) -> tuple[list[NormalizedMcpServer], list[Tool]]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code="mcp_toml_parse_failed",
                message=str(exc),
                path=source_ref,
            )
        )
        return [], []
    servers = normalize_codex_config_mcp_servers(
        data,
        source_ref=source_ref,
        source_path=source_path,
    )
    return servers, tools_from_normalized_mcp_servers(servers)


def _tools_from_toml(
    text: str,
    *,
    source_ref: str,
    source_path: str,
    diagnostics: list[AgentResultDiagnostic],
) -> list[Tool]:
    return _servers_tools_from_toml(
        text,
        source_ref=source_ref,
        source_path=source_path,
        diagnostics=diagnostics,
    )[1]


def _servers_tools_from_mcp_json(
    text: str,
    *,
    source_ref: str,
    source_path: str,
    diagnostics: list[AgentResultDiagnostic],
) -> tuple[list[NormalizedMcpServer], list[Tool]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code="mcp_json_parse_failed",
                message=str(exc),
                path=source_ref,
            )
        )
        return [], []
    if not isinstance(data, dict):
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code="mcp_json_parse_failed",
                message="MCP JSON root must be an object.",
                path=source_ref,
            )
        )
        return [], []
    servers = normalize_mcp_json_servers(
        data,
        source_ref=source_ref,
        source_path=source_path,
    )
    return servers, tools_from_normalized_mcp_servers(servers)


def _tools_from_mcp_json(
    text: str,
    *,
    source_ref: str,
    source_path: str,
    diagnostics: list[AgentResultDiagnostic],
) -> list[Tool]:
    return _servers_tools_from_mcp_json(
        text,
        source_ref=source_ref,
        source_path=source_path,
        diagnostics=diagnostics,
    )[1]


def _tool_key(tool: Tool) -> tuple[str | None, str]:
    return (tool.source_id, tool.name)


def _fact_key(fact: Any) -> tuple[str | None, str]:
    return (fact.evidence.source_id, fact.identity.tool_name)


def _audit_violations(
    tools: list[Tool],
    diff: Any,
    *,
    rules: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    added_ids = {ctx.fact.id for ctx in diff.added}
    changed_ids = {row.after.id for row in [*diff.reidentified, *diff.changed]}
    fact_by_tool = {
        _fact_key(ctx.fact): ctx.fact
        for ctx in [
            *diff.added,
            *[
                row.after_context
                for row in [*diff.reidentified, *diff.changed]
                if row.after_context
            ],
        ]
    }
    for tool in tools:
        fact = fact_by_tool.get(_tool_key(tool))
        fact_id = fact.id if fact else None
        profile = classify_tool_permission(tool)
        secret_names = list(tool.annotations.get("mcp_env_secret_names") or [])
        if secret_names:
            violations.append(
                _violation(
                    "MCP-ENV-SECRET-PASSTHROUGH",
                    path=tool.source_path,
                    evidence={"tool": tool.name, "env_secret_names": secret_names},
                    rules=rules,
                )
            )
        if (
            str(tool.annotations.get("mcp_approval_mode") or "").lower() == "approve"
            and any(
                item in {"write", "destructive", "external", "financial", "production"}
                for item in profile.classes
            )
            and _has_structural_side_effect(tool)
        ):
            violations.append(
                _violation(
                    "MCP-AUTO-APPROVE-SIDE-EFFECT",
                    path=tool.source_path,
                    evidence={
                        "tool": tool.name,
                        "permission_classes": list(profile.classes),
                        "risk_score": profile.risk_score,
                    },
                    rules=rules,
                )
            )
        auto_approved_side_effect = (
            str(tool.annotations.get("mcp_approval_mode") or "").lower() == "approve"
            and any(
                item in {"write", "destructive", "external", "financial", "production"}
                for item in profile.classes
            )
            and _has_structural_side_effect(tool)
        )
        if (
            (tool.annotations.get("mcp_unknown_schema") is True or profile.side_effect_unknown)
            and fact_id in added_ids | changed_ids
            and not auto_approved_side_effect
        ):
            violations.append(
                _violation(
                    "MCP-UNKNOWN-TOOL-SCHEMA",
                    path=tool.source_path,
                    evidence={
                        "tool": tool.name,
                        "capability_id": fact_id,
                        "wildcard": bool(tool.annotations.get("wildcard_tools")),
                    },
                    rules=rules,
                )
            )
        if (
            fact_id in added_ids
            and tool.annotations.get("mcp_local_documentation") is True
            and profile.is_read_only
        ):
            violations.append(
                _violation(
                    "MCP-READONLY-SERVER-ADDED",
                    path=tool.source_path,
                    evidence={
                        "tool": tool.name,
                        "capability_id": fact_id,
                        "risk_score": profile.risk_score,
                    },
                    rules=rules,
                )
            )
    for row in [*diff.reidentified, *diff.changed]:
        if row.semantic_direction not in {"broadened", "mixed", "unknown"}:
            continue
        violations.append(
            _violation(
                "MCP-PERMISSION-EXPANDED",
                path=row.after.evidence.source_path,
                evidence={
                    "capability_id": row.after.id,
                    "tool": row.after.identity.tool_name,
                    "semantic_direction": row.semantic_direction,
                },
                rules=rules,
            )
        )
    return _dedupe_violations(violations)


def _has_structural_side_effect(tool: Tool) -> bool:
    from agents_shipgate.core.semantic_assessment import assess_tool_semantics

    assessment = tool.semantic_assessment or assess_tool_semantics(tool)
    return any(
        claim.value != "read"
        and claim.policy_eligible
        for claim in assessment.effect.claims
    )


def _violation(
    rule_id: str,
    *,
    path: str | None,
    evidence: dict[str, Any],
    rules: dict[str, dict[str, str]],
) -> dict[str, Any]:
    rule = rules[rule_id]
    return {
        "id": rule_id,
        "check_id": rule["check_id"],
        "action": rule["action"],
        "risk_level": rule["risk_level"],
        "title": rule["title"],
        "path": path,
        "evidence": evidence,
        "recommendation": rule["recommendation"],
    }


def _agent_result_from_audit(audit: dict[str, Any]) -> AgentResultV2:
    decision = audit["decision"]
    human_review = _human_review(decision, audit["violated_rules"])
    repair = AgentResultRepair(
        actor="human",
        safe_to_attempt=False,
        instructions=[],
        forbidden_shortcuts=[
            "Do not weaken MCP policy to make this audit pass.",
            "Do not suppress or baseline MCP permission expansion without human review.",
        ],
    )
    summary = audit["summary"]
    control = (
        derive_agent_control(
            reason=summary,
            next_action=HumanControlAction(
                kind="stop" if decision == "block" else "review",
                why=human_review.why or summary,
            ),
            human_review_required=True,
            unsafe_block=decision == "block",
            human_review_why=human_review.why or summary,
            required_reviewers=human_review.required_reviewers,
            stop_reason=human_review.why or summary,
        )
        if decision in {"block", "require_review"}
        else derive_agent_control(reason=summary)
    )
    return AgentResultV2(
        decision=decision,  # type: ignore[arg-type]
        risk_level=audit["risk_level"],
        audit_id=audit["audit_id"],
        policy_version=audit["policy_version"],
        summary=summary,
        changed_files=audit["changed_files"],
        control=control,
        repair=repair,
        policy=AgentResultPolicy(
            id="mcp-permissions",
            version=audit["policy_version"],
            source="packaged_default" if audit["policy_version"] == "builtin" else "workspace",
            discovery=["mcp audit policy"],
        ),
        violated_rules=[
            AgentResultViolatedRule.model_validate(item) for item in audit["violated_rules"]
        ],
        affected_files=[AgentResultAffectedFile(path=path) for path in audit["changed_files"]],
        required_reviewers=human_review.required_reviewers,
        diagnostics=[AgentResultDiagnostic.model_validate(item) for item in audit["diagnostics"]],
        finding_fingerprints=[_fingerprint(item) for item in audit["violated_rules"]],
        source_artifacts={"mcp_audit": "stdout"},
    )


def _human_review(decision: str, violations: list[dict[str, Any]]) -> AgentResultHumanReview:
    if decision not in {"block", "require_review"}:
        return AgentResultHumanReview(required=False)
    why = violations[0]["title"] if violations else "MCP permission review required"
    reviewers = ["security"] if decision == "block" else ["agent-platform"]
    return AgentResultHumanReview(
        required=True,
        why=why,
        required_reviewers=reviewers,
    )


def _minimal_manifest():
    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "mcp-audit"},
            "agent": {
                "name": "mcp-audit",
                "declared_purpose": ["Audit MCP capability changes."],
            },
            "environment": {"target": "local"},
            "tool_sources": [{"id": "mcp_audit", "type": "mcp", "path": "mcp.json"}],
        }
    )


def _server_record(server: NormalizedMcpServer) -> dict[str, Any]:
    return {
        "name": server.name,
        "source_id": server.source_id,
        "source_ref": server.source_ref,
        "default_tools_approval_mode": server.default_tools_approval_mode,
        "transport": server.transport,
        "url_present": server.url_present,
        "env_secret_names": list(server.env_secret_names),
        "local_documentation": server.local_documentation,
        "wildcard": server.wildcard,
        "tool_names": [tool.name for tool in server.tools],
    }


def _row_record(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "tool_name": row.after.identity.tool_name,
        "semantic_direction": row.semantic_direction,
        "changed_hashes": list(row.changed_hashes),
    }


def _risk_record(tool: Tool) -> dict[str, Any]:
    profile = classify_tool_permission(tool)
    return {
        "tool": tool.name,
        "source_id": tool.source_id,
        "permission_classes": list(profile.classes),
        "risk_level": profile.risk_level,
        "risk_score": profile.risk_score,
        "side_effect_unknown": profile.side_effect_unknown,
        "confidence": parse_confidence(tool.extraction_confidence),
        "reasons": list(profile.reasons),
    }


def _decision(violations: list[dict[str, Any]]) -> str:
    decision = "allow"
    for item in violations:
        if _DECISION_RANK[item["action"]] > _DECISION_RANK[decision]:
            decision = item["action"]
    return decision


def _risk_level(violations: list[dict[str, Any]]) -> str:
    if not violations:
        return "none"
    return max((item["risk_level"] for item in violations), key=lambda item: _RISK_RANK[item])


def _summary(decision: str, violations: list[dict[str, Any]]) -> str:
    if decision == "allow":
        return "No MCP permission changes require action."
    if decision == "warn":
        return "MCP permission audit completed with warnings."
    if decision == "require_review":
        return f"{len(violations)} MCP permission change(s) require human review."
    return f"{len(violations)} MCP permission change(s) block local continuation."


def _audit_id(
    *,
    changed_files: list[str],
    base_tools: list[Tool],
    head_tools: list[Tool],
    violations: list[dict[str, Any]],
) -> str:
    payload = {
        "schema_version": "mcp_audit_v1",
        "changed_files": changed_files,
        "base_tools": [_tool_identity(tool) for tool in base_tools],
        "head_tools": [_tool_identity(tool) for tool in head_tools],
        "violations": violations,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    return f"mcp_audit_{digest}"


def _tool_identity(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "source_id": tool.source_id,
        "annotations": tool.annotations,
        "scopes": tool.auth.scopes,
        "input_schema": tool.input_schema,
    }


def _fingerprint(item: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(item, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"fp_{digest}"


def _dedupe_violations(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {json.dumps(item, sort_keys=True, default=str): item for item in violations}
    return [by_key[key] for key in sorted(by_key)]


def _agent_result_json(result: AgentResultV2) -> str:
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["control"] = result.control.model_dump(mode="json")
    return json.dumps(payload, indent=2)


__all__ = ["build_mcp_audit", "mcp_app"]
