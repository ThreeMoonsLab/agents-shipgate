from __future__ import annotations

import json
from pathlib import Path

from agents_shipgate.report.pr_comment import render_pr_comment
from agents_shipgate.report.sarif import render_sarif_report
from agents_shipgate.schemas.capability_change import EffectivePolicy
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    Finding,
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
    ReportSummary,
    ToolSurfaceSummary,
)
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierCapabilityReview,
    VerifierHumanReview,
)
from scripts.github_action_outputs import extract_outputs, merge_verdict_policy_exit_code


def test_blocked_mcp_expansion_can_fail_required_check():
    item = ReleaseDecisionItem(
        id="F1",
        fingerprint="fp_mcp",
        check_id="SHIP-ACTION-POLICY-VIOLATION",
        title="MCP expansion auto-approves destructive repository tools",
        severity="critical",
        blocks_release=True,
        source=SourceReference(
            type="codex_config",
            path=".codex/config.toml",
            start_line=12,
        ),
    )
    report = _report(
        _release_decision("blocked", blockers=[item], exit_code=20),
        run_id="agents_shipgate_blocked",
    )
    verifier = _verifier(report, merge_verdict="blocked", changed_files=[".codex/config.toml"])

    assert verifier.merge_verdict == "blocked"
    assert verifier.can_merge_without_human is False
    assert merge_verdict_policy_exit_code("blocked", "blocked") == 20
    assert merge_verdict_policy_exit_code("human_review_required", "blocked") == 0


def test_require_review_trust_root_change_posts_reviewer_list():
    item = ReleaseDecisionItem(
        id="F2",
        fingerprint="fp_trust",
        check_id="SHIP-VERIFY-TRUST-ROOT-TOUCHED",
        title="Trust-root file changed",
        severity="high",
        source=SourceReference(type="git_diff", path="AGENTS.md", start_line=1),
    )
    report = _report(
        _release_decision("review_required", review_items=[item]),
        run_id="agents_shipgate_review",
    )
    verifier = _verifier(
        report,
        merge_verdict="human_review_required",
        changed_files=["AGENTS.md"],
        capability_review=VerifierCapabilityReview(trust_root_touched=True),
    )

    comment = render_pr_comment(verifier, report=report)

    assert "Merge verdict: `human_review_required`" in comment
    assert "Human review:" in comment
    assert merge_verdict_policy_exit_code(
        "human_review_required", "blocked,human_review_required"
    ) == 20
    assert merge_verdict_policy_exit_code("human_review_required", "blocked") == 0


def test_allow_comment_is_concise_and_has_no_contradictory_decision():
    report = _report(_release_decision("passed"), run_id="agents_shipgate_allow")
    verifier = _verifier(report, merge_verdict="mergeable", can_merge=True)

    comment = render_pr_comment(verifier, report=report)

    assert "Merge verdict: `mergeable`" in comment
    assert "Can merge without human: `true`" in comment
    assert "Release gate: `passed`" in comment
    assert "Decision: `passed`" not in comment
    assert "Required reviewers:" not in comment


def test_sarif_uses_policy_rule_id_and_preserves_check_id_and_location():
    report = ReadinessReport(
        run_id="sarif",
        project={"name": "project"},
        agent={"name": "agent"},
        environment={"target": "local"},
        summary=ReportSummary(status="warnings_detected", medium_count=1),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=[
            Finding(
                check_id="SHIP-ACTION-POLICY-VIOLATION",
                title="Action policy failed",
                severity="medium",
                category="action_surface",
                evidence={"policy_id": "require-audit-for-external-communication"},
                recommendation="Declare audit evidence.",
                source=SourceReference(
                    type="manifest",
                    path="shipgate.yaml",
                    start_line=42,
                ),
            )
        ],
    )

    sarif = render_sarif_report(report)
    result = sarif["runs"][0]["results"][0]
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]

    assert result["ruleId"] == "require-audit-for-external-communication"
    assert result["properties"]["check_id"] == "SHIP-ACTION-POLICY-VIOLATION"
    assert rule["id"] == "require-audit-for-external-communication"
    assert rule["properties"]["check_id"] == "SHIP-ACTION-POLICY-VIOLATION"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "shipgate.yaml"
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 42


