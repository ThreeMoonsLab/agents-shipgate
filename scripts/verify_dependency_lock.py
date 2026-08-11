#!/usr/bin/env python3
"""Prove the committed dependency locks still describe the declared requirements.

CI and release verification install a hash-locked closure rather than resolving
``.[dev]`` fresh, so the release runs against the same bytes that approved the
commit. That only holds while the lock and the declarations agree: a dependency
added to ``pyproject.toml`` without recompiling would be installed at whatever
version the lock happens to carry, or not installed at all.

The binding is the **declaration block** each lock carries, written by
``scripts/update_locks.py`` from the requirements it compiled:

    #   declares: pytest<10,>=9.1.1

Comparing normalized PEP 508 strings rather than "name plus specifier" is what
makes the check complete. A name-and-range comparison silently accepts a
declaration that grows an extra (``demo`` -> ``demo[feature]``), moves behind an
environment marker, or switches to a direct URL — each of which changes what
gets installed while every name and every range still matches. Sources and
marker branches are part of the declaration, so they are part of the comparison.

On top of that binding, the pins themselves are checked:

*missing*
    A declared requirement has no pin. The environment is not the declared one.
*out of range*
    The pin does not satisfy the declared specifier — the usual shape of "the
    range was widened or bumped and nobody recompiled".
*undeclared direct requirement*
    The lock names a direct requirement the declarations no longer contain.
    ``uv`` records who asked for each pin, so a removed dependency that is still
    being installed is visible rather than merely harmless.
*unhashed, or not a pin at all*
    ``pip install --require-hashes`` is only meaningful over exact, hashed
    requirements.

A universal lock may legitimately pin one distribution several times under
disjoint markers, so pins are modelled as a marker-qualified list per name and
*every* branch must satisfy the declaration.

Locks installed into the same environment must also agree: the second
``pip install`` would otherwise move the first one's closure underneath it.

This does **not** re-resolve against the index. "Stale" here means *inconsistent
with the declarations*, not *older than the newest release on PyPI*: a check
that recompiled would fail whenever an unrelated project published a version,
turning a supply-chain control into a source of red builds.

Unlike the publication-side scripts this may use ``packaging`` — it runs in the
suite job and in CI, never in a job holding write or OIDC authority.

    python scripts/verify_dependency_lock.py
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

if __package__:
    from scripts._release_support import ReleaseError
else:  # ``python scripts/verify_dependency_lock.py``
    from _release_support import ReleaseError

REPO_ROOT = Path(__file__).resolve().parent.parent

# Everything from this line to the first pin is generated. `update_locks.py`
# restores the prose above it and rewrites the block below it, so the two never
# drift and regenerating never duplicates the block.
DECLARATION_SENTINEL = "# --- generated: the declarations this lock was compiled from ---"
DECLARATION_PREFIX = "#   declares: "

_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:==\s*(?P<version>[^\s;\\]+)|@\s*(?P<url>\S+))"
    r"\s*(?:;\s*(?P<marker>[^\\]*?))?\s*\\?$"
)


@dataclass(frozen=True)
class LockTarget:
    """A committed lock and the declarations it must satisfy."""

    lock: str
    source: str
    extras: tuple[str, ...] = ()
    purpose: str = ""


# `constraints/release-build.txt` is not a lock but a *source*: one
# hand-maintained backend pin, chosen for the wheel-byte reproducibility
# argument recorded in its header, which `constraints/build-backend.txt`
# resolves into a hashed closure.
LOCK_TARGETS: tuple[LockTarget, ...] = (
    LockTarget(
        lock="constraints/dev.txt",
        source="pyproject.toml",
        extras=("dev",),
        purpose="the development closure CI and release verification both install",
    ),
    LockTarget(
        lock="constraints/build-backend.txt",
        source="constraints/release-build.txt",
        purpose="the build backend installed so editable installs need no isolation",
    ),
    LockTarget(
        lock="constraints/release-seal.txt",
        source="constraints/release-seal.in",
        purpose="the sealing job's toolchain",
    ),
    LockTarget(
        lock="constraints/release-publish.txt",
        source="constraints/release-publish.in",
        purpose="the publication jobs' toolchain",
    ),
)

# Locks installed into one environment. A shared distribution pinned at two
# versions means the second `pip install` moves what the first one placed, so
# the closure that was tested is not the closure that ends up installed.
CO_INSTALLED: tuple[tuple[str, ...], ...] = (
    ("constraints/dev.txt", "constraints/build-backend.txt"),
)


@dataclass
class Pin:
    version: str | None
    line: int
    url: str | None = None
    marker: str = ""
    hashes: int = 0
    requesters: list[str] = field(default_factory=list)

    def describe(self) -> str:
        pinned = f"=={self.version}" if self.version else f" @ {self.url}"
        return f"{pinned}{' ; ' + self.marker if self.marker else ''} (line {self.line})"


def normalize_requirement(requirement: Requirement) -> str:
    """A canonical PEP 508 string: name, extras, source, range, and marker.

    Built field by field rather than from ``str(requirement)`` so the
    distribution name is canonicalized (``ruamel.yaml`` and ``ruamel-yaml`` are
    one declaration) and the ordering is stable regardless of how the
    requirement was spelled.
    """

    name = canonicalize_name(requirement.name)
    extras = (
        "[" + ",".join(sorted(canonicalize_name(extra) for extra in requirement.extras)) + "]"
        if requirement.extras
        else ""
    )
    source = f" @ {requirement.url}" if requirement.url else ""
    # `SpecifierSet.__str__` sorts, so two spellings of one range agree.
    specifier = str(requirement.specifier) if requirement.specifier else ""
    marker = f" ; {requirement.marker}" if requirement.marker else ""
    return f"{name}{extras}{source}{specifier}{marker}"


def render_declarations(requirements: list[Requirement]) -> str:
    """The generated block recording what a lock was compiled from."""

    lines = [
        DECLARATION_SENTINEL,
        "#",
        "# Compared against the source by scripts/verify_dependency_lock.py, so a",
        "# changed extra, marker, URL or range cannot slip past without recompiling.",
        "#",
    ]
    lines += [f"{DECLARATION_PREFIX}{text}" for text in sorted_declarations(requirements)]
    lines.append("#")
    return "\n".join(lines) + "\n"


def sorted_declarations(requirements: list[Requirement]) -> list[str]:
    return sorted(normalize_requirement(requirement) for requirement in requirements)


def recorded_declarations(lock_path: Path) -> list[str] | None:
    """What the lock says it was compiled from, or ``None`` if it says nothing."""

    text = lock_path.read_text(encoding="utf-8")
    if DECLARATION_SENTINEL not in text:
        return None
    return sorted(
        line[len(DECLARATION_PREFIX) :].strip()
        for line in text.splitlines()
        if line.startswith(DECLARATION_PREFIX)
    )


def parse_lock(lock_path: Path) -> dict[str, list[Pin]]:
    """Read the pins, their markers, hash counts, and who requested them.

    Returns a list per distribution: ``uv pip compile --universal`` may pin one
    name several times under disjoint markers, and each of those branches is a
    version some environment will actually install.
    """

    if not lock_path.is_file():
        raise ReleaseError(f"Lock file not found: {lock_path}")

    pins: dict[str, list[Pin]] = {}
    current: Pin | None = None
    in_via = False
    for number, raw in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        if raw[:1].isspace():
            body = raw.strip()
            if current is None:
                continue
            if body.startswith("--hash="):
                current.hashes += 1
            elif body.startswith("#"):
                comment = body.lstrip("#").strip()
                if comment == "via" or comment.startswith("via "):
                    in_via = True
                    requester = comment[3:].strip()
                    if requester:
                        current.requesters.append(requester)
                elif in_via:
                    current.requesters.append(comment)
            continue
        if raw.startswith("#"):
            continue
        match = _PIN.match(raw)
        if not match:
            raise ReleaseError(
                f"{lock_path}:{number} is neither an exact pin nor a direct URL "
                f"({raw.strip()!r}); a lock that resolves at install time is not a lock."
            )
        name = canonicalize_name(match.group("name"))
        marker = (match.group("marker") or "").strip()
        branches = pins.setdefault(name, [])
        if any(existing.marker == marker for existing in branches):
            raise ReleaseError(
                f"{lock_path}:{number} pins {name} twice under the same marker "
                f"({marker or 'no marker'}); pip would install whichever came last."
            )
        current = Pin(
            version=match.group("version"),
            url=match.group("url"),
            marker=marker,
            line=number,
        )
        in_via = False
        branches.append(current)

    if not pins:
        raise ReleaseError(f"{lock_path} contains no pins.")
    return pins


def declared_requirements(target: LockTarget, *, root: Path = REPO_ROOT) -> list[Requirement]:
    """The direct requirements the lock is compiled from."""

    source = root / target.source
    if not source.is_file():
        raise ReleaseError(f"Lock source not found: {source}")

    if source.suffix == ".toml":
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        project = data.get("project", {})
        specs = list(project.get("dependencies", []))
        optional = project.get("optional-dependencies", {})
        for extra in target.extras:
            if extra not in optional:
                raise ReleaseError(f"{source} declares no '{extra}' extra.")
            specs.extend(optional[extra])
    else:
        specs = [
            line.split("#", 1)[0].strip()
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    requirements = []
    for spec in specs:
        try:
            requirements.append(Requirement(spec))
        except InvalidRequirement as exc:
            raise ReleaseError(
                f"{source} declares an unparsable requirement {spec!r}: {exc}"
            ) from exc
    return requirements


def _direct_marker(target: LockTarget, *, distribution: str) -> tuple[str, ...]:
    """How ``uv`` spells "this pin was asked for directly" in a ``# via`` block."""

    if target.source.endswith(".toml"):
        return (f"{distribution} ({target.source})",)
    return (f"-r {target.source}",)


