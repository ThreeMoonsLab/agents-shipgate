from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan import run_scan


def _write_project(
    root: Path,
    *,
    tools: list[dict[str, object]],
    actions: list[dict[str, object]] | None = None,
    confirmation_tools: list[str] | None = None,
    ignored_checks: list[tuple[str, str]] | None = None,
) -> Path:
    (root / "tools.json").write_text(json.dumps({"tools": tools}, indent=2), encoding="utf-8")
    action_surface = ""
    if actions is not None:
        action_lines = []
        for action in actions:
            action_lines.append(f"    - tool: {action['tool']}")
            action_lines.append(f"      effect: {action['effect']}")
            scopes = action.get("scopes") or []
            if scopes:
                action_lines.append("      scopes:")
                action_lines.extend(f"        - {scope}" for scope in scopes)
            risk_tags = action.get("risk_tags") or []
            if risk_tags:
                action_lines.append("      risk_tags:")
                action_lines.extend(f"        - {tag}" for tag in risk_tags)
            authority = action["authority"]
            action_lines.append("      authority:")
            action_lines.append(f"        mode: {authority['mode']}")
            if authority.get("auth_type"):
                action_lines.append(f"        auth_type: {authority['auth_type']}")
            if authority.get("reason"):
                action_lines.append(f"        reason: {authority['reason']}")
            for control in action.get("controls") or []:
                path, value = control
                action_lines.append(f"      {path}:")
                for key, enabled in value.items():
                    action_lines.append(f"        {key}: {str(enabled).lower()}")
        action_surface = "\naction_surface:\n  actions:\n" + "\n".join(action_lines) + "\n"

    policy_block = ""
    if confirmation_tools:
        policy_block = (
            "\npolicies:\n  require_confirmation_for_tools:\n"
            + "\n".join(f"    - {tool}" for tool in confirmation_tools)
            + "\n"
        )
    checks_block = ""
    if ignored_checks:
        rows: list[str] = []
        for check_id, tool in ignored_checks:
            rows.extend(
                [
                    f"    - check_id: {check_id}",
                    f"      tool: {tool}",
                    "      reason: reviewed test suppression",
                ]
            )
        checks_block = "\nchecks:\n  ignore:\n" + "\n".join(rows) + "\n"

    manifest = root / "shipgate.yaml"
    binding_lines = [
        "agent_bindings:",
        "  declarations:",
        "    - agent: root",
        "      complete: true",
        "      tools:",
        *[f"        - {{tool: {tool['name']}, source_id: tools}}" for tool in tools],
        "      handoffs: []",
        "      reason: reviewed P0 semantic fixture binding",
    ]
    manifest.write_text(
        """version: "0.1"
project:
  name: semantic-pass-contract
agent:
  name: semantic-pass-agent
  declared_purpose: [exercise the evidence-backed pass contract]
environment:
  target: production_like
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
ci:
  mode: advisory
output:
  directory: reports
  formats: [json]
"""
        + "\n".join(binding_lines)
        + "\n"
        + action_surface
        + policy_block
        + checks_block,
        encoding="utf-8",
    )
    return manifest


def _tool(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": f"Perform the {name} operation using declared inputs.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    }


