from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.report import CapabilityChange

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

# v0.1 (additive): the merge-oriented projection of the release gate. This is
# NOT a second decision engine — every value below is a deterministic read of
# ``release_decision.decision`` (the source of truth). ``unknown`` models the
# case where no decision exists (a head scan that failed before producing one).
MergeVerdict = Literal[
    "mergeable",
    "human_review_required",
    "insufficient_evidence",
    "blocked",
    "unknown",
]

# release_decision.decision -> merge_verdict. Unrecognized future decisions
# map defensively to human_review_required; a missing decision is the caller's
# concern (see _merge_verdict in the orchestrator), not this table.
_DECISION_TO_VERDICT: dict[str, MergeVerdict] = {
    "passed": "mergeable",
    "review_required": "human_review_required",
    "insufficient_evidence": "insufficient_evidence",
    "blocked": "blocked",
}


def map_merge_verdict(decision: str | None) -> MergeVerdict:
    """Project ``release_decision.decision`` onto a merge verdict.

    ``None`` (no decision was produced) -> ``unknown``. A recognized
    decision maps per :data:`_DECISION_TO_VERDICT`; any unrecognized
    future decision is treated conservatively as human review required.
    """
    if decision is None:
        return "unknown"
    return _DECISION_TO_VERDICT.get(decision, "human_review_required")


class VerifierNextAction(BaseModel):
    """The single recommended next step after verify, with the actor who
    should take it. ``actor`` distinguishes work a coding agent may do
    mechanically from decisions that require a human."""

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


class VerifierArtifact(BaseModel):
    """Machine-readable artifact emitted by ``agents-shipgate verify``.

    Carries an orchestration record (workspace/base/head/status) AND a
    merge-oriented projection layer (``merge_verdict``,
    ``can_merge_without_human``, ``capability_changes`` …). The projection
    layer is the coding-agent surface: read it to answer "can this PR
    merge?". It never disagrees with the gate — every projected field is a
    deterministic read of ``report.json.release_decision.decision``, which
    remains the source of truth.
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
    # --- v0.1 additive: merge-decision projection layer --------------------
    # All defaulted so the skip path and direct test constructors keep
    # working; all are pure projections of release_decision.
    mode: str = "advisory"
    decision: str | None = None
    merge_verdict: MergeVerdict = "unknown"
    can_merge_without_human: bool = False
    headline: str | None = None
    human_review: VerifierHumanReview | None = None
    first_next_action: VerifierNextAction | None = None
    trust_root_touched: bool = False
    capability_changes: list[CapabilityChange] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "MergeVerdict",
    "VerifierArtifact",
    "VerifierBaseStatus",
    "VerifierHeadStatus",
    "VerifierHumanReview",
    "VerifierNextAction",
    "map_merge_verdict",
]
