"""#428 — declaration changes are a first-class reviewer surface."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from agents_shipgate.ci.github_summary import write_github_step_summary
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.verify.git import _MAX_MANIFEST_BYTES
from agents_shipgate.cli.verify.orchestrator import (
    _configured_gate_introduced,
    _derive_verifier_control,
    _manifest_introduced,
    run_verify,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.lenses.declaration_surface import (
    build_action_declaration_facts,
    build_declaration_review,
)
from agents_shipgate.packet.builder import build_packet_from_report
from agents_shipgate.packet.html import render_packet_html
from agents_shipgate.packet.markdown import render_packet_markdown
from agents_shipgate.report.declaration_review import declaration_review_lines
from agents_shipgate.report.markdown import render_markdown_report
from agents_shipgate.report.pr_comment import render_pr_comment
from agents_shipgate.report.pr_projection import select_pr_items
from agents_shipgate.schemas.human_authorization import AuthorizationEvaluationV1
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import (
    AcknowledgedEffectOverride,
    DeclarationReviewDecision,
    DeclarationReviewRow,
    DeclarationReviewSummary,
    EvidenceGap,
    EvidenceGapAction,
)
from agents_shipgate.schemas.semantic import (
    AuthoritySemanticEvidence,
    EffectSemanticEvidence,
    SemanticClaimEvidence,
    SemanticIssueEvidence,
    ToolSemanticEvidence,
)
from agents_shipgate.schemas.surfaces import (
    ActionDeclarationFacts,
    ActionFact,
    ActionSurfaceFacts,
    ActionSurfaceHashes,
)
from agents_shipgate.schemas.verification import VerificationContext
from agents_shipgate.schemas.verifier import (
    VerifierArtifact,
    VerifierCapabilityReview,
    VerifierDiffStatus,
    map_merge_verdict,
)


def _manifest(actions: list[dict[str, object]]) -> AgentsShipgateManifest:
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "declaration-review"},
            "agent": {"name": "reviewer", "declared_purpose": ["test"]},
            "environment": {"target": "local"},
            "tool_sources": [{"id": "api", "type": "openapi", "path": "openapi.json"}],
            "action_surface": {"actions": actions},
        }
    )


def _tool(name: str) -> Tool:
    return Tool.model_validate(
        {
            "id": f"tool:{name}",
            "name": name,
            "source_type": "openapi",
            "source_id": "api",
            "provider": "api",
            "extraction_confidence": "high",
        }
    )


def _action(
    name: str,
    *,
    value: str,
    basis: str,
    policy_eligible: bool,
    status: str,
) -> ActionFact:
    return _action_with_readings(
        name,
        readings=[(value, basis, policy_eligible)],
        status=status,
    )


def _action_with_readings(
    name: str,
    *,
    readings: list[tuple[str, str, bool]],
    status: str = "structural",
) -> ActionFact:
    claims = [
        SemanticClaimEvidence(
            dimension="effect",
            value=value,
            confidence="high",
            provenance_kind="static_declaration",
            basis=basis,
            policy_eligible=policy_eligible,
            source=f"source:{name}:{index}",
        )
        for index, (value, basis, policy_eligible) in enumerate(readings)
    ]
    semantic = ToolSemanticEvidence(
        conservative_effect=readings[-1][0],
        effect=EffectSemanticEvidence(status=status, confidence="high", claims=claims),
        authority=AuthoritySemanticEvidence(status="structural", mode="none"),
        pass_eligible=True,
    )
    return ActionFact(
        action_id=f"action:{name}",
        agent_id="agent:test",
        tool_id=f"tool:{name}",
        tool_name=name,
        provider="api",
        source_type="openapi",
        source_id="api",
        operation=name,
        effect=readings[-1][0],
        semantic_assessment=semantic,
        input_schema_hash="schema",
        hashes=ActionSurfaceHashes(
            identity_hash=f"identity:{name}",
            schema_hash="schema",
            policy_hash="policy",
            risk_hash=f"risk:{name}",
        ),
    )


def test_snapshot_ignores_order_comments_and_basis_only_changes() -> None:
    tools = [_tool("alpha"), _tool("beta")]
    base = build_action_declaration_facts(
        _manifest(
            [
                {
                    "tool": "alpha",
                    "effect": "write",
                    "risk_tags": ["writes_data", "network_access"],
                    "basis": "confirmed:" + "a" * 64,
                    "scopes": ["old:scope"],
                    "authority": {"mode": "scoped", "auth_type": "oauth2"},
                    "approval": {"required": False},
                    "safeguards": {"audit_log": False},
                    "evidence": {"owner": "old-owner"},
                },
                {"tool": "beta", "effect": "read"},
            ]
        ),
        tools,
    )
    reordered = build_action_declaration_facts(
        _manifest(
            [
                {"tool": "beta", "effect": "read"},
                {
                    "tool": "alpha",
                    "effect": "write",
                    # ``write`` is an alias for the same mapped effect and
                    # ``customer_data`` is not an effect answer.  Neither the
                    # alias swap nor the non-effect tag edit is a proposal
                    # change.
                    "risk_tags": ["customer_data", "write"],
                    "basis": "confirmed:" + "b" * 64,
                    "scopes": [],
                    "authority": {"mode": "ambient", "reason": "test runtime"},
                    "approval": {"required": True},
                    "safeguards": {"audit_log": True},
                    "evidence": {"owner": "new-owner"},
                },
            ]
        ),
        tools,
    )

    assert [(row.row_id, row.declaration_hash) for row in base.rows] == [
        (row.row_id, row.declaration_hash) for row in reordered.rows
    ]
    assert (
        build_declaration_review(
            head=reordered,
            base=base,
            action_surface_facts=ActionSurfaceFacts(),
            evidence_gaps=[],
            acknowledged_overrides=[],
        ).changed_count
        == 0
    )


def test_added_non_effect_row_is_unverified_but_existing_non_effect_edits_are_silent() -> None:
    tool = _tool("alpha")
    empty = ActionDeclarationFacts()
    non_effect = build_action_declaration_facts(
        _manifest([{"tool": "alpha", "risk_tags": ["network_access"]}]),
        [tool],
    )
    added = build_declaration_review(
        head=non_effect,
        base=empty,
        action_surface_facts=ActionSurfaceFacts(
            actions=[
                _action(
                    "alpha",
                    value="write",
                    basis="typed_provider_fact",
                    policy_eligible=True,
                    status="structural",
                )
            ]
        ),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )
    assert added.changed_count == 1
    assert added.rows[0].bucket == "unverified"
    assert "no effect-bearing proposal" in added.rows[0].reason

    changed_non_effect_tag = build_action_declaration_facts(
        _manifest([{"tool": "alpha", "risk_tags": ["customer_data"]}]),
        [tool],
    )
    assert changed_non_effect_tag.rows[0].declaration_hash == non_effect.rows[0].declaration_hash
    assert (
        build_declaration_review(
            head=changed_non_effect_tag,
            base=non_effect,
            action_surface_facts=ActionSurfaceFacts(),
            evidence_gaps=[],
            acknowledged_overrides=[],
        ).changed_count
        == 0
    )


def test_alias_selectors_for_one_canonical_subject_fail_closed() -> None:
    tool = _tool("alpha")
    head = build_action_declaration_facts(
        _manifest(
            [
                {"tool": "alpha", "effect": "write"},
                {"tool": "alpha", "tool_id": "tool:alpha", "effect": "write"},
            ]
        ),
        [tool],
    )
    assert {row.resolution for row in head.rows} == {"ambiguous"}

    review = build_declaration_review(
        head=head,
        base=ActionDeclarationFacts(),
        action_surface_facts=ActionSurfaceFacts(
            actions=[
                _action(
                    "alpha",
                    value="write",
                    basis="typed_provider_fact",
                    policy_eligible=True,
                    status="structural",
                )
            ]
        ),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )
    assert review.changed_count == 2
    assert review.summary.unverified == 2
    assert all(row.bucket == "unverified" for row in review.rows)
    assert all("ambiguous" in row.reason for row in review.rows)


def test_row_local_incomparable_effect_set_passes_and_removal_fails() -> None:
    tool = _tool("transfer")
    complete = build_action_declaration_facts(
        _manifest(
            [
                {
                    "tool": "transfer",
                    "effect": "financial_write",
                    "risk_tags": ["external_communication"],
                }
            ]
        ),
        [tool],
    )
    action = _action_with_readings(
        "transfer",
        readings=[
            ("external_communication", "typed_provider_fact", True),
            ("financial_write", "typed_provider_fact", True),
        ],
    )
    added = build_declaration_review(
        head=complete,
        base=ActionDeclarationFacts(),
        action_surface_facts=ActionSurfaceFacts(actions=[action]),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )
    assert added.rows[0].bucket == "evidence_consistent"

    missing_incomparable_effect = build_action_declaration_facts(
        _manifest([{"tool": "transfer", "effect": "financial_write"}]),
        [tool],
    )
    removed = build_declaration_review(
        head=missing_incomparable_effect,
        base=complete,
        action_surface_facts=ActionSurfaceFacts(actions=[action]),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )
    assert removed.changed_count == 1
    assert removed.rows[0].bucket == "unverified"
    assert "external_communication" in removed.rows[0].reason


def test_removed_declaration_is_unverified_on_every_review_surface() -> None:
    base = build_action_declaration_facts(
        _manifest([{"tool": "alpha", "effect": "write"}]),
        [_tool("alpha")],
    )
    review = build_declaration_review(
        head=ActionDeclarationFacts(),
        base=base,
        action_surface_facts=ActionSurfaceFacts(),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )

    assert review.changed_count == 1
    row = review.rows[0]
    assert row.change_type == "removed"
    assert row.bucket == "unverified"
    assert "removed" in row.reason
    assert "Unverified declaration" in "\n".join(declaration_review_lines(review))

    items = select_pr_items(
        {
            "release_decision": {
                "evidence_coverage": {
                    "semantic_coverage": {
                        "declaration_review": review.model_dump(mode="json"),
                        "acknowledged_overrides": [],
                    }
                }
            }
        }
    )
    assert [item.check_id for item in items] == [
        "SHIP-ACTION-DECLARATION-UNVERIFIED"
    ]
    assert "removed" in items[0].title
    assert items[0].selector == base.rows[0].manifest_path


@pytest.mark.parametrize(
    "kind",
    [
        "unattested_surface",
        "conflicting_tool_identity",
        "ambiguous_tool_selector",
        "incomplete_tool_identity",
    ],
)
def test_identity_gaps_block_machine_verified_declaration(kind: str) -> None:
    head = build_action_declaration_facts(
        _manifest([{"tool": "alpha", "effect": "write"}]),
        [_tool("alpha")],
    )
    gap = EvidenceGap(
        kind=kind,  # type: ignore[arg-type]
        subject="alpha [api]",
        subject_id="tool:alpha",
        why="identity is not proven",
        next_action=EvidenceGapAction(
            kind="provide_source",
            why="identity is not proven",
            expects="Provide canonical identity evidence and rerun verification.",
        ),
    )
    review = build_declaration_review(
        head=head,
        base=ActionDeclarationFacts(),
        action_surface_facts=ActionSurfaceFacts(
            actions=[
                _action(
                    "alpha",
                    value="write",
                    basis="typed_provider_fact",
                    policy_eligible=True,
                    status="structural",
                )
            ]
        ),
        evidence_gaps=[gap],
        acknowledged_overrides=[],
    )
    assert review.rows[0].bucket == "unverified"
    assert kind in review.rows[0].reason


def test_effect_issue_blocks_machine_verified_declaration() -> None:
    head = build_action_declaration_facts(
        _manifest([{"tool": "alpha", "effect": "write"}]),
        [_tool("alpha")],
    )
    action = _action(
        "alpha",
        value="write",
        basis="typed_provider_fact",
        policy_eligible=True,
        status="structural",
    )
    assert action.semantic_assessment is not None
    action = action.model_copy(
        update={
            "semantic_assessment": action.semantic_assessment.model_copy(
                update={
                    "effect": action.semantic_assessment.effect.model_copy(
                        update={
                            "issues": [
                                SemanticIssueEvidence(
                                    kind="declaration_drift",
                                    dimension="effect",
                                    message="the evidence basis moved",
                                )
                            ]
                        }
                    )
                }
            )
        }
    )
    review = build_declaration_review(
        head=head,
        base=ActionDeclarationFacts(),
        action_surface_facts=ActionSurfaceFacts(actions=[action]),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )
    assert review.rows[0].bucket == "unverified"
    assert "declaration_drift" in review.rows[0].reason


def test_mixed_strength_observed_effects_never_machine_verify() -> None:
    head = build_action_declaration_facts(
        _manifest(
            [
                {
                    "tool": "transfer",
                    "effect": "financial_write",
                    "risk_tags": ["external_communication"],
                }
            ]
        ),
        [_tool("transfer")],
    )
    mixed = _action_with_readings(
        "transfer",
        readings=[
            ("financial_write", "typed_provider_fact", True),
            ("external_communication", "inferred_keyword", False),
        ],
    )
    review = build_declaration_review(
        head=head,
        base=ActionDeclarationFacts(),
        action_surface_facts=ActionSurfaceFacts(actions=[mixed]),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )
    assert review.rows[0].bucket == "unverified"
    assert "mixed-strength" in review.rows[0].reason


def test_projection_counts_three_buckets_and_names_only_attention_rows() -> None:
    tools = [_tool("consistent"), _tool("unknown"), _tool("override")]
    head = build_action_declaration_facts(
        _manifest(
            [
                {"tool": "consistent", "effect": "write"},
                {"tool": "unknown", "effect": "write"},
                {
                    "tool": "override",
                    "effect": "read",
                    "override": {"evidence": "fixture evidence", "reason": "fixture reason"},
                },
            ]
        ),
        tools,
    )
    override = AcknowledgedEffectOverride(
        subject="override [api]",
        subject_id="tool:override",
        declared_effect="read",
        inferred_effect="external_communication",
        inferred_sources=["risk_hint:keyword"],
        evidence="fixture evidence",
        reason="fixture reason",
        manifest_path="shipgate.yaml#action_surface.actions[tool='override'].override",
    )
    review = build_declaration_review(
        head=head,
        base=ActionDeclarationFacts(),
        action_surface_facts=ActionSurfaceFacts(
            actions=[
                _action(
                    "consistent",
                    value="write",
                    basis="typed_provider_fact",
                    policy_eligible=True,
                    status="structural",
                ),
                _action(
                    "unknown",
                    value="write",
                    basis="protocol_default",
                    policy_eligible=False,
                    status="protocol_default",
                ),
                _action(
                    "override",
                    value="external_communication",
                    basis="inferred_keyword",
                    policy_eligible=False,
                    status="declared",
                ),
            ]
        ),
        evidence_gaps=[],
        acknowledged_overrides=[override],
    )

    assert review.changed_count == 3
    assert review.summary.model_dump() == {
        "evidence_consistent": 1,
        "unverified": 1,
        "acknowledged_override": 1,
    }
    lines = declaration_review_lines(review)
    rendered = "\n".join(lines)
    assert "3 — 1 evidence-consistent, 1 unverified, 1 acknowledged override" in rendered
    assert "unknown [api]" in rendered
    assert "override [api]" in rendered
    assert "consistent [api]" not in rendered
    assert "Review shipgate.yaml#action_surface.actions[2]." in rendered
    items = select_pr_items(
        {
            "release_decision": {
                "evidence_coverage": {
                    "semantic_coverage": {
                        "declaration_review": review.model_dump(mode="json"),
                        "acknowledged_overrides": [
                            override.model_dump(mode="json")
                        ],
                    }
                }
            }
        }
    )
    assert len(
        [
            item
            for item in items
            if item.check_id == "SHIP-ACTION-EFFECT-OVERRIDE-ACKNOWLEDGED"
        ]
    ) == 1


def test_override_must_join_the_changed_row_by_canonical_subject_id() -> None:
    tool = _tool("same_name")
    head = build_action_declaration_facts(
        _manifest([{"tool": "same_name", "effect": "read"}]), [tool]
    )
    wrong_provider_override = AcknowledgedEffectOverride(
        subject="same_name [other]",
        subject_id="tool:other",
        declared_effect="read",
        inferred_effect="write",
        evidence="e",
        reason="r",
        manifest_path="shipgate.yaml#action_surface.actions[0].override",
    )
    review = build_declaration_review(
        head=head,
        base=ActionDeclarationFacts(),
        action_surface_facts=ActionSurfaceFacts(
            actions=[
                _action(
                    "same_name",
                    value="write",
                    basis="protocol_default",
                    policy_eligible=False,
                    status="protocol_default",
                )
            ]
        ),
        evidence_gaps=[],
        acknowledged_overrides=[wrong_provider_override],
    )
    assert review.rows[0].bucket == "unverified"


def test_override_must_match_the_current_rows_evidence_and_reason_digest() -> None:
    head = build_action_declaration_facts(
        _manifest(
            [
                {
                    "tool": "alpha",
                    "effect": "read",
                    "override": {
                        "evidence": "reviewed deployment contract",
                        "reason": "keyword is an example only",
                    },
                }
            ]
        ),
        [_tool("alpha")],
    )
    action = _action(
        "alpha",
        value="external_communication",
        basis="inferred_keyword",
        policy_eligible=False,
        status="declared",
    )

    def _override(*, evidence: str, reason: str) -> AcknowledgedEffectOverride:
        return AcknowledgedEffectOverride(
            subject="alpha [api]",
            subject_id="tool:alpha",
            declared_effect="read",
            inferred_effect="external_communication",
            inferred_sources=["risk_hint:keyword"],
            evidence=evidence,
            reason=reason,
            manifest_path="shipgate.yaml#action_surface.actions[0].override",
        )

    exact = build_declaration_review(
        head=head,
        base=ActionDeclarationFacts(),
        action_surface_facts=ActionSurfaceFacts(actions=[action]),
        evidence_gaps=[],
        acknowledged_overrides=[
            _override(
                evidence="reviewed deployment contract",
                reason="keyword is an example only",
            )
        ],
    )
    assert exact.rows[0].bucket == "acknowledged_override"

    for stale in (
        _override(evidence="different evidence", reason="keyword is an example only"),
        _override(evidence="reviewed deployment contract", reason="different reason"),
    ):
        review = build_declaration_review(
            head=head,
            base=ActionDeclarationFacts(),
            action_surface_facts=ActionSurfaceFacts(actions=[action]),
            evidence_gaps=[],
            acknowledged_overrides=[stale],
        )
        assert review.rows[0].bucket == "unverified"


def test_legacy_or_duplicate_base_disables_without_rendering() -> None:
    disabled = build_declaration_review(
        head=ActionDeclarationFacts(),
        base=None,
        action_surface_facts=ActionSurfaceFacts(),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )
    assert disabled.enabled is False
    assert declaration_review_lines(disabled) == []

    unavailable = build_declaration_review(
        head=ActionDeclarationFacts(),
        base=None,
        action_surface_facts=ActionSurfaceFacts(),
        evidence_gaps=[],
        acknowledged_overrides=[],
        base_comparison_requested=True,
    )
    assert unavailable.base_comparison_requested is True
    assert "comparison unavailable" in declaration_review_lines(unavailable)[0]

    row = build_action_declaration_facts(
        _manifest([{"tool": "alpha", "effect": "write"}]), [_tool("alpha")]
    ).rows[0]
    # Published/runtime models reject this shape up front. Keep the projection
    # defense tested against an object reconstructed without validation, as a
    # belt-and-suspenders guard for legacy/untrusted callers.
    duplicate = ActionDeclarationFacts.model_construct(rows=[row, row.model_copy()])
    collision = build_declaration_review(
        head=ActionDeclarationFacts(rows=[row]),
        base=duplicate,
        action_surface_facts=ActionSurfaceFacts(),
        evidence_gaps=[],
        acknowledged_overrides=[],
    )
    assert collision.enabled is False
    assert "duplicate row ids" in collision.notes[0]


_AGENT = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

def send_email(to: str) -> dict:
    """Send email."""
    return {"ok": True}

root_agent = LlmAgent(name="reviewer", instruction="Review.", tools=[FunctionTool(func=send_email)])
'''

_BASE_MANIFEST = """
version: "0.1"
project: {name: declaration-review}
agent:
  name: reviewer
  declared_purpose: [review]
