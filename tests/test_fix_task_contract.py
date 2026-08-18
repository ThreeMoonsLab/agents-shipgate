"""PR-B contract: deterministic fix_task routing (mechanical vs authority).

`fix_task` is the single repair instruction a verify run hands to whoever
acts next. Its routing is a pure projection of the head scan — never a model
judgment — and the split is the product's core safety boundary: a coding
agent may fix mechanical gaps, but an authority gap (approval/idempotency
evidence it cannot prove, a weakened policy, a touched trust root, degraded
evidence) must route to a human so the agent cannot invent its way to green.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents_shipgate.cli.verify.fix_task import build_fix_task
from agents_shipgate.cli.verify.orchestrator import (
    _derive_verifier_control,
    _verifier_headline,
)
from agents_shipgate.schemas.patches import AppendPointerPatch
from agents_shipgate.schemas.report import (
    BaselineDelta,
    EvidenceCoverageDecision,
    EvidenceGap,
    EvidenceGapAction,
    FailPolicy,
    Finding,
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
    ReportSummary,
    SemanticCoverageDecision,
    ToolSurfaceSummary,
)
from agents_shipgate.schemas.verifier import (
    VerifierCapabilityReview,
    VerifierDiffStatus,
    VerifierFixTask,
    map_merge_verdict,
)


def _finding(
    fid: str,
    *,
    requires_human_review: bool,
    autofix_safe: bool,
    recommendation: str = "Add the missing control.",
    severity: str = "medium",
    blocks_release: bool = False,
) -> Finding:
    return Finding(
        id=fid,
        check_id="SHIP-TEST",
        title=f"finding {fid}",
        severity=severity,  # type: ignore[arg-type]
        category="action_surface",
        evidence={},
        recommendation=recommendation,
        blocks_release=blocks_release,
        requires_human_review=requires_human_review,
        autofix_safe=autofix_safe,
    )


def _with_applicable_patch(finding: Finding) -> Finding:
    """Make an autofix-safe finding genuinely selectable by apply-patches."""

    finding.patches = [
        AppendPointerPatch(
            target_file="/abs/shipgate.yaml",
            pointer=f"/checks/{finding.id}",
            value="owner",
            target_format="yaml",
            confidence="high",
            rationale=f"Apply {finding.id}.",
            target_sha256="abc123",
        )
    ]
    return finding


def _item(finding: Finding) -> ReleaseDecisionItem:
    return ReleaseDecisionItem(
        id=finding.id,
        check_id=finding.check_id,
        severity=finding.severity,
        title=finding.title,
    )


def _report(
    *,
    decision: str,
    findings,
    blockers=(),
    review_items=(),
    low_confidence_tool_count: int = 0,
    source_warning_count: int = 0,
    tool_inventory=None,
    evidence_gaps=(),
    semantic_gap_count: int = 0,
) -> ReadinessReport:
    report = ReadinessReport(
        run_id="r",
        project={"name": "p"},
        agent={"name": "a"},
        environment={"target": "local"},
        summary=ReportSummary(status="clean"),
        release_decision=ReleaseDecision(
            decision=decision,  # type: ignore[arg-type]
            reason="2 findings require review.",
            blockers=[_item(f) for f in blockers],
            review_items=[_item(f) for f in review_items],
            evidence_coverage=EvidenceCoverageDecision(
                level="static",
                human_review_recommended=False,
                source_warning_count=source_warning_count,
                low_confidence_tool_count=low_confidence_tool_count,
                evidence_gaps=list(evidence_gaps),
                semantic_coverage=SemanticCoverageDecision(
                    total_actions=semantic_gap_count,
                    gap_count=semantic_gap_count,
                    reason_counts=(
                        {"missing_authority_evidence": semantic_gap_count}
                        if semantic_gap_count
                        else {}
                    ),
                ),
            ),
            baseline_delta=BaselineDelta(enabled=False),
            fail_policy=FailPolicy(
                ci_mode="advisory",
                fail_on=["critical"],
                new_findings_only=False,
                would_fail_ci=False,
                exit_code=0,
            ),
        ),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=list(findings),
    )
    if tool_inventory is not None:
        report.tool_inventory = list(tool_inventory)
    return report


def _review(*, policy_weakened=False, trust_root_touched=False) -> VerifierCapabilityReview:
    return VerifierCapabilityReview(
        policy_weakened=policy_weakened, trust_root_touched=trust_root_touched
    )


def _fix_task(report, *, capability_review=None, base_ref="origin/main", head_ref="HEAD"):
    decision = report.release_decision.decision
    return build_fix_task(
        report,
        merge_verdict=map_merge_verdict(decision),
        capability_review=capability_review or _review(),
        base_ref=base_ref,
        head_ref=head_ref,
        worktree=True,
    )


def _control_for_task(task: VerifierFixTask, *, merge_verdict: str):
    return _derive_verifier_control(
        execution="succeeded",
        merge_verdict=merge_verdict,  # type: ignore[arg-type]
        release_decision=None,
        fix_task=task,
        capability_review=_review(),
        headline="Fix-task routing.",
        first_next_action_override=None,
        base_status="succeeded",
        base_ref="origin/main",
        diff_status=VerifierDiffStatus(completeness="complete"),
    )


# --- Routing ----------------------------------------------------------------


def test_mergeable_has_no_fix_task() -> None:
    report = _report(decision="passed", findings=[])
    assert (
        build_fix_task(
            report,
            merge_verdict="mergeable",
            capability_review=_review(),
            base_ref="origin/main",
            head_ref="HEAD",
        )
        is None
    )


def test_semantic_gap_routes_human_with_structured_declaration_repair() -> None:
    gap = EvidenceGap(
        kind="missing_authority_evidence",
        subject="process_order",
        source_type="mcp",
        source_ref="/tools/0",
        why="No explicit authority evidence was found.",
        next_action=EvidenceGapAction(
            kind="declare_action_authority",
            command="agents-shipgate verify --workspace . --config shipgate.yaml",
            path="shipgate.yaml#action_surface.actions",
            why="A complete authority declaration is required.",
            expects="Declare a reviewed authority mode and rerun verification.",
            accepted_values=["none", "scoped", "unscoped", "ambient"],
        ),
    )
    report = _report(
        decision="insufficient_evidence",
        findings=[],
        evidence_gaps=[gap],
        semantic_gap_count=1,
    )

    task = _fix_task(report)

    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False
    assert gap.next_action.suggested_patch_kind == "manual"
    repair = next(
        repair for repair in task.allowed_repairs if repair.kind == "declare_action_authority"
    )
    assert "process_order" in (repair.target or "")
    assert repair.command == gap.next_action.command
    assert any("Accepted values" in instruction for instruction in task.instructions)


def test_mechanical_review_routes_to_coding_agent() -> None:
    f = _with_applicable_patch(
        _finding(
            "F1",
            requires_human_review=False,
            autofix_safe=True,
            recommendation="Add an owner field from CODEOWNERS.",
        )
    )
    task = _fix_task(_report(decision="review_required", findings=[f], review_items=[f]))
    assert task is not None
    assert task.actor == "coding_agent"
    assert task.safe_to_attempt is True
    assert "Add an owner field from CODEOWNERS." in task.instructions


def test_authority_review_routes_to_human() -> None:
    f = _finding("F1", requires_human_review=True, autofix_safe=False)
    task = _fix_task(_report(decision="review_required", findings=[f], review_items=[f]))
    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False


def test_blocked_but_mechanical_routes_by_autofix_not_verdict() -> None:
    # Routing is by an exact applicable patch, not the verdict label alone.
    f = _with_applicable_patch(
        _finding(
            "F1",
            requires_human_review=False,
            autofix_safe=True,
            severity="critical",
            blocks_release=True,
        )
    )
    task = _fix_task(_report(decision="blocked", findings=[f], blockers=[f]))
    assert task is not None
    assert task.actor == "coding_agent"


def test_policy_weakened_forces_human_even_when_mechanical() -> None:
    f = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    task = _fix_task(
        _report(decision="review_required", findings=[f], review_items=[f]),
        capability_review=_review(policy_weakened=True),
    )
    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False
    assert any("self-approve" in line for line in task.instructions)


def test_trust_root_touched_forces_human_even_when_mechanical() -> None:
    f = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    task = _fix_task(
        _report(decision="review_required", findings=[f], review_items=[f]),
        capability_review=_review(trust_root_touched=True),
    )
    assert task is not None
    assert task.actor == "human"


def test_insufficient_evidence_forces_human() -> None:
    task = _fix_task(_report(decision="insufficient_evidence", findings=[]))
    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False


def test_degraded_evidence_under_review_required_forces_human() -> None:
    # v0.27 regression guard: an active high finding elevates a degraded-evidence
    # case from insufficient_evidence to review_required. The finding here is
    # mechanically fixable (autofix_safe, no human review), so without an
    # evidence-aware escalation it would route to coding_agent / safe_to_attempt
    # — opening an auto-fix path on evidence too weak to gate. The fix_task must
    # still fail closed to a human because the evidence is below the IE
    # threshold (2 low-confidence tools of 2 → threshold 1).
    f = _with_applicable_patch(
        _finding(
            "F1",
            requires_human_review=False,
            autofix_safe=True,
            severity="high",
            recommendation="Add the missing scope bound.",
        )
    )
    report = _report(
        decision="review_required",
        findings=[f],
        review_items=[f],
        low_confidence_tool_count=2,
        tool_inventory=[
            {"name": "a", "source_type": "langchain", "source_ref": "t.py", "confidence": "low"},
            {"name": "b", "source_type": "langchain", "source_ref": "t.py", "confidence": "low"},
        ],
    )
    task = _fix_task(report)
    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False
    # The human still gets the concrete "make the surface enumerable" remedy,
    # even though the verdict is review_required rather than the bare IE verdict.
    joined = " ".join(task.instructions)
    assert "explicit local tool inventory" in joined


def test_review_required_with_full_evidence_stays_mechanical() -> None:
    # Counterpart guard: a mechanically-fixable high finding with HIGH-confidence
    # evidence (no gap) must still route to the coding agent — the escalation
    # fires on degraded evidence, not on severity.
    f = _with_applicable_patch(
        _finding(
            "F1",
            requires_human_review=False,
            autofix_safe=True,
            severity="high",
            recommendation="Add an owner field from CODEOWNERS.",
        )
    )
    report = _report(
        decision="review_required",
        findings=[f],
        review_items=[f],
        low_confidence_tool_count=0,
        tool_inventory=[
            {"name": "a", "source_type": "mcp", "source_ref": "m.json", "confidence": "high"},
        ],
    )
    task = _fix_task(report)
    assert task is not None
    assert task.actor == "coding_agent"
    assert task.safe_to_attempt is True


# --- Instructions / guardrails / verification -------------------------------


def test_forbidden_shortcuts_and_verification_command_present() -> None:
    f = _finding("F1", requires_human_review=True, autofix_safe=False)
    task = _fix_task(
        _report(decision="blocked", findings=[f], blockers=[f]),
        base_ref="origin/main",
        head_ref="HEAD",
    )
    assert task is not None
    assert task.verification_command == (
        "agents-shipgate verify --base origin/main --json"
    )
    assert task.forbidden_shortcuts
    assert any("suppress" in shortcut for shortcut in task.forbidden_shortcuts)


def test_instructions_are_deduped_and_capped() -> None:
    findings = [
        _finding(
            f"F{i}", requires_human_review=True, autofix_safe=False, recommendation="Same rec."
        )
        for i in range(8)
    ]
    task = _fix_task(_report(decision="blocked", findings=findings, blockers=findings))
    assert task is not None
    assert task.instructions.count("Same rec.") == 1
    assert len(task.instructions) <= 5


def test_human_allowed_repairs_reserve_terminal_verify_step() -> None:
    findings = [
        _finding(
            f"F{i}",
            requires_human_review=True,
            autofix_safe=False,
            recommendation=f"Review finding {i}.",
        )
        for i in range(12)
    ]

    task = _fix_task(_report(decision="blocked", findings=findings, blockers=findings))

    assert task is not None
    assert len(task.allowed_repairs) == 10
    assert task.allowed_repairs[-1].id == "rerun_verify_after_human_action"
    assert task.allowed_repairs[-1].command == (
        "agents-shipgate verify --base origin/main --json"
    )


def test_mechanical_allowed_repairs_reserve_terminal_verify_step() -> None:
    findings = [
        _finding(
            f"F{i}",
            requires_human_review=False,
            autofix_safe=True,
            recommendation=f"Apply patch {i}.",
        )
        for i in range(12)
    ]
    for finding in findings:
        finding.patches = [
            AppendPointerPatch(
                target_file="/abs/shipgate.yaml",
                pointer=f"/checks/{finding.id}",
                value="owner",
                target_format="yaml",
                confidence="high",
                rationale=f"Apply {finding.id}.",
                target_sha256="abc123",
            )
        ]

    task = _fix_task(_report(decision="blocked", findings=findings, blockers=findings))

    assert task is not None
    assert len(task.allowed_repairs) == 10
    assert task.allowed_repairs[-1].id == "rerun_verify"
    assert task.allowed_repairs[-1].command == (
        "agents-shipgate verify --base origin/main --json"
    )


# --- Consistency with first_next_action -------------------------------------


def test_first_next_action_actor_matches_fix_task() -> None:
    mech = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    mech_task = _fix_task(_report(decision="review_required", findings=[mech], review_items=[mech]))
    assert (
        _control_for_task(
            mech_task,
            merge_verdict="human_review_required",
        ).next_action.actor
        == "coding_agent"
    )

    auth = _finding("F2", requires_human_review=True, autofix_safe=False)
    auth_task = _fix_task(_report(decision="blocked", findings=[auth], blockers=[auth]))
    assert (
        _control_for_task(
            auth_task,
            merge_verdict="blocked",
        ).next_action.actor
        == "human"
    )


# --- VerifierFixTask schema validator (anti-reward-hacking) ------------------


def test_human_fix_task_cannot_be_agent_safe() -> None:
    with pytest.raises(ValidationError):
        VerifierFixTask(actor="human", safe_to_attempt=True)


def test_human_fix_task_unsafe_is_valid() -> None:
    VerifierFixTask(actor="human", safe_to_attempt=False)


def test_coding_agent_fix_task_safe_is_valid() -> None:
    VerifierFixTask(
        actor="coding_agent",
        safe_to_attempt=True,
        verification_command="agents-shipgate verify --base origin/main --head HEAD --json",
    )


def test_coding_agent_safe_fix_requires_exact_rerun_path_in_model_and_schema() -> None:
    payload = {"actor": "coding_agent", "safe_to_attempt": True}
    with pytest.raises(ValidationError):
        VerifierFixTask.model_validate(payload)

    from jsonschema import Draft202012Validator

    assert list(Draft202012Validator(VerifierFixTask.model_json_schema()).iter_errors(payload))


# --- Fail-closed routing (review feedback) ----------------------------------


def test_finding_with_unknown_routing_fields_fails_closed_to_human() -> None:
    # autofix_safe / requires_human_review default to None on stale, plugin, or
    # legacy findings; an unresolved finding must never be marked agent-safe.
    f = Finding(
        id="F1",
        check_id="SHIP-TEST",
        title="legacy finding",
        severity="medium",
        category="action_surface",
        evidence={},
        recommendation="Investigate.",
        blocks_release=False,
        # autofix_safe and requires_human_review left at their None defaults.
    )
    task = _fix_task(_report(decision="review_required", findings=[f], review_items=[f]))
    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False


def test_finding_autofix_false_routes_to_human() -> None:
    f = _finding("F1", requires_human_review=False, autofix_safe=False)
    task = _fix_task(_report(decision="review_required", findings=[f], review_items=[f]))
    assert task is not None
    assert task.actor == "human"


def test_unknown_verdict_without_report_emits_human_fix_task() -> None:
    # No head report (scan failed → unknown) still yields a human fix_task so
    # the contract is uniform: every non-mergeable verdict carries one.
    task = build_fix_task(
        None,
        merge_verdict="unknown",
        capability_review=None,
        base_ref="origin/main",
        head_ref="HEAD",
    )
    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False
    assert task.verification_command is not None


def test_mergeable_without_report_has_no_fix_task() -> None:
    assert (
        build_fix_task(
            None,
            merge_verdict="mergeable",
            capability_review=None,
            base_ref="origin/main",
            head_ref="HEAD",
        )
        is None
    )


def test_verification_command_quotes_shell_metacharacters() -> None:
    # ';' is a valid git ref character, so an unquoted command would be
    # injectable when an agent or human runs the suggested string.
    f = _finding("F1", requires_human_review=True, autofix_safe=False)
    task = build_fix_task(
        _report(decision="blocked", findings=[f], blockers=[f]),
        merge_verdict="blocked",
        capability_review=_review(),
        base_ref="origin/main",
        head_ref="foo;rm -rf /",
        worktree=False,
    )
    assert task is not None
    assert task.verification_command is not None
    assert "--head foo;rm" not in task.verification_command
    assert "'foo;rm -rf /'" in task.verification_command


# --- first_next_action / fix_task coherence ---------------------------------


def test_control_next_action_follows_agent_safe_fix_task() -> None:
    mech = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    task = _fix_task(_report(decision="review_required", findings=[mech], review_items=[mech]))
    control = _control_for_task(
        task,
        merge_verdict="human_review_required",
    )
    action = control.next_action
    assert action.actor == "coding_agent"
    assert action.command == task.allowed_repairs[0].command
    assert action.command != task.verification_command


def test_control_does_not_substitute_summary_commands_for_fix_task_contract() -> None:
    mech = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    task = _fix_task(_report(decision="review_required", findings=[mech], review_items=[mech]))
    control = _control_for_task(
        task,
        merge_verdict="human_review_required",
    )
    action = control.next_action
    assert action.actor == "coding_agent"
    assert action.command == task.allowed_repairs[0].command
    assert "--finding-id F1 --confidence high --apply" in (action.command or "")


def test_mechanical_task_projects_machine_patches() -> None:
    from agents_shipgate.schemas.patches import ManualPatch

    f = _finding("F1", requires_human_review=False, autofix_safe=True)
    f.patches = [
        ManualPatch(instructions="think about it"),
        AppendPointerPatch(
            target_file="/abs/shipgate.yaml",
            pointer="/permissions/scopes",
            value="payments:read",
            target_format="yaml",
            confidence="high",
            rationale="declare the missing scope",
            target_sha256="abc123",
        ),
    ]
    report = _report(decision="review_required", findings=[f], review_items=[f])

    task = build_fix_task(
        report,
        merge_verdict="human_review_required",
        capability_review=_review(),
        base_ref="origin/main",
        head_ref="HEAD",
    )

    assert task is not None and task.actor == "coding_agent"
    assert len(task.patches) == 1
    projected = task.patches[0]
    assert projected.finding_id == "F1"
    assert projected.check_id == "SHIP-TEST"
    assert projected.patch["kind"] == "append_pointer"
    assert projected.patch["pointer"] == "/permissions/scopes"


def test_human_task_carries_no_patches() -> None:
    from agents_shipgate.schemas.patches import AppendPointerPatch

    f = _finding("F1", requires_human_review=True, autofix_safe=False)
    f.patches = [
        AppendPointerPatch(
            target_file="/abs/shipgate.yaml",
            pointer="/permissions/scopes",
            value="payments:read",
            target_format="yaml",
            confidence="high",
            rationale="declare the missing scope",
            target_sha256="abc123",
        ),
    ]
    report = _report(decision="review_required", findings=[f], review_items=[f])

    task = build_fix_task(
        report,
        merge_verdict="human_review_required",
        capability_review=_review(),
        base_ref="origin/main",
        head_ref="HEAD",
    )

    assert task is not None and task.actor == "human"
    assert task.patches == []


def test_autofix_flag_without_an_applicable_patch_fails_closed_to_human() -> None:
    f = _finding("F1", requires_human_review=False, autofix_safe=True)
    report = _report(decision="review_required", findings=[f], review_items=[f])

    task = build_fix_task(
        report,
        merge_verdict="human_review_required",
        capability_review=_review(),
        base_ref="origin/main",
        head_ref="HEAD",
    )

    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False
    assert task.patches == []
    assert not any(
        repair.kind == "apply_high_confidence_patch"
        for repair in task.allowed_repairs
    )


def test_ref_bound_mechanical_finding_routes_human_without_apply_repair() -> None:
    finding = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    report = _report(
        decision="review_required",
        findings=[finding],
        review_items=[finding],
    )

    task = build_fix_task(
        report,
        merge_verdict="human_review_required",
        capability_review=_review(),
        base_ref="origin/main",
        head_ref="HEAD",
        worktree=False,
        repair_subject_available=False,
    )

    assert task is not None
    assert task.actor == "human"
    assert task.safe_to_attempt is False
    assert task.patches == []
    assert task.verification_command == (
        "agents-shipgate verify --base origin/main --head HEAD --json"
    )
    assert not any(
        repair.kind == "apply_high_confidence_patch"
        for repair in task.allowed_repairs
    )


def test_insufficient_evidence_names_low_confidence_sources() -> None:
    f = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    report = _report(decision="insufficient_evidence", findings=[f], review_items=[f])
    report.tool_inventory = [
        {
            "name": "stripe.toolkit_factory",
            "source_type": "langchain",
            "source_ref": "agent/toolkits.py",
            "confidence": "low",
        },
        {
            "name": "stripe.refund_helper",
            "source_type": "langchain",
            "source_ref": "agent/toolkits.py",
            "confidence": "medium",
        },
        {
            "name": "search.docs",
            "source_type": "mcp",
            "source_ref": "mcp-tools.json",
            "confidence": "high",
        },
    ]
    report.source_warnings = ["dynamic toolkit factory hides tool surface"]

    task = build_fix_task(
        report,
        merge_verdict="insufficient_evidence",
        capability_review=_review(),
        base_ref="origin/main",
        head_ref="HEAD",
    )

    assert task is not None and task.actor == "human"
    joined = " ".join(task.instructions)
    assert "2 tools from langchain source 'agent/toolkits.py'" in joined
    assert "tool inventory" in joined
    assert "dynamic toolkit factory hides tool surface" in joined
    # The high-confidence MCP source is not blamed.
    assert "mcp-tools.json" not in joined


def test_inventory_semantic_gap_does_not_duplicate_low_confidence_remedy() -> None:
    inventory_action = EvidenceGapAction(
        kind="declare_tool_inventory",
        path="suggested-inventory.json",
        why="The complete tool surface must be enumerable.",
        expects=(
            "Review the skeleton and reference it from "
            "`langchain.tool_inventories`."
        ),
    )
    report = _report(
        decision="insufficient_evidence",
        findings=[],
        low_confidence_tool_count=1,
        semantic_gap_count=1,
        tool_inventory=[
            {
                "name": "lookup_case",
                "source_type": "langchain_function",
                "source_ref": "agent.py",
                "confidence": "medium",
            }
        ],
        evidence_gaps=[
            EvidenceGap(
                kind="incomplete_surface",
                subject="lookup_case [langchain]",
                source_type="langchain_function",
                source_ref="agent.py",
                why="Static extraction did not prove the complete surface.",
                next_action=inventory_action,
            ),
            EvidenceGap(
                kind="low_confidence_tool",
                subject="lookup_case [langchain]",
                source_type="langchain_function",
                source_ref="agent.py",
                why="extraction_confidence=medium",
                next_action=inventory_action,
            ),
        ],
    )

    task = _fix_task(report)

    assert task is not None and task.actor == "human"
    inventory_instructions = [
        instruction
        for instruction in task.instructions
        if "langchain.tool_inventories" in instruction
    ]
    assert len(inventory_instructions) == 1
    assert not any("Review the skeleton" in item for item in task.instructions)


def test_insufficient_evidence_without_inventory_gives_generic_remedy() -> None:
    f = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    report = _report(decision="insufficient_evidence", findings=[f], review_items=[f])

    task = build_fix_task(
        report,
        merge_verdict="insufficient_evidence",
        capability_review=_review(),
        base_ref="origin/main",
        head_ref="HEAD",
    )

    assert task is not None and task.actor == "human"
    joined = " ".join(task.instructions)
    assert "explicit local tool inventory" in joined
    assert "re-run verify" in joined


# --- first adoption: the same routing, honest wording -----------------------


def _trust_root_report(*, adoption: bool = False, path: str = "shipgate.yaml"):
    f = _finding("F1", requires_human_review=True, autofix_safe=False)
    if adoption:
        f.check_id = "SHIP-VERIFY-POLICY-BASE-ABSENT"
        f.evidence = {
            "kind": "manifest_introduced",
            "changed_policy_files": [path],
        }
    return _report(decision="review_required", findings=[f], review_items=[f])


def test_manifest_modification_keeps_the_weakening_wording() -> None:
    task = build_fix_task(
        _trust_root_report(adoption=True),
        merge_verdict="human_review_required",
        capability_review=_review(policy_weakened=True, trust_root_touched=True),
        base_ref="origin/main",
        head_ref="HEAD",
        manifest_introduced=False,
    )

    assert task is not None and task.actor == "human"
    joined = " ".join(task.instructions)
    assert "cannot self-approve" in joined
    assert "adopts Agents Shipgate" not in joined
    assert {"review_policy_weakening", "review_trust_root"} <= {
        r.id for r in task.allowed_repairs
    }


def test_first_adoption_replaces_the_weakening_wording() -> None:
    """Adoption is not weakening: one honest instruction, not two wrong ones."""

    task = build_fix_task(
        _trust_root_report(adoption=True),
        merge_verdict="human_review_required",
        capability_review=_review(trust_root_touched=True),
        base_ref="origin/main",
        head_ref="HEAD",
        manifest_introduced=True,
    )

    assert task is not None
    # Routing is untouched: adoption is still an authority escalation.
    assert task.actor == "human" and task.safe_to_attempt is False
    joined = " ".join(task.instructions)
    assert "adopts Agents Shipgate" in joined
    assert "cannot self-approve" not in joined
    repair_ids = {r.id for r in task.allowed_repairs}
    assert "adopt_shipgate_manifest" in repair_ids
    assert not {"review_policy_weakening", "review_trust_root"} & repair_ids

    report = _trust_root_report(adoption=True)
    headline = _verifier_headline(
        report=report,
        merge_verdict="human_review_required",
        head_status="succeeded",
        capability_review=_review(trust_root_touched=True),
        manifest_introduced=True,
        pure_adoption_review=True,
        configured_manifest="shipgate.yaml",
    )
    assert headline is not None
    assert "introduces Agents Shipgate" in headline
    assert "then merge" in headline


def test_first_adoption_names_only_the_configured_manifest() -> None:
    task = build_fix_task(
        _trust_root_report(adoption=True, path="config/release.gate"),
        merge_verdict="human_review_required",
        capability_review=_review(trust_root_touched=True),
        base_ref="origin/main",
        head_ref="HEAD",
        manifest_introduced=True,
        config="config/release.gate",
    )

    assert task is not None
    joined = " ".join(task.instructions)
    assert "config/release.gate" in joined
    assert "generated shipgate.yaml" not in joined
    assert "agent-instruction" not in joined
    assert "CI files" not in joined
    adoption = next(
        repair
        for repair in task.allowed_repairs
        if repair.id == "adopt_shipgate_manifest"
    )
    assert adoption.target == "config/release.gate"


def test_an_adoption_that_also_weakens_policy_keeps_the_weakening_repair() -> None:
    """`review_policy_weakening` must not vanish behind adoption wording."""

    task = build_fix_task(
        _trust_root_report(),
        merge_verdict="human_review_required",
        capability_review=_review(policy_weakened=True, trust_root_touched=True),
        base_ref="origin/main",
        head_ref="HEAD",
        manifest_introduced=True,
    )

    assert task is not None
    joined = " ".join(task.instructions)
    assert "cannot self-approve" in joined
    assert "nothing existing was weakened" not in joined
    assert "review_policy_weakening" in {r.id for r in task.allowed_repairs}


def test_adoption_escalates_without_borrowing_another_flag() -> None:
    """An adoption is an authority decision in its own right.

    `policy_weakened` is honestly `false` during an adoption, so routing must
    not depend on it: with no capability flags set at all, a mechanically
    fixable finding must still route to a human rather than opening the
    coding-agent auto-fix path.
    """

    f = _with_applicable_patch(
        _finding("F1", requires_human_review=False, autofix_safe=True)
    )
    report = _report(decision="review_required", findings=[f], review_items=[f])

    task = build_fix_task(
        report,
        merge_verdict="human_review_required",
        capability_review=_review(),
        base_ref="origin/main",
        head_ref="HEAD",
        manifest_introduced=True,
    )

    assert task is not None
    assert task.actor == "human" and task.safe_to_attempt is False
    assert "adopts Agents Shipgate" not in " ".join(task.instructions)
    assert "adopt_shipgate_manifest" not in {r.id for r in task.allowed_repairs}


@pytest.mark.parametrize(
    ("decision", "merge_verdict"),
    [
        ("blocked", "blocked"),
        ("insufficient_evidence", "insufficient_evidence"),
    ],
)
def test_non_mergeable_adoption_leads_with_the_real_stop_condition(
    decision: str,
    merge_verdict: str,
) -> None:
    adoption = _finding("adoption", requires_human_review=True, autofix_safe=False)
    adoption.check_id = "SHIP-VERIFY-POLICY-BASE-ABSENT"
    adoption.evidence = {"kind": "manifest_introduced"}
    other = _finding("other", requires_human_review=True, autofix_safe=False)
    report = _report(
        decision=decision,
        findings=[adoption, other],
        blockers=[other] if decision == "blocked" else [],
        review_items=[adoption, other],
    )

    task = build_fix_task(
        report,
        merge_verdict=merge_verdict,  # type: ignore[arg-type]
        capability_review=_review(trust_root_touched=True),
        base_ref="origin/main",
        head_ref="HEAD",
        manifest_introduced=True,
        config="config/release.gate",
    )

    assert task is not None and task.actor == "human"
    assert task.instructions[0] == report.release_decision.reason
    assert "merge" not in " ".join(task.instructions).lower()
    assert "adopt_shipgate_manifest" not in {r.id for r in task.allowed_repairs}

    headline = _verifier_headline(
        report=report,
        merge_verdict=merge_verdict,  # type: ignore[arg-type]
        head_status="succeeded",
        capability_review=_review(trust_root_touched=True),
        manifest_introduced=True,
        pure_adoption_review=False,
        configured_manifest="config/release.gate",
    )
    assert headline is not None
    assert headline.startswith(report.release_decision.reason)
    assert "then merge" not in headline.lower()
    assert "separate human-review decision" in headline
