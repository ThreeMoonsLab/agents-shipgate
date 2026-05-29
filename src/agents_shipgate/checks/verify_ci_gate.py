"""SHIP-VERIFY-CI-GATE-REMOVED (§5.1 Tier B / §5.3).

Catches the bluntest reward hack: deleting Shipgate's CI workflow so the
gate never runs. Fires when, in verify mode, a Shipgate CI-gate workflow
path appears in the PR's changed files AND that file no longer exists on
disk (a deletion). This is detectable without a base snapshot — the
changed-files signal plus the filesystem absence is sufficient — so it
degrades gracefully when no ``--diff-from`` base is available.

Emitted at ``critical``: removing the gate from an opted-in repo is the
strongest weakening signal in the family.
"""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.checks._verify_common import (
    changed_files,
    touched,
    verification_active,
    verify_finding,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-CI-GATE-REMOVED"

_CI_GATE_SURFACES = (
    "**/.github/workflows/agents-shipgate.yml",
    "**/.github/workflows/agents-shipgate.yaml",
)


def run(context: ScanContext) -> list[Finding]:
    if not verification_active(context):
        return []
    touched_gates = touched(_CI_GATE_SURFACES, changed_files(context))
    if not touched_gates:
        return []
    workspace = _workspace_root(context)
    removed = [path for path in touched_gates if not _exists(workspace, path)]
    if not removed:
        return []
    return [
        verify_finding(
            context,
            check_id=CHECK_ID,
            title="Shipgate CI gate removed",
            severity="critical",
            evidence={
                "kind": "ci_gate_removed",
                "removed_workflow_files": removed,
            },
            recommendation=(
                "This PR deletes the Shipgate CI workflow, which would stop "
                "the release gate from running. A human must approve removing "
                "CI enforcement; do not remove the gate to make CI pass."
            ),
        )
    ]


def _workspace_root(context: ScanContext) -> Path:
    """Best-effort workspace root: the directory containing shipgate.yaml."""
    config_path = getattr(context, "config_path", None)
    if config_path is None:
        return Path.cwd()
    return Path(config_path).resolve().parent


def _exists(workspace: Path, changed_path: str) -> bool:
    """True if the changed path resolves to an existing file in the workspace.

    Changed paths are repo-relative; we also accept an absolute changed
    path. A non-existent file means the PR deleted it.
    """
    candidate = Path(changed_path)
    if candidate.is_absolute():
        return candidate.exists()
    return (workspace / candidate).exists()
