from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import GoogleAdkArtifacts, LoadedToolSource
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts


@dataclass(frozen=True)
class FrameworkLoadResult:
    loaded_sources: list[LoadedToolSource]
    adk_artifacts: GoogleAdkArtifacts | None = None


def load_framework_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> FrameworkLoadResult:
    adk_sources, adk_artifacts = load_google_adk_artifacts(manifest, base_dir)
    return FrameworkLoadResult(loaded_sources=adk_sources, adk_artifacts=adk_artifacts)
