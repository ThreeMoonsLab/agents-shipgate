from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    _parse_artifact_entries,
)


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
            raise TypeError("prompt_files must be a list")
        files: list[str] = []
        for item in value:
            if isinstance(item, str):
                files.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                files.append(item["path"])
            else:
                raise TypeError("prompt_files entries must be strings or objects with path")
        return files

    @field_validator("tools", "policy_rules", mode="before")
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    def has_inputs(self) -> bool:
        return any([self.prompt_files, self.tools, self.policy_rules])
