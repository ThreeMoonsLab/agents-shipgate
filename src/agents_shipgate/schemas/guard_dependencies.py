"""Source-reader evidence for one bounded guard, never release authority."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GuardInputEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["tool_module", "guard_module", "package_initializer"]


class GuardDependencyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reader_profile: Literal["sdk_boolean_guard/v1"] = "sdk_boolean_guard/v1"
    source_id: str
    observation_id: str
    tool_id: str | None = None
    tool_name: str
    tool_path: str
    tool_symbol: str
    tool_line: int = Field(ge=1)
    guard_symbol: str | None = None
    guard_path: str | None = None
    guard_line: int | None = Field(default=None, ge=1)
    call_line: int | None = Field(default=None, ge=1)
    status: Literal["observed", "unresolved", "ambiguous", "redacted"]
    reason: str
    # Bit i represents parameters[i]. These are syntactic Boolean inputs,
    # not executions, a claim about the downstream effect, or a safe verdict.
    parameters: list[str] = Field(default_factory=list)
    allowed_inputs: list[int] = Field(default_factory=list)
    inputs: list[GuardInputEvidence] = Field(default_factory=list)
    absent_paths: list[str] = Field(default_factory=list)
    dependency_coverage: Literal["incomplete"] = "incomplete"
    finding_exclusion_eligible: Literal[False] = False


class GuardDependencyComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    tool_id: str | None = None
    tool_name: str
    direction: Literal[
        "predicate_unchanged",
        "predicate_widened",
        "predicate_narrowed",
        "predicate_changed",
        "unresolved",
    ]
    reason: str
    before: GuardDependencyEvidence | None = None
    after: GuardDependencyEvidence | None = None
    finding_exclusion_eligible: Literal[False] = False
