from __future__ import annotations

import io
import subprocess
import tarfile
from dataclasses import dataclass
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


REMOTE_BASE_CANDIDATES = ("origin/main", "origin/master")
LOCAL_BASE_CANDIDATES = ("main", "master")


@dataclass(frozen=True)
class DefaultBaseDetection:
    base: str | None
    notes: list[str]


def commit_sha(workspace: Path, ref: str) -> str | None:
    result = _run_git(
        workspace,
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def detect_default_base(workspace: Path, head: str = "HEAD") -> str | None:
    """Best-effort default base ref for PR-style diff enrichment.

    Tries the remote default branch (``origin/HEAD``) first, then remote
    conventional candidates (``origin/main``, ``origin/master``). A
    candidate qualifies only when it exists locally and points at a
    different commit than ``head`` — diffing a branch against itself adds
    scan cost without diff signal. Local ``main``/``master`` are never
    selected implicitly because they are often stale in CI and worktrees;
    pass ``--base main`` explicitly when that is intended. Never fetches;
    this only reads refs that already exist in the checkout.
    """

    return detect_default_base_with_notes(workspace, head).base


def detect_default_base_with_notes(
    workspace: Path, head: str = "HEAD"
) -> DefaultBaseDetection:
    """Return the implicit base plus warnings for skipped local defaults."""

    head_sha = commit_sha(workspace, head)
    if head_sha is None:
        return DefaultBaseDetection(base=None, notes=[])
    candidates: list[str] = []
    origin_head = _run_git(
        workspace, ["rev-parse", "--abbrev-ref", "origin/HEAD"], check=False
    )
    if origin_head.returncode == 0:
        name = origin_head.stdout.strip()
        if name and name != "origin/HEAD":
            candidates.append(name)
    candidates.extend(c for c in REMOTE_BASE_CANDIDATES if c not in candidates)
    selected_base: str | None = None
    selected_base_sha: str | None = None
    for candidate in candidates:
        sha = commit_sha(workspace, candidate)
        if sha is not None and sha != head_sha:
            selected_base = candidate
            selected_base_sha = sha
            break
    notes = _skipped_local_base_notes(
        workspace,
        head_sha,
        selected_base_sha=selected_base_sha,
    )
    return DefaultBaseDetection(base=selected_base, notes=notes)


def _skipped_local_base_notes(
    workspace: Path,
    head_sha: str,
    *,
    selected_base_sha: str | None,
) -> list[str]:
    notes: list[str] = []
    for local in LOCAL_BASE_CANDIDATES:
        local_sha = commit_sha(workspace, local)
        if local_sha is None or local_sha == head_sha:
            continue
        if selected_base_sha is not None and local_sha == selected_base_sha:
            continue
        remote = f"origin/{local}"
        remote_sha = commit_sha(workspace, remote)
        if remote_sha is not None and remote_sha == local_sha:
            continue
        if remote_sha is not None and remote_sha != local_sha:
            notes.append(
                f"Skipped local base {local!r} for implicit auto-base because "
                "only remote refs are auto-detected; "
                f"{local!r} points at {_short_sha(local_sha)} while {remote!r} "
                f"points at {_short_sha(remote_sha)}. Pass --base {local} "
                "explicitly if that local branch is intended."
            )
            continue
        notes.append(
            f"Skipped local base {local!r} for implicit auto-base because only "
            "remote refs are auto-detected. Pass --base "
            f"{local} explicitly if that local branch is intended."
        )
    return notes


def _short_sha(sha: str) -> str:
    return sha[:12]


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


def read_file_at_ref(workspace: Path, ref: str, path: Path) -> str | None:
    """Return one file's text at ``ref`` without materializing the tree."""

    result = _run_git(
        workspace,
        ["show", f"{ref}:{path.as_posix()}"],
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def working_tree_context(workspace: Path) -> tuple[list[str], str]:
    """Return uncommitted changed paths and tracked-file diff text.

    ``git diff HEAD`` includes staged and unstaged tracked changes. Untracked
    file paths are included for trigger/check context, but their contents are
    intentionally not read into the diff body.
    """

    names = _run_git(workspace, ["diff", "HEAD", "--name-only"])
    body = _run_git(workspace, ["diff", "HEAD"])
    paths = [line for line in names.stdout.splitlines() if line.strip()]
    untracked = _run_git(workspace, ["ls-files", "--others", "--exclude-standard"])
    for line in untracked.stdout.splitlines():
        stripped = line.strip()
        if stripped and stripped not in paths:
            paths.append(stripped)
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


def staged_paths_under(workspace: Path, subdir: str) -> list[str]:
    """Return staged (index) paths under ``subdir``, relative to ``workspace``.

    Reads the git index only (``git diff --cached --name-only --relative``);
    never fetches or writes. Used to warn when generated Agents Shipgate
    reports have been staged for commit — they are generated artifacts that
    ``init`` already gitignores. Returns ``[]`` outside a git checkout.

    Defined below ``_run_git`` so the line-pinned static-only allowlist entry
    for that subprocess call-site (``tests/test_adapter_static_only.py``)
    stays stable.
    """

    prefix = subdir.rstrip("/") + "/"
    result = _run_git(
        workspace, ["diff", "--cached", "--name-only", "--relative"], check=False
    )
    if result.returncode != 0:
        return []
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith(prefix)
    ]


def _safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in tar.getmembers():
        target = (root / member.name).resolve()
        if target != root and root not in target.parents:
            raise ConfigError(f"Base archive contains path outside destination: {member.name}")
    tar.extractall(root, filter="data")


__all__ = [
    "archive_tree",
    "commit_sha",
    "DefaultBaseDetection",
    "detect_default_base",
    "detect_default_base_with_notes",
    "diff_context",
    "ensure_git_workspace",
    "git_path",
    "read_file_at_ref",
    "ref_exists",
    "staged_paths_under",
    "tree_sha",
    "working_tree_context",
]
