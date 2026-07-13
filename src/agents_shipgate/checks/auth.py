from __future__ import annotations

from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.heuristics import is_broad_scope
from agents_shipgate.core.risk_hints import is_policy_eligible_write_tool, risk_tags


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
                provenance_kind="static_declaration",
                policy_evidence_pointer="/permissions/scopes",
            )
        )
    for tool in context.tools:
        authority = getattr(getattr(tool, "semantic_assessment", None), "authority", None)
        effective_scopes = (
            list(authority.scopes) if authority is not None else list(tool.auth.scopes)
        )
        missing_scope_mode = (
            authority is not None and authority.mode in {"unscoped", "ambient"}
        ) or (
            authority is None and _tool_requires_scope(tool) and not effective_scopes
        )
        if missing_scope_mode:
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-AUTH-MISSING-SCOPE",
                    title=f"{tool.name} lacks declared auth scopes",
                    severity="high",
                    category="auth",
                    # Keep the legacy fingerprint evidence stable. Typed
                    # authority details live on action/capability semantic
                    # evidence and release_decision.evidence_coverage.
                    evidence={"risk_tags": risk_tags(tool, min_confidence="medium")},
                    confidence="high" if authority is not None else "medium",
                    recommendation=(
                        f"Declare operation-specific auth scopes for {tool.name}, "
                        "or explicitly declare anonymous authority when the operation "
                        "requires no credentials."
                    ),
                    context=context,
                    provenance_kind="static_declaration",
                )
            )
        missing_scopes = [
            scope
            for scope in effective_scopes
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
                        "tool_scopes": effective_scopes,
                        "manifest_scopes": context.manifest.permissions.scopes,
                        "missing_scopes": missing_scopes,
                    },
                    confidence="high",
                    recommendation=(
                        f"Add the required scopes for {tool.name} to permissions.scopes "
                        "or narrow the tool's declared auth requirements."
                    ),
                    context=context,
                    provenance_kind="static_declaration",
                )
            )
        broad_scopes = [scope for scope in effective_scopes if is_broad_scope(scope)]
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
                    provenance_kind="static_declaration",
                    # Tool source already points at the OpenAPI/MCP/etc.
                    # location where the broad scope is declared; the
                    # manifest pointer below is intentionally
                    # /permissions/scopes (where reviewers can
                    # cross-check the manifest-level scope grant).
                    policy_evidence_pointer="/permissions/scopes",
                )
            )
    return findings

def _tool_requires_scope(tool) -> bool:
    return is_policy_eligible_write_tool(tool)


def _scope_covered(required_scope: str, manifest_scopes: list[str]) -> bool:
    required = required_scope.lower()
    for declared_scope in manifest_scopes:
        declared = declared_scope.lower()
        if declared in {"*", required}:
            return True
        if declared.endswith(":*") and required.startswith(declared[:-1]):
            return True
    return False
