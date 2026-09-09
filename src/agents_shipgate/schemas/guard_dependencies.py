"""Source-reader evidence for one bounded guard, never release authority."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GuardInputEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["tool_module", "guard_module", "package_initializer"]


class BooleanSourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_symbol: str
    agent_name: str
    tool_bound: bool


class BooleanSourceBehavior(BaseModel):
    """A closed source-function model, never deployed behavior or policy evidence."""

    model_config = ConfigDict(extra="forbid")

    reader_profile: Literal["sdk_boolean_function/v1"] = "sdk_boolean_function/v1"
    status: Literal["observed", "unresolved", "redacted"]
    reason: str
    parameters: list[str] = Field(default_factory=list)
    # Entry i is the literal result for input mask i, over all tool parameters.
    returns: list[Literal["true", "false", "none"]] = Field(default_factory=list)
    binding: BooleanSourceBinding | None = None
    configuration_reads: Literal["none"] | None = None


BooleanDomainDirection = Literal["unchanged", "widened", "narrowed", "changed", "unresolved"]


class BooleanSourceComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    returns: Literal["unchanged", "changed", "unresolved"] = "unresolved"
    true_domain: BooleanDomainDirection = "unresolved"
    binding: Literal["unchanged", "added", "removed", "changed", "unresolved"] = "unresolved"
    bound_true_domain: BooleanDomainDirection = "unresolved"
    reason: str
    finding_exclusion_eligible: Literal[False] = False


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
    source_behavior: BooleanSourceBehavior | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
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
    source_behavior: BooleanSourceComparison | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    finding_exclusion_eligible: Literal[False] = False
