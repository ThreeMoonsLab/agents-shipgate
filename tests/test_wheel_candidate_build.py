"""Exercise the actual backend; a mock record cannot prove distribution behavior."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MEMBER = "agents_shipgate/_meta/release-source.json"


def test_actual_candidate_build_is_reproducible_and_does_not_leak_into_next_build(
    tmp_path: Path,
) -> None:
    pytest.importorskip("hatchling")
    source = tmp_path / "source"
    package = source / "src/agents_shipgate"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "1.0.0"\n')
    extras = source / "extras"
    extras.mkdir()
    (extras / "reviewed.json").write_text("{}\n")
    (source / ".gitignore").write_text("extras/*.scratch\n")
    shutil.copyfile(ROOT / "hatch_build.py", source / "hatch_build.py")
    (source / "pyproject.toml").write_text('''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "agents-shipgate"
version = "1.0.0"
[tool.hatch.build.targets.wheel]
packages = ["src/agents_shipgate"]
exclude = ["/src/agents_shipgate/_meta/release-source.json"]
[tool.hatch.build.targets.wheel.force-include]
"extras" = "agents_shipgate/_meta/extras"
[tool.hatch.build.hooks.custom]
path = "hatch_build.py"
''')
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("AGENTS_SHIPGATE_CANDIDATE_SOURCE_COMMIT", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(source), *args], text=True, env=env, stderr=subprocess.PIPE,
        ).strip()

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Candidate test", "-c", "user.email=build@example.invalid",
        "commit", "-qm", "candidate")
    sha = git("rev-parse", "HEAD")

    def build(name: str, candidate: bool, expected_error: str | None = None) -> Path | None:
        build_env = dict(env)
        if candidate:
            build_env["AGENTS_SHIPGATE_CANDIDATE_SOURCE_COMMIT"] = sha
        result = subprocess.run(
            [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path / name)],
            cwd=source, env=build_env, capture_output=True, text=True, check=False,
        )
        if expected_error:
            assert result.returncode != 0
            assert expected_error in result.stdout + result.stderr
            assert not list((tmp_path / name).glob("*.whl"))
            return None
        assert result.returncode == 0, result.stdout + result.stderr
        return next((tmp_path / name).glob("*.whl"))

    first = build("first", True)
    second = build("second", True)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert json.loads(archive.read(MEMBER)) == {
            "schema_version": "shipgate.release_source/v1",
            "source_commit": sha, "package_version": "1.0.0",
        }
    assert not (package / "_meta/release-source.json").exists()
    ordinary = build("ordinary", False)
    with zipfile.ZipFile(ordinary) as archive:
        assert MEMBER not in archive.namelist()
    assert git("status", "--porcelain") == ""
    with (source / ".git/info/exclude").open("a") as handle:
        handle.write("\nsrc/agents_shipgate/untracked.py\n")
    hidden_source = package / "untracked.py"
    hidden_source.write_text("print('not committed')\n")
    assert git("status", "--porcelain") == ""
    build("hidden-source", True, "not a regular tracked HEAD file")
    hidden_source.unlink()
    hidden_force = extras / "untracked.scratch"
    hidden_force.write_text("not committed")
    assert git("status", "--porcelain") == ""
    build("hidden-force", True, "not a regular tracked HEAD file")
    hidden_force.unlink()
    git("update-index", "--assume-unchanged", "pyproject.toml")
    config = source / "pyproject.toml"
    config.write_text(config.read_text() + "\n# hidden metadata edit\n")
    assert git("status", "--porcelain") == ""
    build("hidden-tracked-edit", True, "input bytes differ from HEAD")


def test_ordinary_packaging_configuration_excludes_a_source_record() -> None:
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "/src/agents_shipgate/_meta/release-source.json" in (
        data["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"]
    )
