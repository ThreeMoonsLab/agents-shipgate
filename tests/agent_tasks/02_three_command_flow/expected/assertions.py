"""Verify task 02 outcome regardless of which runner produced it."""

from __future__ import annotations

import json
from pathlib import Path


def assert_outcome(workdir: Path) -> None:
    manifest = workdir / "shipgate.yaml"
    assert manifest.is_file(), "shipgate.yaml was not created"
    manifest_text = manifest.read_text(encoding="utf-8")
    assert "CHANGE_ME" not in manifest_text, (
        "CHANGE_ME placeholder remains in shipgate.yaml; the agent should "
        "have replaced agent.declared_purpose with a real one-liner."
    )

    workflow = workdir / ".github" / "workflows" / "agents-shipgate.yml"
    assert workflow.is_file(), (
        "Expected .github/workflows/agents-shipgate.yml from `init --ci`."
    )
    assert "ThreeMoonsLab/agents-shipgate" in workflow.read_text(encoding="utf-8")

    report = workdir / "agents-shipgate-reports" / "report.json"
    assert report.is_file(), "agents-shipgate-reports/report.json was not produced"
    payload = json.loads(report.read_text(encoding="utf-8"))

    # v0.6 contract: report carries manifest_dir for the containment check.
    assert payload.get("manifest_dir"), "report missing manifest_dir field"

    summary = payload.get("summary") or {}
    for field in ("status", "critical_count", "high_count", "medium_count"):
        assert field in summary, f"summary missing required field: {field}"

    # Every active finding must have at least one patch (the v0.6
    # coverage rule). Confirms scan ran with --suggest-patches.
    findings = payload.get("findings") or []
    active = [f for f in findings if not f.get("suppressed")]
    if active:
        for finding in active:
            assert finding.get("patches"), (
                f"finding {finding['check_id']} has no patches; agent likely "
                "skipped --suggest-patches"
            )
