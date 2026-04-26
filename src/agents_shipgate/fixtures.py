"""Bundled fixture access for agents-shipgate.

Fixtures live under ``samples/`` in the source tree and are bundled into the
wheel as ``agents_shipgate/_fixtures`` via hatch ``force-include``. This module
locates the right path regardless of install mode (editable or wheel) and
exposes the public ``fixture_path`` / ``list_fixtures`` helpers.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

# Files at the top of a fixture directory that mark it as a real fixture.
_FIXTURE_MARKER = "shipgate.yaml"

# Directory names under the fixture root that should never be exposed as
# user-facing fixtures (anti-patterns ship as documentation only; see the
# README in samples/_anti_patterns/).
_HIDDEN_PREFIXES = ("_", ".")


class FixtureNotFoundError(LookupError):
    """Raised when a requested fixture does not exist."""


class FixturesUnavailableError(RuntimeError):
    """Raised when fixtures cannot be located (typically a non-wheel install
    that was repackaged without samples/)."""


def fixtures_root() -> Path:
    """Return the directory that contains all bundled fixtures.

    Tries the wheel-bundled location first (``agents_shipgate/_fixtures``)
    and falls back to a repo-relative ``samples/`` directory for editable
    installs and source checkouts.
    """
    try:
        bundled = files("agents_shipgate") / "_fixtures"
        if bundled.is_dir():
            return Path(str(bundled))
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    # Editable install / source checkout: walk up from this file to repo root.
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / "samples"
        if candidate.is_dir():
            return candidate

    raise FixturesUnavailableError(
        "Fixtures are not available in this install. "
        "Install agents-shipgate from PyPI (which bundles samples) or run "
        "from a source checkout."
    )


def list_fixtures() -> list[dict[str, str]]:
    """Enumerate available fixtures as a list of ``{name, description}``."""
    root = fixtures_root()
    entries: list[dict[str, str]] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        if path.name.startswith(_HIDDEN_PREFIXES):
            continue
        manifest = path / _FIXTURE_MARKER
        if not manifest.is_file():
            continue
        entries.append(
            {
                "name": path.name,
                "description": _short_description(path),
                "path": str(path),
            }
        )
    return entries


def fixture_path(name: str) -> Path:
    """Return the directory of a single fixture by name."""
    root = fixtures_root()
    candidate = root / name
    if not candidate.is_dir() or not (candidate / _FIXTURE_MARKER).is_file():
        raise FixtureNotFoundError(
            f"Fixture {name!r} not found. Run "
            "`agents-shipgate fixture list` to see available fixtures."
        )
    return candidate


def _short_description(path: Path) -> str:
    """Read the first non-empty line of a fixture's README.md (if present)."""
    readme = path / "README.md"
    if not readme.is_file():
        return ""
    for line in readme.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""
