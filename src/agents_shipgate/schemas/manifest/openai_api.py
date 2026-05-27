from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    NamedArtifactPathConfig,
    _parse_artifact_entries,
    _parse_named_artifact_entries,
)


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
        raise TypeError("model_config must be a string path or object with path")