def _declaration_problems(target: LockTarget, lock_path: Path, expected: list[str]) -> list[str]:
    recorded = recorded_declarations(lock_path)
    if recorded is None:
        return [
            f"{target.lock} records no declaration block; regenerate it with "
            "scripts/update_locks.py so what it was compiled from is reviewable and checkable."
        ]
    if recorded == expected:
        return []
    added = [text for text in expected if text not in recorded]
    removed = [text for text in recorded if text not in expected]
    detail = "; ".join(
        part
        for part in (
            f"{target.source} now declares {', '.join(added)}" if added else "",
            f"{target.lock} was compiled with {', '.join(removed)}" if removed else "",
        )
        if part
    )
    return [
        f"{target.lock} was not compiled from the current declarations ({detail}). "
        "Regenerate it with scripts/update_locks.py."
    ]


def verify_lock_target(
    target: LockTarget, *, root: Path = REPO_ROOT, distribution: str = "agents-shipgate"
) -> list[str]:
    """Return every disagreement between ``target``'s lock and its declarations."""

    lock_path = root / target.lock
    pins = parse_lock(lock_path)
    requirements = declared_requirements(target, root=root)
    problems = _declaration_problems(target, lock_path, sorted_declarations(requirements))

    unhashed = sorted(
        f"{name}{branch.describe()}"
        for name, branches in pins.items()
        for branch in branches
        if branch.hashes == 0
    )
    if unhashed:
        problems.append(
            f"{target.lock} pins {', '.join(unhashed)} without a hash; "
            "`pip install --require-hashes` would reject the file."
        )

    declared: set[str] = set()
    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        declared.add(name)
        branches = pins.get(name)
        if not branches:
            if requirement.marker is not None:
                # A declaration that no supported environment selects is
                # legitimately absent from the resolution. The declaration
                # block above still binds it, so a change cannot hide here.
                continue
            problems.append(
                f"{target.source} declares {requirement} but {target.lock} pins no {name}; "
                "the lock is stale. Regenerate it with scripts/update_locks.py."
            )
            continue
        for branch in branches:
            if branch.version is None:
                if requirement.specifier:
                    problems.append(
                        f"{target.lock} resolves {name}{branch.describe()} to a direct URL, "
                        f"but {target.source} declares the range {requirement}."
                    )
                continue
            if requirement.specifier and not requirement.specifier.contains(
                branch.version, prereleases=True
            ):
                problems.append(
                    f"{target.lock} pins {name}{branch.describe()}, which does not satisfy "
                    f"the declared {requirement}. Regenerate the lock with "
                    "scripts/update_locks.py."
                )

    markers = _direct_marker(target, distribution=distribution)
    for name, branches in sorted(pins.items()):
        if name in declared:
            continue
        for branch in branches:
            if any(requester in markers for requester in branch.requesters):
                problems.append(
                    f"{target.lock} carries {name}{branch.describe()} as a direct "
                    f"requirement, but {target.source} no longer declares it."
                )
    return problems


