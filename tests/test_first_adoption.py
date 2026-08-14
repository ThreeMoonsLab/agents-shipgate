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

import pytest

from agents_shipgate.checks import verify_policy
from agents_shipgate.cli.verify.orchestrator import (
    _manifest_introduced,
    _self_approval_note,
    run_verify,
)
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.errors import ConfigError
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


def test_unrelated_base_file_with_custom_basename_does_not_hide_adoption(
    tmp_path: Path,
) -> None:
    """A custom basename is not repository-wide manifest identity."""

    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "release.gate").write_text(
        "ordinary release notes\n",
        encoding="utf-8",
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "ordinary same-basename file")

    manifest = repo / "config" / "release.gate"
    manifest.parent.mkdir()
    manifest.write_text(
        'version: "0.1"\nproject:\n  name: demo\nagent:\n  name: assistant\n',
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "adopt custom gate")

    assert (
        _manifest_introduced(
            git_root=repo,
            config_relative=Path("config/release.gate"),
            base_status="missing_manifest",
            base="HEAD~1",
            head="HEAD",
            changed_files=["config/release.gate"],
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
            configured_manifest_path="shipgate.yaml",
            manifest_introduced=manifest_introduced,
        ),
    )


def test_adoption_fail_safe_keeps_the_severity_and_its_own_reason_code():
    """Honesty changes; the gate does not.

    Both no-base cases now carry the fail-safe's own reason code rather than
    the base-relative weakening one — see ``test_headline_ranking`` for the
    split's own regressions. Severity, and therefore the verdict, is unmoved.
    """

    adopting = verify_policy.run(_scan_context(manifest_introduced=True))
    modifying = verify_policy.run(_scan_context(manifest_introduced=False))

    assert len(adopting) == len(modifying) == 1
    assert (
        adopting[0].check_id
        == modifying[0].check_id
        == verify_policy.BASE_ABSENT_CHECK_ID
    )
    assert verify_policy.BASE_ABSENT_CHECK_ID != verify_policy.CHECK_ID
    assert adopting[0].severity == modifying[0].severity == "medium"

    assert adopting[0].evidence["kind"] == "manifest_introduced"
    assert modifying[0].evidence["kind"] == "base_snapshot_unavailable"
    assert "weaken" not in adopting[0].title.lower()
    assert "cannot be proven safe" not in adopting[0].title


def test_adoption_fail_safe_still_needs_a_touched_policy_surface():
    context = _scan_context(manifest_introduced=True, changed=("README.md",))
    assert verify_policy.run(context) == []


def test_policy_fail_safe_uses_exact_logical_manifest_identity():
    context = _scan_context(
        manifest_introduced=False,
        changed=("release.gate",),
    )
    context.config_path = Path("/tmp/archive/config/release.gate")
    context.verification.configured_manifest_path = "config/release.gate"

    assert verify_policy.run(context) == []

    context.verification.changed_files = ["config/release.gate"]
    findings = verify_policy.run(context)
    assert len(findings) == 1
    assert findings[0].evidence["changed_policy_files"] == [
        "config/release.gate"
    ]


# --- headline and fix_task copy ---------------------------------------------


def _review(*, policy_weakened=False, trust_root_touched=False):
    return VerifierCapabilityReview(
        policy_weakened=policy_weakened,
        trust_root_touched=trust_root_touched,
    )


def test_adoption_headline_replaces_the_weakening_claim():
    review = _review(trust_root_touched=True)
    adopting = _self_approval_note(
        review,
        manifest_introduced=True,
        pure_adoption_review=True,
        configured_manifest="config/release.gate",
    )
    modifying = _self_approval_note(
        _review(policy_weakened=True), manifest_introduced=False
    )

    assert adopting is not None and modifying is not None
    assert "introduces Agents Shipgate" in adopting
    assert "config/release.gate" in adopting
    assert "shipgate.yaml" not in adopting
    assert "agent-instruction" not in adopting
    assert "CI files" not in adopting
    assert "human-reviewed PR" in adopting
    assert "weakens the release policy" in modifying


def test_adoption_raises_the_prohibition_after_a_completed_scan():
    """``_self_approval_note`` is the "a trust root is in play" probe.

    ``_can_merge_without_human`` raises on a passed decision that carries one.
    Since `policy_weakened` is now honestly `false` during an adoption, the
    adoption has to raise the prohibition by itself when a completed scan
    supplies a capability review. Before that point the scan-failure route is
    authoritative and adoption wording would be unsupported.
    """

    for review in (
        _review(),
        _review(policy_weakened=True),
        _review(trust_root_touched=True),
        _review(policy_weakened=True, trust_root_touched=True),
    ):
        assert (
            _self_approval_note(
                review,
                manifest_introduced=True,
                pure_adoption_review=True,
            )
            is not None
        )
    assert _self_approval_note(None, manifest_introduced=True) is None


