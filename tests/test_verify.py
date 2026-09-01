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
from agents_shipgate.report.human_order import (
    HumanArtifactContext,
    capability_delta_subject_rollup,
)
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.agent_control import HumanControlAction
from agents_shipgate.schemas.bindings import BindingSurfaceDiff
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
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.manifest_provenance import ManifestProvenance
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
    ActionFact,
    ActionSurfaceChange,
    ActionSurfaceDiff,
    ActionSurfaceDiffSummary,
    ActionSurfaceFacts,
    ActionSurfaceHashes,
    ToolSurfaceControlChange,
    ToolSurfaceDiff,
    ToolSurfaceHighRiskEffectChange,
    ToolSurfaceScopeChange,
    ToolSurfaceToolChange,
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
    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            check=True,
            input=b'version: "0.1"\n',
            capture_output=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    tree_input = b"".join(
        f"100644 blob {blob}\t{name}".encode() + b"\0" for name in sorted(("GATE.yml", "gate.yml"))
    )
    tree = (
        subprocess.run(
            ["git", "mktree", "-z"],
            cwd=repo,
            check=True,
            input=tree_input,
            capture_output=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
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
    # Reports follow the workspace the caller named, so two projects in one
    # repository never overwrite each other's results (#363).
    assert (nested / "agents-shipgate-reports" / "report.json").is_file()


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
    first_diff = (repo / "agents-shipgate-reports" / "verification-input.diff").read_bytes()

    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)
    second_diff = (repo / "agents-shipgate-reports" / "verification-input.diff").read_bytes()

    assert first_payload["changed_files"] == second_payload["changed_files"]
    assert first_payload["changed_files"] == ["tools.json"]
    assert not any(
        path.startswith("agents-shipgate-reports/") for path in second_payload["changed_files"]
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
    assert "Git index flags hide paths from worktree collection" in " ".join(payload["base_notes"])
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
        f"agents-shipgate verify --workspace {repo} --config missing.yaml --preview --json"
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


def test_verify_text_projects_google_adk_primary_evidence_action(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # ``case_id`` is unannotated on purpose. Since #393 the ADK AST path reports
    # a proven surface for a module it fully resolved, so the inventory
    # remediation this test projects needs a source whose surface genuinely is
    # not proven — here, the parameter type static extraction cannot read.
    (repo / "agent.py").write_text(
        '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def lookup_case(case_id) -> dict:
    """Look up read-only support case metadata."""
    return {"case_id": case_id}


lookup_tool = FunctionTool(func=lookup_case)
root_agent = LlmAgent(name="support_reader", tools=[lookup_tool])
'''.lstrip(),
        encoding="utf-8",
    )
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: google-adk-remediation
agent:
  name: support-reader
  declared_purpose: [read support case metadata]
environment:
  target: local
tool_sources:
  - id: adk
    type: google_adk
    path: agent.py
""".lstrip(),
        encoding="utf-8",
    )
    _commit_all(repo, "add google adk agent")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--no-base",
            "--format",
            "text",
        ],
    )

    assert result.exit_code == 0, result.output
    # Contract v22 leads text output with the control state; the verdict line
    # follows it unchanged.
    assert result.output.startswith("Control: ")
    assert "Agents Shipgate verify: insufficient_evidence" in result.output
    assert "Improve evidence: Review the skeleton" in result.output
    assert "\nRun: agents-shipgate verify" in result.output
    # #358: the human work precedes the exact rerun command. The control
    # headline uses a distinct `Next command:` prefix precisely so it cannot
    # insert a `Run:` line above this remediation.
    assert result.output.index("Review the skeleton") < result.output.index("Run:")
    assert "google_adk.tool_inventories" in result.output
    assert "suggested-inventory.json" in result.output
    assert "verification. Target: suggested-inventory.json." in result.output
    assert "broader OpenAI SDK source path" not in result.output


def test_pr_comment_keeps_code_span_values_unescaped() -> None:
    verifier = VerifierArtifact(
        workspace="/tmp/work",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        manifest_provenance=ManifestProvenance.repository(),
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
        manifest_provenance=ManifestProvenance.repository(),
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
    assert (
        "- Capability delta (analysed surface): "
        "1 subject across 1 change (+1 added, 0 modified, -0 removed)" in comment
    )
    assert "`stripe.create_refund`: added action create\\_refund — blocks release" in comment
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


def test_capability_review_groups_one_bound_tool_without_changing_legacy_counts() -> None:
    """One reader subject may still carry two stable machine change rows (#439)."""

    report = _report(decision="review_required", exit_code=0)
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="cap-update-incident-action",
                direction="added",
                subject_kind="action",
                tool="update_incident",
                action="update_incident",
                release_impact="review_required",
                rationale="Capability added.",
            ),
            CapabilityChangeMember(
                id="cap-update-incident-tool",
                direction="added",
                subject_kind="tool",
                tool="update_incident",
                release_impact="review_required",
                rationale="Tool added.",
            ),
        ],
    )

    legacy_review = build_capability_review(report)
    rollup = capability_delta_subject_rollup(report)
    verifier = _capability_verifier(report, review=legacy_review)
    comments = [
        render_pr_comment(verifier, report=report),
        render_pr_comment(verifier, report=report, style="findings"),
        render_pr_comment(
            verifier,
            report=report,
            human_context=HumanArtifactContext(manifest_introduced=True),
        ),
        render_pr_comment(
            verifier,
            report=report,
            style="findings",
            human_context=HumanArtifactContext(manifest_introduced=True),
        ),
    ]

    # ``verifier.json`` stays backward compatible: these are still two
    # independently useful change rows and the published count remains two.
    assert legacy_review.added == 2
    assert {change.change_type for change in legacy_review.top_changes} == {
        "action_added",
        "tool_added",
    }

    # The schema-free human projection answers the reader's question instead:
    # one thing changed, in two independently visible ways.
    assert rollup.total_subjects == 1
    assert rollup.added_subjects == 1
    assert rollup.change_count == 2
    assert len(rollup.subjects) == 1
    assert rollup.subjects[0].subject == "update_incident"
    assert rollup.subjects[0].change_types == ("action_added", "tool_added")
    assert rollup.subjects[0].change_count == 2
    for comment in comments:
        human_summary = comment.split("### Agent instruction block", 1)[0]
        assert "1 subject" in human_summary
        assert "2 changes" in human_summary
        normalized_summary = human_summary.replace("\\_", "_").lower()
        assert normalized_summary.count("update_incident") == 1
        assert "added action" in normalized_summary
        assert "added tool" in normalized_summary


def test_capability_review_keeps_same_named_provider_tools_as_distinct_subjects() -> None:
    """Display names never replace canonical tool identity during grouping."""

    report = _report(decision="review_required", exit_code=0)
    alpha_action = "agent:alpha/agent:action_v2_" + "a" * 64
    beta_action = "agent:beta/agent:action_v2_" + "b" * 64
    report.findings = [
        Finding(
            id="F-alpha-blocker",
            check_id="SHIP-TEST-ALPHA",
            title="alpha search is blocked",
            severity="critical",
            category="test",
            tool_id="tool-alpha",
            tool_name="search",
            recommendation="Fix alpha.",
            blocks_release=True,
        )
    ]
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        tools=[
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-alpha",
                name="search",
                provider="alpha",
                source_type="mcp",
                source_path="alpha-tools.json",
                source_start_line=10,
            ),
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-beta",
                name="search",
                provider="beta",
                source_type="mcp",
                source_path="beta-tools.json",
                source_start_line=20,
            ),
        ],
    )
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(actions_added=2),
        added=[
            ActionSurfaceChange(
                type="ACTION_ADDED",
                action_id=alpha_action,
                tool_id="tool-alpha",
                tool_name="search",
                operation="search_alpha",
                reason="Action added: search",
                source_path="alpha-tools.json",
                source_start_line=10,
            ),
            ActionSurfaceChange(
                type="ACTION_ADDED",
                action_id=beta_action,
                tool_id="tool-beta",
                tool_name="search",
                operation="search_beta",
                reason="Action added: search",
                source_path="beta-tools.json",
                source_start_line=20,
            ),
        ],
    )
    # Exercise the production compatibility builder. Its established member
    # shape omits provider/tool_id, so the human projection must recover the
    # canonical identities from the complete report without changing schema.
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)

    assert rollup.total_subjects == 2
    assert rollup.added_subjects == 2
    assert rollup.change_count == 4
    assert {subject.subject for subject in rollup.subjects} == {
        "search [alpha]",
        "search [beta]",
    }
    assert all(
        subject.change_types == ("action_added", "tool_added")
        for subject in rollup.subjects
    )
    by_subject = {subject.subject: subject for subject in rollup.subjects}
    assert [(source.path, source.start_line) for source in by_subject["search [alpha]"].sources] == [
        ("alpha-tools.json", 10)
    ]
    assert [(source.path, source.start_line) for source in by_subject["search [beta]"].sources] == [
        ("beta-tools.json", 20)
    ]
    assert all("blocks release" in detail for detail in by_subject["search [alpha]"].changes)
    assert all(
        "blocks release" not in detail and "review required" in detail
        for detail in by_subject["search [beta]"].changes
    )


def test_capability_review_keeps_unseen_explicit_tool_id_out_of_name_fanout() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        tools=[
            ToolSurfaceToolChange(
                kind="changed",
                tool_id="tool-alpha",
                name="search",
                provider="alpha",
                source_type="mcp",
            ),
            ToolSurfaceToolChange(
                kind="changed",
                tool_id="tool-beta",
                name="search",
                provider="beta",
                source_type="mcp",
            ),
        ],
        controls=[
            ToolSurfaceControlChange(
                kind="removed",
                control="approval_policy",
                tool="search",
                tool_id="tool-gamma",
            )
        ],
    )
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)

    assert rollup.total_subjects == 3
    assert rollup.change_count == 3
    assert {subject.subject for subject in rollup.subjects} == {
        "search [alpha]",
        "search [beta]",
        "search [tool-gamma]",
    }
    gamma = next(subject for subject in rollup.subjects if "tool-gamma" in subject.subject)
    assert gamma.change_types == ("policy_broadened",)


def test_capability_review_prefers_effect_signature_over_unrelated_action_id() -> None:
    """A risk tag that equals an action id still belongs to its exact tool."""

    report = _report(decision="review_required", exit_code=0)
    report.action_surface_facts = ActionSurfaceFacts(
        actions=[
            _capability_action_fact(
                action_id="financial_write",
                tool_id="tool-alpha",
                tool_name="alpha",
                provider="alpha-provider",
                operation="read_alpha",
            )
        ]
    )
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        high_risk_effects=[
            ToolSurfaceHighRiskEffectChange(
                kind="added",
                tool_id="tool-beta",
                tool="beta",
                tag="financial_write",
            )
        ],
    )
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)

    assert rollup.total_subjects == 1
    assert rollup.subjects[0].subject == "beta"
    assert rollup.subjects[0].change_types == ("action_broadened",)


def test_capability_review_keeps_only_exact_finding_source_on_name_collision() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.findings = [
        Finding(
            id="F-alpha-blocker",
            check_id="SHIP-TEST-ALPHA",
            title="alpha search is blocked",
            severity="critical",
            category="test",
            tool_id="tool-alpha",
            tool_name="search",
            recommendation="Fix alpha.",
            blocks_release=True,
            source=SourceReference(type="mcp", path="alpha.py", start_line=42),
        )
    ]
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        tools=[
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-alpha",
                name="search",
                provider="alpha",
                source_type="mcp",
            ),
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-beta",
                name="search",
                provider="beta",
                source_type="mcp",
            ),
        ],
    )
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)
    by_subject = {subject.subject: subject for subject in rollup.subjects}

    assert [(source.path, source.start_line) for source in by_subject["search [alpha]"].sources] == [
        ("alpha.py", 42)
    ]
    assert by_subject["search [beta]"].sources == ()


def test_capability_review_labels_idless_ambiguous_action_without_internal_key() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        tools=[
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-alpha",
                name="search",
                provider="alpha",
                source_type="mcp",
            ),
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-beta",
                name="search",
                provider="beta",
                source_type="mcp",
            ),
        ],
    )
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        added=[
            ActionSurfaceChange(
                type="ACTION_ADDED",
                action_id="legacy-search-action",
                tool_id=None,
                tool_name="search",
                operation="search",
                reason="Action added: search",
            )
        ],
    )
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)
    labels = {subject.subject for subject in rollup.subjects}

    assert rollup.total_subjects == 3
    assert rollup.change_count == 3
    assert "search [identity unavailable]" in labels
    assert all("legacy:" not in label for label in labels)


def test_capability_review_preserves_exact_action_fact_identity_for_idless_diff() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.action_surface_facts = ActionSurfaceFacts(
        actions=[
            _capability_action_fact(
                action_id="aid",
                tool_id="tool-alpha",
                tool_name="search",
                provider="alpha",
                operation="search_alpha",
                source_path="alpha.json",
                source_start_line=11,
            ),
            _capability_action_fact(
                action_id="beta-aid",
                tool_id="tool-beta",
                tool_name="search",
                provider="beta",
                operation="search_beta",
                source_path="beta.json",
                source_start_line=22,
            ),
        ]
    )
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        modified=[
            ActionSurfaceChange(
                type="INPUT_SCHEMA_EXPANDED",
                action_id="aid",
                tool_id=None,
                tool_name="search",
                operation="search_alpha",
                reason="Input schema expanded.",
            )
        ],
    )
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)

    assert rollup.total_subjects == 1
    assert rollup.subjects[0].subject == "search [alpha]"
    assert [(source.path, source.start_line) for source in rollup.subjects[0].sources] == [
        ("alpha.json", 11)
    ]


def test_capability_review_sources_only_the_changed_action_fact() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.action_surface_facts = ActionSurfaceFacts(
        actions=[
            _capability_action_fact(
                action_id=action_id,
                tool_id="tool-search",
                tool_name="search",
                provider="alpha",
                operation=action_id,
                source_path=f"{action_id}.json",
                source_start_line=line,
            )
            for line, action_id in enumerate(("a", "b", "c", "d", "z"), start=1)
        ]
    )
    report.action_surface_facts.actions[-1].source_start_line = 99
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        modified=[
            ActionSurfaceChange(
                type="INPUT_SCHEMA_EXPANDED",
                action_id="z",
                tool_id="tool-search",
                tool_name="search",
                operation="z",
                reason="Input schema expanded.",
            )
        ],
    )
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)

    assert [(source.path, source.start_line) for source in rollup.subjects[0].sources] == [
        ("z.json", 99)
    ]


def test_capability_review_does_not_invent_identity_for_ambiguous_legacy_member() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        tools=[
            ToolSurfaceToolChange(
                kind="changed",
                tool_id="tool-alpha",
                name="search",
                provider="alpha",
                source_type="mcp",
            ),
            ToolSurfaceToolChange(
                kind="changed",
                tool_id="tool-beta",
                name="search",
                provider="beta",
                source_type="mcp",
            ),
        ],
    )
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="legacy-search",
                direction="added",
                subject_kind="action",
                tool="search",
                action="legacy-operation",
            )
        ],
    )

    rollup = capability_delta_subject_rollup(report)

    assert rollup.total_subjects == 1
    assert rollup.change_count == 1
    assert rollup.subjects[0].subject == "search [identity unavailable]"


def test_capability_review_final_labels_are_injective_after_disambiguation() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        tools=[
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-alpha",
                name="search",
                provider="alpha",
                source_type="mcp",
            ),
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-beta",
                name="search",
                provider="beta",
                source_type="mcp",
            ),
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-literal",
                name="search [alpha]",
                provider="literal",
                source_type="mcp",
            ),
        ],
    )
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)
    labels = [subject.subject for subject in rollup.subjects]

    assert rollup.total_subjects == 3
    assert len(set(labels)) == 3
    assert any("[subject 2]" in label for label in labels)


def test_capability_review_joins_legacy_action_change_to_unique_canonical_tool() -> None:
    """An omitted legacy tool_id must not split one tool into two subjects."""

    report = _report(decision="review_required", exit_code=0)
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        tools=[
            ToolSurfaceToolChange(
                kind="added",
                tool_id="tool-incident",
                name="incident",
                provider="pager",
                source_type="mcp",
            )
        ],
    )
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        added=[
            ActionSurfaceChange(
                type="ACTION_ADDED",
                action_id="legacy-action-id",
                tool_id=None,
                tool_name="incident",
                operation="update_incident",
                reason="Action added: incident",
            )
        ],
    )
    report.capability_change = None

    rollup = capability_delta_subject_rollup(report)

    assert rollup.total_subjects == 1
    assert rollup.change_count == 2
    assert rollup.subjects[0].subject == "incident"
    assert rollup.subjects[0].change_types == ("action_added", "tool_added")


def test_capability_review_matches_independently_sorted_scope_names_and_ids() -> None:
    """Scope tool_names/tool_ids are sets, not positional parallel arrays."""

    report = _report(decision="review_required", exit_code=0)
    report.tool_surface_diff = ToolSurfaceDiff(
        enabled=True,
        tools=[
            ToolSurfaceToolChange(
                kind="changed",
                tool_id="z-tool",
                name="alpha",
                provider="provider-alpha",
                source_type="mcp",
            ),
            ToolSurfaceToolChange(
                kind="changed",
                tool_id="a-tool",
                name="beta",
                provider="provider-beta",
                source_type="mcp",
            ),
        ],
        scopes=[
            ToolSurfaceScopeChange(
                kind="added",
                scope="production",
                scope_kind="tool_required",
                tool_names=["alpha", "beta"],
                # The producer sorts these independently, so positions do not
                # express alpha->z-tool / beta->a-tool.
                tool_ids=["a-tool", "z-tool"],
            )
        ],
    )
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        broadened=[
            CapabilityChangeMember(
                id="cap-alpha-scope",
                direction="broadened",
                subject_kind="scope",
                tool="alpha",
                scope="production",
                release_impact="blocks_release",
                rationale="Alpha production access expanded.",
            ),
            CapabilityChangeMember(
                id="cap-beta-scope",
                direction="broadened",
                subject_kind="scope",
                tool="beta",
                scope="production",
                release_impact="informational",
                rationale="Beta production access expanded.",
            ),
        ],
    )

    rollup = capability_delta_subject_rollup(report)
    by_subject = {subject.subject: subject for subject in rollup.subjects}

    assert "blocks release" in by_subject["alpha"].changes[0]
    assert "Alpha production access expanded" in by_subject["alpha"].changes[0]
    assert "blocks release" not in by_subject["beta"].changes[0]
    assert "Beta production access expanded" in by_subject["beta"].changes[0]


def test_capability_review_renders_operation_and_semantic_rationale_not_action_id() -> None:
    report = _report(decision="review_required", exit_code=0)
    action_id = "agent:incident/agent:action_v2_" + "c" * 64
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        modified=[
            ActionSurfaceChange(
                type="INPUT_SCHEMA_EXPANDED",
                action_id=action_id,
                tool_id="tool-incident",
                tool_name="incident",
                operation="update_incident",
                reason="Input schema now accepts the production_region field.",
            )
        ],
    )
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        broadened=[
            CapabilityChangeMember(
                id="cap-incident-input",
                direction="broadened",
                subject_kind="action",
                tool="incident",
                action=action_id,
                release_impact="review_required",
                rationale="Input schema now accepts the production_region field.",
            )
        ],
    )

    rollup = capability_delta_subject_rollup(report)
    (detail,) = rollup.subjects[0].changes

    assert "update_incident" in detail
    assert "production_region" in detail
    assert "action_v2_" not in detail


def test_capability_delta_subject_rollup_preserves_overlapping_buckets() -> None:
    """Grouping must not make an added-and-modified subject choose one fact."""

    report = _report(decision="review_required", exit_code=0)
    report.findings = [
        Finding(
            id="F-deploy-scope",
            check_id="SHIP-TEST",
            title="deployment scope changed",
            severity="medium",
            category="test",
            recommendation="Review the scope change.",
            source=SourceReference(
                type="openapi",
                path="tools.json",
                start_line=42,
            ),
        )
    ]
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="cap-deploy-tool",
                direction="added",
                subject_kind="tool",
                tool="deploy_service",
                release_impact="review_required",
                rationale="Tool added.",
            )
        ],
        broadened=[
            CapabilityChangeMember(
                id="cap-deploy-scope",
                direction="broadened",
                subject_kind="scope",
                tool="deploy_service",
                scope="production",
                before_scope="staging",
                after_scope="production",
                release_impact="review_required",
                rationale="Scope broadened.",
                related_finding_ids=["F-deploy-scope"],
            )
        ],
    )

    rollup = capability_delta_subject_rollup(report)

    assert rollup.total_subjects == 1
    assert rollup.added_subjects == 1
    assert rollup.modified_subjects == 1
    assert rollup.removed_subjects == 0
    assert rollup.change_count == 2
    (subject,) = rollup.subjects
    assert subject.subject == "deploy_service"
    assert subject.change_types == ("tool_added", "scope_broadened")
    assert subject.change_buckets == ("added", "modified")
    assert subject.change_count == 2
    assert len(subject.changes) == 2
    assert any("added tool" in detail for detail in subject.changes)
    assert any("broadened scope" in detail for detail in subject.changes)
    assert [(source.path, source.start_line) for source in subject.sources] == [("tools.json", 42)]

    # The legacy verifier subject is ``deploy_service:production`` while the
    # human rollup subject is ``deploy_service``. Provenance is carried by the
    # stable member/finding relation, not a lossy rendered-subject join.
    verifier = _capability_verifier(report, review=build_capability_review(report))
    comment = render_pr_comment(verifier, report=report)
    assert "sources: `tools.json:42`" in comment


def test_capability_review_labels_all_group_sources_without_row_misassociation() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.findings = [
        Finding(
            id="F-action-source",
            check_id="SHIP-TEST-ACTION",
            title="action changed",
            severity="medium",
            category="test",
            recommendation="Review action.",
            source=SourceReference(type="openapi", path="a.json", start_line=1),
        ),
        Finding(
            id="F-tool-source",
            check_id="SHIP-TEST-TOOL",
            title="tool changed",
            severity="medium",
            category="test",
            recommendation="Review tool.",
            source=SourceReference(type="mcp", path="b.json", start_line=2),
        ),
    ]
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="cap-action-source",
                direction="added",
                subject_kind="action",
                tool="deploy",
                action="run",
                related_finding_ids=["F-action-source"],
            ),
            CapabilityChangeMember(
                id="cap-tool-source",
                direction="added",
                subject_kind="tool",
                tool="deploy",
                related_finding_ids=["F-tool-source"],
            ),
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))

    comment = render_pr_comment(verifier, report=report)

    assert "sources: `a.json:1`, `b.json:2`" in comment
    assert "+1 more source" not in comment


def test_capability_review_pr_comment_truncates_complete_ranked_subjects() -> None:
    """The five-row budget ranks groups first and accounts for every hidden row."""

    report = _report(decision="review_required", exit_code=0)
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            member
            for subject in [*(f"a_tool_{index}" for index in range(6)), "z_blocking_tool"]
            for member in (
                CapabilityChangeMember(
                    id=f"cap-{subject}-action",
                    direction="added",
                    subject_kind="action",
                    tool=subject,
                    action=subject,
                    release_impact=(
                        "blocks_release" if subject == "z_blocking_tool" else "informational"
                    ),
                    rationale="Capability added.",
                ),
                CapabilityChangeMember(
                    id=f"cap-{subject}-tool",
                    direction="added",
                    subject_kind="tool",
                    tool=subject,
                    release_impact=(
                        "blocks_release" if subject == "z_blocking_tool" else "informational"
                    ),
                    rationale="Tool added.",
                ),
            )
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))

    human_summary = render_pr_comment(verifier, report=report).split(
        "### Agent instruction block", 1
    )[0]
    normalized_summary = human_summary.replace("\\_", "_")

    assert "7 subjects" in human_summary
    assert "14 changes" in human_summary
    assert "… and 2 more subjects (4 changes) not shown." in human_summary
    # Ranking happens on complete subject groups, so the blocking z_* subject
    # stays visible ahead of informational a_* subjects without losing a row.
    assert normalized_summary.count("z_blocking_tool") == 1
    for index in range(4):
        assert normalized_summary.count(f"a_tool_{index}") == 1
    assert "a_tool_4" not in normalized_summary
    assert "a_tool_5" not in normalized_summary


def test_capability_review_keeps_exact_hidden_counts_with_long_subject_rows() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id=f"cap-long-{index}",
                direction="added",
                subject_kind="tool",
                tool=f"tool-{index}-" + ("x" * 1800),
            )
            for index in range(7)
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))
    comments = [
        render_pr_comment(verifier, report=report),
        render_pr_comment(verifier, report=report, style="findings"),
        render_pr_comment(
            verifier,
            report=report,
            human_context=HumanArtifactContext(manifest_introduced=True),
        ),
        render_pr_comment(
            verifier,
            report=report,
            style="findings",
            human_context=HumanArtifactContext(manifest_introduced=True),
        ),
    ]

    for comment in comments:
        assert len(comment) <= 6000
        assert "… and 7 more subjects (7 changes) not shown." in comment


def test_capability_review_survives_long_release_reason_before_its_rows() -> None:
    """An oversized prose field cannot erase the exact capability disclosure."""

    report = _report(decision="review_required", exit_code=0)
    assert report.release_decision is not None
    report.release_decision.reason = "R" * 7000
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id=f"cap-short-{index}",
                direction="added",
                subject_kind="tool",
                tool=f"tool-{index}",
            )
            for index in range(7)
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))

    for style in ("capability-review", "findings"):
        comment = render_pr_comment(verifier, report=report, style=style)

        assert len(comment) <= 6000
        assert "Capability delta (analysed surface)" in comment
        assert "… and 2 more subjects (2 changes) not shown." in comment


def test_capability_review_survives_escaped_prose_budget_in_findings_style() -> None:
    report = _report(decision="review_required", exit_code=0)
    assert report.release_decision is not None
    report.release_decision.reason = "*" * 7000
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id=f"cap-escaped-{index}",
                direction="added",
                subject_kind="tool",
                tool=f"tool-{index}",
            )
            for index in range(7)
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))
    verifier.trigger = {"rationale": "*" * 7000}
    verifier.base_status = "cache_hit"
    verifier.base_ref = "origin/main"
    verifier.base_notes = ["*" * 7000, "*" * 7000]

    comment = render_pr_comment(verifier, report=report, style="findings")

    assert len(comment) <= 6000
    assert "Capability delta (analysed surface)" in comment
    assert "… and 2 more subjects (2 changes) not shown." in comment


def test_cold_capability_review_reserves_delta_ahead_of_long_surface_row() -> None:
    report = _report(decision="review_required", exit_code=0)
    assert report.release_decision is not None
    report.release_decision.reason = "R" * 7000
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id=f"cap-cold-{index}",
                direction="added",
                subject_kind="tool",
                tool=f"tool-{index}",
            )
            for index in range(7)
        ],
    )
    report.action_surface_facts = ActionSurfaceFacts(
        actions=[
            _capability_action_fact(
                action_id="long-write-action",
                tool_id="tool-write",
                tool_name="write_" + ("W" * 3000),
                provider="write-provider",
                operation="write",
                effect="write",
            )
        ]
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))
    context = HumanArtifactContext(manifest_introduced=True)

    for style in ("capability-review", "findings"):
        comment = render_pr_comment(
            verifier,
            report=report,
            style=style,
            human_context=context,
        )

        assert len(comment) <= 6000
        assert "Capability delta (analysed surface)" in comment
        assert "… and 2 more subjects (2 changes) not shown." in comment


def test_capability_review_keeps_omitted_change_types_and_worst_detail_visible() -> None:
    """A selected subject cannot hide a distinct change kind behind its row limit."""

    report = _report(decision="review_required", exit_code=0)
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="cap-deploy-tool-added",
                direction="added",
                subject_kind="tool",
                tool="deploy_service",
                release_impact="informational",
            ),
            CapabilityChangeMember(
                id="cap-deploy-action-added",
                direction="added",
                subject_kind="action",
                tool="deploy_service",
                action="deploy",
                release_impact="review_required",
            ),
        ],
        broadened=[
            CapabilityChangeMember(
                id="cap-deploy-scope-broadened",
                direction="broadened",
                subject_kind="scope",
                tool="deploy_service",
                scope="production",
                release_impact="informational",
            )
        ],
        narrowed=[
            CapabilityChangeMember(
                id="cap-deploy-policy-narrowed",
                direction="narrowed",
                subject_kind="policy",
                tool="deploy_service",
                action="approval",
                release_impact="blocks_release",
            )
        ],
        removed=[
            CapabilityChangeMember(
                id="cap-deploy-action-removed",
                direction="removed",
                subject_kind="action",
                tool="deploy_service",
                action="rollback",
                release_impact="informational",
            )
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))

    comment = render_pr_comment(verifier, report=report).split("### Agent instruction block", 1)[0]

    assert "narrowed policy approval — blocks release" in comment
    assert "omitted change types: action removed" in comment
    assert "… and 1 more change" in comment


def test_capability_review_reports_requested_but_unavailable_comparison_as_unknown() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.capability_change = CapabilityChangeBlock(enabled=False)
    report.binding_surface_diff = BindingSurfaceDiff(
        enabled=False,
        base_comparison_requested=True,
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))

    comment = render_pr_comment(verifier, report=report)

    assert "newly outside the analysed surface: unknown (comparison unavailable)" in comment
    assert "0 subjects newly outside" not in comment


def test_capability_review_preserves_adversarial_subject_identities_in_markdown() -> None:
    subjects = ["`leading", "trailing`", " leading", "trailing ", " both "]
    report = _report(decision="review_required", exit_code=0)
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id=f"cap-identity-{index}",
                direction="added",
                subject_kind="tool",
                tool=subject,
            )
            for index, subject in enumerate(subjects)
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))

    comment = render_pr_comment(verifier, report=report).split("### Agent instruction block", 1)[0]

    assert "`` `leading ``" in comment
    assert "`` trailing` ``" in comment
    assert "` leading`" in comment
    assert "`trailing `" in comment
    assert "`  both  `" in comment

    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="cap-control-identity",
                direction="added",
                subject_kind="tool",
                tool="line\nbreak",
            )
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))
    comment = render_pr_comment(verifier, report=report).split("### Agent instruction block", 1)[0]
    assert "`line<U+000A>break`" in comment
    assert "line\nbreak" not in comment


def test_capability_review_renders_adversarial_source_paths_as_code() -> None:
    report = _report(decision="review_required", exit_code=0)
    report.findings = [
        Finding(
            id="F-source-markdown",
            check_id="SHIP-TEST",
            title="source path needs review",
            severity="medium",
            category="test",
            recommendation="Review it.",
            source=SourceReference(
                type="openapi",
                path="specs/[prod](unsafe)`tools*.json",
                start_line=17,
            ),
        )
    ]
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="cap-source-markdown",
                direction="added",
                subject_kind="tool",
                tool="safe_subject",
                related_finding_ids=["F-source-markdown"],
            )
        ],
    )
    verifier = _capability_verifier(report, review=build_capability_review(report))

    comment = render_pr_comment(verifier, report=report)

    assert "sources: ``specs/[prod](unsafe)`tools*.json:17``" in comment


def test_capability_delta_subject_rollup_builds_a_missing_legacy_block() -> None:
    """Older in-memory callers still project the existing surface diff."""

    report = _report(decision="review_required", exit_code=0)
    report.capability_change = None
    report.action_surface_diff = ActionSurfaceDiff(
        enabled=True,
        summary=ActionSurfaceDiffSummary(actions_added=1),
        added=[
            ActionSurfaceChange(
                type="ACTION_ADDED",
                action_id="refund",
                tool_name="stripe.create_refund",
                operation="create_refund",
                severity="high",
                reason="Action added: stripe.create_refund",
            )
        ],
    )

    rollup = capability_delta_subject_rollup(report)

    assert rollup.enabled is True
    assert rollup.total_subjects == 1
    assert rollup.added_subjects == 1
    assert rollup.change_count == 1
    assert rollup.subjects[0].subject == "stripe.create_refund"
    assert rollup.subjects[0].change_types == ("action_added",)


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
        manifest_provenance=ManifestProvenance.repository(),
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
        manifest_provenance=ManifestProvenance.repository(),
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
        manifest_provenance=ManifestProvenance.repository(),
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
        manifest_provenance=ManifestProvenance.repository(),
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
        '{"version": "0.1", "project": {"name": "demo"}, "agent": {"name": "assistant"}}\n',
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
        '{"version": "0.1", "project": {"name": "demo"}, "agent": {"name": "assistant"}}\n',
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
    assert payload["artifacts"] == {}
    assert not (workspace / "agents-shipgate-reports").exists()
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
    payload = json.loads([line for line in result.output.splitlines() if line.startswith("{")][-1])
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
    payload = json.loads([line for line in result.output.splitlines() if line.startswith("{")][-1])
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
    payload = json.loads([line for line in result.output.splitlines() if line.startswith("{")][-1])
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


def test_verify_preview_consumes_the_trigger_route_before_publishing_control(
    tmp_path: Path,
) -> None:
    """#414: one preview payload must not tell the caller to preview again."""

    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "agent.py").write_text(
        "from google.adk.agents import LlmAgent\n"
        "root_agent = LlmAgent(name='closer', tools=[])\n",
        encoding="utf-8",
    )
    _commit_all(repo, "add agent")

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
    assert payload["trigger"]["should_run"] is True
    trigger_action = payload["trigger"]["next_action"]
    assert trigger_action == {
        "kind": "command",
        "command": None,
        "why": (
            "The verifier consumed the trigger route; follow "
            "control.next_action for the current operation."
        ),
        "authoritative": False,
        "authoritative_path": "control.next_action",
    }
    assert all(
        rule.get("command") is None for rule in payload["trigger"]["matched_rules"]
    )
    control_action = payload["control"]["next_action"]
    assert control_action["kind"] == "initialize"
    assert " init " in f" {control_action['command']} "
    assert payload["control"]["allowed_next_commands"] == [control_action["command"]]


def test_build_verifier_preserves_trigger_state_but_scrubs_embedded_commands(
    tmp_path: Path,
) -> None:
    """#414: normal verifier construction embeds evidence, never a second route."""

    repo = _init_repo(tmp_path)
    out_dir = repo / "agents-shipgate-reports"
    trigger = {
        "should_run": True,
        "rationale": "the diff input is incomplete",
        "matched_rules": [
            {
                "rule_id": "changed-agent-source",
                "action": "input_required",
                "command": "agents-shipgate verify --preview --json",
            }
        ],
        "next_action": {
            "kind": "input_required",
            "command": "agents-shipgate verify --preview --json",
            "why": "Provide a complete diff.",
        },
    }

    verifier = verify_orchestrator._build_verifier(
        git_root=repo,
        config_path=repo / "shipgate.yaml",
        base=None,
        head="HEAD",
        changed_files=["agent.py"],
        diff_text="",
        trigger=trigger,
        base_status="not_requested",
        base_tree=None,
        base_report=None,
        base_notes=[],
        report=None,
        head_status="skipped",
        head_exit_code=0,
        out_dir=out_dir,
        manifest_provenance_value="unknown",
    )

    assert verifier.trigger["next_action"]["kind"] == "input_required"
    assert verifier.trigger["next_action"]["command"] is None
    assert verifier.trigger["next_action"]["authoritative"] is False
    assert verifier.trigger["matched_rules"][0]["action"] == "input_required"
    assert verifier.trigger["matched_rules"][0]["command"] is None


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
    # The default artifact directory follows the requested workspace, so the
    # nested gate's results sit beside the nested manifest (#363).
    report = json.loads(
        (nested / "agents-shipgate-reports" / "report.json").read_text(encoding="utf-8")
    )
    assert report["project"]["name"] == "nested-test"
    assert not (repo / "agents-shipgate-reports").exists()


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


def _capability_verifier(
    report: ReadinessReport,
    *,
    review: VerifierCapabilityReview,
) -> VerifierArtifact:
    """Minimal verifier envelope for PR-comment capability regressions."""

    assert report.release_decision is not None
    return VerifierArtifact(
        workspace="/tmp/work",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        manifest_provenance=ManifestProvenance.repository(),
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 run_shipgate rule(s) matched."},
        execution="succeeded",
        head_status="succeeded",
        release_decision=report.release_decision,
        decision=report.release_decision.decision,
        merge_verdict="human_review_required",
        applicability="verified",
        control=_human_control("review_required"),
        capability_review=review,
        artifacts={"verifier_json": "agents-shipgate-reports/verifier.json"},
    )


def _capability_action_fact(
    *,
    action_id: str,
    tool_id: str,
    tool_name: str,
    provider: str,
    operation: str,
    effect: str = "read",
    source_path: str | None = None,
    source_start_line: int | None = None,
) -> ActionFact:
    return ActionFact(
        action_id=action_id,
        agent_id="agent:test",
        tool_id=tool_id,
        tool_name=tool_name,
        provider=provider,
        source_type="mcp",
        operation=operation,
        effect=effect,  # type: ignore[arg-type]
        source_path=source_path,
        source_start_line=source_start_line,
        input_schema_hash=f"schema:{action_id}",
        hashes=ActionSurfaceHashes(
            identity_hash=f"identity:{action_id}",
            schema_hash=f"schema:{action_id}",
            policy_hash=f"policy:{action_id}",
            risk_hash=f"risk:{action_id}",
        ),
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
