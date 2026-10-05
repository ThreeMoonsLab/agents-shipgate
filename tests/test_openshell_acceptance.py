"""Real CLI acceptance for the supported local OpenShell document review."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path

import pytest
import yaml
from test_openshell_inputs import POLICY, REGISTRATION, selection
from test_openshell_inventory import REJECTED_POLICIES
from test_openshell_routes import configured_source, invoke
from test_partial_host_comparison import _git, _repository
from typer.testing import CliRunner

from agents_shipgate.cli.main import app


@pytest.mark.parametrize("direction", ["equivalent", "narrowed", "widened"])
def test_audit_diff_check_and_configured_verifier_agree(tmp_path, direction):
    audit = POLICY.replace("enforcement: enforce", "enforcement: audit")
    before = audit if direction == "narrowed" else POLICY
    after = audit if direction == "widened" else POLICY
    if direction == "equivalent":
        after += "# formatting-only policy review\n"
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": before,
        **configured_source()}, {"arbitrary.rules": after})
    baseline = tmp_path / "host-baseline.json"
    _git(root, "checkout", "main")
    invoke(root, "audit", "--host", "--save-baseline", "--baseline-file", str(baseline), "--json")
    _git(root, "checkout", "change")
    drift = invoke(root, "audit", "--host", "--drift", "--baseline-file", str(baseline), "--json")
    assert drift["comparison_status"] == "comparable"
    assert bool(drift["expansion_signals"]) == (direction == "widened")
    diff = invoke(root, "diff", "--base", "main", "--json")
    assert not {"decision", "control", "release_decision", "merge_verdict"} & diff.keys()
    assert bool(diff["rows"]) == (direction != "equivalent")
    assert all(row["direction"] == direction for row in diff["rows"])
    assert any(row["expands"] for row in diff["rows"]) == (direction == "widened")
    for actor in ("codex", "claude-code", "cursor"):
        checked = invoke(root, "check", "--agent", actor, "--base", "main", "--head", "HEAD",
                         "--format", "agent-boundary-json")
        assert checked["input_coverage"] == "complete"
        assert any(row["evidence"].get("direction") == "widened" for row in checked["violations"]) == (direction == "widened")
        if direction == "widened":
            assert checked["control"]["permissions"]["merge"] is False
            assert checked["control"]["permissions"]["report_complete"] is False
    verified = invoke(root, "verify", "--base", "main", "--head", "HEAD", "--json")
    assert verified["release_decision"]["decision"] == ("review_required" if direction == "widened" else "passed")
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    assert any(row["evidence"].get("direction") == "widened" for row in report["findings"]) == (direction == "widened")
    if direction == "widened":
        assert verified["control"]["permissions"]["merge"] is False
        assert verified["control"]["permissions"]["report_complete"] is False


@pytest.mark.parametrize("text", REJECTED_POLICIES)
def test_conformance_rejections_deny_boundary_and_configured_completion(tmp_path, text):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY,
        **configured_source()}, {"arbitrary.rules": text})
    checked = invoke(root, "check", "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json")
    assert checked["input_coverage"] == "partial"
    assert checked["issues"]
    assert checked["control"]["permissions"]["report_complete"] is False
    verified = invoke(root, "verify", "--base", "main", "--head", "HEAD", "--json")
    assert verified["control"]["permissions"]["merge"] is False
    assert verified["control"]["permissions"]["report_complete"] is False
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    assert any("arbitrary.rules" in json.dumps(row) for row in report["findings"])


def test_missing_selected_policy_is_a_named_limit_on_both_routes(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY,
        **configured_source()}, {"README.md": "change"})
    (root / "arbitrary.rules").unlink()
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "remove selected policy")
    checked = invoke(root, "check", "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json")
    assert checked["input_coverage"] == "partial"
    assert "arbitrary.rules" in json.dumps(checked["diagnostics"])
    verified = invoke(root, "verify", "--base", "main", "--head", "HEAD", "--json")
    assert verified["control"]["permissions"]["report_complete"] is False


def test_default_routes_do_not_execute_openshell_connect_or_retrieve_credentials(tmp_path, monkeypatch):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY,
        **configured_source()}, {"arbitrary.rules": POLICY.replace("enforcement: enforce", "enforcement: audit")})
    native = "agents_shipgate.core.openshell_native._execute"
    monkeypatch.setattr(native, lambda *args, **kwargs: pytest.fail("unexpected native execution"))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("unexpected network connection"))
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("unexpected network connection"))
    original = subprocess.Popen

    def local_git_only(args, *rest, **kwargs):
        assert isinstance(args, (list, tuple)) and args[0] == "git", args
        assert not {"fetch", "pull", "push", "clone", "ls-remote"} & set(args), args
        return original(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", local_git_only)
    secret = "fixture-credential-value-never-selected"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    results = [invoke(root, "audit", "--host", "--json"),
        invoke(root, "diff", "--base", "main", "--json"),
        invoke(root, "check", "--base", "main", "--head", "HEAD", "--format", "agent-boundary-json"),
        invoke(root, "verify", "--preview", "--base", "main", "--head", "HEAD", "--json"),
        invoke(root, "verify", "--base", "main", "--head", "HEAD", "--json")]
    scanned = CliRunner().invoke(app, ["scan", "--config", str(root / "shipgate.yaml")])
    assert scanned.exit_code == 0, scanned.output
    assert secret not in json.dumps(results) + scanned.output
    assert all(secret not in path.read_text() for path in (root / "agents-shipgate-reports").iterdir() if path.is_file())


EXAMPLE = Path(__file__).resolve().parent.parent / "docs/examples/openshell-review"

#: Git that ignores the caller's repository, hooks and configuration, so the
#: import holds what any reader's fresh repository holds.
_EXAMPLE_GIT_ENV = {
    **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def test_example_history_reproduces_its_recorded_source_identities(tmp_path):
    """Pin what the example's history alone determines.

    Engine, receipt and current-control identities in the capture are
    historical, and `subject_id` also names the import directory, so none is
    pinned here. Commits, trees, the patch, the changed paths and the source
    digests depend on no engine build, date or directory name.
    """

    recorded = json.loads((EXAMPLE / "acceptance.json").read_text(encoding="utf-8"))
    subject = recorded["subject"]["git"]
    repo = tmp_path / "workspace"
    repo.mkdir()

    def git(*args: str, **kwargs) -> bytes:
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, env=_EXAMPLE_GIT_ENV, **kwargs).stdout

    git("init", "-q")
    with (EXAMPLE / "history.git-export").open("rb") as history:
        git("fast-import", "--quiet", stdin=history)
    base, head, base_tree, head_tree = git(
        "rev-parse", "refs/heads/main", "refs/heads/audit-only",
        "refs/heads/main^{tree}", "refs/heads/audit-only^{tree}").decode().split()
    assert (base, base_tree, head, head_tree) == (
        subject["base_commit_sha"], subject["base_tree_sha"],
        subject["head_commit_sha"], subject["head_tree_sha"])
    assert git("merge-base", base, head).decode().strip() == subject["merge_base_sha"]
    assert subject["source_head_commit_sha"] == head
    assert recorded["diff"]["base_commit"] == base
    assert git("diff", base, head) == (EXAMPLE / "change.diff").read_bytes()
    changed = git("diff", "--name-only", "-z", base, head).decode().split("\0")[:-1]
    assert changed == recorded["source_changes"]
    assert recorded["source_sha256"] == {
        path: "sha256:" + hashlib.sha256(git("cat-file", "blob", f"{head}:{path}")).hexdigest()
        for path in recorded["source_sha256"]
    }
    registration = json.loads(git("cat-file", "blob", f"{head}:.shipgate/openshell.json"))
    policy = yaml.safe_load(git("cat-file", "blob", f"{head}:worker-policy.yaml"))
    assert registration["runtime_version"] == recorded["runtime_version"]
    assert policy["version"] == recorded["policy_schema_version"]
