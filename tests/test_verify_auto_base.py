"""Base auto-detection for zero-flag ``agents-shipgate verify``.

When ``--base`` is omitted, verify auto-detects the default branch so the
capability diff exists without the nine-flag canonical incantation. The
detection never fetches, considers only remote refs, and only fires when the
detected ref points at a different commit than the head — diffing a branch
against itself adds scan cost without diff signal. Local ``main``/``master``
must be passed explicitly. ``--no-base`` restores the pure working-tree mode.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

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
    assert any(
        "Skipped local base 'main'" in note and "--base main" in note
        for note in detection.notes
    )


def test_warns_when_stale_local_main_is_skipped(tmp_path: Path) -> None:
    repo = _repo_with_stale_local_main_and_origin_main_head(tmp_path)

    detection = detect_default_base_with_notes(repo)

    assert detection.base is None
    assert any(
        "Skipped local base 'main'" in note
        and "origin/main" in note
        and "--base main" in note
        for note in detection.notes
    )


def test_does_not_warn_when_local_main_matches_selected_origin_main(
    tmp_path: Path,
) -> None:
    repo = _feature_repo_with_local_main_equal_origin_main(tmp_path)

    detection = detect_default_base_with_notes(repo)

    assert detection.base == "origin/main"
    assert not any("Skipped local base 'main'" in note for note in detection.notes)


def test_returns_none_in_empty_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert detect_default_base(repo) is None


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


def test_explicit_base_wins_over_auto_detection(tmp_path: Path) -> None:
    repo = _docs_only_repo_with_origin_main(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD~1")

    result = _verify(repo, "--base", "origin/master")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] == "origin/master"
    assert not any("Auto-detected base" in note for note in payload["base_notes"])


def test_zero_base_verify_warns_but_skips_stale_local_main(tmp_path: Path) -> None:
    repo = _repo_with_stale_local_main_and_origin_main_head(tmp_path)

    result = _verify(repo)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] is None
    assert payload["base_status"] == "not_requested"
    assert any(
        "Skipped local base 'main'" in note
        and "origin/main" in note
        and "--base main" in note
        for note in payload["base_notes"]
    )


def test_zero_base_verify_does_not_warn_for_equivalent_local_main(
    tmp_path: Path,
) -> None:
    repo = _feature_repo_with_local_main_equal_origin_main(tmp_path)

    result = _verify(repo)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] == "origin/main"
    assert not any(
        "Skipped local base 'main'" in note for note in payload["base_notes"]
    )


def test_explicit_local_base_main_remains_supported(tmp_path: Path) -> None:
    repo = _repo_with_stale_local_main_and_origin_main_head(tmp_path)

    result = _verify(repo, "--base", "main")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] == "main"
    assert not any("Skipped local base 'main'" in note for note in payload["base_notes"])


def test_base_help_documents_remote_only_auto_detection() -> None:
    result = runner.invoke(app, ["verify", "--help"], color=True)

    assert result.exit_code == 0, result.output
    normalized_output = " ".join(
        _strip_ansi(result.output).replace("│", " ").split()
    )
    assert "origin/HEAD, origin/main, origin/master" in normalized_output
    assert "origin/main, origin/master, main, master" not in normalized_output
    assert "Local main/master are used only" in normalized_output
