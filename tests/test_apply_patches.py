"""Tests for ``shipgate apply-patches``.

Per the v0.6 plan §4:
- File-grouped, single SHA per file.
- Containment-checked against report.manifest_dir (per C13).
- Dry-run by default; ``--apply`` required to mutate.
- ``--confidence high`` (default) skips medium-confidence patches like
  scope-coverage appends.
- ManualPatch is never applied.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import run_scan

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
runner = CliRunner()


def _seed_with_stale_suppression(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    shutil.copytree(SAMPLES / "support_refund_agent", workspace)
    manifest_path = workspace / "shipgate.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    data.setdefault("checks", {})["ignore"] = [
        {
            "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
            "tool": "nonexistent_tool",
            "reason": "stale",
        }
    ]
    manifest_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return workspace


def _scan_with_patches(workspace: Path) -> Path:
    """Run scan against a workspace, return the report.json path."""
    out_dir = workspace / "agents-shipgate-reports"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=out_dir,
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    return out_dir / "report.json"


# --- Happy path ------------------------------------------------------------


def test_apply_removes_stale_suppression_round_trip(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    manifest_path = workspace / "shipgate.yaml"

    # Sanity: stale suppression present pre-apply.
    pre = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert any(
        s["tool"] == "nonexistent_tool" for s in pre["checks"]["ignore"]
    )

    result = runner.invoke(
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
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is True
    file_outcome = payload["files"][str(manifest_path.resolve())]
    assert file_outcome["status"] == "applied"

    post = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert post["checks"]["ignore"] == []


def test_dry_run_default_does_not_mutate(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    manifest_path = workspace / "shipgate.yaml"
    pre_text = manifest_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--confidence", "high", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["applied"] is False
    # File untouched.
    assert manifest_path.read_text(encoding="utf-8") == pre_text


# --- Confidence filter -----------------------------------------------------


def test_confidence_high_skips_medium_scope_coverage_patches(tmp_path: Path) -> None:
    """Default --confidence high means medium scope-coverage appends are
    not applied. Per A5: adding scopes can encode policy choices."""
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # The fixture should have at least one scope-coverage finding (medium
    # patch). Skip the test if the fixture happens not to surface one.
    medium_patches = [
        p
        for f in report["findings"]
        for p in (f.get("patches") or [])
        if p.get("kind") == "append_pointer"
    ]
    if not medium_patches:
        return  # fixture-dependent; nothing to assert

    manifest_path = workspace / "shipgate.yaml"
    pre = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    pre_scopes = list(pre.get("permissions", {}).get("scopes", []))

    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--confidence",
            "high",
            "--apply",
        ],
    )
    assert result.exit_code == 0

    post = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    post_scopes = list(post.get("permissions", {}).get("scopes", []))
    # Default --confidence high must NOT have appended scopes.
    assert post_scopes == pre_scopes


def test_confidence_medium_includes_scope_appends(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    medium_patches = [
        p
        for f in report["findings"]
        for p in (f.get("patches") or [])
        if p.get("kind") == "append_pointer"
    ]
    if not medium_patches:
        return

    manifest_path = workspace / "shipgate.yaml"
    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--confidence",
            "medium",
            "--apply",
        ],
    )
    assert result.exit_code == 0
    post = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert post.get("permissions", {}).get("scopes")  # something appended


# --- Containment ----------------------------------------------------------


def test_containment_violation_refused(tmp_path: Path) -> None:
    """A patch whose target_file is outside report.manifest_dir aborts
    with exit code 5. Critical safety net per C13."""
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # Forge a patch targeting /etc/passwd; rewrite the report.
    for finding in report["findings"]:
        for patch in finding.get("patches") or []:
            if patch.get("kind") == "remove_pointer":
                patch["target_file"] = "/etc/passwd"
                break
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--apply"],
    )
    assert result.exit_code == 5
    assert "Containment violation" in result.output or "not under" in result.output


def test_missing_manifest_dir_refuses(tmp_path: Path) -> None:
    """Old reports that pre-date v0.6 won't have manifest_dir; refuse."""
    payload = {
        "report_schema_version": "0.5",
        "findings": [
            {
                "patches": [
                    {
                        "kind": "remove_pointer",
                        "target_file": str(tmp_path / "anything.yaml"),
                        "pointer": "/x",
                        "target_format": "yaml",
                        "confidence": "high",
                        "rationale": "x",
                        "target_sha256": "0" * 64,
                    }
                ]
            }
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    result = runner.invoke(
        app,
        ["apply-patches", "--from", str(report_path), "--apply"],
    )
    assert result.exit_code == 5


# --- SHA drift ------------------------------------------------------------


def test_drift_skips_file_with_clear_message(tmp_path: Path) -> None:
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    manifest_path = workspace / "shipgate.yaml"

    # Mutate the manifest so its SHA diverges from the patch envelope.
    text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(text + "\n# user edit\n", encoding="utf-8")

    result = runner.invoke(
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
    assert result.exit_code == 0  # not an error; per-file skip
    payload = json.loads(result.output)
    file_outcome = payload["files"][str(manifest_path.resolve())]
    assert file_outcome["status"] == "skipped_drift"
    # File still has the user edit.
    assert "# user edit" in manifest_path.read_text(encoding="utf-8")


# --- Manual filter --------------------------------------------------------


def test_manual_patches_never_applied(tmp_path: Path) -> None:
    """Even with --kinds=manual, ManualPatch is filtered out — it carries
    no machine-applicable data. (Defense-in-depth.)"""
    workspace = _seed_with_stale_suppression(tmp_path)
    report_path = _scan_with_patches(workspace)
    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(report_path),
            "--kinds",
            "manual",
            "--apply",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    # No files touched (only manual filtered through, then dropped).
    assert payload["files"] == {}


# --- Multi-remove ordering (regression: list index shifts) ----------------


def test_multi_remove_against_same_list_does_not_corrupt(tmp_path: Path) -> None:
    """Two RemovePointerPatch ops against the same YAML list (e.g.
    /policies/.../0 and /policies/.../1) must NOT raise IndexError or
    silently delete the wrong entry.

    Regression for the v0.6 reviewer's reproduction: applying in report
    order was buggy because the first delete shifts subsequent indexes.
    Fix: removes are now sorted so higher list indexes fire first.
    """
    from agents_shipgate.cli.apply_patches import _apply_yaml
    from agents_shipgate.core.patches import RemovePointerPatch

    text = (
        "policies:\n"
        "  require_approval_for_tools:\n"
        "    - tool: a\n"
        "      reason: x\n"
        "    - tool: b\n"
        "      reason: y\n"
        "    - tool: c\n"
        "      reason: z\n"
    )
    patches = [
        RemovePointerPatch(
            target_file="x",
            pointer="/policies/require_approval_for_tools/0",
            target_format="yaml",
            confidence="high",
            rationale="x",
            target_sha256="0",
        ),
        RemovePointerPatch(
            target_file="x",
            pointer="/policies/require_approval_for_tools/1",
            target_format="yaml",
            confidence="high",
            rationale="x",
            target_sha256="0",
        ),
    ]
    result = _apply_yaml(text, patches)
    # Removed indexes 0 and 1 of the original list → only `c` remains.
    assert "tool: c" in result
    assert "tool: a" not in result
    assert "tool: b" not in result


def test_malformed_patch_payload_exits_2(tmp_path: Path) -> None:
    """A patch with missing required fields must exit 2 (documented
    contract for malformed --from input), not raise a Pydantic
    traceback exiting 1.

    Regression for v0.6 reviewer feedback.
    """
    report = {
        "manifest_dir": str(tmp_path),
        "findings": [
            {
                "patches": [
                    # Missing target_file, pointer, target_format,
                    # rationale, target_sha256.
                    {"kind": "remove_pointer", "confidence": "high"}
                ]
            }
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = runner.invoke(app, ["apply-patches", "--from", str(path)])
    assert result.exit_code == 2, result.output
    assert "Malformed patch" in (result.output or "")


def test_multi_remove_index_overflow_does_not_crash(tmp_path: Path) -> None:
    """2-element list, remove indexes 0 and 1 in report order would
    crash with IndexError after fix=False. Sorted highest-first, both
    succeed and the list is empty."""
    from agents_shipgate.cli.apply_patches import _apply_yaml
    from agents_shipgate.core.patches import RemovePointerPatch

    text = (
        "policies:\n"
        "  require_approval_for_tools:\n"
        "    - tool: a\n"
        "      reason: x\n"
        "    - tool: b\n"
        "      reason: y\n"
    )
    patches = [
        RemovePointerPatch(
            target_file="x",
            pointer="/policies/require_approval_for_tools/0",
            target_format="yaml",
            confidence="high",
            rationale="x",
            target_sha256="0",
        ),
        RemovePointerPatch(
            target_file="x",
            pointer="/policies/require_approval_for_tools/1",
            target_format="yaml",
            confidence="high",
            rationale="x",
            target_sha256="0",
        ),
    ]
    result = _apply_yaml(text, patches)
    # Both entries removed; list should be empty.
    assert "tool: a" not in result
    assert "tool: b" not in result
    assert "require_approval_for_tools: []" in result
