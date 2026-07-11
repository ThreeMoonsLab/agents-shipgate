from __future__ import annotations

from pydantic import BaseModel, Field

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class ToolRiskOverride(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    tags: list[str] = Field(default_factory=list)
    remove_tags: list[str] = Field(default_factory=list)
    owner: str | None = None
    confidence: str = "manual"
    reason: str
    tool: str | None = None
    tool_id: str | None = None
    provider: str | None = None
    source_type: str | None = None
    source_id: str | None = None


class RiskOverridesConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    tools: dict[str, ToolRiskOverride] = Field(default_factory=dict)
    selectors: list[ToolRiskOverride] = Field(default_factory=list)
