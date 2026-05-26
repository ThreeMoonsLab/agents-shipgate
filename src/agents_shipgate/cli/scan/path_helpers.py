from __future__ import annotations

import os
from pathlib import Path

from agents_shipgate.core.baseline_audit import DEFAULT_AUDIT_LOG_PATH
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


def _relative_display_path(path: Path, base_dir: Path) -> str:
    resolved = path.resolve()
    base = base_dir.resolve()
    rel = os.path.relpath(resolved, base)
    if rel == ".." or rel.startswith(f"..{os.sep}"):
        return str(resolved)
    return rel


def _resolve_audit_log_path(
    manifest: AgentsShipgateManifest,
    baseline_path: Path,
) -> Path:
    """Resolve the baseline audit log path.

    Resolution order:
    1. ``manifest.baseline.audit_log`` if set (relative paths resolved
       against the baseline file's directory).
    2. Otherwise ``<baseline_path.parent>/baseline-audit.log`` —
       co-located with the baseline JSON. This matches the default that
       ``write_baseline`` uses, so save/verify see the same file
       without configuration.
    """
    override = manifest.baseline.audit_log
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = baseline_path.parent / candidate
        return candidate
    return baseline_path.parent / DEFAULT_AUDIT_LOG_PATH.name


def _default_baseline_status(base_dir: Path) -> dict[str, object]:
    path = base_dir / ".agents-shipgate" / "baseline.json"
    return {
        "default_path": _relative_display_path(path, base_dir),
        "present": path.exists(),
    }
