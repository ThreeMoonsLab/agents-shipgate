from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    _parse_artifact_entries,
)
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class N8nConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    workflows: list[ArtifactPathConfig] = Field(default_factory=list)
    credential_stubs: list[ArtifactPathConfig] = Field(default_factory=list)
    variable_stubs: list[ArtifactPathConfig] = Field(default_factory=list)
    data_table_schemas: list[ArtifactPathConfig] = Field(default_factory=list)
    execution_samples: list[ArtifactPathConfig] = Field(default_factory=list)
    eval_sets: list[ArtifactPathConfig] = Field(default_factory=list)
    tool_inventories: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator(
        "workflows",
        "credential_stubs",
        "variable_stubs",
        "data_table_schemas",
        "execution_samples",
        "eval_sets",
        "tool_inventories",
        mode="before",
    )
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any(
            [
                self.workflows,
                self.credential_stubs,
                self.variable_stubs,
                self.data_table_schemas,
                self.execution_samples,
                self.eval_sets,
                self.tool_inventories,
            ]
        )
