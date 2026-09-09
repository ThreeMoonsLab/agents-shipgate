"""Versioned structural evidence and conditional instruction-edit routing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InstructionStructureEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    status: Literal["guidance", "structured", "unresolved"]
    sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    reason: str

    @model_validator(mode="after")
    def validate_digest(self):
        if (self.status == "unresolved") != (self.sha256 is None):
            raise ValueError("Only a resolved structure has a comparison digest")
        return self


class ConditionalInstructionEditRule(BaseModel):
    """Standing routing rule, never an edit permission or a cached approval."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.conditional_instruction_edit/v1"] = (
        "shipgate.conditional_instruction_edit/v1"
    )
    patterns: list[str] = Field(min_length=1)
    condition: Literal["complete_unchanged_instruction_structure"] = (
        "complete_unchanged_instruction_structure"
    )
    otherwise: Literal["human_review_required"] = "human_review_required"
    verification_required: Literal[True] = True
    grants_authority: Literal[False] = False
    preflight_command: str = Field(min_length=1)
