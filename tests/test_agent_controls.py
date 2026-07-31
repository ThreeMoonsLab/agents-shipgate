from __future__ import annotations

import shlex
from pathlib import Path

from agents_shipgate.cli.verify.orchestrator import _resolve_config_under_workspace
from agents_shipgate.core.agent_controls import (
    detect_command_for,
    preview_command_for,
    verify_command_for,
)


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


def test_verify_command_preserves_a_lexical_config_symlink_for_rejection(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    target = repository / "new-gate.yml"
    target.write_text("manifest\n", encoding="utf-8")
    link = repository / "gate.yml"
    link.symlink_to(target.name)

    command = verify_command_for(repository, Path("gate.yml"))

    assert _argument(command, "--config") == "gate.yml"
    assert "new-gate.yml" not in command


def test_verify_command_survives_a_symlinked_workspace_anchor(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    nested = repository / "services" / "api"
    (repository / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)
    alias = tmp_path / "repository-alias"
    alias.symlink_to(repository, target_is_directory=True)
    requested_workspace = alias / "services" / "api"

    for config in (Path("gate.yml"), requested_workspace / "gate.yml"):
        command = verify_command_for(requested_workspace, config)

        assert _argument(command, "--workspace") == str(requested_workspace)
        emitted_config = _argument(command, "--config")
        assert emitted_config == "services/api/gate.yml"
        _config_path, config_relative = _resolve_config_under_workspace(
            repository.resolve(),
            Path(emitted_config),
        )
        assert config_relative.as_posix() == emitted_config


def test_verify_resolver_maps_absolute_config_under_direct_nested_workspace_alias(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    nested = repository / "services" / "api"
    (repository / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)
    alias = tmp_path / "api-alias"
    alias.symlink_to(nested, target_is_directory=True)

    config_path, config_relative = _resolve_config_under_workspace(
        repository.resolve(),
        alias / "gate.yml",
        requested_workspace=alias,
    )

    assert config_path == nested / "gate.yml"
    assert config_relative.as_posix() == "services/api/gate.yml"


def test_verify_resolver_keeps_canonical_config_for_nested_workspace_alias(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    nested = repository / "services" / "api"
    (repository / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)
    alias = tmp_path / "api-alias"
    alias.symlink_to(nested, target_is_directory=True)

    config_path, config_relative = _resolve_config_under_workspace(
        repository.resolve(),
        nested / "gate.yml",
        requested_workspace=alias,
    )

    assert config_path == nested / "gate.yml"
    assert config_relative.as_posix() == "services/api/gate.yml"


def test_verify_command_preserves_and_quotes_the_checked_ref_range(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repository with spaces"
    workspace.mkdir()

    command = verify_command_for(
        workspace,
        Path("gate.yml"),
        base="origin/base; printf BAD",
        head="feature head",
    )

    assert _argument(command, "--base") == "origin/base; printf BAD"
    assert _argument(command, "--head") == "feature head"
    assert _argument(command, "--workspace") == str(workspace)


def test_detect_and_preview_commands_preserve_the_requested_target(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repository with spaces"
    workspace.mkdir()

    detect = detect_command_for(workspace)
    preview = preview_command_for(workspace, Path("custom gate.yml"))

    assert shlex.split(detect) == [
        "shipgate",
        "detect",
        "--workspace",
        str(workspace),
        "--json",
    ]
    assert _argument(preview, "--workspace") == str(workspace)
    assert _argument(preview, "--config") == str(
        (workspace / "custom gate.yml").resolve()
    )
    assert "--preview" in shlex.split(preview)
