"""#654: the freeze check refuses new surface, and says so usefully.

A guard that cannot fail is not a guard, so every case here drives the
script's real detection over a real Git history rather than asserting on
its source.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_surface_freeze.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature of the shapes the script reads."""

    workspace = tmp_path / "repo"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "src" / "agents_shipgate" / "inputs").mkdir(parents=True)
    (workspace / "scripts").mkdir()
    (workspace / "docs" / "checks.json").write_text(
        json.dumps({"checks": [{"id": "SHIP-EXISTING-ONE"}]}), encoding="utf-8"
    )
    (workspace / "docs" / "report-schema.v1.json").write_text("{}", encoding="utf-8")
    (workspace / "src" / "agents_shipgate" / "inputs" / "mcp.py").write_text(
        "", encoding="utf-8"
    )
    (workspace / "scripts" / "check_surface_freeze.py").write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "config", "user.name", "Test")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    _git(workspace, "checkout", "-q", "-b", "change")
    return workspace


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/check_surface_freeze.py", "--base", "main", *extra],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str = "change") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def test_an_ordinary_change_passes(repo: Path) -> None:
    (repo / "src" / "agents_shipgate" / "inputs" / "mcp.py").write_text(
        "# a real fix\n", encoding="utf-8"
    )
    _commit(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout
    assert "Freeze respected" in result.stdout


def test_a_new_check_id_is_refused(repo: Path) -> None:
    path = repo / "docs" / "checks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["checks"].append({"id": "SHIP-BRAND-NEW"})
    path.write_text(json.dumps(payload), encoding="utf-8")
    _commit(repo)

    result = _run(repo)

    assert result.returncode == 1
    assert "new check ID: SHIP-BRAND-NEW" in result.stdout
    assert "freeze-exception" in result.stdout


def test_a_new_schema_family_is_refused(repo: Path) -> None:
    (repo / "docs" / "brand-new-schema.v1.json").write_text("{}", encoding="utf-8")
    _commit(repo)

    result = _run(repo)

    assert result.returncode == 1
    assert "new schema family: brand-new-schema" in result.stdout


def test_a_new_version_of_an_existing_family_is_not_a_new_family(repo: Path) -> None:
    """Versioning something that exists is not new surface — refusing it
    would block the migrations the freeze is meant to leave alone."""

    (repo / "docs" / "report-schema.v2.json").write_text("{}", encoding="utf-8")
    _commit(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout


def test_a_new_adapter_is_refused(repo: Path) -> None:
    (repo / "src" / "agents_shipgate" / "inputs" / "brand_new.py").write_text(
        "", encoding="utf-8"
    )
    _commit(repo)

    result = _run(repo)

    assert result.returncode == 1
    assert "new input adapter: brand_new" in result.stdout


def test_a_private_module_is_not_an_adapter(repo: Path) -> None:
    (repo / "src" / "agents_shipgate" / "inputs" / "_helper.py").write_text(
        "", encoding="utf-8"
    )
    _commit(repo)

    assert _run(repo).returncode == 0


def test_the_label_is_the_way_through(repo: Path) -> None:
    path = repo / "docs" / "checks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["checks"].append({"id": "SHIP-DELIBERATE"})
    path.write_text(json.dumps(payload), encoding="utf-8")
    _commit(repo)

    result = _run(repo, "--labels", "P1,freeze-exception")

    assert result.returncode == 0
    assert "new check ID: SHIP-DELIBERATE" in result.stdout
    assert "label is present" in result.stdout


def test_an_unrelated_label_does_not_release_the_freeze(repo: Path) -> None:
    (repo / "src" / "agents_shipgate" / "inputs" / "brand_new.py").write_text(
        "", encoding="utf-8"
    )
    _commit(repo)

    assert _run(repo, "--labels", "P0,bug,enhancement").returncode == 1


def test_the_line_budget_reports_but_never_fails(repo: Path) -> None:
    (repo / "src" / "agents_shipgate" / "inputs" / "mcp.py").write_text(
        "\n".join(f"# line {n}" for n in range(1200)), encoding="utf-8"
    )
    _commit(repo)

    result = _run(repo, "--budget", "800")

    assert result.returncode == 0
    assert "review budget" in result.stdout
    assert "Not a failure" in result.stdout


def test_generated_files_do_not_count_against_the_budget(repo: Path) -> None:
    (repo / "llms-full.txt").write_text(
        "\n".join(f"line {n}" for n in range(2000)), encoding="utf-8"
    )
    _commit(repo)

    result = _run(repo, "--budget", "800")

    assert result.returncode == 0
    assert "review budget" not in result.stdout
    assert "2001 generated" in result.stdout or "generated" in result.stdout
