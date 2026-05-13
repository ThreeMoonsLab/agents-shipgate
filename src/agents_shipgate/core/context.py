from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.artifacts import ArtifactBag
from agents_shipgate.core.models import (
    Agent,
    AnthropicArtifacts,
    CodexPluginArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
    OpenAIApiArtifacts,
    Tool,
    ValidationArtifacts,
)

T = TypeVar("T")


@dataclass
class ScanContext:
    manifest: AgentsShipgateManifest
    agent: Agent
    tools: list[Tool]
    config_path: Path
    framework_artifacts: ArtifactBag = field(default_factory=ArtifactBag)

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
