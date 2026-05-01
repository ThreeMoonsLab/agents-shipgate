"""Verify task 02 outcome regardless of which runner produced it."""

from __future__ import annotations

import json
from pathlib import Path


def assert_outcome(workdir: Path) -> None:
    manifest = workdir / "shipgate.yaml"
    assert manifest.is_file(), "shipgate.yaml was not created"
    # Parse the YAML so we only flag CHANGE_ME in actual values — the
    # auto-init template embeds the literal in comments ("replace
    # CHANGE_ME with …") which is informational, not a placeholder.
    import yaml as _yaml

    data = _yaml.safe_load(manifest.read_text(encoding="utf-8"))
    leftover = _find_change_me(data)
    assert not leftover, (
        f"CHANGE_ME placeholder remains in shipgate.yaml at {leftover!r}; "
        "the agent should have replaced every CHANGE_ME value (including "
        "agent.name when no Agent(name=…) literal was detected)."
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


def _find_change_me(node, path: str = "$") -> str | None:
    """Walk a parsed YAML tree and return the JSON-pointer-ish path of
    the first CHANGE_ME value, or None when none remain."""
    if isinstance(node, str):
        if node == "CHANGE_ME":
            return path
        return None
    if isinstance(node, list):
        for i, item in enumerate(node):
            hit = _find_change_me(item, f"{path}[{i}]")
            if hit:
                return hit
        return None
    if isinstance(node, dict):
        for key, value in node.items():
            hit = _find_change_me(value, f"{path}.{key}")
            if hit:
                return hit
        return None
    return None
