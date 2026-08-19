from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agents_shipgate.schemas.manifest._common import (
    STRICT_MODEL_CONFIG,
    describe_yaml_shape,
)


class ArtifactPathConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    path: str
    optional: bool = False


class NamedArtifactPathConfig(ArtifactPathConfig):
    name: str | None = None
    downstream_critical_fields: list[str] = Field(default_factory=list)


def _parse_artifact_entries(value: Any) -> list[ArtifactPathConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            "must be a list of artifact paths, but is "
            f"{describe_yaml_shape(value)}"
        )
    entries: list[ArtifactPathConfig] = []
    for index, item in enumerate(value):
        if isinstance(item, ArtifactPathConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(ArtifactPathConfig(path=item))
        elif isinstance(item, dict):
            entries.append(ArtifactPathConfig.model_validate(item))
        else:
            raise ValueError(
                f"entry {index} must be a path string or an object with a "
                f"path, but is {describe_yaml_shape(item)}"
            )
    return entries


def _parse_named_artifact_entries(value: Any) -> list[NamedArtifactPathConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            "must be a list of artifact paths, but is "
            f"{describe_yaml_shape(value)}"
        )
    entries: list[NamedArtifactPathConfig] = []
    for index, item in enumerate(value):
        if isinstance(item, NamedArtifactPathConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(NamedArtifactPathConfig(path=item))
        elif isinstance(item, dict):
            entries.append(NamedArtifactPathConfig.model_validate(item))
        else:
            raise ValueError(
                f"entry {index} must be a path string or an object with a "
                f"path, but is {describe_yaml_shape(item)}"
            )
    return entries