def co_installed_problems(
    *,
    root: Path = REPO_ROOT,
    targets: tuple[LockTarget, ...] = LOCK_TARGETS,
    groups: tuple[tuple[str, ...], ...] = CO_INSTALLED,
) -> list[str]:
    """Locks installed side by side must not pin one distribution twice over."""

    known = {target.lock for target in targets}
    problems: list[str] = []
    for group in groups:
        missing = [lock for lock in group if lock not in known]
        if missing:
            problems.append(f"Co-installed group names unknown locks: {', '.join(missing)}.")
            continue
        versions: dict[str, dict[str, set[str]]] = {}
        for lock in group:
            for name, branches in parse_lock(root / lock).items():
                for branch in branches:
                    if branch.version is not None:
                        versions.setdefault(name, {}).setdefault(branch.version, set()).add(lock)
        for name, by_version in sorted(versions.items()):
            if len(by_version) > 1:
                spread = "; ".join(
                    f"{version} in {', '.join(sorted(locks))}"
                    for version, locks in sorted(by_version.items())
                )
                problems.append(
                    f"{name} is pinned at different versions by locks installed together "
                    f"({spread}); the later install would move the earlier closure."
                )
    return problems


def verify_all(*, root: Path = REPO_ROOT, targets: tuple[LockTarget, ...] = LOCK_TARGETS) -> None:
    problems: list[str] = []
    for target in targets:
        problems.extend(verify_lock_target(target, root=root))
    problems.extend(co_installed_problems(root=root, targets=targets))
    if problems:
        raise ReleaseError("\n  - ".join(["Dependency locks are out of date:", *problems]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    try:
        verify_all(root=args.root)
        for target in LOCK_TARGETS:
            pins = parse_lock(args.root / target.lock)
            sys.stdout.write(
                f"OK: {target.lock} pins {len(pins)} distributions for {target.purpose}.\n"
            )
    except (ReleaseError, OSError, ValueError) as exc:
        sys.stderr.write(f"Dependency lock error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
