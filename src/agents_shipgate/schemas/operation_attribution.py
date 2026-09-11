"""Declared operation/predicate attribution; never runtime or merge authority."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["openapi_document", "manifest"]


class DeclaredOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reader_profile: Literal["openapi_delete/v1"] = "openapi_delete/v1"
    source_id: str
    observation_id: str
    tool_name: str
    method: str
    path_template: str
    source_pointer: str
    status: Literal["observed", "unresolved", "ambiguous", "redacted"]
    reason: str
    server: str | None = None
    # Fully expanded, finite *declared* URLs, not server-enforced permissions.
    declared_targets: list[str] = Field(default_factory=list)
    security_contract: str | None = None
    inputs: list[OperationInput] = Field(default_factory=list)


class OperationPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: Literal["effect", "approval_obligation", "missing_approval_policy"]
    observed: str
    claim_ids: list[str] = Field(default_factory=list)
    source_pointer: str
    contributing_pointers: list[str] = Field(default_factory=list)


class OperationAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: DeclaredOperation
    tool_id: str | None = None
    capability_id: str | None = None
    binding_claim_ids: list[str] = Field(default_factory=list)
    declared_binding_path: list[str] = Field(default_factory=list)
    check_id: Literal["SHIP-POLICY-APPROVAL-MISSING"] = "SHIP-POLICY-APPROVAL-MISSING"
    finding_fingerprints: list[str] = Field(default_factory=list)
    status: Literal["observed", "unresolved", "ambiguous", "redacted"]
    reason: str
    predicates: list[OperationPredicate] = Field(default_factory=list)
    control_pack_identity: str | None = None
    missing_approval: bool | None = None
    dependency_coverage: Literal["incomplete"] = "incomplete"
    deployed_reachability: Literal["unknown"] = "unknown"
    finding_exclusion_eligible: Literal[False] = False


class OperationComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    before: OperationAttribution | None = None
    after: OperationAttribution | None = None
    evidence_source: Literal["reconstructed_git_base", "unavailable"] = "unavailable"
    base_tree: str | None = None
    declared_target_domain: Literal["unchanged", "narrowed", "widened", "changed", "unresolved"] = (
        "unresolved"
    )
    approval_predicate: Literal[
        "standing_weakness",
        "still_declared",
        "resolved_by_declaration",
        "newly_missing",
        "unresolved",
    ] = "unresolved"
    reason: str
    deployed_reachability: Literal["unknown"] = "unknown"
    finding_exclusion_eligible: Literal[False] = False
