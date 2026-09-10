"""A selected component's spelling must survive before its target is read."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from test_codex_plugin import _write_skill_only_codex_plugin
from test_current_control import _git, _live, _verify
from test_current_control import repo as repo  # noqa: F401
from test_current_control_input_origins import plan_at
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)
from agents_shipgate.core.verification_input_currency import validate_current_plan_inputs
from agents_shipgate.inputs.codex_plugin import load_codex_plugin_artifacts


def plugin_source(repo: Path, form: str = "skills_directory") -> tuple[Path, Path, str]:
    root = repo / "plugins" / "reviewer"
    _write_skill_only_codex_plugin(root)
    key = "skills" if form.startswith("skills_") else form
    if form == "skills_directory":
        target = root / "skills"
    elif form == "skills_file":
        target = root / "skills" / "review" / "SKILL.md"
    else:
        target = root / f"{form}.json"
        payload = {
            "apps": {"apps": {"example": {"id": "connector_example"}}},
            "mcpServers": {"mcpServers": {"docs": {"command": "never-execute"}}},
            "hooks": {"preRun": {"command": "never-execute"}},
        }[form]
        target.write_text(json.dumps(payload), encoding="utf-8")
    select_component(root, key, target.relative_to(root).as_posix())
    path = repo / "shipgate.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["tool_sources"] = [{
        "id": "plugin", "type": "codex_plugin", "mode": "package",
        "path": "plugins/reviewer",
    }]
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    return root, target, key


def select_component(root: Path, key: str, path: str) -> None:
    manifest = root / ".codex-plugin" / "plugin.json"
    data = json.loads(manifest.read_text())
    data[key] = path
    manifest.write_text(json.dumps(data), encoding="utf-8")


def capture(repo: Path):
    snapshot = StaticInputSnapshot(repo)
    token = activate_static_input_snapshot(snapshot)
    try:
        manifest = load_manifest(repo / "shipgate.yaml")
        _, artifacts = load_codex_plugin_artifacts(manifest, repo)
        snapshot.finish()
    finally:
        reset_static_input_snapshot(token)
    assert artifacts is not None
    return snapshot, artifacts


def symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("fault", ["alias", "missing"])
def test_caught_component_lookup_never_publishes_current_authority(repo, committed, fault):
    root, target, key = plugin_source(repo)
    alias = root / "skills-alias"
    if fault == "alias":
        symlink(alias, Path("skills"), directory=True)
    select_component(root, key, "./skills-alias")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic aliased component")
    reports = repo / "agents-shipgate-reports"
    plan = None
    if committed and fault == "alias":
        # Committed archives already reject tracked symlinks before adapters
        # run. Preserve that stronger boundary: its current failure record
        # carries no permissions, rather than a successful component capture.
        with pytest.raises(ConfigError, match="unsupported external binding"):
            _verify(repo, archive_head=True)
    else:
        _verify(repo, archive_head=committed)
        plan = plan_at(reports)
        assert plan.inputs.options["dependency_inputs"]["unconfirmable_paths"] == [
            "plugins/reviewer/skills-alias"
        ]
    if plan is None:
        refused = read_current_control(reports, live=lambda: _live(repo)).pointer
        assert refused.control.state == "human_review_required"
        assert not any(refused.control.permissions.model_dump().values())
    else:
        with pytest.raises(CurrentControlUnavailable):
            read_current_control(reports, live=lambda: _live(repo))
    result = CliRunner().invoke(app, [
        "agent", "control", "--workspace", str(repo), "--reports-dir", str(reports),
    ], env={"AGENTS_SHIPGATE_AGENT_MODE": "1"})
    if plan is None:
        assert result.exit_code == 0, result.output
        assert not any(json.loads(result.stdout)["permissions"].values())
    else:
        assert result.exit_code == 4, result.output
        assert result.stdout == ""
        assert "verify" in json.loads(result.stderr.splitlines()[-1])["next_action"]

    # Isolate the captured refusal from Git's independent drift check: even a
    # now ordinary directory cannot reconfirm an old refused/empty capture.
    if fault == "alias":
        alias.unlink()
    shutil.copytree(target, alias)
    if plan is not None:
        with pytest.raises(ValueError, match="dependency.*could not be captured"):
            validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic component repair")
    _verify(repo, archive_head=committed)
    refreshed = read_current_control(reports, live=lambda: _live(repo)).pointer
    assert refreshed.control.permissions.update_pr


@pytest.mark.parametrize("form", ["skills_directory", "skills_file", "apps", "mcpServers", "hooks"])
def test_selected_component_alias_is_captured_before_resolution(repo, form):
    root, target, key = plugin_source(repo, form)
    alias = root / ("SKILL.md" if form == "skills_file" else "component-alias")
    symlink(alias, target.relative_to(root), directory=target.is_dir())
    select_component(root, key, "./" + alias.name)
    snapshot, artifacts = capture(repo)
    assert len(artifacts.component_path_issues) == 1
    assert artifacts.component_path_issues[0].path == "./" + alias.name
    assert snapshot.unconfirmable_dependency_paths() == [alias]
    assert not snapshot.paths_under(target)


@pytest.mark.parametrize("form", ["skills_directory", "skills_file", "apps", "mcpServers", "hooks"])
@pytest.mark.parametrize("committed", [False, True])
def test_ordinary_components_remain_current(repo, form, committed):
    plugin_source(repo, form)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic ordinary component")
    _verify(repo, archive_head=committed)
    reports = repo / "agents-shipgate-reports"
    read_current_control(reports, live=lambda: _live(repo))
    assert not plan_at(reports).inputs.options.get("dependency_inputs", {}).get("unconfirmable_paths")


@pytest.mark.parametrize("fault", ["parent_alias", "canceled_parent_alias", "missing_parent", "outside", "dangling", "cycle", "special"])
def test_failed_lexical_lookups_keep_bounded_diagnostics(repo, fault):
    import os

    root, target, key = plugin_source(repo)
    selected = root / "lookup"
    if fault in {"parent_alias", "canceled_parent_alias"}:
        symlink(selected, Path("skills"), directory=True)
        raw = "lookup/review/SKILL.md" if fault == "parent_alias" else "lookup/../skills"
    elif fault == "missing_parent":
        raw = "missing/nested/SKILL.md"
    elif fault == "outside":
        raw = str(repo / "tools.json")
    elif fault in {"dangling", "cycle"}:
        symlink(selected, Path("not-present" if fault == "dangling" else "lookup"))
        raw = "lookup"
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation unavailable")
        os.mkfifo(selected)
        raw = "lookup"
    select_component(root, key, raw)
    snapshot, artifacts = capture(repo)
    assert len(artifacts.component_path_issues) == 1
    issue = artifacts.component_path_issues[0]
    assert issue.component == "skills"
    assert issue.path == raw
    assert "could not be captured" in issue.reason
    assert len(issue.reason) < 1200
    assert not artifacts.skills
    assert not snapshot.paths_under(target)
    assert snapshot.unconfirmable_dependency_paths() or snapshot.input_directory_identity(
        source="worktree"
    )["unconfirmable_paths"]


def test_standalone_in_root_alias_behavior_is_unchanged(repo):
    root, _, key = plugin_source(repo)
    symlink(root / "alias", Path("skills"), directory=True)
    select_component(root, key, "./alias")
    _, artifacts = load_codex_plugin_artifacts(load_manifest(repo / "shipgate.yaml"), repo)
    assert artifacts is not None
    assert [skill.name for skill in artifacts.skills] == ["review"]
    assert not artifacts.component_path_issues


@pytest.mark.parametrize("form", ["skills_directory", "skills_file", "apps", "mcpServers", "hooks"])
def test_component_file_bytes_are_cached_and_incidental_parent_is_not_a_source(repo, form):
    root, target, _ = plugin_source(repo, form)
    snapshot, artifacts = capture(repo)
    assert not artifacts.component_path_issues
    if target.is_file():
        assert snapshot.has(target)
    directories = snapshot.input_directory_identity(source="worktree")["directories"]
    assert root.relative_to(repo).as_posix() not in {row["path"] for row in directories}


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("form", ["skills_directory", "apps"])
def test_prepare_and_worker_preserve_a_caught_missing_component(repo, committed, form):
    root, _, key = plugin_source(repo, form)
    select_component(root, key, "./missing-component")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic missing component")
    reports = repo / "agents-shipgate-reports"
    result = CliRunner().invoke(app, [
        "verification", "prepare", "--workspace", str(repo), "--no-plugins",
        "--out", str(reports / "verification-plan.json"),
        *(["--base", "HEAD", "--head", "HEAD"] if committed else []),
    ])
    assert result.exit_code == 0, str(result.exception)
    plan = plan_at(reports)
    assert "plugins/reviewer/missing-component" in plan.inputs.options[
        "dependency_inputs"
    ]["unconfirmable_paths"]
    result = CliRunner().invoke(app, [
        "verification", "worker", "--plan", str(reports / "verification-plan.json"),
        "--workspace", str(repo), "--diff", str(reports / "verification-input.diff"),
        "--out", str(reports / "replayed-unit.json"),
    ])
    assert result.exit_code != 0
    assert not (reports / "replayed-unit.json").exists()
    assert "dependency could not be captured" in str(result.exception)


@pytest.mark.parametrize("form", ["apps", "mcpServers", "hooks"])
@pytest.mark.parametrize("committed", [False, True])
def test_optional_file_component_directory_cannot_reconfirm_after_repair(repo, form, committed):
    root, target, _ = plugin_source(repo, form)
    data = target.read_bytes()
    target.unlink()
    target.mkdir()
    # Git does not retain empty directories. Keep this wrong-kind selected
    # path present in committed capture without making it a discovered source.
    (target / "unread.txt").write_text("not a component")
    manifest_path = repo / "shipgate.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["tool_sources"][0]["optional"] = True
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic optional file component is a directory")
    _verify(repo, archive_head=committed)
    reports = repo / "agents-shipgate-reports"
    plan = plan_at(reports)
    assert plan.inputs.options["dependency_inputs"]["unconfirmable_paths"] == [
        target.relative_to(repo).as_posix()
    ]
    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=lambda: _live(repo))
    result = CliRunner().invoke(app, [
        "agent", "control", "--workspace", str(repo), "--reports-dir", str(reports),
    ], env={"AGENTS_SHIPGATE_AGENT_MODE": "1"})
    assert result.exit_code == 4, result.output
    assert result.stdout == ""
    shutil.rmtree(target)
    target.write_bytes(data)
    with pytest.raises(ValueError, match="dependency.*could not be captured"):
        validate_current_plan_inputs(plan, root=repo, artifacts_root=reports)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic optional component repair")
    _verify(repo, archive_head=committed)
    read_current_control(reports, live=lambda: _live(repo))
    surface = json.loads((reports / "report.json").read_text())["codex_plugin_surface"]
    count = {"apps": "app_count", "mcpServers": "mcp_server_stub_count", "hooks": "hook_stub_count"}[form]
    assert surface[count] == 1
    assert not (root / "never-execute").exists()
