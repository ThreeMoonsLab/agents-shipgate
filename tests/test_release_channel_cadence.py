"""An advisory v* release must not report the gate line's cadence as kept (#648)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts import release_cadence as cadence
from scripts import release_channel as rc


def _git(repo: Path, *args: str, when: int | None = None) -> None:
    env = {**os.environ}
    if when is not None:
        stamp = f"@{when} +0000"
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _release(repo: Path, tag: str, when: int, channels: dict[str, str] | None) -> None:
    declaration = repo / rc.DECLARATION_PATH
    if channels is None:
        declaration.unlink(missing_ok=True)
    else:
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text(json.dumps({"schema": rc.SCHEMA, "channels": channels}), encoding="utf-8")
    (repo / "VERSION").write_text(tag, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", tag, when=when)
    _git(repo, "tag", tag)


def _history(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@e.invalid")
    _git(repo, "config", "user.name", "T")
    day = 86_400
    _release(repo, "v0.15.0", 10 * day, None)                                   # before declarations
    _release(repo, "v1.0.0", 20 * day, {"1.0.0": "advisory"})                   # advisory v*
    _release(repo, "v1.1.0", 30 * day, {"1.0.0": "advisory", "1.1.0": "qualified"})
    _release(repo, "v1.2.0", 40 * day, {"1.0.0": "advisory", "1.1.0": "qualified"})  # undeclared
    return repo


def test_each_tag_reads_the_channel_its_own_tree_declared(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    assert cadence.tag_channel(repo, "v0.15.0") is None
    assert cadence.tag_channel(repo, "v1.0.0") == "advisory"
    assert cadence.tag_channel(repo, "v1.1.0") == "qualified"
    assert cadence.tag_channel(repo, "v1.2.0") == "undeclared"


def test_an_advisory_v_release_is_not_a_gate_line_release(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    gate = [ref for ref, _ in cadence.read_gate_line_tags(repo)]
    assert gate == ["v1.1.0", "v0.15.0"]


def test_an_advisory_v_release_counts_on_the_advisory_line(tmp_path: Path) -> None:
    repo = _history(tmp_path)
    advisory = [ref for ref, _ in cadence.read_advisory_tags(repo)]
    assert advisory == ["v1.0.0"]


def test_an_undeclared_tag_is_on_neither_line(tmp_path: Path) -> None:
    """The release workflow refuses it, so it was never a release of either kind."""

    repo = _history(tmp_path)
    gate = {ref for ref, _ in cadence.read_gate_line_tags(repo)}
    advisory = {ref for ref, _ in cadence.read_advisory_tags(repo)}
    assert "v1.2.0" not in gate | advisory


def test_the_cli_gate_line_never_reports_an_advisory_tag_as_latest(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@e.invalid")
    _git(repo, "config", "user.name", "T")
    _release(repo, "v0.15.0", 86_400, None)
    _release(repo, "v1.0.0", 2 * 86_400, {"1.0.0": "advisory"})
    assert cadence.main(["--repo", str(repo), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["latest_release_tag"] == "v0.15.0"
    assert report["advisory"]["latest_release_tag"] == "v1.0.0"
    # The cadence JSON names its date basis by key: a preview is dated by its
    # build stamp (``built_at``), a tag by its own date (``tagged_at``).
    assert "tagged_at" in report["advisory"]
    assert "built_at" not in report["advisory"]