def test_action_output_extraction_preserves_existing_fields_and_adds_verify_run(
    tmp_path: Path,
):
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    _write_json(
        output_dir / "report.json",
        {
            "summary": {
                "status": "clean",
                "critical_count": 0,
                "high_count": 0,
                "medium_count": 0,
            },
            "release_decision": {
                "decision": "passed",
                "blockers": [],
                "review_items": [],
                "fail_policy": {"would_fail_ci": False, "exit_code": 0},
            },
        },
    )
    _write_json(
        output_dir / "verifier.json",
        {
            "head_status": "succeeded",
            "merge_verdict": "mergeable",
            "can_merge_without_human": True,
            "agent_controller": {
                "must_stop": False,
                "stop_reason": None,
                "completion_allowed": True,
            },
            "trigger": {"should_run": True, "matched_rules": [{"id": "manifest"}]},
        },
    )
    _write_json(output_dir / "verify-run.json", {"run_id": "sha256:integration"})

    outputs = extract_outputs(output_dir)

    assert outputs["status"] == "clean"
    assert outputs["decision"] == "passed"
    assert outputs["report_json"] == output_dir / "report.json"
    assert outputs["verify_run_json"] == output_dir / "verify-run.json"
    assert outputs["run_id"] == "sha256:integration"
    assert outputs["agent_controller_completion_allowed"] == "true"


def _report(decision: ReleaseDecision, *, run_id: str) -> ReadinessReport:
    return ReadinessReport(
        run_id=run_id,
        project={"name": "project"},
        agent={"name": "codex"},
        environment={"target": "local"},
        summary=ReportSummary(status="warnings_detected"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        release_decision=decision,
        effective_policy=EffectivePolicy(ci_mode="advisory", fail_on=[]),
    )


def _release_decision(
    decision: str,
    *,
    blockers: list[ReleaseDecisionItem] | None = None,
    review_items: list[ReleaseDecisionItem] | None = None,
    exit_code: int = 0,
) -> ReleaseDecision:
    return ReleaseDecision(
        decision=decision,  # type: ignore[arg-type]
        reason=f"Release decision is {decision}.",
        blockers=blockers or [],
        review_items=review_items or [],
        evidence_coverage=EvidenceCoverageDecision(
            level="full",
            human_review_recommended=decision != "passed",
            source_warning_count=0,
            low_confidence_tool_count=0,
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(
            ci_mode="advisory",
            fail_on=[],
            new_findings_only=False,
            would_fail_ci=exit_code != 0,
            exit_code=exit_code,
        ),
    )


def _verifier(
    report: ReadinessReport,
    *,
    merge_verdict: str,
    changed_files: list[str] | None = None,
    capability_review: VerifierCapabilityReview | None = None,
    can_merge: bool = False,
) -> VerifierArtifact:
    assert report.release_decision is not None
    return VerifierArtifact(
        workspace="/tmp/workspace",
        config="shipgate.yaml",
        base_ref="origin/main",
        head_ref="HEAD",
        changed_files=changed_files or [],
        head_status="succeeded",
        head_exit_code=report.release_decision.fail_policy.exit_code,
        release_decision=report.release_decision.model_dump(mode="json"),
        decision=report.release_decision.decision,
        merge_verdict=merge_verdict,  # type: ignore[arg-type]
        can_merge_without_human=can_merge,
        human_review=VerifierHumanReview(
            required=report.release_decision.decision != "passed",
            why=report.release_decision.reason
            if report.release_decision.decision != "passed"
            else None,
        ),
        capability_review=capability_review or VerifierCapabilityReview(),
        artifacts={
            "report_json": "agents-shipgate-reports/report.json",
            "report_sarif": "agents-shipgate-reports/report.sarif",
            "verifier_json": "agents-shipgate-reports/verifier.json",
            "verify_run_json": "agents-shipgate-reports/verify-run.json",
            "pr_comment": "agents-shipgate-reports/pr-comment.md",
        },
    )


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
