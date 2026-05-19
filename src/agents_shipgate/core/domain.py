from __future__ import annotations

from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.common import Confidence, parse_confidence


class AuthInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    scopes: list[str] = Field(default_factory=list)
    credential_mode: str | None = None
    source: str | None = None


class ToolParameter(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    type: str | None = None
    required: bool = False
    description: str | None = None
    enum: list[Any] | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    format: str | None = None
    default: Any = None
    risk_hints: list[str] = Field(default_factory=list)


class ToolRiskHint(BaseModel):
    model_config = ConfigDict(extra="allow")

    tag: str
    source: str
    confidence: Confidence
    evidence: dict[str, Any] = Field(default_factory=dict)


class Tool(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str | None = None
    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    source_location: str | None = None
    source_path: str | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_column: int | None = None
    source_pointer: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    parameters: list[ToolParameter] = Field(default_factory=list)
    function_signature: str | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)
    auth: AuthInfo = Field(default_factory=AuthInfo)
    risk_hints: list[ToolRiskHint] = Field(default_factory=list)
    owner: str | None = None
    extraction_confidence: Confidence = "low"
    extraction: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_extraction_confidence(self) -> Tool:
        raw_confidence = self.extraction.get("confidence")
        if isinstance(raw_confidence, str) and raw_confidence in get_args(Confidence):
            self.extraction_confidence = parse_confidence(raw_confidence)
        else:
            self.extraction["confidence"] = self.extraction_confidence
        return self


class Agent(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    source: dict[str, Any] = Field(default_factory=dict)
    instructions: dict[str, Any] = Field(default_factory=dict)
    declared_purpose: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    handoffs: list[str] = Field(default_factory=list)
    guardrails: dict[str, Any] = Field(default_factory=dict)
    extraction: dict[str, Any] = Field(default_factory=dict)


class LoadedToolSource(BaseModel):
    source_id: str
    source_type: str
    tools: list[Tool] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
