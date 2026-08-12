"""Manifest scope: which directory one ``shipgate.yaml`` should describe.

Two questions share one answer here, so they are answered by one rule.

``verify --preview`` asks it forward: given the paths a pull request
changed, which directory should ``init`` write a manifest into?  Routing
that to the workspace root is wrong on a monorepo — one manifest then
covers every unrelated agent in the repository, and the alignment layer
(``agent.name`` and ``declared_purpose`` versus the observed capability
surface) means nothing when a single declaration covers many agents.

``init`` asks it backwards: the workspace it was handed defines agents in
more than one self-contained project, so no single manifest describes it.
Rather than silently adopting the first agent name it parsed, it refuses
and names the candidate directories.

The shared rule is that a *project marker* file names a self-contained
project root.  Because both readings sit on it, the directory preview
suggests and the directory ``init`` accepts cannot disagree.

Everything here is read-only and lexical-plus-``stat``: it never walks a
tree, so it stays cheap enough for the preview path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agents_shipgate.triggers import paths_without_capability_surface

# Files that mark a self-contained project root, in precedence order. A
# workspace that already carries ``shipgate.yaml`` is the strongest marker
# there is: it is a project someone has already scoped by hand.
#
# Deliberately excluded: ``requirements.txt``, ``setup.cfg``, ``Makefile``,
# and friends. They travel with sub-directories that are not project roots
# (``docs/``, ``tests/``, deployment folders), and picking one of those is
# strictly worse than the repository root — a manifest written there
# describes a directory that holds no agent, and it hides the real one.
PROJECT_MARKERS: tuple[str, ...] = (
    "shipgate.yaml",
    "pyproject.toml",
    "setup.py",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "Gemfile",
)


@dataclass(frozen=True)
class ProjectRoot:
    """A directory that carries a project marker."""

    directory: Path
    marker: str


@dataclass(frozen=True)
class ChangeScope:
    """The one project a diff belongs to. See :func:`resolve_change_scope`."""

    directory: Path
    #: POSIX path of :attr:`directory` relative to the workspace root.
    relative: str
    #: File name of the marker that made :attr:`directory` a project root.
    marker: str


@dataclass(frozen=True)
class ScopeResolution:
    """What the changed paths say about which project they belong to.

    Three answers, and the difference between the last two is the point:

    * ``scope`` set — one project owns the capability-bearing change.
    * ``scope`` unset with ``contested`` populated — several projects do,
      and *which* they are is worth reporting, because "run init at the
      root" is a command that will refuse.
    * both empty — nothing narrows the workspace the caller already named.
    """

    scope: ChangeScope | None = None
    #: Workspace-relative paths of the competing projects, sorted.
    contested: tuple[str, ...] = ()


def repository_root(start: Path) -> Path | None:
    """Nearest enclosing Git checkout root, or ``None``.

    Found by walking up for a ``.git`` entry — a directory in a normal
    checkout, a file in a worktree or submodule — rather than by asking
    git. This module is stat-only by contract (see
    ``tests/test_adapter_static_only.py``), and the question it answers
    does not need git's opinion: GitHub Actions loads workflows from the
    repository root's ``.github/workflows`` and nowhere else, so what
    matters is which directory that is.
    """

    try:
        current = start.resolve()
    except OSError:  # pragma: no cover - unreadable path
        return None
    while True:
        try:
            if (current / ".git").exists():
                return current
        except OSError:  # pragma: no cover - unreadable directory
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent


def project_marker(directory: Path) -> str | None:
    """Name the project marker in ``directory``, if it carries one."""

    for name in PROJECT_MARKERS:
        try:
            if (directory / name).is_file():
                return name
        except OSError:  # pragma: no cover - unreadable directory
            return None
    return None


def find_project_root(directory: Path, *, root: Path) -> ProjectRoot | None:
    """Deepest project root at or above ``directory``, bounded by ``root``.

    ``directory`` and ``root`` must both be resolved, and ``directory``
    must be at or below ``root``; callers that build ``directory`` from
    untrusted path text check containment first.
    """

    current = directory
    while True:
        marker = project_marker(current)
        if marker is not None:
            return ProjectRoot(directory=current, marker=marker)
        if current == root:
            return None
        parent = current.parent
        if parent == current:  # defensive: never climb past a filesystem root
            return None
        current = parent


def resolve_change_scope(
    *,
    root: Path,
    changed_files: Iterable[str],
    limit: Path | None = None,
) -> ScopeResolution:
    """Which project the changed paths belong to.

    Every changed path is attributed to the nearest project root at or
    above it. The scope is that project when exactly one is claimed and
    every capability-bearing path in the change set was claimed by it.

    Two rules decide the rest, and both fail towards the workspace the
    caller already named:

    * **An unclaimed capability path vetoes the answer.** A changed path
      that carries a capability surface and sits under no project root
      cannot be described by a manifest in some sibling directory. Saying
      "this change belongs to `services/b`" while a root `prompts/system.md`
      changed too would be a false statement about the diff, so nothing is
      suggested instead.
    * **Documentation and tests cannot outvote code.** They carry no
      capability surface, so a project claimed *only* by documentation or
      test paths drops out of a contest — the trigger catalog's own
      docs-only rule decides which paths those are, so this cannot drift
      from what ``trigger`` reports. It matters because such paths travel
      with real work: the pull request in #363 edits a `README.md` one
      directory above the project it adds, and counting that README as a
      competing claim would return the answer to the repository root,
      which is the routing the issue is about.

    When several projects survive, the resolution carries them in
    :attr:`ScopeResolution.contested` rather than silently collapsing:
    "run init at the root" is a command that would refuse.

    ``limit`` is the workspace the caller asked about. The suggestion never
    leaves it, so a preview scoped to a sub-directory cannot be answered
    with a directory the caller did not ask about.
    """

    try:
        root_resolved = root.resolve()
    except OSError:  # pragma: no cover - unreadable workspace
        return ScopeResolution()
    try:
        limit_resolved = limit.resolve() if limit is not None else root_resolved
    except OSError:  # pragma: no cover - unreadable workspace
        return ScopeResolution()

    # Paths are used verbatim. A file name may legally begin or end with a
    # space, and trimming one here would attribute the change to a directory
    # that does not exist.
    entries = [entry for entry in changed_files if entry]
    # project directory -> (marker, the changed paths that claimed it)
    claims: dict[Path, tuple[str, list[str]]] = {}
    unclaimed: list[str] = []
    seen: dict[Path, ProjectRoot | None] = {}
    for text in entries:
        path = PurePosixPath(text)
        if path.is_absolute() or ".." in path.parts:
            # Outside anything ``git diff --name-only`` emits. A scope
            # derived from a path this module cannot reason about could name
            # a directory outside the repository, so abandon the answer
            # rather than skip the path and answer from the rest.
            return ScopeResolution()
        directory = root_resolved.joinpath(*path.parts[:-1])
        if directory not in seen:
            seen[directory] = find_project_root(directory, root=root_resolved)
        found = seen[directory]
        if found is None:
            unclaimed.append(text)
            continue
        claims.setdefault(found.directory, (found.marker, []))[1].append(text)

    if len(claims) > 1 or unclaimed:
        # Only a contest — or a path no project owns — needs the docs-only
        # rule evaluated at all, which is what keeps a large single-project
        # diff from paying for it.
        silent = paths_without_capability_surface(
            [text for _marker, texts in claims.values() for text in texts] + unclaimed
        )
        if any(text not in silent for text in unclaimed):
            return ScopeResolution()
        claims = {
            directory: claim
            for directory, claim in claims.items()
            if any(text not in silent for text in claim[1])
        }

    if len(claims) > 1:
        return ScopeResolution(
            contested=tuple(
                sorted(
                    directory.relative_to(root_resolved).as_posix()
                    if directory != root_resolved
                    else "."
                    for directory in claims
                )
            )
        )
    if not claims:
        return ScopeResolution()
    directory, (marker, _texts) = next(iter(claims.items()))
    # A claim on the root — or on the workspace the caller already named —
    # is the scope they have. Say nothing rather than restate it.
    if directory in (root_resolved, limit_resolved):
        return ScopeResolution()
    if not _is_within(directory, limit_resolved):
        return ScopeResolution()
    try:
        if not _is_within(directory.resolve(), root_resolved):
            return ScopeResolution()  # a symlink leading out of the repository
    except OSError:  # pragma: no cover - unreadable directory
        return ScopeResolution()
    return ScopeResolution(
        scope=ChangeScope(
            directory=directory,
            relative=directory.relative_to(root_resolved).as_posix(),
            marker=marker,
        )
    )


def _is_within(candidate: Path, root: Path) -> bool:
    if candidate == root:
        return True
    return root in candidate.parents


__all__ = [
    "PROJECT_MARKERS",
    "ChangeScope",
    "ScopeResolution",
    "ProjectRoot",
    "find_project_root",
    "project_marker",
    "repository_root",
    "resolve_change_scope",
]
