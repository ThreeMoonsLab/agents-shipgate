from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

from agents_shipgate.core.errors import ConfigError


def ensure_git_workspace(workspace: Path) -> Path:
    """Return the git root containing ``workspace``.

    ``verify`` is a PR-diff workflow, so git is required for base/head
    orchestration. The command remains local-only: all calls are fixed argv
    reads against the existing checkout.
    """

    result = _run_git(workspace, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        raise ConfigError(f"Workspace is not inside a git checkout: {workspace}")
    root = result.stdout.strip()
    if not root:
        raise ConfigError(f"Workspace is not inside a git checkout: {workspace}")
    return Path(root).resolve()


def ref_exists(workspace: Path, ref: str) -> bool:
    result = _run_git(
        workspace,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        check=False,
    )
    return result.returncode == 0


def tree_sha(workspace: Path, ref: str) -> str:
    result = _run_git(workspace, ["rev-parse", f"{ref}^{{tree}}"])
    return result.stdout.strip()


def git_path(workspace: Path, path: str) -> Path:
    result = _run_git(workspace, ["rev-parse", "--git-path", path])
    resolved = Path(result.stdout.strip())
    if resolved.is_absolute():
        return resolved.resolve()
    return (workspace / resolved).resolve()


def diff_context(workspace: Path, base: str, head: str) -> tuple[list[str], str]:
    revspec = f"{base}...{head}"
    names = _run_git(workspace, ["diff", "--name-only", revspec])
    body = _run_git(workspace, ["diff", revspec])
    paths = [line for line in names.stdout.splitlines() if line.strip()]
    return paths, body.stdout


def archive_tree(workspace: Path, ref: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    archive = _run_git(
        workspace,
        ["archive", "--format=tar", ref],
        text=False,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
        _safe_extract(tar, destination)


def _run_git(
    workspace: Path,
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(workspace), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        check=check,
        text=text,
        timeout=60,
    )


def _safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in tar.getmembers():
        target = (root / member.name).resolve()
        if target != root and root not in target.parents:
            raise ConfigError(f"Base archive contains path outside destination: {member.name}")
    tar.extractall(root, filter="data")


__all__ = [
    "archive_tree",
    "diff_context",
    "ensure_git_workspace",
    "git_path",
    "ref_exists",
    "tree_sha",
]
