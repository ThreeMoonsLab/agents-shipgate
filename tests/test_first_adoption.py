"""First adoption is not a policy weakening.

Adding the manifest to a repository that had none is the first verdict every
new adopter sees. It used to read "This PR weakens the release policy that
evaluates it", carried a fail-safe finding titled "Policy change cannot be
proven safe", and — because the missing-manifest base was classified as a safe
recovery — shipped no ``fix_task`` at all, so nothing named the one act that
would clear it.

These tests pin the corrected behavior *and* the invariants it must not move:
the verdict, the severity, the check id, and the fact that a coding agent still
cannot self-approve the adoption.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from agents_shipgate.checks import verify_policy
from agents_shipgate.cli.verify.orchestrator import (
    _manifest_introduced,
    _self_approval_note,
    run_verify,
)
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.schemas.verification import VerificationContext
from agents_shipgate.schemas.verifier import VerifierCapabilityReview

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "support_refund_agent"
SAMPLE_CONFIG = Path("samples/support_refund_agent/shipgate.yaml")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo_adopting_shipgate(tmp_path: Path) -> Path:
    """A repo whose first commit has no manifest and whose second adds one."""

    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(SAMPLE, sample_dst)
    manifest = sample_dst / "shipgate.yaml"
    held_back = manifest.read_text(encoding="utf-8")
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


# --- the git-proved introduction signal -------------------------------------


def test_missing_manifest_base_with_no_manifest_anywhere_is_an_adoption(tmp_path):
    repo = _repo_adopting_shipgate(tmp_path)
    assert (
        _manifest_introduced(
            git_root=repo,
            config_relative=SAMPLE_CONFIG,
            base_status="missing_manifest",
            base="HEAD~1",
            head="HEAD",
            changed_files=[SAMPLE_CONFIG.as_posix()],
        )
        is True
    )


def test_moved_manifest_cannot_pass_itself_off_as_an_adoption(tmp_path):
    """The dodge this signal has to survive.

    A PR that moves the manifest to a new path *and* loosens it also finds
    nothing at the configured path on the base. Only "the base carries no
    manifest under any name" separates adoption from relocation.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    (repo / "samples" / "support_refund_agent" / "config").mkdir()
    _git(
        repo,
        "mv",
        "samples/support_refund_agent/shipgate.yaml",
        "samples/support_refund_agent/config/shipgate.yaml",
    )
    _git(repo, "commit", "-m", "move the manifest")

    assert (
        _manifest_introduced(
            git_root=repo,
            config_relative=Path("samples/support_refund_agent/config/shipgate.yaml"),
            base_status="missing_manifest",
            base="HEAD~1",
            head="HEAD",
            changed_files=["samples/support_refund_agent/config/shipgate.yaml"],
        )
        is False
    )


def test_uncommitted_new_manifest_is_an_adoption(tmp_path):
    """The local case: `init --write` then `verify`, nothing committed yet."""

    repo = _repo_adopting_shipgate(tmp_path)
    _git(repo, "reset", "--soft", "HEAD~1")

    assert (
        _manifest_introduced(
            git_root=repo,
            config_relative=SAMPLE_CONFIG,
            base_status="not_requested",
            base=None,
            head="HEAD",
            changed_files=[SAMPLE_CONFIG.as_posix()],
        )
        is True
    )


def test_adopted_repo_is_never_an_adoption(tmp_path):
    repo = _repo_adopting_shipgate(tmp_path)
    assert (
        _manifest_introduced(
            git_root=repo,
            config_relative=SAMPLE_CONFIG,
            base_status="not_requested",
            base=None,
            head="HEAD",
            changed_files=[SAMPLE_CONFIG.as_posix()],
        )
        is False
    )


def test_unknown_base_is_never_an_adoption(tmp_path):
    """Absence of evidence is not evidence of absence."""

    repo = _repo_adopting_shipgate(tmp_path)
    for status in ("ref_missing", "archive_failed", "succeeded", "diff_from_provided"):
        assert (
            _manifest_introduced(
                git_root=repo,
                config_relative=SAMPLE_CONFIG,
                base_status=status,  # type: ignore[arg-type]
                base="HEAD~1",
                head="HEAD",
                changed_files=[SAMPLE_CONFIG.as_posix()],
            )
            is False
        ), status


