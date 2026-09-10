"""Paired base/head repositories for the three classes #515 must distinguish.

A standing weakness, a strict improvement and a newly widened capability all
look identical in ``finding_deltas``: one row in ``unchanged_findings``. These
fixtures pin the difference, and pin that naming it still excludes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest
import yaml
from test_current_control import _live, _verify
from test_openapi_operation_attribution import scan, spec, workspace
from test_operation_attribution_verification import git, repository

from agents_shipgate.core.lenses.finding_attribution import (
    _APPROVAL_EFFECTS,
    _DOMAIN_EFFECTS,
    _GUARD_EFFECTS,
    _ORDER,
    attribute_findings,
    unattributed_sentence,
)
from agents_shipgate.report.markdown import _ATTRIBUTION_LABELS, render_markdown_report
from agents_shipgate.schemas.finding_attribution import FindingAttributionClass
from agents_shipgate.schemas.guard_dependencies import (
    BooleanDomainDirection,
    BooleanSourceBehavior,
    BooleanSourceComparison,
    GuardDependencyComparison,
    GuardDependencyEvidence,
)
from agents_shipgate.schemas.operation_attribution import (
    DeclaredOperation,
    OperationAttribution,
    OperationComparison,
    OperationInput,
)
from agents_shipgate.schemas.report import Finding, ReadinessReport
from agents_shipgate.schemas.surfaces import (
    ToolSurfaceDiff,
    ToolSurfaceFindingDeltaItem,
    ToolSurfaceFindingDeltas,
)


def _head(root, *, document=None, approval=False, extra=None):
    """Commit a head revision and return the base commit it is compared to."""
    base = git(root, "rev-parse", "HEAD")
    workspace(root, document, approval=approval)
    if extra:
        (root / extra).write_text("unrelated\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "head")
    return base


def _attributions(root):
    report = json.loads((root / "agents-shipgate-reports/report.json").read_text())
    diff = report["tool_surface_diff"]
    return report, diff, diff.get("finding_attributions", [])


def _render_with(diff):
    """Render one diff through the real report renderer and sample scaffolding."""
    report = ReadinessReport.model_validate(
        json.loads(Path("samples/support_refund_agent/expected/report.json").read_text())
    )
    report.tool_surface_diff = diff
    return render_markdown_report(report, sanitize_output=False)


def _predicate_row(rows):
    """The one row whose own bound the profile named by fingerprint."""
    (row,) = [
        item
        for item in rows
        if any(evidence["link"] == "predicate" for evidence in item["evidence"])
    ]
    return row


@pytest.mark.parametrize(
    "head,expected,direction",
    [
        # Nothing about the declared capability moved: the weakness stands, and
        # it is the repository's, not this change's.
        (
            {"extra": "NOTES.md"},
            "standing_weakness",
            "unchanged",
        ),
        # `cal-1`'s shape: the change bounds the capability without perfecting
        # it, so the finding survives and must not be read as its cause.
        (
            {"document": spec(("alpha",))},
            "improved_not_resolved",
            "narrowed",
        ),
        # The same identity bucket, the opposite direction.
        (
            {"document": spec(("alpha", "beta", "gamma"))},
            "widened_by_change",
            "widened",
        ),
    ],
)
def test_paired_repositories_separate_the_three_classes(tmp_path, head, expected, direction):
    root = repository(tmp_path)
    base = _head(root, **head)
    _verify(root, base=base)
    report, diff, rows = _attributions(root)
    row = _predicate_row(rows)
    # The identity comparison cannot tell these three apart; that is the point:
    # all four findings sit in one bucket in all three fixtures.
    assert [item["check_id"] for item in diff["finding_deltas"]["unchanged_findings"]] == [
        "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
        "SHIP-DOC-MISSING-DESCRIPTION",
        "SHIP-POLICY-CONFIRMATION-MISSING",
        "SHIP-POLICY-APPROVAL-MISSING",
    ]
    assert diff["finding_deltas"]["new_findings"] == []
    assert row["identity"] == "matched"
    assert row["attribution"] == expected, row
    assert row["check_id"] == "SHIP-POLICY-APPROVAL-MISSING"
    targets = next(
        item for item in row["evidence"] if item["axis"] == "declared_target_domain"
    )
    assert (targets["direction"], targets["link"]) == (direction, "predicate")
    assert targets["base_pointer"] == targets["head_pointer"] == (
        "api.json#/paths/~1documents~1{id}/delete"
    )
    # Attribution is evidence for a later decision, never a decision.
    for item in rows:
        assert item["dependency_coverage"] == "incomplete"
        assert item["finding_exclusion_eligible"] is False
    # Bounds on the same capability are recorded, but a finding whose own
    # predicate the profile never named stays unattributed in either direction.
    for item in rows:
        if item is not row:
            assert item["attribution"] == "unresolved"
            assert {evidence["link"] for evidence in item["evidence"]} == {"capability"}
    assert _live(root) is not None


def test_opposite_attributions_leave_one_identical_verdict(tmp_path):
    """The projection explains; it does not gate. Narrowing and widening the
    same declared capability produce opposite classes, the same finding
    identities and the same release decision."""
    seen = {}
    for label, document in (
        ("improved", spec(("alpha",))),
        ("widened", spec(("alpha", "beta", "gamma"))),
    ):
        (tmp_path / label).mkdir()
        root = repository(tmp_path / label)
        base = _head(root, document=document)
        _verify(root, base=base)
        report, diff, rows = _attributions(root)
        seen[label] = (
            _predicate_row(rows)["attribution"],
            report["release_decision"]["decision"],
            sorted(item["fingerprint"] for item in diff["finding_deltas"]["unchanged_findings"]),
            sorted(item["check_id"] for item in report["findings"] if not item["suppressed"]),
        )
    improved, widened = seen["improved"], seen["widened"]
    assert (improved[0], widened[0]) == ("improved_not_resolved", "widened_by_change")
    assert improved[1:] == widened[1:]


def test_naming_the_class_changes_no_verdict_and_drops_no_finding(tmp_path):
    """The improvement is still reported; only the explanation is new."""
    root = repository(tmp_path)
    base = _head(root, document=spec(("alpha",)))
    _verify(root, base=base)
    report, diff, rows = _attributions(root)
    row = _predicate_row(rows)
    assert row["attribution"] == "improved_not_resolved"
    finding = next(
        item for item in report["findings"] if item["fingerprint"] == row["fingerprint"]
    )
    assert finding["suppressed"] is False
    assert report["release_decision"]["decision"] != "passed"
    assert any(
        item["fingerprint"] == row["fingerprint"]
        for item in diff["finding_deltas"]["unchanged_findings"]
    )


def test_removing_the_approval_declaration_is_attributed_to_the_change(tmp_path):
    """A bound the base declared and the head does not is this change's finding."""
    root = tmp_path / "repo"
    workspace(root, approval=True)
    (root / ".gitignore").write_text("agents-shipgate-reports/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.test")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base declares approval")
    base = _head(root, approval=False)
    _verify(root, base=base)
    _, diff, rows = _attributions(root)
    row = _predicate_row(rows)
    # New fingerprint, and a bound that demonstrably went away.
    assert row["identity"] == "new"
    assert row["attribution"] == "widened_by_change"
    approval = next(
        item for item in row["evidence"] if item["axis"] == "approval_predicate"
    )
    assert (approval["direction"], approval["effect"]) == ("newly_missing", "widening")


