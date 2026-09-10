"""A frozen wheel must not install the previous engine into an adopter's CI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

from agents_shipgate import __version__, release_source
from agents_shipgate.cli.discovery.ci_workflow import _action_ref, write_ci_workflow
from agents_shipgate.published_release import latest_published_action_ref
from scripts._release_support import ReleaseError
from scripts.verify_wheel_provenance import verify_wheel_provenance

SHA = "a" * 40


def _record(sha: str = SHA, version: str = __version__) -> dict[str, str]:
    return {"schema_version": "shipgate.release_source/v1", "source_commit": sha,
            "package_version": version}


@pytest.fixture
def record_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "release-source.json"
    monkeypatch.setattr(release_source, "_RECORD", path)
    monkeypatch.delenv("AGENTS_SHIPGATE_WORKFLOW_REF", raising=False)
    return path


def test_candidate_workflow_uses_immutable_source_before_and_after_publication(
    record_path: Path, tmp_path: Path,
) -> None:
    record_path.write_text(json.dumps(_record()))
    workspace = tmp_path / "adopter"
    assert write_ci_workflow(workspace).status == "written"
    text = (workspace / ".github/workflows/agents-shipgate.yml").read_text()
    assert f"ThreeMoonsLab/agents-shipgate@{SHA}" in text
    assert "ci_mode: advisory" in text
    assert f'shipgate_version: "{__version__}"' in text
    assert latest_published_action_ref() not in text


def test_ordinary_and_preview_builds_keep_published_fallback(record_path: Path) -> None:
    assert _action_ref() == latest_published_action_ref()


def test_explicit_override_is_preserved_for_valid_candidate(
    record_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_path.write_text(json.dumps(_record()))
    monkeypatch.setenv("AGENTS_SHIPGATE_WORKFLOW_REF", "main")
    assert _action_ref() == "main"


def test_dangling_record_is_invalid_not_absent(record_path: Path) -> None:
    try:
        record_path.symlink_to(record_path.parent / "missing-record")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="release-source"):
        _action_ref()


@pytest.mark.parametrize("contents", [
    "{", "[]", "null", "x" * 4097,
    json.dumps(_record(sha="main")),
    json.dumps(_record(version="0.0.1")),
    json.dumps({**_record(), "qualified": True}),
])
def test_malformed_record_never_uses_override_or_old_release(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, contents: str,
) -> None:
    record_path.write_text(contents)
    monkeypatch.setenv("AGENTS_SHIPGATE_WORKFLOW_REF", "main")
    with pytest.raises(ValueError, match="release-source"):
        _action_ref()


def test_a_corrupt_record_only_fails_the_pin_it_could_get_wrong(tmp_path: Path) -> None:
    """`doctor` is the command an operator reaches for when an install looks wrong.

    The record is read to decide one thing: the ref written into an adopter's
    repository. Evaluating that at import time meant a corrupt record in a
    stamped wheel took the whole CLI down with it, including the diagnostic.
    An override is set here so the refusal is not the override's doing.
    """
    record = tmp_path / "release-source.json"
    record.write_text('{"schema_version": "shipgate.release_source/v1"')
    program = textwrap.dedent(
        f"""
        import pathlib
        from agents_shipgate import release_source

        release_source._RECORD = pathlib.Path({str(record)!r})
        # The import that used to raise: this is what pulls the workflow
        # emitter into every command.
        import agents_shipgate.cli.discovery as discovery

        assert discovery.write_ci_workflow is not None
        from agents_shipgate.cli.discovery import ci_workflow

        try:
            ci_workflow.WORKFLOW_TEMPLATE
        except ValueError as exc:
            print("refused:", exc)
        else:
            raise SystemExit("a corrupt record still rendered a workflow")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program], text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "AGENTS_SHIPGATE_WORKFLOW_REF": "main"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused: Invalid candidate release-source record" in result.stdout


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.check_output(
        ["git", "-C", str(root), *args], env=env, text=True, stderr=subprocess.PIPE,
    ).strip()


@pytest.fixture
def committed_source(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Candidate test")
    _git(root, "config", "user.email", "candidate@example.invalid")
    (root / "tracked.py").write_text("x = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "test candidate")
    return root, _git(root, "rev-parse", "HEAD")


def test_build_provenance_reads_its_own_clean_head(
    committed_source: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hatch_build import candidate_source
    root, sha = committed_source
    monkeypatch.setenv("GIT_DIR", "/missing/not-the-build-repository")
    assert candidate_source(root, sha, __version__) == _record(sha)


@pytest.mark.parametrize("mutation", ["tracked", "staged", "untracked", "wrong-sha", "preview"])
def test_build_refuses_a_guessed_or_modified_source(
    committed_source: tuple[Path, str], mutation: str,
) -> None:
    from hatch_build import candidate_source
    root, sha = committed_source
    version = __version__
    if mutation in {"tracked", "staged"}:
        (root / "tracked.py").write_text("x = 2\n")
        if mutation == "staged":
            _git(root, "add", ".")
    elif mutation == "untracked":
        (root / "new.py").write_text("x = 2\n")
    elif mutation == "wrong-sha":
        sha = SHA
    else:
        version += "+preview.1"
    with pytest.raises(ValueError):
        candidate_source(root, sha, version)


def test_no_git_copy_cannot_assert_source_identity(tmp_path: Path) -> None:
    from hatch_build import candidate_source
    with pytest.raises(subprocess.CalledProcessError):
        candidate_source(tmp_path, SHA, __version__)


def _wheel(root: Path, record: dict | None) -> Path:
    root.mkdir()
    path = root / f"agents_shipgate-{__version__}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("agents_shipgate/__init__.py", "")
        if record is not None:
            archive.writestr("agents_shipgate/_meta/release-source.json", json.dumps(record))
    return path


@pytest.mark.parametrize("record", [None, _record(sha="b" * 40), _record(version="0.0.1")])
def test_identical_wheels_do_not_bypass_source_binding(tmp_path: Path, record: dict | None) -> None:
    wheel = _wheel(tmp_path / "wheel", record)
    with pytest.raises(ReleaseError, match="release-source"):
        verify_wheel_provenance(built_path=wheel, qualified_path=wheel, source_commit=SHA)


def test_both_candidate_and_rebuild_must_bind_the_expected_commit(tmp_path: Path) -> None:
    built = _wheel(tmp_path / "built", _record())
    qualified = _wheel(tmp_path / "qualified", _record(sha="b" * 40))
    with pytest.raises(ReleaseError, match="source commit"):
        verify_wheel_provenance(built_path=built, qualified_path=qualified, source_commit=SHA)
    result = verify_wheel_provenance(built_path=built, qualified_path=built, source_commit=SHA)
    assert result["byte_reproducible"] is True
    assert result["source_commit"] == SHA
