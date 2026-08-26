"""The policy reason-code split must not move configuration, gating, or facts.

``SHIP-VERIFY-POLICY-BASE-ABSENT`` carries the no-base fail-safe that
``SHIP-VERIFY-POLICY-WEAKENED`` used to carry. Splitting a reason code touches
three things that are easy to break silently and expensive to notice:

- **Configuration already written against the pre-split id.** A severity
  override raised the fail-safe before the split; after a rename alone it stops
  applying, and a repository's configured `critical / blocked` quietly becomes
  `medium / review_required`.
- **The claim the copy makes.** ``policy_weakened`` stays raised for an
  unprovable direction on purpose — that is fail-closed routing — but a run
  that never compared two policies must not tell a human it proved one got
  weaker.
- **Artifacts written before the split.** They carry the old id with the same
  evidence and are still read by ``--diff-from``, the PR-comment renderer, and
  anything reprojecting a stored ``report.json``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agents_shipgate.checks import verify_policy
from agents_shipgate.cli.verify.capability_review import build_capability_review
from agents_shipgate.cli.verify.fix_task import build_fix_task, is_pure_adoption_review
from agents_shipgate.cli.verify.orchestrator import _self_approval_note, run_verify
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.check_ids import (
    LEGACY_CHECK_ID_ALIASES,
    SPLIT_CHECK_ID_ALIASES,
    expands_to_check_id,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.findings.mutations import apply_severity_overrides
from agents_shipgate.core.findings.verifier_blocks import (
    build_human_ack,
    build_protected_surface_changes,
    build_verifier_summary,
)
from agents_shipgate.core.policy_reason_codes import (
    POLICY_BASE_ABSENT_CHECK_ID,
    POLICY_WEAKENED_CHECK_ID,
)
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
from agents_shipgate.schemas.verification import VerificationContext

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "support_refund_agent"
SAMPLE_CONFIG = Path("samples/support_refund_agent/shipgate.yaml")


# --- fixtures ---------------------------------------------------------------


def _policy_context(*, manifest_introduced: bool, changed=("shipgate.yaml",)):
    return ScanContext(
        manifest=load_manifest(SAMPLE / "shipgate.yaml"),
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        verification=VerificationContext(
            changed_files=list(changed),
            configured_manifest_path="shipgate.yaml",
            manifest_introduced=manifest_introduced,
        ),
    )


def _finding(check_id: str, kind: str, *, path: str = "shipgate.yaml") -> Finding:
    return Finding(
        id="F1",
        check_id=check_id,
        title="policy finding",
        severity="medium",
        category="verify",
        evidence={"kind": kind, "changed_policy_files": [path]},
        recommendation="A human must review the policy surface.",
    )


def _report(findings: list[Finding], *, review_items: list[Finding] | None = None):
    items = [
        ReleaseDecisionItem(
            id=f.id, check_id=f.check_id, severity=f.severity, title=f.title
        )
        for f in (review_items if review_items is not None else findings)
    ]
    report = ReadinessReport(
        run_id="r",
        project={"name": "p"},
        agent={"name": "a"},
        environment={"target": "local"},
        summary=ReportSummary(status="clean"),
        release_decision=ReleaseDecision(
            decision="review_required",
            reason="1 finding requires human review.",
            blockers=[],
            review_items=items,
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
                would_fail_ci=False,
                exit_code=0,
            ),
        ),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=list(findings),
    )
    return report


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo_adopting_shipgate(tmp_path: Path, *, manifest_text: str | None = None) -> Path:
    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(SAMPLE, sample_dst)
    manifest = sample_dst / "shipgate.yaml"
    held_back = manifest_text or manifest.read_text(encoding="utf-8")
    manifest.unlink()

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "before shipgate")

    manifest.write_text(held_back, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "adopt shipgate")
    return repo


def _run_verify(repo: Path):
    verifier, report, exit_code = run_verify(
        workspace=repo,
        config=SAMPLE_CONFIG,
        base="HEAD~1",
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
    return verifier, report, exit_code


# --- 1. configuration written against the pre-split id ----------------------


def test_the_pre_split_id_is_an_umbrella_for_both_halves():
    assert expands_to_check_id(POLICY_WEAKENED_CHECK_ID, POLICY_BASE_ABSENT_CHECK_ID)
    # Not the other way round: the new id is not a name for the old check.
    assert not expands_to_check_id(
        POLICY_BASE_ABSENT_CHECK_ID, POLICY_WEAKENED_CHECK_ID
    )
    # And it is not *deprecated* — a baseline naming it must not be reported as
    # a stale alias, which is what listing it under the legacy map would do.
    assert POLICY_WEAKENED_CHECK_ID in SPLIT_CHECK_ID_ALIASES
    assert POLICY_WEAKENED_CHECK_ID not in LEGACY_CHECK_ID_ALIASES


@pytest.mark.parametrize("manifest_introduced", [True, False])
def test_an_existing_override_still_reaches_the_no_base_finding(manifest_introduced):
    """The reviewer's reproduction: `critical` must not decay to `medium`."""

    findings = verify_policy.run(
        _policy_context(manifest_introduced=manifest_introduced)
    )
    assert [f.check_id for f in findings] == [POLICY_BASE_ABSENT_CHECK_ID]
    apply_severity_overrides(findings, {POLICY_WEAKENED_CHECK_ID: "critical"})
    assert findings[0].severity == "critical"


