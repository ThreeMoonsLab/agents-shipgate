from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from agents_shipgate.schemas.verification_identity import CONTENT_ID_PATTERN

ATTESTATION_SCHEMA_VERSION = "0.6"
#: Earlier attestation versions a reader upgrades in place: every field added
#: since has a default (0.6's ``approval`` reads as ``not_supplied``).
ATTESTATION_SCHEMA_VERSIONS_UPGRADED_ON_READ: frozenset[str] = frozenset(
    {"0.3", "0.4", "0.5"}
)

#: Attestation 0.6. What GitHub's reviews of the pull request say, evaluated
#: against the attested head commit. ``not_supplied`` is every attestation made
#: without ``--reviews``.
ApprovalStatus = Literal[
    "approved",
    "approved_stale",
    "changes_requested",
    "not_approved",
    "not_supplied",
]
ApprovalSource = Literal["github_pull_request_reviews"]
IgnoredApprovalReason = Literal["pr_author", "bot"]


def approval_status_for(
    approvers_on_head: Iterable[bool],
    changes_requested_by: Iterable[str],
) -> ApprovalStatus:
    """The status a set of standing approvals and change requests states.

    Shared by the evaluator that writes an attestation and the validator that
    reads one, so the two cannot disagree. Any standing change request wins,
    as in GitHub's own review decision; then an approval on the attested head;
    then an approval on an earlier commit.
    """

    if any(True for _ in changes_requested_by):
        return "changes_requested"
    on_head = list(approvers_on_head)
    if any(on_head):
        return "approved"
    if on_head:
        return "approved_stale"
    return "not_approved"


class AttestationVerdictV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merge_verdict: str | None = None
    decision: str | None = None
    applicability: str | None = None
    can_merge_without_human: bool = False


class AttestationCapabilitySummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: int = Field(default=0, ge=0)
    modified: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    trust_root_touched: bool = False
    policy_weakened: bool = False
    change_ids: list[str] = Field(default_factory=list)


class AttestationHumanAckV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = False
    satisfied: bool | None = None
    outstanding: list[str] = Field(default_factory=list)
    acks: list[dict[str, str | None]] = Field(default_factory=list)


class AttestationOrgContextV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_id: str | None = None
    repo: str | None = None
    service: str | None = None
    tier: str | None = None
    pr_number: str | None = None
    workflow_run_id: str | None = None
    actor: str | None = None
    merge_sha: str | None = None


class AttestationCapabilityLockBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    sha256: str | None = None
    capability_lock_schema_version: str | None = None
    semantic_capability_set_hash: str | None = None
    evidence_set_hash: str | None = None
    source_set_hash: str | None = None
    # Attestation 0.6, capability standard 0.6: the tool definitions the
    # attested lock was reviewed with. ``None`` for an older lock.
    definition_set_hash: str | None = None
    capability_count: int | None = Field(default=None, ge=0)


class AttestationCapabilityDiffSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    reidentified: int = Field(default=0, ge=0)
    changed: int = Field(default=0, ge=0)
    evidence_changed: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    definition_changed: int = Field(default=0, ge=0)


class AttestationApproverV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str
    review_id: int
    submitted_at: str
    commit_id: str
    on_head: bool


class AttestationIgnoredApprovalV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str
    review_id: int
    reason: IgnoredApprovalReason


