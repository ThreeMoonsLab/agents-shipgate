"""Advisory host comparison evidence; deliberately carries no verdict."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.capability_diff import CapabilityDiffRow
from agents_shipgate.schemas.current_control import CurrentControlWorkspaceIdentity


class HostComparisonLimit(BaseModel):
    """A surface this comparison did not read, and the change did not touch (#721).

    Named rather than dropped: rows exclude it, and the comparison makes no
    claim about it.
    """

    model_config = ConfigDict(extra="forbid")

    host: str
    limit: Literal["unsupported", "parse_failed", "experimental_coverage"]
    source: str
    detail: str


class HostComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_identity: CurrentControlWorkspaceIdentity | None = None
    comparison_status: Literal["comparable", "incomparable"]
    incomparable_reasons: list[str] = Field(default_factory=list)
    base_commit: str | None = None
    head_commit: str | None = None
    head_kind: Literal["commit", "worktree", "provided_diff"]
    base_inventory_sha256: str | None = None
    head_inventory_sha256: str | None = None
    paths: list[str] = Field(default_factory=list)
    rows: list[CapabilityDiffRow] = Field(default_factory=list)
    unchanged_limits: list[HostComparisonLimit] = Field(default_factory=list)
    static_analysis_only: Literal[True] = True

    @model_validator(mode="after")
    def comparison_health(self):
        if self.comparison_status == "incomparable" and (
            self.rows or not self.incomparable_reasons
        ):
            raise ValueError("incomparable input needs reasons and cannot publish rows")
        if self.comparison_status == "incomparable" and self.unchanged_limits:
            raise ValueError("incomparable input names no unchanged limits")
        if self.comparison_status == "comparable" and self.incomparable_reasons:
            raise ValueError("comparable input cannot carry incomparable reasons")
        return self