def test_a_weakened_policy_beats_the_adoption_wording():
    """Introducing the manifest while weakening an existing policy file.

    "There is no prior gate this change could weaken" would describe away the
    very finding that needs review.
    """

    mixed = _self_approval_note(
        _review(policy_weakened=True, trust_root_touched=True),
        manifest_introduced=True,
        pure_adoption_review=True,
    )
    assert mixed is not None
    assert "weakens the release policy" in mixed
    assert "introduces Agents Shipgate" not in mixed


def test_non_adoption_wording_is_unchanged():
    assert _self_approval_note(None, manifest_introduced=False) is None
    assert _self_approval_note(_review(), manifest_introduced=False) is None
    assert (
        _self_approval_note(_review(trust_root_touched=True), manifest_introduced=False)
        is not None
    )


def test_adoption_reports_no_policy_weakening_to_machine_consumers(tmp_path):
    """The flag is read as a fact by the registry, attestations, and feedback.

    An adoption that has other blockers leads with those blockers, but its
    machine-readable policy fact must still say no existing gate was weakened.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    verifier = _run_verify(repo, base="HEAD~1", head="HEAD")

    assert verifier.headline is not None
    assert verifier.headline.startswith("5 active finding(s) block release")
    assert "also introduces the configured manifest" in verifier.headline
    assert "then merge" not in verifier.headline
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


def _run_verify(repo: Path, *, base: str | None, head: str, config: Path | None = None):
    verifier, _report, _exit = run_verify(
        workspace=repo,
        config=config or SAMPLE_CONFIG,
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
    assert verifier.headline.startswith("5 active finding(s) block release")
    assert "also introduces the configured manifest" in verifier.headline
    assert "then merge" not in verifier.headline

    # Fail-closed, unchanged: adoption is still a human decision.
    assert verifier.can_merge_without_human is False
    assert verifier.control.state == "human_review_required"

    # A missing-manifest base used to be classified as a safe recovery, which
    # nulled the fix_task: the run said "stop" and named nothing to do.
    fix_task = verifier.fix_task
    assert fix_task is not None
    assert fix_task.actor == "human"
    assert [
        i
        for i in fix_task.instructions
        if "also introduces the configured manifest" in i
    ]
    assert "merge" not in " ".join(fix_task.instructions).lower()
    assert [
        r for r in fix_task.allowed_repairs if r.id == "review_shipgate_adoption"
    ]
    assert not [
        r for r in fix_task.allowed_repairs if r.id == "adopt_shipgate_manifest"
    ]

    payload = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text("utf-8")
    )
    policy_findings = [
        finding
        for finding in payload["findings"]
        if finding["check_id"] == verify_policy.BASE_ABSENT_CHECK_ID
    ]
    assert policy_findings, "the adoption still records a policy finding"
    assert policy_findings[0]["evidence"]["kind"] == "manifest_introduced"
    assert not [
        finding
        for finding in payload["findings"]
        if finding["check_id"] == verify_policy.CHECK_ID
    ], "an adoption weakens no base-relative policy and must not claim one"


def test_clean_adoption_collapses_same_manifest_rows_into_one_human_act(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "clean-repo"
    shutil.copytree(REPO_ROOT / "samples" / "clean_read_only_agent", repo)
    manifest = repo / "shipgate.yaml"
    held_back = manifest.read_text(encoding="utf-8")
    manifest.unlink()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "before shipgate")
    manifest.write_text(held_back, encoding="utf-8")
    _git(repo, "add", "shipgate.yaml")
    _git(repo, "commit", "-m", "adopt shipgate")

    verifier, report, _exit = run_verify(
        workspace=repo,
        config=Path("shipgate.yaml"),
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

    assert report is not None
    assert report.release_decision is not None
    assert report.release_decision.decision == "review_required"
    assert verifier.headline is not None
    assert "introduces Agents Shipgate" in verifier.headline
    assert "then merge" in verifier.headline
    assert verifier.fix_task is not None
    assert "adopt_shipgate_manifest" in {
        repair.id for repair in verifier.fix_task.allowed_repairs
    }
    assert not {
        "review_shipgate_adoption",
        "review_trust_root",
    } & {repair.id for repair in verifier.fix_task.allowed_repairs}


def test_failed_head_scan_never_emits_adoption_or_merge_guidance(tmp_path):
    repo = _repo_adopting_shipgate(tmp_path)
    manifest = repo / SAMPLE_CONFIG
    manifest.write_text("version: [\n", encoding="utf-8")
    _git(repo, "add", SAMPLE_CONFIG.as_posix())
    _git(repo, "commit", "--amend", "--no-edit")

    with pytest.raises(ConfigError):
        _run_verify(repo, base="HEAD~1", head="HEAD")

    payload = json.loads(
        (repo / "agents-shipgate-reports" / "verifier.json").read_text("utf-8")
    )
    assert payload["execution"] == "failed"
    assert payload["headline"] == (
        "Shipgate could not complete the scan; human review required."
    )
    assert "introduces Agents Shipgate" not in json.dumps(payload["control"])
    assert "then merge" not in json.dumps(payload["control"])
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["verify_required"] is True
    assert payload["control"]["next_action"]["kind"] == "stop"
    assert payload["fix_task"]["actor"] == "human"
    assert "re-run before merge" in " ".join(payload["fix_task"]["instructions"])


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


def test_adoption_finding_names_the_exact_configured_manifest():
    context = _scan_context(manifest_introduced=True, changed=("config/release.gate",))
    context.config_path = Path("/temporary/archive/config/release.gate")
    assert context.verification is not None
    context.verification.configured_manifest_path = "config/release.gate"

    findings = verify_policy.run(context)

    assert len(findings) == 1
    assert "'config/release.gate'" in findings[0].recommendation
    assert "shipgate.yaml" not in findings[0].recommendation
    assert "generated" not in findings[0].recommendation


def test_a_custom_named_manifest_is_still_a_trust_root(tmp_path):
    """The gate is whatever file the run loaded as the gate.

    The trust-root table only knows `**/shipgate.yaml`, so a repository run
    with `--config new-gate.yml` had no manifest trust root at all: its gate
    could be introduced or rewritten with an empty release substrate behind an
    adoption headline that claimed a human was required.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    sample = repo / "samples" / "support_refund_agent"
    _git(
        repo,
        "mv",
        "samples/support_refund_agent/shipgate.yaml",
        "samples/support_refund_agent/new-gate.yml",
    )
    _git(repo, "commit", "-m", "custom manifest name")
    # Roll the manifest back out of the base so this reads as an adoption.
    _git(repo, "reset", "--soft", "HEAD~2")
    _git(repo, "reset")
    (sample / "shipgate.yaml").unlink(missing_ok=True)

    verifier = _run_verify_worktree(repo, "samples/support_refund_agent/new-gate.yml")

    assert verifier.capability_review.trust_root_touched is True
    assert verifier.can_merge_without_human is False
    assert verifier.control.state != "complete"
    assert verifier.headline is not None
    assert "samples/support_refund_agent/new-gate.yml" in verifier.headline
    assert "generated shipgate.yaml" not in verifier.headline
    assert "agent-instruction and CI files" not in verifier.headline
    assert verifier.fix_task is not None
    instructions = " ".join(verifier.fix_task.instructions)
    assert "samples/support_refund_agent/new-gate.yml" in instructions
    assert "generated shipgate.yaml" not in instructions
    assert "agent-instruction and CI files" not in instructions


