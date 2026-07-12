from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    CodexPluginArtifacts,
    ConductorArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
    OpenAIApiArtifacts,
    ValidationArtifacts,
)
from agents_shipgate.inputs.policy_packs import load_policy_packs
from agents_shipgate.inputs.protocol import REGISTRY
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

from .models import _LoadedInputs
from .source_loading import _artifact_warnings, _load_sources
from .validation import _manifest_placeholder_warnings

logger = logging.getLogger(__name__)

def _load_inputs(
    *,
    manifest: AgentsShipgateManifest,
    base_dir: Path,
    config_path: Path,
    policy_pack_paths: list[Path] | None,
    verbose: bool,
    plugins_enabled: bool | None = None,
) -> _LoadedInputs:
    """Phase 2: dispatch every adapter through ``REGISTRY``, extract
    typed artifacts from the ``ArtifactBag``, aggregate source warnings
    (including CHANGE_ME placeholder warnings from the manifest text),
    load policy packs.

    v0.20: also discovers third-party adapters from the
    ``agents_shipgate.adapters`` entry-point group BEFORE
    ``_load_sources`` runs, so the dispatcher resolves any
    user-installed plugin source_types alongside built-ins. Discovery
    is gated by ``plugins_enabled`` (mirroring the plugin-check flow
    in ``checks/registry.py``).
    """
    from agents_shipgate.inputs.protocol import discover_third_party_adapters

    # v0.20 (PR #111 review fix P1 #1+#2): build a per-scan registry
    # clone so third-party discovery NEVER mutates the global
    # ``REGISTRY``. Without this, a later ``--no-plugins`` scan would
    # still see adapters registered by an earlier scan, and the
    # collision check on scan-two would misclassify stable third-
    # party adapters as ``source_type_collision`` (the global already
    # has them from scan-one). The clone captures any monkeypatch
    # state at this exact moment, so existing tests that
    # ``monkeypatch.setitem(REGISTRY._adapters, …)`` still work.
    scan_registry = REGISTRY.clone()
    loaded_adapters: list[dict[str, Any]] = []
    discovery_records = discover_third_party_adapters(
        scan_registry,
        plugins_enabled=plugins_enabled,
        loaded_adapters=loaded_adapters,
    )
    # v0.20 (PR #111 review follow-up #2): map of source_type → valid
    # LoadedAdapter record. Used by ``_load_sources`` to route
    # third-party adapter ``load()`` calls through
    # ``run_validated_adapter`` so runtime exceptions land in
    # ``loaded_adapters[].runtime_errors`` instead of crashing the
    # scan. Invalid records (validation_status != "valid") are
    # excluded: they never registered on ``scan_registry`` and so the
    # dispatcher will never reach them.
    third_party_records: dict[str, Any] = {
        record.adapter.source_type: record
        for record in discovery_records
        if record.adapter is not None
    }
    loaded_sources, artifact_bag = _load_sources(
        manifest,
        base_dir,
        verbose=verbose,
        registry=scan_registry,
        third_party_records=third_party_records,
        plugins_enabled=plugins_enabled,
    )
    logger.debug(
        "loaded sources",
        extra={
            "agents_shipgate_source_count": len(loaded_sources),
            "agents_shipgate_sources": [
                {"id": source.source_id, "type": source.source_type, "tools": len(source.tools)}
                for source in loaded_sources
            ],
        },
    )
    # Keep warning buckets separate so Phase 3 can re-assemble them in the
    # pre-decomp order: source → duplicate → artifact → placeholder →
    # policy_pack → dedup. See _LoadedInputs docstring for the P3 rationale.
    source_only_warnings: list[str] = [
        warning for loaded in loaded_sources for warning in loaded.warnings
    ]
    artifact_warnings_list: list[str] = _artifact_warnings(artifact_bag)
    # Unresolved CHANGE_ME placeholders in the manifest mean the run is
    # operating on stub data. Surface them as source warnings so the
    # existing ``source_warning_count > 0`` branch in
    # release_decision.evidence_coverage routes the gate to
    # ``review_required`` and the packet §10 "Not proven" section
    # mentions the placeholder verbatim.
    placeholder_warnings: list[str] = _manifest_placeholder_warnings(config_path)
    policy_packs = load_policy_packs(
        manifest=manifest,
        base_dir=base_dir,
        cli_policy_packs=policy_pack_paths,
    )
    policy_pack_warnings: list[str] = list(policy_packs.warnings)
    return _LoadedInputs(
        loaded_sources=loaded_sources,
        artifact_bag=artifact_bag,
        policy_packs=policy_packs,
        source_only_warnings=source_only_warnings,
        artifact_warnings_list=artifact_warnings_list,
        placeholder_warnings=placeholder_warnings,
        policy_pack_warnings=policy_pack_warnings,
        loaded_adapters=loaded_adapters,
        adk=artifact_bag.get("google_adk", GoogleAdkArtifacts),
        langchain=artifact_bag.get("langchain", LangChainArtifacts),
        crewai=artifact_bag.get("crewai", CrewAiArtifacts),
        n8n=artifact_bag.get("n8n", N8nArtifacts),
        conductor=artifact_bag.get("conductor", ConductorArtifacts),
        api=artifact_bag.get("openai_api", OpenAIApiArtifacts),
        anthropic=artifact_bag.get("anthropic_api", AnthropicArtifacts),
        codex_plugin=artifact_bag.get("codex_plugin", CodexPluginArtifacts),
        validation=artifact_bag.get("validation", ValidationArtifacts),
    )
