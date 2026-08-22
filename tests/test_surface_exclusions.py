"""The exclusion ledger and the conservation invariant behind it (#403).

The reproduction in ``test_a_destructive_tool_added_by_the_diff_is_gated`` is
``github/github-mcp-server#3076`` in miniature: a published MCP server whose
reviewed declaration is complete at the base commit, a PR that adds one
``destructiveHint: true`` tool to the same source, and a declaration nobody
updated — because updating ``shipgate.yaml`` is a trust-root edit a coding
agent cannot self-approve. Before #403 that run reported ``unbound_tools: 1``
beside ``gap_count: 0`` and ``pass_eligible: true``: the tool was dropped from
the analysed surface before ``SHIP-POLICY-APPROVAL-MISSING`` and
``SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING`` could see it.

The paired test below it is the boundary that must survive the fix: a tool
source is often a catalog, and a catalog entry nobody wired is not a capability
claim (#385). It has to stay visible in the ledger and stay out of the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.semantic_consistency import (
    SemanticConsistencyError,
    validate_semantic_consistency,
)
from agents_shipgate.schemas.exclusions import (
    MAX_LEDGER_ENTRIES,
    SurfaceExclusion,
    SurfaceExclusionLedger,
)

_MANIFEST = """
version: "0.1"
project: {{name: mcp-server}}
agent:
  name: server
  declared_purpose: [publish a tool surface]
environment: {{target: production_like}}
tool_sources:
  - id: server_mcp
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools: [{tools}]
      handoffs: []
      reason: reviewed published tool surface
