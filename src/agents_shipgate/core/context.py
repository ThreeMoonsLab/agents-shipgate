from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import (
    Agent,
    AnthropicArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
    OpenAIApiArtifacts,
    Tool,
    ValidationArtifacts,
)


@dataclass
class ScanContext:
    manifest: AgentsShipgateManifest
    agent: Agent
    tools: list[Tool]
    config_path: Path
    api_artifacts: OpenAIApiArtifacts | None = None
    anthropic_artifacts: AnthropicArtifacts | None = None
    adk_artifacts: GoogleAdkArtifacts | None = None
    langchain_artifacts: LangChainArtifacts | None = None
    crewai_artifacts: CrewAiArtifacts | None = None
    n8n_artifacts: N8nArtifacts | None = None
    validation_artifacts: ValidationArtifacts | None = None