environment: {target: local}
tool_sources:
  - id: adk
    type: google_adk
    path: agent.py
"""


def test_redacted_override_change_remains_indeterminate_without_raw_digest(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(_AGENT, encoding="utf-8")
    manifest = project / "shipgate.yaml"
    first_secret = "sk-aaaaaaaaaaaaaaaaaaaaaaaa"
    second_secret = "sk-bbbbbbbbbbbbbbbbbbbbbbbb"
    reason = "reviewed deployment contract"

    def _manifest_with(evidence: str) -> str:
        return (
            _BASE_MANIFEST
            + f"""
action_surface:
  actions:
    - tool: send_email
      source_id: adk
      effect: read
      override:
        evidence: {evidence}
        reason: {reason}
"""
        )

    manifest.write_text(_manifest_with(first_secret), encoding="utf-8")
    base, _ = run_scan(
        config_path=manifest,
        output_dir=project / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    manifest.write_text(_manifest_with(second_secret), encoding="utf-8")
    head, _ = run_scan(
        config_path=manifest,
        output_dir=project / "head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=project / "base" / "report.json",
        packet_enabled=False,
    )

    base_row = base.action_declaration_facts.rows[0]
    head_row = head.action_declaration_facts.rows[0]
    assert base_row.override_identity == "action_declaration_override_indeterminate"
    assert head_row.override_identity == "action_declaration_override_indeterminate"
    assert head.release_decision is not None
    review = head.release_decision.evidence_coverage.semantic_coverage.declaration_review
    assert review.enabled is True
    assert review.changed_count == 1
    assert review.rows[0].change_type == "modified"
    assert review.rows[0].bucket == "unverified"
    assert "indeterminate after privacy redaction" in review.rows[0].reason

    rendered = "\n".join(
        (project / side / "report.json").read_text(encoding="utf-8")
        for side in ("base", "head")
    )
    for secret in (first_secret, second_secret):
        raw_override_digest = "action_declaration_override_" + hashlib.sha256(
            json.dumps(
                {"evidence": secret, "reason": reason},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        assert secret not in rendered
        assert hashlib.sha256(secret.encode()).hexdigest() not in rendered
        assert raw_override_digest not in rendered


def _pr_comment(report) -> str:
    assert report.release_decision is not None
    capability_review = VerifierCapabilityReview()
    merge_verdict = map_merge_verdict(report.release_decision.decision)
    control = _derive_verifier_control(
        execution="succeeded",
        merge_verdict=merge_verdict,
        release_decision=report.release_decision,
        fix_task=None,
        capability_review=capability_review,
        headline="declaration review",
        first_next_action_override=None,
        base_status="not_requested",
        base_ref=None,
        diff_status=VerifierDiffStatus(completeness="complete"),
    )
    verifier = VerifierArtifact(
        workspace=".",
        diff_status=VerifierDiffStatus(),
        config="shipgate.yaml",
        authorization=AuthorizationEvaluationV1.not_requested(),
        trigger={"rationale": "1 rule matched."},
        execution="succeeded",
        head_status="succeeded",
        release_decision=report.release_decision,
        decision=report.release_decision.decision,
        merge_verdict=merge_verdict,
        applicability="verified",
        headline="declaration review",
        control=control,
        capability_review=capability_review,
        artifacts={"report_json": "agents-shipgate-reports/report.json"},
    )
    return render_pr_comment(verifier, report=report)


def _rendering_report(tmp_path: Path):
    project = tmp_path / "rendering-project"
    project.mkdir()
    (project / "agent.py").write_text(_AGENT, encoding="utf-8")
    (project / "shipgate.yaml").write_text(
        _BASE_MANIFEST
        + """
