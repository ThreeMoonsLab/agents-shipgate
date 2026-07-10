from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class ToolObservationSelectorConfig(BaseModel):
    """Exact selector for one extracted tool observation."""

    model_config = STRICT_MODEL_CONFIG

    source_id: str
    tool: str
    source_type: str | None = None

    @field_validator("source_id", "tool")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tool identity selectors require non-blank values")
        return normalized


class ToolIdentityBindingConfig(BaseModel):
    """Reviewed declaration that observations describe one capability."""

    model_config = STRICT_MODEL_CONFIG

    id: str
    provider: str
    reason: str
    primary: ToolObservationSelectorConfig
    members: list[ToolObservationSelectorConfig] = Field(default_factory=list)

    @field_validator("id", "provider", "reason")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tool identity bindings require non-blank id, provider, and reason")
        return normalized

    @model_validator(mode="after")
    def validate_members(self) -> ToolIdentityBindingConfig:
        if len(self.members) < 2:
            raise ValueError("tool_identity.bindings[].members requires at least two observations")
        member_keys = [
            (member.source_type or "", member.source_id, member.tool)
            for member in self.members
        ]
        if len(set(member_keys)) != len(member_keys):
            raise ValueError("tool_identity.bindings[].members contains duplicate selectors")
        primary_key = (
            self.primary.source_type or "",
            self.primary.source_id,
            self.primary.tool,
        )
        if primary_key not in member_keys:
            raise ValueError("tool_identity.bindings[].primary must also appear in members")
        return self


class ToolIdentityConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    bindings: list[ToolIdentityBindingConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_bindings(self) -> ToolIdentityConfig:
        ids = [binding.id for binding in self.bindings]
        if len(set(ids)) != len(ids):
            raise ValueError("tool_identity.bindings[].id values must be unique")
        return self


__all__ = [
    "ToolIdentityBindingConfig",
    "ToolIdentityConfig",
    "ToolObservationSelectorConfig",
]
