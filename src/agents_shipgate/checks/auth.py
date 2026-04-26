from __future__ import annotations

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.heuristics import is_broad_scope
from agents_shipgate.core.risk_hints import has_risk_tag, is_write_tool, risk_tags


def run(context: ScanContext):
    findings = []
    broad_global_scopes = [
        scope for scope in context.manifest.permissions.scopes if is_broad_scope(scope)
    ]
    if broad_global_scopes:
        findings.append(
            agent_finding(
                check_id="SHIP-AUTH-MANIFEST-BROAD-SCOPE",
                title="Manifest declares broad permission scopes",
                severity="high",
                category="auth",
                evidence={"scopes": broad_global_scopes},
                confidence="high",
                recommendation="Replace broad manifest permission scopes with the narrowest scopes needed for this release.",
                context=context,
            )
        )
    for tool in context.tools:
        if _tool_requires_scope(tool) and not tool.auth.scopes:
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-AUTH-MISSING-SCOPE",
                    title=f"{tool.name} lacks declared auth scopes",
                    severity="high",
                    category="auth",
                    evidence={"risk_tags": risk_tags(tool, min_confidence="medium")},
                    confidence="medium",
                    recommendation=f"Declare auth scopes for {tool.name} in OpenAPI, MCP metadata, or the manifest before release review.",
                    context=context,
                )
            )
        missing_scopes = [
            scope
            for scope in tool.auth.scopes
            if not _scope_covered(scope, context.manifest.permissions.scopes)
        ]
        if missing_scopes:
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-AUTH-SCOPE-COVERAGE-MISSING",
                    title=f"{tool.name} requires scopes not declared in the manifest",
                    severity="high",
                    category="auth",
                    evidence={
                        "tool_scopes": tool.auth.scopes,
                        "manifest_scopes": context.manifest.permissions.scopes,
                        "missing_scopes": missing_scopes,
                    },
                    confidence="high",
                    recommendation=(
                        f"Add the required scopes for {tool.name} to permissions.scopes "
                        "or narrow the tool's declared auth requirements."
                    ),
                    context=context,
                )
            )
        broad_scopes = [scope for scope in tool.auth.scopes if is_broad_scope(scope)]
        if broad_scopes:
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-AUTH-TOOL-BROAD-SCOPE",
                    title=f"{tool.name} uses broad auth scopes",
                    severity="high",
                    category="auth",
                    evidence={"scopes": broad_scopes},
                    confidence="high",
                    recommendation=f"Replace broad scopes for {tool.name} with narrower operation-specific scopes.",
                    context=context,
                )
            )
    return findings

def _tool_requires_scope(tool) -> bool:
    return is_write_tool(tool) or has_risk_tag(
        tool,
        {"sensitive_data_access"},
        min_confidence="medium",
    )


def _scope_covered(required_scope: str, manifest_scopes: list[str]) -> bool:
    required = required_scope.lower()
    for declared_scope in manifest_scopes:
        declared = declared_scope.lower()
        if declared in {"*", required}:
            return True
        if declared.endswith(":*") and required.startswith(declared[:-1]):
            return True
    return False
