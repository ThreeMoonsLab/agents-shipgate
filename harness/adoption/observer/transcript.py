"""Per-cell transcript writers.

Three append-only JSONL streams capture everything a driver produces:

* ``transcript.jsonl`` — chronological event stream (assistant message,
  tool-use block, tool-result block, error). Detectors search this for
  Shipgate mentions and final summaries.
* ``commands.jsonl`` — one row per Bash invocation by the agent. Detectors
  grep this for ``agents-shipgate <verb>`` patterns.
* ``file_ops.jsonl`` — one row per file read/write/edit. Detectors check
  whether the agent ever read ``agents-shipgate-reports/report.json``.

Writers are intentionally tiny — they exist so drivers don't reinvent JSONL
serialization with subtly different shapes.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TranscriptWriter:
    """Append-only JSONL writers for one cell.

    Open a single ``TranscriptWriter`` per cell, pass it to the driver, and
    close it once the driver finishes.
    """

    def __init__(self, raw_dir: Path) -> None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        self._transcript = (raw_dir / "transcript.jsonl").open("a", encoding="utf-8")
        self._commands = (raw_dir / "commands.jsonl").open("a", encoding="utf-8")
        self._file_ops = (raw_dir / "file_ops.jsonl").open("a", encoding="utf-8")

    def transcript(self, event: dict[str, Any]) -> None:
        self._write(self._transcript, event)

    def command(self, command: str, *, exit_code: int | None = None, output: str | None = None) -> None:
        self._write(
            self._commands,
            {"command": command, "exit_code": exit_code, "output": output},
        )

    def file_op(self, op: str, path: str, *, detail: str | None = None) -> None:
        self._write(self._file_ops, {"op": op, "path": path, "detail": detail})

    def close(self) -> None:
        for handle in (self._transcript, self._commands, self._file_ops):
            try:
                handle.close()
            except Exception:
                pass

    def __enter__(self) -> TranscriptWriter:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    @staticmethod
    def _write(handle: Any, payload: dict[str, Any]) -> None:
        payload = {"ts": datetime.now(UTC).isoformat(), **payload}
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


__all__ = ["TranscriptWriter"]
