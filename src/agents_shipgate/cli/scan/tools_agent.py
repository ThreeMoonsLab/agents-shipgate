from __future__ import annotations

import logging

from agents_shipgate.core.agent_bindings import resolve_agent_binding_graph
from agents_shipgate.core.domain import LoadedToolSource
from agents_shipgate.core.risk_hints import enrich_tools_with_risk_hints
from agents_shipgate.core.semantic_assessment import attach_semantic_assessments
from agents_shipgate.core.source_warnings import withdraw_completed_adk_tool_warnings
from agents_shipgate.core.tool_identity import resolve_selectors_by_tool_id
from agents_shipgate.inputs.google_adk import GoogleAdkArtifacts
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

from .agent_builder import _build_agent
from .models import _LoadedInputs, _ToolsAndAgent
from .source_loading import _build_canonical_tools

logger = logging.getLogger(__name__)


def _completed_source_ids(loaded_sources: list[LoadedToolSource]) -> set[str]:
    """Source ids a reviewed inventory declares it completes.

    ``completes_source_id`` is the desugared form of
    ``<framework>.tool_inventories[].source_id`` — a human declaration in the
    trust root, not an inference. Entries naming a source that is not
    configured are reported separately (``unknown_inventory_source_warning``)
    and cannot appear here, so this cannot be pointed at an arbitrary id.
    """

    return {
        completes.strip()
        for loaded in loaded_sources
        if (completes := (loaded.completes_source_id or "").strip())
    }


def _adk_agent_source_ids(adk: GoogleAdkArtifacts | None) -> dict[str, set[str]]:
    """Agent name -> every configured source that published that name.

    The unresolved-symbol warning names the agent; only the artifact bag knows
    which source that agent was read from. The whole set is kept rather than
    collapsed to a unique one: two sources publishing ``LlmAgent(name="Closer")``
    are ambiguous only while *some* of them is still incomplete. Once every
    candidate has a reviewed inventory, the warning is answered whichever one
    raised it, and dropping the name kept it standing forever (PR #401 review).

    Ids are stripped, because the completion side is: the manifest permits
    surrounding whitespace, so ``' adk '`` on one side and ``'adk'`` on the
    other compared unequal and left an answered warning in place.
    """

    if adk is None:
        return {}
    by_name: dict[str, set[str]] = {}
    for agent in adk.agents:
        name, source_id = agent.get("name"), agent.get("source_id")
        if isinstance(name, str) and isinstance(source_id, str) and source_id.strip():
            by_name.setdefault(name, set()).add(source_id.strip())
    return by_name


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
    # The one place holding both halves of the completion relationship: the
    # loaded sources know which inventory completes which source, and the ADK
    # artifacts know which source each agent came from. A warning asking for
    # evidence the manifest now declares is answered, and must stop gating.
    warnings = withdraw_completed_adk_tool_warnings(
        warnings,
        agent_source_ids=_adk_agent_source_ids(inputs.adk),
        completed_source_ids=_completed_source_ids(inputs.loaded_sources),
    )
    tool_catalog = enrich_tools_with_risk_hints(manifest, tools)
    binding_graph, tool_catalog = resolve_agent_binding_graph(
        manifest,
        tool_catalog,
        inputs.artifact_bag,
        inputs.loaded_sources,
    )
    reachable_ids = set(binding_graph.reachable_tool_ids)
    declarations, tool_catalog = resolve_selectors_by_tool_id(
        tool_catalog,
        manifest.action_surface.actions,
        manifest_path="/action_surface/actions",
        copy_tools=False,
    )
    declarations = {
        tool_id: declaration
        for tool_id, declaration in declarations.items()
        if tool_id in reachable_ids
    }
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
        resolved_controls, tool_catalog = resolve_selectors_by_tool_id(
            tool_catalog,
            entries,
            manifest_path=path,
            copy_tools=False,
        )
        for tool in tool_catalog:
            if tool.id in resolved_controls:
                tool.resolved_controls = sorted(set(tool.resolved_controls) | {control})
    tools = [tool for tool in tool_catalog if tool.id in reachable_ids]
    tools = attach_semantic_assessments(
        tools,
        declarations,
        tool_sources={source.id: source for source in manifest.tool_sources},
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
        tools=tools,
        tool_catalog=tool_catalog,
        agent=agent,
        binding_graph=binding_graph,
        warnings=warnings,
        toolkit_bounds=toolkit_bounds,
    )
