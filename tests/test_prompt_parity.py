"""Byte-parity test for the dual-copy agent prompts.

Each prompt under ``prompts/`` is also bundled inside
``skills/agents-shipgate/prompts/`` so the Claude Code skill ships
self-contained. The two copies must stay byte-identical — they
describe the same workflow and any drift between them creates
silent inconsistency for users who follow one or the other.

Per the v0.7 PR 5 plan: this test fails the moment a future PR
edits one copy without the other. The longer-term fix (per the v3
plan risk list) is to convert one copy into a generated artifact
of the other; until then, byte parity is the contract.

Why both copies exist:
- ``prompts/`` is the canonical surface, exposed in the repo's
  top-level structure for humans/agents discovering the project on
  GitHub.
- ``skills/agents-shipgate/prompts/`` ships inside the Claude Code
  skill so behavior is pinned to the installed version and works
  offline; the skill cannot reach back into the canonical surface
  at runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOP_LEVEL_PROMPTS = REPO_ROOT / "prompts"
SKILL_PROMPTS = REPO_ROOT / "skills" / "agents-shipgate" / "prompts"

# README is intentionally top-level only — describes the directory
# itself, not a workflow to bundle with the skill.
PARITY_EXEMPT = {"README.md"}


def _expected_prompt_names() -> list[str]:
    return sorted(
        path.name
        for path in TOP_LEVEL_PROMPTS.iterdir()
        if path.is_file()
        and path.suffix == ".md"
        and path.name not in PARITY_EXEMPT
    )


def test_skill_bundles_every_top_level_prompt():
    """Every prompt under ``prompts/`` (except README) must also exist
    under the skill bundle. Catches the case where a new prompt is
    added top-level but not mirrored to the skill."""
    expected = set(_expected_prompt_names())
    bundled = {
        path.name
        for path in SKILL_PROMPTS.iterdir()
        if path.is_file() and path.suffix == ".md"
    }
    missing = expected - bundled
    assert not missing, (
        f"Skill bundle is missing prompts that exist in prompts/: "
        f"{sorted(missing)}. Copy them to skills/agents-shipgate/prompts/."
    )


def test_skill_bundle_has_no_extra_prompts():
    """The skill bundle should not contain prompts that don't exist
    top-level. Catches drift in the other direction (prompt added to
    the skill without a top-level canonical copy)."""
    expected = set(_expected_prompt_names())
    bundled = {
        path.name
        for path in SKILL_PROMPTS.iterdir()
        if path.is_file() and path.suffix == ".md"
    }
    extra = bundled - expected
    assert not extra, (
        f"Skill bundle has prompts not present in prompts/: "
        f"{sorted(extra)}. Add the canonical top-level copy or "
        f"remove the bundled file."
    )


@pytest.mark.parametrize("name", _expected_prompt_names())
def test_prompt_byte_identical_across_locations(name: str):
    """Each prompt must be byte-identical between the top-level
    canonical location and the skill bundle copy. Future contributors
    editing only one copy will fail this test in the same PR."""
    top = TOP_LEVEL_PROMPTS / name
    bundled = SKILL_PROMPTS / name
    assert top.is_file(), f"Top-level prompt {top} missing"
    assert bundled.is_file(), f"Bundled prompt {bundled} missing"
    top_bytes = top.read_bytes()
    bundled_bytes = bundled.read_bytes()
    assert top_bytes == bundled_bytes, (
        f"{name} differs between prompts/ and skills/agents-shipgate/prompts/. "
        "These two copies must stay byte-identical (they describe the same "
        "workflow). Update the canonical copy under prompts/ and run "
        "`cp prompts/{name} skills/agents-shipgate/prompts/{name}` to mirror."
    )


def test_add_shipgate_prompt_starts_with_detect_first_flow():
    """The canonical onboarding prompt must lead with the v0.7
    `detect → init --write --ci → scan --suggest-patches → apply-patches`
    flow, not the pre-v0.6 install→init→scan path. Pin this so a
    future edit doesn't accidentally regress to the older flow.
    """
    text = (TOP_LEVEL_PROMPTS / "add-shipgate-to-repo.md").read_text(encoding="utf-8")
    assert "agents-shipgate detect" in text, (
        "add-shipgate-to-repo.md must reference `agents-shipgate detect` "
        "(canonical 4-call flow leads with detection)."
    )
    assert "--suggest-patches" in text, (
        "add-shipgate-to-repo.md must reference `--suggest-patches` "
        "(scan step in the canonical flow)."
    )
    assert "apply-patches" in text, (
        "add-shipgate-to-repo.md must reference `apply-patches` "
        "(safe-fix step in the canonical flow)."
    )
    # Soft-stop rule from the trigger table: do not stop just because
    # is_agent_project is false.
    assert "suggested_sources" in text, (
        "add-shipgate-to-repo.md must mention `suggested_sources` so "
        "agents apply the soft-stop rule (don't skip MCP/OpenAPI-only "
        "repos that surface as is_agent_project: false)."
    )
