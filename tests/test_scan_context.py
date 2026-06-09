from pathlib import Path

import pytest

from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    CodexBoundaryArtifacts,
    CodexPluginArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
    OpenAIApiArtifacts,
    ValidationArtifacts,
)
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.schemas.surfaces import ActionSurfaceFacts

LEGACY_ARTIFACT_PROPERTIES = (
    ("api_artifacts", "openai_api", OpenAIApiArtifacts),
    ("anthropic_artifacts", "anthropic_api", AnthropicArtifacts),
    ("adk_artifacts", "google_adk", GoogleAdkArtifacts),
    ("langchain_artifacts", "langchain", LangChainArtifacts),
    ("crewai_artifacts", "crewai", CrewAiArtifacts),
    ("codex_boundary_artifacts", "codex_config", CodexBoundaryArtifacts),
    ("codex_plugin_artifacts", "codex_plugin", CodexPluginArtifacts),
    ("n8n_artifacts", "n8n", N8nArtifacts),
    ("validation_artifacts", "validation", ValidationArtifacts),
)


def _context(artifact_bag: ArtifactBag | None = None) -> ScanContext:
    return ScanContext(
        manifest=load_manifest(Path("samples/support_refund_agent/shipgate.yaml")),
        agent=Agent(id="agent:test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        framework_artifacts=artifact_bag or ArtifactBag(),
    )


def test_scan_context_artifact_returns_none_when_missing():
    context = _context()

    assert context.artifact("google_adk", GoogleAdkArtifacts) is None
    assert isinstance(context.action_surface_facts, ActionSurfaceFacts)
    assert context.action_surface_facts.actions == []


@pytest.mark.parametrize(
    ("property_name", "source_type", "artifact_type"),
    LEGACY_ARTIFACT_PROPERTIES,
)
def test_scan_context_artifact_returns_typed_value_and_legacy_property(
    property_name,
    source_type,
    artifact_type,
):
    artifact = artifact_type()
    bag = ArtifactBag({source_type: artifact})
    context = _context(bag)

    assert context.artifact(source_type, artifact_type) is artifact
    assert getattr(context, property_name) is artifact


def test_scan_context_artifact_raises_on_type_mismatch():
    bag = ArtifactBag({"google_adk": OpenAIApiArtifacts()})
    context = _context(bag)

    with pytest.raises(TypeError, match="expected GoogleAdkArtifacts"):
        context.artifact("google_adk", GoogleAdkArtifacts)
