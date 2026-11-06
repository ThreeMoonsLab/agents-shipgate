"""Append-only audit log for baseline writes.

Each call to ``agents-shipgate baseline save`` appends one JSON line to
``.agents-shipgate/baseline-audit.log`` describing the delta between the
prior baseline (if any) and the newly written one. Reviewers can:

- ``git log`` the log to see when fingerprints joined the baseline.
- Use ``agents-shipgate baseline verify`` to confirm the on-disk baseline
  matches the ``hash_after`` of the most recent audit entry recorded for
  *that* baseline (#1030). Several baselines can share one directory, and so
  one log; a row only ever vouches for the baseline file it names.

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
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.static_inputs import read_static_input_bytes

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
    walks the log to find the most recent entry recorded for the baseline
    being verified (see :func:`audit_entry_records_baseline`) and compares
    its ``hash_after`` against the current baseline hash.

    ``baseline_path`` is the path exactly as the saving command spelled it
    — relative to that command's working directory, or absolute — so only
    its final component identifies the baseline across machines and
    working directories.

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
    """SHA-256 of canonical baseline JSON text.

    ``canonical_json`` is the text ``write_baseline`` writes, whose line
    endings are LF. We hash the file's text rather than a model dump so
    that ``verify_baseline`` can re-read the file and confirm equivalence
    without re-serializing; :func:`compute_baseline_hash_from_file` is the
    reading half and defines exactly what "equivalent" means.
    """
    return "sha256:" + hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def normalize_baseline_line_endings(text: str) -> str:
    """Rewrite CRLF and lone CR line endings as LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def compute_baseline_hash_from_file(path: Path) -> str:
    """SHA-256 of a baseline file's UTF-8 text with line endings read as LF.

    Used by ``verify_baseline`` to detect hand-edits. Every edit to the
    file's content changes the hash: a value, a key order, added or removed
    whitespace (spaces, tabs, blank lines), a byte-order mark. A line-ending
    conversion alone does not: CRLF and a lone CR hash as LF.

    That is deliberate (#1030), and it has been the behaviour of every
    release since the audit log shipped:

    - a line-ending conversion cannot change what the baseline accepts.
      JSON treats CR and LF alike as insignificant whitespace and cannot
      carry a raw one inside a string, so the parsed baseline is identical;
    - Git rewrites line endings on checkout (Git for Windows defaults to
      ``core.autocrlf=true``), so hashing raw bytes would turn every such
      checkout of an untouched baseline into a critical, unsuppressible
      ``SHIP-BASELINE-INTEGRITY-MISMATCH``;
    - ``hash_after`` values in logs written by released versions were
      computed this way, and must keep verifying.

    The bytes come through the active static-input snapshot when there is
    one, so the hash covers the same bytes ``load_baseline`` parsed.
    """
    raw = read_static_input_bytes(path)
    return compute_baseline_hash(normalize_baseline_line_endings(raw.decode("utf-8")))


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


_PATH_SEPARATORS = re.compile(r"[\\/]")


def recorded_baseline_name(recorded_path: str) -> str:
    """The file name an audit row's ``baseline_path`` records.

    ``baseline_path`` is spelled as the saving command spelled it: relative
    to whatever working directory that command ran in, or absolute, with
    POSIX or Windows separators. Its final component is the one part that
    names the same file however and wherever it was saved, so it is the
    part verification compares.
    """
    return _PATH_SEPARATORS.split(recorded_path.rstrip("/\\"))[-1]


def audit_entry_records_baseline(entry: BaselineAuditEntry, baseline_path: Path) -> bool:
    """Whether ``entry`` was written by a save of ``baseline_path`` (#1030).

    A row belongs to the baseline whose file name it records. Baselines in
    one directory have distinct names, so each verifies against its own
    rows: saving ``baseline-prod.json`` neither vouches for nor invalidates
    ``baseline.json``. Two same-named baselines in different directories
    can only meet in one log through ``baseline.audit_log``; they are not
    told apart, and give them distinct names or distinct logs.
    """
    return recorded_baseline_name(entry.baseline_path) == baseline_path.name


def latest_audit_entry(
    log_path: Path,
    *,
    baseline_path: Path | None = None,
) -> BaselineAuditEntry | None:
    """Return the most recently appended audit entry, or ``None``.

    With ``baseline_path``, only rows recorded for that baseline count (see
    :func:`audit_entry_records_baseline`); that entry's ``hash_after`` is
    the value ``verify_baseline`` compares against the current file's hash.
    Without it, the last row of the log, whichever baseline wrote it.
    """
    return latest_entry_for_baseline(read_audit_log(log_path), baseline_path)


def latest_entry_for_baseline(
    entries: list[BaselineAuditEntry],
    baseline_path: Path | None,
) -> BaselineAuditEntry | None:
    """The last of ``entries`` recorded for ``baseline_path``, or ``None``."""
    if baseline_path is None:
        return entries[-1] if entries else None
    for entry in reversed(entries):
        if audit_entry_records_baseline(entry, baseline_path):
            return entry
    return None


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
