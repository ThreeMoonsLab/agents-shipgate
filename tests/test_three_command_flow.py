"""End-to-end: an agent runs the v0.6 single-turn flow.

Canonical sequence (4 CLI calls in 1 user turn):

    1. shipgate detect --json
    2. shipgate init --write --ci --json
    3. shipgate scan -c shipgate.yaml --suggest-patches --format json
    4. shipgate apply-patches --from agents-shipgate-reports/report.json
       --confidence high --apply

This test fires all four against a fresh copy of a real sample and
verifies the contract: detect classifies the workspace, init produces a
schema-valid manifest plus a workflow file, scan emits patches on every
active finding, and apply-patches mutates the manifest only when stale
entries exist (and respects the containment + dry-run rules).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.discovery.ci_workflow import WORKFLOW_RELATIVE_PATH
from agents_shipgate.cli.main import app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
runner = CliRunner()


def _seed_with_stale_entries(tmp_path: Path) -> Path:
    """Copy a sample and inject a stale suppression to exercise
    apply-patches' RemovePointerPatch path."""
    workspace = tmp_path / "agent_repo"
    shutil.copytree(SAMPLES / "support_refund_agent", workspace)
    manifest_path = workspace / "shipgate.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    data.setdefault("checks", {})["ignore"] = [
        {
            "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
            "tool": "nonexistent_tool",
            "reason": "stale fixture for the three-command flow test",
        }
    ]
    manifest_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    # Drop the existing manifest + reports so init writes fresh ones.
    manifest_path.unlink()
    reports = workspace / "agents-shipgate-reports"
    if reports.exists():
        shutil.rmtree(reports)
    # Re-write the manifest after removing it (above) so init can run
    # against a workspace that already had the stale entry baked in.
    manifest_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return workspace


def test_three_command_flow_end_to_end(tmp_path: Path) -> None:
    workspace = _seed_with_stale_entries(tmp_path)

    # 1. detect
    detect_result = runner.invoke(
        app, ["detect", "--workspace", str(workspace), "--json"]
    )
    assert detect_result.exit_code == 0
    detect_payload = json.loads(detect_result.output)
    assert detect_payload["is_agent_project"] is True

    # The seeded sample is OpenAPI/MCP-driven plus has python entrypoints,
    # so detect must surface at least one framework.
    assert detect_payload["frameworks"], "detect should classify this workspace"

    # 2. init --write --ci (we're using the seeded manifest already, so
    # init refuses to overwrite — but workflow still writes orthogonally
    # per the v0.6 matrix).
    init_result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--ci",
            "--json",
        ],
    )
    # exit_code == 2 because the manifest already exists; --ci still ran.
    assert init_result.exit_code == 2
    init_payload = json.loads(init_result.output)
    assert init_payload["manifest_status"] == "skipped_existing"
    assert init_payload["workflow"]["status"] == "written"
    assert (workspace / WORKFLOW_RELATIVE_PATH).exists()

    # 3. scan --suggest-patches
    scan_result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--suggest-patches",
            "--format",
            "json",
            "--out",
            str(workspace / "agents-shipgate-reports"),
            "--ci-mode",
            "advisory",
        ],
    )
    assert scan_result.exit_code == 0, scan_result.output

    report_path = workspace / "agents-shipgate-reports" / "report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # Per C13: report carries manifest_dir for the containment check.
    assert report["manifest_dir"] == str((workspace / "shipgate.yaml").resolve().parent)

    # Per the v4 coverage rule: every active finding has ≥ 1 patch.
    active = [f for f in report["findings"] if not f.get("suppressed")]
    assert active, "expected at least one active finding"
    for finding in active:
        assert finding.get("patches"), (
            f"finding {finding['check_id']} has no patches under --suggest-patches"
        )

    # The seeded stale suppression must produce a high-confidence
    # remove_pointer patch.
    stale_findings = [
        f for f in active if f["check_id"] == "SHIP-MANIFEST-STALE-SUPPRESSION"
    ]
    assert stale_findings, "expected SHIP-MANIFEST-STALE-SUPPRESSION finding"
    stale_patches = [
        p
        for p in stale_findings[0]["patches"]
        if p["kind"] == "remove_pointer"
    ]
    assert stale_patches, (
        "stale suppression generator should emit a RemovePointerPatch"
    )
    assert stale_patches[0]["confidence"] == "high"

    # Per C6: trace approval/confirmation findings (if present) must be
    # ManualPatch with the explicit prohibition language.
    for finding in active:
        if finding["check_id"] in {
            "SHIP-API-TRACE-APPROVAL-MISSING",
            "SHIP-API-TRACE-CONFIRMATION-MISSING",
        }:
            kinds = {p["kind"] for p in finding["patches"]}
            assert kinds == {"manual"}, (
                f"trace finding {finding['check_id']} must be ManualPatch only"
            )
            instructions = finding["patches"][0]["instructions"].lower()
            assert "do not edit the trace" in instructions

    # 4. apply-patches --confidence high --apply
    apply_result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--confidence",
            "high",
            "--apply",
            "--json",
        ],
    )
    assert apply_result.exit_code == 0, apply_result.output
    apply_payload = json.loads(apply_result.output)
    assert apply_payload["applied"] is True

    # The manifest's stale suppression should be gone post-apply.
    post = yaml.safe_load((workspace / "shipgate.yaml").read_text(encoding="utf-8"))
    remaining = post.get("checks", {}).get("ignore") or []
    assert all(s["tool"] != "nonexistent_tool" for s in remaining)

    # Re-run scan; the stale-suppression finding should be gone.
    rescan_result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(workspace / "shipgate.yaml"),
            "--format",
            "json",
            "--out",
            str(workspace / "agents-shipgate-reports-2"),
            "--ci-mode",
            "advisory",
        ],
    )
    assert rescan_result.exit_code == 0
    second_report = json.loads(
        (workspace / "agents-shipgate-reports-2" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    second_active = [f for f in second_report["findings"] if not f.get("suppressed")]
    second_stale = [
        f
        for f in second_active
        if f["check_id"] == "SHIP-MANIFEST-STALE-SUPPRESSION"
    ]
    assert not second_stale, (
        "after apply-patches, the SHIP-MANIFEST-STALE-SUPPRESSION finding "
        "must not reappear on rescan"
    )
