from __future__ import annotations

import logging

from agents_shipgate.core.risk_hints import enrich_tools_with_risk_hints
from agents_shipgate.core.semantic_assessment import attach_semantic_assessments
from agents_shipgate.core.tool_identity import resolve_selectors_by_tool_id
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

from .agent_builder import _build_agent
from .models import _LoadedInputs, _ToolsAndAgent
from .source_loading import _build_canonical_tools

logger = logging.getLogger(__name__)


def _build_tools_and_agent(
    *,
    manifest: AgentsShipgateManifest,
    inputs: _LoadedInputs,
) -> _ToolsAndAgent:
    """Phase 3: build canonical tools, enrich risk hints, and build the agent.

    Identity warnings are interleaved after source warnings so public warning
    ordering remains deterministic.
    """
    tools, identity_warnings = _build_canonical_tools(
        inputs.loaded_sources,
        manifest.tool_identity,
    )
    # Assemble in pre-decomp order: source → identity → artifact →
    # placeholder → policy_pack. Identity warnings MUST come immediately
    # after per-source warnings (before artifact / placeholder / policy_pack)
    # so ``report.source_warnings`` is byte-identical to pre-v0.19 output.
    # (P3 fix: _LoadedInputs now carries separate buckets instead of a
    # pre-assembled list so this interleaving is possible.)
    warnings: list[str] = list(inputs.source_only_warnings)
    warnings.extend(identity_warnings)
    warnings.extend(inputs.artifact_warnings_list)
    warnings.extend(inputs.placeholder_warnings)
    warnings.extend(inputs.policy_pack_warnings)
    # Some adapters expose the same warnings through both LoadedToolSource
    # and the artifact bag; keep report warning output stable and unique.
    warnings = list(dict.fromkeys(warnings))
    tools = enrich_tools_with_risk_hints(manifest, tools)
    declarations, tools = resolve_selectors_by_tool_id(
        tools,
        manifest.action_surface.actions,
        manifest_path="/action_surface/actions",
        copy_tools=False,
    )
    for control, entries, path in (
        (
            "approval",
            manifest.policies.require_approval_for_tools,
            "/policies/require_approval_for_tools",
        ),
        (
            "confirmation",
            manifest.policies.require_confirmation_for_tools,
            "/policies/require_confirmation_for_tools",
        ),
        (
            "idempotency",
            manifest.policies.require_idempotency_for_tools,
            "/policies/require_idempotency_for_tools",
        ),
    ):
        resolved_controls, tools = resolve_selectors_by_tool_id(
            tools,
            entries,
            manifest_path=path,
            copy_tools=False,
        )
        for tool in tools:
            if tool.id in resolved_controls:
                tool.resolved_controls = sorted(set(tool.resolved_controls) | {control})
    tools = attach_semantic_assessments(
        tools,
        declarations,
        copy_tools=False,
    )
    logger.debug(
        "risk hints generated",
        extra={
            "agents_shipgate_tools": [
                {
                    "name": tool.name,
                    "risk_hints": [
                        {"tag": hint.tag, "confidence": hint.confidence, "source": hint.source}
                        for hint in tool.risk_hints
                    ],
                }
                for tool in tools
            ]
        },
    )
    agent = _build_agent(
        manifest, tools, inputs.api, inputs.anthropic, inputs.adk
    )
    # Aggregate statically-parsed toolkit scope bounds across sources, in
    # loaded order. These ride alongside the (enumerable) tools so the
    # capability-scope verify check can diff them base-vs-head.
    toolkit_bounds = [
        bound for loaded in inputs.loaded_sources for bound in loaded.toolkit_bounds
    ]
    return _ToolsAndAgent(
        tools=tools, agent=agent, warnings=warnings, toolkit_bounds=toolkit_bounds
    )
