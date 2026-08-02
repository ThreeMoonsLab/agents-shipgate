"""Base auto-detection for zero-flag ``agents-shipgate verify``.

When ``--base`` is omitted, verify auto-detects the default branch so the
capability diff exists without the nine-flag canonical incantation. Detection
never fetches and never selects a local ``main``/``master`` implicitly. It must
either select a trustworthy remote ref, prove the head is already at the
default, or stop before scanning. ``--no-base`` is the only explicit opt-out.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.git import (
    detect_default_base,
    detect_default_base_with_notes,
)

runner = CliRunner()

_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_CSI.sub("", text)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    return repo


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message)


def _docs_only_repo_with_origin_main(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
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
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "README.md").write_text("docs only\n", encoding="utf-8")
    _commit_all(repo, "docs")
    return repo


def _feature_repo_with_local_main_equal_origin_main(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
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
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "README.md").write_text("feature\n", encoding="utf-8")
    _commit_all(repo, "feature")
    return repo


def _feature_repo_with_only_local_main(tmp_path: Path) -> Path:
    repo = _feature_repo_with_local_main_equal_origin_main(tmp_path)
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    return repo


def _repo_with_stale_local_main_and_origin_main_head(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
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
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _git(repo, "checkout", "-q", "--detach", "main")
    (repo / "README.md").write_text("remote main\n", encoding="utf-8")
    _commit_all(repo, "remote main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


# --- detect_default_base ------------------------------------------------------


def test_detects_origin_main_when_it_differs_from_head(tmp_path: Path) -> None:
    repo = _docs_only_repo_with_origin_main(tmp_path)
    assert detect_default_base(repo) == "origin/main"


def test_does_not_detect_when_only_ref_is_head_itself(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    # Local ``main`` is the current branch — same commit as HEAD.
    assert detect_default_base(repo) is None


def test_detects_origin_master_fallback(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    (repo / "README.md").write_text("more\n", encoding="utf-8")
    _commit_all(repo, "more")
    assert detect_default_base(repo) == "origin/master"


def test_does_not_auto_detect_local_main_from_feature_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "README.md").write_text("feature\n", encoding="utf-8")
    _commit_all(repo, "feature")
    assert detect_default_base(repo) is None
    detection = detect_default_base_with_notes(repo)
    assert detection.base is None
    assert detection.state == "selection_required"
    assert detection.candidates == ("main",)
    assert any("select --base explicitly" in note for note in detection.notes)


def test_warns_when_stale_local_main_is_skipped(tmp_path: Path) -> None:
    repo = _repo_with_stale_local_main_and_origin_main_head(tmp_path)

    detection = detect_default_base_with_notes(repo)

    assert detection.base is None
    assert detection.state == "head_at_default"
    assert detection.default_ref == "origin/main"
    assert any(
        "Skipped local base 'main'" in note and "origin/main" in note and "--base main" in note
        for note in detection.notes
    )


def test_does_not_warn_when_local_main_matches_selected_origin_main(
    tmp_path: Path,
) -> None:
    repo = _feature_repo_with_local_main_equal_origin_main(tmp_path)

    detection = detect_default_base_with_notes(repo)

    assert detection.base == "origin/main"
    assert detection.state == "selected"
    assert not any("Skipped local base 'main'" in note for note in detection.notes)


def test_returns_none_in_empty_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert detect_default_base(repo) is None
    assert detect_default_base_with_notes(repo).state == "selection_required"


def test_origin_head_missing_target_requires_fetch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _git(
        repo,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/trunk",
    )

    detection = detect_default_base_with_notes(repo)

    assert detection.state == "fetch_required"
    assert detection.base is None
    assert detection.default_ref == "origin/trunk"


def test_divergent_remote_candidates_require_selection(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "README.md").write_text("master\n", encoding="utf-8")
    _commit_all(repo, "master")
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")

    detection = detect_default_base_with_notes(repo, "HEAD~1")

    assert detection.state == "selection_required"
    assert detection.candidates == ("origin/main", "origin/master")


def test_equivalent_remote_candidates_select_stable_main_name(tmp_path: Path) -> None:
    repo = _docs_only_repo_with_origin_main(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/master", "origin/main")

    detection = detect_default_base_with_notes(repo)

    assert detection.state == "selected"
    assert detection.base == "origin/main"
    assert detection.candidates == ("origin/main", "origin/master")


def test_configured_origin_without_refs_requires_fetch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    _git(repo, "remote", "add", "origin", "https://example.com/acme/repo.git")

    detection = detect_default_base_with_notes(repo)

    assert detection.state == "fetch_required"
    assert detection.default_ref is None


# --- CLI wiring ---------------------------------------------------------------


def _verify(repo: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--format",
            "json",
            *extra,
        ],
    )


def test_zero_base_verify_auto_detects_origin_main(tmp_path: Path) -> None:
    repo = _docs_only_repo_with_origin_main(tmp_path)

    result = _verify(repo)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] == "origin/main"
    assert any("Auto-detected base" in note for note in payload["base_notes"])


def test_no_base_flag_disables_auto_detection(tmp_path: Path) -> None:
    repo = _docs_only_repo_with_origin_main(tmp_path)

    result = _verify(repo, "--no-base")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] is None
    assert payload["base_status"] == "not_requested"
    plan = json.loads(
        (repo / "agents-shipgate-reports" / "verification-plan.json").read_text(encoding="utf-8")
    )
    assert plan["inputs"]["options"]["base_mode"] == "disabled"
    assert plan["inputs"]["options"]["base_resolution"] == "disabled"


def test_explicit_base_wins_over_auto_detection(tmp_path: Path) -> None:
    repo = _docs_only_repo_with_origin_main(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD~1")

    result = _verify(repo, "--base", "origin/master")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] == "origin/master"
    assert not any("Auto-detected base" in note for note in payload["base_notes"])
    plan = json.loads(
        (repo / "agents-shipgate-reports" / "verification-plan.json").read_text(encoding="utf-8")
    )
    assert plan["inputs"]["options"]["base_mode"] == "explicit"
    assert plan["inputs"]["options"]["base_resolution"] == "selected"


def test_zero_base_verify_warns_but_skips_stale_local_main(tmp_path: Path) -> None:
    repo = _repo_with_stale_local_main_and_origin_main_head(tmp_path)

    result = _verify(repo)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] is None
    assert payload["base_status"] == "not_requested"
    assert any(
        "Skipped local base 'main'" in note and "origin/main" in note and "--base main" in note
        for note in payload["base_notes"]
    )


def test_zero_base_verify_fails_closed_without_trusted_comparison(
    tmp_path: Path,
) -> None:
    repo = _feature_repo_with_only_local_main(tmp_path)
    out = repo / "agents-shipgate-reports"
    out.mkdir()
    for name in ("report.json", "capabilities.lock.json", "verification-receipt.json"):
        (out / name).write_text('{"stale":true}\n', encoding="utf-8")

    with patch("agents_shipgate.cli.verify.orchestrator.run_scan") as run_scan:
        result = _verify(repo)

    assert result.exit_code == 2, result.output
    run_scan.assert_not_called()
    payload = json.loads(result.output)
    assert payload["base_ref"] is None
    assert payload["base_status"] == "ref_missing"
    assert payload["execution"] == "failed"
    assert payload["head_exit_code"] == 2
    assert payload["release_decision"] is None
    assert payload["decision"] is None
    assert payload["merge_verdict"] == "unknown"
    assert payload["applicability"] == "failed"
    assert payload["can_merge_without_human"] is False
    assert payload["fix_task"] is None
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["allowed_next_commands"] == []
    assert "did not run a head-only scan" in payload["headline"]
    assert not (out / "report.json").exists()
    assert not (out / "capabilities.lock.json").exists()
    assert not (out / "verification-receipt.json").exists()
    assert (out / "verifier.json").is_file()
    assert (out / "verify-run.json").is_file()
    assert (out / "agent-handoff.json").is_file()
    plan = json.loads((out / "verification-plan.json").read_text(encoding="utf-8"))
    assert (
        plan["inputs"]["options"]
        | {
            "base_mode": "auto",
            "base_resolution": "selection_required",
            "resolved_default_ref": None,
        }
        == plan["inputs"]["options"]
    )

    first_request_id = plan["request_id"]
    second = _verify(repo)
    assert second.exit_code == 2, second.output
    second_plan = json.loads((out / "verification-plan.json").read_text(encoding="utf-8"))
    assert second_plan["request_id"] == first_request_id


def test_zero_base_missing_remote_default_routes_fetch_without_command(
    tmp_path: Path,
) -> None:
    repo = _feature_repo_with_only_local_main(tmp_path)
    _git(repo, "remote", "add", "origin", "https://example.com/acme/repo.git")

    with patch("agents_shipgate.cli.verify.orchestrator.run_scan") as run_scan:
        result = _verify(repo)

    assert result.exit_code == 2, result.output
    run_scan.assert_not_called()
    payload = json.loads(result.output)
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert payload["control"]["allowed_next_commands"] == []
    assert payload["release_decision"] is None


def test_local_default_at_head_is_safe_without_remote(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: test
agent:
  name: test-agent
  declared_purpose: [test]
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""".lstrip(),
        encoding="utf-8",
    )
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    _commit_all(repo, "base")

    result = _verify(repo)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] is None
    plan = json.loads(
        (repo / "agents-shipgate-reports" / "verification-plan.json").read_text(encoding="utf-8")
    )
    assert plan["inputs"]["options"]["base_resolution"] == "head_at_default"


