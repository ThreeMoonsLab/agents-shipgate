"""Selected executable dependencies route on either side, separately by host."""
import json
import os
import subprocess

import pytest
from test_hook_script_capture import workspace
from typer.testing import CliRunner

from agents_shipgate.cli.main import app


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("change", ["deleted", "deselected", "committed"])
def test_script_routing_keeps_base_selection(tmp_path, host, change):
    root = workspace(tmp_path / "repo", host=host)

    def git(*args):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
            capture_output=True, check=True,
        )

    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    config = root / (".claude/settings.json" if host == "claude-code" else ".codex/hooks.json")
    if change == "deleted":
        (root / "guard.sh").unlink()
    else:
        (root / "guard.sh").write_text("changed script bytes")
        config.write_text('{"hooks": {}}')
    git("add", "-A")
    git("commit", "-qm", "change")
    extra = []
    if change == "committed":
        extra = ["--head", "change"]
        # The requested head is authoritative even when this checkout differs.
        git("checkout", "-q", "main")
    result = CliRunner().invoke(app, [
        "check", "--agent", "codex", "--workspace", str(root),
        "--base", "main", *extra, "--format", "agent-boundary-json",
    ])
    assert result.exit_code in (0, 10, 20), result.output
    data = json.loads(result.stdout)
    assert data["control"]["state"] != "complete"
    assert host in data["affected_hosts"]
    assert any(
        row["path"] == "guard.sh" and row["evidence"].get("hook_script_hosts") == [host]
        for row in data["violations"]
    )
    assert any(host in row["hosts"] and "guard.sh" in row["paths"] for row in data["host_coverage"])


def test_a_configuration_narrowing_does_not_clear_its_separate_executable_role():
    from agents_shipgate.core.agent_boundary import _with_unclassified_protected_changes

    rows = _with_unclassified_protected_changes(
        changed_files=[".codex/config.toml"], violations=[],
        evaluated_paths={".codex/config.toml"},
        script_hosts={".codex/config.toml": ["claude-code"]},
    )
    assert len(rows) == 1
    assert rows[0].evidence["hook_script_hosts"] == ["claude-code"]
