"""#780: the host-only advisory PR recipe, run through the Action's own step.

`examples/github-actions/14-host-only-advisory-pr.yml` is copied into someone
else's repository, so two things are held here. Its shape: `pull_request` only,
the minimum permissions, full history, the pinned release, and no manifest,
failure policy or check. And its behaviour: the recipe's inputs, merged with the
defaults `action.yml` declares, are fed to the Action's real "Run Agents
Shipgate" bash step — not a reconstruction of its arguments — in a clone with no
`shipgate.yaml`, with this tree's CLI standing in for the pinned install.

This is not the GitHub-hosted observation #780 and #570 still need; it keeps the
recipe from drifting away from what the composite step does between those runs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agents_shipgate.published_release import LATEST_PUBLISHED_VERSION, latest_published_action_ref

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE = REPO_ROOT / "examples/github-actions/14-host-only-advisory-pr.yml"
ACTION = REPO_ROOT / "action.yml"

_BASE_SETTINGS = '{"permissions": {"allow": ["Bash(npm test:*)"], "deny": ["Bash(rm -rf:*)"]}}\n'
_HEAD_SETTINGS = '{"permissions": {"allow": ["Bash(npm *)"], "deny": []}}\n'
_BASE_MCP = '{"mcpServers": {"docs": {"command": "npx", "args": ["-y", "@example/docs-mcp"]}}}\n'
_HEAD_MCP = (
    '{"mcpServers": {"docs": {"command": "npx", "args": ["-y", "@example/docs-mcp"]}, '
    '"billing": {"command": "npx", "args": ["-y", "@example/billing-mcp"]}}}\n'
)


def _recipe() -> dict:
    return yaml.safe_load(RECIPE.read_text(encoding="utf-8"))


def _action_step(recipe: dict) -> dict:
    steps = [
        step for step in recipe["jobs"]["shipgate"]["steps"]
        if str(step.get("uses", "")).startswith("ThreeMoonsLab/agents-shipgate@")
    ]
    assert len(steps) == 1
    return steps[0]


def test_the_recipe_is_advisory_minimal_and_pinned() -> None:
    recipe = _recipe()
    # PyYAML reads the bare `on:` key as the boolean True.
    triggers = recipe.get("on", recipe.get(True))
    assert set(triggers) == {"pull_request"}, "pull_request_target would run on untrusted PR code"
    assert recipe["permissions"] == {"contents": "read", "pull-requests": "write"}

    job = recipe["jobs"]["shipgate"]
    assert "permissions" not in job and "if" not in job
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False

    step = _action_step(recipe)
    assert step["uses"] == f"ThreeMoonsLab/agents-shipgate@{latest_published_action_ref()}"
    inputs = step["with"]
    assert inputs["shipgate_version"] == LATEST_PUBLISHED_VERSION
    assert inputs["ci_mode"] == "advisory"
    assert inputs["diff_base"] == "target"
    assert inputs["pr_comment"] == "true"
    for absent in ("config", "fail_on", "fail_on_merge_verdicts", "baseline", "check_run",
                   "policy_packs", "base_ref", "head_ref", "shipgate_wheel"):
        assert absent not in inputs, f"the host-only recipe must not set {absent}"
    assert not (REPO_ROOT / "examples/github-actions/shipgate.yaml").exists()


def test_the_pinned_version_installs_from_the_index_not_the_pr() -> None:
    """With `shipgate_version` set, the Action installs that release by name."""
    install = next(
        step for step in yaml.safe_load(ACTION.read_text())["runs"]["steps"]
        if step.get("name") == "Install Agents Shipgate"
    )
    assert 'pip install "agents-shipgate==${SHIPGATE_VERSION}"' in install["run"]


def test_the_comment_is_updated_in_place_and_falls_back_to_the_summary() -> None:
    comment = next(
        step for step in yaml.safe_load(ACTION.read_text())["runs"]["steps"]
        if step.get("name") == "Comment on pull request"
    )
    script = comment["with"]["script"]
    assert "github.event_name == 'pull_request'" in comment["if"]
    assert "agents-shipgate-pr-comment" in script and "updateComment" in script
    assert "[403, 404].includes(error.status)" in script and "core.summary" in script


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Recipe", "-c", "user.email=recipe@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.fixture
def pull_request_clone(tmp_path: Path):
    """A clone of a manifest-free remote with the documented PR branches."""
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    (seed / ".claude").mkdir()
    (seed / ".claude/settings.json").write_text(_BASE_SETTINGS)
    (seed / ".mcp.json").write_text(_BASE_MCP)
    (seed / "README.md").write_text("# demo\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    for name, files in {
        "change": {".claude/settings.json": _HEAD_SETTINGS, ".mcp.json": _HEAD_MCP},
        "docs-only": {"README.md": "# demo\nmore\n"},
        "broken": {".mcp.json": '{"mcpServers": {"docs": '},
    }.items():
        _git(seed, "checkout", "-q", "-B", name, "origin/main")
        for relative, text in files.items():
            (seed / relative).write_text(text)
        _git(seed, "commit", "-qam", name)
        _git(seed, "push", "-q", "origin", name)
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(remote), str(clone))
    return clone


def _run_action_step(clone: Path, tmp_path: Path) -> tuple[str, dict, str]:
    """Execute action.yml's "Run Agents Shipgate" step with the recipe's inputs."""
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    inputs = {name: str(spec.get("default", "")) for name, spec in action["inputs"].items()}
    inputs.update({name: str(value) for name, value in _action_step(_recipe())["with"].items()})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, check=True,
                          capture_output=True, text=True).stdout.strip()
    github = {"base_ref": "main", "event_name": "pull_request", "sha": head}
    step = next(s for s in action["runs"]["steps"] if s.get("name") == "Run Agents Shipgate")

    def expand(value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            scope, name = match.group(1), match.group(2)
            return inputs[name] if scope == "inputs" else github[name]
        return re.sub(r"\$\{\{\s*(inputs|github)\.(\w+)\s*\}\}", replace, value)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    cli = bin_dir / "agents-shipgate"
    cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m agents_shipgate "$@"\n')
    cli.chmod(0o755)
    output_file = tmp_path / "github-output"
    output_file.write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENTS_SHIPGATE_"))}
    env.update({name: expand(str(value)) for name, value in step["env"].items()})
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "GITHUB_OUTPUT": str(output_file),
    })
    result = subprocess.run(["bash", "-c", step["run"]], cwd=clone, env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = clone / inputs["output_dir"]
    verifier = json.loads((reports / "verifier.json").read_text(encoding="utf-8"))
    return output_file.read_text(), verifier, (reports / "pr-comment.md").read_text(encoding="utf-8")


def _summary(comment: str) -> str:
    return comment.split("### Human summary", 1)[1].split("### Agent instruction block", 1)[0]


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                    reason="The composite step is a bash script.")
@pytest.mark.parametrize("branch", ["change", "docs-only", "broken"])
def test_the_action_step_gives_three_distinct_advisory_answers(
    pull_request_clone: Path, tmp_path: Path, branch: str,
) -> None:
    _git(pull_request_clone, "checkout", "-q", branch)
    outputs, verifier, comment = _run_action_step(pull_request_clone, tmp_path)
    assert "exit_code=0" in outputs

    comparison = verifier["host_comparison"]
    summary = _summary(comment)
    assert "This comparison grants no merge authority." in summary
    assert verifier["can_merge_without_human"] is False
    if branch == "change":
        assert comparison["comparison_status"] == "comparable"
        assert len(comparison["rows"]) == 4
        assert "Repository-declared host capability changes:" in summary
        assert "Bash(npm *)" in summary and "billing" in summary
    elif branch == "docs-only":
        assert comparison["comparison_status"] == "comparable" and comparison["rows"] == []
        assert "No static host-grant changes detected in the covered comparison." in summary
    else:
        assert comparison["comparison_status"] == "incomparable"
        assert comparison["incomparable_reasons"] == ["head_inventory_incomplete"]
        assert "Host capability comparison unavailable:" in summary
        assert "No static host-grant changes detected" not in summary

    # Nothing but the report directory: no manifest, baseline or workflow.
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                            cwd=pull_request_clone, check=True, capture_output=True, text=True)
    assert all(line[3:].startswith("agents-shipgate-reports/")
               for line in status.stdout.splitlines()), status.stdout
    assert not (pull_request_clone / "shipgate.yaml").exists()
