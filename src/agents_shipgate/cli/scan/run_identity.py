from __future__ import annotations

import hashlib
import json

from agents_shipgate.core.domain import Tool
from agents_shipgate.core.findings.summaries import tool_inventory
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment
from agents_shipgate.schemas.codex_plugin import CodexPluginSurface
from agents_shipgate.schemas.surfaces import ActionSurfaceFacts

_ACTION_FACT_PROVENANCE_EXCLUDE = {
    "source_ref": True,
    "source_location": True,
    "source_path": True,
    "source_start_line": True,
    "source_end_line": True,
    "source_start_column": True,
    "source_pointer": True,
}


def _run_id(
    manifest,
    tools: list[Tool],
    findings,
    project: dict[str, object] | None = None,
    agent_name: str | None = None,
    environment: dict[str, object] | None = None,
    api_surface: dict[str, object] | None = None,
    anthropic_surface: dict[str, object] | None = None,
    frameworks: dict[str, object] | None = None,
    codex_plugin_surface: CodexPluginSurface | None = None,
    action_surface_facts: ActionSurfaceFacts | None = None,
    tool_catalog: list[Tool] | None = None,
    binding_graph: AgentBindingGraphAssessment | None = None,
) -> str:
    payload = {
        "project": project
        if project is not None
        else manifest.project.model_dump(mode="json", exclude_none=False),
        "agent_name": agent_name if agent_name is not None else manifest.agent.name,
        "environment": environment
        if environment is not None
        else manifest.environment.model_dump(mode="json", exclude_none=False),
        "tool_inventory": tool_inventory(tools),
        "tool_catalog": tool_inventory(tool_catalog if tool_catalog is not None else tools),
        "binding_graph": (
            binding_graph.model_dump(mode="json") if binding_graph is not None else None
        ),
        "findings": [
            finding.model_dump(
                mode="json",
                # Exclude derived-enrichment fields (per C11 + v0.7
                # review finding 2): patches and the four remediation
                # fields are computed AFTER the input surface is
                # known, so they MUST NOT enter the run_id hash. Two
                # scans of the same workspace must produce the same
                # run_id whether `--suggest-patches` is set or not, and
                # whether v0.7 metadata is present or not.
                exclude={
                    "id": True,
                    "baseline_status": True,
                    "patches": True,
                    "autofix_safe": True,
                    "requires_human_review": True,
                    "suggested_patch_kind": True,
                    "docs_url": True,
                    "blocks_release": True,
                    # v0.12 derived enrichment: same exclusion rule as
                    # the v0.7 remediation fields above. agent_action is
                    # a deterministic projection of those fields, so
                    # excluding them already implies it should be
                    # excluded — but make it explicit so a future
                    # contributor doesn't have to trace the projection.
                    "agent_action": True,
                    # v0.11 provenance fields are excluded so YAML line
                    # drift cannot churn run_id; the legacy
                    # type/ref/location strings stay in the hash so
                    # existing run_ids remain stable.
                    "source": {
                        "path": True,
                        "start_line": True,
                        "end_line": True,
                        "start_column": True,
                        "pointer": True,
                    },
                    # v0.19 reviewer-grade provenance: the secondary
                    # manifest pointer ``policy_evidence_source`` is
                    # excluded in its entirety. The whole field is
                    # additive (older scans never emitted it) and
                    # YAML line drift on the manifest must not churn
                    # run_id — same rationale as the v0.11 exclusion
                    # above.
                    "policy_evidence_source": True,
                    # v0.24 capability-policy evidence is explanatory
                    # reviewer metadata. It must not churn run_id for
                    # otherwise-identical policy findings.
                    "capability_refs": True,
                    "capability_policy_evidence": True,
                    # v0.28 policy-pack routing is non-enforcing
                    # reviewer/audit metadata. Owner/reviewer/approval
                    # routing changes must not churn run_id.
                    "policy_routing": True,
                    # v0.25 trace refs are explanatory runtime-audit
                    # metadata and must not churn run_id.
                    "capability_trace_refs": True,
                },
                exclude_none=False,
            )
            for finding in findings
        ],
        "api_surface": api_surface,
        "anthropic_surface": anthropic_surface,
        "frameworks": frameworks or {},
        "codex_plugin_surface": (
            codex_plugin_surface.model_dump(mode="json") if codex_plugin_surface else None
        ),
        "action_surface_facts": _action_surface_facts_for_run_id(action_surface_facts),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"agents_shipgate_{digest}"


def _action_surface_facts_for_run_id(
    action_surface_facts: ActionSurfaceFacts | None,
) -> dict[str, object] | None:
    if action_surface_facts is None:
        return None
    return action_surface_facts.model_dump(
        mode="json",
        exclude={
            "actions": {
                "__all__": _ACTION_FACT_PROVENANCE_EXCLUDE,
            }
        },
    )
