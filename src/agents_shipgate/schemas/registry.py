from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents_shipgate.schemas.attestation import AttestationApprovalV1
from agents_shipgate.schemas.verification_identity import CONTENT_ID_PATTERN

REGISTRY_SCHEMA_VERSION = "0.6"

#: Row fields added after rows were first hashed. A row id and the
#: ``previous_row_sha256`` chain are hashes over the dumped row, so these are
#: left out of both while they hold ``None``: a ledger written by an earlier
#: release keeps the ids and chain it was written with.
REGISTRY_LATE_OPTIONAL_FIELDS: tuple[str, ...] = ("approval_status", "approval", "observation")

#: Row fields added after rows were first hashed that have a default. Each is
#: left out of both hashes while it holds that default, for the same reason.
REGISTRY_LATE_DEFAULTED_FIELDS: dict[str, Any] = {"row_kind": "attestation"}

RegistryRowKind = Literal["attestation", "drift_observation"]
DriftObservationStatus = Literal["clean", "drift", "incomparable"]
HostChangeDirection = Literal["added", "removed", "changed"]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class RegistryHostGrantChangeV1(BaseModel):
    """One typed host grant that moved, named by id and kind only.

    The grant's values (a command, its arguments, an env value, a URL, a rule)
    stay in the audit payload; the row records which grant moved and how.
    """

    model_config = ConfigDict(extra="forbid")

    grant_id: str
    kind: str | None = None
    host: str | None = None
    change: HostChangeDirection


class RegistryHostArtifactChangeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    kind: str | None = None
    host: str | None = None
    change: HostChangeDirection


class RegistryHostCoverageChangeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    change: HostChangeDirection


class RegistryHostComparisonV1(BaseModel):
    """What one ``audit --host --drift`` comparison found, without values."""

    model_config = ConfigDict(extra="forbid")

    host_grants_schema_version: str
    scope: str | None = None
    baseline_file: str
    comparison_status: Literal["comparable", "incomparable"]
    baseline_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    current_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    has_drift: bool | None = None
    incomparable_reasons: list[str] = Field(default_factory=list)
    grant_changes: list[RegistryHostGrantChangeV1] = Field(default_factory=list)
    artifact_changes: list[RegistryHostArtifactChangeV1] = Field(default_factory=list)
    coverage_changes: list[RegistryHostCoverageChangeV1] = Field(default_factory=list)
    #: Expansion signals counted by kind (``wildcard_allow_added``,
    #: ``deny_rule_removed``, …). The signal text names the rule or server, so
    #: only its kind is kept.
    expansion_signal_kinds: dict[str, int] = Field(default_factory=dict)
    issue_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _status_matches_drift(self) -> RegistryHostComparisonV1:
        moved = bool(self.grant_changes or self.artifact_changes or self.coverage_changes)
        if self.comparison_status == "comparable":
            if self.has_drift is not moved:
                raise ValueError(
                    "a comparable host comparison has drift exactly when it lists a change"
                )
        elif self.has_drift is not None or moved:
            raise ValueError("an incomparable host comparison states no drift and lists no change")
        return self


class RegistryCapabilityLockIdentityV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_lock_schema_version: str | None = None
    semantic_capability_set_hash: str | None = None
    definition_set_hash: str | None = None
    capability_count: int | None = None


class RegistryCapabilityComparisonV1(BaseModel):
    """What one ``capability diff`` between the reviewed lock and a fresh
    export found: counts and capability ids, never the facts."""

    model_config = ConfigDict(extra="forbid")

    capability_lock_diff_schema_version: str
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    base: RegistryCapabilityLockIdentityV1
    head: RegistryCapabilityLockIdentityV1
    summary: dict[str, int] = Field(default_factory=dict)
    added_ids: list[str] = Field(default_factory=list)
    removed_ids: list[str] = Field(default_factory=list)
    reidentified_ids: list[str] = Field(default_factory=list)
    changed_ids: list[str] = Field(default_factory=list)
    definition_changed_ids: list[str] = Field(default_factory=list)
    has_drift: bool

    @model_validator(mode="after")
    def _drift_matches_ids(self) -> RegistryCapabilityComparisonV1:
        moved = bool(
            self.added_ids
            or self.removed_ids
            or self.reidentified_ids
            or self.changed_ids
            or self.definition_changed_ids
        )
        if moved != self.has_drift:
            raise ValueError("capability has_drift must match the listed change ids")
        return self


