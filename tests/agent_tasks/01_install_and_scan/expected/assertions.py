"""Verify task 01 outcome regardless of which runner produced it."""

from __future__ import annotations

import json
from pathlib import Path


def assert_outcome(workdir: Path) -> None:
    manifest = workdir / "shipgate.yaml"
    assert manifest.is_file(), "shipgate.yaml was not created"
    manifest_text = manifest.read_text(encoding="utf-8")
    assert "CHANGE_ME" not in manifest_text, (
        "CHANGE_ME placeholders remain in shipgate.yaml; the agent should "
        "have replaced them based on tools.json before scanning."
    )

    report = workdir / "agents-shipgate-reports" / "report.json"
    assert report.is_file(), "agents-shipgate-reports/report.json was not produced"
    payload = json.loads(report.read_text(encoding="utf-8"))

    summary = payload.get("summary") or {}
    for field in ("status", "critical_count", "high_count", "medium_count"):
        assert field in summary, f"summary missing required field: {field}"
    assert isinstance(summary["status"], str)

    findings = payload.get("findings") or []
    # The seeded tool list contains a customer-comms write tool with no policy,
    # so we expect at least one finding.
    assert findings, "expected at least one finding for the seeded tool surface"
