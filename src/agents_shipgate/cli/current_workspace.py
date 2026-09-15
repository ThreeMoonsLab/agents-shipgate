"""Resolve the repository as it stands right now, for the control protocol.

Its own module rather than a helper inside ``verify/git.py`` for two reasons.
It is shared by every entry point that returns control authority — ``verify
--format control`` and ``agents-shipgate agent control`` — and two entry points
into one decision must apply one currency test; when they did not, ``verify
--format control`` reported ``complete`` with ``permissions.merge=true`` on a
workspace ``agent control`` was simultaneously refusing as ``workspace_changed``.
And ``verify/git.py`` carries line-pinned static-analysis allowlists for its
subprocess surfaces, so inserting unrelated code there churns a security pin.

Where a workspace's reports live by default is defined here for the same
reason: ``verify`` writes there and ``agent control`` reads there, and when the
two resolved that default differently a valid run read as ``missing`` (#575).
"""

from __future__ import annotations

import os
from pathlib import Path

from agents_shipgate.core.current_control import LiveWorkspace

#: The reports directory name used when no output directory is named — the
#: published ``DEFAULT_PATHS["reports_dir"]``, spelled here rather than imported
#: so this leaf does not load every schema the contract module does.
DEFAULT_REPORTS_DIR = Path("agents-shipgate-reports")


def default_reports_dir(workspace: Path) -> Path:
    """Where ``verify --workspace <workspace>`` publishes when ``--out`` is omitted.

    Beneath the requested workspace — not the Git root, and not the invoking
    directory. This is the one definition for both sides of the control loop:
    ``verify`` writes its default output here and ``agent control`` reads its
    default here. When the reader resolved the same relative name against the
    caller's current directory instead, a run published under
    ``<repo>/agents-shipgate-reports`` read as ``missing`` from anywhere else,
    and a caller standing in another verified repository had that repository's
    pointer checked against this one (#575).

    The final component is deliberately left unresolved. The reader opens the
    directory without following a symlink, and resolving it here would quietly
    follow one that read refuses; ``verify`` resolves the result itself.
    """

    return workspace.resolve() / DEFAULT_REPORTS_DIR


def live_workspace(workspace: Path, reports_dir: Path) -> LiveWorkspace | None:
    """Resolve the repository as it stands now, or ``None`` outside Git.

    Shared by every entry point that returns control authority — `verify
    --format control` and `agents-shipgate agent control` — because two entry
    points into one decision must apply one currency test. When they did not,
    `verify --format control` reported `complete` with `permissions.merge=true`
    on a workspace `agent control` was simultaneously refusing as
    `workspace_changed`.

    ``None`` is not "no drift" — it means the comparison could not be made, and
    the reader refuses completion authority on that basis rather than assuming
    the pointer still holds.

    The reports directory is excluded from the change set for the same reason
    ``verify`` excludes it when building the plan: the run's own output is not
    part of the change it evaluated, and including it here would make every
    refresh disagree with the decision it is checking.
    """

    # Imported here, not at module scope: `agents_shipgate.cli.verify.__init__`
    # imports `command`, which imports this module, so a top-level import of
    # anything under that package is a cycle. The Git helpers are the CLI
    # layer's, and this is the only place they are needed.
    from agents_shipgate.cli.verify.git import (
        commit_sha,
        ensure_git_workspace,
        repository_identity,
        tree_sha,
        working_tree_context,
    )

    try:
        root = ensure_git_workspace(workspace.resolve())
        try:
            changed, _ = working_tree_context(
                root, exclude=worktree_exclusion(root, reports_dir)
            )
            changed_paths: tuple[str, ...] | None = tuple(changed)
        except Exception:  # noqa: BLE001 - an unreadable worktree is "unverified".
            changed_paths = None
        return LiveWorkspace(
            root=root,
            repository=repository_identity(root),
            head_commit_sha=commit_sha(root, "HEAD"),
            head_tree_sha=tree_sha(root, "HEAD"),
            changed_paths=changed_paths,
            resolve_commit=lambda ref: _safe_commit_sha(root, ref),
            resolve_merge_base=lambda base, head: _safe_merge_base(root, base, head),
        )
    except Exception:  # noqa: BLE001 - an unresolvable workspace is "unverified".
        return None


def worktree_exclusion(root: Path, reports_dir: Path) -> Path | None:
    """The reports directory as a Git change-set exclusion, or ``None``.

    The one rule for every place that hands an output directory to the Git
    worktree readers as ``exclude=``: the writing run (``verify``, ``verify
    --preview``, the pointer's worktree overlay, the manifest-free host
    comparison) and the refresh that checks it (``live_workspace``). Two
    spellings of it would let the writer and the reader disagree about which
    paths make up the change set.

    Excluding the directory only matters where Git could report it: beneath the
    repository. A directory wholly outside the checkout never appears in its
    change set, so there is nothing to exclude — and the Git helpers refuse it
    as an exclusion ("must remain inside workspace"). Every caller swallowed
    that refusal: plain ``verify`` recorded no input census and exited 2,
    ``--preview`` bound no worktree overlay and then read an untracked file as
    a change it never saw, the host comparison went ``incomparable``, and the
    refresh could not observe the workspace (#575, #785).

    Only the disjoint case changes. A directory inside the repository, the root
    itself, or one of its ancestors — under either the spelling given or the
    resolved one — is returned unchanged, so an in-repository exclusion applies
    exactly as it did, and the root or a parent still reaches the helpers'
    refusal and still withholds authority. Dropping an exclusion can only
    surface more changed paths, never hide one.
    """

    roots = {_absolute(root), root.resolve()}
    for spelling in {_absolute(reports_dir), reports_dir.resolve()}:
        if any(
            spelling.is_relative_to(candidate) or candidate.is_relative_to(spelling)
            for candidate in roots
        ):
            return reports_dir
    return None


def _absolute(path: Path) -> Path:
    """The lexical absolute spelling: no symlink followed, ``..`` folded."""

    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _safe_commit_sha(root: Path, ref: str) -> str | None:
    """Resolve a ref recorded in a pointer; ``None`` when it no longer exists.

    A base ref that has been deleted is drift, not a crash — and it is drift the
    caller must see, so a failure here resolves to ``None`` and compares unequal
    rather than propagating.
    """

    from agents_shipgate.cli.verify.git import commit_sha

    try:
        return commit_sha(root, ref)
    except Exception:  # noqa: BLE001 - an unresolvable ref is drift.
        return None


def _safe_merge_base(root: Path, base: str, head: str) -> str | None:
    from agents_shipgate.cli.verify.git import merge_base_sha

    try:
        return merge_base_sha(root, base, head)
    except Exception:  # noqa: BLE001 - an unresolvable range is drift.
        return None


__all__ = [
    "DEFAULT_REPORTS_DIR",
    "default_reports_dir",
    "live_workspace",
    "worktree_exclusion",
]
