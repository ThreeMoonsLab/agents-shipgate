"""Advisory rows for an explicitly supplied diff, using boundary reconstruction."""

from __future__ import annotations

import tempfile
from pathlib import Path, PurePosixPath

from agents_shipgate.core.boundary_diff import _resolve_changed_file_text, parse_unified_diff
from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.host_comparison import compare_host_inventories
from agents_shipgate.core.host_grants import HostStaticParseCache, build_host_boundary_snapshot
from agents_shipgate.schemas.host_comparison import HostComparison


def compare_host_diff(workspace: Path, diff_text: str) -> HostComparison:
    """Read only changed host files. Unchanged context is not an inventory claim."""
    cache = HostStaticParseCache()
    with tempfile.TemporaryDirectory(prefix="shipgate-host-diff-") as scratch:
        before, after = Path(scratch) / "base", Path(scratch) / "head"
        before.mkdir()
        after.mkdir()
        for change in parse_unified_diff(diff_text):
            names = [name for name in (change.old_path, change.new_path) if name]
            if not any(adapter.matches(name) for adapter in BOUNDARY_ADAPTERS for name in names):
                continue
            if any(
                PurePosixPath(name).is_absolute()
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                or "\0" in name
                for name in names
            ):
                return HostComparison(
                    comparison_status="incomparable",
                    incomparable_reasons=["invalid_changed_host_path"],
                    head_kind="provided_diff",
                )
            resolved = _resolve_changed_file_text(
                workspace, change, [], cache, preserve_rename_source=True
            )
            if resolved.old_text is None or resolved.new_text is None:
                return HostComparison(
                    comparison_status="incomparable",
                    incomparable_reasons=["changed_host_content_unresolved"],
                    head_kind="provided_diff",
                    paths=sorted(set(names)),
                )
            for root, name, value, absent in (
                (before, change.old_path, resolved.old_text, change.is_new),
                (after, change.new_path, resolved.new_text, change.is_deleted),
            ):
                if name and not absent:
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(value, encoding="utf-8")
        cache.finish()
        return compare_host_inventories(
            build_host_boundary_snapshot(before).inventory,
            build_host_boundary_snapshot(after).inventory,
            head_kind="provided_diff",
            redact_permission_arguments=True,
        )
