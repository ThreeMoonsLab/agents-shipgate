from __future__ import annotations

from pydantic import BaseModel, Field

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class AgentSdkConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    type: str | None = None
    language: str | None = None
    entrypoint: str | None = None
    object: str | None = None
    static_extract: bool = True
    deep_import: bool = False


class AgentConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    name: str
    sdk: AgentSdkConfig | None = None
    declared_purpose: list[str] = Field(default_factory=list)
    instructions_preview: str | None = None
    prohibited_actions: list[str] = Field(default_factory=list)
