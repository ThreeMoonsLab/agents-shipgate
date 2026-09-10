"""Implicit plugin components bind named selection, including real absence."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_codex_plugin_input_identity import capture, plugin_source, select_component, symlink
from test_current_control import _git, _live, _verify
from test_current_control import repo as repo  # noqa: F401
from test_current_control_input_origins import plan_at
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from agents_shipgate.core.verification_input_currency import validate_current_plan_inputs
from agents_shipgate.inputs.codex_plugin import load_codex_plugin_artifacts

DEFAULTS = {"skills": "skills", "apps": ".app.json", "mcpServers": ".mcp.json"}


def missing_default(repo, key):
    root, _, _ = plugin_source(repo)
    path = root / ".codex-plugin" / "plugin.json"
    data = json.loads(path.read_text())
    data.pop(key, None)
    path.write_text(json.dumps(data))
    target = root / DEFAULTS[key]
    if target.is_dir():
        shutil.rmtree(target)
    with (repo / ".gitignore").open("a") as handle:
        handle.write(f"/{target.relative_to(repo).as_posix()}\n")
        handle.write("/plugins/reviewer/unrelated.txt\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic absent default component")
    return root, target


def add_default(target, key):
    if key == "skills":
        (target / "added").mkdir(parents=True)
        (target / "added" / "SKILL.md").write_text(
            "---\nname: added\ndescription: Review only\n---\nReview.\n"
        )
    else:
        target.write_text(json.dumps({
            "apps": {"apps": {"example": {"id": "connector_example"}}},
            "mcpServers": {"mcpServers": {"docs": {"command": "never-execute"}}},
        }[key]))


@pytest.mark.parametrize("key", DEFAULTS)
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("observation", [0, 1, 2])
def test_ignored_default_insertion_invalidates_current_control(repo, key, committed, observation):
    _, target = missing_default(repo, key)
    _verify(repo, archive_head=committed)
    reports = repo / "agents-shipgate-reports"
    read_current_control(reports, live=lambda: _live(repo))
    calls = []
    if observation == 0:
        add_default(target, key)

    def live():
        state = _live(repo)
        calls.append(1)
        if len(calls) == observation:
            add_default(target, key)
        return state

    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=live)
    assert _live(repo).changed_paths == ()
    result = CliRunner().invoke(app, [
        "agent", "control", "--workspace", str(repo), "--reports-dir", str(reports),
    ], env={"AGENTS_SHIPGATE_AGENT_MODE": "1"})
    assert result.exit_code == 4, result.output
    assert result.stdout == ""
    assert "verify" in json.loads(result.stderr.splitlines()[-1])["next_action"]
    _, artifacts = capture(repo)
    assert getattr(artifacts, {"mcpServers": "mcp_server_stubs"}.get(key, key))


@pytest.mark.parametrize("key", DEFAULTS)
@pytest.mark.parametrize("committed", [False, True])
def test_prepare_worker_reject_a_new_default(repo, key, committed):
    _, target = missing_default(repo, key)
    reports = repo / "agents-shipgate-reports"
    result = CliRunner().invoke(app, [
        "verification", "prepare", "--workspace", str(repo), "--no-plugins",
        "--out", str(reports / "verification-plan.json"),
        *(["--base", "HEAD", "--head", "HEAD"] if committed else []),
    ])
    assert result.exit_code == 0, str(result.exception)
    plan = plan_at(reports)
    validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    command = [
        "verification", "worker", "--plan", str(reports / "verification-plan.json"),
        "--workspace", str(repo), "--diff", str(reports / "verification-input.diff"),
        "--out", str(reports / "replayed-unit.json"),
    ]
    result = CliRunner().invoke(app, command)
    assert result.exit_code == 0, str(result.exception)
    (reports / "replayed-unit.json").unlink()
    add_default(target, key)
    with pytest.raises(ValueError, match="lookup candidate appeared"):
        validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    result = CliRunner().invoke(app, command)
    assert result.exit_code != 0
    assert "lookup candidate appeared" in str(result.exception)
    assert not (reports / "replayed-unit.json").exists()


def test_named_absence_rejects_a_case_alias_on_this_filesystem(tmp_path):
    (tmp_path / ".APP.JSON").write_text("{}")
    candidate = tmp_path / ".app.json"
    if not candidate.exists():
        pytest.skip("filesystem keeps these names distinct")
    snapshot = StaticInputSnapshot(tmp_path)
    with pytest.raises(ValueError, match="differently spelled"):
        snapshot.bind_dependency_absence(candidate)
    assert snapshot.absent_dependency_paths() == []
    assert snapshot.unconfirmable_dependency_paths() == [candidate]


@pytest.mark.parametrize("key", DEFAULTS)
@pytest.mark.parametrize("explicit", [False, True])
def test_only_selected_defaults_bind_named_dependencies(repo, key, explicit):
    root, target = missing_default(repo, key)
    if explicit:
        selected = root / "explicit-component"
        add_default(selected, key)
        select_component(root, key, selected.name)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Synthetic explicit component")
    _verify(repo, archive_head=False)
    reports = repo / "agents-shipgate-reports"
    plan = plan_at(reports)
    dependencies = plan.inputs.options["dependency_inputs"]
    relative = target.relative_to(repo).as_posix()
    assert (relative in dependencies["absent_paths"]) is not explicit
    assert relative not in dependencies["present_paths"]
    assert root.relative_to(repo).as_posix() not in {
        row["path"] for row in plan.inputs.options["input_directories"]["directories"]
    }
    (root / "unrelated.txt").write_text("not read by the plugin")
    if explicit:
        add_default(target, key)
    assert _live(repo).changed_paths == ()
    read_current_control(reports, live=lambda: _live(repo))


@pytest.mark.parametrize("key", DEFAULTS)
@pytest.mark.parametrize("committed", [False, True])
def test_present_implicit_component_uses_normal_capture_and_fresh_verification(repo, key, committed):
    _, target = missing_default(repo, key)
    add_default(target, key)
    _git(repo, "add", "-f", str(target.relative_to(repo)))
    _git(repo, "commit", "-m", "Synthetic present default")
    _verify(repo, archive_head=committed)
    reports = repo / "agents-shipgate-reports"
    read_current_control(reports, live=lambda: _live(repo))
    plan = plan_at(reports)
    assert target.relative_to(repo).as_posix() in plan.inputs.options[
        "dependency_inputs"
    ]["present_paths"]
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    with pytest.raises((OSError, ValueError)):
        validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    _git(repo, "add", "-u")
    _git(repo, "commit", "-m", "Synthetic default removal")
    _verify(repo, archive_head=committed)
    read_current_control(reports, live=lambda: _live(repo))


@pytest.mark.parametrize("key", DEFAULTS)
@pytest.mark.parametrize("fault", ["dangling", "cycle", "alias", "special", "wrong_kind"])
def test_failed_implicit_components_keep_diagnostics_and_obligations(repo, key, fault):
    import os

    root, target = missing_default(repo, key)
    if fault in {"dangling", "cycle", "alias"}:
        destination = Path("missing" if fault == "dangling" else target.name)
        if fault == "alias":
            destination = Path("ordinary")
            add_default(root / destination, key)
        symlink(target, destination, directory=key == "skills")
    elif fault == "special":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation unavailable")
        os.mkfifo(target)
    elif key == "skills":
        target.write_text("not a skill directory")
    else:
        target.mkdir()
    snapshot, artifacts = capture(repo)
    issues = [issue for issue in artifacts.component_path_issues if issue.component == key]
    assert len(issues) == 1
    assert issues[0].path == target.name
    assert len(issues[0].reason) < 1200
    assert target in snapshot.present_dependency_paths()
    if fault == "wrong_kind" and key == "skills":
        # Ordinary bytes remain bound even when skill parsing diagnoses them.
        assert snapshot.has(target)
    else:
        assert target in snapshot.unconfirmable_dependency_paths()


@pytest.mark.parametrize("present", [False, True])
def test_excluded_default_cannot_be_confirmed_absent_or_read(repo, present):
    _, target = missing_default(repo, "apps")
    if present:
        add_default(target, "apps")
    snapshot = StaticInputSnapshot(repo, excluded_paths=[target])
    token = activate_static_input_snapshot(snapshot)
    try:
        _, artifacts = load_codex_plugin_artifacts(load_manifest(repo / "shipgate.yaml"), repo)
        snapshot.finish()
    finally:
        reset_static_input_snapshot(token)
    assert target not in snapshot.absent_dependency_paths()
    assert target in snapshot.unconfirmable_dependency_paths()
    assert not snapshot.has(target)
    assert any("excluded verification output" in row.reason for row in artifacts.component_path_issues)


@pytest.mark.parametrize("failure", ["permission", "cap"])
def test_default_lookup_failures_remain_named_even_if_a_later_probe_succeeds(repo, monkeypatch, failure):
    root, target = missing_default(repo, "apps")
    original = StaticInputSnapshot.bind_dependency_absence
    calls = []

    def fail_once(self, path):
        if path == target and not calls:
            calls.append(1)

            def fail_parent(path):
                assert path == root
                if failure == "permission":
                    raise PermissionError("synthetic unreadable plugin parent")
                raise ValueError("synthetic directory entry cap")

            with monkeypatch.context() as scoped:
                scoped.setattr(self, "bind_directory", fail_parent)
                return original(self, path)
        return original(self, path)

    monkeypatch.setattr(StaticInputSnapshot, "bind_dependency_absence", fail_once)
    snapshot, artifacts = capture(repo)
    assert calls == [1]
    assert snapshot.unconfirmable_dependency_paths() == [target]
    assert target not in snapshot.absent_dependency_paths()
    assert artifacts.component_path_issues[0].path == ".app.json"
    assert "Default component lookup could not be captured" in artifacts.component_path_issues[0].reason


def test_case_alias_added_after_absence_is_refused_by_full_currency(repo):
    root, target = missing_default(repo, "apps")
    with (repo / ".gitignore").open("a") as handle:
        handle.write("/plugins/reviewer/.APP.JSON\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "Synthetic ignored alias")
    _verify(repo, archive_head=False)
    reports = repo / "agents-shipgate-reports"
    (root / ".APP.JSON").write_text("{}")
    if not target.exists():
        pytest.skip("filesystem keeps these names distinct")
    assert _live(repo).changed_paths == ()
    with pytest.raises(ValueError, match="differently spelled"):
        validate_current_plan_inputs(plan_at(reports), root=repo, artifacts_root=reports)
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=lambda: _live(repo))


@pytest.mark.parametrize("value", [None, "", " ", [], ["", 7], 42])
def test_empty_or_invalid_explicit_paths_preserve_default_selection(repo, value):
    root, target = missing_default(repo, "apps")
    path = root / ".codex-plugin" / "plugin.json"
    data = json.loads(path.read_text())
    data["apps"] = value
    path.write_text(json.dumps(data))
    snapshot, artifacts = capture(repo)
    assert target in snapshot.absent_dependency_paths()
    assert not artifacts.apps
    add_default(target, "apps")
    snapshot, artifacts = capture(repo)
    assert target in snapshot.present_dependency_paths()
    assert [row.name for row in artifacts.apps] == ["example"]


@pytest.mark.parametrize("fault", ["dangling", "case_alias"])
def test_failed_default_lookup_needs_fresh_verification_after_repair(repo, fault):
    root, target = missing_default(repo, "apps")
    alias = root / ".APP.JSON"
    if fault == "case_alias":
        alias.write_text("{}")
        if not target.exists():
            pytest.skip("filesystem keeps these names distinct")
    else:
        symlink(target, Path("missing"))
    _verify(repo, archive_head=False)
    reports = repo / "agents-shipgate-reports"
    plan = plan_at(reports)
    assert target.relative_to(repo).as_posix() in plan.inputs.options[
        "dependency_inputs"
    ]["unconfirmable_paths"]
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=lambda: _live(repo))
    (alias if fault == "case_alias" else target).unlink()
    add_default(target, "apps")
    with pytest.raises(ValueError, match="dependency.*could not be captured"):
        validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    _verify(repo, archive_head=False)
    read_current_control(reports, live=lambda: _live(repo))
    _, artifacts = capture(repo)
    assert [row.name for row in artifacts.apps] == ["example"]
