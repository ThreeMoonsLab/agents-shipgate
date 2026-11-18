"""Verify task 01 outcome regardless of which runner produced it."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def assert_outcome(workdir: Path) -> None:
    manifest = workdir / "shipgate.yaml"
    assert manifest.is_file(), "shipgate.yaml was not created"
    config = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert "CHANGE_ME" not in yaml.safe_dump(config), (
        "CHANGE_ME placeholders remain in shipgate.yaml; the agent should "
        "have replaced them based on tools.json before scanning."
    )

    assert any(
        source.get("type") == "mcp" and source.get("path") == "tools.json"
        for source in config.get("tool_sources", [])
    ), "expected the MCP export to be declared as an input"

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
