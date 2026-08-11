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
) -> ChangeScope | None:
    """The one project the changed paths belong to, or ``None``.

    Each changed path is attributed to the nearest project root at or above
    it, and the scope is that project when exactly one is claimed this way.
    Paths no project claims are simply silent: a repository-level file
    travelling with a change does not erase the answer its companions give.

    Documentation and tests cannot outvote code. When more than one project
    is claimed, the projects claimed *only* by documentation or test paths
    drop out — the trigger catalog's own docs-only rule decides which paths
    those are, so this cannot drift from what ``trigger`` reports. It
    matters because such paths travel with real work: the pull request in
    #363 edits a `README.md` one directory above the project it adds, and
    counting that README as a competing claim would return the answer to
    the repository root, which is the routing the issue is about.

    Returns ``None`` — meaning "no better answer than the workspace you
    already named" — for an empty change set, for a change no project
    claims, for a single claim that is ``root`` (or ``limit``) itself, and
    for paths that still span two projects after documentation drops out.
    Two projects deliberately narrow to nothing rather than to their common
    parent: the parent of two projects is not itself a project, and a
    manifest written there would describe both.

    ``limit`` is the workspace the caller asked about. The suggestion never
    leaves it, so a preview scoped to a sub-directory cannot be answered
    with a directory the caller did not ask about.
    """

    try:
        root_resolved = root.resolve()
    except OSError:  # pragma: no cover - unreadable workspace
        return None
    try:
        limit_resolved = limit.resolve() if limit is not None else root_resolved
    except OSError:  # pragma: no cover - unreadable workspace
        return None

    # project directory -> (marker, the changed paths that claimed it)
    claims: dict[Path, tuple[str, list[str]]] = {}
    seen: dict[Path, ProjectRoot | None] = {}
    for entry in changed_files:
        text = (entry or "").strip()
        if not text:
            continue
        path = PurePosixPath(text)
        if path.is_absolute() or ".." in path.parts:
            # Outside anything ``git diff --name-only`` emits. A scope
            # derived from a path this module cannot reason about could name
            # a directory outside the repository, so abandon the answer
            # rather than skip the path and answer from the rest.
            return None
        directory = root_resolved.joinpath(*path.parts[:-1])
        if directory not in seen:
            seen[directory] = find_project_root(directory, root=root_resolved)
        found = seen[directory]
        if found is None:
            continue  # no project claims this path
        claims.setdefault(found.directory, (found.marker, []))[1].append(text)

    if len(claims) > 1:
        # Only a contest needs the docs-only rule evaluated at all, which is
        # also what keeps a large diff cheap: the ordinary single-project
        # change never pays for it.
        silent = paths_without_capability_surface(
            [text for _marker, texts in claims.values() for text in texts]
        )
        claims = {
            directory: claim
            for directory, claim in claims.items()
            if any(text not in silent for text in claim[1])
        }

    if len(claims) != 1:
        return None
    directory, (marker, _texts) = next(iter(claims.items()))
    # A claim on the root — or on the workspace the caller already named —
    # is the scope they have. Say nothing rather than restate it.
    if directory in (root_resolved, limit_resolved):
        return None
    if not _is_within(directory, limit_resolved):
        return None
    try:
        if not _is_within(directory.resolve(), root_resolved):
            return None  # a symlinked directory leading out of the repository
    except OSError:  # pragma: no cover - unreadable directory
        return None
    return ChangeScope(
        directory=directory,
        relative=directory.relative_to(root_resolved).as_posix(),
        marker=marker,
    )


def _is_within(candidate: Path, root: Path) -> bool:
    if candidate == root:
        return True
    return root in candidate.parents


__all__ = [
    "PROJECT_MARKERS",
    "ChangeScope",
    "ProjectRoot",
    "find_project_root",
    "project_marker",
    "resolve_change_scope",
]
