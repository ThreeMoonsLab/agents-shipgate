"""Configured verification must bind host script bytes as dependency inputs."""
import json

import pytest
from test_current_control import _git, _live, _verify
from test_current_control import repo as repo  # noqa: F401

from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.hook_script_capture import capture_hook_script
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from agents_shipgate.core.trust_roots import IdentityBoundReadSession


def test_configured_verify_binds_an_ignored_hook_script(repo):
    (repo / ".gitignore").write_text("agents-shipgate-reports/\nguard.sh\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore constructed script")
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": '"${CLAUDE_PROJECT_DIR}/guard.sh"'},
    ]}]}}))
    script = repo / "guard.sh"
    script.write_text("first generation")
    _verify(repo, archive_head=False)
    out = repo / "agents-shipgate-reports"
    plan = json.loads((out / "verification-plan.json").read_text())
    dependencies = plan["inputs"]["options"]["dependency_inputs"]["files"]
    assert any(item["path"] == "guard.sh" for item in dependencies)
    read_current_control(out, live=lambda: _live(repo))
    script.write_text("second generation")
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(out, live=lambda: _live(repo))


@pytest.mark.parametrize("path,absent", [("guard.sh", "guard.sh"), ("private/guard.sh", "private")])
def test_configured_verify_binds_missing_ignored_script(repo, path, absent):
    (repo / ".gitignore").write_text("agents-shipgate-reports/\nguard.sh\nprivate/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore constructed dependencies")
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": f'"${{CLAUDE_PROJECT_DIR}}/{path}"'},
    ]}]}}))
    _verify(repo, archive_head=False)
    out = repo / "agents-shipgate-reports"
    plan = json.loads((out / "verification-plan.json").read_text())
    dependencies = plan["inputs"]["options"]["dependency_inputs"]
    assert absent in dependencies["absent_paths"]
    read_current_control(out, live=lambda: _live(repo))
    script = repo / path
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("now present")
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(out, live=lambda: _live(repo))


@pytest.mark.parametrize("shape", ["symlink", "directory", "hardlink"])
def test_unsafe_dependency_cannot_leave_a_confirmable_snapshot(tmp_path, shape):
    script = tmp_path / "guard.sh"
    if shape == "directory":
        script.mkdir()
    else:
        target = tmp_path / "target"
        target.write_text("not safely bound")
        if shape == "symlink":
            script.symlink_to(target)
        else:
            script.hardlink_to(target)
    snapshot = StaticInputSnapshot(tmp_path)
    token = activate_static_input_snapshot(snapshot)
    try:
        reader = IdentityBoundReadSession(tmp_path, max_entries=1000, max_total_bytes=4096)
        facts = capture_hook_script(reader, "guard.sh")
        assert facts["limit"] is not None
        assert snapshot.unconfirmable_dependency_paths() == [script]
        assert snapshot.dependency_paths() == []
    finally:
        reset_static_input_snapshot(token)


@pytest.mark.parametrize("initial", ["missing_file", "missing_parent", "symlink"])
def test_advisory_control_binds_failed_ignored_script(tmp_path, initial):
    from test_hook_script_capture import workspace
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("guard.sh\nprivate/\ntarget\nagents-shipgate-reports/\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test User")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-m", "base")
    _git(root, "checkout", "-qb", "change")
    path = "private/guard.sh" if initial == "missing_parent" else "guard.sh"
    workspace(root, command=f'"${{CLAUDE_PROJECT_DIR}}/{path}"')
    (root / "guard.sh").unlink()
    script = root / path
    if initial == "symlink":
        (root / "target").write_text("unsafe target")
        script.symlink_to(root / "target")
    run = CliRunner()
    result = run.invoke(app, ["verify", "--workspace", str(root), "--base", "main", "--json"])
    assert result.exit_code in (0, 10, 20), result.output
    comparison = json.loads(result.stdout)["host_comparison"]
    command = ["agent", "control", "--workspace", str(root)]
    before = run.invoke(app, command)
    if initial == "symlink":
        assert comparison["input_script_unconfirmable_paths"] == [path]
        assert before.exit_code != 0
        script.unlink()
    else:
        assert comparison["input_script_absent_paths"] == ["private" if initial == "missing_parent" else path]
        assert before.exit_code == 0, before.output
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("now safely present")
    after = run.invoke(app, command)
    assert after.exit_code != 0, after.output


def test_missing_dependency_appearing_during_other_script_read_invalidates_currency(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    import agents_shipgate.core.hook_script_capture as capture
    from agents_shipgate.core.current_control import _validate_hook_script_currency

    script = tmp_path / "existing.sh"
    script.write_bytes(b"fixed bytes")
    missing = tmp_path / "missing.sh"
    original = capture.capture_hook_script

    def race(reader, path, **kwargs):
        result = original(reader, path, **kwargs)
        missing.write_text("appeared while reading a different dependency")
        return result

    monkeypatch.setattr(capture, "capture_hook_script", race)
    verifier = {"host_comparison": {
        "input_script_absent_paths": ["missing.sh"],
        "input_script_blobs": [{"path": "existing.sh", "sha256": "sha256:" + hashlib.sha256(b"fixed bytes").hexdigest(),
                                "size_bytes": len(b"fixed bytes"), "source": "worktree"}],
    }}
    with pytest.raises(CurrentControlUnavailable):
        _validate_hook_script_currency(
            tmp_path, SimpleNamespace(root=tmp_path), {"verifier": json.dumps(verifier).encode()},
        )
