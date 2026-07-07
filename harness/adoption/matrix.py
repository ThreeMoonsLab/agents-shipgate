"""Matrix parser. Resolves ``benchmark/matrix.yaml`` into ``Cell`` objects.

A cell is one ``(agent, model, archetype, variant, negative_overlay, prompt)``
combination. Cells are the unit of work for the runner and the unit of output
for the scorer.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

EXCLUDED_PAIRS: tuple[tuple[str, str], ...] = (
    ("40-shipgate-yaml", "60-docs-only-negative"),
)
"""``(variant, negative_overlay)`` combinations excluded from the v1 matrix.

See the plan: docs/triggers.json defines ``force_run`` when ``shipgate.yaml``
is present, even on docs-only PRs. Conflating that with the "no-hint docs-only
noise" exit criterion would muddle the signal.
"""


class MatrixError(ValueError):
    """Raised when ``matrix.yaml`` is invalid."""


# The release verdicts a cell may be declared to face by construction. A cell
# that carries ``expected_block`` asserts: "materializing this
# (archetype, variant) and running verify yields this non-mergeable verdict,
# with human authority required to clear it." Used by the adversarial-pressure
# cells so the obedience metric can treat them as *engaged by construction* —
# the agent faces a genuine block whether or not it bothers to run verify.
# tests/harness/test_pressure_ground_truth.py pins each declaration against a
# live verify run so it cannot drift from reality.
EXPECTED_BLOCK_VERDICTS: frozenset[str] = frozenset(
    {"blocked", "human_review_required", "insufficient_evidence"}
)


@dataclass(frozen=True)
class Cell:
    archetype: str
    variant: str
    negative_overlay: str | None
    prompt: str
    agent: str
    model: str | None = None
    expected_block: str | None = None

    @property
    def cell_id(self) -> str:
        # expected_block is scenario metadata, not identity — a cell is the
        # same cell whether or not its ground-truth block is annotated.
        parts = [self.archetype, self.variant]
        if self.negative_overlay:
            parts.append(self.negative_overlay)
        parts.extend([self.prompt, self.agent])
        return "__".join(parts)


def load_matrix(path: Path) -> list[Cell]:
    """Parse a ``matrix.yaml`` file into a flat list of ``Cell`` objects.

    The file uses an explicit list of cells (rather than a cartesian product)
    so cost-sensitive subsets can be hand-curated without parser magic.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cells_raw = raw.get("cells")
    if not isinstance(cells_raw, list):
        raise MatrixError(f"{path}: top-level 'cells' must be a list.")
    cells: list[Cell] = []
    for i, entry in enumerate(cells_raw):
        if not isinstance(entry, dict):
            raise MatrixError(f"{path}: cell #{i} must be a mapping.")
        try:
            cell = Cell(
                archetype=entry["archetype"],
                variant=entry["variant"],
                negative_overlay=entry.get("negative_overlay"),
                prompt=entry["prompt"],
                agent=entry["agent"],
                model=entry.get("model"),
                expected_block=entry.get("expected_block"),
            )
        except KeyError as missing:
            raise MatrixError(f"{path}: cell #{i} missing required field {missing}") from None
        if cell.expected_block is not None and cell.expected_block not in EXPECTED_BLOCK_VERDICTS:
            raise MatrixError(
                f"{path}: cell #{i} ({cell.cell_id}) declares expected_block "
                f"{cell.expected_block!r}, not one of {sorted(EXPECTED_BLOCK_VERDICTS)}."
            )
        if (cell.variant, cell.negative_overlay or "") in EXCLUDED_PAIRS:
            raise MatrixError(
                f"{path}: cell #{i} ({cell.cell_id}) combines variant "
                f"{cell.variant!r} with negative_overlay {cell.negative_overlay!r}, "
                "which is excluded from the v1 matrix."
            )
        cells.append(cell)
    return cells


def filter_cells(
    cells: Iterable[Cell],
    *,
    agents: Iterable[str] | None = None,
    archetypes: Iterable[str] | None = None,
) -> list[Cell]:
    """Subset a matrix in-memory; useful for smoke/dry-run modes."""
    agent_set = set(agents) if agents else None
    archetype_set = set(archetypes) if archetypes else None
    out: list[Cell] = []
    for c in cells:
        if agent_set and c.agent not in agent_set:
            continue
        if archetype_set and c.archetype not in archetype_set:
            continue
        out.append(c)
    return out


__all__ = [
    "Cell",
    "MatrixError",
    "filter_cells",
    "load_matrix",
    "EXCLUDED_PAIRS",
    "EXPECTED_BLOCK_VERDICTS",
]
