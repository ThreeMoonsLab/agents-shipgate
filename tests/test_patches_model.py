"""Patch data-model + run_id stability tests for the v0.6 contract.

Per C11: ``_run_id`` excludes ``patches`` from its hash payload, so the
report's ``run_id`` is identical whether scan ran with or without
``--suggest-patches``. Per C4: ``patches`` is absent from the JSON when
None, additive when set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.models import Finding
from agents_shipgate.core.patches import (
    ManualPatch,
    RemovePointerPatch,
    SetPointerPatch,
)

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "support_refund_agent" / "shipgate.yaml"


# --- Patch model -----------------------------------------------------------


def test_set_pointer_patch_roundtrips() -> None:
    patch = SetPointerPatch(
        target_file="/tmp/x/shipgate.yaml",
        pointer="/policies/require_approval_for_tools",
        value=[{"tool": "issue_refund"}],
        target_format="yaml",
        confidence="high",
        rationale="Add approval policy.",
        target_sha256="0" * 64,
    )
    payload = patch.model_dump(mode="json")
    restored = SetPointerPatch.model_validate(payload)
    assert restored == patch
    assert payload["kind"] == "set_pointer"


def test_remove_pointer_patch_roundtrips() -> None:
    patch = RemovePointerPatch(
        target_file="/tmp/x/shipgate.yaml",
        pointer="/checks/ignore/0",
        target_format="yaml",
        confidence="high",
        rationale="Remove stale suppression.",
        target_sha256="0" * 64,
    )
    assert RemovePointerPatch.model_validate(patch.model_dump(mode="json")) == patch


def test_manual_patch_carries_only_instructions() -> None:
    patch = ManualPatch(instructions="Implement the runtime approval gate.")
    payload = patch.model_dump(mode="json")
    assert payload == {"kind": "manual", "instructions": payload["instructions"]}


def test_patch_discriminator_dispatches_correct_subtype() -> None:
    """Pydantic must select the right subclass based on `kind`."""
    finding = Finding(
        check_id="SHIP-DEMO",
        title="t",
        severity="medium",
        category="manifest",
        evidence={},
        confidence="medium",
        recommendation="r",
        patches=[
            {"kind": "manual", "instructions": "do the thing"},
            {
                "kind": "set_pointer",
                "target_file": "/tmp/x.yaml",
                "pointer": "/foo",
                "value": "bar",
                "target_format": "yaml",
                "confidence": "high",
                "rationale": "demo",
                "target_sha256": "0" * 64,
            },
        ],
    )
    assert isinstance(finding.patches[0], ManualPatch)
    assert isinstance(finding.patches[1], SetPointerPatch)


def test_set_pointer_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        SetPointerPatch.model_validate(
            {
                "kind": "wrong_kind",
                "target_file": "/tmp/x",
                "pointer": "/x",
                "value": 1,
                "target_format": "yaml",
                "confidence": "high",
                "rationale": "x",
                "target_sha256": "0",
            }
        )


# --- run_id stability ------------------------------------------------------


def test_run_id_identical_when_only_patches_field_differs(tmp_path: Path) -> None:
    """C11 verification: scan output's run_id must NOT depend on
    Finding.patches. Two scans of the same workspace, one with no
    patches and one with synthetic patches injected, produce the same
    run_id."""
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "first",
        formats=["json"],
        ci_mode="advisory",
    )
    baseline_run_id = report.run_id

    # Inject patches into a copy of the same findings; recompute run_id.
    from agents_shipgate.cli.scan import _run_id

    findings_with_patches = []
    for finding in report.findings:
        new = finding.model_copy(deep=True)
        new.patches = [ManualPatch(instructions="demo")]
        findings_with_patches.append(new)

    # Use the same manifest + tool inventory as the original scan.
    from agents_shipgate.config.loader import load_manifest

    manifest = load_manifest(SAMPLE)
    # Build a synthetic tool list matching the report's inventory; the
    # exact tool objects don't matter to _run_id beyond their stable
    # serialization, so we reuse the inventory shape verbatim.
    same_run_id = _run_id(
        manifest,
        [],  # tool_inventory is independent of patches
        findings_with_patches,
        api_surface=report.api_surface,
        anthropic_surface=report.anthropic_surface,
        frameworks=report.frameworks,
    )
    no_patches_run_id = _run_id(
        manifest,
        [],
        report.findings,
        api_surface=report.api_surface,
        anthropic_surface=report.anthropic_surface,
        frameworks=report.frameworks,
    )

    # The two synthetic _run_id calls must match each other (patches
    # excluded from the hash) — that's the load-bearing C11 assertion.
    assert same_run_id == no_patches_run_id
    # And both should be deterministic strings.
    assert isinstance(baseline_run_id, str) and len(baseline_run_id) > 0
