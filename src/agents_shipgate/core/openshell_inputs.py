"""Bind selected document and link bytes to the existing dependency lifecycle."""
from __future__ import annotations

import hashlib
from pathlib import Path

from agents_shipgate.core.hook_script_capture import capture_hook_script
from agents_shipgate.core.static_inputs import active_static_input_snapshot
from agents_shipgate.core.trust_roots import IdentityBoundReadSession, IdentityReadBudgetExceeded


def capture_openshell_input(reader: IdentityBoundReadSession, path: str, *, absent_paths: set[str]) -> dict:
    """Regular documents use the shared file capture; links bind target text.

    A `generated` VerificationBlob is the derived UTF-8 link-target text, not
    a regular file. Its source discriminator makes type replacement stale.
    Nothing follows a link here; the host reader separately resolves it.
    """
    relative = Path(path)
    snapshot = active_static_input_snapshot()
    if snapshot is not None and snapshot.root != reader.root:
        snapshot = None
    try:
        if reader.directory_entry_kind(relative) != "symlink":
            return {**capture_hook_script(reader, path, absent_paths=absent_paths), "source": "worktree"}
        raw = reader.link_target(relative).encode("utf-8")
        if snapshot is not None:
            snapshot.bind_dependency_link(reader.root / relative, raw)
        return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "limit": None, "source": "generated"}
    except IdentityReadBudgetExceeded:
        raise
    except (OSError, ValueError, UnicodeError):
        # A missing component still belongs to the generic absence mechanism.
        return {**capture_hook_script(reader, path, absent_paths=absent_paths), "source": "worktree"}
