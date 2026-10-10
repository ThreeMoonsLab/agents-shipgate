"""Run the shipped Action step on sample applications in the existing CI suite."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.github_action_application import fail_policy, markdown, output_directory
from tests.test_action_comment_fallback import HARNESS, NODE
from tests.test_application_diff import SDK, commit, git

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / "action.yml").read_text())
STEPS = {step["name"]: step for step in ACTION["runs"]["steps"]}
pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="Action uses bash")


@pytest.fixture
def application(tmp_path):
    workspace = tmp_path / "app"
    workspace.mkdir()
    git(workspace, "init", "-q", "-b", "main")
    base = commit(workspace, {"agent.py": SDK.replace("TOOLS", "[lookup]")})
    head = commit(workspace, {"agent.py": SDK.replace("TOOLS", "[lookup, execute]")})
    return workspace, base, head


def run_step(tmp_path, workspace, base, head, **overrides):
    step = STEPS["Run Agents Shipgate"]
    values = {}
    for key, expression in step["env"].items():
        match = re.fullmatch(r"\$\{\{ inputs\.(\w+) \}\}", expression)
        values[key] = str(ACTION["inputs"][match[1]]["default"]) if match else ""
    output = tmp_path / "github-output"
    output.write_text("")
    summary = tmp_path / "github-summary"
    summary.write_text("")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    python = bin_dir / "python"
    python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o755)
    env = {
        **os.environ, **values, "APPLICATION": "true",
        "APPLICATION_BASE_SHA": base, "APPLICATION_HEAD_SHA": head,
        "GITHUB_ACTION_PATH": str(ROOT), "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "PYTHONPATH": str(ROOT / "src"),
        **overrides,
    }
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                            cwd=workspace, env=env, capture_output=True, text=True, check=True)
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    return outputs, summary.read_text(), result.stdout


def test_action_uses_exact_pr_head_and_merge_base(tmp_path, application):
    workspace, base, head = application
    git(workspace, "checkout", "-q", "-B", "target", base)
    target = commit(workspace, {"README.md": "A later target branch commit.\n"})
    outputs, summary, _ = run_step(tmp_path, workspace, target, head)
    assert outputs["exit_code"] == "0" and outputs["application_status"] == "compared"
    payload = json.loads(Path(outputs["application_json"]).read_text())
    assert payload["base"]["requested_commit"] == target
    assert payload["base"]["compared_commit"] == base
    assert payload["head"]["compared_commit"] == head
    assert payload["scope_selection"]["mode"] == "derived"
    assert [(row["tool"], row["change"]) for row in payload["rows"]] == [("execute", "added")]
    assert "ADDED" in summary and "execute" in summary
    assert not {"control", "decision", "merge_verdict", "release_decision"} & payload.keys()
    comment = Path(outputs["application_reports"], "pr-comment.md").read_text()
    assert comment.startswith("<!-- agents-shipgate-application-comment -->")
    assert "ADDED" in comment


def test_action_no_change_and_explicit_scope(tmp_path, application):
    workspace, base, _ = application
    head = commit(workspace, {"agent.py": SDK.replace("TOOLS", "[lookup]").replace("return code", "return code.upper()")})
    outputs, summary, _ = run_step(tmp_path, workspace, base, head, APPLICATION_SCOPE=".")
    payload = json.loads(Path(outputs["application_json"]).read_text())
    assert outputs["exit_code"] == "0" and payload["rows"] == []
    assert payload["comparison_status"] == "compared"
    assert "No established binding/interface/implementation changes" in summary


def test_refusal_does_not_reuse_a_successful_run(tmp_path, application):
    workspace, base, head = application
    first, _, _ = run_step(tmp_path, workspace, base, head)
    second, summary, _ = run_step(tmp_path, workspace, base, "f" * 40)
    assert second["exit_code"] == "2" and second["application_status"] == "refused"
    assert second["application_json"] == ""
    assert first["application_reports"] != second["application_reports"]
    assert not Path(second["application_reports"], "application-comparison.json").exists()
    assert "fetch-depth: 0" in summary and "No application comparison" in summary


def test_shallow_checkout_refuses_with_fix(tmp_path, application):
    workspace, base, head = application
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth=1", workspace.as_uri(), str(shallow))
    outputs, summary, _ = run_step(tmp_path, shallow, base, head)
    assert outputs["exit_code"] == "2" and outputs["application_status"] == "refused"
    assert "fetch-depth: 0" in summary and "full objects" in summary


def test_unobserved_binding_stays_partial_and_advisory(tmp_path, application):
    workspace, base, _ = application
    head = commit(workspace, {"agent.py": SDK.replace("TOOLS", "load_tools()")})
    outputs, summary, _ = run_step(tmp_path, workspace, base, head)
    assert outputs["application_status"] == "partial" and outputs["exit_code"] == "0"
    assert "partial" in summary and "not established" in summary.lower()
    opted_in, _, _ = run_step(tmp_path, workspace, base, head, APPLICATION_FAIL_ON="partial")
    assert opted_in["exit_code"] == "20"


def test_pr_workspace_cannot_replace_action_python_imports(tmp_path, application):
    workspace, base, head = application
    fake = workspace / "agents_shipgate"
    fake.mkdir()
    (fake / "__init__.py").write_text("raise AssertionError('PR code imported')\n")
    (fake / "__main__.py").write_text("raise AssertionError('PR code executed')\n")
    outputs, _, _ = run_step(tmp_path, workspace, base, head)
    assert outputs["exit_code"] == "0"


@pytest.mark.parametrize("override", [{"ATTESTATION": "true"}, {"CI_MODE": "strict"}, {"APPLICATION_FAIL_ON": "blocked"}, {"APPLICATION_HEAD_SHA": ""}])
def test_incompatible_or_missing_inputs_do_not_publish_verifier_outputs(tmp_path, application, override):
    workspace, base, head = application
    outputs, _, _ = run_step(tmp_path, workspace, base, head, **override)
    assert outputs["exit_code"] == "2" and outputs["application_json"] == ""
    assert not {"decision", "merge_verdict", "agent_control_state"} & outputs.keys()


@pytest.mark.skipif(NODE is None, reason="Node required for actual comment step")
@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.parametrize("prior", [False, True])
def test_application_comment_publication_or_fork_summary(tmp_path, application, denied, prior):
    workspace, base, head = application
    outputs, _, _ = run_step(tmp_path, workspace, base, head)
    result = subprocess.run(
        [NODE, "-e", HARNESS], text=True, capture_output=True, check=True,
        input=json.dumps({
            "script": STEPS["Comment on pull request"]["with"]["script"], "status": 403 if denied else 0,
            "comments": [{"id": 41, "body": "<!-- agents-shipgate-pr-comment --> old"}]
            + ([{"id": 42, "body": "<!-- agents-shipgate-application-comment --> old"}] if prior else []),
        }),
        env={**os.environ, "OUTPUT_DIR": outputs["application_reports"], "APPLICATION": "true", "GITHUB_RUN_ID": "123"},
    )
    actual = json.loads(result.stdout)
    assert actual["error"] is None
    assert "execute" in actual["summary"] if denied else "execute" in actual["calls"][0]["value"]["body"]
    if denied:
        assert "No merge verdict is implied" in actual["summary"]
    else:
        assert actual["calls"][0]["kind"] == ("update" if prior else "create")
        if prior:
            assert actual["calls"][0]["value"]["comment_id"] == 42


def test_application_output_rejects_symlinks_and_protected_paths(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    link = tmp_path / "link"
    link.symlink_to(other, target_is_directory=True)
    for path in ["link/reports", ".git/reports", ".github/workflows/reports", "../reports", "."]:
        with pytest.raises(ValueError):
            output_directory(tmp_path, path)


def test_application_policy_and_comment_limit():
    assert fail_policy("partial", "") == 0
    assert fail_policy("partial", "partial,not_established") == 20
    body = markdown("```\n" * 6000, short=True)
    assert len(body) < 5500 and "Detail was shortened" in body
    assert "\n```\n" not in body


@pytest.mark.parametrize("value", ["FALSE", "TRUE", "invalid"])
def test_invalid_application_input_cannot_route_to_stale_verifier_publication(tmp_path, application, value):
    workspace, base, head = application
    old = workspace / "agents-shipgate-reports"
    old.mkdir()
    (old / "pr-comment.md").write_text("<!-- agents-shipgate-pr-comment --> old verdict")
    outputs, _, _ = run_step(tmp_path, workspace, base, head, APPLICATION=value)
    assert outputs == {"exit_code": "2"}
    # GitHub compares strings without case sensitivity; consumers must select
    # the exact route emitted by the shipped Bash, never the rejected input.
    for name in ["Extract Agents Shipgate outputs", "Upload Agents Shipgate report", "Comment on pull request"]:
        condition = STEPS[name]["if"]
        assert "inputs.application" not in condition
        assert "steps.scan.outputs.execution_mode" in condition
    assert (old / "pr-comment.md").read_text().endswith("old verdict")
