from __future__ import annotations

import os
import shlex
import subprocess
import zlib
from pathlib import Path

import pytest

from agents_shipgate.cli.verify import git as verify_git
from agents_shipgate.core import authorization_execution
from agents_shipgate.schemas.human_authorization import build_git_push_operation


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _committed_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.test")
    _git(repo, "config", "user.name", "Shipgate Tests")
    (repo / "policy.yaml").write_text("safe\n", encoding="utf-8")
    _git(repo, "add", "policy.yaml")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD:policy.yaml")


def _committed_sha256_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source-sha256"
    repo.mkdir()
    initialized = subprocess.run(
        ["git", "init", "--object-format=sha256", "-q", str(repo)],
        capture_output=True,
        check=False,
        text=True,
    )
    if initialized.returncode != 0:
        pytest.skip("local Git does not support SHA-256 repositories")
    _git(repo, "config", "user.email", "tests@example.test")
    _git(repo, "config", "user.name", "Shipgate Tests")
    _git(repo, "remote", "add", "origin", "https://example.test/acme/sha256-agent.git")
    (repo / "policy.yaml").write_text("safe\n", encoding="utf-8")
    _git(repo, "add", "policy.yaml")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD^{tree}")


def test_guarded_push_disables_http_redirect_retargeting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signed HTTPS endpoint must remain the endpoint Git contacts."""

    repo = tmp_path / "repo"
    (repo / ".git" / "objects").mkdir(parents=True)
    operation = build_git_push_operation(
        destination_repository_id="example.test/acme/review-agent",
        push_url="https://example.test/acme/review-agent.git",
        source_commit_sha="c" * 40,
        destination_ref="refs/heads/codex/authorized",
        expected_lease_oid="e" * 40,
    )

    monkeypatch.setattr(
        authorization_execution,
        "_trusted_git_executable",
        lambda _workspace: Path("/usr/bin/git"),
    )
    monkeypatch.setattr(authorization_execution, "_git_root", lambda _git, _workspace: repo)
    monkeypatch.setattr(
        authorization_execution,
        "_repository_identity",
        lambda _git, _workspace: operation.destination_repository_id,
    )
    monkeypatch.setattr(
        authorization_execution,
        "_commit_sha",
        lambda _git, _workspace, _ref: operation.source_commit_sha,
    )
    monkeypatch.setattr(
        authorization_execution,
        "_tree_sha",
        lambda _git, _workspace, _ref: "d" * 40,
    )
    monkeypatch.setattr(
        authorization_execution,
        "_active_replace_refs",
        lambda _git, _workspace: [],
    )
    events: list[str] = []
    monkeypatch.setattr(
        authorization_execution,
        "_copy_verified_source_graph",
        lambda **_kwargs: events.append("snapshot"),
    )
    monkeypatch.setattr(
        authorization_execution,
        "_authorization_header",
        lambda **_kwargs: events.append("credentials") or None,
    )
    monkeypatch.setattr(
        authorization_execution,
        "_trusted_git_exec_path",
        lambda _git: Path("/usr/libexec/git-core"),
    )

    invocations: list[tuple[list[str], dict[str, object]]] = []

    def _run(argv, **kwargs):
        invocations.append((argv, kwargs))
        if "rev-parse" in argv:
            events.append("isolated-tree")
            stdout = "d" * 40 + "\n"
        else:
            events.append("push")
            stdout = ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(authorization_execution.subprocess, "run", _run)

    result = authorization_execution.execute_pinned_git_push(
        operation,
        workspace=repo,
        expected_source_tree_sha="d" * 40,
        revalidate_authority=lambda: events.append("revalidate"),
    )

    assert result.returncode == 0
    argv = invocations[-1][0]
    assert isinstance(argv, list)
    config_values = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-c"]
    assert "http.followRedirects=false" in config_values
    assert any(part.startswith("--git-dir=") for part in invocations[-2][0])
    assert events == ["snapshot", "isolated-tree", "credentials", "revalidate", "push"]


def test_execution_snapshot_rejects_blob_bytes_that_do_not_match_oid(tmp_path: Path) -> None:
    repo, commit, blob = _committed_repo(tmp_path)
    loose_object = repo / ".git" / "objects" / blob[:2] / blob[2:]
    if not loose_object.is_file():
        pytest.skip("fixture blob is not stored as a loose object")
    loose_object.chmod(0o644)
    loose_object.write_bytes(zlib.compress(b"blob 5\0evil\n"))

    git_dir = tmp_path / "isolated.git"
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = true\n",
        encoding="utf-8",
    )
    (git_dir / "HEAD").write_text("ref: refs/heads/empty\n", encoding="ascii")
    git = Path("/usr/bin/git")

    with pytest.raises(ValueError, match="snapshot"):
        authorization_execution._copy_verified_source_graph(
            git=git,
            workspace=repo,
            git_dir=git_dir,
            source_commit_sha=commit,
            env=authorization_execution._read_only_git_environment(git),
        )


@pytest.mark.skipif(os.name != "posix", reason="authorization execution v1 is POSIX-only")
def test_source_snapshot_enforces_parent_side_pack_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, commit, _blob = _committed_repo(tmp_path)
    git_dir = tmp_path / "isolated.git"
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = true\n",
        encoding="utf-8",
    )
    (git_dir / "HEAD").write_text("ref: refs/heads/empty\n", encoding="ascii")
    git = Path("/usr/bin/git")
    monkeypatch.setattr(
        authorization_execution,
        "_MAX_AUTHORIZED_GRAPH_PACK_BYTES",
        32,
    )

    with pytest.raises(ValueError, match="object graph exceeds"):
        authorization_execution._copy_verified_source_graph(
            git=git,
            workspace=repo,
            git_dir=git_dir,
            source_commit_sha=commit,
            env=authorization_execution._read_only_git_environment(git),
        )

    assert (tmp_path / "reachable.pack").stat().st_size <= 32


def test_guarded_push_uses_repository_format_v1_for_sha256_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, commit, tree = _committed_sha256_repo(tmp_path)
    operation = build_git_push_operation(
        destination_repository_id="example.test/acme/sha256-agent",
        push_url="https://example.test/acme/sha256-agent.git",
        source_commit_sha=commit,
        destination_ref="refs/heads/codex/authorized-sha256",
        expected_lease_oid="e" * 64,
    )
    monkeypatch.setattr(
        authorization_execution,
        "_authorization_header",
        lambda **_kwargs: None,
    )
    real_run_process = authorization_execution._run_process
    observed_config: list[str] = []

    def _intercept_push(cmd, **kwargs):
        if "push" not in cmd:
            return real_run_process(cmd, **kwargs)
        git_dir_arg = next(part for part in cmd if part.startswith("--git-dir="))
        config = Path(git_dir_arg.removeprefix("--git-dir=")) / "config"
        observed_config.append(config.read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(authorization_execution, "_run_process", _intercept_push)

    result = authorization_execution.execute_pinned_git_push(
        operation,
        workspace=repo,
        expected_source_tree_sha=tree,
    )

    assert result.returncode == 0
    assert observed_config == [
        "[core]\n"
        "\trepositoryformatversion = 1\n"
        "\tbare = true\n"
        "\thooksPath = /dev/null\n"
        "[extensions]\n"
        "\tobjectFormat = sha256\n"
    ]


def test_source_snapshot_disables_promisor_lazy_fetch_and_remote_helpers(
    tmp_path: Path,
) -> None:
    repo, commit, _blob = _committed_repo(tmp_path)
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    loose_object = repo / ".git" / "objects" / tree[:2] / tree[2:]
    if not loose_object.is_file():
        pytest.skip("fixture blob is not stored as a loose object")
    loose_object.unlink()
    marker = tmp_path / "remote-helper-ran"
    _git(repo, "config", "core.repositoryformatversion", "1")
    _git(repo, "config", "extensions.partialClone", "evil")
    _git(repo, "config", "remote.evil.promisor", "true")
    _git(repo, "config", "remote.evil.partialclonefilter", "blob:none")
    _git(repo, "config", "protocol.ext.allow", "always")
    _git(repo, "config", "remote.evil.url", f"ext::touch {marker}")

    git_dir = tmp_path / "isolated.git"
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = true\n",
        encoding="utf-8",
    )
    (git_dir / "HEAD").write_text("ref: refs/heads/empty\n", encoding="ascii")
    execution_root = tmp_path / "execution"
    (execution_root / "home").mkdir(parents=True)
    (execution_root / "xdg").mkdir()
    git = Path("/usr/bin/git")
    env = authorization_execution._sanitized_git_environment(
        git=git,
        execution_root=execution_root,
    )

    with pytest.raises(subprocess.CalledProcessError):
        verify_git.tree_sha(repo, commit)
    assert not marker.exists()
    with pytest.raises(ValueError, match="snapshot"):
        authorization_execution._copy_verified_source_graph(
            git=git,
            workspace=repo,
            git_dir=git_dir,
            source_commit_sha=commit,
            env=env,
        )

    assert env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_ALLOW_PROTOCOL"] == ""
    assert "SHIPGATE_HTTP_AUTHORIZATION" not in env
    assert not marker.exists()


def test_execute_command_preserves_virtualenv_launcher_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = tmp_path / "venv" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(Path(authorization_execution.sys.executable).resolve())
    monkeypatch.setattr(authorization_execution.sys, "executable", str(launcher))

    command = authorization_execution.authorization_execute_command(
        workspace=tmp_path / "workspace",
        receipt="reports/verification-receipt.json",
        artifacts_root="reports",
    )

    argv = shlex.split(command)
    assert argv[2] == str(launcher)
    assert argv[2] != str(launcher.resolve())
