from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION = "shipgate.org_evidence_bundle/v1"


class OrgEvidenceBundleArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str | None = None


class OrgEvidenceBundleV1(BaseModel):
    """Compact local artifact for org/fleet aggregation.

    This is a consumer artifact only. It cannot override or recalculate the
    release gate; ``release_decision.decision`` remains the only gating signal.
    """

    model_config = ConfigDict(extra="forbid")

    org_evidence_bundle_schema_version: Literal["shipgate.org_evidence_bundle/v1"] = (
        ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION
    )
    gating_signal: Literal["release_decision.decision"] = "release_decision.decision"
    bundle_kind: Literal["org_evidence"] = "org_evidence"
    cli_version: str
    organization: dict[str, Any] | None = None
    workspace: str
    config: str
    source_verifier: str
    source_report: str | None = None
    source_verify_run: str | None = None
    source_attestation: str | None = None
    attestation: dict[str, Any] | None = None
    registry_row: dict[str, Any] | None = None
    org_status: dict[str, Any] | None = None
    policy_packs: list[dict[str, Any]] = Field(default_factory=list)
    host_grants: dict[str, Any] | None = None
    host_grant_drift: dict[str, Any] | None = None
    privacy: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, OrgEvidenceBundleArtifactRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _not_a_second_gate(self) -> OrgEvidenceBundleV1:
        if self.gating_signal != "release_decision.decision":
            raise ValueError("org evidence bundles must name release_decision.decision")
        return self


class OrgEvidenceBundleArtifactV1(RootModel[OrgEvidenceBundleV1]):
    root: OrgEvidenceBundleV1


__all__ = [
    "ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION",
    "OrgEvidenceBundleArtifactRef",
    "OrgEvidenceBundleArtifactV1",
    "OrgEvidenceBundleV1",
]
