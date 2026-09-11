from __future__ import annotations

import json
import os

import pytest
import yaml
from test_current_control import _git, _live, _verify
from test_current_control import repo as repo  # noqa: F401
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control
from agents_shipgate.core.static_inputs import StaticInputSnapshot
from agents_shipgate.schemas.baseline import BaselineFile


def auxiliary(repo, directory, kind):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("policy.yaml" if kind == "policy" else kind + ".json")
    if kind == "policy":
        path.write_text('name: Synthetic empty policy\nversion: "1"\nrules: []\n')
    elif kind == "baseline":
        path.write_text(BaselineFile(
            created_at="2026-09-10T00:00:00Z", source_report_run_id="synthetic-empty-baseline"
        ).model_dump_json())
    else:
        _verify(repo, archive_head=False)
        path.write_bytes((repo / "agents-shipgate-reports/report.json").read_bytes())
    return path, {"policy_packs": [path]} if kind == "policy" else {kind: path}


def current(repo):
    return read_current_control(repo / "agents-shipgate-reports", live=lambda: _live(repo)).pointer


@pytest.mark.parametrize("kind", ["policy", "baseline", "diff_from"])
@pytest.mark.parametrize("committed", [False, True])
def test_portable_auxiliary_copy_does_not_erase_repository_origin(repo, kind, committed):
    path, options = auxiliary(repo, repo / "policy-inputs", kind)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic auxiliary input")
    _verify(repo, archive_head=committed, **options)
    current(repo)
    plan = json.loads((repo / "agents-shipgate-reports/verification-plan.json").read_text())
    (origin,) = plan["inputs"]["options"]["input_origins"]["external"]
    assert origin["path"] == path.relative_to(repo).as_posix()
    assert origin["kind"] == ("git_blob" if committed and kind != "diff_from" else "worktree")
    _git(repo, "update-index", "--assume-unchanged", path.relative_to(repo).as_posix())
    path.write_text(path.read_text() + "\n")
    assert _live(repo).changed_paths == ()
    with pytest.raises(CurrentControlUnavailable):
        current(repo)
    result = CliRunner().invoke(app, [
        "agent", "control", "--workspace", str(repo),
        "--reports-dir", str(repo / "agents-shipgate-reports"),
    ], env={"AGENTS_SHIPGATE_AGENT_MODE": "1"})
    assert result.exit_code == 4, result.output
    assert result.stdout == ""
    assert "workspace_unverifiable" in result.stderr
    payload = json.loads(result.stderr.splitlines()[-1])
    assert payload["error"] == "other_error"
    assert "verify" in payload["next_actions"][0]["command"]


@pytest.mark.parametrize("kind", ["policy", "baseline", "diff_from"])
def test_ignored_auxiliary_input_remains_a_live_dependency(repo, kind):
    with (repo / ".gitignore").open("a") as handle:
        handle.write("private-inputs/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "Ignore local auxiliary inputs")
    path, options = auxiliary(repo, repo / "private-inputs", kind)
    _verify(repo, archive_head=False, **options)
    current(repo)
    path.write_text(path.read_text() + "\n")
    assert _live(repo).changed_paths == ()
    with pytest.raises(CurrentControlUnavailable):
        current(repo)


@pytest.mark.parametrize("kind", ["baseline", "diff_from"])
@pytest.mark.parametrize("mutation", ["bytes", "missing", "symlink"])
def test_external_import_is_frozen_but_its_portable_bytes_must_remain_current(
    repo, tmp_path, kind, mutation
):
    path, options = auxiliary(repo, tmp_path / "outside", kind)
    _verify(repo, archive_head=False, **options)
    before = current(repo)
    reports = repo / "agents-shipgate-reports"
    plan = json.loads((reports / "verification-plan.json").read_text())
    (origin,) = plan["inputs"]["options"]["input_origins"]["external"]
    assert origin["kind"] == "external_snapshot"
    assert origin["path"] is None
    assert str(tmp_path / "outside") not in json.dumps(plan)
    path.write_text(path.read_text() + "\n")
    assert current(repo).current_control_id == before.current_control_id
    portable = reports / origin["input_path"]
    if mutation == "bytes":
        portable.write_text(portable.read_text() + "\n")
    elif mutation == "missing":
        portable.unlink()
    else:
        twin = portable.with_name("identical-copy")
        twin.write_bytes(portable.read_bytes())
        portable.unlink()
        portable.symlink_to(twin.name)
    with pytest.raises(CurrentControlUnavailable):
        current(repo)


@pytest.mark.parametrize("mutation", ["missing", "directory", "symlink", "hardlink", "fifo"])
def test_hidden_input_replacement_refuses_boundedly(repo, mutation):
    _verify(repo, archive_head=False)
    current(repo)
    path = repo / "tools.json"
    _git(repo, "update-index", "--assume-unchanged", "tools.json")
    if mutation == "hardlink":
        os.link(path, repo / "agents-shipgate-reports/twin.json")
    else:
        data = path.read_bytes()
        path.unlink()
        if mutation == "directory":
            path.mkdir()
        elif mutation == "symlink":
            twin = repo / "agents-shipgate-reports/twin.json"
            twin.write_bytes(data)
            path.symlink_to(twin)
        elif mutation == "fifo":
            if not hasattr(os, "mkfifo"):
                pytest.skip("FIFO unavailable")
            os.mkfifo(path)
    with pytest.raises(CurrentControlUnavailable):
        current(repo)


@pytest.mark.parametrize("observation", [1, 2])
def test_input_change_while_finishing_the_identity_session_refuses(repo, monkeypatch, observation):
    _verify(repo, archive_head=False)
    path = repo / "tools.json"
    _git(repo, "update-index", "--assume-unchanged", "tools.json")
    original = StaticInputSnapshot.finish
    seen = []

    def finish(snapshot):
        if snapshot.has(path):
            seen.append(snapshot)
            if len(seen) == observation:
                path.write_text(path.read_text() + "\n")
        return original(snapshot)

    monkeypatch.setattr(StaticInputSnapshot, "finish", finish)
    with pytest.raises(CurrentControlUnavailable):
        current(repo)
    assert len(seen) == observation


@pytest.mark.parametrize("committed", [False, True])
def test_framework_artifact_is_reconfirmed_without_a_tool_source_entry(repo, committed):
    manifest_path = repo / "shipgate.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["openai_api"] = {"prompt_files": ["prompt.md"]}
    manifest_path.write_text(yaml.safe_dump(manifest))
    prompt = repo / "prompt.md"
    prompt.write_text("Look up documentation.\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Synthetic framework prompt")
    _verify(repo, archive_head=committed)
    current(repo)
    _git(repo, "update-index", "--assume-unchanged", "prompt.md")
    prompt.write_text("Changed instructions.\n")
    assert _live(repo).changed_paths == ()
    with pytest.raises(CurrentControlUnavailable):
        current(repo)
