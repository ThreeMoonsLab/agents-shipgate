"""Ephemeral per-cell workspace creation.

Each cell gets a fresh copy of ``benchmark/repos/<archetype>/`` with a clean
``git init``. The vendored archetype tree is not a bare git repo, so we copy
rather than ``git worktree add``. After overlay application, the workspace is
committed once so the agent sees a clean tree and ``git diff`` is meaningful
at the end of the run.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorkspaceError(RuntimeError):
    """Raised when the workspace cannot be set up cleanly."""


@dataclass(frozen=True)
class Workspace:
    """A materialized per-cell workspace, ready for overlay + driver."""

    root: Path
    archetype: str
    cell_id: str

    def run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run ``git`` inside the workspace, suppressing local-config noise."""
        return subprocess.run(
            ("git", *args),
            cwd=self.root,
            check=check,
            capture_output=True,
            text=True,
            env={
                "GIT_AUTHOR_NAME": "harness",
                "GIT_AUTHOR_EMAIL": "harness@local",
                "GIT_COMMITTER_NAME": "harness",
                "GIT_COMMITTER_EMAIL": "harness@local",
                "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": str(self.root.parent),
                "PATH": _safe_path(),
            },
        )


def materialize(
    *,
    archetype_dir: Path,
    workspace_root: Path,
    cell_id: str,
) -> Workspace:
    """Copy the archetype into a fresh per-cell workspace and commit it.

    Subsequent overlay application produces a second commit; the agent sees a
    clean tree on top of that.
    """
    if not archetype_dir.is_dir():
        raise WorkspaceError(
            f"Archetype directory does not exist: {archetype_dir}. "
            "Run `python -m harness.adoption sync-fixtures` first."
        )

    workspace = workspace_root / cell_id / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(archetype_dir, workspace, symlinks=False)

    ws = Workspace(root=workspace, archetype=archetype_dir.name, cell_id=cell_id)
    ws.run_git("init", "-q")
    ws.run_git("add", "-A")
    ws.run_git("commit", "-q", "--allow-empty", "-m", "archetype baseline")
    return ws


def commit_overlay(ws: Workspace, message: str = "variant overlay") -> None:
    """Stage and commit overlay-applied changes; tolerates an empty diff."""
    ws.run_git("add", "-A")
    status = ws.run_git("status", "--porcelain")
    if status.stdout.strip():
        ws.run_git("commit", "-q", "-m", message)
    else:
        ws.run_git("commit", "-q", "--allow-empty", "-m", f"{message} (no-op)")


def teardown(ws: Workspace) -> None:
    """Remove the workspace tree. Idempotent."""
    if ws.root.exists():
        shutil.rmtree(ws.root, ignore_errors=True)


def _safe_path() -> str:
    """Minimal PATH for the subprocess; agents may extend their own."""
    import os

    return os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")


__all__ = [
    "Workspace",
    "WorkspaceError",
    "commit_overlay",
    "materialize",
    "teardown",
]
