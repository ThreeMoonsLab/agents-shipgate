from __future__ import annotations

from agents_shipgate.checks._framework_common import (
    DynamicSurfaceConfig,
    collect_dynamic_surface_findings,
)
from agents_shipgate.checks.base import agent_finding, tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import Finding, N8nArtifacts, SourceReference

N8N_TOOL_SOURCE_TYPES = {
    "n8n_ai_tool",
    "n8n_workflow_tool",
    "n8n_code_tool",
    "n8n_http_tool",
    "n8n_mcp_client_tool",
}


def run(context: ScanContext) -> list[Finding]:
    artifacts = context.artifact("n8n", N8nArtifacts)
    if not artifacts:
        return []

    findings: list[Finding] = []
    has_inventory = bool(artifacts.tool_inventory_files)

    findings.extend(
        collect_dynamic_surface_findings(
            context,
            surfaces=artifacts.dynamic_tool_surfaces,
            config=DynamicSurfaceConfig(
                check_id="SHIP-N8N-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
                title="n8n tool surface cannot be statically enumerated",
                severity="high",
                category="n8n",
                confidence="medium",
                evidence_key="surface",
                recommendation=(
                    "Provide explicit local n8n/MCP tool inventory metadata or "
                    "replace runtime/wildcard n8n tool exposure before release review."
                ),
                suppress=lambda surface: _inventory_suppresses_dynamic_surface(
                    surface, has_inventory
                ),
                explicit_inventory_value=lambda _surface: has_inventory,
                source_for=_n8n_source,
                provenance_kind="static_declaration",
            ),
        )
    )

    if not has_inventory:
        for toolset in artifacts.mcp_client_tools:
            if toolset.get("selection_mode") not in {"all", "all_except"}:
                continue
            findings.append(
                Finding(
                    check_id="SHIP-N8N-MCP-CLIENT-TOOLSET-UNFILTERED",
                    title="n8n MCP Client Tool exposes an unfiltered toolset",
                    severity="high"
                    if context.manifest.environment.target
                    in {"production_like", "production"}
                    else "medium",
                    category="n8n",
                    agent_id=context.agent.id,
                    evidence={
                        "source_ref": toolset.get("source_ref"),
                        "node_id": toolset.get("node_id"),
                        "selection_mode": toolset.get("selection_mode"),
                        "explicit_inventory": False,
                    },
                    confidence="high",
                    provenance_kind="static_declaration",
                    source=_n8n_source(toolset),
                    recommendation=(
                        "Select an explicit MCP tool allowlist in n8n or provide a "
                        "local MCP inventory for this MCP Client Tool."
                    ),
                )
            )

    for tool in context.tools:
        if tool.source_type not in N8N_TOOL_SOURCE_TYPES and not (
            tool.source_type == "mcp"
            and tool.annotations.get("exposed_by") == "n8n_mcp_server_trigger"
        ):
            continue
        missing = []
        if not (tool.description or "").strip():
            missing.append("description")
        if not tool.parameters and not tool.input_schema.get("properties"):
            missing.append("parameters")
        if not missing:
            continue
        findings.append(
            tool_finding(
                tool=tool,
                check_id="SHIP-N8N-AI-TOOL-METADATA-MISSING",
                title=f"{tool.name} lacks static n8n tool metadata",
                severity="medium",
                category="n8n",
                evidence={"missing": missing, "source_type": tool.source_type},
                confidence="high",
                recommendation=(
                    "Add n8n tool descriptions, $fromAI() parameter metadata, "
                    f"workflow input schemas, or explicit inventory metadata for {tool.name}."
                ),
                context=context,
                provenance_kind="static_declaration",
            )
        )

    if (
        context.manifest.environment.target in {"production_like", "production"}
        and artifacts.credential_refs
        and not artifacts.credential_stub_files
    ):
        findings.append(
            agent_finding(
                check_id="SHIP-N8N-CREDENTIAL-EVIDENCE-MISSING",
                title="n8n credential stubs are not declared",
                severity="high",
                category="n8n",
                evidence={
                    "credential_ref_count": len(artifacts.credential_refs),
                    "credential_stub_file_count": 0,
                },
                confidence="high",
                recommendation=(
                    "Declare local n8n credential stubs so release reviewers can "
                    "verify credential types without exposing secret values."
                ),
                context=context,
                provenance_kind="static_declaration",
            )
        )

    if (
        context.manifest.environment.target in {"production_like", "production"}
        and not artifacts.eval_files
    ):
        findings.append(
            agent_finding(
                check_id="SHIP-N8N-EVAL-COVERAGE-MISSING",
                title="n8n eval coverage is not declared",
                severity="medium",
                category="n8n",
                evidence={
                    "workflow_count": len(artifacts.workflows),
                    "ai_agent_count": len(artifacts.ai_agents),
                    "eval_file_count": 0,
                },
                confidence="high",
                recommendation=(
                    "Declare n8n eval files that cover expected responses and "
                    "tool-use trajectories for this release."
                ),
                context=context,
                provenance_kind="static_declaration",
            )
        )

    for exposure in artifacts.secret_exposures:
        findings.append(
            Finding(
                check_id="SHIP-N8N-SECRET-IN-WORKFLOW-PARAMETER",
                title="n8n workflow JSON contains a secret-like value",
                severity="high",
                category="security",
                agent_id=context.agent.id,
                evidence={
                    "source_ref": exposure.get("source_ref"),
                    "parameter_pointer": exposure.get("parameter_pointer"),
                    "secret_kind": exposure.get("secret_kind"),
                },
                confidence="high",
                provenance_kind="regex_heuristic",
                source=_n8n_source(exposure),
                recommendation=(
                    "Move secret values into n8n credentials or variables and "
                    "rotate the exposed value before release review."
                ),
            )
        )

    return findings


def _inventory_suppresses_dynamic_surface(surface: object, has_inventory: bool) -> bool:
    if not isinstance(surface, dict):
        raise TypeError("n8n dynamic tool surface must be a dictionary")
    if not has_inventory:
        return False
    return surface.get("kind") in {
        "mcp_client_wildcard",
        "mcp_server_wildcard",
        "community_tool",
    }


def _n8n_source(record: dict[str, object]) -> SourceReference:
    source_ref = _as_string(record.get("source_ref"))
    return SourceReference(
        type="n8n_workflow",
        ref=source_ref,
        path=_as_string(record.get("source_path")) or _path_from_ref(source_ref),
        pointer=_as_string(record.get("source_pointer"))
        or _as_string(record.get("parameter_pointer")),
    )


def _path_from_ref(value: str | None) -> str | None:
    if not value:
        return None
    return value.split("#", 1)[0]


def _as_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
