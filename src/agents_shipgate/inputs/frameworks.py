"""Framework-artifact aggregation shim for v0.11.

v0.12 TODO: collapse this into the registry result directly. For now,
this module exists to keep ``scan.py``'s variable names
(``adk_artifacts``, ``langchain_artifacts``, etc.) stable across the
adapter-registry refactor — it reshapes the registry's typed
``ArtifactBag`` into the existing ``FrameworkLoadResult`` dataclass
that the rest of ``scan.py`` and ``inspect_sources`` consumes.

When called without an ``artifact_bag`` (the legacy code path for
tests that bypass the registry), it falls back to direct loader calls.
That fallback is removed in v0.12.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import (
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    LoadedToolSource,
    N8nArtifacts,
)
from agents_shipgate.inputs.crewai import load_crewai_artifacts
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts
from agents_shipgate.inputs.langchain import load_langchain_artifacts
from agents_shipgate.inputs.n8n import load_n8n_artifacts
from agents_shipgate.inputs.protocol import ArtifactBag


@dataclass(frozen=True)
class FrameworkLoadResult:
    loaded_sources: list[LoadedToolSource]
    adk_artifacts: GoogleAdkArtifacts | None = None
    langchain_artifacts: LangChainArtifacts | None = None
    crewai_artifacts: CrewAiArtifacts | None = None
    n8n_artifacts: N8nArtifacts | None = None


def load_framework_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
    artifact_bag: ArtifactBag | None = None,
) -> FrameworkLoadResult:
    """Reshape registry output into the typed ``FrameworkLoadResult``.

    When ``artifact_bag`` is provided, framework adapters have already
    run via the registry; this function just unpacks artifacts by key.
    ``loaded_sources`` is empty in this path because the registry
    already collected every framework's ``LoadedToolSource``.

    When ``artifact_bag`` is ``None``, falls back to direct loader
    calls — kept for unit tests that bypass the registry. v0.12 will
    remove this fallback.
    """
    if artifact_bag is None:
        return _legacy_load_framework_artifacts(manifest, base_dir)
    return FrameworkLoadResult(
        loaded_sources=[],
        adk_artifacts=artifact_bag.get("google_adk", GoogleAdkArtifacts),
        langchain_artifacts=artifact_bag.get("langchain", LangChainArtifacts),
        crewai_artifacts=artifact_bag.get("crewai", CrewAiArtifacts),
        n8n_artifacts=artifact_bag.get("n8n", N8nArtifacts),
    )


def _legacy_load_framework_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> FrameworkLoadResult:
    adk_sources, adk_artifacts = load_google_adk_artifacts(manifest, base_dir)
    langchain_sources, langchain_artifacts = load_langchain_artifacts(manifest, base_dir)
    crewai_sources, crewai_artifacts = load_crewai_artifacts(manifest, base_dir)
    n8n_sources, n8n_artifacts = load_n8n_artifacts(manifest, base_dir)
    return FrameworkLoadResult(
        loaded_sources=[
            *adk_sources,
            *langchain_sources,
            *crewai_sources,
            *n8n_sources,
        ],
        adk_artifacts=adk_artifacts,
        langchain_artifacts=langchain_artifacts,
        crewai_artifacts=crewai_artifacts,
        n8n_artifacts=n8n_artifacts,
    )
