"""Recognized configuration locations must not disappear when they are directories."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.host_comparison import compare_host_refs
from agents_shipgate.core.host_grants import host_audit_inventory, inventory_is_complete


@pytest.mark.parametrize("source", [".mcp.json", "packages/app/.mcp.json", ".claude/settings.json"])
def test_directory_at_config_path_is_a_failed_input(tmp_path, source):
    (tmp_path / source).mkdir(parents=True)
    inventory = host_audit_inventory(tmp_path)
    assert not inventory_is_complete(inventory)
    assert any(a["path"] == source and a["parse_status"] == "failed" for a in inventory["artifacts"])
    assert any(i.get("source") == source and i.get("blocking") for i in inventory["issues"])


def test_ordinary_containers_are_not_failed_inputs(tmp_path):
    for path in ["src", ".claude", "packages/app", ".cursor/rules/python", ".claude/commands/review"]:
        (tmp_path / path).mkdir(parents=True)
    inventory = host_audit_inventory(tmp_path)
    assert inventory_is_complete(inventory)
    assert inventory["issues"] == []


def _committed_directory(tmp_path):
    root = tmp_path / "repo"
    (root / ".mcp.json").mkdir(parents=True)
    (root / ".mcp.json/readme.txt").write_text("A directory is not an MCP configuration.\n")
    for args in [("init", "-q"), ("config", "user.name", "T"),
                 ("config", "user.email", "t@example.invalid"), ("add", "."),
                 ("commit", "-qm", "invalid configuration directory")]:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def test_worktree_diff_does_not_call_a_configuration_directory_no_change(tmp_path):
    root = _committed_directory(tmp_path)
    result = CliRunner().invoke(app, ["diff", "--workspace", str(root), "--base", "HEAD", "--json"])
    payload = json.loads(result.output)
    assert payload["comparison_status"] == "incomparable", payload


def test_committed_comparison_preserves_wrong_kind_input(tmp_path):
    root = _committed_directory(tmp_path)
    result = compare_host_refs(workspace=root, base="HEAD", head="HEAD", auto_base=False,
                               config_relative=Path("shipgate.yaml"))
    assert result is not None
    assert result.comparison_status == "incomparable"


def test_nested_rule_and_command_directories_keep_their_files(tmp_path):
    rule = tmp_path / ".cursor/rules/python/style.mdc"
    command = tmp_path / ".claude/commands/review/check.md"
    rule.parent.mkdir(parents=True)
    command.parent.mkdir(parents=True)
    rule.write_text("---\ndescription: Style\nglobs: '**/*.py'\nalwaysApply: false\n---\nUse clear names.\n")
    command.write_text("---\ndescription: Review\n---\nReview the change.\n")
    inventory = host_audit_inventory(tmp_path)
    assert inventory_is_complete(inventory), inventory["issues"]
    paths = {a["path"] for a in inventory["artifacts"]}
    assert ".cursor/rules/python/style.mdc" in paths
    assert ".claude/commands/review/check.md" in paths
    assert ".cursor/rules" not in paths
    assert ".claude/commands" not in paths