def test_an_override_on_the_new_id_wins_over_the_umbrella():
    findings = verify_policy.run(_policy_context(manifest_introduced=False))
    apply_severity_overrides(
        findings,
        {
            POLICY_WEAKENED_CHECK_ID: "critical",
            POLICY_BASE_ABSENT_CHECK_ID: "high",
        },
    )
    assert findings[0].severity == "high"


def test_a_configured_override_still_blocks_the_release_end_to_end(tmp_path):
    """Gating, not just severity: the configured verdict must not move.

    A repository that had raised this fail-safe to `critical` and gated on it
    got `blocked`. After the split — with no compatibility mapping — the same
    tree produced `review_required`, which is a gate that loosened because an
    id moved.
    """

    manifest = (SAMPLE / "shipgate.yaml").read_text(encoding="utf-8")
    manifest = manifest.replace(
        "\nci:\n",
        "\nchecks:\n"
        f"  severity_overrides:\n    {POLICY_WEAKENED_CHECK_ID}: critical\n"
        "\nci:\n",
        1,
    )
    repo = _repo_adopting_shipgate(tmp_path, manifest_text=manifest)

    _verifier, report, _exit = _run_verify(repo)

    assert report is not None and report.release_decision is not None
    policy = [
        finding
        for finding in report.findings
        if finding.check_id == POLICY_BASE_ABSENT_CHECK_ID
    ]
    assert policy, [f.check_id for f in report.findings]
    assert policy[0].severity == "critical"
    assert report.release_decision.decision == "blocked"
    assert any(
        item.check_id == POLICY_BASE_ABSENT_CHECK_ID
        for item in report.release_decision.blockers
    )


# --- 2. fail-closed routing vs. honest copy ---------------------------------


def test_an_unprovable_direction_routes_closed_but_claims_nothing():
    review = build_capability_review(
        _report(verify_policy.run(_policy_context(manifest_introduced=False)))
    )

    # Fail-closed routing is unchanged.
    assert review.policy_weakened is True
    # The narrower fact is what the copy reads.
    assert review.policy_weakening_proven is False

    note = _self_approval_note(review)
    assert note is not None
    assert "weakens the release policy" not in note
    assert "no base policy was available" in note
    # The prohibition itself is untouched.
    assert "cannot self-approve" in note
    assert "a human must review it" in note


def test_a_proven_weakening_still_says_it_weakens_the_policy():
    proven = _report([_finding(POLICY_WEAKENED_CHECK_ID, "ci_mode_weakened")])
    review = build_capability_review(proven)

    assert review.policy_weakened is True
    assert review.policy_weakening_proven is True
    note = _self_approval_note(review)
    assert note is not None
    assert "weakens the release policy" in note


def test_the_repair_reason_does_not_assert_an_unproven_weakening():
    report = _report(verify_policy.run(_policy_context(manifest_introduced=False)))
    review = build_capability_review(report)

    task = build_fix_task(
        report,
        merge_verdict="human_review_required",
        capability_review=review,
        base_ref="origin/main",
        head_ref="HEAD",
    )

    assert task is not None and task.actor == "human"
    repair = next(
        r for r in task.allowed_repairs if r.id == "review_policy_weakening"
    )
    assert "could not be proven" in repair.reason
    assert "approve release-policy weakening" not in repair.reason


