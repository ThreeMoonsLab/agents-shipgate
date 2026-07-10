from __future__ import annotations

from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    GoogleAdkArtifacts,
    OpenAIApiArtifacts,
)
from agents_shipgate.core.domain import Agent, Tool


def _build_agent(
    manifest,
    tools: list[Tool],
    api_artifacts: OpenAIApiArtifacts | None = None,
    anthropic_artifacts: AnthropicArtifacts | None = None,
    adk_artifacts: GoogleAdkArtifacts | None = None,
) -> Agent:
    sdk = manifest.agent.sdk
    instructions_preview = manifest.agent.instructions_preview
    instruction_source = "config" if instructions_preview else "dynamic_unknown"
    instruction_confidence = "high" if instructions_preview else "medium"
    if not instructions_preview and api_artifacts and api_artifacts.prompt_text:
        instructions_preview = api_artifacts.prompt_text[:500]
        instruction_source = "openai_api_prompt_files"
        instruction_confidence = "high"
    if (
        not instructions_preview
        and anthropic_artifacts
        and anthropic_artifacts.prompt_text
    ):
        instructions_preview = anthropic_artifacts.prompt_text[:500]
        instruction_source = "anthropic_prompt_files"
        instruction_confidence = "high"
    if not instructions_preview and adk_artifacts:
        adk_instruction = _first_adk_instruction_preview(adk_artifacts)
        if adk_instruction:
            instructions_preview = adk_instruction[:500]
            instruction_source = "google_adk_static"
            instruction_confidence = "medium"
    return Agent(
        id=f"agent:{manifest.project.name}/{manifest.agent.name}",
        name=manifest.agent.name,
        source=sdk.model_dump(exclude_none=True) if sdk else {"source": "manifest"},
        instructions={
            "value_preview": instructions_preview,
            "source": instruction_source,
            "confidence": instruction_confidence,
        },
        declared_purpose=manifest.agent.declared_purpose,
        prohibited_actions=manifest.agent.prohibited_actions,
        tools=[tool.name for tool in tools],
        tool_ids=[tool.id for tool in tools],
        guardrails={
            "input": "unknown",
            "output": "unknown",
            "tool": "unknown",
            "source": "unknown",
        },
        extraction={
            "method": "config_assisted",
            "confidence": "medium",
            "missing_fields": ["runtime_traces"],
            "dynamic_fields": [],
        },
    )


def _first_adk_instruction_preview(adk_artifacts: GoogleAdkArtifacts) -> str | None:
    for agent in adk_artifacts.agents:
        value = agent.get("instruction_preview")
        if isinstance(value, str) and value.strip():
            return value
    return None
