"""Pin v0.12's `Finding.agent_action` and `report.agent_summary`.

Covers:
- The five enum values are exactly what the strategy doc and the
  contract surface promise (no silent additions/removals).
- `derive_agent_action` produces the documented value for each
  canonical input shape (no patches, manual-only, all-high non-manual,
  mixed confidence, suppressed).
- `build_agent_summary` is a deterministic projection of
  `release_decision` + per-finding `agent_action` (counts match,
  verdict mirrors decision, first_recommended_action follows the
  documented priority order).

Schema-level checks (every finding carries `agent_action`, every
emitted report carries `agent_summary`) live in test_reports.py and
test_finding_remediation.py — this file pins the *semantics* of those
fields.
"""

from __future__ import annotations

from typing import get_args

from agents_shipgate.core.findings import (
    build_agent_summary,
    derive_agent_action,
)
from agents_shipgate.core.models import (
    AgentAction,
    AgentSummary,
    BaselineDelta,
    EvidenceCoverageDecision,
    FailPolicy,
    Finding,
    ReleaseDecision,
    ReleaseDecisionItem,
)
from agents_shipgate.core.patches import (
    AppendPointerPatch,
    ManualPatch,
    RemovePointerPatch,
    SetPointerPatch,
)

# --- AgentAction enum surface contract ----------------------------------

EXPECTED_AGENT_ACTIONS = {
    "auto_apply",
    "propose_patch_for_review",
    "escalate_to_human",
    "suppress_with_reason",
    "informational",
}


def test_agent_action_enum_values():
    """The agent_action enum is a public contract surface (read by
    coding agents, mentioned in STABILITY.md and the keystone contract
    doc). Pin the exact set so additions or removals trip a test in
    the same PR."""
    assert set(get_args(AgentAction)) == EXPECTED_AGENT_ACTIONS, (
        "AgentAction enum diverged from the strategy doc + STABILITY.md "
        "promise. If you're adding or removing a value, update "
        "docs/agent-contract-current.md and STABILITY.md in the same PR."
    )


# --- derive_agent_action contract --------------------------------------


def _make_finding(**kwargs) -> Finding:
    """Test helper that constructs a Finding with sensible defaults
    and lets the caller override individual fields."""
    defaults = {
        "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
        "title": "Missing description",
        "severity": "medium",
        "category": "documentation",
        "recommendation": "Add a description.",
        "patches": None,
        "autofix_safe": None,
        "requires_human_review": None,
    }
    defaults.update(kwargs)
    return Finding(**defaults)


def test_derive_agent_action_suppressed_finding():
    finding = _make_finding(suppressed=True, requires_human_review=True)
    assert derive_agent_action(finding) == "informational"


def test_derive_agent_action_no_patches_human_review_required():
    """No patches + check needs human review → escalate_to_human.
    This is the most common shape for documentation/policy findings."""
    finding = _make_finding(
        patches=None, requires_human_review=True, autofix_safe=False
    )
    assert derive_agent_action(finding) == "escalate_to_human"


def test_derive_agent_action_no_patches_no_review_needed():
    """No patches + no review needed → informational. Rare but valid:
    a fully advisory check without machine-applicable remediation."""
    finding = _make_finding(
        patches=None, requires_human_review=False, autofix_safe=True
    )
    assert derive_agent_action(finding) == "informational"


def test_derive_agent_action_empty_patches_human_review():
    """patches == [] (scan ran with --suggest-patches but generator
    emitted nothing) treats the same as no patches."""
    finding = _make_finding(
        patches=[], requires_human_review=True, autofix_safe=False
    )
    assert derive_agent_action(finding) == "escalate_to_human"


def test_derive_agent_action_manual_only_patches():
    """Patches list contains only ManualPatch → escalate_to_human.
    The patch is in the report for human reading; no machine-applicable
    change is on offer."""
    finding = _make_finding(
        patches=[ManualPatch(instructions="Edit the prompt.")],
        autofix_safe=False,
        requires_human_review=True,
    )
    assert derive_agent_action(finding) == "escalate_to_human"


def test_derive_agent_action_all_high_confidence_non_manual():
    """All non-manual + all high confidence → auto_apply. This is the
    canonical safe-autofix shape (e.g. stale-suppression removal)."""
    patch = RemovePointerPatch(
        target_file="/abs/shipgate.yaml",
        pointer="/checks/ignore/0",
        target_format="yaml",
        confidence="high",
        rationale="Stale suppression for tool that no longer exists.",
        target_sha256="0" * 64,
    )
    finding = _make_finding(
        patches=[patch], autofix_safe=True, requires_human_review=False
    )
    assert derive_agent_action(finding) == "auto_apply"