"""


def _tool(name: str, *, destructive: bool = False) -> dict[str, object]:
    return {
        "name": name,
        "description": f"{name} tool",
        "inputSchema": {
            "type": "object",
            "properties": {"owner": {"type": "string"}},
            "required": ["owner"],
        },
        "annotations": {
            "readOnlyHint": not destructive,
            "destructiveHint": destructive,
            "idempotentHint": False,
        },
    }


def _write_tree(root: Path, tools: list[dict[str, object]], declared: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "tools.json").write_text(json.dumps({"tools": tools}), encoding="utf-8")
    selectors = ", ".join(
        f"{{tool: {name}, source_id: server_mcp}}" for name in declared
    )
    (root / "shipgate.yaml").write_text(
        _MANIFEST.format(tools=selectors), encoding="utf-8"
    )
    return root / "shipgate.yaml"


def _scan(config: Path, out: Path, *, diff_from: Path | None = None):
    return run_scan(
        config_path=config,
        output_dir=out,
        formats=["json"],
        ci_mode="advisory",
        diff_from_path=diff_from,
        packet_enabled=False,
    )


def test_a_destructive_tool_added_by_the_diff_is_gated(tmp_path):
    base_config = _write_tree(
        tmp_path / "base", [_tool("list_issues"), _tool("get_repo")], ["list_issues", "get_repo"]
    )
    head_config = _write_tree(
        tmp_path / "head",
        [_tool("list_issues"), _tool("get_repo"), _tool("delete_repository", destructive=True)],
        # Unchanged: the declaration lives in the trust root, and the PR that
        # adds the tool does not touch it.
        ["list_issues", "get_repo"],
    )
    _scan(base_config, tmp_path / "base" / "reports")
    report, _ = _scan(
        head_config,
        tmp_path / "head" / "reports",
        diff_from=tmp_path / "base" / "reports" / "report.json",
    )

    graph = report.binding_surface_facts
    assert len(graph.unbound_tool_ids) == 1, "the new tool is still excluded from analysis"
    assert report.binding_surface_diff.enabled is True
    assert report.binding_surface_diff.added_unbound_tool_ids == graph.unbound_tool_ids

    decision = report.release_decision
    assert decision is not None
    coverage = decision.evidence_coverage.binding_coverage
    assert coverage.unbound_tools == 1
    # The whole point: the count and the gate no longer disagree.
    assert coverage.gap_count > 0
    assert decision.decision != "passed"

    named = [
        gap
        for gap in decision.evidence_coverage.evidence_gaps
        if "delete_repository" in gap.subject
    ]
    assert named, "the excluded tool must be named by an evidence gap"
    assert named[0].kind == "missing_binding_evidence"
    assert named[0].next_action.path == "shipgate.yaml#agent_bindings.declarations"

    ledger = report.surface_exclusions
    rows = [entry for entry in ledger.entries if "delete_repository" in entry.subject]
    assert [row.reason for row in rows] == ["newly_unbound_tool"]
    assert rows[0].accounting == "evidence_gap"
    assert rows[0].stage == "binding"
    assert ledger.gated >= 1


def test_declaring_the_new_tool_clears_the_gap(tmp_path):
    """The remedy the gap prescribes actually closes it.

    A prescribed fix whose own side effect blocks it is a bug class this
    repository has hit twice (#385, #386), so the A/B is worth pinning: the
    only difference here is the declaration listing the third tool.
    """

    base_config = _write_tree(
        tmp_path / "base", [_tool("list_issues"), _tool("get_repo")], ["list_issues", "get_repo"]
    )
    head_config = _write_tree(
        tmp_path / "head",
        [_tool("list_issues"), _tool("get_repo"), _tool("delete_repository", destructive=True)],
        ["list_issues", "get_repo", "delete_repository"],
    )
    _scan(base_config, tmp_path / "base" / "reports")
    report, _ = _scan(
        head_config,
        tmp_path / "head" / "reports",
        diff_from=tmp_path / "base" / "reports" / "report.json",
    )

    assert report.binding_surface_facts.unbound_tool_ids == []
    assert report.surface_exclusions.total == 0
    decision = report.release_decision
    assert decision is not None
    # Now that the tool is inside the analysed surface the destructive-capability
    # checks can see it, which is the outcome the fail-open denied.
    assert any(
        "delete_repository" in (finding.evidence.get("tool") or "")
        or "delete_repository" in finding.title
        for finding in report.findings
    )


def test_a_pre_existing_unbound_tool_is_recorded_and_not_gated(tmp_path):
    """The #385 boundary: catalog membership is not a capability claim.

    ``samples/large_multi_framework_agent`` has 58 of these by design. They
    must appear in the ledger — being told the gate did not look is the whole
    deliverable — and they must not turn a declared spec into a self-block.
    """

    tools = [_tool("list_issues"), _tool("get_repo"), _tool("delete_repository", destructive=True)]
    base_config = _write_tree(tmp_path / "base", tools, ["list_issues", "get_repo"])
    head_config = _write_tree(tmp_path / "head", tools, ["list_issues", "get_repo"])
    _scan(base_config, tmp_path / "base" / "reports")
    report, _ = _scan(
        head_config,
        tmp_path / "head" / "reports",
        diff_from=tmp_path / "base" / "reports" / "report.json",
    )

    assert len(report.binding_surface_facts.unbound_tool_ids) == 1
    assert report.binding_surface_diff.added_unbound_tool_ids == []
    rows = [
        entry
        for entry in report.surface_exclusions.entries
        if "delete_repository" in entry.subject
    ]
    assert [row.reason for row in rows] == ["unbound_tool"]
    assert rows[0].accounting == "not_claimed"
    assert report.surface_exclusions.gated == 0
    decision = report.release_decision
    assert decision is not None
    assert decision.evidence_coverage.binding_coverage.gap_count == 0


def test_a_plain_scan_has_no_base_and_gates_nothing_new(tmp_path):
    """No base report, no newly-excluded set — a scan is unchanged by #403."""

    config = _write_tree(
        tmp_path / "solo",
        [_tool("list_issues"), _tool("delete_repository", destructive=True)],
        ["list_issues"],
    )
    report, _ = _scan(config, tmp_path / "solo" / "reports")
    assert report.binding_surface_diff.enabled is False
    assert report.binding_surface_diff.added_unbound_tool_ids == []
    assert [row.reason for row in report.surface_exclusions.entries] == ["unbound_tool"]
    assert report.surface_exclusions.gated == 0


# --- the invariant itself ---------------------------------------------------


def test_conservation_rejects_a_gated_exclusion_with_no_gap(tmp_path):
    """Negative control: the ledger cannot claim an accounting it lacks.

    Mutating a fixture the way #403 describes — drop the gate, keep the
    record — must be caught rather than emitted.
    """

    config = _write_tree(
        tmp_path / "solo",
        [_tool("list_issues"), _tool("delete_repository", destructive=True)],
        ["list_issues"],
    )
    report, _ = _scan(config, tmp_path / "solo" / "reports")
    tools = _rehydrated_tools(report)
    report.surface_exclusions = SurfaceExclusionLedger.from_entries(
        [
            SurfaceExclusion(
                stage="binding",
                subject="delete_repository [server_mcp]",
                reason="unbound_tool",
                detail="claims a gap that was never emitted",
                accounting="evidence_gap",
            )
        ]
    )
    with pytest.raises(SemanticConsistencyError, match="claims an evidence gap"):
        validate_semantic_consistency(report, tools)


def test_conservation_rejects_an_ungated_new_exclusion(tmp_path):
    """Negative control: a subject the diff introduced cannot be `not_claimed`."""

    config = _write_tree(
        tmp_path / "solo",
        [_tool("list_issues"), _tool("delete_repository", destructive=True)],
        ["list_issues"],
    )
    report, _ = _scan(config, tmp_path / "solo" / "reports")
    report.surface_exclusions = SurfaceExclusionLedger.from_entries(
        [
            SurfaceExclusion(
                stage="binding",
                subject="delete_repository [server_mcp]",
                reason="newly_unbound_tool",
                detail="introduced by the change and left ungated",
                accounting="not_claimed",
            )
        ]
    )
    with pytest.raises(SemanticConsistencyError, match="introduced by this change"):
        validate_semantic_consistency(report, _rehydrated_tools(report))


def test_ledger_counts_survive_truncation():
    entries = [
        SurfaceExclusion(
            stage="binding",
            subject=f"tool_{index:04d}",
            reason="unbound_tool",
            detail="pre-existing catalog entry",
            accounting="not_claimed",
        )
        for index in range(MAX_LEDGER_ENTRIES + 5)
    ] + [
        SurfaceExclusion(
            stage="binding",
            subject="zzz_gated",
            reason="newly_unbound_tool",
            detail="introduced by this change",
            accounting="evidence_gap",
        )
    ]
    ledger = SurfaceExclusionLedger.from_entries(entries)
    assert ledger.total == MAX_LEDGER_ENTRIES + 6
    assert len(ledger.entries) == MAX_LEDGER_ENTRIES
    assert ledger.truncated is True
    assert ledger.gated == 1
    # The gated row sorts last alphabetically and would be the first casualty
    # of a naive prefix cut; it has to survive, or the invariant check loses
    # the very row that proves the subject was accounted for.
    assert ledger.entries[0].subject == "zzz_gated"


def _rehydrated_tools(report):
    """The analysed tools a report was built from, for re-validation.

    ``validate_semantic_consistency`` compares the report against the tool
    objects the scan assessed. The tests above mutate a finished report, so
    they need those objects back; the reachable catalog rows carry everything
    the checks read.
    """

    from agents_shipgate.core.domain import Tool
    from agents_shipgate.core.semantic_assessment import attach_semantic_assessments

    reachable = set(report.binding_surface_facts.reachable_tool_ids)
    tools = [
        Tool(
            id=str(row["tool_id"]),
            name=str(row["name"]),
            description="",
            source_type=str(row["source_type"]),
            source_id=row.get("source_id"),
            source_ref=row.get("source_ref"),
        )
        for row in report.tool_catalog
        if str(row.get("tool_id")) in reachable
    ]
    return attach_semantic_assessments(tools, {}, copy_tools=False)


def test_a_possibly_reachable_tool_is_recorded_as_gated():
    """One spelling, or the ledger cannot join a tool with itself (review 1).

    `partial_binding_evidence` used to name its subject by the raw canonical
    tool id while every other emitter rendered `name [provider]`. The ledger
    looked up one spelling, found the other, and wrote `not_claimed` for a tool
    the decision had gapped — `binding_coverage.gap_count: 1` beside
    `surface_exclusions.gated: 0`, which is the reported failure re-expressed
    one layer up.
    """

    from agents_shipgate.ci.release_decision import build_release_decision
    from agents_shipgate.core.surface_exclusions import build_surface_exclusions
    from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment
    from agents_shipgate.schemas.report import (
        ReadinessReport,
        ReportSummary,
        ToolSurfaceSummary,
    )

    report = ReadinessReport(
        run_id="run-1",
        project={},
        agent={},
        environment={},
        summary=ReportSummary(status="review_required"),
        tool_surface=ToolSurfaceSummary(total_tools=1, high_risk_tools=0),
        binding_surface_facts=AgentBindingGraphAssessment(
            root_agent_id="agent",
            status="partial",
            pass_eligible=False,
            possible_tool_ids=["tool_v1:abc"],
        ),
        tool_catalog=[
            {
                "tool_id": "tool_v1:abc",
                "name": "charge_card",
                "provider": "billing",
                "source_type": "mcp",
                "source_ref": "mcp/tools.json",
            }
        ],
    )
    report.release_decision = build_release_decision(
        report=report,
        tools=[],
        tool_catalog=[],
        ci_mode="advisory",
        fail_on=None,
        new_findings_only=False,
    )
    coverage = report.release_decision.evidence_coverage
    assert coverage.binding_coverage.gap_count == 1
    # No gap may name a catalog tool by its raw id — that is the spelling that
    # broke the join, and `validate_semantic_consistency` now refuses it.
    assert [gap.subject for gap in coverage.evidence_gaps] == ["charge_card [billing]"]

    ledger = build_surface_exclusions(report)
    assert [entry.accounting for entry in ledger.entries] == ["evidence_gap"]
    assert ledger.gated == 1
