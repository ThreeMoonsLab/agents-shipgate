"""#780: the host-only advisory PR recipe, run through the Action's own steps.

`examples/github-actions/14-host-only-advisory-pr.yml` is copied into someone
else's repository, so two things are held here. Its shape: `pull_request` only,
one run per PR, the minimum permissions, full history, the pinned release, and
no manifest, failure policy or check. And its behaviour: the recipe's inputs,
merged with the defaults `action.yml` declares, are fed to the Action's real
"Run Agents Shipgate", "Extract Agents Shipgate outputs" and "Emit Agents
Shipgate annotations" bash steps — not a reconstruction of their arguments — in
clones with no `shipgate.yaml`, with this tree's CLI standing in for the pinned
install. The rows are checked against a local `agents-shipgate diff --json` on
the same refs.

Git runs without the developer's global or system config and hooks, and the CLI
without colour or terminal-width input, so the answers do not depend on the
machine running them.

This is not the GitHub-hosted observation #780 and #570 still need; it keeps the
recipe from drifting away from what the composite steps do between those runs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from agents_shipgate.published_release import LATEST_PUBLISHED_VERSION, latest_published_action_ref

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE = REPO_ROOT / "examples/github-actions/14-host-only-advisory-pr.yml"
ACTION = REPO_ROOT / "action.yml"

_BASE_SETTINGS = '{"permissions": {"allow": ["Bash(npm test:*)"], "deny": ["Bash(rm -rf:*)"]}}\n'
_HEAD_SETTINGS = '{"permissions": {"allow": ["Bash(npm *)"], "deny": []}}\n'
_NARROW_SETTINGS = '{"permissions": {"allow": [], "deny": ["Bash(rm -rf:*)", "Bash(curl:*)"]}}\n'
_BASE_MCP = '{"mcpServers": {"docs": {"command": "npx", "args": ["-y", "@example/docs-mcp"]}}}\n'
_HEAD_MCP = (
    '{"mcpServers": {"docs": {"command": "npx", "args": ["-y", "@example/docs-mcp"]}, '
    '"billing": {"command": "npx", "args": ["-y", "@example/billing-mcp"]}}}\n'
)

#: PR branch -> (files it writes, the base branch it targets). `limited` is a
#: second base whose Cursor configuration cannot be parsed and is never touched.
_BRANCHES: dict[str, tuple[dict[str, str], str]] = {
    "change": ({".claude/settings.json": _HEAD_SETTINGS, ".mcp.json": _HEAD_MCP}, "main"),
    "docs-only": ({"README.md": "# demo\nmore\n"}, "main"),
    "narrow": ({".claude/settings.json": _NARROW_SETTINGS, ".mcp.json": '{"mcpServers": {}}\n'}, "main"),
    "broken": ({".mcp.json": '{"mcpServers": {"docs": '}, "main"),
    "invalid-manifest": ({"shipgate.yaml": "version: [unclosed\n"}, "main"),
    "limited": ({".cursor/mcp.json": '{"mcpServers": '}, "main"),
    "limited-docs": ({"README.md": "# demo\nlimited\n"}, "limited"),
}

_BASH_ONLY = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                                reason="The composite steps are bash scripts.")


def _recipe() -> dict:
    return yaml.safe_load(RECIPE.read_text(encoding="utf-8"))


def _action() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def _named_step(name: str) -> dict:
    return next(step for step in _action()["runs"]["steps"] if step.get("name") == name)


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
    # One run per PR: two quick pushes must not race the comment upsert or let
    # the older result overwrite the newer one.
    assert recipe["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number }}",
        "cancel-in-progress": True,
    }

    job = recipe["jobs"]["shipgate"]
    assert "permissions" not in job and "if" not in job and "concurrency" not in job
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
    """With `shipgate_version` set, the Action installs that release by name.

    `-P` keeps the PR's checkout off sys.path while pip runs; the behaviour is
    executed in `tests/test_action_engine_install.py`.
    """
    install = _named_step("Install Agents Shipgate")
    assert 'python -P -m pip install "agents-shipgate==${SHIPGATE_VERSION}"' in install["run"]


def test_the_comment_is_updated_in_place_and_falls_back_to_the_summary() -> None:
    comment = _named_step("Comment on pull request")
    script = comment["with"]["script"]
    assert "github.event_name == 'pull_request'" in comment["if"]
    assert "agents-shipgate-pr-comment" in script and "updateComment" in script
    assert "[403, 404].includes(error.status)" in script and "core.summary" in script


def _clean_env() -> dict[str, str]:
    """The ambient environment without anything that changes git or CLI output."""

    dropped = {"FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE", "NO_COLOR", "COLUMNS", "LINES", "TERM"}
    env = {
        key: value for key, value in os.environ.items()
        if key not in dropped
        and not key.startswith(("GIT_", "GITHUB_", "RUNNER_", "PYTHON", "AGENTS_SHIPGATE_"))
    }
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "NO_COLOR": "1",
        "COLUMNS": "120",
        "TERM": "dumb",
    })
    return env


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "user.name=Recipe",
         "-c", "user.email=recipe@example.invalid", "-c", "commit.gpgsign=false",
         "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True, text=True, env=_clean_env(),
    ).stdout


@pytest.fixture
def pull_request_remote(tmp_path: Path) -> Path:
    """A manifest-free remote carrying the documented PR branches."""
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    (seed / ".claude").mkdir()
    (seed / ".claude/settings.json").write_text(_BASE_SETTINGS, encoding="utf-8")
    (seed / ".mcp.json").write_text(_BASE_MCP, encoding="utf-8")
    (seed / "README.md").write_text("# demo\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    for name, (files, base) in _BRANCHES.items():
        _git(seed, "checkout", "-q", "-B", name, f"origin/{base}")
        for relative, text in files.items():
            (seed / relative).parent.mkdir(parents=True, exist_ok=True)
            (seed / relative).write_text(text, encoding="utf-8")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-qm", name)
        _git(seed, "push", "-q", "origin", name)
    return remote


@pytest.fixture
def pull_request_clone(pull_request_remote: Path, tmp_path: Path) -> Path:
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(pull_request_remote), str(clone))
    return clone


@dataclass
class ActionRun:
    exit_code: str
    verifier: dict | None
    comment: str | None
    outputs: str
    step_env: dict[str, str]


def _bin_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, command in (("agents-shipgate", "-P -m agents_shipgate"), ("python", "")):
        shim = bin_dir / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" {command} "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return bin_dir


def _inputs() -> dict[str, str]:
    inputs = {name: str(spec.get("default", "")) for name, spec in _action()["inputs"].items()}
    inputs.update({name: str(value) for name, value in _action_step(_recipe())["with"].items()})
    return inputs


def _run_step(name: str, clone: Path, tmp_path: Path, github: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Execute one of action.yml's bash steps with the recipe's inputs."""
    inputs = _inputs()
    step = _named_step(name)

    def expand(value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            scope, key = match.group(1), match.group(2)
            return inputs[key] if scope == "inputs" else github[key]
        return re.sub(r"\$\{\{\s*(inputs|github)\.(\w+)\s*\}\}", replace, value)

    env = _clean_env()
    env.update({key: expand(str(value)) for key, value in step.get("env", {}).items()})
    env.update({
        "PATH": f"{_bin_dir(tmp_path)}{os.pathsep}{env.get('PATH', '')}",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "GITHUB_ACTION_PATH": str(REPO_ROOT),
        "GITHUB_WORKSPACE": str(clone),
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step-summary"),
    })
    for handle in ("github-output", "step-summary"):
        (tmp_path / handle).touch()
    return subprocess.run(["bash", "-c", expand(step["run"])], cwd=clone, env=env,
                          capture_output=True, text=True, timeout=300)


def _run_action(clone: Path, tmp_path: Path, *, base_ref: str = "main") -> ActionRun:
    """The recipe's run step, then the outputs and annotations steps it reaches."""
    head = _git(clone, "rev-parse", "HEAD").strip()
    github = {"base_ref": base_ref, "event_name": "pull_request", "sha": head}
    (tmp_path / "github-output").write_text("", encoding="utf-8")
    result = _run_step("Run Agents Shipgate", clone, tmp_path, github)
    assert result.returncode == 0, result.stdout + result.stderr
    exit_code = re.findall(r"^exit_code=(\d+)$", (tmp_path / "github-output").read_text(), re.M)
    assert len(exit_code) == 1, (tmp_path / "github-output").read_text()

    # Both run under `always()`; annotations also need the default input on.
    assert _named_step("Extract Agents Shipgate outputs")["if"] == "${{ always() }}"
    assert _inputs()["check_annotations"] == "true"
    for name in ("Extract Agents Shipgate outputs", "Emit Agents Shipgate annotations"):
        step = _run_step(name, clone, tmp_path, github)
        assert step.returncode == 0, f"{name}:\n{step.stdout}{step.stderr}"

    reports = clone / _inputs()["output_dir"]
    verifier_path, comment_path = reports / "verifier.json", reports / "pr-comment.md"
    return ActionRun(
        exit_code=exit_code[0],
        verifier=json.loads(verifier_path.read_text(encoding="utf-8")) if verifier_path.is_file() else None,
        comment=comment_path.read_text(encoding="utf-8") if comment_path.is_file() else None,
        outputs=(tmp_path / "github-output").read_text(encoding="utf-8"),
        step_env=github,
    )


def _summary(comment: str) -> str:
    return comment.split("### Human summary", 1)[1].split("### Agent instruction block", 1)[0]


def _local_diff(clone: Path, tmp_path: Path, base_ref: str) -> dict:
    """What a developer's `agents-shipgate diff --json` says about the same refs."""
    env = _clean_env()
    env.update({"PYTHONPATH": str(REPO_ROOT / "src")})
    result = subprocess.run(
        [str(_bin_dir(tmp_path) / "agents-shipgate"), "diff", "--workspace", str(clone),
         "--base", f"origin/{base_ref}", "--json"],
        cwd=clone, env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@_BASH_ONLY
@pytest.mark.parametrize("branch", ["change", "narrow", "docs-only", "limited-docs", "broken"])
def test_the_action_steps_give_distinct_advisory_answers(
    pull_request_clone: Path, tmp_path: Path, branch: str,
) -> None:
    base_ref = _BRANCHES[branch][1]
    _git(pull_request_clone, "checkout", "-q", branch)
    run = _run_action(pull_request_clone, tmp_path, base_ref=base_ref)
    assert run.exit_code == "0"
    assert run.verifier is not None and run.comment is not None

    comparison = run.verifier["host_comparison"]
    rows = comparison["rows"]
    summary = _summary(run.comment)
    assert "Advisory: no application release policy configured. This comparison grants no merge authority." in summary
    assert run.verifier["can_merge_without_human"] is False
    if branch == "change":
        assert comparison["comparison_status"] == "comparable"
        assert len(rows) == 4
        assert "Repository-declared host capability changes:" in summary
        assert "Bash(npm *)" in summary and "billing" in summary
    elif branch == "narrow":
        # A narrowing must not read as a widening: no row is `widened` or
        # `expands`, and at least one removes a grant.
        assert comparison["comparison_status"] == "comparable"
        directions = sorted(row["direction"] for row in rows)
        assert directions and "widened" not in directions and "removed" in directions, rows
        assert not any(row["expands"] for row in rows), rows
        assert "Bash(curl:*)" in summary
        assert "No static host-grant changes detected" not in summary
    elif branch == "docs-only":
        assert comparison["comparison_status"] == "comparable"
        assert rows == [] and comparison["unchanged_limits"] == []
        assert (
            "Repository-declared host capability changes:\n"
            "No static host-grant changes detected in the covered comparison. No verdict is implied.\n"
        ) in summary
        assert "Not compared:" not in summary
    elif branch == "limited-docs":
        assert comparison["comparison_status"] == "comparable" and rows == []
        assert [(limit["source"], limit["limit"]) for limit in comparison["unchanged_limits"]] == [
            (".cursor/mcp.json", "parse_failed")
        ]
        assert "No static host-grant changes detected in the covered comparison." in summary
        assert "Not compared: unchanged in this change and not read, so no claim is made about them:" in summary
    else:
        assert comparison["comparison_status"] == "incomparable" and rows == []
        assert comparison["incomparable_reasons"] == ["head_inventory_incomplete"]
        assert "Host capability comparison unavailable:" in summary
        assert "No static host-grant changes detected" not in summary

    # The PR answer is the local answer.
    local = _local_diff(pull_request_clone, tmp_path, base_ref)
    for key in ("comparison_status", "incomparable_reasons", "rows", "unchanged_limits"):
        assert comparison[key] == local[key], key

    # Nothing but the report directory: no manifest, baseline or workflow.
    status = _git(pull_request_clone, "status", "--porcelain", "--untracked-files=all")
    assert all(line[3:].startswith("agents-shipgate-reports/") for line in status.splitlines()), status
    assert not (pull_request_clone / "shipgate.yaml").exists()


@_BASH_ONLY
@pytest.mark.parametrize("history", ["shallow", "base-branch-not-fetched"])
def test_missing_history_is_a_visible_unavailable_comparison_not_quiet_success(
    pull_request_remote: Path, tmp_path: Path, history: str,
) -> None:
    """Without the base in the clone, the comment says so; the job does not fail."""
    clone = tmp_path / "ci-clone"
    if history == "shallow":
        # `--depth` needs a URL: a plain local path clone ignores it.
        _git(tmp_path, "clone", "-q", "--depth", "1", "--no-single-branch", "--branch", "change",
             pull_request_remote.as_uri(), str(clone))
    else:
        _git(tmp_path, "clone", "-q", "--single-branch", "--branch", "change",
             str(pull_request_remote), str(clone))
    run = _run_action(clone, tmp_path)
    assert run.exit_code == "0"
    assert run.verifier is not None and run.comment is not None

    comparison = run.verifier["host_comparison"]
    assert comparison["comparison_status"] == "incomparable", comparison
    assert comparison["rows"] == [] and comparison["incomparable_reasons"]
    summary = _summary(run.comment)
    assert "Host capability comparison unavailable:" in summary
    assert "No static host-grant changes detected" not in summary
    assert "Repository-declared host capability changes:" not in summary
    assert run.verifier["can_merge_without_human"] is False


@_BASH_ONLY
def test_an_invalid_manifest_added_by_the_pr_fails_the_job(
    pull_request_clone: Path, tmp_path: Path,
) -> None:
    """The documented way a PR's own contents fail this advisory job."""
    _git(pull_request_clone, "checkout", "-q", "invalid-manifest")
    run = _run_action(pull_request_clone, tmp_path)
    assert run.exit_code == "2"
    apply = _named_step("Apply Agents Shipgate exit code")
    assert "steps.scan.outputs.exit_code != '0'" in apply["if"]
    assert apply["run"] == 'exit "${EXIT_CODE}"'


def test_the_entry_pages_hand_off_to_the_recipe() -> None:
    """#780: after local `diff` value, the README and quickstart name the recipe."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (REPO_ROOT / "docs/quickstart.md").read_text(encoding="utf-8")
    first_task = readme.split("## What did this PR change?", 1)[1].split("\n## ", 1)[0]
    assert "(examples/github-actions/14-host-only-advisory-pr.yml)" in first_task
    review = quickstart.split("## Review a host-configuration change", 1)[1].split("\n## ", 1)[0]
    next_steps = review.split("### Next", 1)[1]
    assert "(../examples/github-actions/14-host-only-advisory-pr.yml)" in next_steps
