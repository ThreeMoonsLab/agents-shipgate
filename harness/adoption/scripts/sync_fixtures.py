"""Materialize ``benchmark/repos/<archetype>/`` from in-repo sources.

The archetype directories under ``benchmark/repos/`` are vendored copies of
``samples/`` and ``examples/golden-prs/`` content. Keeping them as a separate
tree lets us pin a benchmark schema independently from the sample fixtures
that the rest of the test suite uses.

This script is the canonical materializer. Run it once when setting up the
harness; rerun after adding a new archetype.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# (archetype_slug, source_relative_to_repo_root)
RECIPES: tuple[tuple[str, str], ...] = (
    ("openai-agents-sdk", "samples/support_refund_agent"),
    ("mcp-only", "samples/mcp_only_server"),
    ("openapi-only", "samples/openapi_only_agent"),
    ("langgraph", "samples/simple_langchain_agent"),
    ("adk-dynamic-toolset", "samples/google_adk_agent"),
    ("crewai", "samples/simple_crewai_agent"),
    ("clean-read-only", "samples/clean_read_only_agent"),
    ("n8n", "samples/n8n_workflow_agent"),
)
"""Archetype slug → source path under the repo root.

``non-agent-negative-control`` is intentionally excluded — it is a git
submodule of an external library and must be initialized via ``git submodule
add``. See ``benchmark/repos/README.md``.
"""


@dataclass(frozen=True)
class SyncResult:
    archetype: str
    source: Path
    destination: Path
    copied_files: int


def repo_root() -> Path:
    """Resolve the repo root from this file's location."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "pyproject.toml").is_file() and (ancestor / "benchmark").is_dir():
            return ancestor
    raise RuntimeError(f"Could not locate repo root from {here}")


def materialize(root: Path | None = None, *, force: bool = False) -> list[SyncResult]:
    """Copy each recipe source into ``benchmark/repos/<slug>/``.

    Skips ``non-agent-negative-control`` (submodule).
    """
    root = root or repo_root()
    results: list[SyncResult] = []
    for slug, source_rel in RECIPES:
        source = root / source_rel
        destination = root / "benchmark" / "repos" / slug
        if not source.is_dir():
            print(f"[sync] SKIP {slug}: source not found at {source}", file=sys.stderr)
            continue
        if destination.exists():
            if not force:
                print(f"[sync] SKIP {slug}: {destination} already exists (--force to overwrite)")
                continue
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=_ignore)
        file_count = sum(1 for _ in destination.rglob("*") if _.is_file())
        results.append(SyncResult(slug, source, destination, file_count))
        print(f"[sync] OK {slug}: copied {file_count} files from {source_rel}")
    return results


def _ignore(_src: str, names: list[str]) -> list[str]:
    """Skip non-fixture artifacts that would just bloat the vendored copy."""
    drop = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "expected", "evals"}
    return [n for n in names if n in drop]


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing archetype directories under benchmark/repos/.",
    )
    args = parser.parse_args()
    materialize(force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
