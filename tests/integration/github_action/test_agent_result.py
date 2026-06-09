from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agents_shipgate.ci.agent_result import build_agent_result, write_agent_result
from agents_shipgate.report.pr_comment import render_pr_comment
from agents_shipgate.report.sarif import render_sarif_report
from agents_shipgate.schemas.capability_change import EffectivePolicy
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.report import (
    BaselineDelta,
    ContributionRule,
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
    VerifierFixTask,
)
from scripts.github_action_outputs import decision_policy_exit_code, extract_outputs


def test_blocked_mcp_expansion_writes_agent_result_and_can_fail_required_check(tmp_path: Path):
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

    result = build_agent_result(verifier=verifier, report=report)
    write_agent_result(result, tmp_path / "agent-result.json")
    reread = json.loads((tmp_path / "agent-result.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "docs/agent-result-schema.v1.json")
        .read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(reread)
    assert reread["schema_version"] == "agent_result_v1"
    assert reread["agent"] == "codex"
    assert reread["decision"] == "block"
    assert reread["risk_level"] == "critical"
    assert reread["affected_files"][0]["path"] == ".codex/config.toml"
    assert reread["affected_files"][0]["start_line"] == 12
    assert reread["required_reviewers"] == ["agent-platform", "security"]
    assert decision_policy_exit_code("block", "block") == 20
    assert decision_policy_exit_code("require_review", "block") == 0


def test_reviewer_routing_does_not_match_ci_inside_words():
    item = ReleaseDecisionItem(
        id="F-ci-substring",
        fingerprint="fp_ci_substring",
        check_id="SHIP-VERIFY-EVIDENCE-GAP",
        title="Insufficient evidence for a specific release decision",
        severity="medium",
        source=SourceReference(type="manifest", path="shipgate.yaml", start_line=4),
    )
    report = _report(
        _release_decision("review_required", review_items=[item]),
        run_id="agents_shipgate_ci_substring",
    )
    verifier = _verifier(report, merge_verdict="human_review_required")

    result = build_agent_result(verifier=verifier, report=report)

    assert result.required_reviewers == ["release-owner"]


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

    result = build_agent_result(verifier=verifier, report=report)
    comment = render_pr_comment(verifier, report=report, agent_result=result)

    assert result.decision == "require_review"
    assert result.required_reviewers == ["agent-platform"]
    assert "Required reviewers: `agent-platform`" in comment
    assert decision_policy_exit_code("require_review", "block,require_review") == 20
    assert decision_policy_exit_code("require_review", "block") == 0


def test_allow_comment_is_concise_and_has_no_contradictory_decision():
    report = _report(_release_decision("passed"), run_id="agents_shipgate_allow")
    verifier = _verifier(report, merge_verdict="mergeable", can_merge=True)

    result = build_agent_result(verifier=verifier, report=report)
    comment = render_pr_comment(verifier, report=report, agent_result=result)

    assert result.decision == "allow"
    assert result.risk_level == "none"
    assert result.required_reviewers == []
    assert "Decision: `allow`" in comment
    assert "Release gate: `passed`" in comment
    assert "Decision: `passed`" not in comment
    assert "Required reviewers:" not in comment


def test_warn_only_for_review_tier_advisory_and_maps_to_low_risk():
    low_finding = Finding(
        check_id="SHIP-LOW",
        title="Low advisory",
        severity="low",
        category="policy",
        recommendation="Read it.",
        fingerprint="fp_low",
    )
    medium_finding = Finding(
        check_id="SHIP-MEDIUM",
        title="Medium advisory",
        severity="medium",
        category="policy",
        recommendation="Review it.",
        fingerprint="fp_medium",
    )
    low_report = _report(
        _release_decision(
            "passed",
            contribution_rules=[
                ContributionRule(
                    finding_id="fp_low",
                    fingerprint="fp_low",
                    check_id="SHIP-LOW",
                    category="excluded",
                    rule="sub_threshold",
                    rationale="below threshold",
                )
            ],
        ),
        run_id="agents_shipgate_low_advisory",
        findings=[low_finding],
    )
    medium_report = _report(
        _release_decision(
            "passed",
            contribution_rules=[
                ContributionRule(
                    finding_id="fp_medium",
                    fingerprint="fp_medium",
                    check_id="SHIP-MEDIUM",
                    category="excluded",
                    rule="sub_threshold",
                    rationale="below threshold",
                )
            ],
        ),
        run_id="agents_shipgate_medium_advisory",
        findings=[medium_finding],
    )

    low = build_agent_result(
        verifier=_verifier(low_report, merge_verdict="mergeable", can_merge=True),
        report=low_report,
    )
    medium = build_agent_result(
        verifier=_verifier(medium_report, merge_verdict="mergeable", can_merge=True),
        report=medium_report,
    )

    assert low.decision == "allow"
    assert low.risk_level == "none"
    assert medium.decision == "warn"
    assert medium.risk_level == "low"
    assert [rule.check_id for rule in medium.violated_rules] == ["SHIP-MEDIUM"]


def test_agent_repair_instructions_include_forbidden_shortcuts():
    item = ReleaseDecisionItem(
        id="F3",
        fingerprint="fp_block",
        check_id="SHIP-ACTION-POLICY-VIOLATION",
        title="Action policy failed",
        severity="critical",
        blocks_release=True,
    )
    report = _report(_release_decision("blocked", blockers=[item]), run_id="repair")
    verifier = _verifier(
        report,
        merge_verdict="blocked",
        fix_task=VerifierFixTask(
            actor="human",
            safe_to_attempt=False,
            instructions=["Review the blocker."],
            forbidden_shortcuts=[
                "Do not suppress the finding.",
                "Do not lower severity.",
            ],
        ),
    )

    result = build_agent_result(verifier=verifier, report=report)

    assert "Do not suppress the finding." in result.agent_repair_instructions
    assert "Do not lower severity." in result.agent_repair_instructions


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


def test_action_output_extraction_preserves_existing_fields_and_adds_agent_result(
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
            "trigger": {"should_run": True, "matched_rules": [{"id": "manifest"}]},
        },
    )
    _write_json(
        output_dir / "agent-result.json",
        {
            "decision": "allow",
            "risk_level": "low",
            "audit_id": "sg_audit_test",
            "required_reviewers": [],
            "policy_snapshot_sha256": "a" * 64,
        },
    )

    outputs = extract_outputs(output_dir)

    assert outputs["status"] == "clean"
    assert outputs["decision"] == "passed"
    assert outputs["report_json"] == output_dir / "report.json"
    assert outputs["agent_result_json"] == output_dir / "agent-result.json"
    assert outputs["agent_decision"] == "allow"
    assert outputs["risk_level"] == "low"
    assert outputs["audit_id"] == "sg_audit_test"
    assert outputs["policy_snapshot_sha256"] == "a" * 64


def _report(
    decision: ReleaseDecision,
    *,
    run_id: str,
    findings: list[Finding] | None = None,
) -> ReadinessReport:
    return ReadinessReport(
        run_id=run_id,
        project={"name": "project"},
        agent={"name": "codex"},
        environment={"target": "local"},
        summary=ReportSummary(status="warnings_detected"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=findings or [],
        release_decision=decision,
        effective_policy=EffectivePolicy(ci_mode="advisory", fail_on=[]),
    )


def _release_decision(
    decision: str,
    *,
    blockers: list[ReleaseDecisionItem] | None = None,
    review_items: list[ReleaseDecisionItem] | None = None,
    contribution_rules: list[ContributionRule] | None = None,
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
        contribution_rules=contribution_rules or [],
    )


def _verifier(
    report: ReadinessReport,
    *,
    merge_verdict: str,
    changed_files: list[str] | None = None,
    capability_review: VerifierCapabilityReview | None = None,
    fix_task: VerifierFixTask | None = None,
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
        fix_task=fix_task,
        capability_review=capability_review or VerifierCapabilityReview(),
        artifacts={
            "report_json": "agents-shipgate-reports/report.json",
            "report_sarif": "agents-shipgate-reports/report.sarif",
            "verifier_json": "agents-shipgate-reports/verifier.json",
            "agent_result_json": "agents-shipgate-reports/agent-result.json",
            "pr_comment": "agents-shipgate-reports/pr-comment.md",
        },
    )


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
