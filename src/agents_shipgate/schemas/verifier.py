from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VerifierBaseStatus = Literal[
    "not_requested",
    "skipped",
    "diff_from_provided",
    "ref_missing",
    "archive_failed",
    "missing_manifest",
    "scan_failed",
    "cache_hit",
    "succeeded",
]
VerifierHeadStatus = Literal["skipped", "succeeded", "failed"]
MergeVerdict = Literal[
    "mergeable",
    "human_review_required",
    "insufficient_evidence",
    "blocked",
    "unknown",
]
CapabilityChangeBucket = Literal["added", "modified", "removed"]
CapabilityReleaseImpact = Literal[
    "blocks_release",
    "review_required",
    "insufficient_evidence",
    "informational",
    "none",
]

_DECISION_TO_VERDICT: dict[str, MergeVerdict] = {
    "passed": "mergeable",
    "review_required": "human_review_required",
    "insufficient_evidence": "insufficient_evidence",
    "blocked": "blocked",
}


def map_merge_verdict(decision: str | None) -> MergeVerdict:
    """Project ``release_decision.decision`` onto a merge verdict."""
    if decision is None:
        return "unknown"
    return _DECISION_TO_VERDICT.get(decision, "human_review_required")


class VerifierNextAction(BaseModel):
    """Single recommended next step after verify."""

    model_config = ConfigDict(extra="forbid")

    actor: Literal["coding_agent", "human"] = "human"
    kind: str = "review"
    command: str | None = None
    why: str = ""


class VerifierHumanReview(BaseModel):
    """Whether a human must review before merge, and why."""

    model_config = ConfigDict(extra="forbid")

    required: bool = False
    why: str | None = None


class VerifierCapabilityChange(BaseModel):
    """One reviewer-facing capability change projected for verifier output."""

    model_config = ConfigDict(extra="forbid")

    id: str
    change_type: str
    change_bucket: CapabilityChangeBucket
    subject_kind: str
    subject: str
    impact: CapabilityReleaseImpact = "informational"
    rationale: str
    source_path: str | None = None
    source_start_line: int | None = None
    related_finding_ids: list[str] = Field(default_factory=list)


class VerifierCapabilityReview(BaseModel):
    """Derived capability-review rollup for PR comments and Action outputs.

    This is a projection only. It never gates independently of
    ``report.json.release_decision.decision``.
    """

    model_config = ConfigDict(extra="forbid")

    added: int = 0
    modified: int = 0
    removed: int = 0
    trust_root_touched: bool = False
    policy_weakened: bool = False
    top_changes: list[VerifierCapabilityChange] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class VerifierArtifact(BaseModel):
    """Machine-readable artifact emitted by ``agents-shipgate verify``.

    This is an orchestration record only. The release gate remains
    ``report.json.release_decision.decision`` from the head scan.
    """

    model_config = ConfigDict(extra="forbid")

    verifier_schema_version: Literal["0.1"] = "0.1"
    workspace: str
    config: str
    base_ref: str | None = None
    head_ref: str = "HEAD"
    changed_files: list[str] = Field(default_factory=list)
    diff_text_available: bool = False
    trigger: dict[str, Any] = Field(default_factory=dict)
    base_status: VerifierBaseStatus = "not_requested"
    base_tree_sha: str | None = None
    base_report_json: str | None = None
    base_notes: list[str] = Field(default_factory=list)
    head_status: VerifierHeadStatus = "skipped"
    head_report_json: str | None = None
    head_exit_code: int = 0
    release_decision: dict[str, Any] | None = None
    agent_summary: dict[str, Any] | None = None
    reviewer_summary: dict[str, Any] | None = None
    capability_review: VerifierCapabilityReview = Field(
        default_factory=VerifierCapabilityReview
    )
    mode: str = "advisory"
    decision: str | None = None
    merge_verdict: MergeVerdict = "unknown"
    can_merge_without_human: bool = False
    headline: str | None = None
    human_review: VerifierHumanReview | None = None
    first_next_action: VerifierNextAction | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "CapabilityChangeBucket",
    "CapabilityReleaseImpact",
    "MergeVerdict",
    "VerifierArtifact",
    "VerifierBaseStatus",
    "VerifierCapabilityChange",
    "VerifierCapabilityReview",
    "VerifierHeadStatus",
    "VerifierHumanReview",
    "VerifierNextAction",
    "map_merge_verdict",
]
