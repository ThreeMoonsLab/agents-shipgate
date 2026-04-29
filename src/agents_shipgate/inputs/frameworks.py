from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import (
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    LoadedToolSource,
)
from agents_shipgate.inputs.crewai import load_crewai_artifacts
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts
from agents_shipgate.inputs.langchain import load_langchain_artifacts


@dataclass(frozen=True)
class FrameworkLoadResult:
    loaded_sources: list[LoadedToolSource]
    adk_artifacts: GoogleAdkArtifacts | None = None
    langchain_artifacts: LangChainArtifacts | None = None
    crewai_artifacts: CrewAiArtifacts | None = None


def load_framework_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> FrameworkLoadResult:
    adk_sources, adk_artifacts = load_google_adk_artifacts(manifest, base_dir)
    langchain_sources, langchain_artifacts = load_langchain_artifacts(manifest, base_dir)
    crewai_sources, crewai_artifacts = load_crewai_artifacts(manifest, base_dir)
    return FrameworkLoadResult(
        loaded_sources=[*adk_sources, *langchain_sources, *crewai_sources],
        adk_artifacts=adk_artifacts,
        langchain_artifacts=langchain_artifacts,
        crewai_artifacts=crewai_artifacts,
    )
