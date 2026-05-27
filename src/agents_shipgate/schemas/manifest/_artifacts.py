from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


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
        raise TypeError("artifact entries must be a list")
    entries: list[ArtifactPathConfig] = []
    for item in value:
        if isinstance(item, ArtifactPathConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(ArtifactPathConfig(path=item))
        elif isinstance(item, dict):
            entries.append(ArtifactPathConfig.model_validate(item))
        else:
            raise TypeError("artifact entries must be strings or objects")
    return entries


def _parse_named_artifact_entries(value: Any) -> list[NamedArtifactPathConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("artifact entries must be a list")
    entries: list[NamedArtifactPathConfig] = []
    for item in value:
        if isinstance(item, NamedArtifactPathConfig):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(NamedArtifactPathConfig(path=item))
        elif isinstance(item, dict):
            entries.append(NamedArtifactPathConfig.model_validate(item))
        else:
            raise TypeError("artifact entries must be strings or objects")
    return entries
