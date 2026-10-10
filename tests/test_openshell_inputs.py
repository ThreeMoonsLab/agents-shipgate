from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_current_control import _live, _verify
from test_current_control import repo as repo  # noqa: F401
from test_partial_host_comparison import _git, _repository
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.host_comparison import compare_host_refs
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.host_grants import HostStaticParseCache, build_host_boundary_snapshot

REGISTRATION = ".shipgate/openshell.json"
POLICY = """version: 1
filesystem_policy: {include_workdir: false, read_only: [/data]}
network_policies:
  api:
    binaries: [{path: /usr/bin/client}]
    endpoints: [{host: api.example.com, port: 443, protocol: rest, access: read-only, enforcement: enforce}]
"""


def selection(path="arbitrary.rules"):
    return {"version": 1, "runtime_version": "0.1.2", "policies": [{"path": path, "role": "authored"}]}


def comparison(root, *, head=None):
    result = compare_host_refs(workspace=root, base="main", head=head, auto_base=False, config_relative=Path("shipgate.yaml"))
    assert result is not None
    return result


def register(root, path="arbitrary.rules"):
    registration = root / REGISTRATION
    registration.parent.mkdir(parents=True, exist_ok=True)
    registration.write_text(json.dumps(selection(path)))
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(POLICY)
    return target


def test_committed_arbitrary_policy_uses_each_tree_even_with_dirty_checkout(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY},
                       {"arbitrary.rules": POLICY.replace("enforcement: enforce", "enforcement: audit")})
    (root / "arbitrary.rules").write_text(POLICY)
    committed = comparison(root, head="HEAD")
    assert committed.comparison_status == "comparable"
    row, = [row for row in committed.rows if row.subject.startswith("openshell")]
    assert row.expands and row.direction == "widened"
    assert not [row for row in comparison(root).rows if row.subject.startswith("openshell")]


def test_selection_only_change_is_visible_even_with_identical_bytes(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection("one"), "one": POLICY, "two": POLICY},
                       {REGISTRATION: selection("two")})
    result = comparison(root, head="HEAD")
    rows = [row for row in result.rows if row.subject.startswith("openshell")]
    assert len(rows) == 2 and {row.subject for row in rows} == {"openshell one", "openshell two"}
    assert all("selected document added or removed" in row.why for row in rows)


def test_deleted_selected_policy_is_unread_not_an_empty_policy(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection(), "arbitrary.rules": POLICY}, {"README.md": "change"})
    (root / "arbitrary.rules").unlink()
    result = comparison(root)
    assert result.comparison_status == "incomparable" and not result.rows
    assert "arbitrary.rules" in result.input_script_absent_paths


def test_selected_link_chain_materializes_target_bytes_and_retains_hops(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection("policy-link"), "actual": POLICY},
                       {"actual": POLICY.replace("enforcement: enforce", "enforcement: audit")},
                       links={"policy-link": "middle", "middle": "actual"})
    result = comparison(root, head="HEAD")
    assert result.comparison_status == "comparable"
    assert any(row.expands and row.subject == "openshell policy-link" for row in result.rows)
    snapshot = build_host_boundary_snapshot(root)
    artifact = next(item for item in snapshot.inventory["artifacts"] if item["kind"] == "openshell_policy")
    assert artifact["resolved_through"] == ["middle", "actual"]
    assert snapshot.cache.openshell_selected_paths == {REGISTRATION, "policy-link", "middle", "actual"}


def test_policy_only_worktree_change_binds_regular_and_link_objects(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection("policy-link"), "actual": POLICY},
                       {"README.md": "change"}, links={"policy-link": "actual"})
    (root / "actual").write_text(POLICY.replace("enforcement: enforce", "enforcement: audit"))
    result = comparison(root)
    assert result.comparison_status == "comparable"
    bindings = {blob.path: blob.source for blob in result.input_script_blobs}
    assert bindings == {REGISTRATION: "worktree", "actual": "worktree", "policy-link": "generated"}


