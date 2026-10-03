"""Pinned native containment contract and operator-owned execution inputs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.openshell import OpenShellPolicyReference
from agents_shipgate.schemas.verification_identity import VerificationSubject

DOMAINS = ["filesystem", "network_l4", "network_rest", "process", "landlock"]


class NativeObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PinnedFile(NativeObject):
    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class CandidateSelection(NativeObject):
    registration: str
    path: str = ""
    composition: str = ""

    @model_validator(mode="after")
    def local_selection(self):
        OpenShellPolicyReference(path=self.registration, role="authored")
        if bool(self.path) == bool(self.composition):
            raise ValueError("select exactly one document or local composition")
        if self.path:
            OpenShellPolicyReference(path=self.path, role="authored")
        return self


class NativeTrustConfig(NativeObject):
    version: Literal[1]
    required: bool
    runtime_version: Literal["0.1.2"]
    prover_version: Literal["0.1.2"]
    executable: PinnedFile
    boundary: PinnedFile
    candidate: CandidateSelection
    required_domains: list[str] = Field(min_length=5, max_length=5)
    timeout_seconds: int = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def domain_contract(self):
        if sorted(self.required_domains) != sorted(DOMAINS):
            raise ValueError("all pinned containment domains are required")
        return self


class Coverage(NativeObject):
    domains: list[str] = Field(max_length=5)


class NativeInputs(NativeObject):
    candidate: str
    boundary: str


class NativeEnvelope(NativeObject):
    schema_version: Literal[1]
    prover_version: Literal["0.1.2"]
    check: Literal["boundary"]
    coverage: Coverage | None
    result: Literal["within_boundary", "exceeds_boundary", "unsupported", "inconclusive", "error"]
    exit_code: int
    inputs: NativeInputs
    counterexample: dict | None
    reason_code: str | None
    reason: str | None


class ExternalIdentity(NativeObject):
    path: str
    state: Literal["file", "absent", "unconfirmable"]
    sha256: str | None = None
    size_bytes: int = Field(default=0, ge=0, le=64 * 1024 * 1024)


class NativeObservation(NativeObject):
    version: Literal[1] = 1
    provenance: Literal["not_executed", "local_trusted_execution"] = "not_executed"
    status: Literal["absent", "within_boundary", "exceeds_boundary", "unsupported", "inconclusive", "error", "invalid", "timeout", "cancelled"]
    reason_code: str
    required: bool
    external_inputs: list[ExternalIdentity] = Field(default_factory=list, max_length=3)
    candidate: dict = Field(default_factory=dict)
    invocation: list[str] = Field(default_factory=list)
    required_domains: list[str] = Field(default_factory=lambda: list(DOMAINS))
    actual_exit_code: int | None = None
    raw_result: str | None = Field(default=None, max_length=256 * 1024)
    raw_result_sha256: str | None = None
    modeled_containment_only: Literal[True] = True
    deployment_enforcement_verified: Literal[False] = False
    grants_merge_authority: Literal[False] = False


class NativeEvidence(NativeObject):
    evidence_schema_version: Literal["1"] = "1"
    observation: NativeObservation
    request_id: str
    subject: VerificationSubject
