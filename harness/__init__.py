"""Harness families for agents-shipgate.

Each top-level subpackage under ``harness/`` is one harness family — a
focused, local-only piece of developer infrastructure that measures
something about Shipgate's behavior or its adoption. The first such
family is ``harness.adoption``, which drives coding agents across a
matrix of (archetype, variant, prompt) cells and scores their behavior
against the adoption rubric.

This module defines the **harness layout convention** so future
families (perf regression, false-positive baseline, framework-version
drift, etc.) can be added with a shared shape and a shared dispatcher.

## Convention (every family MUST follow)

A subpackage ``harness/<name>/`` is recognized as a harness family iff:

1. ``harness/<name>/__init__.py`` exists with a non-empty docstring.
   The first line of the docstring becomes the family's one-line
   description in ``python -m harness list``.
2. ``harness/<name>/cli.py`` exists and exposes ``app`` — typically a
   ``typer.Typer`` instance, but any zero-arg callable suffices.
3. ``harness/<name>/__main__.py`` exists and calls ``app()`` so that
   ``python -m harness.<name>`` is a working entry point.

Subdirectories under ``harness/<name>/`` (e.g. ``drivers/``,
``observer/``, ``scorer/``) are family-internal. Only top-level
subpackages of ``harness/`` are scanned for the convention.

Harness families are **not packaged** into the ``agents-shipgate``
wheel — they are developer infrastructure only. Shared runtime
dependencies live in ``harness/requirements.txt``; install with
``pip install -r harness/requirements.txt`` from a clone.

## Discovery and dispatch

- ``discover_harnesses()`` walks ``harness/*/`` and returns one
  :class:`HarnessSpec` per conforming family.
- ``python -m harness list`` prints the discovered set (delegates to
  ``discover_harnesses()``).
- ``python -m harness <name> [args...]`` forwards to
  ``python -m harness.<name>`` so a future family is invokable through
  the same dispatcher.
- ``tests/harness/test_harness_layout.py`` pins the convention with a
  parametrized contract test — a new family that misses any of the
  three required files fails the test loudly.

## Adding a new harness family (checklist)

1. Create ``harness/<name>/`` with ``__init__.py``, ``cli.py``,
   ``__main__.py``.
2. Make ``cli.py`` export ``app`` (Typer recommended for argv
   parsing; the existing :mod:`harness.adoption.cli` is the canonical
   template).
3. Make ``__main__.py`` call ``app()`` after bootstrapping
   ``sys.path`` the way :mod:`harness.adoption.__main__` does — so a
   sibling-worktree editable install never wins over the colocated
   ``src/``.
4. Add shared runtime deps to ``harness/requirements.txt``.
5. Drop tests under ``tests/harness/`` (the layout contract test
   picks the new family up automatically).
6. Document any new top-level entry-point flag or score rubric under
   ``docs/`` or the family's own ``README.md``.

See :mod:`harness.adoption` for the canonical example.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["HARNESS_DIR", "HarnessSpec", "discover_harnesses"]

# Filesystem root for harness discovery. Kept as a module-level constant
# so tests can sanity-check the package layout without re-deriving it.
HARNESS_DIR: Path = Path(__file__).resolve().parent

# Subpackages under ``harness/`` that are NOT harness families even if
# they satisfy the cli.py shape. ``tests`` is reserved because pytest
# can pick up a future ``harness/tests/`` directory; underscored names
# are private by convention. Add to this set if you introduce a
# non-family helper subpackage (don't add new public families here).
_EXCLUDED_SUBPACKAGES: frozenset[str] = frozenset({"tests"})


@dataclass(frozen=True)
class HarnessSpec:
    """Discovered metadata for one harness family.

    Attributes:
        name: The subpackage name (e.g. ``"adoption"``). Used as the
            argv selector for ``python -m harness <name>`` and as the
            stable identifier in the contract test.
        description: First line of the family's ``__init__.py``
            docstring. Empty string only if the docstring is itself
            empty, which the contract test rejects.
        app: The entry-point callable from ``harness.<name>.cli``.
            Conforming families expose ``typer.Typer`` instances; the
            convention only requires a callable so a future family
            using a different argv parser remains valid.
        module_path: The dotted module path (e.g.
            ``"harness.adoption"``). ``python -m <module_path>`` works
            via the family's ``__main__.py``.
        package_dir: Absolute filesystem path to the family's package
            directory. Tests and tooling use this to read sibling
            files (README.md, requirements.txt) without re-deriving
            ``HARNESS_DIR``.
    """

    name: str
    description: str
    app: Callable[..., Any]
    module_path: str
    package_dir: Path


def discover_harnesses() -> list[HarnessSpec]:
    """Walk ``harness/`` and return every conforming family.

    A subpackage conforms iff (a) it is not in
    :data:`_EXCLUDED_SUBPACKAGES`, (b) it has a ``cli.py`` module that
    can be imported, and (c) ``cli`` exposes a non-None ``app``
    attribute. Non-conforming directories are silently skipped here —
    the contract test
    (``tests/harness/test_harness_layout.py::test_every_harness_subpackage_conforms``)
    is what FAILS LOUDLY if a subpackage looks like a harness but
    misses a required file.

    Ordering: results are sorted by ``name`` for deterministic
    enumeration in ``python -m harness list`` and parametrized tests.

    Import failures in ``cli.py`` are NOT swallowed — they propagate
    so the developer sees a real traceback instead of an empty list.
    """
    specs: list[HarnessSpec] = []
    for finder_info in pkgutil.iter_modules([str(HARNESS_DIR)]):
        if not finder_info.ispkg:
            continue
        name = finder_info.name
        if name.startswith("_") or name in _EXCLUDED_SUBPACKAGES:
            continue
        package_dir = HARNESS_DIR / name
        cli_path = package_dir / "cli.py"
        if not cli_path.exists():
            continue
        cli_module = importlib.import_module(f"harness.{name}.cli")
        app = getattr(cli_module, "app", None)
        if app is None:
            continue
        init_module = importlib.import_module(f"harness.{name}")
        doc = (init_module.__doc__ or "").strip()
        description = doc.splitlines()[0] if doc else ""
        specs.append(
            HarnessSpec(
                name=name,
                description=description,
                app=app,
                module_path=f"harness.{name}",
                package_dir=package_dir,
            )
        )
    specs.sort(key=lambda spec: spec.name)
    return specs