action_surface:
  actions:
    - tool: send_email
      source_id: adk
      effect: external_communication
      authority: {mode: none}
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=project / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    return report


def test_declaration_renderers_make_hostile_text_visible_injective_and_safe(
    tmp_path: Path,
) -> None:
    hostile = "m\r\n- forged-declaration<script>\u202e\u200b"
    override = AcknowledgedEffectOverride(
        subject=hostile,
        subject_id="tool:hostile",
        declared_effect="read",
        inferred_effect="write\u200b",
        inferred_sources=["s\nline"],
        corroborating_sources=["c\u202e"],
        evidence="<b>\r\nforged",
        reason="r\u200b\u202e",
        manifest_path="s.yaml\r\n#forged",
    )
    row = DeclarationReviewRow(
        row_id="hostile-row",
        change_type="modified",
        bucket="acknowledged_override",
        subject=hostile,
        subject_id="tool:hostile",
        declared_effect="read",
        reason="review\r\n- forged-declaration\u202e",
        manifest_path="shipgate.yaml\r\n#forged",
        acknowledged_overrides=[override],
    )
    review = DeclarationReviewDecision(
        enabled=True,
        base_kind="report",
        changed_count=1,
        summary=DeclarationReviewSummary(acknowledged_override=1),
        rows=[row],
    )

    lines = declaration_review_lines(review)
    assert all("\r" not in line and "\n" not in line for line in lines)
    visible = "\n".join(lines)
    assert "<script>" not in visible
    assert "\u202e" not in visible
    assert "\u200b" not in visible
    assert "<U+000D><U+000A>" in visible
    assert "<U+202E>" in visible
    assert "<U+200B>" in visible
    assert "<U+003C>script>" in visible

    # A real newline and the literal spelling of its escape remain distinct;
    # the display transform cannot let one repository value impersonate the
    # other.
    newline_row = row.model_copy(
        update={
            "row_id": "newline",
            "bucket": "unverified",
            "subject": "same\nname",
            "subject_id": None,
            "declared_effect": "write",
            "acknowledged_overrides": [],
        }
    )
    literal_row = newline_row.model_copy(
        update={"row_id": "literal", "subject": "same<U+000A>name"}
    )
    injective = DeclarationReviewDecision(
        enabled=True,
        base_kind="report",
        changed_count=2,
        summary=DeclarationReviewSummary(unverified=2),
        rows=[newline_row, literal_row],
    )
    detail = declaration_review_lines(injective)[1:]
    assert detail[0] != detail[1]
    assert "same<U+000A>name" in detail[0]
    assert "same<U+003C>U+000A>name" in detail[1]

    report = _rendering_report(tmp_path)
    semantic = report.release_decision.evidence_coverage.semantic_coverage
    semantic.declaration_review = review
    packet = build_packet_from_report(report)
    rendered_surfaces = (
        _pr_comment(report),
        render_packet_markdown(packet),
        render_packet_html(packet),
    )
    for rendered in rendered_surfaces:
        assert "\r" not in rendered
        assert "\u202e" not in rendered
        assert "\u200b" not in rendered
        assert "<script>" not in rendered
        assert "\n- forged-declaration" not in rendered
        # Markdown escapes punctuation after the injective display transform
        # (``U\+000D``); HTML does not. Strip only Markdown escape slashes so
        # these assertions compare the visible value shared by all surfaces.
        visible_rendered = rendered.replace("\\", "")
        assert "U+000D" in visible_rendered
        assert "U+000A" in visible_rendered
        assert "U+202E" in visible_rendered
        assert "U+200B" in visible_rendered
        assert "U+003C" in visible_rendered


