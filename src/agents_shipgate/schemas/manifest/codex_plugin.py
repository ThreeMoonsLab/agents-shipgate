from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._artifacts import ArtifactPathConfig
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


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
        raise TypeError("mcp_tool_inventories must be a list")
    entries: list[CodexPluginMcpInventoryConfig] = []
    for item in value:
        if isinstance(item, CodexPluginMcpInventoryConfig):
            entries.append(item)
        elif isinstance(item, dict):
            entries.append(CodexPluginMcpInventoryConfig.model_validate(item))
        else:
            raise TypeError("mcp_tool_inventories entries must be objects")
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
