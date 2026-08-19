from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    NamedArtifactPathConfig,
    _parse_artifact_entries,
    _parse_named_artifact_entries,
)
from agents_shipgate.schemas.manifest._common import describe_yaml_shape


class OpenAIApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    prompt_files: list[str] = Field(default_factory=list)
    tools: list[ArtifactPathConfig] = Field(default_factory=list)
    function_schemas: list[NamedArtifactPathConfig] = Field(default_factory=list)
    response_formats: list[NamedArtifactPathConfig] = Field(default_factory=list)
    api_model_config: ArtifactPathConfig | None = Field(default=None, alias="model_config")
    test_cases: list[ArtifactPathConfig] = Field(default_factory=list)
    trace_samples: list[ArtifactPathConfig] = Field(default_factory=list)
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

    @field_validator("tools", "test_cases", "trace_samples", "policy_rules", mode="before")
    @classmethod
    def parse_artifacts(cls, value: Any) -> list[ArtifactPathConfig]:
        return _parse_artifact_entries(value)

    @field_validator("function_schemas", "response_formats", mode="before")
    @classmethod
    def parse_named_artifacts(cls, value: Any) -> list[NamedArtifactPathConfig]:
        return _parse_named_artifact_entries(value)

    @field_validator("api_model_config", mode="before")
    @classmethod
    def parse_model_config(cls, value: Any) -> ArtifactPathConfig | None:
        if value is None:
            return None
        if isinstance(value, str):
            return ArtifactPathConfig(path=value)
        if isinstance(value, dict):
            return ArtifactPathConfig.model_validate(value)
        raise ValueError(
            "must be a path string or an object with a path, but is "
            f"{describe_yaml_shape(value)}"
        )
