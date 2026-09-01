"""Issue #326: side-effect-contained external-repository review."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.discovery.local_review import ensure_local_review_excludes
from agents_shipgate.cli.main import app
from agents_shipgate.core.manifest_provenance import (
    LOCAL_REVIEW_MANIFEST_NAME,
    LOCAL_REVIEW_PROVISIONAL_NOTE,
)

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
    assert local["reports"]["recovery"] == {
        "action": "remove_directory",
        "path": str((repo / "agents-shipgate-reports").resolve()),
    }
    effects = {item["kind"]: item for item in local["side_effects"]}
    assert effects["manifest"]["changed"] is True
    assert effects["manifest"]["recovery"]["path"] == str(manifest.resolve())
    exclude = effects["git_private_exclude"]
    assert exclude["changed"] is True
    assert exclude["recovery"]["action"] == "remove_managed_block"
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


def test_preview_routes_unconfigured_repo_to_local_review(tmp_path: Path):
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
