"""Row schema for mined-PR results (miner schema v0.1).

Privacy rule: rows carry public PR metadata, verdicts, check IDs, and
counts only — never diff text, code excerpts, or report evidence. The
same "no raw transcript text" convention as the adoption-harness CSVs.
"""

from __future__ import annotations

import csv
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

MINER_SCHEMA_VERSION = "0.1"

# Row lifecycle states.
STATUS_EVALUATED = "evaluated"  # head scan produced a release decision
STATUS_TRIGGER_SKIP = "trigger_skip"
STATUS_INIT_SKIP = "init_skip"  # cold-start init wrote no manifest
STATUS_SCAN_FAILED = "scan_failed"  # manifest existed/was written; scan died
STATUS_ERROR = "error"


@dataclass
class MinedRow:
    repo: str
    pr_number: int
    pr_url: str
    title: str
    merged_at: str
    base_sha: str
    head_sha: str
    files_changed: int = 0
    trigger_run: bool = False
    trigger_rationale: str = ""
    check_decision: str = ""
    check_rule_ids: str = ""  # comma-separated check IDs, no evidence
    init_status: str = ""  # preexisting | written | not_agent_project | failed
    head_decision: str = ""
    head_blockers: int | None = None
    head_review_items: int | None = None
    evidence_gaps: int | None = None
    tools_scanned: int | None = None
    cap_added: int | None = None
    cap_removed: int | None = None
    cap_changed: int | None = None
    cap_broadened: int | None = None
    status: str = STATUS_ERROR
    notes: str = ""
    schema_version: str = field(default=MINER_SCHEMA_VERSION)

    def to_json(self) -> dict[str, object]:
        return dataclasses.asdict(self)


CSV_COLUMNS: tuple[str, ...] = tuple(
    f.name for f in dataclasses.fields(MinedRow)
)


def write_csv(rows: list[MinedRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        # LF-only: the csv module's default dialect writes CRLF, which makes
        # every committed row fail `git diff --check` as trailing whitespace.
        writer = csv.DictWriter(
            handle, fieldnames=list(CSV_COLUMNS), lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            payload = row.to_json()
            writer.writerow(
                {key: ("" if value is None else value) for key, value in payload.items()}
            )


def write_jsonl(rows: list[MinedRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.to_json(), sort_keys=True) + "\n")


def summarize(rows: list[MinedRow]) -> dict[str, object]:
    """Aggregate counts for the run summary printed after a mine."""

    by_status: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        if row.head_decision:
            by_decision[row.head_decision] = by_decision.get(row.head_decision, 0) + 1
    decided = [r for r in rows if r.head_decision]
    ie_count = sum(1 for r in decided if r.head_decision == "insufficient_evidence")
    return {
        "rows": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "by_head_decision": dict(sorted(by_decision.items())),
        "ie_rate_on_decided": (
            round(ie_count / len(decided), 3) if decided else None
        ),
    }
