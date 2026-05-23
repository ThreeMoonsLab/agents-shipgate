from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.baseline import load_baseline
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.report.tool_surface_diff import (
    ToolSurfaceDiffReference,
    load_tool_surface_diff_reference,
    reference_from_baseline,
)

from .models import _DiffReferences
from .path_helpers import _relative_display_path


def _load_diff_references(
    *,
    baseline_path: Path | None,
    diff_from_path: Path | None,
    base_dir: Path,
) -> _DiffReferences:
    """Phase 4: load optional baseline JSON + tool-surface diff reference.

    ``--diff-from`` wins over baseline-derived reference when both are
    supplied. ``InputParseError`` from either path is caught and returned
    as a string so the downstream diff is rendered as ``enabled=False``
    with a reviewer-visible note rather than aborting the scan.
    """
    baseline_file = load_baseline(baseline_path) if baseline_path else None
    baseline_display_path = (
        _relative_display_path(baseline_path, base_dir) if baseline_path else None
    )
    diff_reference: ToolSurfaceDiffReference | None = None
    diff_reference_error: str | None = None
    try:
        if diff_from_path:
            diff_reference = load_tool_surface_diff_reference(
                diff_from_path,
                display_path=_relative_display_path(diff_from_path, base_dir),
            )
        elif baseline_file:
            diff_reference = reference_from_baseline(
                baseline_file,
                display_path=baseline_display_path,
            )
    except InputParseError as exc:
        diff_reference_error = str(exc)
    return _DiffReferences(
        baseline_file=baseline_file,
        baseline_display_path=baseline_display_path,
        diff_reference=diff_reference,
        diff_reference_error=diff_reference_error,
    )
