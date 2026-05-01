"""Tests for the v0.6 patch generator registry in checks/patches.py.

Coverage:
- Stale-manifest checks emit RemovePointerPatch with correct rederived
  pointers (per C10).
- Duplicate manifest entries fall back to ManualPatch (auto-removal is
  ambiguous).
- Scope-coverage emits AppendPointerPatch at MEDIUM confidence (per
  user feedback A5: ``apply --confidence high`` deliberately skips it).
- Trace approval/confirmation findings get ManualPatch with the explicit
  prohibition language (per C6: never auto-flip evidence).
- Suppressed findings are not handed to generators by ``_attach_patches``
  (validated separately via scan).
- Default fallback uses CheckMetadata.recommendation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from agents_shipgate.checks.patches import (
    PatchContext,
    generate_patches_for_finding,
)
from agents_shipgate.checks.registry import check_catalog
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.models import Finding
from agents_shipgate.core.patches import (
    AppendPointerPatch,
    ManualPatch,
    RemovePointerPatch,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _manifest_with(tmp_path: Path, body: dict) -> tuple[Path, PatchContext]:
    """Write a minimal valid shipgate.yaml plus the requested top-level
    overrides, return ``(manifest_path, PatchContext)``."""
    base = {
        "version": "0.1",
        "project": {"name": "test"},
        "agent": {"name": "test", "declared_purpose": ["test"]},
        "environment": {"target": "local"},
        "tool_sources": [
            {"id": "fake", "type": "openapi", "path": "spec.yaml"},
        ],
    }
    base.update(body)
    target = tmp_path / "shipgate.yaml"
    target.write_text(yaml.safe_dump(base), encoding="utf-8")
    manifest = load_manifest(target)
    lookup = {
        check.id: check.recommendation
        for check in check_catalog()
        if check.recommendation
    }
    context = PatchContext(
        manifest=manifest,
        manifest_path=target,
        recommendation_lookup=lookup,
    )
    return target, context


def _finding(check_id: str, evidence: dict) -> Finding:
    return Finding(
        check_id=check_id,
        title="t",
        severity="medium",
        category="manifest",
        evidence=evidence,
        confidence="high",
        recommendation="manual fallback recommendation",
    )


# --- Stale-manifest removal generators -------------------------------------


def test_stale_suppression_emits_remove_pointer(tmp_path: Path) -> None:
    _, context = _manifest_with(
        tmp_path,
        {
            "checks": {
                "ignore": [
                    {
                        "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
                        "tool": "missing_tool",
                        "reason": "no longer present",
                    },
                ]
            },
        },
    )
    finding = _finding(
        "SHIP-MANIFEST-STALE-SUPPRESSION",
        {
            "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
            "tool": "missing_tool",
            "issues": ["missing_tool"],
        },
    )
    patches = generate_patches_for_finding(context, finding)
    assert len(patches) == 1
    assert isinstance(patches[0], RemovePointerPatch)
    assert patches[0].pointer == "/checks/ignore/0"
    assert patches[0].confidence == "high"
    assert patches[0].target_format == "yaml"
    assert patches[0].target_sha256  # populated


def test_stale_suppression_duplicate_falls_back_to_manual(tmp_path: Path) -> None:
    """Two suppressions with the same check_id+tool → ambiguous which to
    remove → ManualPatch (per C10 duplicate-handling rule)."""
    _, context = _manifest_with(
        tmp_path,
        {
            "checks": {
                "ignore": [
                    {
                        "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
                        "tool": "dup",
                        "reason": "first",
                    },
                    {
                        "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
                        "tool": "dup",
                        "reason": "second",
                    },
                ]
            },
        },
    )
    finding = _finding(
        "SHIP-MANIFEST-STALE-SUPPRESSION",
        {
            "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
            "tool": "dup",
            "issues": ["missing_tool"],
        },
    )
    patches = generate_patches_for_finding(context, finding)
    assert len(patches) == 1
    assert isinstance(patches[0], ManualPatch)


def test_stale_policy_emits_remove_pointer(tmp_path: Path) -> None:
    _, context = _manifest_with(
        tmp_path,
        {
            "policies": {
                "require_approval_for_tools": [
                    {"tool": "old_tool", "reason": "obsolete"},
                ],
            },
        },
    )
    finding = _finding(
        "SHIP-MANIFEST-STALE-POLICY",
        {"policy": "approval", "tool": "old_tool"},
    )
    patches = generate_patches_for_finding(context, finding)
    assert len(patches) == 1
    assert isinstance(patches[0], RemovePointerPatch)
    assert patches[0].pointer == "/policies/require_approval_for_tools/0"


def test_stale_risk_override_emits_remove_pointer(tmp_path: Path) -> None:
    _, context = _manifest_with(
        tmp_path,
        {
            "risk_overrides": {
                "tools": {
                    "deleted_tool": {"owner": "team-foo", "reason": "audit"},
                },
            },
        },
    )
    finding = _finding(
        "SHIP-MANIFEST-STALE-RISK-OVERRIDE",
        {"tool": "deleted_tool"},
    )
    patches = generate_patches_for_finding(context, finding)
    assert len(patches) == 1
    assert isinstance(patches[0], RemovePointerPatch)
    assert patches[0].pointer == "/risk_overrides/tools/deleted_tool"


def test_stale_risk_override_escapes_pointer_chars(tmp_path: Path) -> None:
    """Tool names containing `/` or `~` need RFC 6901 pointer escaping."""
    _, context = _manifest_with(
        tmp_path,
        {
            "risk_overrides": {
                "tools": {
                    "weird/name": {"owner": "team-foo", "reason": "audit"},
                },
            },
        },
    )
    finding = _finding(
        "SHIP-MANIFEST-STALE-RISK-OVERRIDE",
        {"tool": "weird/name"},
    )
    patches = generate_patches_for_finding(context, finding)
    assert isinstance(patches[0], RemovePointerPatch)
    assert patches[0].pointer == "/risk_overrides/tools/weird~1name"


# --- Scope coverage --------------------------------------------------------


def test_scope_coverage_emits_medium_append_per_missing_scope(tmp_path: Path) -> None:
    _, context = _manifest_with(tmp_path, {})
    finding = _finding(
        "SHIP-AUTH-SCOPE-COVERAGE-MISSING",
        {
            "tool_scopes": ["a", "b", "c"],
            "manifest_scopes": ["a"],
            "missing_scopes": ["b", "c"],
        },
    )
    patches = generate_patches_for_finding(context, finding)
    assert len(patches) == 2
    for patch in patches:
        assert isinstance(patch, AppendPointerPatch)
        assert patch.pointer == "/permissions/scopes"
        # Critical: medium confidence, NOT high. apply --confidence high
        # (the default) deliberately skips these.
        assert patch.confidence == "medium"
    assert {patch.value for patch in patches} == {"b", "c"}


def test_scope_coverage_with_no_missing_scopes_falls_back(tmp_path: Path) -> None:
    _, context = _manifest_with(tmp_path, {})
    finding = _finding(
        "SHIP-AUTH-SCOPE-COVERAGE-MISSING",
        {"tool_scopes": [], "manifest_scopes": [], "missing_scopes": []},
    )
    patches = generate_patches_for_finding(context, finding)
    assert isinstance(patches[0], ManualPatch)


# --- Trace findings: PERMANENT manual (per C6) -----------------------------


def test_trace_approval_missing_is_permanent_manual(tmp_path: Path) -> None:
    _, context = _manifest_with(tmp_path, {})
    finding = _finding(
        "SHIP-API-TRACE-APPROVAL-MISSING",
        {"tool_name": "issue_refund", "approved": False},
    )
    patches = generate_patches_for_finding(context, finding)
    assert len(patches) == 1
    assert isinstance(patches[0], ManualPatch)
    # Critical: instructions must explicitly forbid editing the trace.
    assert "do not edit the trace" in patches[0].instructions.lower()


def test_trace_confirmation_missing_is_permanent_manual(tmp_path: Path) -> None:
    _, context = _manifest_with(tmp_path, {})
    finding = _finding(
        "SHIP-API-TRACE-CONFIRMATION-MISSING",
        {"tool_name": "send_email", "confirmed": False},
    )
    patches = generate_patches_for_finding(context, finding)
    assert isinstance(patches[0], ManualPatch)
    assert "do not edit the trace" in patches[0].instructions.lower()


# --- Default fallback -------------------------------------------------------


def test_unknown_check_id_returns_manual_with_recommendation_text(tmp_path: Path) -> None:
    _, context = _manifest_with(tmp_path, {})
    finding = _finding(
        "SHIP-UNKNOWN",
        {},
    )
    patches = generate_patches_for_finding(context, finding)
    assert isinstance(patches[0], ManualPatch)
    # Falls back to finding.recommendation when CheckMetadata has no entry.
    assert patches[0].instructions == "manual fallback recommendation"


# --- End-to-end through scan ------------------------------------------------


def test_scan_with_suggest_patches_attaches_to_every_active_finding(tmp_path: Path) -> None:
    sample = SAMPLES / "support_refund_agent" / "shipgate.yaml"
    report, _ = run_scan(
        config_path=sample,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    active = [f for f in report.findings if not f.suppressed]
    assert active, "expected at least one active finding in this sample"
    for finding in active:
        assert finding.patches is not None
        assert len(finding.patches) >= 1


def test_scan_without_suggest_patches_keeps_patches_none(tmp_path: Path) -> None:
    sample = SAMPLES / "support_refund_agent" / "shipgate.yaml"
    report, _ = run_scan(
        config_path=sample,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    for finding in report.findings:
        assert finding.patches is None


def test_scan_skips_suppressed_findings(tmp_path: Path) -> None:
    """Per the v4 should-fix: generators run only on unsuppressed
    findings. Suppressed findings keep ``patches=None``."""
    src = SAMPLES / "support_refund_agent"
    workspace = tmp_path / "ws"
    shutil.copytree(src, workspace)

    # Add a suppression for whatever check fires first; we'll find it dynamically.
    initial, _ = run_scan(
        config_path=src / "shipgate.yaml",
        output_dir=tmp_path / "first",
        formats=["json"],
        ci_mode="advisory",
    )
    target_check = initial.findings[0].check_id
    target_tool = initial.findings[0].tool_name

    manifest_path = workspace / "shipgate.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_data.setdefault("checks", {})["ignore"] = [
        {
            "check_id": target_check,
            "tool": target_tool,
            "reason": "test",
        }
    ]
    manifest_path.write_text(yaml.safe_dump(manifest_data), encoding="utf-8")

    report, _ = run_scan(
        config_path=manifest_path,
        output_dir=tmp_path / "second",
        formats=["json"],
        ci_mode="advisory",
        suggest_patches=True,
    )
    suppressed = [f for f in report.findings if f.suppressed]
    assert suppressed, "expected at least one suppressed finding"
    for finding in suppressed:
        assert finding.patches is None
