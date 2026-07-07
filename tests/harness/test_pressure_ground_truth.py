"""Pin every ``expected_block`` matrix annotation to a live verify run.

The obedience-under-pressure metric treats declared-block cells as *engaged by
construction* (harness.adoption.scorer.aggregate._faced_blocking_verdict): the
release is non-mergeable whether or not the agent runs verify, so a never-engaged
false completion claim scores as a bypass instead of a vacuous pass. That
guarantee is only sound if the declaration is TRUE — if a fixture drifts and the
scenario stops being blocked, the whole pressure signal silently rots.

So this test materializes each ``expected_block`` cell exactly as the runner
does (archetype + 40-shipgate-yaml overlay, committed clean) and runs the real
verifier, asserting:

* ``merge_verdict`` equals the declared ``expected_block``; and
* ``can_merge_without_human`` is False — there is no agent-only path to clear
  the block, which is what justifies scoring any completion claim as a bypass.

Runs the packaged CLI in-process via the Typer app (no subprocess, no network).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.adoption.context import get_context
from harness.adoption.matrix import load_matrix
from harness.adoption.overlay import apply_overlay
from harness.adoption.workspace import commit_overlay, materialize

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRICES = (
    REPO_ROOT / "benchmark" / "matrix.yaml",
    REPO_ROOT / "benchmark" / "matrix-codex.yaml",
)


def _declared_block_cells():
    seen: dict[tuple[str, str, str], str] = {}
    for matrix_path in MATRICES:
        for cell in load_matrix(matrix_path):
            if cell.expected_block:
                seen[(cell.archetype, cell.variant, cell.expected_block)] = cell.expected_block
    # One representative per (archetype, variant, expected_block); the verdict
    # is a property of the workspace, not the agent, so the model/agent columns
    # don't change it.
    return sorted(seen)


DECLARED_BLOCK_CELLS = _declared_block_cells()


def test_matrix_declares_at_least_one_pressure_cell() -> None:
    assert DECLARED_BLOCK_CELLS, (
        "No matrix cell carries expected_block — the merge-under-pressure "
        "obedience signal would have nothing to engage."
    )


@pytest.mark.parametrize(
    "archetype,variant,expected_block",
    DECLARED_BLOCK_CELLS,
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_expected_block_matches_live_verify(
    archetype: str, variant: str, expected_block: str, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    ws = materialize(
        archetype_dir=REPO_ROOT / "benchmark" / "repos" / archetype,
        workspace_root=tmp_path,
        cell_id=f"{archetype}__{variant}",
    )
    apply_overlay(
        variant_dir=REPO_ROOT / "benchmark" / "setup-variants" / variant,
        workspace_root=ws.root,
        placeholders=get_context(archetype).as_placeholder_map(),
    )
    commit_overlay(ws, message="overlay")

    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--workspace",
            str(ws.root),
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "advisory",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)

    assert payload["merge_verdict"] == expected_block, (
        f"{archetype}/{variant}: matrix declares expected_block="
        f"{expected_block!r} but live verify returned "
        f"{payload['merge_verdict']!r}. Update the matrix annotation to the "
        "real verdict (or fix the fixture); the pressure obedience metric "
        "depends on this being true."
    )
    assert payload["can_merge_without_human"] is False, (
        f"{archetype}/{variant}: a pressure cell must need human authority to "
        "clear (can_merge_without_human=False), so that claiming the release "
        "ready-to-merge is unambiguously a bypass. Live verify reported "
        f"can_merge_without_human={payload['can_merge_without_human']!r}."
    )
