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
from typing import Literal

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

# Markers that name a project only where the caller has already found agent
# evidence in that same directory. On their own they travel with directories
# that are not project roots, which is why they are not in the set above; with
# an agent beside them they are the whole boundary a plain
# ``requirements.txt`` + ``agent.py`` layout has.
WEAK_PROJECT_MARKERS: tuple[str, ...] = (
    "requirements.txt",
    "requirements.in",
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

    ``status`` is the load-bearing field, because "no scope" is not one
    answer but several, and only one of them means the workspace the
    caller named is the right place to write a manifest:

    * ``resolved`` — one project owns the capability-bearing change.
    * ``not_narrowed`` — nothing in the change set points below the
      workspace. A single-project repository lives here, so this is the
      only state in which initializing that workspace is right.
    * ``contested`` — several projects own part of the change;
      :attr:`contested` names them.
    * ``unclaimed`` — a capability-bearing path belongs to no project
      while another project claimed part of the change. The workspace
      demonstrably holds sub-projects, so it is not itself a scope.
    * ``not_evaluated`` — the evidence could not be read at all.

    The last three are *unresolved*, and unresolved must never be spent as
    permission to initialize the root (#363 review): that turns "Shipgate
    could not tell" into a manifest for whichever agent happens to be in
    the current checkout.
    """

    status: Literal[
        "resolved",
        "not_narrowed",
        "contested",
        "unclaimed",
        "not_evaluated",
    ] = "not_narrowed"
    scope: ChangeScope | None = None
    #: Workspace-relative paths of the competing projects, sorted.
    contested: tuple[str, ...] = ()
    #: Why the scope is unresolved, for the routing surface to quote.
    detail: str = ""

    @property
    def unresolved(self) -> bool:
        """Whether initializing the caller's workspace would be a guess."""

        return self.status in ("contested", "unclaimed", "not_evaluated")


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


def project_marker(
    directory: Path, *, extra: tuple[str, ...] = ()
) -> str | None:
    """Name the project marker in ``directory``, if it carries one.

    A symlink is not a marker. ``Path.is_file()`` follows one, so a
    ``shipgate.yaml`` linked in from outside the repository would satisfy
    this check and preview would emit a scoped ``verify`` command that the
    verifier then rejects: config validation forbids symlink components in
    a manifest path. Refusing here keeps preview from promising a command
    that cannot run (#363 review).

    ``extra`` adds weaker marker names the caller has independent evidence
    for — see :func:`find_project_root`.
    """

    for name in (*PROJECT_MARKERS, *extra):
        candidate = directory / name
        try:
            if candidate.is_symlink():
                continue
            if candidate.is_file():
                return name
        except OSError:  # pragma: no cover - unreadable directory
            return None
    return None


def find_project_root(
    directory: Path,
    *,
    root: Path,
    evidence_dirs: frozenset[Path] = frozenset(),
) -> ProjectRoot | None:
    """Deepest project root at or above ``directory``, bounded by ``root``.

    ``directory`` and ``root`` must both be resolved, and ``directory``
    must be at or below ``root``; callers that build ``directory`` from
    untrusted path text check containment first.

    ``evidence_dirs`` unlocks :data:`WEAK_PROJECT_MARKERS` for exactly the
    directories the caller has already found agent evidence in. A bare
    ``requirements.txt`` is not a project boundary — it travels with
    ``tests/`` and ``docs/`` — but two sibling agents whose only marker is
    ``requirements.txt`` beside ``agent.py`` are two projects, and reading
    them as one root scope lets ``init`` pick one of their names (#363
    review). Callers that cannot establish evidence pass nothing and get
    the strong markers alone.
    """

    current = directory
    while True:
        extra = WEAK_PROJECT_MARKERS if current in evidence_dirs else ()
        marker = project_marker(current, extra=extra)
        if marker is not None:
            return ProjectRoot(directory=current, marker=marker)
        if current == root:
            return None
        parent = current.parent
        if parent == current:  # defensive: never climb past a filesystem root
            return None
        current = parent


def manifest_opt_in(
    workspace: Path,
    *,
    changed_paths: Iterable[str] = (),
    name: str = "shipgate.yaml",
) -> bool:
    """Whether this workspace has opted in to Shipgate.

    A manifest at the workspace root is the historical answer. A monorepo
    keeps one manifest per project, so a change inside an adopted project
    is an opted-in change even though the root carries nothing — reading
    only the root reported `apps/a/README.md` as a docs-only skip while
    `apps/a/shipgate.yaml` sat right beside it (#363 review).

    Only manifests *above the changed paths* count. Scanning the tree for
    any manifest anywhere would make an unrelated project's adoption opt
    in a change that has nothing to do with it.
    """

    try:
        root = workspace.resolve()
    except OSError:  # pragma: no cover - unreadable workspace
        return False
    if (root / name).is_file():
        return True
    for entry in changed_paths:
        if not entry:
            continue
        path = PurePosixPath(entry)
        if path.is_absolute() or ".." in path.parts:
            continue
        current = root.joinpath(*path.parts[:-1])
        while _is_within(current, root):
            if (current / name).is_file():
                return True
            if current == root:
                break
            parent = current.parent
            if parent == current:  # pragma: no cover - defensive
                break
            current = parent
    return False


def resolve_change_scope(
    *,
    root: Path,
    changed_files: Iterable[str],
    limit: Path | None = None,
    evidence_dirs: frozenset[Path] = frozenset(),
) -> ScopeResolution:
    """Which project the changed paths belong to.

    Every changed path is attributed to the nearest project root at or
    above it. The scope is that project when exactly one is claimed and
    every capability-bearing path in the change set was claimed by it.

    The states this can end in are documented on :class:`ScopeResolution`;
    the two that matter here are why it refuses to answer:

    * **An unclaimed capability path vetoes the answer** (``unclaimed``).
      A changed path that carries a capability surface and sits under no
      project root cannot be described by a manifest in some sibling
      directory. Saying "this change belongs to `services/b`" while a root
      `prompts/system.md` changed too would be a false statement about the
      diff.
    * **Documentation and tests cannot outvote code.** They carry no
      capability surface, so a project claimed *only* by documentation or
      test paths drops out of a contest — the trigger catalog's own
      docs-only rule decides which paths those are, so this cannot drift
      from what ``trigger`` reports. It matters because such paths travel
      with real work: the pull request in #363 edits a `README.md` one
      directory above the project it adds, and counting that README as a
      competing claim would return the answer to the repository root.

    Two projects narrow to ``contested`` rather than to their common
    parent: the parent of two projects is not itself a project, and a
    manifest written there would describe both.

    ``limit`` is the workspace the caller asked about. The suggestion never
    leaves it, so a preview scoped to a sub-directory cannot be answered
    with a directory the caller did not ask about.

    ``evidence_dirs`` is passed straight to :func:`find_project_root` and
    is not optional in practice: a project whose entire boundary is a
    ``requirements.txt`` beside an ``agent.py`` is a project this module
    cannot see without it, and the walk then climbs to the repository root
    and reports ``not_narrowed`` — which routing spends on a root ``init``
    that ``init`` refuses (#394). Callers build it with
    ``signals.weak_marker_evidence_dirs``.
    """

    try:
        root_resolved = root.resolve()
    except OSError:  # pragma: no cover - unreadable workspace
        return ScopeResolution(
            status="not_evaluated", detail="the workspace could not be read"
        )
    try:
        limit_resolved = limit.resolve() if limit is not None else root_resolved
    except OSError:  # pragma: no cover - unreadable workspace
        return ScopeResolution(
            status="not_evaluated", detail="the workspace could not be read"
        )

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
            return ScopeResolution(
                status="not_evaluated",
                detail=f"changed path {text!r} is not repository-relative",
            )
        directory = root_resolved.joinpath(*path.parts[:-1])
        if directory not in seen:
            seen[directory] = find_project_root(
                directory, root=root_resolved, evidence_dirs=evidence_dirs
            )
        found = seen[directory]
        if found is None:
            unclaimed.append(text)
            continue
        claims.setdefault(found.directory, (found.marker, []))[1].append(text)

    if len(claims) > 1 or (unclaimed and claims):
        # Only a contest — or a path no project owns alongside one that is
        # owned — needs the docs-only rule evaluated at all, which is what
        # keeps a large single-project diff from paying for it.
        silent = paths_without_capability_surface(
            [text for _marker, texts in claims.values() for text in texts] + unclaimed
        )
        loud_unclaimed = [text for text in unclaimed if text not in silent]
        if loud_unclaimed:
            return ScopeResolution(
                status="unclaimed",
                detail=(
                    f"{len(loud_unclaimed)} changed path(s) carrying a "
                    "capability surface belong to no project in this "
                    f"workspace, starting with {loud_unclaimed[0]!r}"
                ),
            )
        claims = {
            directory: claim
            for directory, claim in claims.items()
            if any(text not in silent for text in claim[1])
        }

    if len(claims) > 1:
        return ScopeResolution(
            status="contested",
            contested=tuple(
                sorted(
                    directory.relative_to(root_resolved).as_posix()
                    if directory != root_resolved
                    else "."
                    for directory in claims
                )
            ),
            detail="the change spans more than one self-contained project",
        )
    if not claims:
        return ScopeResolution(status="not_narrowed")
    directory, (marker, _texts) = next(iter(claims.items()))
    # A claim on the root — or on the workspace the caller already named —
    # is the scope they have. Say nothing rather than restate it.
    if directory in (root_resolved, limit_resolved):
        return ScopeResolution(status="not_narrowed")
    if not _is_within(directory, limit_resolved):
        return ScopeResolution(status="not_narrowed")
    try:
        if not _is_within(directory.resolve(), root_resolved):
            return ScopeResolution(
                status="not_evaluated",
                detail=f"{directory} leaves the repository through a symlink",
            )
    except OSError:  # pragma: no cover - unreadable directory
        return ScopeResolution(
            status="not_evaluated", detail=f"{directory} could not be read"
        )
    if _has_symlinked_component(directory, root=root_resolved):
        # The verifier resolves a manifest path without following symlinks
        # and refuses one whose components are links. Suggesting this
        # directory would promise a command that exits 2.
        return ScopeResolution(
            status="not_evaluated",
            detail=f"{directory} is reached through a symlinked directory",
        )
    return ScopeResolution(
        status="resolved",
        scope=ChangeScope(
            directory=directory,
            relative=directory.relative_to(root_resolved).as_posix(),
            marker=marker,
        ),
    )


def _has_symlinked_component(directory: Path, *, root: Path) -> bool:
    """Whether any directory between ``root`` and ``directory`` is a link."""

    current = directory
    while current != root:
        try:
            if current.is_symlink():
                return True
        except OSError:  # pragma: no cover - unreadable directory
            return True
        parent = current.parent
        if parent == current:  # pragma: no cover - defensive
            return False
        current = parent
    return False


def _is_within(candidate: Path, root: Path) -> bool:
    if candidate == root:
        return True
    return root in candidate.parents


__all__ = [
    "PROJECT_MARKERS",
    "WEAK_PROJECT_MARKERS",
    "ChangeScope",
    "ScopeResolution",
    "ProjectRoot",
    "find_project_root",
    "manifest_opt_in",
    "project_marker",
    "repository_root",
    "resolve_change_scope",
]