def test_pr_declaration_budget_counts_prefix_and_markdown_escaping(
    tmp_path: Path,
) -> None:
    report = _rendering_report(tmp_path)
    assert report.release_decision is not None
    rows = [
        DeclarationReviewRow(
            row_id=f"budget-{index}",
            change_type="modified",
            bucket="unverified",
            subject=f"row{index}-" + ("*[]" * 24),
            subject_id=f"tool:budget-{index}",
            declared_effect="write",
            reason="review " + ("_{}" * 18),
            manifest_path=f"shipgate.yaml#action_surface.actions[{index}]",
        )
        for index in range(7)
    ]
    report.release_decision.evidence_coverage.semantic_coverage.declaration_review = (
        DeclarationReviewDecision(
            enabled=True,
            base_kind="report",
            changed_count=len(rows),
            summary=DeclarationReviewSummary(unverified=len(rows)),
            rows=rows,
        )
    )

    comment = _pr_comment(report)
    prefixes = (
        "- Declaration changes:",
        "- Unverified declaration:",
        "- Acknowledged override declaration:",
    )
    block = [
        line
        for line in comment.splitlines()
        if line.startswith(prefixes)
        or (line.startswith("- ") and " additional rows; see report" in line)
    ]
    details = [line for line in block if line.startswith(prefixes[1:])]
    assert block and details
    assert len("\n".join(block)) <= 1_000
    assert all(len(line) <= 400 for line in details)
    assert "additional rows; see report" in block[-1]
    omitted = int(block[-1].split()[1])
    assert omitted == len(rows) - len(details)


