"""Current authority must reconfirm evaluated inputs, not only Git's change list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from test_current_control import _git, _live, _verify
from test_current_control import repo as repo  # noqa: F401 - shared real-engine fixture

from agents_shipgate.core.current_control import CurrentControlUnavailable, read_current_control


@pytest.mark.parametrize("input_name", ["shipgate.yaml", "tools.json", "api.json"])
@pytest.mark.parametrize("observation", [0, 1, 2])
def test_git_hidden_declared_input_revokes_current_control(
    repo: Path, input_name: str, observation: int
) -> None:
    if input_name == "api.json":
        manifest_path = repo / "shipgate.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["tool_sources"][0].update(type="openapi", path="api.json")
        manifest_path.write_text(yaml.safe_dump(manifest))
        (repo / "api.json").write_text(json.dumps({
            "openapi": "3.0.3",
            "info": {"title": "Docs", "version": "1"},
            "servers": [{"url": "https://docs.example.test"}],
            "paths": {"/docs": {"get": {
                "operationId": "docs.lookup",
                "responses": {"200": {"description": "Document"}},
            }}},
        }))
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "GET regression input")
    _verify(repo, archive_head=False)
    reports = repo / "agents-shipgate-reports"
    before = read_current_control(reports, live=lambda: _live(repo))
    assert before.pointer.control.permissions.update_pr
    plan = json.loads((reports / "verification-plan.json").read_text())
    blobs = [plan["inputs"]["config"], *plan["inputs"]["tool_sources"]]
    assert any(blob["path"] == input_name and blob["source"] == "worktree" for blob in blobs)
    _git(repo, "update-index", "--assume-unchanged", input_name)
    path = repo / input_name

    def change():
        # Even a whitespace-only change invalidates a byte-bound answer.
        path.write_text(path.read_text() + "\n")

    if observation == 0:
        change()
    observed = []

    def observe():
        state = _live(repo)
        assert state.changed_paths == ()
        observed.append(state)
        if len(observed) == observation:
            change()
        return state

    with pytest.raises(CurrentControlUnavailable):
        read_current_control(reports, live=observe, attempts=1)
    assert len(observed) == max(1, observation)
