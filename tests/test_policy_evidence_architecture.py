"""Static guards against reintroducing parallel evidence inference."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_semantic_consumers_use_typed_policy_eligibility() -> None:
    action_surface = _source("src/agents_shipgate/core/lenses/action_surface.py")
    side_effects = _source("src/agents_shipgate/checks/side_effects.py")
    mcp_audit = _source("src/agents_shipgate/cli/mcp.py")

    assert "claim.policy_eligible" in action_surface
    assert "claim.policy_eligible" in side_effects
    assert "claim.policy_eligible" in mcp_audit
    assert "claim.provenance_kind not in" not in action_surface
    assert "claim.provenance_kind not in" not in mcp_audit


def test_policy_rule_metadata_cannot_upgrade_support() -> None:
    policy_loader = _source("src/agents_shipgate/inputs/policy_packs.py")

    assert "requested_confidence=rule.confidence" in policy_loader
    assert "blocks_release=rule.block and support.blocking_eligible" in policy_loader
    assert re.search(r"(?<!requested_)confidence=rule\.confidence,", policy_loader) is None
    assert re.search(r"blocks_release=rule\.block,(?! and)", policy_loader) is None


def test_free_form_hint_provenance_inference_stays_deleted() -> None:
    resolver = _source("src/agents_shipgate/core/semantic_assessment.py")

    assert "def _hint_provenance" not in resolver
    assert "_validated_hint_basis" in resolver