# --- the fail-safe finding ---------------------------------------------------


def _scan_context(*, manifest_introduced: bool, changed=("shipgate.yaml",)):
    return ScanContext(
        manifest=load_manifest(SAMPLE / "shipgate.yaml"),
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        verification=VerificationContext(
            changed_files=list(changed),
            manifest_introduced=manifest_introduced,
        ),
    )


def test_adoption_fail_safe_keeps_the_check_id_and_severity():
    """Honesty changes; the gate does not."""

    adopting = verify_policy.run(_scan_context(manifest_introduced=True))
    modifying = verify_policy.run(_scan_context(manifest_introduced=False))

    assert len(adopting) == len(modifying) == 1
    assert adopting[0].check_id == modifying[0].check_id == verify_policy.CHECK_ID
    assert adopting[0].severity == modifying[0].severity == "medium"

    assert adopting[0].evidence["kind"] == "manifest_introduced"
    assert modifying[0].evidence["kind"] == "base_snapshot_unavailable"
    assert "weaken" not in adopting[0].title.lower()
    assert "cannot be proven safe" not in adopting[0].title


def test_adoption_fail_safe_still_needs_a_touched_policy_surface():
    context = _scan_context(manifest_introduced=True, changed=("README.md",))
    assert verify_policy.run(context) == []


# --- headline and fix_task copy ---------------------------------------------


def _review(*, policy_weakened=False, trust_root_touched=False):
    return VerifierCapabilityReview(
        policy_weakened=policy_weakened,
        trust_root_touched=trust_root_touched,
    )


def test_adoption_headline_replaces_the_weakening_claim():
    review = _review(policy_weakened=True, trust_root_touched=True)
    adopting = _self_approval_note(review, manifest_introduced=True)
    modifying = _self_approval_note(review, manifest_introduced=False)

    assert adopting is not None and modifying is not None
    assert "introduces Agents Shipgate" in adopting
    assert "human-reviewed PR" in adopting
    assert "weakens the release policy" in modifying


def test_adoption_raises_the_prohibition_on_its_own():
    """``_self_approval_note`` is the "a trust root is in play" probe.

    ``_can_merge_without_human`` raises on a passed decision that carries one.
    Since `policy_weakened` is now honestly `false` during an adoption, the
    adoption has to raise the prohibition by itself — including when no other
    flag is set and when there is no capability review at all.
    """

    for review in (
        None,
        _review(),
        _review(policy_weakened=True),
        _review(trust_root_touched=True),
        _review(policy_weakened=True, trust_root_touched=True),
    ):
        assert _self_approval_note(review, manifest_introduced=True) is not None


def test_non_adoption_wording_is_unchanged():
    assert _self_approval_note(None, manifest_introduced=False) is None
    assert _self_approval_note(_review(), manifest_introduced=False) is None
    assert (
        _self_approval_note(_review(trust_root_touched=True), manifest_introduced=False)
        is not None
    )


