from __future__ import annotations

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.config.schema import PolicyToolEntry
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.heuristics import is_broad_scope
from agents_shipgate.core.risk_hints import is_high_risk_tool, risk_tags


def run(context: ScanContext, *, known_check_ids: set[str]) -> list:
    findings = []
    tool_names = {tool.name for tool in context.tools}
    findings.extend(_stale_suppressions(context, tool_names, known_check_ids))
    findings.extend(_stale_policies(context, tool_names))
    findings.extend(_stale_overrides(context, tool_names))
    findings.extend(_missing_high_risk_owners(context))
    findings.extend(_unused_manifest_scopes(context))
    return findings


def _stale_suppressions(
    context: ScanContext, tool_names: set[str], known_check_ids: set[str]
) -> list:
    findings = []
    for suppression in context.manifest.checks.ignore:
        issues = []
        if suppression.check_id not in known_check_ids:
            issues.append("unknown_check_id")
        if suppression.tool and suppression.tool not in tool_names:
            issues.append("missing_tool")
        if not issues:
            continue
        findings.append(
            agent_finding(
                check_id="SHIP-MANIFEST-STALE-SUPPRESSION",
                title=f"Suppression for {suppression.check_id} no longer matches the manifest",
                severity="medium",
                category="manifest",
                evidence={
                    "check_id": suppression.check_id,
                    "tool": suppression.tool,
                    "issues": issues,
                },
                confidence="high",
                recommendation="Remove stale suppressions or update them to match current check IDs and tool names.",
                context=context,
            )
        )
    return findings


def _stale_policies(context: ScanContext, tool_names: set[str]) -> list:
    findings = []
    policy_sets: list[tuple[str, list[PolicyToolEntry]]] = [
        ("approval", context.manifest.policies.require_approval_for_tools),
        ("confirmation", context.manifest.policies.require_confirmation_for_tools),
        ("idempotency", context.manifest.policies.require_idempotency_for_tools),
    ]
    for policy_name, entries in policy_sets:
        for entry in entries:
            if entry.tool in tool_names:
                continue
            findings.append(
                agent_finding(
                    check_id="SHIP-MANIFEST-STALE-POLICY",
                    title=f"{policy_name} policy references missing tool {entry.tool}",
                    severity="medium",
                    category="manifest",
                    evidence={"policy": policy_name, "tool": entry.tool},
                    confidence="high",
                    recommendation="Remove stale policy entries or update them to current tool names.",
                    context=context,
                )
            )
    return findings


def _stale_overrides(context: ScanContext, tool_names: set[str]) -> list:
    findings = []
    for tool_name in context.manifest.risk_overrides.tools:
        if tool_name in tool_names:
            continue
        findings.append(
            agent_finding(
                check_id="SHIP-MANIFEST-STALE-RISK-OVERRIDE",
                title=f"Risk override references missing tool {tool_name}",
                severity="medium",
                category="manifest",
                evidence={"tool": tool_name},
                confidence="high",
                recommendation="Remove stale risk overrides or update them to current tool names.",
                context=context,
            )
        )
    return findings


def _missing_high_risk_owners(context: ScanContext) -> list:
    if context.manifest.environment.target not in {"production_like", "production"}:
        return []
    findings = []
    for tool in context.tools:
        if not is_high_risk_tool(tool) or tool.owner:
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-MANIFEST-HIGH-RISK-OWNER-MISSING",
                title=f"{tool.name} is high-risk but has no owner",
                severity="high",
                category="manifest",
                evidence={
                    "environment": context.manifest.environment.target,
                    "risk_tags": risk_tags(tool, min_confidence="medium"),
                },
                confidence="high",
                recommendation="Declare an owner for each high-risk production tool in risk_overrides.tools.",
                context=context,
            )
        )
    return findings


def _unused_manifest_scopes(context: ScanContext) -> list:
    manifest_scopes = context.manifest.permissions.scopes
    if not manifest_scopes:
        return []
    tool_scopes = [scope for tool in context.tools for scope in tool.auth.scopes]
    findings = []
    for manifest_scope in manifest_scopes:
        if any(_scope_covers_tool_scope(manifest_scope, tool_scope) for tool_scope in tool_scopes):
            continue
        severity = "high" if is_broad_scope(manifest_scope) else "medium"
        findings.append(
            agent_finding(
                check_id="SHIP-MANIFEST-UNUSED-SCOPE",
                title=f"Manifest declares unused permission scope {manifest_scope}",
                severity=severity,
                category="manifest",
                evidence={
                    "scope": manifest_scope,
                    "tool_scopes": sorted(tool_scopes),
                },
                confidence="medium",
                recommendation="Remove unused manifest scopes or add tool metadata showing why they are required.",
                context=context,
            )
        )
    return findings


def _scope_covers_tool_scope(manifest_scope: str, tool_scope: str) -> bool:
    declared = manifest_scope.lower()
    required = tool_scope.lower()
    if declared in {"*", required}:
        return True
    return declared.endswith(":*") and required.startswith(declared[:-1])
