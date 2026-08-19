from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import ArtifactPathConfig
from agents_shipgate.schemas.manifest._common import (
    STRICT_MODEL_CONFIG,
    describe_yaml_shape,
)


class CodexPluginMcpInventoryConfig(ArtifactPathConfig):
    model_config = STRICT_MODEL_CONFIG

    plugin: str
    server: str


def _parse_codex_plugin_inventory_entries(
    value: Any,
) -> list[CodexPluginMcpInventoryConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            "must be a list of inventory objects, but is "
            f"{describe_yaml_shape(value)}"
        )
    entries: list[CodexPluginMcpInventoryConfig] = []
    for index, item in enumerate(value):
        if isinstance(item, CodexPluginMcpInventoryConfig):
            entries.append(item)
        elif isinstance(item, dict):
            entries.append(CodexPluginMcpInventoryConfig.model_validate(item))
        else:
            raise ValueError(
                f"entry {index} must be an object with plugin, server, and "
                f"path, but is {describe_yaml_shape(item)}"
            )
    return entries


class CodexPluginsConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    mcp_tool_inventories: list[CodexPluginMcpInventoryConfig] = Field(
        default_factory=list
    )

    @field_validator("mcp_tool_inventories", mode="before")
    @classmethod
    def parse_mcp_tool_inventories(
        cls, value: Any
    ) -> list[CodexPluginMcpInventoryConfig]:
        return _parse_codex_plugin_inventory_entries(value)
