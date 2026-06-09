from __future__ import annotations

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.capabilities import capability_fact_from_action_fact
from agents_shipgate.core.capability_delta import (
    CapabilityDeltaRow,
    CapabilityFactContext,
    diff_capability_fact_sets,
)
from agents_shipgate.core.capability_lattice import classify_tool_permission
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Tool
from agents_shipgate.schemas.capabilities import CapabilityFactV1
from agents_shipgate.schemas.report import Finding
from agents_shipgate.schemas.surfaces import ActionFact, ActionSurfaceFacts

MCP_SOURCE_TYPES = frozenset(
    {
        "mcp",
        "codex_config_mcp",
        "codex_plugin_mcp_inventory",
        "n8n_mcp_client_tool",
    }
)
CapabilityKey = tuple[str | None, str]


def run(context: ScanContext) -> list[Finding]:
    mcp_tools = [tool for tool in context.tools if _is_mcp_tool(tool)]
    if not mcp_tools:
        return []

    findings: list[Finding] = []
    current_contexts = _mcp_capability_contexts(context.action_surface_facts)
    diff_available = (
        context.diff_reference is not None
        and context.diff_reference.action_facts is not None
    )
    base_contexts = (
        _mcp_capability_contexts(context.diff_reference.action_facts)
        if diff_available
        else []
    )
    diff = diff_capability_fact_sets(base_contexts, current_contexts)
    added_ids = {ctx.fact.id for ctx in diff.added}
    changed_rows = [*diff.reidentified, *diff.changed]
    changed_ids = {row.after.id for row in changed_rows}
    broadened_rows = [
        row
        for row in changed_rows
        if row.semantic_direction in {"broadened", "mixed", "unknown"}
    ]
    fact_by_tool = _current_fact_by_tool(current_contexts)
    tool_by_key = {_tool_key(tool): tool for tool in mcp_tools}

    findings.extend(_env_secret_findings(context, mcp_tools, fact_by_tool))
    findings.extend(_auto_approve_findings(context, mcp_tools, fact_by_tool))
    findings.extend(
        _unknown_schema_findings(
            context,
            mcp_tools,
            fact_by_tool,
            added_ids=added_ids,
            changed_ids=changed_ids,
            diff_available=diff_available,
        )
    )
    findings.extend(
        _permission_expansion_findings(
            context,
            broadened_rows,
            tool_by_key=tool_by_key,
        )
    )
    if diff_available:
        findings.extend(
            _read_only_added_findings(
                context,
                diff.added,
                tool_by_key=tool_by_key,
            )
        )
    return _dedupe(findings)


def _env_secret_findings(
    context: ScanContext,
    tools: list[Tool],
    fact_by_tool: dict[CapabilityKey, CapabilityFactV1],
) -> list[Finding]:
    findings: list[Finding] = []
    seen_servers: set[tuple[str | None, tuple[str, ...]]] = set()
    for tool in tools:
        secret_names = tuple(str(item) for item in tool.annotations.get("mcp_env_secret_names") or [])
        if not secret_names:
            continue
        key = (tool.source_id, secret_names)
        if key in seen_servers:
            continue
        seen_servers.add(key)
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MCP-ENV-SECRET-PASSTHROUGH",
                title="MCP server passes through secret environment variables",
                severity="high",
                category="mcp_permissions",
                evidence={
                    "source_id": tool.source_id,
                    "env_secret_names": list(secret_names),
                },
                confidence="high",
                recommendation=(
                    "Have a human review the MCP server secret boundary or replace "
                    "secret pass-through with a narrower credential mechanism."
                ),
                context=context,
                provenance_kind="static_declaration",
                capability_refs=_capability_ref(tool, fact_by_tool),
            )
        )
    return findings


def _auto_approve_findings(
    context: ScanContext,
    tools: list[Tool],
    fact_by_tool: dict[CapabilityKey, CapabilityFactV1],
) -> list[Finding]:
    findings: list[Finding] = []
    for tool in tools:
        approval_mode = str(tool.annotations.get("mcp_approval_mode") or "").lower()
        if approval_mode != "approve":
            continue
        profile = classify_tool_permission(tool)
        if not any(
            item in {"write", "destructive", "external", "financial", "production"}
            for item in profile.classes
        ):
            continue
        finding = tool_finding(
            tool=tool,
            check_id="SHIP-MCP-AUTO-APPROVE-SIDE-EFFECT",
            title="MCP side-effecting tool is auto-approved",
            severity="critical",
            category="mcp_permissions",
            evidence={
                "permission_classes": list(profile.classes),
                "risk_score": profile.risk_score,
                "approval_mode": approval_mode,
                "reasons": list(profile.reasons),
            },
            confidence="high",
            recommendation="Do not auto-approve write, destructive, external, financial, or production MCP tools.",
            context=context,
            provenance_kind="static_declaration",
            capability_refs=_capability_ref(tool, fact_by_tool),
        )
        finding.blocks_release = True
        findings.append(finding)
    return findings


