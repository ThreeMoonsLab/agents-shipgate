"""Advisory rows for an explicitly supplied diff, using boundary reconstruction."""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from agents_shipgate.core.boundary_diff import (
    DiffFile,
    _resolve_changed_file_text,
    parse_unified_diff,
)
from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.host_comparison import (
    compare_host_inventories,
    without_untouched_script_limits,
)
from agents_shipgate.core.host_grants import (
    MAX_HOST_CONFIG_BYTES,
    HostStaticParseCache,
    build_host_boundary_snapshot,
    hook_dependency_issues,
    hook_dependency_limits,
    without_host_issues,
)
from agents_shipgate.core.unread_inputs import ChangedInputs
from agents_shipgate.schemas.host_comparison import HostComparison


def _names(change: DiffFile) -> list[str]:
    return [name for name in (change.old_path, change.new_path) if name]


def _invalid_name(name: str) -> bool:
    return (
        PurePosixPath(name).is_absolute()
        or ".." in PurePosixPath(name).parts
        or "\\" in name
        or "\0" in name
    )


def _diff_changed_inputs(workspace: Path, changes: list[DiffFile]) -> ChangedInputs:
    """The diff's own paths, and each side as the diff states it (#929).

    Only for the wording of a removed MCP server's row: this route records no
    coverage. A path is present on a side only where the diff has it there,
    since unchanged context is not an inventory claim, and its bytes are the
    side the diff resolves to, within the host reader's per-file bound. A
    path this route refuses to write is neither present nor read. It reads
    through no parse cache: the comparison's cache has finished by then.
    """

    sides: dict[str, dict[str, DiffFile]] = {"base": {}, "head": {}}
    for change in changes:
        if any(_invalid_name(name) for name in _names(change)):
            continue
        if change.old_path and not change.is_new:
            sides["base"][change.old_path] = change
        if change.new_path and not change.is_deleted:
            sides["head"][change.new_path] = change

    def present(side: str, paths: Sequence[str]) -> set[str]:
        return {path for path in paths if path in sides[side]}

    def read(side: str, paths: Sequence[str]) -> dict[str, bytes]:
        contents: dict[str, bytes] = {}
        for path in paths:
            change = sides[side].get(path)
            if change is None:
                continue
            resolved = _resolve_changed_file_text(
                workspace, change, [], None, preserve_rename_source=True
            )
            text = resolved.old_text if side == "base" else resolved.new_text
            data = text.encode("utf-8") if text is not None else None
            if data is not None and len(data) <= MAX_HOST_CONFIG_BYTES:
                contents[path] = data
        return contents

    paths = {name for change in changes for name in _names(change) if not _invalid_name(name)}
    return ChangedInputs(paths=tuple(sorted(paths)), present=present, read=read)


def _write_sides(
    workspace: Path,
    changes: list[DiffFile],
    before: Path,
    after: Path,
    cache: HostStaticParseCache,
) -> HostComparison | None:
    """Write each change's two sides, or the refusal a path or its content earns."""

    for change in changes:
        names = _names(change)
        if any(_invalid_name(name) for name in names):
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
    return None


def _absence_the_diff_states(
    inventory: dict[str, Any], *, touched: set[str], present: set[str]
) -> dict[str, Any]:
    """One side, where a script the diff touches is absent as the diff states it (#702).

    A touched script was written on each side the diff has it. Where the diff
    adds or deletes it, its absence from this side is the diff's own
    statement, compared (`missing_input` against its bytes) rather than a
    limit no reconstruction could lift.
    """

    limits = hook_dependency_limits(inventory)
    stated = {
        issue
        for issue, script in hook_dependency_issues(inventory).items()
        if script[1] in touched
        and script[1] not in present
        and limits.get(script) == "missing_input"
    }
    return without_host_issues(inventory, stated) if stated else inventory


def compare_host_diff(workspace: Path, diff_text: str) -> HostComparison:
    """Read only changed host files. Unchanged context is not an inventory claim.

    A selected hook script the diff touches is reconstructed from it like a
    host file, and one it does not touch is left out (#702 review), so a
    hook's declaration is compared as it was before scripts were read.
    """
    cache = HostStaticParseCache()
    changes = parse_unified_diff(diff_text)
    touched = {name for change in changes for name in _names(change)}
    host_changes = [
        change
        for change in changes
        if any(adapter.matches(name) for adapter in BOUNDARY_ADAPTERS for name in _names(change))
    ]
    with tempfile.TemporaryDirectory(prefix="shipgate-host-diff-") as scratch:
        before, after = Path(scratch) / "base", Path(scratch) / "head"
        before.mkdir()
        after.mkdir()
        refused = _write_sides(workspace, host_changes, before, after, cache)
        if refused is not None:
            return refused

        def read() -> tuple[dict[str, Any], dict[str, Any]]:
            # The original checkout names absolute references, as `diff` reads them.
            return tuple(  # type: ignore[return-value]
                build_host_boundary_snapshot(
                    root, cache=HostStaticParseCache(reference_workspace=workspace.resolve())
                ).inventory
                for root in (before, after)
            )

        base, head = read()
        scripts = {path for inventory in (base, head) for _host, path in hook_dependency_limits(inventory)}
        written = {id(change) for change in host_changes}
        edited = [
            change
            for change in changes
            if id(change) not in written and scripts.intersection(_names(change))
        ]
        if edited:
            refused = _write_sides(workspace, edited, before, after, cache)
            if refused is not None:
                return refused
            base, head = read()
        cache.finish()
        # The diff is the whole change: a script it does not touch is
        # unchanged by construction, and left out rather than refusing.
        base, head = without_untouched_script_limits(base, head, touched.__contains__)
        base = _absence_the_diff_states(
            base,
            touched=touched,
            present={change.old_path for change in edited if change.old_path and not change.is_new},
        )
        head = _absence_the_diff_states(
            head,
            touched=touched,
            present={change.new_path for change in edited if change.new_path and not change.is_deleted},
        )
        return compare_host_inventories(
            base,
            head,
            head_kind="provided_diff",
            redact_permission_arguments=True,
            # `agent-result` keeps only the rows, reasons and status, so no
            # coverage is built to be discarded (#812).
            coverage=False,
            # Read only if a removed MCP server's row is worded from it (#929).
            changed_inputs=lambda: _diff_changed_inputs(workspace, changes),
        )
