from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class PolicyToolEntry(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    tool: str
    tool_id: str | None = None
    provider: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    reason: str | None = None


def _parse_policy_entries(value: Any) -> list[PolicyToolEntry]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("policy value must be a list")
    entries: list[PolicyToolEntry] = []
    for item in value:
        if isinstance(item, PolicyToolEntry):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(PolicyToolEntry(tool=item))
        elif isinstance(item, dict):
            entries.append(PolicyToolEntry.model_validate(item))
        else:
            raise TypeError("policy entries must be strings or objects")
    return entries


class PoliciesConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    require_approval_for_tools: list[PolicyToolEntry] = Field(default_factory=list)
    require_confirmation_for_tools: list[PolicyToolEntry] = Field(default_factory=list)
    require_idempotency_for_tools: list[PolicyToolEntry] = Field(default_factory=list)

    @field_validator(
        "require_approval_for_tools",
        "require_confirmation_for_tools",
        "require_idempotency_for_tools",
        mode="before",
    )
    @classmethod
    def parse_entries(cls, value: Any) -> list[PolicyToolEntry]:
        return _parse_policy_entries(value)

    def approval_tools(self) -> set[str]:
        return {entry.tool for entry in self.require_approval_for_tools}

    def confirmation_tools(self) -> set[str]:
        return {entry.tool for entry in self.require_confirmation_for_tools}

    def idempotency_tools(self) -> set[str]:
        return {entry.tool for entry in self.require_idempotency_for_tools}