def _unknown_schema_findings(
    context: ScanContext,
    tools: list[Tool],
    fact_by_tool: dict[CapabilityKey, CapabilityFactV1],
    *,
    added_ids: set[str],
    changed_ids: set[str],
    diff_available: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    for tool in tools:
        if tool.annotations.get("mcp_unknown_schema") is not True:
            continue
        profile = classify_tool_permission(tool)
        if (
            str(tool.annotations.get("mcp_approval_mode") or "").lower() == "approve"
            and any(
                item in {"write", "destructive", "external", "financial", "production"}
                for item in profile.classes
            )
        ):
            continue
        fact = fact_by_tool.get(_tool_key(tool))
        current = fact.id if fact else None
        if diff_available and current not in added_ids | changed_ids:
            continue
        if not diff_available and tool.source_type != "codex_config_mcp":
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MCP-UNKNOWN-TOOL-SCHEMA",
                title="MCP tool side effect cannot be proven from static schema",
                severity="high",
                category="mcp_permissions",
                evidence={
                    "source_id": tool.source_id,
                    "wildcard": bool(tool.annotations.get("wildcard_tools")),
                    "approval_mode": tool.annotations.get("mcp_approval_mode"),
                },
                confidence="high",
                recommendation=(
                    "Provide an explicit MCP tool inventory/schema or keep the "
                    "server behind human review."
                ),
                context=context,
                provenance_kind="static_declaration",
                capability_refs=_capability_ref(tool, fact_by_tool),
            )
        )
    return findings


def _permission_expansion_findings(
    context: ScanContext,
    rows: list[CapabilityDeltaRow],
    *,
    tool_by_key: dict[CapabilityKey, Tool],
) -> list[Finding]:
    findings: list[Finding] = []
    for row in rows:
        tool = tool_by_key.get(_fact_key(row.after))
        if tool is None:
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MCP-PERMISSION-EXPANDED",
                title="MCP capability permissions expanded",
                severity="high",
                category="mcp_permissions",
                evidence={
                    "capability_id": row.after.id,
                    "semantic_direction": row.semantic_direction,
                    "semantic_changes": [
                        change.model_dump(mode="json") for change in row.semantic_changes
                    ],
                },
                confidence="high",
                recommendation=(
                    "Review the expanded MCP capability; narrow tools/scopes or "
                    "add explicit approval evidence."
                ),
                context=context,
                provenance_kind="static_declaration",
                capability_refs=[row.after.id],
            )
        )
    return findings


def _read_only_added_findings(
    context: ScanContext,
    added: list[CapabilityFactContext],
    *,
    tool_by_key: dict[CapabilityKey, Tool],
) -> list[Finding]:
    findings: list[Finding] = []
    for ctx in added:
        tool = tool_by_key.get(_fact_key(ctx.fact))
        if tool is None or tool.annotations.get("mcp_local_documentation") is not True:
            continue
        profile = classify_tool_permission(tool)
        if not profile.is_read_only:
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MCP-READONLY-SERVER-ADDED",
                title="Read-only local documentation MCP server added",
                severity="low",
                category="mcp_permissions",
                evidence={
                    "capability_id": ctx.fact.id,
                    "permission_classes": list(profile.classes),
                    "risk_score": profile.risk_score,
                },
                confidence="high",
                recommendation=(
                    "Keep the MCP server read-only and local; review if it gains "
                    "write, network, or secret access."
                ),
                context=context,
                provenance_kind="static_declaration",
                capability_refs=[ctx.fact.id],
            )
        )
    return findings


def _mcp_capability_contexts(facts: ActionSurfaceFacts | None) -> list[CapabilityFactContext]:
    if facts is None:
        return []
    contexts: list[CapabilityFactContext] = []
    for action in facts.actions:
        if action.source_type not in MCP_SOURCE_TYPES:
            continue
        contexts.append(_context_from_action(action))
    return contexts


def _context_from_action(action: ActionFact) -> CapabilityFactContext:
    return CapabilityFactContext(
        fact=capability_fact_from_action_fact(action),
        action_id=action.action_id,
        input_fields=tuple(action.input_fields),
        required_input_fields=tuple(action.required_input_fields),
    )


def _current_fact_by_tool(
    contexts: list[CapabilityFactContext],
) -> dict[CapabilityKey, CapabilityFactV1]:
    return {_fact_key(ctx.fact): ctx.fact for ctx in contexts}


def _capability_ref(tool: Tool, fact_by_tool: dict[CapabilityKey, CapabilityFactV1]) -> list[str]:
    fact = fact_by_tool.get(_tool_key(tool))
    return [fact.id] if fact else []


def _tool_key(tool: Tool) -> CapabilityKey:
    return (tool.source_id, tool.name)


def _fact_key(fact: CapabilityFactV1) -> CapabilityKey:
    return (fact.evidence.source_id, fact.identity.tool_name)


def _is_mcp_tool(tool: Tool) -> bool:
    if tool.source_type in MCP_SOURCE_TYPES:
        return True
    return tool.annotations.get("mcp_server") is True


def _dedupe(findings: list[Finding]) -> list[Finding]:
    by_key = {
        (
            finding.check_id,
            finding.tool_name,
            repr(sorted(finding.evidence.items())),
        ): finding
        for finding in findings
    }
    return [by_key[key] for key in sorted(by_key)]
