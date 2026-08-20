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


class ToolInventoryConfig(ArtifactPathConfig):
    """A reviewed tool inventory, optionally bound to the source it completes.

    ``source_id`` names the ``tool_sources[].id`` (or framework-entrypoint
    source id) whose surface this file enumerates. Without it the inventory is
    an *independent* source: its entries become new observations that merely
    happen to share names with the statically-extracted ones, so the catalog
    grows and the ``incomplete_surface`` gap keyed to the original source is
    never satisfied (#386). With it, each named tool is joined to that source's
    observation through the one reviewed-binding engine in
    ``core/tool_identity.py`` — nothing is ever joined by name alone.

    Entries the completed source does not expose stay standalone observations:
    an inventory exists precisely because static extraction may have missed
    tools, and a tool nobody wired is honestly reported as unbound rather than
    silently attributed to an agent.
    """

    model_config = STRICT_MODEL_CONFIG

    source_id: str | None = None


def _parse_tool_inventory_entries(value: Any) -> list[ToolInventoryConfig]:
    """Parse ``<framework>.tool_inventories``, accepting the bare-path form."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            "must be a list of tool inventories, but is "
            f"{describe_yaml_shape(value)}"
        )
    entries: list[ToolInventoryConfig] = []
    for index, item in enumerate(value):
        if isinstance(item, ToolInventoryConfig):
            entries.append(item)
        elif isinstance(item, ArtifactPathConfig):
            entries.append(
                ToolInventoryConfig(path=item.path, optional=item.optional)
            )
        elif isinstance(item, str):
            entries.append(ToolInventoryConfig(path=item))
        elif isinstance(item, dict):
            entries.append(ToolInventoryConfig.model_validate(item))
        else:
            raise ValueError(
                f"entry {index} must be a path string or an object with a path "
                "(and optionally source_id, the tool source this inventory "
                f"completes), but is {describe_yaml_shape(item)}"
            )
    return entries
