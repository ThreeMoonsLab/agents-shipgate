from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agents_shipgate.cli.verify.orchestrator import run_verify

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


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

    _verifier, report, _exit_code = run_verify(
        workspace=repo,
        config=Path("samples/support_refund_agent/shipgate.yaml"),
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
    assert any(
        finding.check_id == "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
        and finding.evidence.get("changed_file") == "AGENTS.md"
        for finding in report.findings
    )


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
