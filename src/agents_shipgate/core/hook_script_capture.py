"""Byte-only capture of one established hook executable reference (#702)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from agents_shipgate.core.static_inputs import active_static_input_snapshot
from agents_shipgate.core.trust_roots import IdentityBoundReadSession, IdentityReadBudgetExceeded

MAX_HOOK_SCRIPT_BYTES = 1024 * 1024


def capture_hook_script(
    reader: IdentityBoundReadSession, path: str, *, absent_paths: set[str] | None = None,
) -> dict:
    """Use the host read session's containment, budget and final identity pass.

    Missing/unsafe inputs retain an explicit limitation. Aggregate exhaustion
    propagates, so the caller cannot turn an incomplete census into a pass.
    """
    relative = Path(path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        return {"sha256": None, "size_bytes": None, "limit": "escaping_path"}
    snapshot = active_static_input_snapshot()
    if snapshot is not None and snapshot.root != reader.root:
        snapshot = None

    def limited(reason: str, missing: Path | None = None) -> dict:
        if snapshot is not None:
            if missing is not None:
                # Bind the first absent component, whose parent was read.
                # A missing ancestor is an obligation too, even when Git
                # ignores the entire directory containing the executable.
                try:
                    absent = snapshot.bind_dependency_absence(reader.root / missing)
                except (OSError, ValueError):
                    absent = False
                if absent:
                    return {"sha256": None, "size_bytes": None, "limit": reason}
            snapshot.mark_unconfirmable_dependency(reader.root / relative)
        return {"sha256": None, "size_bytes": None, "limit": reason}

    try:
        for index, component in enumerate(relative.parts):
            parent = Path(*relative.parts[:index])
            if component not in reader.directory_entries(parent):
                missing = parent / component
                # An exact listing miss is not absence on a case-insensitive
                # filesystem. Never bind a differently spelled alias.
                try:
                    (reader.root / missing).lstat()
                except FileNotFoundError:
                    if absent_paths is not None:
                        absent_paths.add(missing.as_posix())
                else:
                    return limited("unsafe_or_unreadable_input")
                return limited("missing_input", parent / component)
            kind = reader.directory_entry_kind(parent / component)
            if kind == "symlink":
                return limited("symlink_input")
            expected = "file" if index == len(relative.parts) - 1 else "directory"
            if kind != expected:
                return limited("non_regular_input")
        raw = reader.read_bytes(relative, max_bytes=MAX_HOOK_SCRIPT_BYTES)
        if snapshot is not None:
            # Configured verify already binds dependency_inputs in its plan.
            # Consume the same bytes here and there, never stamp a later read
            # over evidence from an earlier generation.
            absolute = reader.root / relative
            captured = snapshot.read_bytes(absolute, max_bytes=MAX_HOOK_SCRIPT_BYTES)
            if captured != raw:
                snapshot.mark_unconfirmable_dependency(absolute)
                raise ValueError("hook script moved between identity-bound reads")
            snapshot.mark_dependency_input(absolute)
    except IdentityReadBudgetExceeded:
        raise
    except ValueError as exc:
        reason = "oversized_input" if str(exc) == f"file exceeds the {MAX_HOOK_SCRIPT_BYTES}-byte read limit" else "unsafe_or_unreadable_input"
        return limited(reason)
    except (OSError, NotImplementedError):
        return limited("unreadable_input")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "limit": None}
