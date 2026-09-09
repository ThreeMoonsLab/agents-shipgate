"""Real regeneration and refusal checks for the committed sample recipes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agents_shipgate.core.current_control import read_current_control

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "regenerate_goldens.py"


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("shipgate_golden_generator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy(root: Path, sample: str = "conductor_agent") -> Path:
    target = root / "samples" / sample
    shutil.copytree(ROOT / "samples" / sample, target)
    return target


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_cli_check_all_goldens_from_another_directory(tmp_path):
    """Normal CI invokes the real --check entry point; no workflow copy needed."""
    before = _files(ROOT / "samples")
    summary = tmp_path / "step-summary.md"
    summary.write_text("caller-owned\n")
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "24 sample artifacts; 0 changed" in result.stdout
    assert _files(ROOT / "samples") == before
    assert summary.read_text() == "caller-owned\n"


def test_two_fresh_repositories_generate_identical_bytes(generator, tmp_path):
    root1, root2 = tmp_path / "one", tmp_path / "a different root"
    _copy(root1)
    _copy(root2)
    first = generator.build_goldens(root1, ["conductor_agent"])
    second = generator.build_goldens(root2, ["conductor_agent"])
    assert first == second
    expected = root1 / "samples/conductor_agent/expected"
    for path, data in first.items():
        (root1 / path).write_bytes(data)
        assert b"\r" not in data
    pointer = read_current_control(expected).pointer
    assert pointer.supersedes is None
    assert set(pointer.artifacts) == {"report", "report_markdown"}
    assert not pointer.control.permissions.merge
    for reference in pointer.artifacts.values():
        data = (expected / reference.path).read_bytes()
        assert reference.size_bytes == len(data)
        assert reference.sha256 == "sha256:" + hashlib.sha256(data).hexdigest()
    assert (
        json.loads((expected / "report.json").read_bytes())["manifest_dir"]
        == "<REPO>/samples/conductor_agent"
    )


@pytest.mark.parametrize(
    "name", ["report.json", "report.md", "summary.json", "current-control.json"]
)
def test_check_names_one_byte_drift_without_writing(generator, tmp_path, capsys, name):
    sample = _copy(tmp_path)
    target = sample / "expected" / name
    target.write_bytes(target.read_bytes() + b" ")
    before = _files(tmp_path)
    assert generator.main(["conductor_agent", "--check"], root=tmp_path) == 1
    assert f"DRIFT samples/conductor_agent/expected/{name}" in capsys.readouterr().out
    assert _files(tmp_path) == before


def test_missing_golden_is_drift_then_regeneration_repairs_it(generator, tmp_path, capsys):
    sample = _copy(tmp_path, "declaration_repair_agent")
    target = sample / "expected/suggested-declarations.yaml"
    expected = target.read_bytes()
    target.unlink()
    assert generator.main(["declaration_repair_agent", "--check"], root=tmp_path) == 1
    assert "suggested-declarations.yaml" in capsys.readouterr().out
    assert not target.exists()
    assert generator.main(["declaration_repair_agent"], root=tmp_path) == 0
    assert target.read_bytes() == expected
    assert generator.main(["declaration_repair_agent", "--check"], root=tmp_path) == 0


@pytest.mark.parametrize("leak", ["generated_report", "other_field", "markdown"])
def test_leaked_temp_path_refuses_all_writes(generator, tmp_path, monkeypatch, capsys, leak):
    _copy(tmp_path)
    before = _files(tmp_path)
    run_scan = generator.run_scan

    def leaking_scan(**kwargs):
        result = run_scan(**kwargs)
        out = kwargs["config_path"].parent / "expected/report.json"
        if leak == "markdown":
            from agents_shipgate.report.markdown import _safe_markdown_text

            markdown = out.with_suffix(".md")
            markdown.write_text(markdown.read_text() + _safe_markdown_text(str(out.parent.parent)))
            return result
        payload = json.loads(out.read_text())
        if leak == "generated_report":
            payload["generated_reports"]["json"] = str(out)
        else:
            payload["project"]["unexpected_path"] = str(out.parent.parent)
        out.write_text(json.dumps(payload))
        return result

    monkeypatch.setattr(generator, "run_scan", leaking_scan)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", "preserve-caller-setting")
    assert generator.main(["conductor_agent"], root=tmp_path) == 2
    assert ("report.md" if leak == "markdown" else "report.json") in capsys.readouterr().err
    assert _files(tmp_path) == before
    assert os.environ["GITHUB_STEP_SUMMARY"] == "preserve-caller-setting"


def test_unowned_artifact_or_sample_is_not_silently_ignored(generator, tmp_path, capsys):
    sample = _copy(tmp_path)
    (sample / "expected/unowned.json").write_text("{}")
    before = _files(tmp_path)
    assert generator.main(["--check"], root=tmp_path) == 2
    assert "unowned.json" in capsys.readouterr().err
    assert generator.main(["../outside"], root=tmp_path) == 2
    assert "No golden recipe" in capsys.readouterr().err
    assert _files(tmp_path) == before


def test_regeneration_does_not_load_opted_in_installed_plugins(generator, monkeypatch):
    from agents_shipgate.checks import registry

    loaded = []

    class Plugin:
        name = "golden-poison"
        value = "golden_poison:checks"

        def load(self):
            loaded.append(True)
            return lambda context: []

    monkeypatch.setenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", "1")
    monkeypatch.setattr(registry, "entry_points", lambda group: [Plugin()])
    generator.build_goldens()
    assert loaded == []
    assert os.environ["AGENTS_SHIPGATE_ENABLE_PLUGINS"] == "1"


@pytest.mark.parametrize("component", ["samples", "expected"])
def test_symlinked_ancestor_cannot_rewrite_external_goldens(generator, tmp_path, capsys, component):
    root, outside = tmp_path / "root", tmp_path / "outside"
    sample = _copy(outside)
    root.mkdir()
    try:
        if component == "samples":
            (root / "samples").symlink_to(outside / "samples", target_is_directory=True)
        else:
            target = _copy(root)
            shutil.rmtree(target / "expected")
            (target / "expected").symlink_to(sample / "expected", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    report = sample / "expected/report.md"
    report.write_bytes(report.read_bytes() + b"outside sentinel\n")
    before = _files(outside)
    assert generator.main(["conductor_agent"], root=root) == 2
    assert "symlink" in capsys.readouterr().err
    assert _files(outside) == before


def test_crlf_checkout_inputs_generate_the_same_artifacts(generator, tmp_path):
    for sample in generator.RECIPES:
        target = _copy(tmp_path, sample)
        for path in target.rglob("*"):
            if path.is_file() and "expected" not in path.relative_to(target).parts:
                path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    result = generator.build_goldens(tmp_path)
    assert all(data == (ROOT / path).read_bytes() for path, data in result.items())


def test_git_context_and_global_exclusions_do_not_change_fixture_state(
    generator, tmp_path, monkeypatch
):
    _copy(tmp_path)
    excludes = tmp_path / "excluded"
    excludes.write_text("*\n")
    config = tmp_path / "global-config"
    config.write_text(f'[core]\n excludesFile = "{excludes.as_posix()}"\n')
    settings = {
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_DIR": str(ROOT / ".git"),
        "GIT_WORK_TREE": str(ROOT),
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    result = generator.build_goldens(tmp_path, ["conductor_agent"])
    assert all(data == (ROOT / path).read_bytes() for path, data in result.items())
    assert all(os.environ[key] == value for key, value in settings.items())
