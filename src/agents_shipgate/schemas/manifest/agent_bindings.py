from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class AgentBindingRootConfig(BaseModel):
    """Exact selector for the statically reviewed root agent."""

    model_config = STRICT_MODEL_CONFIG

    object: str
    source_id: str | None = None

    @field_validator("object", "source_id")
    @classmethod
    def require_non_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("agent binding root selectors require non-blank values")
        return normalized


class BoundToolSelectorConfig(BaseModel):
    """One canonical tool selector inside a reviewed binding declaration."""

    model_config = STRICT_MODEL_CONFIG

    tool: str
    tool_id: str | None = None
    provider: str | None = None
    source_type: str | None = None
    source_id: str | None = None

    @field_validator("tool", "tool_id", "provider", "source_type", "source_id")
    @classmethod
    def require_non_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("agent binding tool selectors require non-blank values")
        return normalized


class AgentBindingDeclarationConfig(BaseModel):
    """Reviewed, closed-world tool and handoff set for one agent."""

    model_config = STRICT_MODEL_CONFIG

    agent: str
    complete: Literal[True] = True
    tools: list[BoundToolSelectorConfig] = Field(default_factory=list)
    handoffs: list[str] = Field(default_factory=list)
    reason: str

    @field_validator("agent", "reason")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("agent binding declarations require non-blank agent and reason")
        return normalized

    @field_validator("handoffs")
    @classmethod
    def validate_handoffs(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("agent binding handoffs require non-blank values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("agent binding handoffs contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_tools(self) -> AgentBindingDeclarationConfig:
        keys = [
            (
                selector.tool_id or "",
                selector.tool,
                selector.provider or "",
                selector.source_type or "",
                selector.source_id or "",
            )
            for selector in self.tools
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("agent binding declarations contain duplicate tool selectors")
        return self


class AgentBindingsConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    root: AgentBindingRootConfig | None = None
    declarations: list[AgentBindingDeclarationConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_agent_declarations(self) -> AgentBindingsConfig:
        agents = [declaration.agent for declaration in self.declarations]
        if len(set(agents)) != len(agents):
            raise ValueError("agent_bindings.declarations[].agent values must be unique")
        return self


__all__ = [
    "AgentBindingDeclarationConfig",
    "AgentBindingRootConfig",
    "AgentBindingsConfig",
    "BoundToolSelectorConfig",
]
