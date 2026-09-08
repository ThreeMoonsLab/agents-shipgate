"""Project one complete-evidence human review request from the verifier graph."""

from __future__ import annotations

import re

from agents_shipgate.schemas.human_authorization import authorization_review_items, review_set_id
from agents_shipgate.schemas.human_review_request import (
    HumanReviewQuestionV1,
    HumanReviewRequestV1,
    build_human_review_request,
)
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.verification_identity import VerificationPlan
from agents_shipgate.schemas.verifier import VerifierArtifact


def project_human_review_request(
    *, verifier: VerifierArtifact, report: ReadinessReport | None, plan: VerificationPlan
) -> HumanReviewRequestV1 | None:
    """Admit a documentation-quality question only, without altering authority.

    The existing committed docs.lookup fixture makes this lifecycle reachable.
    It is a bounded contract fixture, not evidence that documentation warnings
    are the product wedge. Every other class keeps its existing human route.
    """

    if (
        report is None
        or verifier.execution != "succeeded"
        or verifier.decision != "review_required"
        or verifier.merge_verdict != "human_review_required"
        or verifier.can_merge_without_human
        or verifier.request_id != plan.request_id
        or verifier.subject_id != plan.subject.subject_id
        or verifier.input_set_id != plan.inputs.input_set_id
        or verifier.engine_requirement_id != plan.engine.engine_requirement_id
        or plan.inputs.options.get("plugins_enabled") is not False
        or plan.inputs.options.get("manifest_provenance") != "repository"
    ):
        return None
    git = plan.subject.git
    if (
        git.snapshot_kind != "committed_tree"
        or git.worktree_overlay_sha256 is not None
        or not all((git.base_commit_sha, git.merge_base_sha, git.base_tree_sha,
                    git.head_tree_sha, git.source_head_commit_sha))
    ):
        return None
    decision = report.release_decision
    if decision is None or decision.decision != "review_required" or decision.blockers:
        return None
    rows = decision.review_items
    if not rows or any(
        row.check_id != "SHIP-DOC-MISSING-DESCRIPTION"
        or row.severity != "medium"
        or row.blocks_release
        or row.baseline_status is not None
        for row in rows
    ):
        return None
    coverage = decision.evidence_coverage
    if coverage is None:
        return None
    # A known binding is not a complete leaf inventory. Every independent
    # evidence dimension must be complete before this narrow route is offered.
    data = coverage.model_dump(mode="json")
    semantic = data.get("semantic_coverage") or {}
    identity = data.get("identity_coverage") or {}
    binding = data.get("binding_coverage") or {}
    if (
        data.get("source_warning_count") != 0
        or data.get("low_confidence_tool_count") != 0
        or data.get("evidence_gaps")
        or data.get("policy_gap_count") != 0
        or not semantic.get("total_actions")
        or semantic.get("pass_eligible_actions") != semantic.get("total_actions")
        or semantic.get("gap_count") != 0
        or semantic.get("review_concern_count") != 0
        or semantic.get("acknowledged_overrides")
        or not identity.get("canonical_tools")
        or identity.get("pass_eligible_tools") != identity.get("canonical_tools")
        or identity.get("gap_count") != 0
        or identity.get("ambiguous_name_count") != 0
        or binding.get("pass_eligible") is not True
        or binding.get("gap_count") != 0
    ):
        return None
    items = authorization_review_items(decision.model_dump(mode="json"))
    titles = {row.id: row.title for row in rows}
    questions = [
        HumanReviewQuestionV1(
            review_item_id=item.review_item_id,
            question=(
                "Do you accept this documentation-quality concern for the reviewed change, "
                "reject it, or dispute the finding? "
                + re.sub(r"[\x00-\x1f\x7f]", " ", titles[item.review_item_id])[:900]
            ),
        )
        for item in items
    ]
    return build_human_review_request(
        repository_id=git.repository_id,
        verification_request_id=plan.request_id,
        subject_id=plan.subject.subject_id,
        input_set_id=plan.inputs.input_set_id,
        engine_requirement_id=plan.engine.engine_requirement_id,
        decision_id=verifier.decision_id,
        base_commit_sha=git.base_commit_sha,
        merge_base_sha=git.merge_base_sha,
        base_tree_sha=git.base_tree_sha,
        head_tree_sha=git.head_tree_sha,
        source_head_commit_sha=git.source_head_commit_sha,
        review_items=items,
        review_set_id=review_set_id(items),
        questions=questions,
    )
