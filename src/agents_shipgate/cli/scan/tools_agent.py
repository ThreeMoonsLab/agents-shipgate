from __future__ import annotations

import logging

from agents_shipgate.core.risk_hints import enrich_tools_with_risk_hints
from agents_shipgate.core.semantic_assessment import attach_semantic_assessments
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

from .agent_builder import _build_agent
from .models import _LoadedInputs, _ToolsAndAgent
from .source_loading import _flatten_and_deduplicate_tools

logger = logging.getLogger(__name__)

def _build_tools_and_agent(
    *,
    manifest: AgentsShipgateManifest,
    inputs: _LoadedInputs,
) -> _ToolsAndAgent:
    """Phase 3: flatten/dedup tools with source priority, enrich with
    manifest-derived risk hints, build the ``Agent`` object, finalize
    the source-warnings list (dedup after appending the duplicate-tool
    warnings from ``_flatten_and_deduplicate_tools``).
    """
    tools, duplicate_warnings = _flatten_and_deduplicate_tools(inputs.loaded_sources)
    # Assemble in pre-decomp order: source → duplicate → artifact →
    # placeholder → policy_pack. Duplicate warnings MUST come immediately
    # after per-source warnings (before artifact / placeholder / policy_pack)
    # so ``report.source_warnings`` is byte-identical to pre-v0.19 output.
    # (P3 fix: _LoadedInputs now carries separate buckets instead of a
    # pre-assembled list so this interleaving is possible.)
    warnings: list[str] = list(inputs.source_only_warnings)
    warnings.extend(duplicate_warnings)
    warnings.extend(inputs.artifact_warnings_list)
    warnings.extend(inputs.placeholder_warnings)
    warnings.extend(inputs.policy_pack_warnings)
    # Some adapters expose the same warnings through both LoadedToolSource
    # and the artifact bag; keep report warning output stable and unique.
    warnings = list(dict.fromkeys(warnings))
    tools = enrich_tools_with_risk_hints(manifest, tools)
    tools = attach_semantic_assessments(
        tools,
        {entry.tool: entry for entry in manifest.action_surface.actions},
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
