#!/usr/bin/env python3
"""Prove the committed dependency locks still describe the declared requirements.

CI and release verification install a hash-locked closure rather than resolving
``.[dev]`` fresh, so the release runs against the same bytes that approved the
commit. That only holds while the lock and the declarations agree: a dependency
added to ``pyproject.toml`` without recompiling would be installed at whatever
version the lock happens to carry, or not installed at all.

Three failure modes are checked, for every lock in the repository:

*missing*
    A declared requirement has no pin. The environment is not the declared one.
*out of range*
    The pin does not satisfy the declared specifier — the usual shape of "the
    range was widened or bumped and nobody recompiled".
*undeclared direct requirement*
    The lock names a direct requirement the declarations no longer contain.
    ``uv`` records who asked for each pin, so a removed dependency that is still
    being installed is visible rather than merely harmless.

Every pin must also be exact and carry at least one hash, which is what makes
``pip install --require-hashes`` meaningful.

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

_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*(?P<version>[^\s;\\]+)")


@dataclass(frozen=True)
class LockTarget:
    """A committed lock and the declarations it must satisfy."""

    lock: str
    source: str
    extras: tuple[str, ...] = ()
    purpose: str = ""


# `constraints/release-build.txt` is deliberately absent: it is a single
# hand-written backend pin with no transitive closure, and the wheel-byte
# reproducibility argument in its header is the reason it changes, not a
# resolution.
LOCK_TARGETS: tuple[LockTarget, ...] = (
    LockTarget(
        lock="constraints/dev.txt",
        source="pyproject.toml",
        extras=("dev",),
        purpose="the development closure CI and release verification both install",
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


@dataclass
class Pin:
    version: str
    line: int
    hashes: int = 0
    requesters: list[str] = field(default_factory=list)


def parse_lock(lock_path: Path) -> dict[str, Pin]:
    """Read ``name==version`` pins, their hash count, and who requested them."""

    if not lock_path.is_file():
        raise ReleaseError(f"Lock file not found: {lock_path}")

    pins: dict[str, Pin] = {}
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
                f"{lock_path}:{number} is not an exact pin ({raw.strip()!r}); a lock that "
                "resolves at install time is not a lock."
            )
        name = canonicalize_name(match.group("name"))
        if name in pins:
            raise ReleaseError(f"{lock_path}:{number} pins {name} a second time.")
        current = Pin(version=match.group("version"), line=number)
        in_via = False
        pins[name] = current

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


def verify_lock_target(
    target: LockTarget, *, root: Path = REPO_ROOT, distribution: str = "agents-shipgate"
) -> list[str]:
    """Return every disagreement between ``target``'s lock and its declarations."""

    lock_path = root / target.lock
    pins = parse_lock(lock_path)
    requirements = declared_requirements(target, root=root)
    problems: list[str] = []

    unhashed = sorted(name for name, pin in pins.items() if pin.hashes == 0)
    if unhashed:
        problems.append(
            f"{target.lock} pins {', '.join(unhashed)} without a hash; "
            "`pip install --require-hashes` would reject the file."
        )

    declared: set[str] = set()
    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        declared.add(name)
        pin = pins.get(name)
        if pin is None:
            problems.append(
                f"{target.source} declares {requirement} but {target.lock} pins no {name}; "
                "the lock is stale. Regenerate it with scripts/update_locks.py."
            )
            continue
        if requirement.specifier and not requirement.specifier.contains(
            pin.version, prereleases=True
        ):
            problems.append(
                f"{target.lock} pins {name}=={pin.version} (line {pin.line}), which does not "
                f"satisfy the declared {requirement}. Regenerate the lock with "
                "scripts/update_locks.py."
            )

    markers = _direct_marker(target, distribution=distribution)
    for name, pin in sorted(pins.items()):
        if name in declared:
            continue
        if any(requester in markers for requester in pin.requesters):
            problems.append(
                f"{target.lock} carries {name}=={pin.version} (line {pin.line}) as a direct "
                f"requirement, but {target.source} no longer declares it."
            )
    return problems


def verify_all(*, root: Path = REPO_ROOT, targets: tuple[LockTarget, ...] = LOCK_TARGETS) -> None:
    problems: list[str] = []
    for target in targets:
        problems.extend(verify_lock_target(target, root=root))
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
