from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    CodexPluginArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
    OpenAIApiArtifacts,
    ValidationArtifacts,
)
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.domain import (
    Agent,
    Tool,
)
from agents_shipgate.inputs.common import PositionIndex
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.surfaces import ActionSurfaceFacts

T = TypeVar("T")


def _empty_position_index() -> PositionIndex:
    """Default factory for ``ScanContext.manifest_positions``.

    The position index is best-effort: tests that construct a
    ``ScanContext`` directly (without going through ``run_scan``) and
    plugin contexts that don't have a manifest text on disk both want
    the unsupported sentinel so ``positions.lookup(...)`` returns
    ``None`` and ``agent_finding`` / ``tool_finding`` fall back to the
    legacy filename-only manifest provenance.
    """
    return PositionIndex(supported=False)


@dataclass
class ScanContext:
    manifest: AgentsShipgateManifest
    agent: Agent
    tools: list[Tool]
    config_path: Path
    framework_artifacts: ArtifactBag = field(default_factory=ArtifactBag)
    action_surface_facts: ActionSurfaceFacts = field(default_factory=ActionSurfaceFacts)
    # v0.19 reviewer-grade provenance: JSON-pointer → ``(line, col)``
    # index built from the manifest YAML so agent-level and
    # tool-level high-risk emitters can attach ``shipgate.yaml:N``
    # provenance for the missing-mitigation pointer. Defaults to the
    # unsupported sentinel — direct construction in tests and older
    # callers that don't thread the index keep working with
    # ``positions.lookup(...) == None``.
    manifest_positions: PositionIndex = field(default_factory=_empty_position_index)

    def artifact(self, source_type: str, expected_type: type[T]) -> T | None:
        return self.framework_artifacts.get(source_type, expected_type)

    # DEPRECATED: legacy attribute access kept for v0.11 plugin compatibility.
    # Remove in v0.12. Use context.artifact("...", T) or
    # context.framework_artifacts. These properties still use ArtifactBag's
    # type validation and raise TypeError if a key contains the wrong artifact.
    @property
    def api_artifacts(self) -> OpenAIApiArtifacts | None:
        return self.artifact("openai_api", OpenAIApiArtifacts)

    @property
    def anthropic_artifacts(self) -> AnthropicArtifacts | None:
        return self.artifact("anthropic_api", AnthropicArtifacts)

    @property
    def adk_artifacts(self) -> GoogleAdkArtifacts | None:
        return self.artifact("google_adk", GoogleAdkArtifacts)

    @property
    def langchain_artifacts(self) -> LangChainArtifacts | None:
        return self.artifact("langchain", LangChainArtifacts)

    @property
    def crewai_artifacts(self) -> CrewAiArtifacts | None:
        return self.artifact("crewai", CrewAiArtifacts)

    @property
    def codex_plugin_artifacts(self) -> CodexPluginArtifacts | None:
        return self.artifact("codex_plugin", CodexPluginArtifacts)

    @property
    def n8n_artifacts(self) -> N8nArtifacts | None:
        return self.artifact("n8n", N8nArtifacts)

    @property
    def validation_artifacts(self) -> ValidationArtifacts | None:
        return self.artifact("validation", ValidationArtifacts)
