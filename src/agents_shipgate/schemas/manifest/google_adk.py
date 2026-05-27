from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    _parse_artifact_entries,
)
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class GoogleAdkConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    python_entrypoints: list[ArtifactPathConfig] = Field(default_factory=list)
    agent_configs: list[ArtifactPathConfig] = Field(default_factory=list)
    eval_sets: list[ArtifactPathConfig] = Field(default_factory=list)
    tool_inventories: list[ArtifactPathConfig] = Field(default_factory=list)
    trace_samples: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator(
        "python_entrypoints",
        "agent_configs",
        "eval_sets",
        "tool_inventories",
        "trace_samples",
        mode="before",
    )
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any(
            [
                self.python_entrypoints,
                self.agent_configs,
                self.eval_sets,
                self.tool_inventories,
                self.trace_samples,
            ]
        )
