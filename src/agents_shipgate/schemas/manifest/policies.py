from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from agents_shipgate.schemas.manifest._common import (
    STRICT_MODEL_CONFIG,
    describe_yaml_shape,
)


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
        raise ValueError(
            "must be a list of tool entries, but is "
            f"{describe_yaml_shape(value)}"
        )
    entries: list[PolicyToolEntry] = []
    for index, item in enumerate(value):
        if isinstance(item, PolicyToolEntry):
            entries.append(item)
        elif isinstance(item, str):
            entries.append(PolicyToolEntry(tool=item))
        elif isinstance(item, dict):
            entries.append(PolicyToolEntry.model_validate(item))
        else:
            raise ValueError(
                f"entry {index} must be a tool-name string or an object with "
                f"a tool, but is {describe_yaml_shape(item)}"
            )
    return entries


class PoliciesConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    # #410 §F: which built-in control pack states what each effect obliges.
    # Chosen once for the repository instead of answered once per tool.
    # ``None`` means ``default`` — silence has to keep today's rules, or
    # adding the field would move every existing manifest's verdict. Every
    # other pack obliges a superset of ``default``, so this answer can only
    # tighten the gate (``core.control_packs``).
    control_pack: Literal["default", "financial-strict", "read-only-agent"] | None = (
        Field(
            default=None,
            description=(
                "Built-in control pack naming which controls each action "
                "effect obliges. Omit for 'default'. Every pack requires at "
                "least what 'default' requires."
            ),
        )
    )
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