_SDK_AGENT = '''from agents import Agent, function_tool
from .guards import permitted as allowed

@function_tool
def delete_refund(approved: bool, within_limit: bool) -> bool:
    if not allowed(approved, within_limit):
        return False
    return True

agent = Agent(name="Refund", tools=[delete_refund])
'''


def _sdk_workspace(root, predicate="approved and within_limit"):
    """An SDK tool whose only guard lives in a separate imported module."""
    package = root / "refund_agent"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("")
    (package / "agent.py").write_text(_SDK_AGENT)
    (package / "guards.py").write_text(
        f"def permitted(approved: bool, within_limit: bool) -> bool:\n    return {predicate}\n"
    )
    (root / "shipgate.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "0.1",
                "project": {"name": "guard-attribution-fixture"},
                "agent": {"name": "Refund", "declared_purpose": ["compare an imported guard"]},
                "environment": {"target": "local"},
                "tool_sources": [
                    {"id": "sdk", "type": "openai_agents_sdk", "path": "refund_agent/agent.py"}
                ],
                "output": {"directory": "agents-shipgate-reports", "formats": ["markdown", "json"]},
            }
        )
    )


def test_a_weakened_shared_guard_reaches_the_findings_of_an_unchanged_tool(tmp_path):
    """#515's shared-helper case, as far as the shipped profiles prove it.

    Only `refund_agent/guards.py` changes; the tool declaration file does not,
    and the finding's own fingerprint is unchanged. The finding still carries
    the widened predicate and its base/head location, and it may not be called
    a standing weakness.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _sdk_workspace(root)
    (root / ".gitignore").write_text("agents-shipgate-reports/\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.test")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    declaration = (root / "refund_agent/agent.py").read_bytes()
    _sdk_workspace(root, "approved")
    git(root, "add", "refund_agent/guards.py")
    git(root, "commit", "-qm", "weaken the shared predicate")
    _verify(root, base=base)
    assert (root / "refund_agent/agent.py").read_bytes() == declaration
    _, diff, rows = _attributions(root)
    (row,) = rows
    assert row["identity"] == "matched"
    assert [item["axis"] for item in row["evidence"]] == [
        "guard_predicate",
        "bound_true_domain",
    ]
    for item in row["evidence"]:
        # A bound that moved on this capability, carried by the guard module
        # rather than by anything the tool's own declaration says.
        assert (item["profile"], item["effect"], item["link"]) == (
            "sdk_boolean_guard/v1",
            "widening",
            "capability",
        )
        assert item["base_pointer"] == item["head_pointer"] == "refund_agent/guards.py:1"
    # Same-capability evidence explains; it never attributes on its own.
    assert row["attribution"] == "unresolved"
    assert row["finding_exclusion_eligible"] is False


def test_markdown_spells_exactly_what_the_json_block_says(tmp_path):
    root = repository(tmp_path)
    base = _head(root, document=spec(("alpha", "beta", "gamma")))
    _verify(root, base=base)
    _, _, rows = _attributions(root)
    text = (root / "agents-shipgate-reports/report.md").read_text()
    assert "### Finding attribution" in text
    assert "widened by this change" in text
    assert "declared target domain: widened (widening, predicate-linked)" in text
    assert "no finding is excluded" in text
    # The most consequential row is rendered first, and names the document.
    assert _predicate_row(rows) is rows[0]
    assert "api.json\\#/paths/~1documents~1\\{id\\}/delete" in text
    # The three same-capability-only rows here are all unresolved, so Markdown
    # counts them instead of repeating one sentence and two axes each. Their
    # evidence stays in report.json.
    assert (
        "3 finding(s) have evidence on their capability but could not be "
        "attributed in either direction" in text
    )
    assert len([row for row in rows if row["attribution"] == "unresolved"]) == 3
    assert "capability-linked" not in text


def test_a_scan_with_no_base_asks_no_question_and_attributes_nothing(tmp_path):
    """A run with nothing to compare against must not answer a diff question."""
    root = tmp_path / "repo"
    workspace(root)
    report = scan(root, tmp_path / "reports")
    diff = report.tool_surface_diff
    assert diff.operation_comparisons and diff.base.kind == "none"
    assert diff.finding_attributions == []
    # The missing base stays explicit where it already was.
    assert any("No --diff-from report" in note for note in diff.notes)


def test_a_reconstructed_git_base_asks_the_question_without_a_reference():
    """Verifier provenance a public report cannot set still earns attribution."""
    comparison = _unchanged_operation()
    diff = _diff(operations=[comparison], matched=["fp1"])
    assert diff.base.kind == "none"
    assert attribute_findings(diff, [_finding()])[0][0].attribution == "standing_weakness"
    comparison.evidence_source = "unavailable"
    assert attribute_findings(diff, [_finding()]) == ([], 0, [])


def test_a_run_with_no_comparison_profile_adds_no_rows_and_no_notes():
    rows, _, notes = attribute_findings(ToolSurfaceDiff(), [])
    assert (rows, notes) == ([], [])


@pytest.mark.parametrize(
    "table,literals",
    [
        (_APPROVAL_EFFECTS, get_args(OperationComparison.model_fields["approval_predicate"].annotation)),
        (
            _DOMAIN_EFFECTS,
            get_args(OperationComparison.model_fields["declared_target_domain"].annotation),
        ),
        (_GUARD_EFFECTS, get_args(GuardDependencyComparison.model_fields["direction"].annotation)),
        (_DOMAIN_EFFECTS, get_args(BooleanDomainDirection)),
    ],
)
def test_every_shipped_direction_has_an_effect(table, literals):
    """A profile that grows a direction must not fall through to a KeyError."""
    assert literals and set(literals) <= set(table)


def test_every_class_has_an_order_and_a_rendered_label():
    """A new class must not silently sort last or crash the Markdown renderer."""
    classes = set(get_args(FindingAttributionClass))
    assert classes == set(_ORDER) == set(_ATTRIBUTION_LABELS)
    assert len(set(_ORDER.values())) == len(classes)


# --- Classification rules the paired repositories cannot reach on their own ---


def _guard(observation="obs", *, tool_id="tool_v2_a", allowed=(1,), behavior=None):
    return GuardDependencyEvidence(
        source_id="sdk", observation_id=observation, tool_id=tool_id,
        tool_name="refund", tool_path="tools/refund.py", tool_symbol="refund", tool_line=4,
        guard_symbol="allowed", guard_path="shared/guard.py", guard_line=2,
        status="observed", reason="observed", parameters=["dry_run"],
        allowed_inputs=list(allowed), source_behavior=behavior,
    )


def _operation(fingerprints, *, tool_id="tool_v2_a", approval=True, targets=("a",)):
    return OperationAttribution(
        operation=DeclaredOperation(
            source_id="api", observation_id="op", tool_name="remove_document",
            method="delete", path_template="/documents/{id}",
            source_pointer="/paths/~1documents~1{id}/delete",
            status="observed", reason="observed", declared_targets=list(targets),
            inputs=[OperationInput(path="api.json", sha256="0" * 64, role="openapi_document")],
        ),
        tool_id=tool_id, capability_id="cap_a", finding_fingerprints=list(fingerprints),
        status="observed", reason="observed", missing_approval=approval,
    )


def _diff(*, operations=(), guards=(), matched=()):
    return ToolSurfaceDiff(
        operation_comparisons=list(operations),
        guard_comparisons=list(guards),
        finding_deltas=ToolSurfaceFindingDeltas(
            unchanged_findings=[
                ToolSurfaceFindingDeltaItem(
                    fingerprint=value, check_id="ORG-CHECK", severity="high", title="t"
                )
                for value in matched
            ]
        ),
    )


def _finding(fingerprint="fp1", *, tool_id="tool_v2_a", refs=("cap_a",)):
    return Finding(
        fingerprint=fingerprint, check_id="ORG-CHECK", title="t", severity="high",
        category="policy", tool_id=tool_id, tool_name="remove_document",
        capability_refs=list(refs), recommendation="Review it.",
    )


def _unchanged_operation(fingerprints=("fp1",)):
    row = _operation(fingerprints)
    return OperationComparison(
        observation_id="op", before=row, after=row, evidence_source="reconstructed_git_base",
        base_tree="t" * 40, declared_target_domain="unchanged",
        approval_predicate="standing_weakness", reason="compared",
    )


@pytest.mark.parametrize("direction", ["predicate_widened", "predicate_changed"])
def test_capability_evidence_withdraws_a_standing_weakness_it_cannot_confirm(direction):
    """A bound moved on this capability, so "nothing changed" is not available."""
    guard = GuardDependencyComparison(
        observation_id="obs", tool_id="tool_v2_a", tool_name="refund",
        direction=direction, reason="compared", before=_guard(), after=_guard(allowed=(1, 2)),
    )
    rows, _, _ = attribute_findings(
        _diff(operations=[_unchanged_operation()], guards=[guard], matched=["fp1"]),
        [_finding()],
    )
    (row,) = rows
    assert row.attribution == "unresolved"
    assert "another modeled bound on the same capability" in row.reason
    # Without the guard row the very same inputs are a standing weakness.
    rows, _, _ = attribute_findings(
        _diff(operations=[_unchanged_operation()], matched=["fp1"]), [_finding()]
    )
    assert rows[0].attribution == "standing_weakness"


def test_capability_widening_withdraws_an_improvement_claim():
    improvement = _unchanged_operation()
    improvement.declared_target_domain = "narrowed"
    guard = GuardDependencyComparison(
        observation_id="obs", tool_id="tool_v2_a", tool_name="refund",
        direction="predicate_widened", reason="compared",
        before=_guard(), after=_guard(allowed=(1, 2)),
    )
    rows, _, _ = attribute_findings(
        _diff(operations=[improvement], guards=[guard], matched=["fp1"]), [_finding()]
    )
    assert rows[0].attribution == "unresolved"
    assert "net direction is unresolved" in rows[0].reason
    rows, _, _ = attribute_findings(
        _diff(operations=[improvement], matched=["fp1"]), [_finding()]
    )
    assert rows[0].attribution == "improved_not_resolved"


def test_a_guard_comparison_never_joins_a_finding_by_display_name():
    """A tool name is a label; only a canonical id is an identity."""
    guard = GuardDependencyComparison(
        observation_id="obs", tool_id=None, tool_name="remove_document",
        direction="predicate_widened", reason="compared",
        before=_guard(tool_id=None), after=_guard(tool_id=None, allowed=(1, 2)),
    )
    diff = _diff(operations=[_unchanged_operation()], guards=[guard], matched=["fp1"])
    rows, _, _ = attribute_findings(diff, [_finding()])
    assert rows[0].attribution == "standing_weakness"
    assert not [item for item in rows[0].evidence if item.profile == "sdk_boolean_guard/v1"]
    # A finding carrying no canonical id is not joined to a guard either, so
    # it draws no row at all rather than one built on a name match.
    rows, _, _ = attribute_findings(diff, [_finding("fp2", tool_id=None, refs=())])
    assert not rows


def test_the_whole_function_domain_is_carried_as_its_own_axis():
    behavior = BooleanSourceBehavior(
        status="observed", reason="observed", parameters=["dry_run"], returns=["false", "true"],
    )
    guard = GuardDependencyComparison(
        observation_id="obs", tool_id="tool_v2_a", tool_name="refund",
        direction="predicate_unchanged", reason="compared",
        before=_guard(behavior=behavior), after=_guard(behavior=behavior),
        source_behavior=BooleanSourceComparison(
            returns="unchanged", true_domain="unchanged", binding="unchanged",
            bound_true_domain="widened", reason="compared",
        ),
    )
    rows, _, _ = attribute_findings(
        _diff(operations=[_unchanged_operation()], guards=[guard], matched=["fp1"]), [_finding()]
    )
    bound = next(item for item in rows[0].evidence if item.axis == "bound_true_domain")
    assert (bound.effect, bound.link) == ("widening", "capability")
    assert bound.base_pointer == bound.head_pointer == "shared/guard.py:2"
    # The literal true-domain widening is what withdraws the negative claim.
    assert rows[0].attribution == "unresolved"


def test_two_findings_sharing_one_identity_are_never_attributed():
    """A profile row names a fingerprint, not a finding; a tie is unresolvable."""
    diff = _diff(operations=[_unchanged_operation()], matched=["fp1"])
    twin = _finding().model_copy(update={"check_id": "ORG-OTHER"})
    rows, _, _ = attribute_findings(diff, [_finding(), twin])
    assert [row.attribution for row in rows] == ["unresolved", "unresolved"]
    assert all("share this identity" in row.reason for row in rows)
    # A suppressed twin is not an active finding and does not create the tie.
    rows, _, _ = attribute_findings(
        diff, [_finding(), twin.model_copy(update={"suppressed": True})]
    )
    assert [row.attribution for row in rows] == ["standing_weakness"]


def test_a_fingerprint_no_identity_bucket_names_is_never_reported_as_new():
    """Without identity buckets there is no base match, and no claim of one."""
    diff = _diff(operations=[_unchanged_operation()])
    assert diff.finding_deltas.new_findings == []
    (row,) = attribute_findings(diff, [_finding()])[0]
    assert row.identity == "unresolved"
    assert row.attribution == "unresolved"
    assert "not an unchanged base identity (unresolved)" in row.reason


def test_accepted_debt_keeps_its_bucket_and_is_never_a_standing_weakness():
    diff = _diff(operations=[_unchanged_operation()])
    diff.finding_deltas.accepted_debt = [
        ToolSurfaceFindingDeltaItem(
            fingerprint="fp1", check_id="ORG-CHECK", severity="high", title="t",
            baseline_status="matched",
        )
    ]
    (row,) = attribute_findings(diff, [_finding()])[0]
    assert row.identity == "accepted_debt"
    assert row.attribution == "unresolved"


def test_an_unattributed_finding_is_counted_rather_than_dropped_silently():
    rows, _, notes = attribute_findings(
        _diff(operations=[_unchanged_operation()], matched=["fp1"]),
        [_finding(), _finding("fp2", tool_id="tool_v2_other", refs=())],
    )
    assert [row.fingerprint for row in rows] == ["fp1"]
    assert any("1 active finding(s) have no comparison profile evidence" in note for note in notes)
    assert any("no finding is excluded" in note for note in notes)


def test_a_suppressed_finding_is_not_attributed():
    finding = _finding().model_copy(update={"suppressed": True})
    rows, _, notes = attribute_findings(
        _diff(operations=[_unchanged_operation()], matched=["fp1"]), [finding]
    )
    assert rows == []
    assert not [note for note in notes if "active finding" in note]


# --- The statements that must survive every truncation on the way out ---


def test_the_uncompared_count_survives_the_three_note_report_limit():
    """`report.md` prints three diff notes. The absence statement is not one."""
    diff = _diff(operations=[_unchanged_operation()], matched=["fp1"])
    rows, unattributed, notes = attribute_findings(
        diff, [_finding(), _finding("fp2", tool_id="tool_v2_other", refs=())]
    )
    assert (unattributed, [row.fingerprint for row in rows]) == (1, ["fp1"])
    diff.finding_attributions = rows
    diff.unattributed_findings = unattributed
    # Four notes ahead of it, exactly as an enabled diff produces.
    diff.notes = ["first", "second", "third", *notes]
    diff.enabled = True
    text = _render_with(diff)
    # The note carrying the same statement is truncated away, as it is today.
    assert "more comparison notes in report.json" in text
    assert notes[-1] not in text.split("Notes:", 1)[1].split("##", 1)[0]
    # The section states it anyway.
    section = text.split("### Finding attribution", 1)[1].split("- Base:", 1)[0]
    assert unattributed_sentence(1) in section


def test_a_finding_with_no_identity_at_all_is_counted_not_dropped():
    """Both identity fields are optional; a plugin check can supply neither."""
    diff = _diff(operations=[_unchanged_operation()], matched=["fp1"])
    nameless = _finding().model_copy(update={"fingerprint": None, "id": None})
    rows, unattributed, notes = attribute_findings(diff, [_finding(), nameless])
    assert [row.fingerprint for row in rows] == ["fp1"]
    assert unattributed == 1
    assert unattributed_sentence(1) in notes


def test_an_improvement_does_not_claim_a_continuity_its_identity_denies():
    improvement = _unchanged_operation()
    improvement.declared_target_domain = "narrowed"
    matched, *_ = attribute_findings(
        _diff(operations=[improvement], matched=["fp1"]), [_finding()]
    )
    assert matched[0].attribution == "improved_not_resolved"
    assert "the finding still stands" in matched[0].reason
    # The same direction against a fingerprint the base never matched must not
    # be described as a finding that survived the change.
    fresh, *_ = attribute_findings(_diff(operations=[improvement]), [_finding()])
    assert (fresh[0].attribution, fresh[0].identity) == ("improved_not_resolved", "unresolved")
    assert "still stands" not in fresh[0].reason
    assert "present at head (identity unresolved)" in fresh[0].reason


def test_an_unlocated_guard_publishes_no_pointer_rather_than_the_tool_s():
    """A pointer under a guard axis names the guard or nothing at all."""
    unlocated = _guard().model_copy(update={"guard_path": None, "guard_line": None})
    comparison = GuardDependencyComparison(
        observation_id="obs", tool_id="tool_v2_a", tool_name="refund",
        direction="unresolved", reason="predicate_or_dependency_evidence_unresolved",
        before=unlocated, after=_guard(),
    )
    rows, *_ = attribute_findings(
        _diff(operations=[_unchanged_operation()], guards=[comparison], matched=["fp1"]),
        [_finding()],
    )
    guard = next(item for item in rows[0].evidence if item.profile == "sdk_boolean_guard/v1")
    assert guard.base_pointer is None
    assert guard.head_pointer == "shared/guard.py:2"
    assert "tools/refund.py" not in rows[0].model_dump_json()


def test_a_section_with_no_direction_says_so_instead_of_promising_rows():
    """A profile that publishes no fingerprint can never reach a direction."""
    diff = _diff(operations=[_unchanged_operation(("nomatch",))], matched=["fp1"])
    rows, unattributed, _ = attribute_findings(
        diff, [_finding(), _finding("fp2", tool_id="tool_v2_other", refs=())]
    )
    assert [row.attribution for row in rows] == ["unresolved"]
    diff.finding_attributions, diff.unattributed_findings = rows, unattributed
    section = _render_with(diff).split("### Finding attribution", 1)[1].split("- Base:", 1)[0]
    assert "No finding could be attributed to this change in either direction." in section
    assert "What this change did to the bound" not in section
    assert "\n\n\n" not in section
    assert unattributed_sentence(1) in section
    # The decided lead comes back as soon as a row reaches a direction.
    widened = _unchanged_operation()
    widened.declared_target_domain = "widened"
    diff = _diff(operations=[widened], matched=["fp1"])
    diff.finding_attributions = attribute_findings(diff, [_finding()]).rows
    section = _render_with(diff).split("### Finding attribution", 1)[1].split("- Base:", 1)[0]
    assert "What this change did to the bound each finding depends on." in section
    assert "\n\n\n" not in section


def test_an_uncomparable_bound_elsewhere_does_not_withdraw_an_improvement():
    """The two negative claims claim different amounts, so they withdraw
    differently: `standing_weakness` needs every same-capability axis
    unchanged, while a demonstrated narrowing survives an axis nobody could
    compare and is withdrawn only by a demonstrated widening."""
    improvement = _unchanged_operation()
    improvement.declared_target_domain = "narrowed"
    unresolved_guard = GuardDependencyComparison(
        observation_id="obs", tool_id="tool_v2_a", tool_name="refund",
        direction="unresolved", reason="base_or_head_guard_evidence_unavailable",
        after=_guard(),
    )
    rows, *_ = attribute_findings(
        _diff(operations=[improvement], guards=[unresolved_guard], matched=["fp1"]),
        [_finding()],
    )
    assert rows[0].attribution == "improved_not_resolved"
    assert {item.effect for item in rows[0].evidence if item.link == "capability"} == {
        "unresolved"
    }
    # The same unresolved axis does defeat the wider claim.
    rows, *_ = attribute_findings(
        _diff(
            operations=[_unchanged_operation()], guards=[unresolved_guard], matched=["fp1"]
        ),
        [_finding()],
    )
    assert rows[0].attribution == "unresolved"
    assert "could not be compared" in rows[0].reason