def test_derive_agent_action_medium_confidence_non_manual():
    """Non-manual patch with medium confidence → propose_patch_for_review.
    Even though `requires_human_review` is True (which it always is in
    this branch — autofix_safe demands all-high), the patch IS
    machine-applicable, so it gets the propose-for-review verdict
    rather than being escalated."""
    patch = AppendPointerPatch(
        target_file="/abs/shipgate.yaml",
        pointer="/permissions/scopes/-",
        value="orders.read",
        target_format="yaml",
        confidence="medium",
        rationale="Tool requires the orders.read scope.",
        target_sha256="0" * 64,
    )
    finding = _make_finding(
        patches=[patch], autofix_safe=False, requires_human_review=True
    )
    assert derive_agent_action(finding) == "propose_patch_for_review"


def test_derive_agent_action_low_confidence_non_manual():
    """Same propose-for-review path as medium."""
    patch = SetPointerPatch(
        target_file="/abs/shipgate.yaml",
        pointer="/agent/declared_purpose/0",
        value="placeholder",
        target_format="yaml",
        confidence="low",
        rationale="Heuristic guess; user should verify.",
        target_sha256="0" * 64,
    )
    finding = _make_finding(
        patches=[patch], autofix_safe=False, requires_human_review=True
    )
    assert derive_agent_action(finding) == "propose_patch_for_review"


def test_derive_agent_action_mixed_manual_and_non_manual():
    """Manual is the FIRST patch → escalate_to_human (per the docstring's
    'kind of the first non-manual patch' — but here the first IS manual,
    so we can't propose anything machine-applicable as the headline)."""
    finding = _make_finding(
        patches=[
            ManualPatch(instructions="Walk through this."),
            SetPointerPatch(
                target_file="/abs/shipgate.yaml",
                pointer="/x",
                value="y",
                target_format="yaml",
                confidence="high",
                rationale="OK",
                target_sha256="0" * 64,
            ),
        ],
        autofix_safe=False,
        requires_human_review=True,
    )
    assert derive_agent_action(finding) == "escalate_to_human"


# --- build_agent_summary contract --------------------------------------


def _make_release_decision(
    *,
    decision: str,
    blockers: list[str] | None = None,
    review_items: list[str] | None = None,
    reason: str = "",
) -> ReleaseDecision:
    """Helper that builds a ReleaseDecision with the minimum fields the
    summary builder reads."""
    blockers = blockers or []
    review_items = review_items or []

    def item(check_id: str) -> ReleaseDecisionItem:
        return ReleaseDecisionItem(
            id=f"f_{check_id}",
            check_id=check_id,
            severity="high",
            title=check_id,
        )

    return ReleaseDecision(
        decision=decision,  # type: ignore[arg-type]
        reason=reason,
        blockers=[item(c) for c in blockers],
        review_items=[item(c) for c in review_items],
        evidence_coverage=EvidenceCoverageDecision(
            level="static",
            human_review_recommended=False,
            source_warning_count=0,
            low_confidence_tool_count=0,
        ),
        baseline_delta=BaselineDelta(enabled=False),
        fail_policy=FailPolicy(
            ci_mode="advisory",
            fail_on=[],
            would_fail_ci=False,
            exit_code=0,
        ),
    )


def test_build_agent_summary_passed_with_no_findings():
    summary = build_agent_summary(
        findings=[],
        release_decision=_make_release_decision(decision="passed"),
    )
    assert isinstance(summary, AgentSummary)
    assert summary.verdict == "passed"
    assert summary.blocker_count == 0
    assert summary.review_item_count == 0
    assert summary.auto_appliable_patches == 0
    assert summary.needs_human_review == 0
    assert summary.first_recommended_action is None


def test_build_agent_summary_blocked_recommends_apply_when_auto_appliable():
    """When the verdict is blocked but at least one finding is
    auto-applicable, first_recommended_action surfaces apply-patches —
    not the top blocker. Apply removes the friction; the user shouldn't
    have to know the priority order themselves."""
    finding = _make_finding(
        check_id="SHIP-MANIFEST-STALE-SUPPRESSION",
        severity="medium",
        agent_action="auto_apply",
    )
    blocker = _make_finding(
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        severity="critical",
        agent_action="escalate_to_human",
    )
    summary = build_agent_summary(
        findings=[finding, blocker],
        release_decision=_make_release_decision(
            decision="blocked",
            blockers=["SHIP-POLICY-APPROVAL-MISSING"],
            reason="1 active finding blocks release.",
        ),
        json_report_path="/abs/agents-shipgate-reports/report.json",
    )
    assert summary.verdict == "blocked"
    assert summary.blocker_count == 1
    assert summary.auto_appliable_patches == 1
    assert summary.needs_human_review == 1
    assert summary.first_recommended_action is not None
    assert summary.first_recommended_action.kind == "command"
    assert "apply-patches" in (summary.first_recommended_action.command or "")