def test_a_no_base_run_reports_the_honest_copy_end_to_end(tmp_path):
    """The whole artifact, not just the helper: nothing claims a weakening."""

    repo = _repo_adopting_shipgate(tmp_path)
    # An adoption that also touches an existing policy pack is not a pure
    # adoption: the pack was already there, so the fail-safe keeps the
    # unprovable-direction kind rather than the adoption kind.
    pack = repo / "samples" / "support_refund_agent" / "policies" / "refunds.yaml"
    pack.parent.mkdir(parents=True, exist_ok=True)
    pack.write_text("version: 1\nrules: []\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--amend", "--no-edit")

    verifier, _report, _exit = _run_verify(repo)

    payload = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text("utf-8")
    )
    policy = [
        f for f in payload["findings"] if f["check_id"] == POLICY_BASE_ABSENT_CHECK_ID
    ]
    assert policy and policy[0]["evidence"]["kind"] == "base_snapshot_unavailable"
    assert verifier.capability_review.policy_weakened is True
    assert verifier.capability_review.policy_weakening_proven is False
    assert verifier.headline is not None
    assert "weakens the release policy" not in verifier.headline
    assert "weakens the release policy" not in json.dumps(
        verifier.control.model_dump(mode="json")
    )
    assert "weakens the release policy" not in json.dumps(
        verifier.fix_task.model_dump(mode="json")
    )
    # Still fail-closed.
    assert verifier.can_merge_without_human is False
    assert verifier.control.human_review.required is True


# --- 4. artifacts written before the split ----------------------------------


def test_a_legacy_adoption_artifact_still_reprojects_as_an_adoption():
    """Old id + ``manifest_introduced`` meant "nothing existed to weaken"."""

    legacy = _finding(POLICY_WEAKENED_CHECK_ID, "manifest_introduced")
    report = _report([legacy])

    assert build_capability_review(report).policy_weakened is False
    assert build_verifier_summary(report).policy_weakened is False
    report.protected_surface_changes = build_protected_surface_changes(report)
    assert [(r.kind, r.path) for r in report.protected_surface_changes] == [
        ("policy", "shipgate.yaml")
    ]
    assert build_human_ack(report).required is True
    assert is_pure_adoption_review(report, manifest_introduced=True) is True


def test_a_legacy_no_base_artifact_still_reprojects_as_weakened():
    legacy = _finding(POLICY_WEAKENED_CHECK_ID, "base_snapshot_unavailable")
    report = _report([legacy])

    review = build_capability_review(report)
    assert review.policy_weakened is True
    # It was never a proven weakening then either, and must not become one now.
    assert review.policy_weakening_proven is False
    assert is_pure_adoption_review(report, manifest_introduced=True) is False


def test_the_split_never_re_emits_the_pre_split_id_for_a_no_base_run():
    for manifest_introduced in (True, False):
        emitted = {
            f.check_id
            for f in verify_policy.run(
                _policy_context(manifest_introduced=manifest_introduced)
            )
        }
        assert emitted == {POLICY_BASE_ABSENT_CHECK_ID}, manifest_introduced


def test_github_action_outputs_read_both_reason_codes():
    """The Action's findings fallback mirrors ``verifier_summary``."""

    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from github_action_outputs import _verifier_flags
    finally:
        sys.path.pop(0)

    def payload(check_id: str, kind: str) -> dict:
        return {
            "findings": [
                {"check_id": check_id, "suppressed": False, "evidence": {"kind": kind}}
            ]
        }

    for check_id in (POLICY_WEAKENED_CHECK_ID, POLICY_BASE_ABSENT_CHECK_ID):
        assert _verifier_flags(payload(check_id, "manifest_introduced"), {})[1] is False
        assert (
            _verifier_flags(payload(check_id, "base_snapshot_unavailable"), {})[1]
            is True
        )


# --- 6. the machine catalog describes the implementation --------------------


def test_the_catalog_states_the_comparison_in_the_direction_it_runs():
    catalog = json.loads((REPO_ROOT / "docs" / "checks.json").read_text("utf-8"))
    entry = next(
        item
        for item in catalog["checks"]
        if item["id"] == POLICY_WEAKENED_CHECK_ID
    )
    fires_when = entry["fires_when"]
    assert "head manifest's effective policy is weaker than the base" in fires_when
    assert "base report's effective_policy is weaker than the head" not in fires_when
    # And the pair is described as mutually exclusive on the base snapshot.
    absent = next(
        item
        for item in catalog["checks"]
        if item["id"] == POLICY_BASE_ABSENT_CHECK_ID
    )
    assert "no base effective-policy snapshot is available" in absent["fires_when"]


