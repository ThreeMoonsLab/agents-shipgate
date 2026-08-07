from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify import git as verify_git
from agents_shipgate.cli.verify import orchestrator as verify_orchestrator
from agents_shipgate.cli.verify.capability_review import build_capability_review
from agents_shipgate.cli.verify.fix_task import build_fix_task
from agents_shipgate.cli.verify.git import carries_manifest_like_yaml, read_file_at_ref
from agents_shipgate.cli.verify.orchestrator import _prune_base_scan_cache, _rerun_options
from agents_shipgate.cli.verify.pr_comment import render_pr_comment
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.trust_roots import PathIdentityIssue
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.capabilities import (
    CapabilityLockFileV1,
    CapabilityLockHashes,
    CapabilityLockSource,
    CapabilityLockSummary,
)
from agents_shipgate.schemas.capability_change import (
    CapabilityChangeBlock,
    CapabilityChangeMember,
    ProtectedSurfaceChange,
    VerifierCapabilityDeltaSummary,
    VerifierSummary,
)
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.patches import RemovePointerPatch
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
from agents_shipgate.schemas.surfaces import (
    ActionSurfaceChange,
    ActionSurfaceDiff,
    ActionSurfaceDiffSummary,
)
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierCapabilityReview,
    VerifierDiffStatus,
    VerifierFixTask,
    VerifierRepair,
)

runner = CliRunner()


def test_verify_rejects_a_retargeted_tracked_config_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    repo = _init_repo(tmp_path)
    _write_manifest(repo)
    strict = repo / "strict.yml"
    (repo / "shipgate.yaml").rename(strict)
    weak = repo / "weak.yml"
    weak.write_text(strict.read_text("utf-8"), encoding="utf-8")
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    gate = repo / "gate.yml"
    gate.symlink_to("strict.yml")
    _commit_all(repo, "tracked strict gate")

    gate.unlink()
    gate.symlink_to("weak.yml")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "gate.yml",
            "--no-base",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--config must not contain symlink components: gate.yml" in result.output
    assert "weak.yml" not in result.output
    assert not (repo / "agents-shipgate-reports" / "verifier.json").exists()
    json_lines = [line for line in result.output.splitlines() if line.startswith("{")]
    payload = json.loads(json_lines[-1])
    assert len(payload["next_actions"]) == 1
    action = payload["next_actions"][0]
    assert action["kind"] == "review"
    assert action["command"] is None
    assert action["path"] is None


@pytest.mark.parametrize("flag", ["--policy-pack", "--baseline"])
def test_ref_bound_verify_rejects_retargeted_static_input_symlinks(
    tmp_path: Path,
    flag: str,
) -> None:
    repo = _init_repo(tmp_path)
    _write_manifest(repo)
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    strict = repo / "strict-input.yml"
    weak = repo / "weak-input.yml"
    strict.write_text("version: 1\n", encoding="utf-8")
    weak.write_text("version: 1\n", encoding="utf-8")
    _commit_all(repo, "tracked verifier inputs")

    strict.unlink()
    strict.symlink_to(weak.name)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--head",
            "HEAD",
            "--no-base",
            flag,
            strict.name,
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    assert f"{flag} must not contain symlink components" in result.output
    assert not (repo / "agents-shipgate-reports" / "verifier.json").exists()


def test_verify_rejects_a_filesystem_resolved_config_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    _write_manifest(repo)
    actual = repo / "new-gate.yml"
    (repo / "shipgate.yaml").rename(actual)
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "custom manifest")
    alias = repo / "NEW-GATE.yml"
    real_lstat = Path.lstat

    def aliased_lstat(path: Path, *args, **kwargs):
        if path == alias:
            return real_lstat(actual, *args, **kwargs)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", aliased_lstat)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            alias.name,
            "--no-base",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--config must use the exact filesystem spelling" in result.output
    assert "NEW-GATE.yml resolves to new-gate.yml" in result.output
    assert not (repo / "agents-shipgate-reports" / "verifier.json").exists()


def test_archive_alias_allowance_requires_a_proven_same_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = tmp_path / "Gate.gate"
    monkeypatch.setattr(
        verify_orchestrator,
        "inspect_lexical_path_identity",
        lambda _root, _relative: PathIdentityIssue(
            kind="alias",
            requested=requested,
            actual=None,
        ),
    )

    with pytest.raises(
        ConfigError,
        match=r"must use the exact filesystem spelling",
    ):
        verify_orchestrator._reject_symlink_components(
            tmp_path,
            Path("Gate.gate"),
            label="Head manifest Gate.gate",
            allow_filesystem_alias=True,
        )


def test_tree_identity_rejects_portable_collision_even_on_an_exact_hit(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    parent = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        check=True,
        input=b'version: "0.1"\n',
        capture_output=True,
    ).stdout.decode("ascii").strip()
    tree_input = b"".join(
        f"100644 blob {blob}\t{name}".encode() + b"\0"
        for name in sorted(("GATE.yml", "gate.yml"))
    )
    tree = subprocess.run(
        ["git", "mktree", "-z"],
        cwd=repo,
        check=True,
        input=tree_input,
        capture_output=True,
    ).stdout.decode("ascii").strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-p", parent, "-m", "portable collision"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(ConfigError, match="filesystem-colliding paths"):
        verify_git.resolve_tree_path_identity(repo, commit, Path("gate.yml"))