class RegistryDriftObservationV1(BaseModel):
    """One scheduled comparison of the repository with its approved state.

    Evidence that monitoring ran, what it compared against and what it found.
    It never decides whether anything may merge or ship.
    """

    model_config = ConfigDict(extra="forbid")

    status: DriftObservationStatus
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    trigger: str | None = None
    host: RegistryHostComparisonV1
    capability: RegistryCapabilityComparisonV1 | None = None

    @model_validator(mode="after")
    def _status_matches_comparisons(self) -> RegistryDriftObservationV1:
        if self.status != drift_observation_status(self.host, self.capability):
            raise ValueError(
                "status must be drift when either comparison found drift, "
                "incomparable when the host comparison could not be made, "
                "and clean otherwise"
            )
        return self


def drift_observation_status(
    host: RegistryHostComparisonV1,
    capability: RegistryCapabilityComparisonV1 | None,
) -> DriftObservationStatus:
    """``drift`` over ``incomparable`` over ``clean``.

    A capability change found while the host comparison was incomparable is
    still drift: what was found is reported before what could not be read.
    """

    if host.has_drift is True or (capability is not None and capability.has_drift):
        return "drift"
    if host.comparison_status != "comparable":
        return "incomparable"
    return "clean"


class RegistryRowV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    registry_schema_version: str = REGISTRY_SCHEMA_VERSION
    row_id: str
    # Registry 0.6: what the row records. A row without the field was written
    # before observations existed and is an attestation row.
    row_kind: RegistryRowKind = "attestation"
    repo: str = ""
    org_id: str | None = None
    service: str | None = None
    tier: str | None = None
    pr_number: str | None = None
    workflow_run_id: str | None = None
    actor: str | None = None
    merge_sha: str | None = None
    event_time: str | None = None
    source_url: str | None = None
    branch: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    attestation_schema_version: str | None = None
    cli_version: str | None = None
    run_id: str | None = None
    request_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    receipt_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    decision_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    artifact_set_id: str | None = Field(default=None, pattern=CONTENT_ID_PATTERN)
    source_verification_receipt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_attestation_sha256: str | None = None
    source_verify_run_sha256: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    base_tree_sha: str | None = None
    head_tree_sha: str | None = None
    merge_verdict: str | None = None
    decision: str | None = None
    can_merge_without_human: bool | None = None
    capability_added: int | None = None
    capability_modified: int | None = None
    capability_removed: int | None = None
    capability_change_ids: list[str] = Field(default_factory=list)
    trust_root_touched: bool | None = None
    policy_weakened: bool | None = None
    human_ack_required: bool | None = None
    human_ack_satisfied: bool | None = None
    human_ack_outstanding: list[str] = Field(default_factory=list)
    human_ack: dict[str, Any] = Field(default_factory=dict)
    # Registry 0.5 (attestation 0.6): the attestation's ``approval`` block and
    # its status, copied as attested. ``None`` on a row from an older
    # attestation.
    approval_status: str | None = None
    approval: dict[str, Any] | None = None
    # Registry 0.6: a ``drift_observation`` row's comparison. ``None`` on an
    # attestation row, whose verdict and capability fields an observation
    # leaves empty.
    observation: RegistryDriftObservationV1 | None = None
    policy_snapshot_sha256: str | None = None
    artifact_sha256: dict[str, str] = Field(default_factory=dict)
    capability_lock: dict[str, Any] = Field(default_factory=dict)
    capability_diff: dict[str, Any] | None = None
    policy_packs: list[dict[str, Any]] = Field(default_factory=list)
    previous_row_id: str | None = None
    previous_row_sha256: str | None = None

    @model_validator(mode="after")
    def _approval_is_one_an_attestation_could_carry(self) -> RegistryRowV1:
        """An approval block reads only as the attestation model reads it.

        The block is stored as attested, so a row's id and chain hash are taken
        over the bytes that were ingested. It is still validated as
        :class:`AttestationApprovalV1`, on ingest and on every read: a row that
        claims ``approved`` with no approver, or a status its rows do not
        support, is not a row a reader counts.
        """

        if self.approval is None:
            if self.approval_status is not None:
                raise ValueError("approval_status is set but the row has no approval block")
            return self
        try:
            approval = AttestationApprovalV1.model_validate(self.approval)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ()))
            reason = str(first.get("msg", "")).removeprefix("Value error, ")
            raise ValueError(
                f"approval{'.' + where if where else ''}: {reason}"
            ) from None
        if self.approval_status != approval.status:
            raise ValueError(
                f"approval_status {self.approval_status!r} is not the approval "
                f"block's status {approval.status!r}"
            )
        return self


class RegistrySkippedRowV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1)
    reason: str


class RegistryQueryResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.6"] = REGISTRY_SCHEMA_VERSION
    registry: str
    count: int
    skipped_count: int = 0
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)
    rows: list[RegistryRowV1] = Field(default_factory=list)


class RegistryBypassReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.6"] = REGISTRY_SCHEMA_VERSION
    registry: str
    bypass_count: int
    skipped_count: int = 0
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)
    rows: list[RegistryRowV1] = Field(default_factory=list)


class RegistrySummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.6"] = REGISTRY_SCHEMA_VERSION
    registry: str
    count: int
    skipped_count: int = 0
    by_merge_verdict: dict[str, int] = Field(default_factory=dict)
    by_decision: dict[str, int] = Field(default_factory=dict)
    by_repo: dict[str, int] = Field(default_factory=dict)
    by_service: dict[str, int] = Field(default_factory=dict)
    by_tier: dict[str, int] = Field(default_factory=dict)
    by_approval_status: dict[str, int] = Field(default_factory=dict)
    by_row_kind: dict[str, int] = Field(default_factory=dict)
    by_observation_status: dict[str, int] = Field(default_factory=dict)
    bypass_count: int = 0
    trust_root_touched_count: int = 0
    policy_weakened_count: int = 0
    human_ack_required_count: int = 0
    human_ack_unsatisfied_count: int = 0
    policy_pack_unverified_count: int = 0
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)


class RegistryDriftGapV1(BaseModel):
    """A stretch with no observation longer than the expected interval.

    ``after_row_id`` is ``None`` when the stretch opens at ``--since``, and
    ``before_row_id`` when it closes at ``--until``.
    """

    model_config = ConfigDict(extra="forbid")

    repo: str
    start: str
    end: str
    duration_seconds: int = Field(ge=0)
    after_row_id: str | None = None
    before_row_id: str | None = None


class RegistryDriftRepoSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    observation_count: int = 0
    clean_count: int = 0
    drift_count: int = 0
    incomparable_count: int = 0
    first_observed_at: str | None = None
    last_observed_at: str | None = None
    gap_count: int = 0


class RegistryDriftReportV1(BaseModel):
    """``registry report --drift``: the observations in a window.

    Counts runs that found drift, runs that could not compare, and stretches
    without a run longer than ``expected_interval_days``. Evidence for a
    reviewer; ``release_decision.decision`` remains the only gate.
    """

    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.6"] = REGISTRY_SCHEMA_VERSION
    registry: str
    repo: str | None = None
    since: str | None = None
    until: str | None = None
    expected_interval_days: int | None = None
    observation_count: int = 0
    clean_count: int = 0
    drift_count: int = 0
    incomparable_count: int = 0
    host_drift_count: int = 0
    capability_drift_count: int = 0
    undated_count: int = 0
    gap_count: int = 0
    gaps: list[RegistryDriftGapV1] = Field(default_factory=list)
    repos: list[RegistryDriftRepoSummaryV1] = Field(default_factory=list)
    skipped_count: int = 0
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)
    rows: list[RegistryRowV1] = Field(default_factory=list)


class RegistryVerificationIssueV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str | None = None
    repo: str = ""
    kind: str
    message: str


class RegistryVerificationResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registry_schema_version: Literal["0.6"] = REGISTRY_SCHEMA_VERSION
    registry: str
    row_count: int
    skipped_count: int = 0
    issue_count: int = 0
    ok: bool = True
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)
    issues: list[RegistryVerificationIssueV1] = Field(default_factory=list)


__all__ = [
    "REGISTRY_LATE_DEFAULTED_FIELDS",
    "REGISTRY_LATE_OPTIONAL_FIELDS",
    "REGISTRY_SCHEMA_VERSION",
    "DriftObservationStatus",
    "RegistryBypassReportV1",
    "RegistryCapabilityComparisonV1",
    "RegistryCapabilityLockIdentityV1",
    "RegistryDriftGapV1",
    "RegistryDriftObservationV1",
    "RegistryDriftReportV1",
    "RegistryDriftRepoSummaryV1",
    "RegistryHostArtifactChangeV1",
    "RegistryHostComparisonV1",
    "RegistryHostCoverageChangeV1",
    "RegistryHostGrantChangeV1",
    "RegistryRowKind",
    "RegistryQueryResultV1",
    "RegistryRowV1",
    "RegistrySkippedRowV1",
    "RegistrySummaryV1",
    "RegistryVerificationIssueV1",
    "RegistryVerificationResultV1",
    "drift_observation_status",
]
