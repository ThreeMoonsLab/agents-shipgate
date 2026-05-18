"""Append-only audit log for baseline writes.

Each call to ``agents-shipgate baseline save`` appends one JSON line to
``.agents-shipgate/baseline-audit.log`` describing the delta between the
prior baseline (if any) and the newly written one. Reviewers can:

- ``git log`` the log to see when fingerprints joined the baseline.
- Use ``agents-shipgate baseline verify`` to confirm the on-disk baseline
  matches the most recent audit entry's ``hash_after``.

The audit log is **tamper-evident but not tamper-proof**. A well-resourced
adversary who edits both the baseline JSON and the audit log atomically
will defeat ``verify``; the goal is to make casual / accidental edits
observably wrong in code review. See ``docs/baseline-integrity.md``.

Storage format: JSONL. Append-only is enforced by always opening with
``mode="a"``; the file is never rewritten in place. ``read_audit_log``
returns entries in append order (oldest first).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_shipgate.core.errors import InputParseError

AUDIT_LOG_SCHEMA_VERSION = "0.1"

# Path conventions. The log lives next to the baseline JSON so a single
# ``.agents-shipgate/`` directory holds both. CLI callers can override
# both, but the default keeps them co-located.
DEFAULT_AUDIT_LOG_PATH = Path(".agents-shipgate") / "baseline-audit.log"


class BaselineAuditEntry(BaseModel):
    """One row in ``baseline-audit.log`` describing a single save.

    ``hash_before`` is the SHA-256 of the prior baseline file's canonical
    content (see :func:`compute_baseline_hash`) or ``None`` if this was
    the first save. ``hash_after`` is the new file's hash. Verification
    walks the log to find the most recent entry and compares its
    ``hash_after`` against the current baseline hash.

    ``added_fingerprints`` / ``removed_fingerprints`` describe the delta
    so reviewers can scan the log without diffing files. They are
    redundant with the JSON contents but cheap to record.
    """

    model_config = ConfigDict(extra="forbid")

    audit_schema_version: str = AUDIT_LOG_SCHEMA_VERSION
    timestamp: str
    run_id: str
    scanner_version: str
    baseline_path: str
    hash_before: str | None
    hash_after: str
    added_fingerprints: list[str] = Field(default_factory=list)
    removed_fingerprints: list[str] = Field(default_factory=list)


def compute_baseline_hash(canonical_json: str) -> str:
    """SHA-256 of canonical baseline JSON.

    ``canonical_json`` should be the exact bytes that ``write_baseline``
    writes to disk. We hash the literal file content rather than a model
    dump so that ``verify_baseline`` can re-read the file and confirm
    byte-equivalence without re-serializing.
    """
    return "sha256:" + hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_baseline_hash_from_file(path: Path) -> str:
    """SHA-256 of a baseline file's exact on-disk bytes.

    Used by ``verify_baseline`` to detect any hand-edits. Reads the file
    bytes directly so trailing whitespace / line endings / re-ordering
    are all detected.
    """
    return compute_baseline_hash(path.read_text(encoding="utf-8"))


def append_audit_entry(log_path: Path, entry: BaselineAuditEntry) -> None:
    """Append one JSON line to the audit log.

    Creates the parent directory if needed. Each line is the entry's
    ``model_dump_json()`` with a trailing newline. The function never
    rewrites existing content.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = entry.model_dump_json() + "\n"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def read_audit_log(log_path: Path) -> list[BaselineAuditEntry]:
    """Read all audit log entries in append order.

    Returns ``[]`` if the file does not exist. Raises
    :class:`InputParseError` on malformed lines so callers can surface
    a clean error rather than silently dropping rows.
    """
    if not log_path.exists():
        return []
    entries: list[BaselineAuditEntry] = []
    text = log_path.read_text(encoding="utf-8")
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            entries.append(BaselineAuditEntry.model_validate_json(raw))
        except ValidationError as exc:
            raise InputParseError(
                f"Invalid baseline audit log entry at {log_path}:{line_number}: {exc}"
            ) from exc
    return entries


def latest_audit_entry(log_path: Path) -> BaselineAuditEntry | None:
    """Return the most recently appended audit entry, or ``None``.

    The most recent entry's ``hash_after`` is the value
    ``verify_baseline`` compares against the current baseline file's hash.
    """
    entries = read_audit_log(log_path)
    return entries[-1] if entries else None


def audit_entry_for_run(
    log_path: Path, run_id: str
) -> BaselineAuditEntry | None:
    """Return the audit entry whose ``run_id`` matches, or ``None``.

    Used by ``verify_baseline`` to confirm that every fingerprint's
    ``provenance.run_id`` has a corresponding audit entry. Missing
    correspondence produces ``SHIP-BASELINE-INTEGRITY-MISMATCH``.
    """
    for entry in read_audit_log(log_path):
        if entry.run_id == run_id:
            return entry
    return None


def utc_now_isoformat() -> str:
    """ISO-8601 UTC timestamp with ``Z`` suffix; second precision.

    Used for both ``BaselineProvenance.recorded_at`` and
    ``BaselineAuditEntry.timestamp`` so they always agree byte-for-byte
    when both are stamped in the same save call.
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