@pytest.mark.parametrize("configured", [True, False])
def test_ignored_selected_policy_invalidates_existing_control(repo, configured):
    if not configured:
        (repo / "shipgate.yaml").unlink()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "host-only workspace")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\nprivate/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore selected policy")
    target = register(repo, "private/policy")
    if configured:
        _verify(repo, archive_head=False)
        plan = json.loads((repo / "agents-shipgate-reports/verification-plan.json").read_text())
        assert "private/policy" in {item["path"] for item in plan["inputs"]["options"]["dependency_inputs"]["files"]}
    else:
        result = CliRunner().invoke(app, ["verify", "--workspace", str(repo), "--base", "main", "--json"])
        assert result.exit_code in (0, 10, 20), result.output
    out = repo / "agents-shipgate-reports"
    read_current_control(out, live=lambda: _live(repo))
    target.write_text(POLICY.replace("enforcement: enforce", "enforcement: audit"))
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(out, live=lambda: _live(repo))


@pytest.mark.parametrize("configured", [True, False])
def test_ignored_selected_link_retargeting_invalidates_current_control(repo, configured):
    if not configured:
        (repo / "shipgate.yaml").unlink()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "host-only workspace")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\nprivate/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore bundle")
    target = register(repo, "private/link")
    target.unlink()
    one, two = repo / "private/one", repo / "private/two"
    one.write_text(POLICY)
    two.write_text(POLICY)
    target.symlink_to("one")
    if configured:
        _verify(repo, archive_head=False)
    else:
        result = CliRunner().invoke(app, ["verify", "--workspace", str(repo), "--base", "main", "--json"])
        assert result.exit_code in (0, 10, 20), result.output
    out = repo / "agents-shipgate-reports"
    if configured:
        plan = json.loads((out / "verification-plan.json").read_text())
        links = plan["inputs"]["options"]["dependency_inputs"]["links"]
        assert [item["path"] for item in links] == ["private/link"]
    read_current_control(out, live=lambda: _live(repo))
    target.unlink()
    target.symlink_to("two")
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(out, live=lambda: _live(repo))


@pytest.mark.parametrize("configured", [True, False])
def test_ignored_selecting_reference_change_invalidates_control_with_identical_policy_bytes(repo, configured):
    if not configured:
        (repo / "shipgate.yaml").unlink()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "host-only workspace")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n.shipgate/\nprivate/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore selection bundle")
    register(repo, "private/one")
    (repo / "private/two").write_text(POLICY)
    result = CliRunner().invoke(app, ["verify", "--workspace", str(repo), "--base", "main", "--json"])
    assert result.exit_code in (0, 10, 20), result.output
    out = repo / "agents-shipgate-reports"
    read_current_control(out, live=lambda: _live(repo))
    (repo / REGISTRATION).write_text(json.dumps(selection("private/two")))
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(out, live=lambda: _live(repo))


def test_unrelated_hook_link_does_not_acquire_a_policy_dependency(tmp_path):
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": '"${CLAUDE_PROJECT_DIR}/guard.sh"'}]}]}}))
    (tmp_path / "target").write_text("not policy")
    (tmp_path / "guard.sh").symlink_to("target")
    cache = HostStaticParseCache()
    build_host_boundary_snapshot(tmp_path, cache=cache)
    assert not cache.openshell_selected_paths and not cache.openshell_input_reads


def test_credential_shaped_link_target_is_unconfirmable_without_path_disclosure(tmp_path):
    secret = "sk-live-0123456789abcdef0123456789abcdef"
    register(tmp_path, secret)
    (tmp_path / REGISTRATION).write_text(json.dumps(selection("policy-link")))
    (tmp_path / "policy-link").symlink_to(secret)
    snapshot = build_host_boundary_snapshot(tmp_path)
    published = json.dumps(snapshot.inventory) + json.dumps(snapshot.cache.openshell_input_reads)
    assert secret not in published
    assert not snapshot.inventory["grants"]
    assert snapshot.cache.openshell_input_reads["policy-link"]["limit"] == "redacted_input_path"


def test_selected_hook_link_keeps_unconfirmable_hook_capture(tmp_path):
    root = _repository(tmp_path, {REGISTRATION: selection("guard.sh"), "actual": POLICY,
        ".claude/settings.json": {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
        "command": '"${CLAUDE_PROJECT_DIR}/guard.sh"'}]}]}}}, {"README.md": "change"},
        links={"guard.sh": "actual"})
    result = comparison(root)
    assert "guard.sh" in result.input_script_unconfirmable_paths