def _run_verify_worktree(repo: Path, config: str):
    verifier, _report, _exit = run_verify(
        workspace=repo,
        config=Path(config),
        base=None,
        head="HEAD",
        archive_head=False,
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


def test_the_rerun_command_repeats_the_whole_request(tmp_path):
    """A rerun that drops options evaluates a different question."""

    repo = _repo_adopting_shipgate(tmp_path)
    pack = repo / "extra-policy.yaml"
    pack.write_text("version: 1\nrules: []\n", encoding="utf-8")

    verifier, _report, _exit = run_verify(
        workspace=repo,
        config=SAMPLE_CONFIG,
        base=None,
        head="HEAD",
        archive_head=False,
        out=repo / "agents-shipgate-reports",
        ci_mode="strict",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=True,
        verbose=False,
        auto_base=False,
    )

    command = (verifier.fix_task or {}) and verifier.fix_task.verification_command
    assert command is not None
    # --no-base matters most: without it the rerun auto-detects a base the
    # evaluated run never compared against.
    assert "--no-base" in command
    assert "--ci-mode strict" in command
    assert "--no-heuristics" in command
    assert "--no-plugins" in command
    assert f"--config {SAMPLE_CONFIG.as_posix()}" in command


def test_custom_manifest_evidence_is_deterministic(tmp_path):
    """Evidence is hashed into the fingerprint, so it cannot carry a temp path.

    A committed-head run loads its manifest from a freshly named
    `agents-shipgate-verify-head-*` archive; returning that resolved path made
    two identical runs produce different fingerprints and report run ids.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    _git(
        repo,
        "mv",
        "samples/support_refund_agent/shipgate.yaml",
        "samples/support_refund_agent/new-gate.yml",
    )
    _git(repo, "commit", "-m", "custom manifest name")

    config = Path("samples/support_refund_agent/new-gate.yml")
    first = _run_verify(repo, base="HEAD~1", head="HEAD", config=config)
    second = _run_verify(repo, base="HEAD~1", head="HEAD", config=config)

    payloads = [
        json.loads((repo / "agents-shipgate-reports" / "report.json").read_text("utf-8"))
    ]
    assert first.request_id == second.request_id
    evidence = [
        finding["evidence"]
        for finding in payloads[0]["findings"]
        if finding["check_id"] == "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
    ]
    assert evidence, payloads[0]["findings"]
    for item in evidence:
        assert "agents-shipgate-verify-head-" not in json.dumps(item)


def test_a_base_that_keeps_another_manifest_is_not_an_adoption(tmp_path):
    """Absence has to be established, not inferred from two basenames.

    A base that retains an operational `old-gate.json` deletes nothing and
    matches no name check. ``load_manifest`` accepts its YAML content despite
    the suffix, so only a suffix-agnostic content probe separates it from a
    genuine first adoption.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    sample = repo / "samples" / "support_refund_agent"
    _git(
        repo,
        "mv",
        "samples/support_refund_agent/shipgate.yaml",
        "samples/support_refund_agent/old-gate.json",
    )
    _git(repo, "commit", "-m", "base keeps a custom manifest")
    (sample / "new-gate.yml").write_text(
        (sample / "old-gate.json").read_text("utf-8"), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add a second manifest without removing the first")

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


def test_a_quoted_key_manifest_on_the_base_blocks_the_adoption_claim(tmp_path):
    """A text probe for `^project:` misses a manifest that loads fine.

    Quoted keys, flow style, and indentation all parse to the same document,
    so the retained-manifest check has to parse rather than grep.
    """

    import yaml

    repo = _repo_adopting_shipgate(tmp_path)
    sample = repo / "samples" / "support_refund_agent"
    document = yaml.safe_load((sample / "shipgate.yaml").read_text("utf-8"))
    (sample / "shipgate.yaml").unlink()
    (sample / "old-gate.yml").write_text(
        yaml.safe_dump(document, default_style='"'), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base keeps a quoted-key manifest")
    (sample / "new-gate.yml").write_text(
        (sample / "old-gate.yml").read_text("utf-8"), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add a second manifest")

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


def test_equivalent_config_spellings_resolve_to_the_same_manifest():
    """Raw suffix comparison was bypassable by an equivalent path spelling."""

    from agents_shipgate.core.trust_roots import is_configured_manifest

    assert is_configured_manifest("docs/engineering/../manifest.yaml", "docs/manifest.yaml")
    assert is_configured_manifest("/repo/sub/gate.yml", "sub/gate.yml", workspace="/repo")
    # With a workspace the comparison is exact, so a same-named file elsewhere
    # is not the gate.
    assert not is_configured_manifest("/repo/x/sub/gate.yml", "sub/gate.yml", workspace="/repo")
    assert not is_configured_manifest("/repo/new-gate.yml", "README.md")


# --- archived scan identity --------------------------------------------------


def test_archived_verify_keeps_the_nested_configured_manifest_identity(tmp_path):
    """The scan archive path must not hide the configured manifest from boundary checks.

    ``run_verify(..., archive_head=True)`` loads this manifest from a temporary
    ``<archive>/head/...`` path while the diff remains repository-relative.
    The stable identity on ``VerificationContext`` keeps both coordinate
    systems aligned.
    """

    repo = _repo_adopting_shipgate(tmp_path)
    custom = Path("samples/support_refund_agent/new-gate.yml")
    _git(
        repo,
        "mv",
        SAMPLE_CONFIG.as_posix(),
        custom.as_posix(),
    )
    _git(repo, "commit", "-m", "move gate to a custom nested path")

    _run_verify(repo, base="HEAD~1", head="HEAD", config=custom)

    payload = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text("utf-8")
    )
    boundary = [
        finding
        for finding in payload["findings"]
        if finding["check_id"]
        == "SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
        and finding.get("source", {}).get("path") == custom.as_posix()
    ]
    assert boundary, payload["findings"]
    assert boundary[0]["evidence"]["trust_root_class"] == "manifest"
    assert "agents-shipgate-verify-head-" not in json.dumps(boundary[0])
