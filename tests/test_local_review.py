"""Issue #326: side-effect-contained external-repository review."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.discovery import local_review as local_review_module
from agents_shipgate.cli.discovery.local_review import (
    LocalReviewExcludeStatus,
    ensure_local_review_excludes,
)
from agents_shipgate.cli.main import app
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.manifest_provenance import (
    LOCAL_REVIEW_MANIFEST_NAME,
    LOCAL_REVIEW_PROVISIONAL_NOTE,
)
from agents_shipgate.schemas.verification import VerificationContext

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
runner = CliRunner()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")


def test_init_local_review_leaves_clean_checkout_and_lists_recovery(tmp_path: Path):
    repo = tmp_path / "external-agent"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "from agents import Agent, function_tool\n"
        "@function_tool\n"
        "def lookup(ticket: str) -> str:\n"
        "    return ticket\n"
        "agent = Agent(name='support', instructions='Read tickets', tools=[lookup])\n",
        encoding="utf-8",
    )
    _init_repo(repo)
    _git(repo, "add", "agent.py")
    _git(repo, "commit", "-qm", "fixture")

    result = runner.invoke(
        app,
        ["init", "--workspace", str(repo), "--local-review", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    manifest = repo / LOCAL_REVIEW_MANIFEST_NAME
    assert manifest.is_file()
    assert not (repo / ".gitignore").exists()
    local = payload["local_review"]
    assert local["ephemeral"] is True
    assert local["release_authoritative"] is False
    assert local["reports"]["created_by_init"] is False
    cleanup_command = local["cleanup_command"]
    assert "--local-review --undo --json" in cleanup_command
    assert local["reports"]["recovery"] == {
        "action": "run_command",
        "command": cleanup_command,
    }
    effects = {item["kind"]: item for item in local["side_effects"]}
    assert effects["manifest"]["changed"] is True
    assert effects["manifest"]["recovery"]["command"] == cleanup_command
    exclude = effects["git_private_exclude"]
    assert exclude["changed"] is True
    assert exclude["recovery"]["action"] == "run_command"
    assert exclude["recovery"]["command"] == cleanup_command
    assert Path(exclude["path"]).is_file()
    assert (
        "--config" in (payload["next_actions"][0].get("command") or "")
        or payload["control"]["next_action"]["actor"] == "human"
    )

    reports = repo / "agents-shipgate-reports"
    reports.mkdir()
    (reports / "probe.json").write_text("{}\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain", "--untracked-files=all").stdout == ""


def test_local_review_exclude_is_idempotent_and_scoped(tmp_path: Path):
    repo = tmp_path / "monorepo"
    project = repo / "apps" / "support"
    project.mkdir(parents=True)
    _init_repo(repo)
    first = ensure_local_review_excludes(project)
    second = ensure_local_review_excludes(project)

    assert first.changed is True
    assert second.changed is False
    text = Path(first.path).read_text(encoding="utf-8")
    assert text.count(first.start_marker) == 1
    assert "/apps/support/.agents-shipgate-local-review.yaml" in text
    assert "/apps/support/agents-shipgate-reports/" in text


def test_local_review_manifest_forces_provisional_verifier_artifacts(tmp_path: Path):
    repo = tmp_path / "clean-agent"
    shutil.copytree(SAMPLE, repo)
    durable = repo / "shipgate.yaml"
    manifest_text = durable.read_text(encoding="utf-8")
    durable.unlink()
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    ensure_local_review_excludes(repo)
    local_manifest = repo / LOCAL_REVIEW_MANIFEST_NAME
    local_manifest.write_text(manifest_text, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            str(local_manifest),
            "--no-base",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    verifier = json.loads(result.output)
    assert verifier["decision"] != "passed"
    assert verifier["merge_verdict"] != "mergeable"
    assert verifier["can_merge_without_human"] is False
    assert LOCAL_REVIEW_PROVISIONAL_NOTE in verifier["base_notes"]

    reports = repo / "agents-shipgate-reports"
    report = json.loads((reports / "report.json").read_text(encoding="utf-8"))
    finding = next(
        row
        for row in report["findings"]
        if row["check_id"] == "SHIP-VERIFY-LOCAL-REVIEW-PROVISIONAL"
    )
    assert finding["evidence"]["manifest_provenance"] == "local_review"
    assert finding["evidence"]["release_authoritative"] is False

    handoff = json.loads((reports / "agent-handoff.json").read_text(encoding="utf-8"))
    assert any(
        row["check_id"] == "SHIP-VERIFY-LOCAL-REVIEW-PROVISIONAL" for row in handoff["blocked_by"]
    )
    plan = json.loads((reports / "verification-plan.json").read_text(encoding="utf-8"))
    assert plan["inputs"]["options"]["manifest_provenance"] == "local_review"
    receipt = json.loads((reports / "verification-receipt.json").read_text(encoding="utf-8"))
    assert receipt["decision"] != "passed"
    assert receipt["can_merge_without_human"] is False
    assert verifier["control"]["next_action"]["actor"] == "human"
    assert "--write --json" in verifier["control"]["next_action"]["why"]
    assert verifier["control"]["allowed_next_commands"] == [
        f"agents-shipgate init --workspace {repo} --write --json"
    ]
    adoption = next(
        repair
        for repair in verifier["fix_task"]["allowed_repairs"]
        if repair["kind"] == "durable_adoption"
    )
    assert adoption["actor"] == "human"
    assert adoption["command"] == verifier["control"]["allowed_next_commands"][0]
    assert _git(repo, "status", "--porcelain", "--untracked-files=all").stdout == ""


def test_ignored_custom_manifest_cannot_launder_release_authority(tmp_path: Path):
    repo = tmp_path / "custom-review-manifest"
    shutil.copytree(SAMPLE, repo)
    manifest_text = (repo / "shipgate.yaml").read_text(encoding="utf-8")
    (repo / "shipgate.yaml").unlink()
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")

    custom_manifest = repo / "review-policy.yaml"
    custom_manifest.write_text(manifest_text, encoding="utf-8")
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text(
        f"/{custom_manifest.name}\n/agents-shipgate-reports/\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            str(custom_manifest),
            "--no-base",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    verifier = json.loads(result.output)
    assert verifier["decision"] != "passed"
    assert verifier["merge_verdict"] != "mergeable"
    report = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text(encoding="utf-8")
    )
    finding = next(
        row
        for row in report["findings"]
        if row["check_id"] == "SHIP-VERIFY-LOCAL-REVIEW-PROVISIONAL"
    )
    assert finding["evidence"]["manifest_provenance"] == "uncommitted"
    plan = json.loads(
        (repo / "agents-shipgate-reports" / "verification-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["inputs"]["options"]["manifest_provenance"] == "uncommitted"
    assert _git(repo, "status", "--porcelain", "--untracked-files=all").stdout == ""


def test_preview_keeps_durable_adoption_as_the_default_route(tmp_path: Path):
    repo = tmp_path / "external-agent"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "from agents import Agent\nagent = Agent(name='support', instructions='help')\n",
        encoding="utf-8",
    )
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")

    result = runner.invoke(
        app,
        ["verify", "--preview", "--workspace", str(repo), "--format", "control"],
    )
    assert result.exit_code == 0, result.output
    control = json.loads(result.output)
    command = control["next_action"]["command"]
    assert "--write" in command
    assert "--local-review" not in command


def test_preview_routes_to_local_review_only_on_explicit_reserved_config(tmp_path: Path):
    repo = tmp_path / "external-agent"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "from agents import Agent\nagent = Agent(name='support', instructions='help')\n",
        encoding="utf-8",
    )
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")

    result = runner.invoke(
        app,
        [
            "verify",
            "--preview",
            "--workspace",
            str(repo),
            "--config",
            LOCAL_REVIEW_MANIFEST_NAME,
            "--format",
            "control",
        ],
    )

    assert result.exit_code == 0, result.output
    command = json.loads(result.output)["next_action"]["command"]
    assert "--local-review" in command
    assert "--write" not in command


def test_local_review_rejects_durable_setup_flags_before_writing(tmp_path: Path):
    repo = tmp_path / "external-agent"
    repo.mkdir()
    _init_repo(repo)

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(repo),
            "--local-review",
            "--write",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "cannot be combined" in result.output
    assert not (repo / LOCAL_REVIEW_MANIFEST_NAME).exists()
    assert not (repo / ".gitignore").exists()


def test_local_review_archive_verification_uses_worktree_manifest_for_pr_diff(
    tmp_path: Path,
):
    repo = tmp_path / "external-pr"
    shutil.copytree(SAMPLE, repo)
    manifest_text = (repo / "shipgate.yaml").read_text(encoding="utf-8")
    (repo / "shipgate.yaml").unlink()
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    tool_source = repo / "tools.json"
    tool_source.write_text(
        tool_source.read_text(encoding="utf-8").replace(
            '"description": "Look up internal documentation metadata for an existing article."',
            '"description": "Read internal documentation metadata for an existing article."',
        ),
        encoding="utf-8",
    )
    _git(repo, "add", "tools.json")
    _git(repo, "commit", "-qm", "head")
    ensure_local_review_excludes(repo)
    local_manifest = repo / LOCAL_REVIEW_MANIFEST_NAME
    local_manifest.write_text(manifest_text, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            str(local_manifest),
            "--base",
            "HEAD~1",
            "--head",
            "HEAD",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    verifier = json.loads(result.output)
    assert verifier["changed_files"] == ["tools.json"]
    assert verifier["diff_status"]["completeness"] == "complete"
    assert verifier["decision"] == "review_required"
    assert verifier["merge_verdict"] == "human_review_required"
    plan = json.loads(
        (repo / "agents-shipgate-reports" / "verification-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["inputs"]["options"]["manifest_provenance"] == "local_review"
    assert _git(repo, "status", "--porcelain", "--untracked-files=all").stdout == ""


def test_scan_changed_files_fails_closed_for_ephemeral_manifest(tmp_path: Path):
    repo = tmp_path / "scan-review"
    shutil.copytree(SAMPLE, repo)
    manifest_text = (repo / "shipgate.yaml").read_text(encoding="utf-8")
    (repo / "shipgate.yaml").unlink()
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    local_manifest = repo / LOCAL_REVIEW_MANIFEST_NAME
    local_manifest.write_text(manifest_text, encoding="utf-8")
    changed = repo / "changed.txt"
    changed.write_text("tools.json\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(local_manifest),
            "--changed-files",
            str(changed),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(
        (repo / "agents-shipgate-reports" / "report.json").read_text(encoding="utf-8")
    )
    assert report["release_decision"]["decision"] == "review_required"
    finding = next(
        row
        for row in report["findings"]
        if row["check_id"] == "SHIP-VERIFY-LOCAL-REVIEW-PROVISIONAL"
    )
    assert finding["evidence"]["manifest_provenance"] == "local_review"


def test_local_review_refusal_has_no_false_side_effect_inventory(tmp_path: Path):
    repo = tmp_path / "monorepo"
    for name in ("one", "two"):
        project = repo / name
        project.mkdir(parents=True)
        (project / "agent.py").write_text(
            "from agents import Agent\nagent = Agent(name='support', instructions='help')\n",
            encoding="utf-8",
        )
        (project / "pyproject.toml").write_text(
            f"[project]\nname = {name!r}\nversion = '0.1.0'\n",
            encoding="utf-8",
        )
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")

    result = runner.invoke(
        app,
        ["init", "--workspace", str(repo), "--local-review", "--json"],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "refused_unresolved_scope"
    assert "local_review" not in payload
    assert not (repo / LOCAL_REVIEW_MANIFEST_NAME).exists()
    assert "agents-shipgate:local-review" not in (
        repo / ".git" / "info" / "exclude"
    ).read_text(encoding="utf-8")


def test_local_review_failure_emits_structured_agent_error(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "not-git"
    workspace.mkdir()
    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")

    result = runner.invoke(
        app,
        ["init", "--workspace", str(workspace), "--local-review", "--json"],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert payload["error"] == "config_error"
    assert payload["control"]["control_state"] == "human_review_required"
    assert payload["next_actions"][0]["kind"] == "review"


def test_local_review_refuses_to_shadow_existing_repository_manifest(tmp_path: Path):
    repo = tmp_path / "adopted"
    shutil.copytree(SAMPLE, repo)
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")

    result = runner.invoke(
        app,
        ["init", "--workspace", str(repo), "--local-review", "--json"],
    )

    assert result.exit_code == 2, result.output
    assert "shipgate.yaml already exists" in result.output
    assert not (repo / LOCAL_REVIEW_MANIFEST_NAME).exists()


def test_local_review_refuses_linked_worktree_shared_exclude(tmp_path: Path):
    repo = tmp_path / "main"
    linked = tmp_path / "linked"
    repo.mkdir()
    _init_repo(repo)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    _git(repo, "worktree", "add", "-q", "-b", "review", str(linked))

    with pytest.raises(ConfigError, match="linked Git worktree"):
        ensure_local_review_excludes(linked)
    assert "agents-shipgate:local-review" not in (
        repo / ".git" / "info" / "exclude"
    ).read_text(encoding="utf-8")


def test_local_review_marker_version_migrates_without_orphaning_block(
    tmp_path: Path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    first = ensure_local_review_excludes(repo)
    monkeypatch.setattr(local_review_module, "LOCAL_REVIEW_EXCLUDE_VERSION", 2)

    migrated = ensure_local_review_excludes(repo)

    assert migrated.status is LocalReviewExcludeStatus.MIGRATED
    text = Path(first.path).read_text(encoding="utf-8")
    assert text.count("agents-shipgate:local-review:start") == 1
    assert "start v=2" in text


def test_local_review_undo_removes_manifest_and_block_but_keeps_reports(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "from agents import Agent\nagent = Agent(name='support', instructions='help')\n",
        encoding="utf-8",
    )
    _init_repo(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    created = runner.invoke(
        app,
        ["init", "--workspace", str(repo), "--local-review", "--json"],
    )
    assert created.exit_code == 0, created.output
    reports = repo / "agents-shipgate-reports"
    reports.mkdir()
    (reports / "report.json").write_text("{}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(repo),
            "--local-review",
            "--undo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "local_review_removed"
    assert not (repo / LOCAL_REVIEW_MANIFEST_NAME).exists()
    assert reports.is_dir()
    assert "agents-shipgate:local-review" not in (
        repo / ".git" / "info" / "exclude"
    ).read_text(encoding="utf-8")
    assert "agents-shipgate-reports/report.json" in _git(
        repo, "status", "--porcelain", "--untracked-files=all"
    ).stdout


def test_missing_manifest_does_not_publish_a_provenance_claim(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "missing.yaml",
            "--no-base",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    verifier = json.loads(result.output)
    assert not any("Manifest provenance" in note for note in verifier["base_notes"])
    assert verifier["diff_status"]["detail"] == "verify stopped at the missing manifest."


def test_verification_context_defaults_manifest_provenance_closed() -> None:
    assert VerificationContext().manifest_provenance == "unknown"