# --- second review: schema contract, comparator, and accepted debt ----------


def test_the_frozen_v0_8_schema_still_describes_v0_8_artifacts():
    """Adding an emitted field under a frozen identifier is a wire break.

    A consumer pinned to the published v0.8 schema validates every artifact
    that *declares* 0.8. The field lives in 0.9 and later; 0.8 keeps its bytes.
    """

    from jsonschema import Draft202012Validator

    v08 = json.loads((REPO_ROOT / "docs" / "verifier-schema.v0.8.json").read_text("utf-8"))
    current = json.loads(
        (REPO_ROOT / "docs" / "verifier-schema.v0.12.json").read_text("utf-8")
    )

    review = v08["$defs"]["VerifierCapabilityReview"]["properties"]
    assert "policy_weakening_proven" not in review
    assert "policy_weakening_proven" in (
        current["$defs"]["VerifierCapabilityReview"]["properties"]
    )
    assert v08["properties"]["verifier_schema_version"]["const"] == "0.8"
    assert current["properties"]["verifier_schema_version"]["const"] == "0.12"

    from agents_shipgate.schemas.verifier import VerifierArtifact

    assert VerifierArtifact.model_fields["verifier_schema_version"].default == "0.12"
    Draft202012Validator.check_schema(current)


def test_a_v0_8_artifact_still_reads_and_normalizes_forward(tmp_path):
    """The frozen shape stays readable; the field defaults to the honest false.

    A stored v0.8 artifact carries no ``policy_weakening_proven``. Reading one
    must not fail and must not invent a proven weakening — ``false`` is exactly
    what "this artifact recorded no comparison" means.
    """

    from agents_shipgate.schemas.verifier import VerifierArtifact

    repo = _repo_adopting_shipgate(tmp_path)
    verifier, _report, _exit = _run_verify(repo)
    payload = verifier.model_dump(mode="json")
    assert payload["verifier_schema_version"] == "0.12"

    payload["verifier_schema_version"] = "0.8"
    payload["capability_review"].pop("policy_weakening_proven")
    normalized = VerifierArtifact.model_validate(payload)

    assert normalized.verifier_schema_version == "0.12"
    assert normalized.capability_review.policy_weakening_proven is False


def test_proven_weakening_cannot_contradict_the_routing_flag():
    from pydantic import ValidationError

    from agents_shipgate.schemas.verifier import VerifierCapabilityReview

    with pytest.raises(ValidationError, match="requires policy_weakened=True"):
        VerifierCapabilityReview(policy_weakened=False, policy_weakening_proven=True)
    # The supported combinations still construct.
    VerifierCapabilityReview(policy_weakened=True, policy_weakening_proven=True)
    VerifierCapabilityReview(policy_weakened=True, policy_weakening_proven=False)
    VerifierCapabilityReview(policy_weakened=False, policy_weakening_proven=False)


def _override(severity: str):
    from agents_shipgate.schemas.manifest.checks import SeverityOverrideEntry

    return SeverityOverrideEntry(severity=severity, reason="test")


def _comparison_context(*, base_overrides: dict, head_overrides: dict):
    from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference
    from agents_shipgate.schemas.capability_change import EffectivePolicy

    manifest = load_manifest(SAMPLE / "shipgate.yaml")
    manifest.checks.severity_overrides = {
        check_id: _override(severity) for check_id, severity in head_overrides.items()
    }
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        verification=VerificationContext(
            changed_files=["shipgate.yaml"],
            configured_manifest_path="shipgate.yaml",
        ),
        diff_reference=ToolSurfaceDiffReference(
            kind="report",
            facts=None,
            effective_policy=EffectivePolicy(
                ci_mode="advisory",
                fail_on=["critical", "high"],
                severity_overrides=dict(base_overrides),
            ),
        ),
    )


def _lowerings(context) -> list:
    return [
        finding
        for finding in verify_policy.run(context)
        if finding.evidence.get("kind") == "severity_override_lowered"
    ]


