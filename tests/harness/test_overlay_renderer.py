"""Overlay renderer tests.

A workspace with unresolved placeholders or missing destination files must
never reach an agent. These tests pin that contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.adoption import context as ctx_mod
from harness.adoption import overlay as overlay_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
VARIANTS_DIR = REPO_ROOT / "benchmark" / "setup-variants"


def test_every_variant_has_overlay_yaml() -> None:
    """Each variant directory under benchmark/setup-variants/ must carry overlay.yaml."""
    variants = [p for p in VARIANTS_DIR.iterdir() if p.is_dir()]
    missing = [v.name for v in variants if not (v / "overlay.yaml").is_file()]
    assert missing == [], (
        f"Variants without overlay.yaml: {missing}. Add one so the renderer can apply them."
    )


def test_40_shipgate_yaml_renders_clean_for_every_archetype(tmp_path: Path) -> None:
    """The 40-shipgate-yaml variant must produce a placeholder-free workspace for every archetype."""
    variant_dir = VARIANTS_DIR / "40-shipgate-yaml"
    for archetype, archetype_ctx in ctx_mod.ARCHETYPE_CONTEXTS.items():
        if archetype == "non-agent-negative-control":
            # No tool surface to render; not part of the matrix's 40 variant.
            continue
        workspace = tmp_path / archetype
        workspace.mkdir(parents=True, exist_ok=True)
        overlay_mod.apply_overlay(
            variant_dir=variant_dir,
            workspace_root=workspace,
            placeholders=archetype_ctx.as_placeholder_map(),
        )
        text = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
        assert "CHANGE_ME" not in text, f"CHANGE_ME survived for {archetype}"
        assert "{{" not in text, f"Unresolved placeholder for {archetype}"


def test_every_archetype_uses_a_valid_tool_source_type() -> None:
    """Each archetype's ``ToolSource.type`` must be a known built-in
    source type (third-party extensions are intentionally NOT used in
    canonical archetypes — the harness ships a fixed set).

    v0.20 PR #111 review fix: ``ToolSourceConfig.type`` relaxed from
    Literal to ``str`` to support third-party adapters; this test now
    pins the archetype set against the explicit
    ``BUILTIN_TOOL_SOURCE_TYPES`` constant. Catches regressions like
    the original ``openai_api`` typo for clean-read-only that would
    otherwise only surface when an operator runs
    ``agents-shipgate doctor`` on a rendered 40-shipgate-yaml manifest.
    """
    from agents_shipgate.schemas.manifest import BUILTIN_TOOL_SOURCE_TYPES

    allowed = set(BUILTIN_TOOL_SOURCE_TYPES)
    for archetype, ctx in ctx_mod.ARCHETYPE_CONTEXTS.items():
        for ts in ctx.tool_sources:
            assert ts.type in allowed, (
                f"{archetype}: tool_source.type={ts.type!r} is not one of {sorted(allowed)}"
            )


def test_missing_required_placeholder_fails_loudly(tmp_path: Path) -> None:
    """Forgetting a required placeholder is a cell failure, not a silent bad render."""
    variant_dir = VARIANTS_DIR / "40-shipgate-yaml"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(overlay_mod.OverlayError):
        overlay_mod.apply_overlay(
            variant_dir=variant_dir,
            workspace_root=workspace,
            placeholders={"REPO_NAME": "x"},  # missing AGENT_NAME, ONE_LINE_PURPOSE, TOOL_SOURCES
        )


def test_negative_overlay_appends_to_readme(tmp_path: Path) -> None:
    """The 60-docs-only-negative overlay appends rather than overwriting README.md."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Original\n", encoding="utf-8")
    overlay_mod.apply_overlay(
        variant_dir=VARIANTS_DIR / "60-docs-only-negative",
        workspace_root=workspace,
        placeholders={},
    )
    text = (workspace / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# Original")
    assert "docs-only" in text.lower()