def test_adoption_reports_no_policy_weakening_to_machine_consumers(tmp_path):
    """The flag is read as a fact by the registry, attestations, and feedback.

    A run whose headline says "introduces Agents Shipgate" while
    `capability_review.policy_weakened` is true feeds that contradiction to
    every downstream consumer — including feedback's gate-bypass alarm.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    verifier = _run_verify(repo, base="HEAD~1", head="HEAD")

    assert verifier.headline is not None
    assert "introduces Agents Shipgate" in verifier.headline
    assert verifier.capability_review.policy_weakened is False
    # The adoption is still visible as a trust-root touch, which is what keeps
    # reviewer routing and the gate-bypass alarm intact.
    assert verifier.capability_review.trust_root_touched is True
    assert verifier.can_merge_without_human is False

    payload = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text("utf-8")
    )
    assert payload["verifier_summary"]["policy_weakened"] is False


# --- end to end --------------------------------------------------------------


def _run_verify(repo: Path, *, base: str | None, head: str):
    verifier, _report, _exit = run_verify(
        workspace=repo,
        config=SAMPLE_CONFIG,
        base=base,
        head=head,
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
    return verifier


def test_first_adoption_pr_is_honest_and_actionable(tmp_path):
    repo = _repo_adopting_shipgate(tmp_path)
    verifier = _run_verify(repo, base="HEAD~1", head="HEAD")

    assert verifier.base_status == "missing_manifest"
    assert verifier.headline is not None
    assert "introduces Agents Shipgate" in verifier.headline

    # Fail-closed, unchanged: adoption is still a human decision.
    assert verifier.can_merge_without_human is False
    assert verifier.control.state == "human_review_required"

    # A missing-manifest base used to be classified as a safe recovery, which
    # nulled the fix_task: the run said "stop" and named nothing to do.
    fix_task = verifier.fix_task
    assert fix_task is not None
    assert fix_task.actor == "human"
    assert [i for i in fix_task.instructions if "adopts Agents Shipgate" in i]
    assert [r for r in fix_task.allowed_repairs if r.id == "adopt_shipgate_manifest"]

    payload = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text("utf-8")
    )
    weakening = [
        finding
        for finding in payload["findings"]
        if finding["check_id"] == verify_policy.CHECK_ID
    ]
    assert weakening, "the adoption still records a policy finding"
    assert weakening[0]["evidence"]["kind"] == "manifest_introduced"


def test_adopted_repo_gets_the_ordinary_wording_back(tmp_path):
    repo = _repo_adopting_shipgate(tmp_path)
    manifest = repo / "samples" / "support_refund_agent" / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text("utf-8") + "\n# edited after adoption\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "edit the manifest")

    verifier = _run_verify(repo, base="HEAD~1", head="HEAD")
    assert verifier.base_status == "succeeded"
    assert verifier.headline is not None
    assert "introduces Agents Shipgate" not in verifier.headline


def test_a_renamed_manifest_under_any_name_is_not_an_adoption(tmp_path):
    """The name check alone cannot see this.

    A repository may call its manifest anything. `old-gate.yml` renamed to
    `new-gate.yml` — while loosening it — leaves no file called
    `shipgate.yaml` or `new-gate.yml` on the base, so only git's own
    rename/delete detection separates it from a genuine first adoption.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    sample = repo / "samples" / "support_refund_agent"
    _git(repo, "mv", "samples/support_refund_agent/shipgate.yaml", "samples/support_refund_agent/old-gate.yml")
    _git(repo, "commit", "-m", "rename to a custom manifest name")
    _git(repo, "mv", "samples/support_refund_agent/old-gate.yml", "samples/support_refund_agent/new-gate.yml")
    (sample / "new-gate.yml").write_text(
        (sample / "new-gate.yml").read_text("utf-8") + "\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "rename and loosen")

    assert (
        _manifest_introduced(
            git_root=repo,
            config_relative=Path("samples/support_refund_agent/new-gate.yml"),
            base_status="missing_manifest",
            base="HEAD~1",
            head="HEAD",
            changed_files=["samples/support_refund_agent/new-gate.yml"],
        )
        is False
    )


def test_a_manifest_absent_from_the_diff_is_not_an_adoption(tmp_path):
    """"This PR introduces it" has to be literally true.

    It is also what makes `trust_root_touched` structural for every adoption,
    which matters now that `policy_weakened` is honestly false there.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    assert (
        _manifest_introduced(
            git_root=repo,
            config_relative=SAMPLE_CONFIG,
            base_status="missing_manifest",
            base="HEAD~1",
            head="HEAD",
            changed_files=["README.md"],
        )
        is False
    )


def test_an_adoption_that_also_edits_a_policy_pack_keeps_the_strict_wording():
    """"Nothing existed to weaken" is false for a policy file that did exist."""

    context = _scan_context(
        manifest_introduced=True,
        changed=("shipgate.yaml", "policies/refunds.yaml"),
    )
    findings = verify_policy.run(context)
    assert len(findings) == 1
    assert findings[0].evidence["kind"] == "base_snapshot_unavailable"
