"""YAML-driven loader for the built-in check metadata catalog.

``CHECK_METADATA`` is sourced from per-category YAML files under
``docs/checks/``. Each file is keyed by category (the filename stem);
each row carries the ``CheckMetadata`` fields except ``category``
(injected from the filename) and ``docs_url`` (auto-derived from the
canonical base URL + check id).

The loader runs once at registry import time and returns a list
sorted by check id. Malformed YAML raises ``ValueError`` at startup
with the file path and offending check id — the catalog is a wire-
stable contract (``docs/checks.json``), so schema deviations must
fail fast rather than at scan time.

**Editing the catalog is now a YAML edit.** No Python tooling change
is required to add or modify a check. After edits, regenerate
``docs/checks.json`` with::

    python scripts/generate_schemas.py

The check **callable** (``checks/<category>.py:run``) still needs a
matching Python implementation and an entry in
``BUILTIN_CHECKS``/``project.scripts`` of ``pyproject.toml`` for
plugin checks. The YAML covers only metadata (id, severity,
description, narrative, remediation policy) — not behavior.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agents_shipgate.schemas.checks import CheckMetadata

# Stable base for per-check docs URLs. The H3 anchor convention
# (``### SHIP-...``) is GitHub-lowercased — match it here so the
# generated ``docs_url`` resolves correctly.
DOCS_URL_BASE = (
    "https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/docs/checks.md"
)


def _docs_url(check_id: str) -> str:
    return f"{DOCS_URL_BASE}#{check_id.lower()}"


def _resolve_checks_dir() -> Path:
    """Return the directory containing the per-category YAML files.

    Two install modes are supported, both resolved deterministically
    from ``__file__`` without ``importlib.resources``:

    1. **Wheel** — ``pyproject.toml`` force-includes ``docs/checks/``
       into the wheel under ``agents_shipgate/_meta/checks/``. This
       location is checked first so an installed package never falls
       through to a stale repo checkout that happens to be on
       ``sys.path``.
    2. **Editable install / source checkout** — files at
       ``<repo>/docs/checks/``, found relative to this module file.

    Raises ``RuntimeError`` with both attempted paths so a packaging
    regression is debuggable from the traceback alone.
    """
    pkg_root = Path(__file__).resolve().parent.parent  # .../agents_shipgate/
    packaged = pkg_root / "_meta" / "checks"
    if packaged.is_dir():
        return packaged
    repo_root = pkg_root.parent.parent  # .../<repo>/
    dev = repo_root / "docs" / "checks"
    if dev.is_dir():
        return dev
    raise RuntimeError(
        "Cannot locate check metadata YAMLs. Tried packaged "
        f"({packaged}) and dev ({dev}). Reinstall the wheel or run "
        "from a repo checkout that includes docs/checks/."
    )


def _load_yaml_file(path: Path) -> list[dict[str, Any]]:
    """Parse one per-category YAML file into a list of row dicts.

    Empty files (only header comments) are tolerated and yield ``[]``.
    Malformed top-level shapes raise ``ValueError`` immediately.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, dict) or "checks" not in raw:
        raise ValueError(
            f"{path.name}: top-level YAML must be a mapping with a "
            f"'checks' key (got: {type(raw).__name__})"
        )
    checks = raw["checks"]
    if not isinstance(checks, list):
        raise ValueError(
            f"{path.name}: 'checks' must be a list "
            f"(got: {type(checks).__name__})"
        )
    return checks


def load_check_metadata() -> list[CheckMetadata]:
    """Load the built-in CHECK_METADATA catalog from per-category YAML.

    Returns ``list[CheckMetadata]`` sorted by check id (deterministic
    regardless of filesystem iteration order or YAML row order).

    Raises ``ValueError`` on:

    - Duplicate check ids across files (the first file wins, the
      second raises with both file names in the message).
    - Per-row ``category`` that disagrees with the filename — drop the
      per-row field; the loader injects ``category`` from the filename
      stem so each YAML row stays DRY.
    - Per-row ``docs_url`` — the loader owns this for catalog
      stability; explicit YAML values would silently break URL
      consistency across releases.
    - Pydantic validation errors (file path and check id included in
      the message so a malformed row is one grep away).

    Raises ``RuntimeError`` if no YAML files are found (a missing
    docs/checks/ directory in the wheel is a packaging regression).
    """
    checks_dir = _resolve_checks_dir()
    yaml_files = sorted(checks_dir.glob("*.yaml"))
    if not yaml_files:
        raise RuntimeError(
            f"No check metadata YAMLs found under {checks_dir}. "
            "Reinstall the wheel or restore the docs/checks/ directory."
        )

    out: list[CheckMetadata] = []
    seen_ids: dict[str, str] = {}  # id -> first file that defined it

    for path in yaml_files:
        category = path.stem
        for row in _load_yaml_file(path):
            if not isinstance(row, dict):
                raise ValueError(
                    f"{path.name}: each check entry must be a mapping "
                    f"(got: {type(row).__name__})"
                )
            check_id = row.get("id")
            if not isinstance(check_id, str) or not check_id:
                raise ValueError(
                    f"{path.name}: every entry needs a non-empty 'id' "
                    f"string (got: {check_id!r})"
                )
            if check_id in seen_ids:
                raise ValueError(
                    f"Duplicate check id {check_id!r}: first defined in "
                    f"{seen_ids[check_id]!r}, redefined in {path.name!r}. "
                    "Check IDs must be unique across the entire catalog."
                )
            seen_ids[check_id] = path.name

            # The loader owns category and docs_url. Reject attempts
            # to set them in YAML so the source-of-truth claim
            # (filename → category, id → docs_url) cannot be silently
            # bypassed by a per-row override.
            if "category" in row and row["category"] != category:
                raise ValueError(
                    f"{path.name}: entry {check_id!r} declares "
                    f"category={row['category']!r} but the filename "
                    f"implies category={category!r}. Drop the per-row "
                    "category — the loader injects it from the "
                    "filename stem."
                )
            if "docs_url" in row:
                raise ValueError(
                    f"{path.name}: entry {check_id!r} declares an "
                    "explicit docs_url. The loader derives docs_url "
                    "from the check id; remove the field."
                )

            merged: dict[str, Any] = {
                **row,
                "category": category,
                "docs_url": _docs_url(check_id),
            }
            try:
                out.append(CheckMetadata(**merged))
            except Exception as exc:
                raise ValueError(
                    f"{path.name}: failed to construct CheckMetadata "
                    f"for {check_id!r}: {exc}"
                ) from exc

    out.sort(key=lambda check: check.id)
    return out
