from __future__ import annotations

from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    GoogleAdkArtifacts,
    OpenAIApiArtifacts,
)
from agents_shipgate.core.domain import Agent, Tool
from agents_shipgate.core.privacy import redact_and_truncate

#: How much of a prompt file or ADK instruction the agent record publishes as
#: ``instructions.value_preview``. The text is redacted before it is cut: cut
#: first, a credential straddling the bound kept a head too short for the
#: report-time redaction to recognise, and that head was published (#1079).
INSTRUCTIONS_PREVIEW_CHARS = 500


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
        instructions_preview = redact_and_truncate(
            api_artifacts.prompt_text, INSTRUCTIONS_PREVIEW_CHARS
        )
        instruction_source = "openai_api_prompt_files"
        instruction_confidence = "high"
    if (
        not instructions_preview
        and anthropic_artifacts
        and anthropic_artifacts.prompt_text
    ):
        instructions_preview = redact_and_truncate(
            anthropic_artifacts.prompt_text, INSTRUCTIONS_PREVIEW_CHARS
        )
        instruction_source = "anthropic_prompt_files"
        instruction_confidence = "high"
    if not instructions_preview and adk_artifacts:
        adk_instruction = _first_adk_instruction_preview(adk_artifacts)
        if adk_instruction:
            instructions_preview = redact_and_truncate(
                adk_instruction, INSTRUCTIONS_PREVIEW_CHARS
            )
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
