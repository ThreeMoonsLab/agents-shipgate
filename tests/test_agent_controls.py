from __future__ import annotations

import shlex
from pathlib import Path

from agents_shipgate.core.agent_controls import verify_command_for


def _argument(command: str, name: str) -> str:
    parts = shlex.split(command)
    return parts[parts.index(name) + 1]


def test_verify_command_relativizes_nested_config_within_workspace_git_root(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    workspace = repository / "services" / "api"
    (repository / ".git").mkdir(parents=True)
    workspace.mkdir(parents=True)

    command = verify_command_for(workspace, Path("shipgate.yaml"))

    assert _argument(command, "--workspace") == str(workspace)
    assert _argument(command, "--config") == "services/api/shipgate.yaml"


def test_verify_command_keeps_cross_repo_config_absolute(tmp_path: Path) -> None:
    requested_repository = tmp_path / "requested"
    other_repository = tmp_path / "other"
    (requested_repository / ".git").mkdir(parents=True)
    (other_repository / ".git").mkdir(parents=True)
    config = other_repository / "shipgate.yaml"

    command = verify_command_for(requested_repository, config)

    assert _argument(command, "--workspace") == str(requested_repository)
    assert _argument(command, "--config") == config.resolve().as_posix()
