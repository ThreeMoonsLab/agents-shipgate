from pathlib import Path

import pytest

from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import Agent, GoogleAdkArtifacts, OpenAIApiArtifacts


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


def test_scan_context_artifact_returns_typed_value_and_legacy_property():
    artifact = GoogleAdkArtifacts(agent_config_files=["agent.yaml"])
    bag = ArtifactBag({"google_adk": artifact})
    context = _context(bag)

    assert context.artifact("google_adk", GoogleAdkArtifacts) is artifact
    assert context.adk_artifacts is artifact


def test_scan_context_artifact_raises_on_type_mismatch():
    bag = ArtifactBag({"google_adk": OpenAIApiArtifacts()})
    context = _context(bag)

    with pytest.raises(TypeError, match="expected GoogleAdkArtifacts"):
        context.artifact("google_adk", GoogleAdkArtifacts)
