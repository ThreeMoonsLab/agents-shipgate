"""Run the surviving mutations reported in #1155 against their regressions."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    (
        "ci/release_decision.py",
        "        if diff.base_comparison_requested and (\n",
        "        if False and (\n",
        "tests/test_release_decision.py::test_unavailable_binding_base_leaves_an_unbound_tool_gap",
    ),
    (
        "core/codex_boundary.py",
        "    if diff_file.is_deleted or weakened_action or weakened_risk:\n",
        "    if weakened_action or weakened_risk:\n",
        "tests/test_agent_boundary.py::test_deleted_codex_boundary_policy_emits_a_weakened_policy_finding",
    ),
    (
        "core/agent_boundary.py",
        '        or any(item.status == "partial" for item in coverage)\n',
        "        or False\n",
        "tests/test_agent_boundary.py::test_partial_host_coverage_holds_total_coverage_without_input_issues",
    ),
    (
        "core/agent_boundary.py",
        "        openshell_safe_paths=frozenset(openshell_safe if not input_issues else set()),\n",
        "        openshell_safe_paths=frozenset(openshell_safe),\n",
        "tests/test_agent_boundary.py::test_unread_boundary_input_invalidates_openshell_safe_paths",
    ),
    (
        "inputs/list_expressions.py",
        "                            directory.lstat()\n"
        "                        except FileNotFoundError:\n"
        "                            pass\n"
        "                        except OSError:\n"
        "                            return False\n",
        "                            directory.lstat()\n"
        "                        except FileNotFoundError:\n"
        "                            pass\n"
        "                        except OSError:\n"
        "                            pass\n",
        "tests/test_list_expressions.py::test_namespace_root_stat_failure_is_not_proof_of_absence",
    ),
    (
        "inputs/mcp_server_source.py",
        '    except OSError:\n        return "unreadable_file"\n',
        "    except OSError:\n        return None\n",
        "tests/test_mcp_server_source.py::test_source_stat_failure_reports_unreadable_file",
    ),
]


def run_case(root: Path, node_id: str) -> subprocess.CompletedProcess[str]:
    child_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("PYTEST_ADDOPTS", "SHIPGATE_TEST_SHARD", "SHIPGATE_TEST_SHARDS"):
        child_env.pop(key, None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node_id],
        cwd=root,
        env=child_env,
        text=True,
        capture_output=True,
        timeout=60,
    )


@pytest.mark.slow
@pytest.mark.parametrize(
    "path,original,replacement,node_id",
    MUTATIONS,
    ids=["missing-base", "deleted-policy", "host-coverage", "openshell-input", "namespace", "mcp-stat"],
)
def test_verdict_regression_rejects_its_mutation(tmp_path, path, original, replacement, node_id):
    # The root pytest setup deliberately loads its own source directory. Give
    # the nested run its own checkout-shaped inputs so it exercises the edit.
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache")
    shutil.copytree(ROOT / "src", tmp_path / "src", ignore=ignore)
    shutil.copytree(ROOT / "tests", tmp_path / "tests", ignore=ignore)
    shutil.copytree(ROOT / "docs" / "checks", tmp_path / "docs" / "checks")
    shutil.copytree(ROOT / "policies", tmp_path / "policies")
    for filename in ("conftest.py", "ci_sharding.py", "pyproject.toml", "docs/triggers.json"):
        shutil.copyfile(ROOT / filename, tmp_path / filename)
    target = tmp_path / "src" / "agents_shipgate" / path
    source = target.read_text(encoding="utf-8")
    assert source.count(original) == 1, f"Mutation anchor moved in {path}"

    baseline = run_case(tmp_path, node_id)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    target.write_text(source.replace(original, replacement, 1), encoding="utf-8")
    mutated = run_case(tmp_path, node_id)
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert f"FAILED {node_id}" in mutated.stdout, mutated.stdout + mutated.stderr
