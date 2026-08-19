from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    _parse_artifact_entries,
)
from agents_shipgate.schemas.manifest._common import describe_yaml_shape


class AnthropicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    prompt_files: list[str] = Field(default_factory=list)
    tools: list[ArtifactPathConfig] = Field(default_factory=list)
    policy_rules: list[ArtifactPathConfig] = Field(default_factory=list)

    @field_validator("prompt_files", mode="before")
    @classmethod
    def parse_prompt_files(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(
                "must be a list of prompt files, but is "
                f"{describe_yaml_shape(value)}"
            )
        files: list[str] = []
        for index, item in enumerate(value):
            if isinstance(item, str):
                files.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                files.append(item["path"])
            else:
                raise ValueError(
                    f"entry {index} must be a path string or an object with "
                    f"a path, but is {describe_yaml_shape(item)}"
                )
        return files

    @field_validator("tools", "policy_rules", mode="before")
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any([self.prompt_files, self.tools, self.policy_rules])
