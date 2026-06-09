from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.core.capabilities import build_capability_facts
from agents_shipgate.core.capability_delta import diff_capability_fact_sets
from agents_shipgate.core.capability_lattice import classify_tool_permission
from agents_shipgate.core.codex_boundary import (
    _is_codex_config_path,
    _resolve_changed_file_text,
    parse_unified_diff,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.inputs.mcp_manifest import (
    NormalizedMcpServer,
    normalize_codex_config_mcp_servers,
    tools_from_normalized_mcp_servers,
)
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultDiagnostic,
    AgentResultNextAction,
    AgentResultV1,
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
    del config, policy
    if format_ not in {"json", "agent-json"}:
        typer.echo("--format must be 'json' or 'agent-json'.", err=True)
        raise typer.Exit(2)
    try:
        diff_text = sys.stdin.read() if diff == "-" else Path(diff).read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Could not read --diff input: {exc}", err=True)
        raise typer.Exit(2) from exc

    audit = build_mcp_audit(workspace=workspace, diff_text=diff_text)
    if format_ == "agent-json":
        typer.echo(_agent_result_json(_agent_result_from_audit(audit)))
        return
    typer.echo(json.dumps(audit, indent=2, sort_keys=False))


def build_mcp_audit(*, workspace: Path, diff_text: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    diagnostics: list[AgentResultDiagnostic] = []
    base_tools: list[Tool] = []
    head_tools: list[Tool] = []
    servers: list[NormalizedMcpServer] = []
    changed_files = sorted({item.path for item in diff_files if item.path})

    for diff_file in diff_files:
        if not diff_file.path or not _is_codex_config_path(diff_file.path):
            continue
        resolved = _resolve_changed_file_text(workspace, diff_file, diagnostics)
        if resolved.old_text:
            base_tools.extend(
                _tools_from_toml(
                    resolved.old_text,
                    source_ref=diff_file.path,
                    source_path=diff_file.path,
                    diagnostics=diagnostics,
                )
            )
        if resolved.new_text:
            parsed_servers, tools = _servers_tools_from_toml(
                resolved.new_text,
                source_ref=diff_file.path,
                source_path=diff_file.path,
                diagnostics=diagnostics,
            )
            servers.extend(parsed_servers)
            head_tools.extend(tools)

    base_facts = build_capability_facts(_minimal_manifest(), agent_id="agent:mcp-audit", tools=base_tools)
    head_facts = build_capability_facts(_minimal_manifest(), agent_id="agent:mcp-audit", tools=head_tools)
    diff = diff_capability_fact_sets(base_facts, head_facts)
    violations = _audit_violations(head_tools, diff)
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
        "policy_version": "1",
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
        "risk_scores": [_risk_record(tool) for tool in sorted(head_tools, key=lambda item: item.name)],
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


def _audit_violations(
    tools: list[Tool],
    diff: Any,
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    added_ids = {ctx.fact.id for ctx in diff.added}
    changed_ids = {row.after.id for row in [*diff.reidentified, *diff.changed]}
    fact_by_tool = {
        ctx.fact.identity.tool_name: ctx.fact
        for ctx in [*diff.added, *[row.after_context for row in [*diff.reidentified, *diff.changed] if row.after_context]]
    }
    for tool in tools:
        fact = fact_by_tool.get(tool.name)
        fact_id = fact.id if fact else None
        profile = classify_tool_permission(tool)
        secret_names = list(tool.annotations.get("mcp_env_secret_names") or [])
        if secret_names:
            violations.append(
                _violation(
                    "MCP-ENV-SECRET-PASSTHROUGH",
                    path=tool.source_path,
                    evidence={"tool": tool.name, "env_secret_names": secret_names},
                )
            )
        if (
            str(tool.annotations.get("mcp_approval_mode") or "").lower() == "approve"
            and any(
                item in {"write", "destructive", "external", "financial", "production"}
                for item in profile.classes
            )
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
                )
            )
        auto_approved_side_effect = (
            str(tool.annotations.get("mcp_approval_mode") or "").lower() == "approve"
            and any(
                item in {"write", "destructive", "external", "financial", "production"}
                for item in profile.classes
            )
        )
        if (
            tool.annotations.get("mcp_unknown_schema") is True
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
            )
        )
    return _dedupe_violations(violations)


def _violation(rule_id: str, *, path: str | None, evidence: dict[str, Any]) -> dict[str, Any]:
    rule = _RULES[rule_id]
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


def _agent_result_from_audit(audit: dict[str, Any]) -> AgentResultV1:
    decision = audit["decision"]
    return AgentResultV1(
        decision=decision,
        risk_level=audit["risk_level"],
        audit_id=audit["audit_id"],
        policy_version=audit["policy_version"],
        summary=audit["summary"],
        changed_files=audit["changed_files"],
        first_next_action=_next_action(decision, audit["violated_rules"]),
        violated_rules=[
            AgentResultViolatedRule.model_validate(item)
            for item in audit["violated_rules"]
        ],
        diagnostics=[
            AgentResultDiagnostic.model_validate(item)
            for item in audit["diagnostics"]
        ],
        finding_fingerprints=[
            _fingerprint(item) for item in audit["violated_rules"]
        ],
    )


def _next_action(decision: str, violations: list[dict[str, Any]]) -> AgentResultNextAction:
    if decision == "allow":
        return AgentResultNextAction(
            actor="coding_agent",
            kind="continue",
            why="No MCP permission rule requires review or blocking.",
        )
    if decision == "warn":
        return AgentResultNextAction(
            actor="coding_agent",
            kind="warn",
            why="MCP audit completed with warnings.",
        )
    if decision == "require_review":
        why = violations[0]["title"] if violations else "MCP permission review required"
        return AgentResultNextAction(actor="human", kind="review", why=why)
    why = violations[0]["title"] if violations else "MCP permission change blocked"
    return AgentResultNextAction(actor="human", kind="stop", why=why)


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


def _agent_result_json(result: AgentResultV1) -> str:
    return json.dumps(result.model_dump(mode="json", exclude_none=True), indent=2)


__all__ = ["build_mcp_audit", "mcp_app"]
