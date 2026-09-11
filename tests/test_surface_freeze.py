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


@pytest.mark.parametrize(("before", "after"), [("0.43", "1.0"), ("1.0", "1.1"), ("1.0.0", "1.0.1")])
def test_dotted_schema_versions_remain_one_family(repo: Path, before: str, after: str) -> None:
    (repo / "docs" / f"report-schema.v{before}.json").write_text("{}")
    _commit(repo)
    _git(repo, "branch", "-f", "main", "HEAD")
    (repo / "docs" / f"report-schema.v{after}.json").write_text("{}")
    _commit(repo)
    result = _run(repo)
    assert result.returncode == 0, result.stdout


def test_package_adapter_with_private_loader_is_refused(repo: Path) -> None:
    package = repo / "src/agents_shipgate/inputs/new_framework"
    package.mkdir()
    (package / "__init__.py").write_text("from ._adapter import NewAdapter\n")
    (package / "_adapter.py").write_text("class NewAdapter: pass\n")
    _commit(repo)
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "new input adapter: new_framework" in result.stdout


def test_helpers_within_existing_adapter_do_not_create_adapter_surface(repo: Path) -> None:
    package = repo / "src/agents_shipgate/inputs/existing"
    package.mkdir()
    (package / "__init__.py").write_text("")
    _commit(repo)
    _git(repo, "branch", "-f", "main", "HEAD")
    (package / "helpers.py").write_text("# existing adapter implementation\n")
    _commit(repo)
    result = _run(repo)
    assert result.returncode == 0, result.stdout


def test_shallow_checkout_needs_trees_not_merge_base(repo: Path, tmp_path: Path) -> None:
    (repo / "src/agents_shipgate/inputs/mcp.py").write_text("# ordinary fix\n")
    _commit(repo)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--depth", "1", "--branch", "change", repo.as_uri(), str(shallow)], check=True, capture_output=True)
    _git(shallow, "fetch", "origin", "main:refs/heads/main")
    merge_base = subprocess.run(["git", "merge-base", "main", "HEAD"], cwd=shallow, capture_output=True)
    assert merge_base.returncode != 0
    result = _run(shallow)
    assert result.returncode == 0, result.stderr
    assert "1 reviewable" in result.stdout


@pytest.mark.parametrize("labels", [["other,freeze-exception"], ['"; echo freeze-exception'], ["$(echo freeze-exception)"], [" freeze-exception "]])
def test_json_event_labels_are_compared_exactly(repo: Path, labels: list[str]) -> None:
    (repo / "src/agents_shipgate/inputs/new_adapter.py").write_text("")
    _commit(repo)
    result = _run(repo, "--labels-json", json.dumps(labels))
    assert result.returncode == 1, result.stdout
    assert "Allowed:" not in result.stdout


def test_exact_json_event_exception_is_allowed(repo: Path) -> None:
    (repo / "src/agents_shipgate/inputs/new_adapter.py").write_text("")
    _commit(repo)
    result = _run(repo, "--labels-json", json.dumps(["P1", "freeze-exception"]))
    assert result.returncode == 0, result.stderr
    assert "label is present" in result.stdout


@pytest.mark.parametrize("raw", ["invalid", "null", "{}", '["freeze-exception", 1]', '"freeze-exception"'])
def test_invalid_event_label_json_fails_closed(repo: Path, raw: str) -> None:
    result = _run(repo, "--labels-json", raw)
    assert result.returncode == 2
    assert "JSON array of strings" in result.stderr


def test_label_inputs_cannot_be_mixed(repo: Path) -> None:
    result = _run(repo, "--labels", "freeze-exception", "--labels-json", "[]")
    assert result.returncode == 2
