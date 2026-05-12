from __future__ import annotations

from agents_shipgate.checks.base import agent_finding
from agents_shipgate.core.context import ScanContext


def run(context: ScanContext):
    artifacts = context.codex_plugin_artifacts
    if artifacts is None:
        return []

    findings = []
    for plugin in artifacts.plugins:
        issues: list[str] = []
        if plugin.missing_fields:
            issues.extend(plugin.missing_fields)
        if plugin.name_mismatch:
            issues.append("name does not match plugin root directory")
        if plugin.duplicate_name:
            issues.append("duplicate plugin name")
        if issues:
            findings.append(
                agent_finding(
                    check_id="SHIP-CODEX-PLUGIN-METADATA-MISSING",
                    title=f"Codex plugin {plugin.name} metadata needs review",
                    severity="medium",
                    category="codex_plugin",
                    evidence={
                        "plugin": plugin.name,
                        "source_id": plugin.source_id,
                        "manifest_path": plugin.manifest_path,
                        "issues": issues,
                    },
                    confidence="high",
                    recommendation=(
                        "Fill required Codex plugin metadata and make plugin "
                        "identity unambiguous before release review."
                    ),
                    context=context,
                )
            )

    if artifacts.component_path_issues:
        for issue in artifacts.component_path_issues:
            findings.append(
                agent_finding(
                    check_id="SHIP-CODEX-PLUGIN-COMPONENT-PATH-MISSING",
                    title="Codex plugin component path cannot be loaded",
                    severity="high",
                    category="codex_plugin",
                    evidence=issue.model_dump(mode="json"),
                    confidence="high",
                    recommendation=(
                        "Fix the plugin component path so every declared skill, "
                        "MCP, app, or hook artifact resolves inside the plugin package."
                    ),
                    context=context,
                )
            )

    for marketplace in artifacts.marketplaces:
        for entry in marketplace.missing_policy_entries:
            findings.append(
                agent_finding(
                    check_id="SHIP-CODEX-PLUGIN-MARKETPLACE-POLICY-MISSING",
                    title="Codex plugin marketplace entry is missing policy metadata",
                    severity="medium",
                    category="codex_plugin",
                    evidence={
                        "marketplace": marketplace.path,
                        **entry,
                    },
                    confidence="high",
                    recommendation=(
                        "Add policy.installation, policy.authentication, and category "
                        "to every Codex marketplace plugin entry."
                    ),
                    context=context,
                )
            )

    for stub in artifacts.mcp_server_stubs:
        if stub.inventory_loaded:
            continue
        findings.append(
            agent_finding(
                check_id="SHIP-CODEX-PLUGIN-MCP-SERVER-NOT-ENUMERABLE",
                title=f"Codex plugin MCP server {stub.server} is not enumerable",
                severity="high",
                category="codex_plugin",
                evidence={
                    "plugin": stub.plugin,
                    "server": stub.server,
                    "path": stub.path,
                    "command_present": bool(stub.command),
                    "inventory_path": stub.inventory_path,
                },
                confidence="high",
                recommendation=(
                    "Provide a local MCP tools inventory for this Codex plugin "
                    "server through codex_plugins.mcp_tool_inventories."
                ),
                context=context,
            )
        )

    for app in artifacts.apps:
        findings.append(
            agent_finding(
                check_id="SHIP-CODEX-PLUGIN-APP-SURFACE-NOT-ENUMERABLE",
                title=f"Codex plugin app {app.name} is not statically enumerable",
                severity="medium",
                category="codex_plugin",
                evidence={
                    "plugin": app.plugin,
                    "app": app.name,
                    "connector_id": app.connector_id,
                    "path": app.path,
                },
                confidence="high",
                recommendation=(
                    "Treat connector-backed Codex app capabilities as a review item "
                    "unless an explicit local tool inventory or policy evidence is provided."
                ),
                context=context,
            )
        )

    for skill in artifacts.skills:
        if not skill.missing_fields and not skill.duplicate:
            continue
        findings.append(
            agent_finding(
                check_id="SHIP-CODEX-PLUGIN-SKILL-METADATA-MISSING",
                title="Codex plugin skill metadata needs review",
                severity="medium",
                category="codex_plugin",
                evidence={
                    "plugin": skill.plugin,
                    "skill": skill.name,
                    "path": skill.path,
                    "missing_fields": skill.missing_fields,
                    "duplicate": skill.duplicate,
                },
                confidence="high",
                recommendation=(
                    "Give each Codex skill a unique frontmatter name and a clear "
                    "description so agents can route it deterministically."
                ),
                context=context,
            )
        )

    return findings
