from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.common import ReleaseDecisionStatus

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

# The projection from the canonical release verdict (``ReleaseDecisionStatus``,
# the ONE thing ``build_release_decision`` computes) onto the agent-facing
# ``MergeVerdict``. Keyed with ``ReleaseDecisionStatus`` so a key that is not a
# real release status is a type error, and covered by a totality test
# (tests/test_verdict_contract.py) so adding a release status without a mapping
# fails CI rather than silently falling back. This dict is the only bridge
# between the two vocabularies.
_DECISION_TO_VERDICT: dict[ReleaseDecisionStatus, MergeVerdict] = {
    "passed": "mergeable",
    "review_required": "human_review_required",
    "insufficient_evidence": "insufficient_evidence",
    "blocked": "blocked",
}


def map_merge_verdict(decision: str | None) -> MergeVerdict:
    """Project ``release_decision.decision`` onto a merge verdict.

    ``None`` (no head scan / no decision) is ``unknown``. A decision string
    outside the canonical vocabulary fails safe to ``human_review_required``
    rather than ``mergeable`` — an unrecognized verdict must never auto-pass.
    """
    if decision is None:
        return "unknown"
    return _DECISION_TO_VERDICT.get(decision, "human_review_required")  # type: ignore[arg-type]


def merge_verdict_for(*, decision: str | None, head_status: str) -> MergeVerdict:
    """Single authority for deriving a ``MergeVerdict`` for a verify run.

    When the head scan produced a ``release_decision`` the verdict is a pure
    projection of it (``map_merge_verdict``). With no decision the verdict
    reflects *why*: a skipped head (Shipgate had nothing to gate) is
    ``mergeable``; any other no-decision state (scan failed, or not yet run)
    is ``unknown``. Centralized here so the orchestrator — or any future
    caller — cannot invent a second, inconsistent rule.
    """
    if decision is not None:
        return map_merge_verdict(decision)
    return "mergeable" if head_status == "skipped" else "unknown"


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

    @model_validator(mode="after")
    def _verdict_projects_release_decision(self) -> VerifierArtifact:
        """Lock the one-decision-engine contract structurally.

        Whenever a head ``release_decision`` is present, the agent-facing
        ``merge_verdict`` and the convenience ``decision`` copy MUST be exact
        projections of it — never an independently computed second opinion.
        Construction-time enforcement makes an inconsistent artifact
        impossible to emit. (No release_decision — skipped / failed / preview
        — is left unconstrained: there is no substrate to project.)
        """
        if self.release_decision is None:
            return self
        substrate = self.release_decision.get("decision")
        if self.decision != substrate:
            raise ValueError(
                "VerifierArtifact.decision must equal "
                "release_decision['decision'] (one decision engine): "
                f"{self.decision!r} != {substrate!r}"
            )
        expected = map_merge_verdict(substrate)
        if self.merge_verdict != expected:
            raise ValueError(
                "VerifierArtifact.merge_verdict must be the projection of "
                f"release_decision['decision']={substrate!r} via "
                f"map_merge_verdict (expected {expected!r}, got "
                f"{self.merge_verdict!r})"
            )
        return self


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
    "merge_verdict_for",
]