def test_first_recommended_action_uses_actual_json_report_path():
    """When the scan wrote its JSON to a custom path (e.g. via
    ``scan --out custom-reports``), the recommended apply-patches
    command must name THAT path — not the default. Regression for
    PR #57 review P1.1: hardcoding `agents-shipgate-reports/report.json`
    routed agents at the wrong file (or, worse, at a stale default-path
    report from a previous run)."""
    finding = _make_finding(
        check_id="SHIP-MANIFEST-STALE-SUPPRESSION",
        severity="medium",
        agent_action="auto_apply",
    )
    summary = build_agent_summary(
        findings=[finding],
        release_decision=_make_release_decision(decision="passed"),
        json_report_path="/abs/custom-out/report.json",
    )
    assert summary.first_recommended_action is not None
    assert summary.first_recommended_action.kind == "command"
    assert summary.first_recommended_action.command == (
        "agents-shipgate apply-patches --from "
        "/abs/custom-out/report.json --confidence high --apply"
    )


def test_first_recommended_action_falls_back_to_info_without_json_path():
    """When the scan didn't emit a JSON report (e.g. markdown-only),
    we cannot promise a command pointing at a real file. The action
    falls back to ``kind: "info"`` with no command, naming the
    canonical pattern in why-text instead. Catches a regression where
    the hardcoded default sneaks back in."""
    finding = _make_finding(
        check_id="SHIP-MANIFEST-STALE-SUPPRESSION",
        severity="medium",
        agent_action="auto_apply",
    )
    summary = build_agent_summary(
        findings=[finding],
        release_decision=_make_release_decision(decision="passed"),
        json_report_path=None,
    )
    assert summary.first_recommended_action is not None
    assert summary.first_recommended_action.kind == "info"
    assert summary.first_recommended_action.command is None
    assert "apply-patches" in summary.first_recommended_action.why


def test_build_agent_summary_blocked_without_auto_appliable_surfaces_top_blocker():
    blocker = _make_finding(
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        severity="critical",
        tool_name="stripe.create_refund",
        agent_action="escalate_to_human",
    )
    summary = build_agent_summary(
        findings=[blocker],
        release_decision=_make_release_decision(
            decision="blocked",
            blockers=["SHIP-POLICY-APPROVAL-MISSING"],
            reason="1 active finding blocks release.",
        ),
    )
    assert summary.first_recommended_action is not None
    assert summary.first_recommended_action.kind == "info"
    assert "SHIP-POLICY-APPROVAL-MISSING" in summary.first_recommended_action.why
    assert "stripe.create_refund" in summary.first_recommended_action.why


def test_build_agent_summary_review_required_routes_to_review():
    review = _make_finding(
        check_id="SHIP-DOC-MISSING-DESCRIPTION",
        severity="medium",
        agent_action="escalate_to_human",
    )
    summary = build_agent_summary(
        findings=[review],
        release_decision=_make_release_decision(
            decision="review_required",
            review_items=["SHIP-DOC-MISSING-DESCRIPTION"],
            reason="1 finding requires human review.",
        ),
    )
    assert summary.verdict == "review_required"
    assert summary.review_item_count == 1
    assert summary.needs_human_review == 1
    assert summary.first_recommended_action is not None
    assert summary.first_recommended_action.kind == "info"
    assert "review item" in summary.first_recommended_action.why.lower()


def test_build_agent_summary_passed_with_no_release_decision():
    """If `release_decision` is None (e.g. minimal test fixture), the
    summary still constructs cleanly — verdict defaults to passed,
    counts are zero."""
    summary = build_agent_summary(findings=[], release_decision=None)
    assert summary.verdict == "passed"
    assert summary.blocker_count == 0
    assert summary.first_recommended_action is None


def test_build_agent_summary_excludes_suppressed_findings_from_counts():
    """Only active (non-suppressed) findings contribute to
    auto_appliable_patches and needs_human_review counts. Suppressed
    findings get agent_action == 'informational' anyway, but pin the
    behavior so a future refactor doesn't accidentally double-count."""
    suppressed = _make_finding(
        suppressed=True,
        agent_action="informational",
    )
    active = _make_finding(
        agent_action="escalate_to_human",
    )
    summary = build_agent_summary(
        findings=[suppressed, active],
        release_decision=_make_release_decision(decision="review_required"),
    )
    assert summary.needs_human_review == 1


# --- Integration: full report carries the new surface ------------------


