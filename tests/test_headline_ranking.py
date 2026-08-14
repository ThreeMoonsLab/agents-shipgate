"""Issue #365: the headline ranks by the strongest signal, not by category.

Two facts used to be reported in the wrong order. A PR that both touched the
release trust root and blocked release on critical findings got a headline
about the trust root — a medium governance notice — while the blockers went
unmentioned in the one line that reaches a PR comment or a triage list. And
the fail-safe that fires when there is no base policy to compare against
shared a reason code with a real base-relative weakening, so a first adoption
reported a weakening that definitionally could not have happened.

These tests pin the corrected ordering and the reason-code split *and* the
invariants neither may move: the control state, the merge verdict, the
permission vector, the human-review requirement, and the ``policy_weakened``
fail-safe that keeps an unprovable direction treated as weakening.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agents_shipgate.checks import verify_policy
from agents_shipgate.cli.verify.orchestrator import (
    _blockers_outrank_governance,
    _derive_verifier_control,
    _report_primary_headline,
    _verifier_headline,
    _worst_blocker,
    run_verify,
)
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.findings.verifier_blocks import (
    build_human_ack,
    build_protected_surface_changes,
)
from agents_shipgate.schemas.report import (
    AgentSummary,
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
from agents_shipgate.schemas.verification import VerificationContext
from agents_shipgate.schemas.verifier import (
    VerifierCapabilityReview,
    VerifierDiffStatus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORT_SAMPLE = REPO_ROOT / "samples" / "support_refund_agent"
SUPPORT_CONFIG = Path("samples/support_refund_agent/shipgate.yaml")
CLEAN_SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"

SELF_APPROVAL_CLAUSE = "cannot self-approve"


# --- fixtures ---------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init(repo: Path, message: str) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _adopted_repo(tmp_path: Path, sample: Path, *, into: str = ".") -> Path:
    """A repository that already carries the manifest on its base commit."""

    repo = tmp_path / "repo"
    destination = repo / into
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(sample, destination)
    _init(repo, "already adopted")
    return repo


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text("utf-8") + text, encoding="utf-8")


def _run_verify(repo: Path, config: Path, *, base: str | None = "HEAD~1"):
    verifier, report, _exit = run_verify(
        workspace=repo,
        config=config,
        base=base,
        head="HEAD",
        archive_head=True,
        out=repo / "agents-shipgate-reports",
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
    )
    return verifier, report


def _review(*, policy_weakened=False, trust_root_touched=False):
    return VerifierCapabilityReview(
        policy_weakened=policy_weakened, trust_root_touched=trust_root_touched
    )


def _blocker(check_id: str, severity: str, title: str | None = None):
    return ReleaseDecisionItem(
        id=check_id,
        check_id=check_id,
        severity=severity,  # type: ignore[arg-type]
        title=title or check_id,
    )


def _report_with(*, decision: str, blockers: list[ReleaseDecisionItem], headline: str):
    report = ReadinessReport(
        run_id="r",
        project={"name": "p"},
        agent={"name": "a"},
        environment={"target": "local"},
        summary=ReportSummary(status="clean"),
        release_decision=ReleaseDecision(
            decision=decision,  # type: ignore[arg-type]
            reason=f"{len(blockers)} active findings block release.",
            blockers=list(blockers),
            review_items=[],
            evidence_coverage=EvidenceCoverageDecision(
                level="static",
                human_review_recommended=False,
                source_warning_count=0,
                low_confidence_tool_count=0,
            ),
            baseline_delta=BaselineDelta(enabled=False),
            fail_policy=FailPolicy(
                ci_mode="advisory",
                fail_on=["critical", "high"],
                new_findings_only=False,
                would_fail_ci=True,
                exit_code=20,
            ),
        ),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=[],
    )
    report.agent_summary = AgentSummary(
        verdict=decision,  # type: ignore[arg-type]
        headline=headline,
        blocker_count=len(blockers),
        review_item_count=0,
        auto_appliable_patches=0,
        needs_human_review=len(blockers),
    )
    return report


# --- the ranking predicate --------------------------------------------------


@pytest.mark.parametrize("severity", ["critical", "high"])
def test_a_critical_or_high_blocker_outranks_the_governance_notice(severity):
    report = _report_with(
        decision="blocked",
        blockers=[_blocker("SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING", severity)],
        headline="1 active finding(s) block release.",
    )
    assert _blockers_outrank_governance(report.release_decision) is True


def test_a_medium_blocker_does_not_demote_the_governance_notice():
    """The notices are themselves medium; a peer must not outrank them."""

    report = _report_with(
        decision="blocked",
        blockers=[_blocker("SHIP-DOC-MISSING-DESCRIPTION", "medium")],
        headline="1 active finding(s) block release.",
    )
    assert _blockers_outrank_governance(report.release_decision) is False


def test_no_release_decision_never_outranks():
    assert _blockers_outrank_governance(None) is False


# --- naming the blocking cause ----------------------------------------------


def test_the_worst_blocker_is_named_and_chosen_by_severity_then_check_id():
    """A count is not a cause; the reviewer needs to know what blocks."""

    report = _report_with(
        decision="blocked",
        blockers=[
            _blocker("SHIP-POLICY-APPROVAL-MISSING", "critical", "b lacks approval"),
            _blocker(
                "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
                "critical",
                "spraay_batch_eth has financial write capability "
                "without required controls",
            ),
            _blocker("SHIP-DOC-MISSING-DESCRIPTION", "high", "c lacks a description"),
        ],
        headline="3 active finding(s) block release.",
    )

    worst = _worst_blocker(report.release_decision)
    assert worst is not None
    assert worst.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
    assert _report_primary_headline(report) == (
        "3 active finding(s) block release. Most severe: spraay_batch_eth has "
        "financial write capability without required controls."
    )


def test_a_run_with_no_blockers_names_no_cause():
    report = _report_with(
        decision="review_required", blockers=[], headline="2 review item(s) flagged."
    )
    assert _report_primary_headline(report) == "2 review item(s) flagged."


# --- headline composition ---------------------------------------------------


def test_headline_leads_with_the_blockers_and_keeps_the_review_requirement():
    report = _report_with(
        decision="blocked",
        blockers=[
            _blocker(
                "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
                "critical",
                "spraay_batch_eth has financial write capability "
                "without required controls",
            ),
        ],
        headline="4 active finding(s) block release.",
    )

    headline = _verifier_headline(
        report=report,
        merge_verdict="blocked",
        head_status="succeeded",
        capability_review=_review(trust_root_touched=True),
    )

    assert headline is not None
    assert headline.startswith("4 active finding(s) block release.")
    assert "financial write capability" in headline
    assert headline.index("financial write") < headline.index("trust root")
    assert SELF_APPROVAL_CLAUSE in headline


def test_a_weakened_policy_still_says_so_after_the_blockers():
    report = _report_with(
        decision="blocked",
        blockers=[_blocker("SHIP-POLICY-APPROVAL-MISSING", "critical")],
        headline="1 active finding(s) block release.",
    )

    headline = _verifier_headline(
        report=report,
        merge_verdict="blocked",
        head_status="succeeded",
        capability_review=_review(policy_weakened=True, trust_root_touched=True),
    )

    assert headline is not None
    assert headline.startswith("1 active finding(s) block release.")
    assert "weakens the release policy" in headline


def test_a_trust_root_notice_with_no_blockers_is_still_the_whole_headline():
    report = _report_with(
        decision="review_required", blockers=[], headline="2 review item(s) flagged."
    )

    headline = _verifier_headline(
        report=report,
        merge_verdict="human_review_required",
        head_status="succeeded",
        capability_review=_review(trust_root_touched=True),
    )

    assert headline is not None
    assert headline.startswith("This PR edits a release trust root")
    assert "review item(s) flagged" not in headline


def test_a_failed_scan_still_wins_over_every_ranking():
    report = _report_with(
        decision="blocked",
        blockers=[_blocker("SHIP-POLICY-APPROVAL-MISSING", "critical")],
        headline="1 active finding(s) block release.",
    )
    assert (
        _verifier_headline(
            report=report,
            merge_verdict="unknown",
            head_status="failed",
            capability_review=_review(trust_root_touched=True),
        )
        == "Shipgate could not complete the scan; human review required."
    )


# --- the control envelope is ordering-only ----------------------------------


def _control(report, *, headline):
    return _derive_verifier_control(
        execution="succeeded",
        merge_verdict="blocked",
        release_decision=report.release_decision,
        fix_task=None,
        capability_review=_review(trust_root_touched=True),
        headline=headline,
        first_next_action_override=None,
        base_status="succeeded",
        base_ref="origin/main",
        diff_status=VerifierDiffStatus(completeness="complete"),
    )


def test_the_human_review_reason_keeps_both_facts_and_the_route_is_unchanged():
    """The reordered headline must not cost the control envelope either fact."""

    report = _report_with(
        decision="blocked",
        blockers=[_blocker("SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING", "critical")],
        headline="4 active finding(s) block release.",
    )
    headline = _verifier_headline(
        report=report,
        merge_verdict="blocked",
        head_status="succeeded",
        capability_review=_review(trust_root_touched=True),
    )

    control = _control(report, headline=headline)

    assert control.state == "human_review_required"
    assert control.must_stop is True
    assert control.human_review.required is True
    assert control.human_review.why is not None
    assert "block release" in control.human_review.why
    assert SELF_APPROVAL_CLAUSE in control.human_review.why
    assert control.next_action is not None and control.next_action.kind == "stop"
    permissions = control.permissions.model_dump()
    assert not any(permissions.values()), permissions


def test_a_governance_only_run_routes_exactly_as_before():
    report = _report_with(
        decision="review_required", blockers=[], headline="2 review item(s) flagged."
    )
    headline = _verifier_headline(
        report=report,
        merge_verdict="human_review_required",
        head_status="succeeded",
        capability_review=_review(trust_root_touched=True),
    )

    control = _derive_verifier_control(
        execution="succeeded",
        merge_verdict="human_review_required",
        release_decision=report.release_decision,
        fix_task=None,
        capability_review=_review(trust_root_touched=True),
        headline=headline,
        first_next_action_override=None,
        base_status="succeeded",
        base_ref="origin/main",
        diff_status=VerifierDiffStatus(completeness="complete"),
    )

    assert control.human_review.required is True
    assert control.human_review.why is not None
    assert control.human_review.why.startswith("This PR edits a release trust root")
    assert control.permissions.merge is False
    assert control.permissions.report_complete is False


# --- the reason-code split --------------------------------------------------


def _policy_context(*, manifest_introduced: bool, changed=("shipgate.yaml",)):
    return ScanContext(
        manifest=load_manifest(SUPPORT_SAMPLE / "shipgate.yaml"),
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        verification=VerificationContext(
            changed_files=list(changed),
            configured_manifest_path="shipgate.yaml",
            manifest_introduced=manifest_introduced,
        ),
    )


def _report_carrying(findings: list[Finding]) -> ReadinessReport:
    report = _report_with(decision="review_required", blockers=[], headline="x")
    report.findings = findings
    return report


def test_the_no_base_fail_safe_no_longer_claims_a_base_relative_weakening():
    for manifest_introduced in (True, False):
        findings = verify_policy.run(
            _policy_context(manifest_introduced=manifest_introduced)
        )
        assert [f.check_id for f in findings] == [
            verify_policy.BASE_ABSENT_CHECK_ID
        ], manifest_introduced


def test_an_unprovable_direction_still_counts_as_weakened():
    """The fail-safe is what stops a broken base scan from clearing the alarm.

    Only a git-proven first adoption may report ``policy_weakened: false``;
    every other missing base keeps the flag raised even under the new reason
    code, so a rename-and-loosen diff cannot hide behind it.
    """

    from agents_shipgate.cli.verify.capability_review import build_capability_review

    unprovable = _report_carrying(
        verify_policy.run(_policy_context(manifest_introduced=False))
    )
    adoption = _report_carrying(
        verify_policy.run(_policy_context(manifest_introduced=True))
    )

    assert build_capability_review(unprovable).policy_weakened is True
    assert build_capability_review(adoption).policy_weakened is False


def test_the_new_reason_code_keeps_the_acknowledgement_and_surface_rows():
    """Reason code and copy move; authority and surface accounting do not."""

    report = _report_carrying(
        verify_policy.run(_policy_context(manifest_introduced=False))
    )

    ack = build_human_ack(report)
    assert ack.required is True
    assert "policy" in ack.outstanding
    assert ack.satisfied is False

    rows = build_protected_surface_changes(report)
    assert [(row.kind, row.path) for row in rows] == [("policy", "shipgate.yaml")]


def test_a_real_base_relative_weakening_keeps_the_weakening_reason_code(tmp_path):
    """The base-relative claim stays where it belongs."""

    repo = _adopted_repo(
        tmp_path, SUPPORT_SAMPLE, into="samples/support_refund_agent"
    )
    manifest = repo / SUPPORT_CONFIG
    strict = manifest.read_text("utf-8").replace(
        "ci:\n  mode: advisory", "ci:\n  mode: strict"
    )
    assert "mode: strict" in strict
    manifest.write_text(strict, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "strict gate")
    manifest.write_text(
        strict.replace("ci:\n  mode: strict", "ci:\n  mode: advisory"),
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "weaken the gate")

    verifier, _report = _run_verify(repo, SUPPORT_CONFIG)

    payload = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text("utf-8")
    )
    kinds = {
        finding["evidence"].get("kind")
        for finding in payload["findings"]
        if finding["check_id"] == verify_policy.CHECK_ID
    }
    assert "ci_mode_weakened" in kinds
    assert not [
        finding
        for finding in payload["findings"]
        if finding["check_id"] == verify_policy.BASE_ABSENT_CHECK_ID
    ]
    assert verifier.capability_review.policy_weakened is True


# --- end to end -------------------------------------------------------------


def test_a_blocked_trust_root_pr_leads_with_the_blockers(tmp_path):
    """The issue's repro shape on a repository that has already adopted."""

    repo = _adopted_repo(
        tmp_path, SUPPORT_SAMPLE, into="samples/support_refund_agent"
    )
    _append(repo / SUPPORT_CONFIG, "\n# reviewed edit\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "edit the manifest")

    verifier, report = _run_verify(repo, SUPPORT_CONFIG)

    assert report is not None and report.release_decision is not None
    assert report.release_decision.decision == "blocked"
    assert report.release_decision.blockers
    assert verifier.capability_review.trust_root_touched is True

    assert verifier.headline is not None
    assert verifier.headline.startswith("5 active finding(s) block release")
    # The cause, not just the count — and before the governance notice.
    assert "financial write capability without required controls" in verifier.headline
    assert verifier.headline.index("financial write") < verifier.headline.index(
        "trust root"
    )
    assert SELF_APPROVAL_CLAUSE in verifier.headline

    # Ordering only: every gating, control, and permission fact is what a
    # blocked trust-root PR produced before the headline was reordered.
    assert verifier.merge_verdict == "blocked"
    assert verifier.can_merge_without_human is False
    assert verifier.control.state == "human_review_required"
    assert verifier.control.must_stop is True
    assert verifier.control.human_review.required is True
    assert SELF_APPROVAL_CLAUSE in verifier.control.human_review.why
    assert not any(verifier.control.permissions.model_dump().values())


def test_a_trust_root_only_pr_still_leads_with_the_trust_root_message(tmp_path):
    repo = _adopted_repo(tmp_path, CLEAN_SAMPLE)
    _append(repo / "shipgate.yaml", "\n# reviewed edit\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "edit the manifest")

    verifier, report = _run_verify(repo, Path("shipgate.yaml"))

    assert report is not None and report.release_decision is not None
    assert report.release_decision.blockers == []
    assert verifier.headline is not None
    assert verifier.headline.startswith("This PR edits a release trust root")
    assert verifier.can_merge_without_human is False
