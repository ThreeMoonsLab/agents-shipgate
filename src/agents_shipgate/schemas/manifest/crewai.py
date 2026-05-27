from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    _parse_artifact_entries,
)
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class CrewAiConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    python_entrypoints: list[ArtifactPathConfig] = Field(default_factory=list)
    tool_inventories: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator("python_entrypoints", "tool_inventories", mode="before")
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any([self.python_entrypoints, self.tool_inventories])