def test_local_default_at_head_includes_dirty_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project: {name: test}
agent:
  name: test-agent
  declared_purpose: [test]
environment: {target: local}
tool_sources:
  - {id: tools, type: mcp, path: tools.json}
""".lstrip(),
        encoding="utf-8",
    )
    (repo / "tools.json").write_text('{"tools":[]}\n', encoding="utf-8")
    (repo / "staged.txt").write_text("base\n", encoding="utf-8")
    (repo / "unstaged.txt").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")
    (repo / "unstaged.txt").write_text("unstaged\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    result = _verify(repo)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {"staged.txt", "unstaged.txt", "untracked.txt"} <= set(payload["changed_files"])


def test_explicit_local_base_recovers_committed_and_worktree_scope(
    tmp_path: Path,
) -> None:
    repo = _feature_repo_with_only_local_main(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    unresolved = _verify(repo)
    explicit = _verify(repo, "--base", "main")

    assert unresolved.exit_code == 2, unresolved.output
    assert explicit.exit_code == 0, explicit.output
    payload = json.loads(explicit.output)
    assert {"README.md", "dirty.txt"} <= set(payload["changed_files"])


def test_shallow_missing_merge_base_routes_fetch(tmp_path: Path) -> None:
    repo = _feature_repo_with_local_main_equal_origin_main(tmp_path)

    with (
        patch("agents_shipgate.cli.verify.orchestrator.merge_base_sha", return_value=None),
        patch("agents_shipgate.cli.verify.orchestrator.is_shallow_repository", return_value=True),
        patch("agents_shipgate.cli.verify.orchestrator.run_scan") as run_scan,
    ):
        result = _verify(repo, "--base", "origin/main")

    assert result.exit_code == 2, result.output
    run_scan.assert_not_called()
    payload = json.loads(result.output)
    assert payload["base_status"] == "archive_failed"
    assert payload["control"]["state"] == "agent_action_required"
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert "shallow repository" in payload["control"]["next_action"]["why"]


def test_unrelated_history_requires_human_selection(tmp_path: Path) -> None:
    repo = _feature_repo_with_local_main_equal_origin_main(tmp_path)

    with (
        patch("agents_shipgate.cli.verify.orchestrator.merge_base_sha", return_value=None),
        patch("agents_shipgate.cli.verify.orchestrator.is_shallow_repository", return_value=False),
        patch("agents_shipgate.cli.verify.orchestrator.run_scan") as run_scan,
    ):
        result = _verify(repo, "--base", "origin/main")

    assert result.exit_code == 2, result.output
    run_scan.assert_not_called()
    payload = json.loads(result.output)
    assert payload["base_status"] == "archive_failed"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["allowed_next_commands"] == []
    assert "not shallow" in payload["control"]["next_action"]["why"]


def test_zero_base_verify_does_not_warn_for_equivalent_local_main(
    tmp_path: Path,
) -> None:
    repo = _feature_repo_with_local_main_equal_origin_main(tmp_path)

    result = _verify(repo)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] == "origin/main"
    assert not any("Skipped local base 'main'" in note for note in payload["base_notes"])


def test_explicit_local_base_main_remains_supported(tmp_path: Path) -> None:
    repo = _repo_with_stale_local_main_and_origin_main_head(tmp_path)

    result = _verify(repo, "--base", "main")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] == "main"
    assert not any("Skipped local base 'main'" in note for note in payload["base_notes"])


def test_check_recovery_command_cannot_fall_through_to_head_only(
    tmp_path: Path,
) -> None:
    repo = _feature_repo_with_only_local_main(tmp_path)
    (repo / "tools.json").write_text(
        '{"tools":[{"name":"search","description":"Search records."}]}\n',
        encoding="utf-8",
    )

    check_result = runner.invoke(
        app,
        [
            "check",
            "--agent",
            "codex",
            "--workspace",
            str(repo),
            "--format",
            "agent-boundary-json",
        ],
    )

    assert check_result.exit_code == 0, check_result.output
    check_payload = json.loads(check_result.output)
    assert check_payload["control"]["state"] == "agent_action_required"
    verify_commands = [
        command
        for command in check_payload["control"]["allowed_next_commands"]
        if "agents-shipgate verify" in command
    ]
    assert len(verify_commands) == 1
    assert " --base " not in verify_commands[0]
    assert " --no-base" not in verify_commands[0]

    verify_result = _verify(repo)

    assert verify_result.exit_code == 2, verify_result.output
    verify_payload = json.loads(verify_result.output)
    assert verify_payload["control"]["state"] == "human_review_required"
    assert verify_payload["release_decision"] is None
    assert "did not run a head-only scan" in verify_payload["headline"]


def test_base_help_documents_remote_only_auto_detection() -> None:
    result = runner.invoke(app, ["verify", "--help"], color=True)

    assert result.exit_code == 0, result.output
    normalized_output = " ".join(_strip_ansi(result.output).replace("│", " ").split())
    assert "origin/HEAD, origin/main, origin/master" in normalized_output
    assert "origin/main, origin/master, main, master" not in normalized_output
    assert "Local main/master are used only" in normalized_output
    assert "exits 2 without running a head-only scan" in normalized_output
    assert "intentional head/worktree-only verification" in normalized_output