def test_the_comparator_sees_a_lowering_written_against_the_new_id():
    """False negative: the applied severity drops with no umbrella key change.

    Base applies `critical` to both halves through the umbrella. Head keeps the
    umbrella and adds an explicit override for the new id at `medium`, which is
    what the runtime applier uses — so the gate got weaker and the comparator
    has to say so.
    """

    findings = _lowerings(
        _comparison_context(
            base_overrides={POLICY_WEAKENED_CHECK_ID: "critical"},
            head_overrides={
                POLICY_WEAKENED_CHECK_ID: "critical",
                POLICY_BASE_ABSENT_CHECK_ID: "medium",
            },
        )
    )
    assert [f.evidence["target_check_id"] for f in findings] == [
        POLICY_BASE_ABSENT_CHECK_ID
    ]
    assert findings[0].evidence["base_severity"] == "critical"
    assert findings[0].evidence["head_severity"] == "medium"


def test_the_comparator_does_not_invent_a_lowering_that_never_happened():
    """False positive: dropping a redundant explicit override changes nothing.

    Base spells `critical` twice; head spells it once and still applies
    `critical` to both halves through the umbrella. Reporting a weakening here
    would send a human to review a change that does not exist.
    """

    assert (
        _lowerings(
            _comparison_context(
                base_overrides={
                    POLICY_WEAKENED_CHECK_ID: "critical",
                    POLICY_BASE_ABSENT_CHECK_ID: "critical",
                },
                head_overrides={POLICY_WEAKENED_CHECK_ID: "critical"},
            )
        )
        == []
    )


def test_a_real_lowering_on_an_unrelated_check_is_untouched():
    findings = _lowerings(
        _comparison_context(
            base_overrides={"SHIP-AUTH-MISSING-SCOPE": "critical"},
            head_overrides={"SHIP-AUTH-MISSING-SCOPE": "medium"},
        )
    )
    assert [f.evidence["target_check_id"] for f in findings] == ["SHIP-AUTH-MISSING-SCOPE"]


def test_pre_split_accepted_debt_still_matches_and_keeps_the_verdict():
    """Debt accepted under the old id is the same accepted item.

    Without a legacy candidate the row goes matched -> new, and a `critical`
    accepted-debt finding moves the decision from `review_required` to
    `blocked` — a verdict change nobody authored.
    """

    from agents_shipgate.ci.release_decision import build_release_decision
    from agents_shipgate.core.baseline import apply_baseline
    from agents_shipgate.core.findings.identity import (
        assign_finding_ids,
        legacy_split_check_id_fingerprints,
    )
    from agents_shipgate.schemas.baseline import BaselineFile, BaselineFinding

    finding = verify_policy.run(_policy_context(manifest_introduced=False))[0]
    finding.severity = "critical"
    assign_finding_ids([finding])

    legacy = sorted(legacy_split_check_id_fingerprints(finding))
    assert len(legacy) == 1
    assert legacy[0] != finding.fingerprint

    baseline = BaselineFile(
        created_at="2026-08-01T00:00:00Z",
        source_report_run_id="run-1",
        findings=[
            BaselineFinding(
                fingerprint=legacy[0],
                check_id=POLICY_WEAKENED_CHECK_ID,
                severity="critical",
                title="Policy change cannot be proven safe (no base snapshot)",
            )
        ],
    )
    summary = apply_baseline([finding], baseline, display_path="baseline.json")

    assert finding.baseline_status == "matched"
    assert (summary.matched_count, summary.new_count, summary.resolved_count) == (1, 0, 0)

    accepted = _report([finding])
    accepted.findings = [finding]
    decision = build_release_decision(
        report=accepted,
        tools=[],
        ci_mode="advisory",
        fail_on=["critical", "high"],
        new_findings_only=False,
    )
    assert decision.decision == "review_required"
    assert not decision.blockers
    assert [item.check_id for item in decision.review_items] == [
        POLICY_BASE_ABSENT_CHECK_ID
    ]


def test_the_legacy_fingerprint_candidate_is_scoped_to_declared_splits():
    """It must not become a way to absorb unrelated accepted debt."""

    from agents_shipgate.core.findings.identity import legacy_split_check_id_fingerprints

    unrelated = _finding("SHIP-AUTH-MISSING-SCOPE", "kind")
    assert legacy_split_check_id_fingerprints(unrelated) == set()
    # The umbrella id itself is not a split *target*, so it offers no candidate.
    umbrella = _finding(POLICY_WEAKENED_CHECK_ID, "ci_mode_weakened")
    assert legacy_split_check_id_fingerprints(umbrella) == set()