def test_verify_accepts_absolute_config_under_direct_nested_workspace_alias(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    nested = repo / "services" / "api"
    nested.mkdir(parents=True)
    _write_manifest(repo)
    (repo / "shipgate.yaml").rename(nested / "gate.yml")
    (nested / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "nested manifest")
    alias = tmp_path / "api-alias"
    alias.symlink_to(nested, target_is_directory=True)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(alias),
            "--config",
            str(alias / "gate.yml"),
            "--no-base",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "succeeded"
    assert (repo / "agents-shipgate-reports" / "report.json").is_file()


def test_diff_context_retains_both_sides_of_a_rename(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    policy = repo / "policies" / "review.yml"
    policy.parent.mkdir()
    policy.write_text("review: required\n", encoding="utf-8")
    _commit_all(repo, "policy")
    subprocess.run(
        ["git", "mv", "policies/review.yml", "retired.txt"],
        cwd=repo,
        check=True,
    )
    _commit_all(repo, "retire policy")

    changed_files, _diff = verify_git.diff_context(repo, "HEAD~1", "HEAD")

    assert changed_files == ["policies/review.yml", "retired.txt"]


def test_verify_manifest_present_force_runs_even_docs_only_diff(tmp_path: Path) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")
    out_dir = repo / "agents-shipgate-reports"
    out_dir.mkdir()
    (out_dir / "report.json").write_text('{"stale": true}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "succeeded"
    assert payload["trigger"]["run_shipgate"] is True
    assert payload["trigger"]["force_run"] is True
    verifier_json = repo / "agents-shipgate-reports" / "verifier.json"
    pr_comment = repo / "agents-shipgate-reports" / "pr-comment.md"
    assert verifier_json.is_file()
    assert pr_comment.is_file()
    assert (repo / "agents-shipgate-reports" / "report.json").is_file()
    assert "report_json" in payload["artifacts"]
    assert payload["base_status"] == "succeeded"


def test_no_base_worktree_runs_exclude_their_own_outputs_from_identity(
    tmp_path: Path,
) -> None:
    repo = _repo_with_manifest(tmp_path)
    (repo / "tools.json").write_text(
        '{"tools":[{"name":"docs.lookup","description":"Read docs."}]}\n',
        encoding="utf-8",
    )
    args = [
        "verify",
        "--workspace",
        str(repo),
        "--config",
        "shipgate.yaml",
        "--no-base",
        "--json",
    ]

    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)
    first_diff = (
        repo / "agents-shipgate-reports" / "verification-input.diff"
    ).read_bytes()

    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)
    second_diff = (
        repo / "agents-shipgate-reports" / "verification-input.diff"
    ).read_bytes()

    assert first_payload["changed_files"] == second_payload["changed_files"]
    assert first_payload["changed_files"] == ["tools.json"]
    assert not any(
        path.startswith("agents-shipgate-reports/")
        for path in second_payload["changed_files"]
    )
    assert first_diff == second_diff
    for field in ("request_id", "subject_id", "input_set_id", "decision_id"):
        assert first_payload[field] == second_payload[field]


def test_verify_rejects_an_index_hidden_source_in_worktree_mode(
    tmp_path: Path,
) -> None:
    repo = _repo_with_manifest(tmp_path)
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "tools.json"],
        cwd=repo,
        check=True,
    )
    (repo / "tools.json").write_text(
        '{"tools":[{"name":"hidden.delete","description":"Delete data."}]}\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--no-base",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "failed"
    assert payload["control"]["state"] == "human_review_required"
    assert "Git index flags hide paths from worktree collection" in " ".join(
        payload["base_notes"]
    )
    assert not (repo / "agents-shipgate-reports" / "report.json").exists()


def test_verify_missing_config_docs_only_diff_fails_closed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")
    out_dir = repo / "agents-shipgate-reports"
    out_dir.mkdir()
    (out_dir / "report.json").write_text('{"stale": true}\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "missing.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "failed"
    assert payload["head_exit_code"] == 2
    assert payload["merge_verdict"] == "unknown"
    assert payload["applicability"] == "failed"
    assert payload["can_merge_without_human"] is False
    assert payload["release_decision"] is None
    assert "correct --config" in payload["headline"].lower()
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["must_stop"] is False
    assert payload["control"]["human_review"]["required"] is False
    assert payload["control"]["next_action"]["command"] == (
        f"agents-shipgate verify --workspace {repo} --config missing.yaml "
        "--preview --base origin/main --head HEAD --json"
    )
    assert (out_dir / "verifier.json").is_file()
    assert not (out_dir / "verify-run.json").exists()
    assert not (out_dir / "verification-receipt.json").exists()
    assert (out_dir / "agent-handoff.json").is_file()
    assert (out_dir / "pr-comment.md").is_file()
    assert not (out_dir / "agent-result.json").exists()
    assert not (out_dir / "report.json").exists()


def test_verify_json_missing_config_emits_verifier_unknown(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "missing.yaml",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["merge_verdict"] == "unknown"
    assert payload["applicability"] == "failed"
    assert payload["head_status"] == "failed"
    assert payload["head_exit_code"] == 2
    assert payload["can_merge_without_human"] is False
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["command"] == (
        f"agents-shipgate verify --workspace {repo} --config missing.yaml "
        "--preview --json"
    )
    assert "schema_version" not in payload
    assert not (repo / "agents-shipgate-reports" / "report.json").exists()


def test_missing_config_preview_recovery_preserves_custom_request(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "README.md").write_text("head\n", encoding="utf-8")
    _commit_all(repo, "head")
    out = repo / "custom reports"

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "config/custom gate.yml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--out",
            str(out),
            "--pr-comment-style",
            "findings",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["control"]["next_action"]["command"] == (
        f"agents-shipgate verify --workspace {repo} "
        "--config 'config/custom gate.yml' --preview --base origin/main "
        f"--head HEAD --out '{out}' --pr-comment-style findings --json"
    )


def test_verify_missing_config_relevant_diff_fails_before_head_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "tools.json").write_text(
        '{"tools":[{"name":"delete_files","description":"Delete files."}]}\n',
        encoding="utf-8",
    )
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "missing.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "failed"
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False
    assert calls == []


def test_verify_warns_when_reports_directory_is_staged(tmp_path: Path) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")

    # Simulate the W24 footgun: the agent ran a scan/verify and then
    # `git add`-ed the generated reports directory.
    reports = repo / "agents-shipgate-reports"
    reports.mkdir()
    (reports / "report.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "agents-shipgate-reports/report.json"], cwd=repo, check=True)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    # Advisory only: the staged-reports nudge never changes the verdict or
    # the exit code.
    assert result.exit_code == 0, result.output
    assert "warning:" in result.output
    assert "agents-shipgate-reports/" in result.output
    assert "git restore --staged" in result.output


def test_verify_warns_on_staged_reports_from_subdirectory_workspace(
    tmp_path: Path,
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")

    # Reports staged at the GIT ROOT, where verify writes them...
    reports = repo / "agents-shipgate-reports"
    reports.mkdir()
    (reports / "report.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "agents-shipgate-reports/report.json"], cwd=repo, check=True)

    # ...but verify is invoked with a subdirectory --workspace. The nudge must
    # still resolve to the git root rather than probing only the subdirectory.
    subdir = repo / "service"
    subdir.mkdir()

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(subdir),
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "warning:" in result.output
    assert "agents-shipgate-reports/" in result.output


def test_verify_no_staged_reports_warning_and_stdout_json_is_clean(
    tmp_path: Path,
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    # Verify writes verifier.json/pr-comment.md into the reports dir during
    # the run, but never stages them, so no nudge fires and stdout stays
    # pure JSON for agent consumers.
    assert "report file(s) staged" not in result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "succeeded"


def test_verify_missing_config_takes_precedence_over_missing_base(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "base")
    (repo / "tools.json").write_text(
        '{"tools":[{"name":"delete_files","description":"Delete files."}]}\n',
        encoding="utf-8",
    )
    _commit_all(repo, "head")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "not_requested"
    assert payload["head_status"] == "failed"
    assert payload["merge_verdict"] == "unknown"
    assert payload["applicability"] == "failed"
    assert payload["can_merge_without_human"] is False
    assert payload["control"]["state"] == "agent_action_required"
    assert not (repo / "agents-shipgate-reports" / "report.json").exists()


def test_verify_non_git_workspace_exits_config_error(tmp_path: Path) -> None:
    workspace = tmp_path / "not-git"
    workspace.mkdir()

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(workspace), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 2
    assert "Workspace is not inside a git checkout" in result.output


def test_verify_explicit_head_scans_ref_archive_not_dirty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text(
        '{"tools":[{"name":"delete_files"}]}\n',
        encoding="utf-8",
    )
    calls: list[dict[str, Any]] = []

    def fake_run_scan(**kwargs: Any):
        calls.append(kwargs)
        scan_root = Path(kwargs["config_path"]).parent
        assert scan_root != repo
        assert "delete_files" not in (scan_root / "tools.json").read_text(encoding="utf-8")
        report = _report(decision="passed", exit_code=0)
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(report_json_payload(report), indent=2),
            encoding="utf-8",
        )
        return report, 0

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--head",
            "origin/main",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["changed_files"] == []
    assert len(calls) == 1


def test_verify_real_base_scan_enables_head_diff(tmp_path: Path) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up internal documentation metadata.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""".lstrip(),
        encoding="utf-8",
    )
    _commit_all(repo, "add tool")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "succeeded"
    report_payload = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text(encoding="utf-8")
    )
    assert report_payload["tool_surface_diff"]["enabled"] is True
    assert report_payload["tool_surface_diff"]["summary"]["tools_added"] == 0
    assert len(report_payload["tool_catalog"]) == 1
    assert report_payload["release_decision"]["decision"] == "insufficient_evidence"


def test_pr_comment_keeps_code_span_values_unescaped() -> None:
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        base_ref="origin/main",
        head_ref="HEAD",
        trigger={"rationale": "docs-only"},
        base_status="cache_hit",
        execution="skipped",
        head_status="skipped",
        merge_verdict="mergeable",
        applicability="not_applicable",
        can_merge_without_human=True,
        control=derive_agent_control(reason="No applicable changes."),
        artifacts={
            "report_markdown": "agents-shipgate-reports/report.md",
            "verifier_json": "agents-shipgate-reports/verifier.json",
        },
    )

    comment = render_pr_comment(verifier, report=None, style="findings")

    assert "`cache_hit`" in comment
    assert "cache\\_hit" not in comment
    assert "`agents-shipgate-reports/report.md`" in comment
    assert "agents\\-shipgate\\-reports" not in comment
    assert "workflow artifact" in comment


def test_capability_review_pr_comment_leads_with_top_changes_and_trust_root() -> None:
    report = _report(decision="blocked", exit_code=20)
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(actions_added=1, blocking_findings=1),
        added=[
            ActionSurfaceChange(
                type="ACTION_ADDED",
                action_id="refund",
                tool_name="stripe.create_refund",
                operation="create_refund",
                severity="critical",
                reason="Action added: stripe.create_refund",
            )
        ],
    )
    report.findings = [
        Finding(
            id="F-action",
            check_id="SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
            title="refund action lacks controls",
            severity="critical",
            category="action_surface",
            tool_name="stripe.create_refund",
            evidence={"action_id": "refund"},
            recommendation="Declare approval and idempotency.",
            blocks_release=True,
        ),
        Finding(
            id="F-trust",
            check_id="SHIP-VERIFY-TRUST-ROOT-TOUCHED",
            title="Release trust root touched: shipgate.yaml",
            severity="medium",
            category="verify",
            evidence={
                "changed_file": "shipgate.yaml",
                "trust_root_class": "manifest",
            },
            recommendation="Human review required.",
        ),
    ]
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="cap-refund",
                direction="added",
                subject_kind="action",
                tool="stripe.create_refund",
                action="refund",
                release_impact="blocks_release",
                rationale="Action added: stripe.create_refund",
                related_finding_ids=["F-action"],
            )
        ],
    )
    report.protected_surface_changes = [
        ProtectedSurfaceChange(
            path="shipgate.yaml",
            kind="manifest",
            related_finding_ids=["F-trust"],
        )
    ]
    report.verifier_summary = VerifierSummary(
        verdict="blocked",
        capability_delta_summary=VerifierCapabilityDeltaSummary(added=1),
        protected_surface_touched=True,
        policy_weakened=False,
    )
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        execution="succeeded",
        head_status="succeeded",
        release_decision=report.release_decision,
        decision="blocked",
        merge_verdict="blocked",
        applicability="verified",
        control=_human_control("blocked"),
        headline="This PR adds a refund action without approval evidence.",
        capability_review=build_capability_review(report),
        fix_task=VerifierFixTask(
            actor="human",
            safe_to_attempt=False,
            instructions=["A human owner must confirm approval and idempotency evidence."],
            forbidden_shortcuts=[],
            verification_command="agents-shipgate verify --base origin/main --head HEAD --json",
        ),
        artifacts={
            "report_json": "agents-shipgate-reports/report.json",
            "packet_json": "agents-shipgate-reports/packet.json",
            "verifier_json": "agents-shipgate-reports/verifier.json",
        },
    )

    comment = render_pr_comment(verifier, report=report)

    assert "## Agents Shipgate" in comment
    assert comment.count("### ") == 2
    assert "### Human summary" in comment
    assert "### Agent instruction block" in comment
    assert "- Merge verdict: `blocked`" in comment
    assert "Summary: This PR adds a refund action without approval evidence" in comment
    assert "- Release gate: `blocked`" in comment
    assert "- Reason: test decision" in comment
    assert "- Capability delta: +1, 0 modified, -0" in comment
    assert "`stripe.create_refund`: blocks release" in comment
    assert "- Next actor: `human`" in comment
    assert "A human owner must confirm approval and idempotency evidence" in comment
    assert "- Trust root touched: `true`" in comment
    assert "- Static-verdict boundary:" in comment
    assert "did not execute the agent or prove runtime behavior" in comment
    assert "[packet.json](agents-shipgate-reports/packet.json)" in comment
    assert '"merge_verdict": "blocked"' in comment
    assert '"fix_task": {' in comment
    assert (
        '"verification_command": "agents-shipgate verify --base origin/main --head HEAD --json"'
        in comment
    )


def test_capability_review_pr_comment_preserves_valid_agent_json_when_compacted() -> None:
    report = _report(decision="blocked", exit_code=20)
    bulky_repairs = [
        VerifierRepair(
            id=f"repair_{index}",
            actor="human",
            kind="review_or_provide_evidence",
            target=f"tool_{index}",
            finding_id=f"F-{index}",
            check_id="SHIP-TEST",
            command=None,
            reason="Review the source-backed evidence. " * 20,
        )
        for index in range(30)
    ]
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        execution="succeeded",
        head_status="succeeded",
        release_decision=report.release_decision,
        decision="blocked",
        merge_verdict="blocked",
        applicability="verified",
        control=_human_control("blocked"),
        capability_review=build_capability_review(report),
        fix_task=VerifierFixTask(
            actor="human",
            safe_to_attempt=False,
            instructions=["Human review required."],
            allowed_repairs=bulky_repairs,
            forbidden_repairs=bulky_repairs,
            forbidden_shortcuts=[],
            verification_command="agents-shipgate verify --base origin/main --head HEAD --json",
        ),
        artifacts={
            "report_json": "agents-shipgate-reports/report.json",
            "verifier_json": "agents-shipgate-reports/verifier.json",
        },
    )

    comment = render_pr_comment(verifier, report=report)
    payload = json.loads(comment.split("```json\n", 1)[1].rsplit("\n```", 1)[0])

    assert len(comment) <= 6000
    assert comment.count("### ") == 2
    assert payload["merge_verdict"] == "blocked"
    assert payload["fix_task"]["omitted"] is True
    assert payload["fix_task"]["artifact"] == "agents-shipgate-reports/verifier.json"
    assert payload["verification_command"] == (
        "agents-shipgate verify --base origin/main --head HEAD --json"
    )


def test_capability_review_pr_comment_uses_merge_verdict_vocabulary() -> None:
    report = _report(decision="review_required", exit_code=0)
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        execution="succeeded",
        head_status="succeeded",
        release_decision=report.release_decision,
        decision="review_required",
        merge_verdict="human_review_required",
        applicability="verified",
        control=_human_control("review_required"),
        capability_review=build_capability_review(report),
        artifacts={"verifier_json": "agents-shipgate-reports/verifier.json"},
    )

    comment = render_pr_comment(verifier, report=report)

    assert "## Agents Shipgate" in comment
    assert "- Merge verdict: `human_review_required`" in comment
    assert "- Release gate: `review_required`" in comment
    assert "- Reason: test decision" in comment


def test_capability_review_pr_comment_does_not_double_blank_without_headline() -> None:
    report = _report(decision="review_required", exit_code=0)
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        execution="succeeded",
        head_status="succeeded",
        release_decision=report.release_decision,
        decision="review_required",
        merge_verdict="human_review_required",
        applicability="verified",
        control=_human_control("review_required"),
        headline="",
        capability_review=build_capability_review(report),
        artifacts={"verifier_json": "agents-shipgate-reports/verifier.json"},
    )

    comment = render_pr_comment(verifier, report=report)

    assert "\n\n\nDecision:" not in comment


def test_capability_review_pr_comment_unknown_when_head_scan_failed() -> None:
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        execution="failed",
        head_status="failed",
        head_exit_code=2,
        merge_verdict="unknown",
        applicability="failed",
        control=_human_control("failed"),
        artifacts={"verifier_json": "agents-shipgate-reports/verifier.json"},
    )

    comment = render_pr_comment(verifier, report=None)

    assert "## Agents Shipgate" in comment
    assert "- Merge verdict: `unknown`" in comment
    assert "## Agents Shipgate: mergeable" not in comment
    assert "Head scan did not produce a report" in comment


def test_worktree_fix_task_structures_allowed_and_forbidden_repairs() -> None:
    report = _report(decision="blocked", exit_code=20)
    finding = Finding(
        id="F-stale",
        fingerprint="fp_stale",
        check_id="SHIP-MANIFEST-STALE-SUPPRESSION",
        title="Stale suppression",
        severity="high",
        category="manifest",
        evidence={},
        recommendation="Remove stale suppression.",
        autofix_safe=True,
        requires_human_review=False,
        patches=[
            RemovePointerPatch(
                target_file="/repo/shipgate.yaml",
                pointer="/checks/ignore/0",
                target_format="yaml",
                confidence="high",
                rationale="Remove stale suppression.",
                target_sha256="0" * 64,
            )
        ],
    )
    report.findings = [finding]
    report.release_decision.blockers = [
        ReleaseDecisionItem(
            id="F-stale",
            fingerprint="fp_stale",
            check_id="SHIP-MANIFEST-STALE-SUPPRESSION",
            severity="high",
            title="Stale suppression",
        )
    ]

    task = build_fix_task(
        report,
        merge_verdict="blocked",
        capability_review=VerifierCapabilityReview(),
        base_ref="origin/main",
        head_ref="HEAD",
        worktree=True,
    )

    assert task is not None
    assert task.actor == "coding_agent"
    assert task.allowed_repairs
    assert task.allowed_repairs[0].kind == "apply_high_confidence_patch"
    assert task.allowed_repairs[0].command.startswith("agents-shipgate apply-patches")
    assert any(repair.id == "invent_authority_evidence" for repair in task.forbidden_repairs)


def test_fix_task_human_authority_gap_has_no_agent_allowed_repairs() -> None:
    report = _report(decision="blocked", exit_code=20)
    finding = Finding(
        id="F-approval",
        fingerprint="fp_approval",
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        title="Approval missing",
        severity="critical",
        category="policy",
        evidence={},
        recommendation="A human must declare approval evidence.",
        autofix_safe=False,
        requires_human_review=True,
    )
    report.findings = [finding]
    report.release_decision.blockers = [
        ReleaseDecisionItem(
            id="F-approval",
            fingerprint="fp_approval",
            check_id="SHIP-POLICY-APPROVAL-MISSING",
            severity="critical",
            title="Approval missing",
        )
    ]

    task = build_fix_task(
        report,
        merge_verdict="blocked",
        capability_review=VerifierCapabilityReview(),
        base_ref="origin/main",
        head_ref="HEAD",
    )

    assert task is not None
    assert task.actor == "human"
    assert all(repair.actor == "human" for repair in task.allowed_repairs)
    assert any(repair.id == "invent_authority_evidence" for repair in task.forbidden_repairs)


def test_verify_missing_base_ref_is_unknown_not_head_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "ref_missing"
    assert payload["head_status"] == "failed"
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False
    assert payload["release_decision"] is None
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert payload["control"]["next_action"]["expects"] == "origin/main"
    assert calls == []


def test_verify_missing_head_ref_emits_agent_input_recovery_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--head",
            "missing-head",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["execution"] == "failed"
    assert payload["applicability"] == "failed"
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["must_stop"] is False
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert payload["control"]["next_action"]["expects"] == "missing-head"
    assert calls == []


def test_verify_base_missing_manifest_disables_diff_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    _write_manifest(repo)
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "missing_manifest"
    assert len(calls) == 1
    assert calls[0]["diff_from_path"] is None


def test_verify_successful_base_scan_feeds_head_diff_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text('{"tools":[{"name":"new"}]}\n', encoding="utf-8")
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_status"] == "succeeded"
    assert len(calls) == 2
    assert calls[0]["ci_mode"] == "advisory"
    assert calls[0]["packet_enabled"] is False
    assert calls[1]["diff_from_path"] is not None
    assert Path(calls[1]["diff_from_path"]).is_file()


def test_verify_base_scan_cache_hit_skips_second_base_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text('{"tools":[{"name":"new"}]}\n', encoding="utf-8")
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)
    args = [
        "verify",
        "--workspace",
        str(repo),
        "--config",
        "shipgate.yaml",
        "--base",
        "origin/main",
        "--format",
        "json",
    ]

    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    # Cache provenance is deliberately excluded from public identity and
    # operational projections; cold and warm executions both serialize the
    # deterministic state of the base evaluation.
    assert json.loads(first.output)["base_status"] == "succeeded"
    assert json.loads(second.output)["base_status"] == "succeeded"
    cache_path = json.loads(second.output)["base_report_json"]
    assert cache_path == "agents-shipgate-reports/verification-base-report.json"
    assert (repo / cache_path).is_file()
    assert not (repo / "agents-shipgate-reports" / ".cache").exists()
    assert len(calls) == 3
    assert calls[0]["config_path"] != calls[1]["config_path"]  # base then head
    assert calls[2]["config_path"] == calls[1]["config_path"]  # second run head only


def test_verify_head_overrides_manifest_packet_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    with (repo / "shipgate.yaml").open("a", encoding="utf-8") as handle:
        handle.write(
            """
output:
  packet:
    formats: [md, html]
""",
        )
    _commit_all(repo, "packet formats")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["packet_enabled"] is True
    assert calls[0]["packet_formats"] == ["json"]


def test_verify_head_strict_gate_exit_is_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=20, decision="blocked")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "strict",
        ],
    )

    assert result.exit_code == 20
    assert (repo / "agents-shipgate-reports" / "verifier.json").is_file()


@pytest.mark.parametrize(
    ("exc", "exit_code", "message"),
    [
        (ConfigError("bad config"), 2, "Config error: bad config"),
        (InputParseError("bad input"), 3, "Input parsing error: bad input"),
        (AgentsShipgateError("bad shipgate"), 4, "Agents Shipgate error: bad shipgate"),
    ],
)
def test_verify_head_errors_preserve_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    exit_code: int,
    message: str,
) -> None:
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**_kwargs: Any):
        raise exc

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == exit_code
    assert message in result.output
    verifier_path = repo / "agents-shipgate-reports" / "verifier.json"
    handoff_path = repo / "agents-shipgate-reports" / "agent-handoff.json"
    assert verifier_path.is_file()
    assert handoff_path.is_file()
    verifier = json.loads(verifier_path.read_text(encoding="utf-8"))
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert verifier["execution"] == "failed"
    assert verifier["control"]["state"] == "human_review_required"
    assert verifier["control"] == handoff["control"]


def test_internal_control_consistency_failure_clears_stale_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    reports = repo / "agents-shipgate-reports"
    reports.mkdir(exist_ok=True)
    stale = reports / "agent-handoff.json"
    stale.write_text('{"stale": true}\n', encoding="utf-8")
    _patch_run_scan(monkeypatch, [], head_exit=0)

    def fail_control_projection(**_kwargs: Any):
        raise ValueError("agent control consistency failure")

    monkeypatch.setattr(
        "agents_shipgate.cli.verify.orchestrator._build_verifier",
        fail_control_projection,
    )
    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 4
    assert "agent control consistency failure" in result.output
    assert not stale.exists()


def test_advisory_and_strict_change_only_exit_policy_not_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**kwargs: Any):
        strict = kwargs.get("ci_mode") == "strict"
        exit_code = 20 if strict else 0
        report = _report(decision="blocked", exit_code=exit_code)
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(report_json_payload(report), indent=2), encoding="utf-8"
        )
        return report, exit_code

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)
    advisory = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "advisory",
            "--format",
            "json",
        ],
    )
    advisory_control = json.loads(advisory.output)["control"]
    strict = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "strict",
            "--format",
            "json",
        ],
    )
    strict_control = json.loads(strict.output)["control"]

    assert advisory.exit_code == 0
    assert strict.exit_code == 20
    assert advisory_control == strict_control


def test_verify_config_error_prints_next_action_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTS_SHIPGATE_AGENT_MODE", raising=False)
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**_kwargs: Any):
        raise ConfigError("bad config")

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 2
    # The manifest exists but the loader rejected it, so the rank-1
    # recovery step is the invalid-manifest edit hint.
    assert "next: Edit" in result.output


def test_verify_agent_mode_emits_structured_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**_kwargs: Any):
        raise ConfigError("bad config")

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 2
    json_lines = [line for line in result.output.splitlines() if line.startswith("{")]
    assert json_lines
    payload = json.loads(json_lines[-1])
    assert payload["error"] == "config_error"
    assert payload["next_actions"]


def test_verify_flag_error_emits_structured_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(app, ["verify", "--format", "bogus"])

    assert result.exit_code == 2
    json_lines = [line for line in result.output.splitlines() if line.startswith("{")]
    assert json_lines
    payload = json.loads(json_lines[-1])
    assert payload["error"] == "config_error"
    # Flag errors must NOT carry manifest diagnostics — the fix is the
    # flag value, not shipgate.yaml.
    assert payload["next_actions"][0]["kind"] == "review"


def test_verify_artifact_write_failure_does_not_mask_scan_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**_kwargs: Any):
        raise ConfigError("bad config")

    def fake_write_artifacts(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("artifact failed")

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)
    monkeypatch.setattr(
        "agents_shipgate.cli.verify.orchestrator._write_artifacts",
        fake_write_artifacts,
    )

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml"],
    )

    assert result.exit_code == 2
    assert "Config error: bad config" in result.output
    assert "artifact failed" not in result.output


def test_verify_capability_review_artifact_failure_does_not_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo_with_manifest(tmp_path)

    def fake_run_scan(**kwargs: Any):
        callback = kwargs.get("capability_lock_callback")
        if callback is not None:
            callback(_empty_capability_lock())
        report = _report(decision="passed", exit_code=0)
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(report_json_payload(report), indent=2),
            encoding="utf-8",
        )
        return report, 0

    def fail_review_artifacts(**_kwargs: Any):
        raise RuntimeError("artifact boom")

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)
    monkeypatch.setattr(
        "agents_shipgate.cli.verify.orchestrator._write_capability_review_artifacts",
        fail_review_artifacts,
    )

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["head_status"] == "succeeded"
    assert payload["head_exit_code"] == 0
    assert payload["merge_verdict"] == "mergeable"
    assert "capability_lock" not in payload["artifacts"]
    assert payload["base_notes"] == ["Capability review artifacts unavailable: artifact boom"]


def test_verify_base_materialization_does_not_create_git_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_manifest(tmp_path)
    _set_origin_main(repo)
    (repo / "tools.json").write_text('{"tools":[{"name":"new"}]}\n', encoding="utf-8")
    _commit_all(repo, "head")
    calls: list[dict[str, Any]] = []
    _patch_run_scan(monkeypatch, calls, head_exit=0)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--base",
            "origin/main",
        ],
    )

    assert result.exit_code == 0, result.output
    worktrees = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert worktrees.count("worktree ") == 1


def test_read_file_at_ref_reads_single_blob(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    lock = repo / ".agents-shipgate" / "capabilities.lock.json"
    lock.parent.mkdir()
    lock.write_text('{"ok": true}\n', encoding="utf-8")
    _commit_all(repo, "lock")

    assert (
        read_file_at_ref(repo, "HEAD", Path(".agents-shipgate/capabilities.lock.json"))
        == '{"ok": true}\n'
    )
    assert read_file_at_ref(repo, "HEAD", Path("missing.json")) is None


def test_authorization_rerun_path_is_invocation_absolute_and_lexical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    invocation_dir = tmp_path / "invocation"
    invocation_dir.mkdir()
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    target = host_dir / "grant-target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = host_dir / "grant.json"
    link.symlink_to(target)
    monkeypatch.chdir(invocation_dir)

    options = _rerun_options(
        git_root=repo,
        out_dir=repo / "agents-shipgate-reports",
        pr_comment_style="capability-review",
        base=None,
        auto_base=False,
        ci_mode=None,
        fail_on=None,
        baseline_path=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_pack_paths=None,
        plugins_enabled=None,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        authorization=Path("../host/grant.json"),
    )

    tokens = shlex.split(" ".join(options))
    serialized = tokens[tokens.index("--authorization") + 1]
    assert serialized == str(link)
    assert serialized != str(target)


def test_retained_manifest_probe_parses_quoted_flow_mapping(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "old-gate.yml").write_text(
        '{"version": "0.1", "project": {"name": "demo"}, '
        '"agent": {"name": "assistant"}}\n',
        encoding="utf-8",
    )
    _commit_all(repo, "quoted manifest")

    assert carries_manifest_like_yaml(repo, "HEAD") is True


def test_retained_manifest_probe_batches_large_small_file_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    for index in range(500):
        (repo / f"source-{index:03d}.txt").write_text(
            f"ordinary source {index}\n",
            encoding="utf-8",
        )
    (repo / "release.gate").write_text(
        '{"version": "0.1", "project": {"name": "demo"}, '
        '"agent": {"name": "assistant"}}\n',
        encoding="utf-8",
    )
    _commit_all(repo, "large small-file tree")
    calls: list[tuple[str, ...]] = []
    bounded_calls: list[tuple[str, ...]] = []
    original = verify_git._run_git
    original_bounded = verify_git._run_git_bounded_output

    def recording_run_git(workspace, args, **kwargs):
        calls.append(tuple(args))
        return original(workspace, args, **kwargs)

    def recording_bounded(workspace, args, **kwargs):
        bounded_calls.append(tuple(args))
        return original_bounded(workspace, args, **kwargs)

    monkeypatch.setattr(verify_git, "_run_git", recording_run_git)
    monkeypatch.setattr(verify_git, "_run_git_bounded_output", recording_bounded)

    assert (
        carries_manifest_like_yaml(
            repo,
            "HEAD",
            protected_names=frozenset({"shipgate.yaml"}),
        )
        is True
    )
    assert [args[0] for args in bounded_calls] == ["ls-tree"]
    assert [args[0] for args in calls] == ["rev-parse", "cat-file"]
    assert calls[1][:2] == ("cat-file", "--batch")


def test_retained_manifest_name_guard_reuses_the_bounded_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text("not: an operational manifest\n", encoding="utf-8")
    _commit_all(repo, "named gate")
    calls: list[tuple[str, ...]] = []
    bounded_calls: list[tuple[str, ...]] = []
    original = verify_git._run_git
    original_bounded = verify_git._run_git_bounded_output

    def recording_run_git(workspace, args, **kwargs):
        calls.append(tuple(args))
        return original(workspace, args, **kwargs)

    def recording_bounded(workspace, args, **kwargs):
        bounded_calls.append(tuple(args))
        return original_bounded(workspace, args, **kwargs)

    monkeypatch.setattr(verify_git, "_run_git", recording_run_git)
    monkeypatch.setattr(verify_git, "_run_git_bounded_output", recording_bounded)

    assert (
        carries_manifest_like_yaml(
            repo,
            "HEAD",
            protected_names=frozenset({"shipgate.yaml"}),
        )
        is True
    )
    assert [args[0] for args in bounded_calls] == ["ls-tree"]
    assert [args[0] for args in calls] == ["rev-parse"]


@pytest.mark.parametrize("filename", ["old-gate.json", "old-gate.conf", "MANIFEST"])
def test_retained_manifest_probe_is_suffix_agnostic(
    tmp_path: Path,
    filename: str,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / filename).write_text(
        """
version: "0.1"
project:
  name: demo
agent:
  name: assistant
  declared_purpose:
    - test
environment:
  target: local
""".lstrip(),
        encoding="utf-8",
    )
    _commit_all(repo, "custom named manifest")

    assert carries_manifest_like_yaml(repo, "HEAD") is True


def test_retained_manifest_probe_treats_unknown_version_as_manifest_like(
    tmp_path: Path,
) -> None:
    """A future or legacy gate must conservatively suppress adoption wording."""

    repo = _init_repo(tmp_path)
    (repo / "old-policy.conf").write_text(
        """
version: "0.2"
project:
  name: demo
agent:
  name: assistant
""".lstrip(),
        encoding="utf-8",
    )
    _commit_all(repo, "unknown-version gate")

    assert carries_manifest_like_yaml(repo, "HEAD") is True


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="exercises Git NFC/worktree NFD spelling drift on macOS",
)
def test_explicit_head_nfd_manifest_adoption_keeps_adoption_wording(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "base")

    nfc_name = "café.gate"
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    assert nfd_name != nfc_name
    _write_manifest(repo)
    (repo / "shipgate.yaml").rename(repo / nfd_name)
    _commit_all(repo, "adopt decomposed manifest")

    emitted = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "diff",
            "--name-only",
            "HEAD~1",
            "HEAD",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if nfc_name not in emitted or nfd_name in emitted:
        pytest.skip("Git did not precompose the configured filename")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            nfd_name,
            "--base",
            "HEAD~1",
            "--head",
            "HEAD",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "also introduces the configured manifest" in payload["headline"]
    assert "weakens the release policy" not in payload["headline"]


def test_retained_manifest_probe_fails_closed_on_unparseable_yaml(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "unknown.yml").write_text("project: [\nagent:\n", encoding="utf-8")
    _commit_all(repo, "unparseable yaml")

    assert carries_manifest_like_yaml(repo, "HEAD") is None


def test_retained_manifest_probe_fails_closed_on_batch_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "candidate.txt").write_text("ordinary text\n", encoding="utf-8")
    _commit_all(repo, "candidate")
    original = verify_git._run_git

    def failing_batch(workspace, args, **kwargs):
        if args[:2] == ["cat-file", "--batch"]:
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"failed")
        return original(workspace, args, **kwargs)

    monkeypatch.setattr(verify_git, "_run_git", failing_batch)

    assert carries_manifest_like_yaml(repo, "HEAD") is None


def test_retained_manifest_probe_fails_closed_on_batch_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "candidate.txt").write_text("ordinary text\n", encoding="utf-8")
    _commit_all(repo, "candidate")
    original = verify_git._run_git

    def timing_out_batch(workspace, args, **kwargs):
        if args[:2] == ["cat-file", "--batch"]:
            raise subprocess.TimeoutExpired(args, 60)
        return original(workspace, args, **kwargs)

    monkeypatch.setattr(verify_git, "_run_git", timing_out_batch)

    assert carries_manifest_like_yaml(repo, "HEAD") is None


def test_retained_manifest_probe_skips_non_yaml_constructor_errors(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "digits.txt").write_text("9" * 5_000, encoding="utf-8")
    _commit_all(repo, "large integer-like source")

    assert carries_manifest_like_yaml(repo, "HEAD") is False


def test_bounded_git_output_stops_before_buffering_the_full_blob(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "large.txt").write_text("x" * 32_000, encoding="utf-8")
    _commit_all(repo, "large blob")

    assert (
        verify_git._run_git_bounded_output(
            repo,
            ["show", "HEAD:large.txt"],
            max_output_bytes=128,
        )
        is None
    )


def test_retained_manifest_probe_rejects_oversized_blob_before_reading_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "oversized.yml").write_bytes(
        b"project:\n  name: demo\nagent:\n  name: assistant\n"
        + b"#" * verify_git._MAX_MANIFEST_BYTES
    )
    _commit_all(repo, "oversized yaml")
    calls: list[tuple[str, ...]] = []
    original = verify_git._run_git

    def recording_run_git(workspace, args, **kwargs):
        calls.append(tuple(args))
        return original(workspace, args, **kwargs)

    monkeypatch.setattr(verify_git, "_run_git", recording_run_git)

    assert carries_manifest_like_yaml(repo, "HEAD") is None
    assert [args[0] for args in calls] == ["rev-parse"]


def test_retained_manifest_probe_fails_closed_above_candidate_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    (repo / "two.txt").write_text("two\n", encoding="utf-8")
    _commit_all(repo, "two candidates")
    monkeypatch.setattr(verify_git, "_MAX_MANIFEST_CANDIDATES", 1)

    assert carries_manifest_like_yaml(repo, "HEAD") is None


def test_retained_manifest_probe_fails_closed_on_non_utf8_yaml(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "unknown.yml").write_bytes(b"project:\n  name: \xff\nagent:\n  name: bot\n")
    _commit_all(repo, "non-utf8 yaml")

    assert carries_manifest_like_yaml(repo, "HEAD") is None


def test_prune_base_scan_cache_keeps_newest_entries(tmp_path: Path) -> None:
    cache_root = tmp_path / "base-scans"
    cache_root.mkdir()
    old = cache_root / "old"
    new = cache_root / "new"
    newest = cache_root / "newest"
    for index, path in enumerate((old, new, newest), start=1):
        path.mkdir()
        os.utime(path, (index, index))

    _prune_base_scan_cache(cache_root, keep=2)

    assert not old.exists()
    assert new.exists()
    assert newest.exists()


def test_non_git_preview_with_omitted_config_uses_workspace_default(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "fresh"
    workspace.mkdir()

    result = runner.invoke(
        app, ["verify", "--workspace", str(workspace), "--preview", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"
    assert payload["execution"] == "not_run"
    assert payload["applicability"] == "not_evaluated"
    assert payload["merge_verdict"] == "unknown"
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["must_stop"] is False
    assert "init" in payload["control"]["next_action"]["command"]
    assert (workspace / "agents-shipgate-reports" / "verifier.json").is_file()
    assert not (workspace / "shipgate.yaml").exists()


def test_verify_preview_rejects_output_that_overlaps_its_config(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    config = workspace / "verifier.json"
    original = '{"manifest": "must survive"}\n'
    config.write_text(original, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace),
            "--config",
            "verifier.json",
            "--out",
            ".",
            "--preview",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2
    assert "Verifier --out cannot be the workspace root" in result.output
    assert config.read_text(encoding="utf-8") == original


def test_verify_preview_rejects_a_manifest_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    (workspace / "shipgate.yaml").mkdir()

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace),
            "--preview",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "must identify one singly-linked regular file" in result.output
    payload = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert payload["error"] == "config_error"
    assert payload["next_actions"][0]["kind"] == "command"
    assert " verify " in payload["next_actions"][0]["command"]
    assert "--preview --json" in payload["next_actions"][0]["command"]


def test_verify_preview_rejects_a_symlinked_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    (workspace / "target.yml").write_text('version: "0.1"\n', encoding="utf-8")
    (workspace / "gate.yml").symlink_to("target.yml")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace),
            "--config",
            "gate.yml",
            "--preview",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--config must not contain symlink components" in result.output
    payload = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert [action["kind"] for action in payload["next_actions"]] == ["review"]


def test_verify_preview_rejects_an_external_absolute_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    external = tmp_path / "external.yml"
    external.write_text('version: "0.1"\n', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace),
            "--config",
            str(external),
            "--preview",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "--config must be inside --workspace" in result.output
    payload = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert [action["kind"] for action in payload["next_actions"]] == ["review"]


def test_verify_preview_docs_only_diff_does_not_recommend_init(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"
    assert payload["trigger"]["should_run"] is False
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "initialize"
    assert payload["control"]["next_action"]["command"] == (
        f"shipgate init --workspace {repo} --write --json"
    )


def test_verify_preview_missing_base_without_manifest_reports_the_missing_ref(
    tmp_path: Path,
) -> None:
    """An unreadable diff outranks the adoption route, manifest or not.

    A shallow or blobless clone of an un-adopted repository is the normal
    shape of first contact, so this is exactly the case where routing to
    "Shipgate is not configured here" would hide the fact that the PR was
    never inspected.
    """

    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"
    assert payload["config"] == "shipgate.yaml"
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert payload["diff_status"]["completeness"] == "unavailable"
    assert payload["diff_status"]["reason"] == "refs_missing"
    assert payload["trigger"]["evaluation_status"] == "not_evaluated"
    assert payload["trigger"]["should_run"] is None
    assert payload["trigger"]["skip_reason"] is None
    assert payload["base_notes"]
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False


def test_verify_preview_configured_repo_preserves_exact_verify_args(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text('version: "0.1"\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")
    out = repo / "custom-reports"

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--out",
            str(out),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "verify"
    assert payload["control"]["next_action"]["command"] == (
        f"agents-shipgate verify --workspace {repo} --config shipgate.yaml "
        f"--base origin/main --head HEAD --out {out} --ci-mode advisory --json"
    )


def test_nested_workspace_preview_command_verifies_the_same_manifest(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    _write_manifest(repo)
    root_gate = repo / "gate.yml"
    (repo / "shipgate.yaml").rename(root_gate)
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    nested = repo / "services" / "api"
    nested.mkdir(parents=True)
    nested_gate = nested / "gate.yml"
    nested_gate.write_text(
        root_gate.read_text(encoding="utf-8").replace(
            "name: test\n",
            "name: nested-test\n",
            1,
        ),
        encoding="utf-8",
    )
    (nested / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "root and nested gates")

    preview = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(nested),
            "--config",
            "gate.yml",
            "--preview",
            "--json",
        ],
    )

    assert preview.exit_code == 0, preview.output
    preview_payload = json.loads(preview.output)
    assert preview_payload["config"] == "services/api/gate.yml"
    command = preview_payload["control"]["next_action"]["command"]
    assert command is not None

    verified = runner.invoke(app, shlex.split(command)[1:])

    assert verified.exit_code == 0, verified.output
    verified_payload = json.loads(verified.output)
    assert verified_payload["config"] == "services/api/gate.yml"
    report = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text(encoding="utf-8")
    )
    assert report["project"]["name"] == "nested-test"


def test_verify_preview_configured_repo_missing_base_fetches_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text('version: "0.1"\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    why = payload["control"]["next_action"]["why"]
    assert "refs_missing" in why
    assert "git fetch" in why
    assert payload["control"]["next_action"]["expects"] == "origin/main...HEAD"
    assert payload["base_notes"]


def test_verify_json_flag_is_shortcut_for_format_json(tmp_path: Path) -> None:
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    result = runner.invoke(app, ["verify", "--workspace", str(workspace), "--preview", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "preview"


def _patch_run_scan(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    *,
    head_exit: int,
    decision: str = "passed",
) -> None:
    def fake_run_scan(**kwargs: Any):
        calls.append(kwargs)
        report = _report(decision=decision, exit_code=head_exit)
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.json").write_text(
            json.dumps(report_json_payload(report), indent=2),
            encoding="utf-8",
        )
        return report, head_exit

    monkeypatch.setattr("agents_shipgate.cli.verify.orchestrator.run_scan", fake_run_scan)


def _report(*, decision: str, exit_code: int) -> ReadinessReport:
    return ReadinessReport(
        run_id="r",
        project={"name": "p"},
        agent={"name": "a"},
        environment={"target": "local"},
        summary=ReportSummary(status="clean"),
        release_decision=ReleaseDecision(
            decision=decision,  # type: ignore[arg-type]
            reason="test decision",
            evidence_coverage=EvidenceCoverageDecision(
                level="static",
                human_review_recommended=False,
                source_warning_count=0,
                low_confidence_tool_count=0,
            ),
            baseline_delta=BaselineDelta(enabled=False),
            fail_policy=FailPolicy(
                ci_mode="strict" if exit_code == 20 else "advisory",
                fail_on=["critical"],
                new_findings_only=False,
                would_fail_ci=exit_code == 20,
                exit_code=exit_code,
            ),
        ),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
    )


def _human_control(reason: str):
    why = f"Release decision is {reason}."
    return derive_agent_control(
        reason=why,
        next_action=HumanControlAction(
            kind="stop" if reason in {"blocked", "failed"} else "review",
            why=why,
        ),
        human_review_required=True,
        unsafe_block=reason == "blocked",
    )


def _empty_capability_lock() -> CapabilityLockFileV1:
    return CapabilityLockFileV1(
        cli_version="test",
        source=CapabilityLockSource(
            config_path="shipgate.yaml",
            manifest_dir=".",
            agent_id="agent:test",
            agent_name="test-agent",
        ),
        summary=CapabilityLockSummary(),
        hashes=CapabilityLockHashes(
            semantic_capability_set_hash="0" * 64,
            evidence_set_hash="1" * 64,
            source_set_hash="2" * 64,
        ),
        capabilities=[],
    )


def _repo_with_manifest(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
    _write_manifest(repo)
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "base")
    return repo


def _write_manifest(repo: Path) -> None:
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: test
agent:
  name: test-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""".lstrip(),
        encoding="utf-8",
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    return repo


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _set_origin_main(repo: Path) -> None:
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
