"""Codex CLI driver — phase-2 stub.

Phase 2 implementation: ``codex exec --workdir=<workspace>
--json-logs=<artifacts>/codex.ndjson --max-turns=25 < prompt.txt``, normalize
the NDJSON into the standard JSONL streams. Falls back to regex-scraped
rich-text logs with ``degraded=True`` if ``--json-logs`` is unavailable.

This module ships as a stub so the import path is stable for the matrix.yaml
parser. Calling ``run`` raises immediately so cells routed here in v1 fail
fast with a clear message.
"""
from __future__ import annotations

from datetime import UTC, datetime

from harness.adoption.drivers.base import DriverInputs, RunResult
from harness.adoption.observer.transcript import TranscriptWriter


class CodexDriver:
    name = "codex"

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        now = datetime.now(UTC)
        msg = (
            "Codex driver is a v2 stub. Edit matrix.yaml to remove codex cells "
            "or install and wire up the Codex CLI per docs/adoption-harness-automated.md."
        )
        writer.transcript({"type": "driver_error", "message": msg})
        return RunResult(
            started_at=now,
            ended_at=now,
            degraded=True,
            error=msg,
            summary_text="(codex driver not implemented in v1)",
        )


__all__ = ["CodexDriver"]