def test_neutral_mcp_tool_cannot_pass_and_strict_fails(tmp_path: Path) -> None:
    config = _write_project(tmp_path, tools=[_tool("process_order")])

    advisory, advisory_exit = run_scan(
        config_path=config,
        output_dir=tmp_path / "advisory",
        formats=["json", "sarif"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    strict, strict_exit = run_scan(
        config_path=config,
        output_dir=tmp_path / "strict",
        formats=["json"],
        ci_mode="strict",
        packet_enabled=False,
    )

    assert advisory.release_decision is not None
    assert advisory.release_decision.decision == "insufficient_evidence"
    assert advisory.release_decision.evidence_coverage.semantic_coverage.gap_count == 2
    assert advisory.action_surface_facts.actions[0].effect == "write"
    assert advisory_exit == 0
    sarif = json.loads((tmp_path / "advisory" / "report.sarif").read_text())
    semantic_results = [
        result
        for result in sarif["runs"][0]["results"]
        if result["ruleId"].startswith("SHIP-SEMANTIC-")
    ]
    assert {result["properties"]["evidence_gap_kind"] for result in semantic_results} == {
        "missing_effect_evidence",
        "missing_authority_evidence",
    }
    assert sarif["runs"][0]["properties"]["runtime_behavior_verified"] is False
    assert strict.release_decision is not None
    assert strict.release_decision.decision == "insufficient_evidence"
    assert strict.release_decision.fail_policy.would_fail_ci is True
    assert strict_exit == 20


def test_reviewed_read_and_no_authority_can_pass(tmp_path: Path) -> None:
    config = _write_project(
        tmp_path,
        tools=[_tool("list_orders")],
        actions=[
            {
                "tool": "list_orders",
                "effect": "read",
                "authority": {"mode": "none"},
            }
        ],
    )

    report, exit_code = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="strict",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == "passed"
    assert report.release_decision.static_analysis_only is True
    assert report.release_decision.runtime_behavior_verified is False
    assert "did not execute the agent" in report.release_decision.static_verdict_disclaimer
    assert report.release_decision.evidence_coverage.semantic_coverage.model_dump() == {
        "total_actions": 1,
        "pass_eligible_actions": 1,
        "gap_count": 0,
        "review_concern_count": 0,
        "reason_counts": {},
        "acknowledged_overrides": [],
        "declaration_review": {
            "enabled": False,
            "base_kind": "none",
            "changed_count": 0,
            "summary": {
                "evidence_consistent": 0,
                "unverified": 0,
                "acknowledged_override": 0,
            },
            "rows": [],
            "notes": [
                "No trustworthy base declaration snapshot was available; declaration review disabled."
            ],
        },
        # Both dimensions were asked and both were answered: without the
        # declaration this action has neither effect nor authority evidence.
        "declaration_questions": {
            "total": 2,
            "answered": 2,
            "open": 0,
            "open_by_dimension": {},
            "open_questions": [],
        },
    }
    action = report.action_surface_facts.actions[0]
    assert action.semantic_assessment is not None
    assert action.effect == action.semantic_assessment.conservative_effect == "read"
    assert exit_code == 0


@pytest.mark.parametrize("safe_count", [1, 2, 10, 100])
def test_one_unresolved_tool_cannot_be_diluted_by_declared_tools(
    tmp_path: Path,
    safe_count: int,
) -> None:
    safe_tools = [_tool(f"safe_{index}") for index in range(safe_count)]
    actions = [
        {
            "tool": str(tool["name"]),
            "effect": "read",
            "authority": {"mode": "none"},
        }
        for tool in safe_tools
    ]
    config = _write_project(
        tmp_path,
        tools=[*safe_tools, _tool("process_order")],
        actions=actions,
    )

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    semantic = report.release_decision.evidence_coverage.semantic_coverage
    assert semantic.total_actions == safe_count + 1
    assert semantic.pass_eligible_actions == safe_count
    assert semantic.gap_count == 2


def test_destructive_declaration_enforces_current_surface_controls(
    tmp_path: Path,
) -> None:
    config = _write_project(
        tmp_path,
        tools=[_tool("purge_order")],
        actions=[
            {
                "tool": "purge_order",
                "effect": "destructive",
                "authority": {"mode": "none"},
            }
        ],
    )

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == "blocked"
    assert any(
        finding.check_id == "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING" and finding.blocks_release
        for finding in report.findings
    )
    capability = next(fact for fact in report.capability_facts if fact.tool_name == "purge_order")
    action = report.action_surface_facts.actions[0]
    assert capability.semantic_assessment is not None
    assert (
        capability.effect
        == capability.semantic_assessment.conservative_effect
        == action.effect
        == "destructive"
    )


def test_contradictory_mcp_hints_cannot_be_masked_by_declaration(
    tmp_path: Path,
) -> None:
    tool = _tool("purge_order")
    tool["annotations"] = {"readOnlyHint": True, "destructiveHint": True}
    config = _write_project(
        tmp_path,
        tools=[tool],
        actions=[
            {
                "tool": "purge_order",
                "effect": "destructive",
                "authority": {"mode": "none"},
                "controls": [
                    ("approval", {"required": True}),
                    ("safeguards", {"rollback": True}),
                ],
            }
        ],
        confirmation_tools=["purge_order"],
    )

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision != "passed"
    assert "conflicting_effect_evidence" in {
        gap.kind for gap in report.release_decision.evidence_coverage.evidence_gaps
    }


def test_control_union_preserves_financial_and_destructive_claims(
    tmp_path: Path,
) -> None:
    tool = _tool("settle_and_delete")
    tool["annotations"] = {"permission_classes": ["financial", "destructive"]}
    config = _write_project(
        tmp_path,
        tools=[tool],
        actions=[
            {
                "tool": "settle_and_delete",
                "effect": "destructive",
                "authority": {"mode": "none"},
                "controls": [
                    ("approval", {"required": True}),
                    ("safeguards", {"rollback": True}),
                ],
            }
        ],
        confirmation_tools=["settle_and_delete"],
    )

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == "blocked"
    assert any(
        finding.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
        for finding in report.findings
    )


def test_manual_positive_action_tag_cannot_hide_behind_read_effect(
    tmp_path: Path,
) -> None:
    """A declared tag applies its category's controls; it does not soften.

    The tag used to be reported as `conflicting_effect_evidence` — the
    manifest read as contradicting itself — which is the #424 defect: the
    same reading made the `declare_risk_tags` repair unable to close the row
    that published it. What must hold is that the tag cannot *hide* anything,
    and the stronger form of that is what the gate now says: the
    financial-write controls apply, they are missing, and the run is blocked
    for that reason rather than for the shape of the manifest.
    """

    config = _write_project(
        tmp_path,
        tools=[_tool("settle_order")],
        actions=[
            {
                "tool": "settle_order",
                "effect": "read",
                "risk_tags": ["financial_write"],
                "authority": {"mode": "none"},
            }
        ],
    )

    report, _ = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == "blocked"
    assert any(
        finding.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
        for finding in report.findings
    )


def _without_claims(value):
    """The same fact with its provenance record removed.

    The two spellings are genuinely different reviewed statements, so their
    claim lists must differ — that is the audit trail saying *where* each
    answer was written. Everything else a scan publishes about the action is
    supposed to be the same, and that is what this strips down to.
    """

    if isinstance(value, dict):
        return {
            key: _without_claims(item)
            for key, item in value.items()
            if key != "claims"
        }
    if isinstance(value, list):
        return [_without_claims(item) for item in value]
    return value


def test_a_tag_on_a_read_row_publishes_what_declaring_the_effect_publishes(
    tmp_path: Path,
) -> None:
    """`effect: read` + a tag is now pass-eligible, so it must publish the truth.

    Comparing the decision and the check IDs is not enough: a false *fact* can
    ride into a passing artifact without changing either. This spelling emitted
    a synthesized `read_only` risk tag beside the positive one, which
    `derive_side_effect` reads as evidence — publishing `reversibility:
    reversible` for a `financial_write` action, and diverging action and
    capability facts from the direct declaration (#461, folded in on review
    because this PR is what moves the wrong claim into a report that can pass).

    Facts are compared rather than diffs: equal facts are equal against every
    base, so this is the stronger statement.

    Scoped to `effect: read` on purpose — see
    `test_a_tag_unions_obligations_with_the_declared_effect` for why the
    equivalence is not general.
    """

    controls = [
        ("approval", {"required": True}),
        ("safeguards", {"audit_log": True, "idempotency": True}),
    ]
    counter = itertools.count()

    def _scan(action: dict[str, object]):
        root = tmp_path / f"case{next(counter)}"
        root.mkdir()
        report, _ = run_scan(
            config_path=_write_project(
                root, tools=[_tool("settle_order")], actions=[action]
            ),
            output_dir=root / "reports",
            formats=["json"],
            ci_mode="advisory",
            packet_enabled=False,
        )
        assert report.release_decision is not None
        return report

    def _spellings(**extra: object):
        base: dict[str, object] = {
            "tool": "settle_order",
            "authority": {"mode": "none"},
            **extra,
        }
        return (
            _scan({**base, "effect": "read", "risk_tags": ["financial_write"]}),
            _scan({**base, "effect": "financial_write"}),
        )

    for reports in (_spellings(controls=controls), _spellings()):
        tagged, declared = reports
        gates = [
            (
                report.release_decision.decision,
                frozenset(finding.check_id for finding in report.findings),
            )
            for report in reports
        ]
        assert gates[0] == gates[1]

        facts = [
            (
                _without_claims(report.action_surface_facts.model_dump(mode="json")),
                _without_claims(
                    [fact.model_dump(mode="json") for fact in report.capability_facts]
                ),
            )
            for report in reports
        ]
        assert facts[0] == facts[1]

        # And the provenance really was the only difference: the claim lists
        # say where each answer was written, and they are not the same.
        assert (
            tagged.action_surface_facts.actions[0].semantic_assessment.effect.claims
            != declared.action_surface_facts.actions[0].semantic_assessment.effect.claims
        )

    # The controls were the whole gate difference: without them both spellings
    # block on the same missing financial-write control.
    bare, _ = _spellings()
    assert bare.release_decision.decision == "blocked"
    assert any(
        finding.check_id == "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING"
        for finding in bare.findings
    )


def test_a_tag_unions_obligations_with_the_declared_effect(tmp_path: Path) -> None:
    """A tag adds a category; it does not replace the one already declared.

    The `effect: read` equivalence above holds because `read` obliges nothing.
    Beside a positive effect the two spellings are not the same statement:
    `external_communication` + a financial tag owes confirmation *and* audit
    *and* approval *and* idempotency, while `effect: financial_write` alone
    drops the confirmation the outward communication required. Stating the
    equivalence generally would have sent a reviewer to replace an effect and
    lose a category (#424 review).
    """

    controls = [
        ("approval", {"required": True}),
        ("safeguards", {"audit_log": True, "idempotency": True}),
    ]
    counter = itertools.count()

    def _gate(action: dict[str, object]) -> tuple[str, frozenset[str]]:
        root = tmp_path / f"union{next(counter)}"
        root.mkdir()
        report, _ = run_scan(
            config_path=_write_project(
                root, tools=[_tool("settle_order")], actions=[action]
            ),
            output_dir=root / "reports",
            formats=["json"],
            ci_mode="advisory",
            packet_enabled=False,
        )
        assert report.release_decision is not None
        return report.release_decision.decision, frozenset(
            finding.check_id for finding in report.findings
        )

    tagged = _gate(
        {
            "tool": "settle_order",
            "effect": "external_communication",
            "risk_tags": ["financial_write"],
            "authority": {"mode": "none"},
            "controls": controls,
        }
    )
    replaced = _gate(
        {
            "tool": "settle_order",
            "effect": "financial_write",
            "authority": {"mode": "none"},
            "controls": controls,
        }
    )

    assert tagged != replaced
    assert tagged[0] == "blocked"
    assert "SHIP-POLICY-CONFIRMATION-MISSING" in tagged[1]
    assert "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING" in tagged[1]
    # Replacing the effect drops the category the row had already declared.
    assert replaced[0] != "blocked"
    assert "SHIP-POLICY-CONFIRMATION-MISSING" not in replaced[1]


def test_suppression_cannot_waive_mandatory_destructive_control(
    tmp_path: Path,
) -> None:
    config = _write_project(
        tmp_path,
        tools=[_tool("purge_order")],
        actions=[
            {
                "tool": "purge_order",
                "effect": "destructive",
                "authority": {"mode": "none"},
                "controls": [("approval", {"required": True})],
            }
        ],
        confirmation_tools=["purge_order"],
        ignored_checks=[("SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING", "purge_order")],
    )

    report, exit_code = run_scan(
        config_path=config,
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="strict",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == "blocked"
    assert report.release_decision.fail_policy.would_fail_ci is True
    assert report.release_consequence is not None
    assert report.release_consequence.blocker_misalignment_count == len(
        report.release_decision.blockers
    )
    assert exit_code == 20