class AttestationApprovalV1(BaseModel):
    """Who approved the pull request, from host-supplied GitHub reviews.

    Evidence about the change, never a verdict: it does not change
    ``verdict``, the release decision or an exit code. ``status`` is
    recomputed from the lists on read, so a hand-edited attestation that
    claims an approval its own rows do not support is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    status: ApprovalStatus = "not_supplied"
    source: ApprovalSource | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pr_number: str | None = None
    pr_author: str | None = None
    head_sha: str | None = None
    approvers: list[AttestationApproverV1] = Field(default_factory=list)
    changes_requested_by: list[str] = Field(default_factory=list)
    ignored_approvals: list[AttestationIgnoredApprovalV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_is_what_the_rows_say(self) -> AttestationApprovalV1:
        supplied = (
            self.source,
            self.source_sha256,
            self.pr_number,
            self.pr_author,
            self.head_sha,
        )
        if self.status == "not_supplied":
            if any(value is not None for value in supplied) or (
                self.approvers or self.changes_requested_by or self.ignored_approvals
            ):
                raise ValueError("a not_supplied approval carries no review evidence")
            return self
        if any(value is None for value in supplied):
            raise ValueError(
                "an evaluated approval names its source, source_sha256, pr_number, "
                "pr_author and head_sha"
            )
        head = (self.head_sha or "").lower()
        if any(item.on_head != (item.commit_id.lower() == head) for item in self.approvers):
            raise ValueError("approvers[].on_head must say whether commit_id is head_sha")
        logins = [item.login.lower() for item in self.approvers]
        if len(logins) != len(set(logins)):
            raise ValueError("each approver appears once")
        if (self.pr_author or "").lower() in logins:
            raise ValueError("the pull request author is never an approver")
        expected = approval_status_for(
            (item.on_head for item in self.approvers), self.changes_requested_by
        )
        if self.status != expected:
            raise ValueError(
                f"approval status {self.status!r} does not match its rows ({expected!r})"
            )
        return self


class AttestationCapabilityDiffBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    sha256: str | None = None
    capability_lock_diff_schema_version: str | None = None
    base_semantic_capability_set_hash: str | None = None
    head_semantic_capability_set_hash: str | None = None
    summary: AttestationCapabilityDiffSummaryV1 | None = None


class ReleaseAttestationV1(BaseModel):
    """Deterministic local release attestation emitted by ``agents-shipgate attest``."""

    model_config = ConfigDict(extra="forbid")

    attestation_schema_version: Literal["0.6"] = ATTESTATION_SCHEMA_VERSION
    cli_version: str
    org: AttestationOrgContextV1 = Field(default_factory=AttestationOrgContextV1)
    source_verifier: str
    redacted: bool = True
    run_id: str | None = None
    request_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    receipt_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    decision_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    artifact_set_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    verification_receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    verify_run_sha256: str | None = None
    event_time: str | None = None
    source_url: str | None = None
    branch: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    base_tree_sha: str | None = None
    head_tree_sha: str | None = None
    mode: str | None = None
    verdict: AttestationVerdictV1
    capability: AttestationCapabilitySummaryV1
    capability_lock: AttestationCapabilityLockBindingV1
    capability_diff: AttestationCapabilityDiffBindingV1 | None = None
    human_ack: AttestationHumanAckV1
    approval: AttestationApprovalV1 = Field(default_factory=AttestationApprovalV1)
    policy_snapshot_sha256: str | None = None
    policy_packs: list[dict[str, str | int | None]] = Field(default_factory=list)
    artifact_sha256: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _receipt_identity_is_all_or_none(self) -> ReleaseAttestationV1:
        fields = (
            self.request_id,
            self.receipt_id,
            self.decision_id,
            self.artifact_set_id,
            self.verification_receipt_sha256,
        )
        if any(value is not None for value in fields) and not all(
            value is not None for value in fields
        ):
            raise ValueError("verification receipt identity must be complete when present")
        return self


class ReleaseAttestationArtifactV1(RootModel[ReleaseAttestationV1]):
    root: ReleaseAttestationV1


__all__ = [
    "ATTESTATION_SCHEMA_VERSION",
    "ATTESTATION_SCHEMA_VERSIONS_UPGRADED_ON_READ",
    "ApprovalSource",
    "ApprovalStatus",
    "AttestationApprovalV1",
    "AttestationApproverV1",
    "AttestationIgnoredApprovalV1",
    "IgnoredApprovalReason",
    "approval_status_for",
    "AttestationCapabilityDiffBindingV1",
    "AttestationCapabilityDiffSummaryV1",
    "AttestationCapabilityLockBindingV1",
    "AttestationCapabilitySummaryV1",
    "AttestationHumanAckV1",
    "AttestationOrgContextV1",
    "AttestationVerdictV1",
    "ReleaseAttestationArtifactV1",
    "ReleaseAttestationV1",
]