def test_row_character_budget_omits_a_moderately_oversized_row() -> None:
    row = DeclarationReviewRow(
        row_id="moderately-oversized",
        change_type="modified",
        bucket="unverified",
        subject="alpha",
        subject_id="tool:alpha",
        declared_effect="write",
        reason="x" * 500,
        manifest_path="shipgate.yaml#action_surface.actions[0]",
    )
    review = DeclarationReviewDecision(
        enabled=True,
        base_comparison_requested=True,
        base_kind="report",
        changed_count=1,
        summary=DeclarationReviewSummary(unverified=1),
        rows=[row],
    )
    lines = declaration_review_lines(review, row_char_limit=400)
    assert len(lines) == 2
    assert "moderately-oversized" not in "\n".join(lines)
    assert lines[-1] == "1 additional rows; see report.json."


def test_report_and_step_summary_use_shared_declaration_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _rendering_report(tmp_path)
    assert report.release_decision is not None
    row = DeclarationReviewRow(
        row_id="summary-row",
        change_type="removed",
        bucket="unverified",
        subject="send_email [google_adk]",
        subject_id="tool:send_email",
        declared_effect="external_communication",
        reason="The reviewed declaration row was removed.",
        manifest_path="services/mailer/shipgate.yaml#action_surface.actions[0]",
    )
    report.release_decision.evidence_coverage.semantic_coverage.declaration_review = (
        DeclarationReviewDecision(
            enabled=True,
            base_comparison_requested=True,
            base_kind="report",
            changed_count=1,
            summary=DeclarationReviewSummary(unverified=1),
            rows=[row],
        )
    )

    markdown = render_markdown_report(report)
    assert "## Declaration Review" in markdown
    assert "The reviewed declaration row was removed" in markdown

    summary_path = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    write_github_step_summary(report)
    summary = summary_path.read_text(encoding="utf-8")
    assert "### Declaration review" in summary
    assert "The reviewed declaration row was removed" in summary


