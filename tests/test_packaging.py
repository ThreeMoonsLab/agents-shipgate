from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_wheel_includes_adoption_kits(tmp_path: Path) -> None:
    pytest.importorskip("build", reason="python-build not installed")
    out_dir = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    [wheel] = out_dir.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "agents_shipgate/_adoption_kits/codex-skill/SKILL.md" in names
    assert "agents_shipgate/_adoption_kits/claude-code-skill/SKILL.md" in names
    assert (
        "agents_shipgate/_adoption_kits/codex-skill/.agents-shipgate-kit-metadata.json"
        in names
    )
