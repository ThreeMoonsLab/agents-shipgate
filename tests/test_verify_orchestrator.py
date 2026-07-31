from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from agents_shipgate.cli.verification import assemble, worker
from agents_shipgate.cli.verify import orchestrator as verify_orchestrator
from agents_shipgate.cli.verify.git import commit_date
from agents_shipgate.cli.verify.orchestrator import run_verify
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.static_inputs import StaticInputSnapshot
from agents_shipgate.core.verification_identity import (
    build_terminal_receipt,
    validate_receipt_artifacts,
)
from agents_shipgate.schemas.baseline import BaselineFile
from agents_shipgate.schemas.verification_identity import (
    VerificationPlan,
    VerificationReceipt,
    VerificationUnitResult,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    command_env = os.environ.copy()
    command_env.update(env or {})
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env=command_env,
    )


def test_verify_backdated_commit_cannot_extend_expired_override(tmp_path: Path) -> None:
    """The real verify path must not use a forgeable commit date for expiry."""

    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "samples" / "support_refund_agent", sample_dst)

    wall_clock_today = date.today()
    expired_on = wall_clock_today - timedelta(days=1)
    manifest_path = sample_dst / "shipgate.yaml"
    manifest = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        manifest.replace(
            "checks:\n  ignore:\n",
            "checks:\n"
            "  severity_overrides:\n"
            "    SHIP-DOC-MISSING-DESCRIPTION:\n"
            "      severity: low\n"
            "      reason: expired integration-test review\n"
            f"      expires: {expired_on.isoformat()}\n"
            "  ignore:\n",
        ),
        encoding="utf-8",
    )

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    forged_date = wall_clock_today - timedelta(days=3650)
    forged_timestamp = f"{forged_date.isoformat()}T12:00:00+0000"
    _git(
        repo,
        "commit",
        "-m",
        "backdated head",
        env={
            "GIT_AUTHOR_DATE": forged_timestamp,
            "GIT_COMMITTER_DATE": forged_timestamp,
        },
    )
    assert commit_date(repo, "HEAD") == forged_date.isoformat()

    with pytest.raises(ConfigError, match=r"severity_overrides.*expired"):
        run_verify(
            workspace=repo,
            config=Path("samples/support_refund_agent/shipgate.yaml"),
            base=None,
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


def test_archived_verify_rejects_external_baseline_change_after_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "samples" / "support_refund_agent", sample_dst)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")

    baseline_path = tmp_path / "external-baseline.json"
    baseline_path.write_text(
        BaselineFile(
            created_at="2026-01-01T00:00:00Z",
            source_report_run_id="baseline-test",
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    real_run_scan = verify_orchestrator.run_scan
    changed = False

    def mutate_baseline_after_scan(**kwargs):
        nonlocal changed
        result = real_run_scan(**kwargs)
        if not changed:
            changed = True
            baseline_path.write_text("{}\n", encoding="utf-8")
        return result

    monkeypatch.setattr(verify_orchestrator, "run_scan", mutate_baseline_after_scan)

    with pytest.raises(
        InputParseError,
        match="Verification inputs changed while they were being evaluated",
    ):
        run_verify(
            workspace=repo,
            config=Path("samples/support_refund_agent/shipgate.yaml"),
            base=None,
            head="HEAD",
            archive_head=True,
            out=repo / "agents-shipgate-reports",
            ci_mode="advisory",
            fail_on=None,
            baseline=baseline_path,
            baseline_mode="new-findings",
            diff_from=None,
            policy_packs=None,
            plugins_enabled=False,
            strict_plugins=False,
            suggest_patches=False,
            no_heuristics=False,
            verbose=False,
        )


def test_finalized_snapshot_keeps_deleted_baseline_in_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "samples" / "support_refund_agent", sample_dst)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")

    baseline_path = tmp_path / "external-baseline.json"
    baseline_bytes = (
        BaselineFile(
            created_at="2026-01-01T00:00:00Z",
            source_report_run_id="baseline-test",
        ).model_dump_json(indent=2)
        + "\n"
    ).encode("utf-8")
    baseline_path.write_bytes(baseline_bytes)
    real_finish = StaticInputSnapshot.finish
    deleted = False

    def finish_then_delete(snapshot: StaticInputSnapshot) -> None:
        nonlocal deleted
        real_finish(snapshot)
        if not deleted and snapshot.has(baseline_path):
            deleted = True
            baseline_path.unlink()

    monkeypatch.setattr(StaticInputSnapshot, "finish", finish_then_delete)
    out_dir = repo / "agents-shipgate-reports"

    _verifier, report, _exit_code = run_verify(
        workspace=repo,
        config=Path("samples/support_refund_agent/shipgate.yaml"),
        base=None,
        head="HEAD",
        archive_head=True,
        out=out_dir,
        ci_mode="advisory",
        fail_on=None,
        baseline=baseline_path,
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
    assert deleted is True
    plan = VerificationPlan.model_validate(
        json.loads((out_dir / "verification-plan.json").read_text(encoding="utf-8"))
    )
    assert plan.inputs.baseline is not None
    portable_baseline = out_dir / plan.inputs.baseline.path
    assert portable_baseline.read_bytes() == baseline_bytes


def test_worktree_recursive_source_excludes_verifier_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".codex").mkdir()
    (repo / ".codex" / "config.toml").write_text(
        "[features]\nresponses_websockets = true\n",
        encoding="utf-8",
    )
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: root-config
agent:
  name: root-agent
  declared_purpose:
    - inspect repository config
environment:
  target: local
tool_sources:
  - id: codex
    type: codex_config
    path: .
""".lstrip(),
        encoding="utf-8",
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")

    _verifier, report, _exit_code = run_verify(
        workspace=repo,
        config=Path("shipgate.yaml"),
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

    assert report is not None
    assert (repo / "agents-shipgate-reports" / "verification-plan.json").is_file()


def test_verify_threads_changed_files_into_head_scan(tmp_path):
    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "samples" / "support_refund_agent", sample_dst)
    (repo / "AGENTS.md").write_text("base instructions\n", encoding="utf-8")

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    (repo / "AGENTS.md").write_text("head instructions\n", encoding="utf-8")
    _git(repo, "add", "AGENTS.md")
    _git(repo, "commit", "-m", "touch agent instructions")

    out_dir = repo / "agents-shipgate-reports"
    verifier, report, _exit_code = run_verify(
        workspace=repo,
        config=Path("samples/support_refund_agent/shipgate.yaml"),
        base="HEAD~1",
        head="HEAD",
        archive_head=True,
        out=out_dir,
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
    assert any(
        finding.check_id == "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
        and finding.evidence.get("changed_file") == "AGENTS.md"
        for finding in report.findings
    )
    assert verifier.artifacts["capability_lock_json"] == (
        "agents-shipgate-reports/capabilities.lock.json"
    )
    assert verifier.artifacts["base_capability_lock_json"] == (
        "agents-shipgate-reports/base.capabilities.lock.json"
    )
    assert verifier.artifacts["capability_lock_diff_json"] == (
        "agents-shipgate-reports/capability-lock-diff.json"
    )
    assert (out_dir / "capabilities.lock.json").is_file()
    assert (out_dir / "base.capabilities.lock.json").is_file()
    assert (out_dir / "capability-lock-diff.json").is_file()
    initial_receipt = VerificationReceipt.model_validate(
        json.loads((out_dir / "verification-receipt.json").read_text(encoding="utf-8"))
    )
    initial_verify_run = json.loads(
        (out_dir / "verify-run.json").read_text(encoding="utf-8")
    )
    assert "agent_handoff_json" not in initial_verify_run["artifacts"]
    for name, nested_ref in initial_verify_run["artifacts"].items():
        receipt_ref = initial_receipt.artifact_manifest.artifacts[name]
        assert f"sha256:{nested_ref['sha256']}" == receipt_ref.sha256
    validate_receipt_artifacts(initial_receipt, root=out_dir)
    diff_input = out_dir / "verification-input.diff"
    assert diff_input.is_file()
    distributed_unit = out_dir / "distributed-unit.json"
    worker(
        plan_path=out_dir / "verification-plan.json",
        workspace=repo,
        diff_path=diff_input,
        out=distributed_unit,
    )
    assemble(
        plan_path=out_dir / "verification-plan.json",
        unit_paths=[distributed_unit],
        verifier_path=out_dir / "verifier.json",
        artifacts_root=out_dir,
        out=out_dir / "verification-receipt.json",
    )
    receipt_path = out_dir / "verification-receipt.json"
    receipt = VerificationReceipt.model_validate(
        json.loads(receipt_path.read_text(encoding="utf-8"))
    )
    validate_receipt_artifacts(receipt, root=out_dir)
    first_bytes = receipt_path.read_bytes()
    assemble(
        plan_path=out_dir / "verification-plan.json",
        unit_paths=[distributed_unit],
        verifier_path=out_dir / "verifier.json",
        artifacts_root=out_dir,
        out=receipt_path,
    )
    assert receipt_path.read_bytes() == first_bytes

    verify_run_path = out_dir / "verify-run.json"
    verify_run_payload = json.loads(verify_run_path.read_text(encoding="utf-8"))
    assert "report_json" in verify_run_payload["artifacts"]
    verify_run_payload["artifacts"]["report_json"]["sha256"] = "0" * 64
    verify_run_path.write_text(
        json.dumps(verify_run_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    plan = VerificationPlan.model_validate(
        json.loads((out_dir / "verification-plan.json").read_text(encoding="utf-8"))
    )
    unit = VerificationUnitResult.model_validate(
        json.loads(distributed_unit.read_text(encoding="utf-8"))
    )
    rebound_paths = {
        name: out_dir / ref.path
        for name, ref in receipt.artifact_manifest.artifacts.items()
    }
    tampered_manifest, tampered_receipt = build_terminal_receipt(
        plan=plan,
        unit_results=[unit],
        decision=receipt.decision,
        merge_verdict=receipt.merge_verdict,
        can_merge_without_human=receipt.can_merge_without_human,
        artifact_paths=rebound_paths,
        artifact_root=out_dir,
    )
    (out_dir / "verification-artifacts.json").write_text(
        tampered_manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="verify-run artifact 'report_json' hash disagrees"):
        validate_receipt_artifacts(tampered_receipt, root=out_dir)


def test_verify_threads_uncommitted_worktree_files_into_head_scan(tmp_path):
    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "samples" / "support_refund_agent", sample_dst)
    (repo / "AGENTS.md").write_text("base instructions\n", encoding="utf-8")

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    (repo / "AGENTS.md").write_text("uncommitted instructions\n", encoding="utf-8")
    (repo / ".claude" / "commands").mkdir(parents=True)
    (repo / ".claude" / "commands" / "review.md").write_text("review command\n", encoding="utf-8")
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "marketplace.json").write_text("{}\n", encoding="utf-8")

    _verifier, report, _exit_code = run_verify(
        workspace=repo,
        config=Path("samples/support_refund_agent/shipgate.yaml"),
        base="HEAD",
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

    assert report is not None
    assert any(
        finding.check_id == "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
        and finding.evidence.get("changed_file") == "AGENTS.md"
        for finding in report.findings
    )


def test_verify_fails_closed_when_worktree_diff_cannot_be_collected(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "samples" / "support_refund_agent", sample_dst)

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    def fail_worktree_context(_repo, **_kwargs):
        raise RuntimeError("simulated worktree diff failure")

    monkeypatch.setattr(
        "agents_shipgate.cli.verify.orchestrator.working_tree_context",
        fail_worktree_context,
    )

    verifier, report, exit_code = run_verify(
        workspace=repo,
        config=Path("samples/support_refund_agent/shipgate.yaml"),
        base="HEAD",
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

    assert exit_code == 2
    assert report is None
    assert verifier.head_status == "failed"
    assert verifier.merge_verdict == "unknown"
    assert verifier.can_merge_without_human is False
    assert any("simulated worktree diff failure" in note for note in verifier.base_notes)


def test_verify_pr232_toolkit_bound_removal_blocks(tmp_path):
    """Stripe stripe/ai PR #232: the agent's Stripe tools load via a dynamic
    factory the static extractor cannot enumerate, so the head scan alone reads
    as ``insufficient_evidence``. The base→head removal of the explicit
    ``StripeAgentToolkit(configuration=...)`` allowlist is statically parseable,
    so the full ``verify`` command must return a ``blocked`` merge verdict.
    """
    fixtures = REPO_ROOT / "tests" / "fixtures" / "stripe_pr232"
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(fixtures / "base" / "shipgate.yaml", repo / "shipgate.yaml")
    shutil.copy(fixtures / "base" / "support_agent.py", repo / "support_agent.py")

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base: bounded stripe toolkit")

    # The PR migrates the toolkit to the MCP factory and drops the bound.
    shutil.copy(fixtures / "head" / "support_agent.py", repo / "support_agent.py")
    _git(repo, "add", "support_agent.py")
    _git(repo, "commit", "-m", "head: migrate to MCP factory (drops the bound)")

    verifier, report, _exit_code = run_verify(
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
    assert report.release_decision.decision == "blocked"
    assert verifier.merge_verdict == "blocked"
    assert verifier.human_review.required is True
    [finding] = [
        f for f in report.findings if f.check_id == "SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED"
    ]
    assert finding.severity == "critical"
    assert finding.evidence["kind"] == "scope_bound_removed"
    assert finding.evidence["provider"] == "stripe"
