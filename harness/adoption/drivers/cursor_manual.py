"""Cursor manual-entry driver for behavioural adoption scorecards.

Cursor does not provide a reliable headless agent runner. This driver lets an
operator capture a real Cursor session under ``<cell>/manual/`` and replay that
evidence into the same transcript, command, file-op, summary, and diff streams
used by live drivers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from harness.adoption.drivers.base import DriverInputs, RunResult
from harness.adoption.observer.transcript import TranscriptWriter


class CursorManualDriver:
    name = "cursor-manual"

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        started = datetime.now(UTC)
        manual_dir = inputs.artifacts_dir / "manual"
        if not manual_dir.is_dir():
            ended = datetime.now(UTC)
            return RunResult(
                started_at=started,
                ended_at=ended,
                degraded=True,
                error=f"manual Cursor evidence directory not found: {manual_dir}",
                summary_text=(
                    "Cursor manual-entry evidence missing. Create manual/"
                    "{transcript.jsonl,commands.jsonl,file_ops.jsonl,summary.md,final.diff} "
                    "under this cell directory and rerun the harness."
                ),
            )
        if not _has_behavioral_evidence(manual_dir):
            ended = datetime.now(UTC)
            return RunResult(
                started_at=started,
                ended_at=ended,
                degraded=True,
                error=(
                    "manual Cursor behavioral evidence not found: expected a "
                    "non-empty transcript.jsonl or commands.jsonl"
                ),
                summary_text=(
                    "Cursor manual-entry evidence incomplete. Add at least one "
                    "non-empty manual/transcript.jsonl or manual/commands.jsonl "
                    "file for the captured session and rerun the harness."
                ),
            )

        for payload in _read_jsonl(manual_dir / "transcript.jsonl"):
            writer.transcript(payload)
        for payload in _read_jsonl(manual_dir / "commands.jsonl"):
            writer.command(
                payload.get("command", ""),
                exit_code=payload.get("exit_code"),
                output=payload.get("output"),
            )
        for payload in _read_jsonl(manual_dir / "file_ops.jsonl"):
            writer.file_op(
                payload.get("op", ""),
                payload.get("path", ""),
                detail=payload.get("detail"),
            )

        summary = _read_text(manual_dir / "summary.md")
        final_diff = _read_text(manual_dir / "final.diff")
        ended = datetime.now(UTC)
        return RunResult(
            started_at=started,
            ended_at=ended,
            degraded=False,
            summary_text=summary,
            final_diff=final_diff,
        )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"type": "manual_parse_error", "line": line}
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _has_behavioral_evidence(manual_dir: Path) -> bool:
    return any(
        path.is_file() and bool(path.read_text(encoding="utf-8").strip())
        for path in (manual_dir / "transcript.jsonl", manual_dir / "commands.jsonl")
    )


__all__ = ["CursorManualDriver"]