def test_scan_carries_base_snapshot_and_packet_renders_changed_row(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(_AGENT, encoding="utf-8")
    manifest = project / "shipgate.yaml"
    manifest.write_text(_BASE_MANIFEST, encoding="utf-8")
    base, _ = run_scan(
        config_path=manifest,
        output_dir=project / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert base.action_declaration_facts.rows == []

    manifest.write_text(
        _BASE_MANIFEST
        + """
action_surface:
  actions:
    - tool: send_email
      source_id: adk
      effect: external_communication
      authority: {mode: none}
""",
        encoding="utf-8",
    )
    head, _ = run_scan(
        config_path=manifest,
        output_dir=project / "head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=project / "base" / "report.json",
        packet_enabled=False,
    )
    assert len(head.action_declaration_facts.rows) == 1
    assert head.release_decision is not None
    review = head.release_decision.evidence_coverage.semantic_coverage.declaration_review
    assert review.enabled is True
    assert review.changed_count == 1
    assert review.rows[0].bucket == "unverified"

    packet = build_packet_from_report(head)
    for rendered in (render_packet_markdown(packet), render_packet_html(packet)):
        assert "Declaration changes" in rendered
        assert "send_email" in rendered or "send\\_email" in rendered

    # PR detail is bounded with an exact omission count. Packet §1 continues
    # to render every attention row from the same semantic projection.
    attention_rows = [
        review.rows[0].model_copy(
            update={
                "row_id": f"row-{index}",
                "subject": f"row{index}",
                "subject_id": f"tool:row{index}",
                "manifest_path": f"shipgate.yaml#action_surface.actions[{index}]",
            }
        )
        for index in range(5)
    ]
    oversized_override = AcknowledgedEffectOverride(
        subject="row0",
        subject_id="tool:row0",
        declared_effect="read",
        inferred_effect="external_communication",
        evidence="e" * 8_000,
        reason="r" * 8_000,
        manifest_path="shipgate.yaml#action_surface.actions[0].override",
    )
    attention_rows[0] = attention_rows[0].model_copy(
        update={
            "bucket": "acknowledged_override",
            "declared_effect": "read",
            "acknowledged_overrides": [oversized_override],
        }
    )
    semantic = head.release_decision.evidence_coverage.semantic_coverage
    semantic.declaration_review = DeclarationReviewDecision(
        enabled=True,
        base_kind="report",
        changed_count=5,
        summary=DeclarationReviewSummary(unverified=4, acknowledged_override=1),
        rows=attention_rows,
    )
    comment = _pr_comment(head).replace("\\", "")
    # The oversized first row is omitted whole; it does not prevent the next
    # three bounded rows from being shown. The final short row is the second
    # omitted row because the row-count budget is then full.
    assert "row0" not in comment
    for index in range(1, 4):
        assert f"row{index}" in comment
    assert "row4" not in comment
    assert "2 additional rows; see report.json." in comment
    assert "e" * 1_000 not in comment

    exhaustive_packet = build_packet_from_report(head)
    for rendered in (
        render_packet_markdown(exhaustive_packet),
        render_packet_html(exhaustive_packet),
    ):
        for index in range(5):
            assert f"row{index}" in rendered
        assert "additional rows; see report.json" not in rendered

    legacy = json.loads((project / "base" / "report.json").read_text(encoding="utf-8"))
    legacy.pop("action_declaration_facts")
    legacy_path = project / "legacy-report.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    legacy_head, _ = run_scan(
        config_path=manifest,
        output_dir=project / "legacy-head",
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=legacy_path,
        packet_enabled=False,
    )
    assert legacy_head.release_decision is not None
    legacy_review = (
        legacy_head.release_decision.evidence_coverage.semantic_coverage.declaration_review
    )
    assert legacy_review.enabled is False
    assert legacy_review.base_comparison_requested is True
    assert "comparison unavailable" in declaration_review_lines(legacy_review)[0]
    legacy_packet = build_packet_from_report(legacy_head)
    for rendered in (
        _pr_comment(legacy_head),
        render_packet_markdown(legacy_packet),
        render_packet_html(legacy_packet),
    ):
        assert "comparison unavailable" in rendered


def test_scan_preserves_sanitized_workspace_relative_manifest_path(
    tmp_path: Path,
) -> None:
    scoped = tmp_path / "workspace" / "services" / "billing"
    scoped.mkdir(parents=True)
    (scoped / "agent.py").write_text(_AGENT, encoding="utf-8")
    manifest = scoped / "shipgate.yaml"
    manifest.write_text(
        _BASE_MANIFEST
        + """
action_surface:
  actions:
    - tool: send_email
      source_id: adk
      effect: external_communication
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=manifest,
        output_dir=tmp_path / "scoped-report",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
        verification_context=VerificationContext(
            configured_manifest_path="services/billing/shipgate.yaml"
        ),
    )
    assert len(report.action_declaration_facts.rows) == 1
    assert report.action_declaration_facts.rows[0].manifest_path.startswith(
        "services/billing/shipgate.yaml#action_surface.actions["
    )


def test_scoped_override_uses_the_configured_manifest_location(
    tmp_path: Path,
) -> None:
    scoped = tmp_path / "workspace" / "services" / "billing"
    scoped.mkdir(parents=True)
    (scoped / "agent.py").write_text(_AGENT, encoding="utf-8")
    manifest = scoped / "shipgate.yaml"
    manifest.write_text(
        _BASE_MANIFEST
        + """
action_surface:
  actions:
    - tool: send_email
      source_id: adk
      effect: read
      override:
        evidence: no mail client is constructed
        reason: the fixture returns a local dictionary
""",
        encoding="utf-8",
    )
    report, _ = run_scan(
        config_path=manifest,
        output_dir=tmp_path / "scoped-override-report",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
        verification_context=VerificationContext(
            configured_manifest_path="services/billing/shipgate.yaml"
        ),
    )
    assert report.release_decision is not None
    overrides = (
        report.release_decision.evidence_coverage.semantic_coverage.acknowledged_overrides
    )
    assert len(overrides) == 1
    assert overrides[0].manifest_path == (
        "services/billing/shipgate.yaml#action_surface.actions[0].override"
    )


def test_scoped_adoption_keeps_declaration_review_when_base_probe_is_bounded(
    tmp_path: Path,
) -> None:
    """The cheap configured-gate fact, not the whole-tree probe, enables §D.

    A single oversized base blob is enough for the suffix-agnostic manifest
    probe to answer ``None``. That conservative answer must keep policy routing
    fail closed without erasing the declarations introduced by a scoped gate.
    """

    repo = tmp_path / "monorepo"
    service = repo / "services" / "mailer"
    service.mkdir(parents=True)
    (service / "agent.py").write_text(_AGENT, encoding="utf-8")
    (repo / "large-fixture.bin").write_bytes(b"x" * (_MAX_MANIFEST_BYTES + 1))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.test"], cwd=repo, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo, check=True
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "base without gate"], cwd=repo, check=True
    )

    manifest_relative = Path("services/mailer/shipgate.yaml")
    (repo / manifest_relative).write_text(
        _BASE_MANIFEST
        + """
action_surface:
  actions:
    - tool: send_email
      source_id: adk
      effect: external_communication
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "adopt scoped gate"], cwd=repo, check=True
    )

    introduction_args = {
        "git_root": repo,
        "config_relative": manifest_relative,
        "base_status": "missing_manifest",
        "base": "HEAD~1",
        "head": "HEAD",
        "changed_files": [manifest_relative.as_posix()],
    }
    assert _configured_gate_introduced(
        **introduction_args,
        worktree_ref=None,
    ) is True
    assert _manifest_introduced(**introduction_args) is False

    verifier, report, _ = run_verify(
        workspace=repo,
        config=manifest_relative,
        base="HEAD~1",
        head="HEAD",
        archive_head=True,
        out=repo / "agents-shipgate-reports",
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
    )
    assert verifier.base_status == "missing_manifest"
    assert report is not None and report.release_decision is not None
    review = report.release_decision.evidence_coverage.semantic_coverage.declaration_review
    assert review.enabled is True
    assert review.base_kind == "absent_manifest"
    assert review.changed_count == 1
    assert report.action_declaration_facts.rows[0].manifest_path.startswith(
        "services/mailer/shipgate.yaml#action_surface.actions["
    )
