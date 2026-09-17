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
So is which output directories may be left out of the change set at all: the
run that writes into one and every refresh that reads from one must refuse the
same directories, or a pointer ``verify`` would no longer publish still reads
as current (#804).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from agents_shipgate.cli._artifact_lifecycle import (
    REPORTS_DIRECTORY_ARTIFACT_NAMES,
    REPORTS_DIRECTORY_ARTIFACT_SUBDIRECTORIES,
)
from agents_shipgate.core.current_control import LiveWorkspace
from agents_shipgate.core.errors import ConfigError

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

    That exclusion is sound only while the directory holds nothing but Shipgate
    artifacts, so the directory is classified first, by the rule ``verify``
    refuses an output directory by (:func:`output_directory_refusal`). A
    directory holding repository content comes back as a workspace carrying
    that refusal, which denies every pointer read from it, whatever its state
    (#804). The classification cannot raise, and it is returned before any Git
    read that can: an exception must not turn it into ``None``, because
    ``None`` withholds completion only and returns every other pointer state
    without the currency checks.
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
    except Exception:  # noqa: BLE001 - an unresolvable workspace is "unverified".
        return None
    refusal = output_directory_refusal(root, reports_dir)
    if refusal is not None:
        return LiveWorkspace(root=root, reports_dir_refusal=refusal)

    def refusal_against(commit: str) -> str | None:
        # The pointer names the merge base its run diffed against, which this
        # observation cannot know before the pointer is read.
        return output_directory_refusal(root, reports_dir, compared=(commit,))

    try:
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
            reports_dir_refusal_against=refusal_against,
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

    Containment is decided by *physical identity*, never by how the directory
    was spelled. The answer is a property of the directory, so the writer's
    already-resolved ``--out`` and the refresh's current-directory-anchored
    ``--reports-dir`` reach the same one for the same place. Deciding it on
    either spelling let them disagree: an in-repository symlink pointing
    outside kept its exclusion for the reader and lost it for the writer, the
    Git helper refused it, ``live_workspace`` swallowed the refusal, and a
    ``review_publishable`` pointer stayed current across tracked and untracked
    edits (#575 review cycle 2).

    The walk compares each ancestor's ``(st_dev, st_ino)`` with the repository
    root's rather than comparing names, so a spelling that differs only in case
    on a case-insensitive filesystem (``<parent>/REPO/rpt`` for ``repo``) is the
    directory it physically is. Inside the repository, the returned path is
    respelled beneath the root, which is the one spelling the Git helpers can
    turn into a pathspec, and every component that exists is spelled as its
    directory entry is stored: Git matches a pathspec byte for byte, so
    ``<repo>/DOCS`` names ``docs`` to the filesystem but nothing to Git (#804).

    Only the disjoint case drops its exclusion, and "disjoint" is decided after
    resolution: an outside spelling that resolves into the repository keeps its
    exclusion, and an in-repository spelling that resolves outside — a symlink
    such as ``repo/lnkdir`` — loses it, because the directory Git would have to
    exclude is not in the tree Git is reading. A directory genuinely inside the
    repository keeps its exclusion, so an in-repository exclusion applies
    exactly as it did, and the root itself or one of its ancestors is still
    handed over, still refused by the helpers, and still withholds authority.
    Dropping an exclusion can only surface more changed paths, never hide one.
    """

    root_physical = root.resolve()
    target = reports_dir.resolve()
    beneath = _beneath_repository(root_physical, target)
    if beneath is not None:
        return beneath
    if _contains_repository(root_physical, target):
        # The root's own ancestor: handed over so the Git helpers refuse it,
        # exactly as before. Nothing may exclude the tree it is reading.
        return reports_dir
    return None


class OutputDirectoryHoldsRepositoryContent(ConfigError):
    """``verify`` refused an output directory it would have to hide content in.

    A ``ConfigError``, so it keeps the existing ``config_error`` kind and exit
    ``2``; its own type lets the command route the recovery to choosing another
    directory rather than to editing the manifest.
    """

    def __init__(self, message: str, *, directory: Path, refusal: str) -> None:
        super().__init__(message)
        self.directory = directory
        self.refusal = refusal


def output_directory_refusal(
    root: Path, reports_dir: Path, *, compared: Sequence[str] = ()
) -> str | None:
    """Why ``reports_dir`` may not be left out of ``root``'s change set, or ``None``.

    A run leaves its output directory out of the change set it decides on, and
    a refresh leaves the reports directory it reads out of the change set it
    checks, so that the run's own reports never count as part of the change.
    That is sound only while the directory holds nothing else Git would report.
    ``verify --out docs`` left a tracked ``docs/`` out of the decision and out
    of every later currency check; ``--out .claude`` did the same to a widened
    ``.claude/settings.json``, which turned a high-risk permission expansion
    into ``complete`` and ``mergeable`` (#804).

    The directory may be left out when doing so can hide nothing:

    * it lies outside the repository, where Git reports nothing anyway;
    * Git holds nothing beneath it: it is gitignored, empty, or not there yet;
    * everything Git holds beneath it is a Shipgate artifact that is not
      committed — staged or untracked, directly beneath it and named in
      :data:`REPORTS_DIRECTORY_ARTIFACT_NAMES`, or a file under
      :data:`REPORTS_DIRECTORY_ARTIFACT_SUBDIRECTORIES`.

    Anything committed beneath it at ``HEAD``, anything a ``compared`` commit
    (the merge base a run diffs against) held there that ``HEAD`` no longer
    does, and any staged or unignored untracked path that is not an artifact
    is repository content. Committed content counts whatever its name: the
    run would overwrite or unlink a committed file there, and hide that edit.
    A removal counts because a change set taken against the merge base would
    report the deletion, and the exclusion hides it: ``git rm`` of a
    ``.claude/settings.json`` holding deny rules left no directory to find
    content in, and still read as ``complete``. So is the repository root
    or an ancestor of it. The returned text names what was found, and completes
    a sentence whose subject is the directory.

    Staged artifacts are allowed because generated reports that were
    ``git add``-ed by mistake are an advisory case ``verify`` warns about, not
    content any decision reads.

    Containment comes from :func:`worktree_exclusion`, so the directory is
    judged by its physical identity: a symlink into the repository, or a
    spelling that differs only in case, is classified as the directory it is,
    and the answer is the same for the writer's resolved ``--out`` and the
    reader's ``--reports-dir``. The inventory is Git's own, read with the
    exclude rules the change-set inventory uses, so it lists exactly what
    leaving the directory out would hide.

    Never raises. A directory whose content could not be listed has not been
    shown to be safe to leave out, and the answer says so.
    """

    try:
        excluded = worktree_exclusion(root, reports_dir)
        if excluded is None:
            return None
        try:
            relative = excluded.relative_to(root.resolve()).as_posix()
        except ValueError:
            relative = "."
        if relative == ".":
            return "is the repository root or one of its ancestors"
        from agents_shipgate.cli.verify.git import output_directory_inventory

        inventory = output_directory_inventory(root, relative, compared=compared)
    except Exception as exc:  # noqa: BLE001 - an unlisted directory is not shown safe.
        return f"could not be shown to hold only Shipgate artifacts ({exc})"
    tracked = [
        *inventory.head,
        *(path for path in inventory.staged if not _is_reports_artifact(relative, path)),
    ]
    if tracked:
        return f"holds tracked repository files ({_examples(tracked)})"
    if inventory.removed:
        return (
            "held committed repository files that the change being verified "
            f"removes ({_examples(list(inventory.removed))})"
        )
    foreign = [
        path for path in inventory.untracked if not _is_reports_artifact(relative, path)
    ]
    if foreign:
        return (
            "holds untracked files that Git does not ignore and that are not "
            f"Shipgate artifacts ({_examples(foreign)})"
        )
    return None


def _is_reports_artifact(directory: str, path: str) -> bool:
    """Whether Git's ``path`` is an artifact a Shipgate run writes into ``directory``."""

    prefix = f"{directory}/"
    if not path.startswith(prefix):
        return False
    name, separator, below = path[len(prefix) :].partition("/")
    if separator:
        return bool(below) and name in REPORTS_DIRECTORY_ARTIFACT_SUBDIRECTORIES
    if name in REPORTS_DIRECTORY_ARTIFACT_NAMES:
        return True
    # The pointer is published through a same-directory temporary file, which
    # a concurrent reader can observe for the instant before it is renamed.
    return name.startswith(".current-control.json.") and name.endswith(".tmp")


def _examples(paths: list[str], *, limit: int = 3) -> str:
    shown = sorted(paths)[:limit]
    rest = len(paths) - len(shown)
    return ", ".join(shown) + (f", and {rest} more" if rest else "")


def _identity(path: Path) -> tuple[int, int] | None:
    """The physical file this path names, or ``None`` when nothing is there."""

    try:
        status = path.stat()
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


def _beneath_repository(root: Path, target: Path) -> Path | None:
    """``target`` respelled under ``root``, or ``None`` when it is not inside.

    Walking up from the target and comparing physical identity answers the
    question a lexical prefix test only approximates: the root reached through
    a different case, a symlinked parent, or a mount alias is still the root,
    while ``repo-reports`` never is, because the comparison is per directory and
    not per character. A component that does not exist yet — the usual case for
    a reports directory at write time — simply does not match, and the walk
    carries its name through to the nearest ancestor that does.
    """

    root_identity = _identity(root)
    if root_identity is None:
        return None
    trailing: list[str] = []
    node = target
    while True:
        if _identity(node) == root_identity:
            return _stored_spelling(root, reversed(trailing))
        parent = node.parent
        if parent == node:
            return None
        trailing.append(node.name)
        node = parent


def _stored_spelling(root: Path, names: Iterable[str]) -> Path:
    """``names`` beneath ``root``, each existing component spelled as stored.

    Where the filesystem folds case, ``docs`` and ``DOCS`` open the same
    directory, but a Git pathspec is matched byte for byte: excluding ``DOCS``
    excludes nothing, and listing ``DOCS`` lists nothing, from a tree that
    stores ``docs``. Each component that exists is therefore respelled to the
    directory entry with its physical identity. The first component that does
    not exist, and everything below it, keeps the caller's spelling — the one
    it will be created with.
    """

    path = root
    remaining = list(names)
    while remaining:
        name = remaining.pop(0)
        identity = _identity(path / name)
        if identity is None:
            return path.joinpath(name, *remaining)
        path = path / _stored_name(path, name, identity)
    return path


def _stored_name(parent: Path, name: str, identity: tuple[int, int]) -> str:
    """The entry of ``parent`` that physically is ``parent / name``."""

    try:
        with os.scandir(parent) as scan:
            entries = [entry.name for entry in scan]
    except OSError:
        return name
    if name in entries:
        return name
    for candidate in entries:
        try:
            status = os.stat(parent / candidate, follow_symlinks=False)
        except OSError:
            continue
        if (status.st_dev, status.st_ino) == identity:
            return candidate
    return name


def _contains_repository(root: Path, target: Path) -> bool:
    """``target`` is a strict ancestor of ``root`` — the case Git must refuse."""

    target_identity = _identity(target)
    if target_identity is None:
        return False
    node = root.parent
    while True:
        if _identity(node) == target_identity:
            return True
        parent = node.parent
        if parent == node:
            return False
        node = parent


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
    "OutputDirectoryHoldsRepositoryContent",
    "default_reports_dir",
    "live_workspace",
    "output_directory_refusal",
    "worktree_exclusion",
]