def test_emitted_report_carries_agent_action_and_summary(tmp_path):
    """Black-box check: a real scan emits a report whose findings carry
    `agent_action` and whose top level carries `agent_summary`. The
    contract test (test_reports.py) validates the schema; this test
    validates the runtime path actually populates both."""
    import json
    from pathlib import Path

    from agents_shipgate.cli.scan import run_scan

    sample = (
        Path(__file__).resolve().parent.parent
        / "samples"
        / "support_refund_agent"
        / "shipgate.yaml"
    )

    run_scan(
        config_path=sample,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["agent_summary"] is not None
    assert payload["agent_summary"]["verdict"] in {
        "blocked",
        "review_required",
        "passed",
    }
    for finding in payload["findings"]:
        assert finding["agent_action"] in EXPECTED_AGENT_ACTIONS, (
            f"Finding {finding['check_id']!r} has unexpected agent_action "
            f"{finding['agent_action']!r}"
        )


# --- Schema integration (the v0.12 schema declares both fields) -------


def test_v12_schema_declares_agent_action_and_summary():
    """The generated v0.12 schema must declare the two new fields. If
    the schema generator silently drops them (e.g. a refactor that
    detaches the model from the generator), this test catches it."""
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    schema = json.loads(
        (repo_root / "docs" / "report-schema.v0.12.json").read_text("utf-8")
    )
    assert "agent_summary" in schema["properties"], (
        "v0.12 schema must declare agent_summary at the top level."
    )
    finding_props = schema["$defs"]["Finding"]["properties"]
    assert "agent_action" in finding_props, (
        "v0.12 schema must declare agent_action on Finding."
    )
    # The enum values must be a subset of the contract enum (extras
    # would mean schema and Python disagree).
    enum_field = finding_props["agent_action"].get("anyOf") or [
        finding_props["agent_action"]
    ]
    declared_values: set[str] = set()
    for entry in enum_field:
        if "$ref" in entry:
            ref = entry["$ref"].rsplit("/", 1)[-1]
            target = schema["$defs"].get(ref, {})
            declared_values.update(target.get("enum", []))
        if "enum" in entry:
            declared_values.update(entry["enum"])
    assert declared_values <= EXPECTED_AGENT_ACTIONS, (
        f"v0.12 schema agent_action enum diverged from contract: "
        f"{declared_values!r}"
    )


def test_v12_schema_requires_agent_summary_and_agent_action_non_nullable():
    """The v0.12 schema must REQUIRE both fields and reject ``null`` —
    otherwise the contract that "every emitted report carries them"
    is unenforceable. Regression for PR #57 review P1.2: the original
    schema generator omitted both from required lists and emitted
    `anyOf: [..., null]` for each, so a payload with the fields
    stripped or set to null would silently validate."""
    import json
    from pathlib import Path

    import jsonschema
    import pytest as _pytest

    repo_root = Path(__file__).resolve().parent.parent
    schema = json.loads(
        (repo_root / "docs" / "report-schema.v0.12.json").read_text("utf-8")
    )
    assert "agent_summary" in schema["required"], (
        "v0.12 schema must list agent_summary as required."
    )
    finding_required = set(schema["$defs"]["Finding"]["required"])
    assert "agent_action" in finding_required, (
        "v0.12 schema must list findings[].agent_action as required."
    )
    # Direct $ref form (or the inline-enum form) — neither permits null.
    summary_schema = schema["properties"]["agent_summary"]
    assert "type" not in summary_schema or summary_schema.get("type") != "null"
    assert summary_schema == {"$ref": "#/$defs/AgentSummary"}, (
        "agent_summary must be a direct $ref (no anyOf with null) so "
        "null payloads are rejected at the schema level."
    )
    action_schema = schema["$defs"]["Finding"]["properties"]["agent_action"]
    assert "anyOf" not in action_schema, (
        "agent_action must be inlined as the enum directly (no anyOf "
        "with null), otherwise null payloads would silently pass."
    )

    # End-to-end: a payload with either field missing must fail
    # validation. We construct a minimal payload from a real scan and
    # mutate it.
    from agents_shipgate.cli.scan import run_scan

    sample = repo_root / "samples" / "support_refund_agent" / "shipgate.yaml"
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        run_scan(
            config_path=sample,
            output_dir=out,
            formats=["json"],
            ci_mode="advisory",
        )
        payload = json.loads((out / "report.json").read_text("utf-8"))

    # Real payload validates.
    jsonschema.validate(payload, schema)

    # Strip agent_summary → must fail.
    stripped_summary = {k: v for k, v in payload.items() if k != "agent_summary"}
    with _pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(stripped_summary, schema)

    # Strip agent_action from one finding → must fail.
    stripped_action = json.loads(json.dumps(payload))
    if stripped_action["findings"]:
        del stripped_action["findings"][0]["agent_action"]
        with _pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(stripped_action, schema)

    # Set agent_summary to null → must fail.
    null_summary = json.loads(json.dumps(payload))
    null_summary["agent_summary"] = None
    with _pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(null_summary, schema)
