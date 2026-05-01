from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.core.models import ReadinessReport


def report_json_payload(report: ReadinessReport) -> dict[str, Any]:
    """Canonical JSON-serializable shape for a ReadinessReport.

    Use this in writers AND tests so they validate the same shape.
    ``report.model_dump(...)`` will still include ``patches: None`` for
    every finding because Pydantic v2 ``field_serializer`` does not
    cleanly omit keys; we strip the key here.

    Per C4 (v0.6 plan): preserves byte-equivalence of the JSON for
    callers that did not run scan with ``--suggest-patches``.
    """
    data = report.model_dump(mode="json", exclude_none=False)
    for finding in data.get("findings", []):
        if finding.get("patches") is None:
            finding.pop("patches", None)
    return data


def write_json_report(report: ReadinessReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report_json_payload(report), indent=2),
        encoding="utf-8",
    )
