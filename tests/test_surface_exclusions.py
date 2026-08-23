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
import re
from pathlib import Path

import pytest

from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.evidence_actions import evidence_gap_headline
from agents_shipgate.core.semantic_consistency import (
    SemanticConsistencyError,
    _validate_exclusion_ledger,
    validate_semantic_consistency,
)
from agents_shipgate.core.surface_exclusions import BINDING_GAP_KINDS
from agents_shipgate.schemas.exclusions import (
    MAX_LEDGER_ENTRIES,
    SurfaceExclusion,
    SurfaceExclusionLedger,
)

#: A realistically-shaped canonical tool id — ``tool_v2`` plus a sha256, the
#: only shape ``core.tool_identity._stable_id`` produces. The synthetic short
#: id this fixture used before did not exercise the real thing.
TOOL_ID = "tool_v2_f8e7804c48c4ce36de4c20c96f8143721961b2d79a0522532b269fdd6cb527bb"

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
                accounted_by="delete_repository [server_mcp]",
            )
        ]
    )
    with pytest.raises(SemanticConsistencyError, match="which the decision does not carry"):
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
            accounted_by="zzz_gated",
        )
    ]
    ledger = SurfaceExclusionLedger.from_entries(entries)
    assert ledger.total == MAX_LEDGER_ENTRIES + 6
    assert len(ledger.entries) == MAX_LEDGER_ENTRIES
    assert ledger.truncated is True
    assert ledger.gated == 1
    assert ledger.gap_backed == 1
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


def _report_with_one_possible_tool():
    """One catalog tool the graph reaches but cannot prove complete.

    The smallest report that exercises the label rule in both directions: a
    catalog row to render a label from, and a gap that names it.
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
            possible_tool_ids=[TOOL_ID],
        ),
        tool_catalog=[
            {
                "tool_id": TOOL_ID,
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
    report.surface_exclusions = build_surface_exclusions(report)
    return report


def test_a_possibly_reachable_tool_is_recorded_as_gated():
    """One spelling, or the ledger cannot join a tool with itself (review 1).

    `partial_binding_evidence` used to name its subject by the raw canonical
    tool id while every other emitter rendered `name [provider]`. The ledger
    looked up one spelling, found the other, and wrote `not_claimed` for a tool
    the decision had gapped — `binding_coverage.gap_count: 1` beside
    `surface_exclusions.gated: 0`, which is the reported failure re-expressed
    one layer up.
    """

    report = _report_with_one_possible_tool()
    coverage = report.release_decision.evidence_coverage
    assert coverage.binding_coverage.gap_count == 1
    # No gap may label a catalog tool with its raw id — that is the spelling
    # that broke the join, and `validate_semantic_consistency` refuses it.
    assert [gap.subject for gap in coverage.evidence_gaps] == ["charge_card [billing]"]

    ledger = report.surface_exclusions
    assert [entry.accounting for entry in ledger.entries] == ["evidence_gap"]
    assert ledger.gated == 1


# --- the stages that run before a release decision exists -------------------
#
# `detect` and `trigger` narrow too, and neither has a gate to point at. Both
# emit the same record with `accounting: "route_blocked"` — the only accounting
# a stage with no decision can offer is to decline to publish one — so the
# ledger and the command's own verdict say the same thing. These paths had no
# coverage on the first pass of this PR.

_ADK_AGENT = (
    "from google.adk.agents import LlmAgent\n"
    "root_agent = LlmAgent(name='root', tools=[])\n"
)


def _detect(workspace: Path, **kwargs):
    from agents_shipgate.cli.discovery.signals import detect_workspace

    return detect_workspace(workspace, **kwargs)


def test_a_capped_discovery_walk_is_recorded_and_blocks_the_route(tmp_path):
    for index in range(12):
        (tmp_path / f"m{index}.py").write_text("x = 1\n", encoding="utf-8")

    result = _detect(tmp_path, max_python_files=5)

    assert result.python_parse_truncated is True
    (entry,) = result.surface_exclusions.entries
    assert (entry.stage, entry.subject, entry.reason) == (
        "discovery",
        ".",
        "walk_capped",
    )
    assert entry.accounting == "route_blocked"
    # The numbers a retry needs are in the record, not only in `next_action`.
    assert "5 of 12" in entry.detail
    assert result.surface_exclusions.gated == 1


def test_an_ambiguous_scope_records_every_contested_candidate(tmp_path):
    for name in ("alpha", "beta"):
        project = tmp_path / "services" / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        (project / "agent.py").write_text(_ADK_AGENT, encoding="utf-8")

    result = _detect(tmp_path)

    assert result.agent_scope == "ambiguous"
    rows = result.surface_exclusions.entries
    assert [row.stage for row in rows] == ["scope_resolution"] * 2
    assert [row.subject for row in rows] == ["services/alpha", "services/beta"]
    # Every candidate is excluded from being *the* scope until a human picks
    # one, which is what makes the list routable rather than a tie to break.
    assert {row.accounting for row in rows} == {"route_blocked"}


def test_a_rejected_source_candidate_is_recorded_but_not_routed(tmp_path):
    """A glob match the real adapter refuses is a narrowing with a reason.

    `host-mcp.json` matches `*mcp*.json` and is an mcpServers host config, not
    a tools-array export. Nothing claims it as a tool source, so it is
    reported and deliberately not routed — unlike the two cases above.
    """

    (tmp_path / "host-mcp.json").write_text(
        json.dumps({"mcpServers": {"a": {"command": "x"}}}), encoding="utf-8"
    )

    result = _detect(tmp_path)

    (entry,) = result.surface_exclusions.entries
    assert (entry.stage, entry.subject, entry.reason) == (
        "discovery",
        "host-mcp.json",
        "source_rejected",
    )
    assert entry.accounting == "not_claimed"
    assert result.surface_exclusions.gated == 0
    # The adapter's own reason survives into the record rather than being
    # flattened to the stage's generic prose.
    assert "mcpServers" in entry.detail


def test_a_settled_workspace_records_nothing(tmp_path):
    """An empty ledger is a claim too — it must not be the default everywhere."""

    (tmp_path / "agent.py").write_text(_ADK_AGENT, encoding="utf-8")

    result = _detect(tmp_path)

    assert result.python_parse_truncated is False
    assert result.agent_scope == "single"
    assert result.surface_exclusions.total == 0


# --- conservation as a property, over every bundled fixture -----------------

_SAMPLE_MANIFESTS = sorted(
    Path("samples").glob("*/shipgate.yaml"),
    key=lambda path: path.parent.name,
)


def test_every_sample_ships_a_manifest_to_check():
    """Guard the parametrization: an empty glob would make the sweep vacuous."""

    assert len(_SAMPLE_MANIFESTS) >= 14


@pytest.mark.parametrize(
    "manifest", _SAMPLE_MANIFESTS, ids=lambda path: path.parent.name
)
def test_conservation_holds_for_every_sample(manifest, tmp_path):
    """`observed == analysed ∪ excluded`, and the excluded side is accounted for.

    Enforced in `validate_semantic_consistency` at emission, so `run_scan`
    returning at all already proves it — this sweep exists so the proof is
    *stated* over the whole fixture corpus rather than depending on whichever
    samples other tests happen to scan. `benchmark/repos/` is materialized from
    `samples/` (eight of its nine archetypes are copies), so covering samples
    covers the benchmark corpus by construction.
    """

    report, _ = run_scan(
        config_path=manifest,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    graph = report.binding_surface_facts
    analysed = set(graph.reachable_tool_ids)
    excluded = set(graph.possible_tool_ids) | set(graph.unbound_tool_ids)
    observed = {str(row["tool_id"]) for row in report.tool_catalog}

    assert analysed | excluded == observed
    assert not (analysed & excluded), "a tool cannot be both analysed and excluded"

    ledger = report.surface_exclusions
    assert ledger.total == len(ledger.entries)
    assert ledger.gated == sum(
        1 for entry in ledger.entries if entry.accounting != "not_claimed"
    )
    # Every excluded tool is recorded. A sample with an empty ledger and a
    # non-empty excluded set is the silent narrowing this whole change is about.
    recorded = {entry.subject for entry in ledger.entries if entry.stage == "binding"}
    assert len(recorded) >= len({_subject_of(report, tool_id) for tool_id in excluded})

    decision = report.release_decision
    assert decision is not None
    gap_subjects = {gap.subject for gap in decision.evidence_coverage.evidence_gaps}
    for entry in ledger.entries:
        if entry.accounting == "evidence_gap":
            assert entry.subject in gap_subjects


def _subject_of(report, tool_id: str) -> str:
    from agents_shipgate.core.surface_exclusions import catalog_subject

    for row in report.tool_catalog:
        if str(row.get("tool_id")) == tool_id:
            return catalog_subject(row)
    return catalog_subject({"tool_id": tool_id})


# --- the review's regressions ----------------------------------------------


def test_an_unavailable_base_comparison_fails_closed(tmp_path):
    """A comparison that was asked for and did not happen is not evidence.

    `binding_surface_diff.enabled == False` means two different things, and
    reading the second as the first let a head scan conclude an unbound
    destructive tool was pre-existing using a comparison it never performed —
    `unbound_tools: 1`, `gap_count: 0` — which
    `docs/engineering/ai-coding-workflow-verifier.md` §2.3 forbids outright
    (PR #404 review).
    """

    base_config = _write_tree(tmp_path / "base", [_tool("safe")], ["safe"])
    head_config = _write_tree(
        tmp_path / "head",
        [_tool("safe"), _tool("delete_repository", destructive=True)],
        ["safe"],
    )
    _scan(base_config, tmp_path / "base" / "reports")
    # A base report the loader accepts and the binding diff cannot use: v0.31
    # is where `binding_surface_facts` arrived, and v0.30 still passes the
    # `--diff-from` comparability floor.
    base_report = tmp_path / "base" / "reports" / "report.json"
    payload = json.loads(base_report.read_text(encoding="utf-8"))
    payload["report_schema_version"] = "0.30"
    payload.pop("binding_surface_facts", None)
    base_report.write_text(json.dumps(payload), encoding="utf-8")

    report, _ = _scan(
        head_config, tmp_path / "head" / "reports", diff_from=base_report
    )

    diff = report.binding_surface_diff
    assert diff.enabled is False
    assert diff.base_comparison_requested is True
    coverage = report.release_decision.evidence_coverage.binding_coverage
    assert coverage.unbound_tools == 1
    assert coverage.gap_count == 1

    (gap,) = [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.subject.startswith("base comparison")
    ]
    assert gap.next_action.kind == "provide_source"

    # One row for one mechanism and one repair (#361), not one per tool — and
    # the excluded tool is `unverified`, never `not_claimed`, because that
    # word asserts a comparison nobody ran.
    (entry,) = report.surface_exclusions.entries
    assert entry.reason == "unverified_unbound_tool"
    assert entry.accounting == "unverified"


def test_a_scan_with_no_base_still_claims_nothing_it_cannot_show(tmp_path):
    """The paired case: nobody asked, so `not_claimed` stays available."""

    config = _write_tree(
        tmp_path / "solo",
        [_tool("safe"), _tool("delete_repository", destructive=True)],
        ["safe"],
    )
    report, _ = _scan(config, tmp_path / "solo" / "reports")

    assert report.binding_surface_diff.base_comparison_requested is False
    (entry,) = report.surface_exclusions.entries
    assert entry.accounting == "not_claimed"
    assert report.release_decision.evidence_coverage.binding_coverage.gap_count == 0


def test_binding_gap_kinds_covers_every_kind_the_binding_stage_emits():
    """`BINDING_GAP_KINDS` must not be a hand-kept copy that drifts.

    It was, and it omitted `invalid_binding_annotation` — a kind the release
    decision emits and routes — so a tool-scoped row of that kind left its
    ledger entry `not_claimed` while the decision carried the gap, and the
    invariant accepted the contradiction (PR #404 review). It is derived from
    the schema now; this pins that it stays derived.
    """

    from typing import get_args

    from agents_shipgate.core.surface_exclusions import BINDING_GAP_KINDS
    from agents_shipgate.schemas.bindings import AgentBindingIssue

    emitted = set(get_args(AgentBindingIssue.model_fields["kind"].annotation))
    assert emitted, "AgentBindingIssue.kind must stay a closed Literal"
    assert emitted <= BINDING_GAP_KINDS, sorted(emitted - BINDING_GAP_KINDS)
    assert "invalid_binding_annotation" in BINDING_GAP_KINDS


def test_the_cap_never_discards_a_gap_backed_row():
    """201 gated rows must all survive a 200-row cap (PR #404 review).

    Sorting them first was not enough: `rows[:limit]` still dropped one while
    the ledger went on reporting `gated=201`, so the count claimed evidence
    the entries no longer showed.
    """

    gap_backed = [
        SurfaceExclusion(
            stage="binding",
            subject=f"gated_{index:04d}",
            reason="newly_unbound_tool",
            detail="introduced by this change",
            accounting="evidence_gap",
            accounted_by=f"gated_{index:04d}",
        )
        for index in range(MAX_LEDGER_ENTRIES + 1)
    ]
    ledger = SurfaceExclusionLedger.from_entries(gap_backed)
    assert len(ledger.entries) == MAX_LEDGER_ENTRIES + 1
    assert ledger.gated == MAX_LEDGER_ENTRIES + 1
    assert ledger.truncated is False

    # The rest still gets capped, and gated rows still come first.
    mixed = gap_backed[:3] + [
        SurfaceExclusion(
            stage="binding",
            subject=f"quiet_{index:04d}",
            reason="unbound_tool",
            detail="pre-existing catalog entry",
            accounting="not_claimed",
        )
        for index in range(500)
    ]
    capped = SurfaceExclusionLedger.from_entries(mixed)
    assert len(capped.entries) == MAX_LEDGER_ENTRIES
    assert capped.truncated is True
    assert capped.total == 503
    assert capped.gap_backed == 3
    assert [row.accounting for row in capped.entries[:3]] == ["evidence_gap"] * 3

    # `route_blocked` and `unverified` are counted in `gated` and *may* be
    # capped: their accounting is one whole-run fact, which a single row proves
    # as well as five hundred. The published guarantee is about `gap_backed`,
    # and the earlier wording — "the cap never drops a gated row" — was untrue
    # of these two (PR #404 review 2).
    for accounting, reason, pointer in (
        ("route_blocked", "unclassified_change", None),
        ("unverified", "unverified_unbound_tool", "base comparison"),
    ):
        bulk = SurfaceExclusionLedger.from_entries(
            [
                SurfaceExclusion(
                    stage="binding",
                    subject=f"{accounting}_{index:04d}",
                    reason=reason,
                    detail="d",
                    accounting=accounting,
                    accounted_by=pointer,
                )
                for index in range(MAX_LEDGER_ENTRIES + 1)
            ]
        )
        assert len(bulk.entries) == MAX_LEDGER_ENTRIES
        assert bulk.gated == MAX_LEDGER_ENTRIES + 1
        assert bulk.gap_backed == 0
        assert bulk.truncated is True


def test_a_degraded_source_is_not_reported_as_a_catalog_omission(tmp_path):
    """The ledger may not invent provenance it cannot prove (PR #404 review).

    Every `source_warning` used to become "part of that input never entered
    the catalog". `samples/simple_crewai_agent` disproves it: `FileReadTool`
    is recorded as low-confidence metadata *and* is in the catalog, in the
    inventory, structurally reachable, and carrying high-confidence evidence.
    """

    report, _ = run_scan(
        config_path=Path("samples/simple_crewai_agent/shipgate.yaml"),
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.source_warnings, "the sample must still warn, or it proves nothing"
    assert not [
        entry
        for entry in report.surface_exclusions.entries
        if entry.stage == "adapter_parse"
    ]
    # The warning is unchanged where it belongs: the decision still reads it.
    assert any(
        gap.kind == "source_warning"
        for gap in report.release_decision.evidence_coverage.evidence_gaps
    )


def test_the_v035_schema_rejects_an_erased_ledger():
    """A nominally valid report must not be able to delete this PR's evidence."""

    from jsonschema import Draft202012Validator

    schema = json.loads(
        Path("docs/report-schema.v0.36.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    golden = json.loads(
        Path("samples/support_refund_agent/expected/report.json").read_text(
            encoding="utf-8"
        )
    )
    assert not list(validator.iter_errors(golden))

    for mutate in (
        lambda p: p.__setitem__("surface_exclusions", {}),
        lambda p: p["surface_exclusions"].pop("gated"),
        lambda p: p["surface_exclusions"].pop("total"),
        lambda p: p["surface_exclusions"].pop("gap_backed"),
        lambda p: p["surface_exclusions"]["entries"][0].pop("accounted_by"),
        lambda p: p["binding_surface_diff"].pop("added_unbound_tool_ids"),
        lambda p: p["binding_surface_diff"].pop("base_comparison_requested"),
        # Emitted on every diff block, nullable in value only — so unlike the
        # other stable fields here it could be deleted outright.
        lambda p: p["binding_surface_diff"].pop("base_report_schema_version"),
        lambda p: p["surface_exclusions"].__setitem__("total", -1),
    ):
        mutated = json.loads(json.dumps(golden))
        mutate(mutated)
        assert list(validator.iter_errors(mutated)), (
            "the schema accepted a payload with the ledger evidence removed"
        )


def test_the_ledger_counts_cannot_be_forged():
    """`gated` is what consumers gate on; nothing validated it (review 2).

    Both Pydantic and the generated schema accepted `entries: []` beside
    `gated: 999`, and full semantic validation accepted it too.
    """

    with pytest.raises(ValueError, match="gated cannot exceed total"):
        SurfaceExclusionLedger(entries=[], total=0, gated=999, truncated=False)
    with pytest.raises(ValueError, match="total disagrees with its entries"):
        SurfaceExclusionLedger(entries=[], total=5, gated=0, truncated=False)
    with pytest.raises(ValueError, match="cannot be negative"):
        SurfaceExclusionLedger(entries=[], total=-1, gated=0, truncated=False)
    with pytest.raises(ValueError, match="truncation it did not apply"):
        SurfaceExclusionLedger(entries=[], total=0, gated=0, truncated=True)


def test_an_accounting_that_claims_a_gap_must_name_it():
    """`accounted_by` is the join, so it cannot be absent — or invented."""

    with pytest.raises(ValueError, match="must name the gap"):
        SurfaceExclusion(
            stage="binding",
            subject="t",
            reason="unbound_tool",
            detail="d",
            accounting="evidence_gap",
        )
    with pytest.raises(ValueError, match="must name the gap"):
        SurfaceExclusion(
            stage="binding",
            subject="t",
            reason="unverified_unbound_tool",
            detail="d",
            accounting="unverified",
        )
    with pytest.raises(ValueError, match="accounted_by must be unset"):
        SurfaceExclusion(
            stage="binding",
            subject="t",
            reason="unbound_tool",
            detail="d",
            accounting="not_claimed",
            accounted_by="something",
        )


def test_two_tools_sharing_a_display_label_are_accounted_for_separately(tmp_path):
    """The join key is the canonical id, not `name [provider]` (review 2).

    Two catalog ids can legitimately render the same label. Joining on it
    marked both rows `evidence_gap` when the decision had gapped one, so the
    ledger reported twice the gating it actually had — and semantic validation
    accepted it.
    """

    from agents_shipgate.ci.release_decision import build_release_decision
    from agents_shipgate.core.surface_exclusions import build_surface_exclusions
    from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, BindingSurfaceDiff
    from agents_shipgate.schemas.report import (
        ReadinessReport,
        ReportSummary,
        ToolSurfaceSummary,
    )

    duplicated = [
        {
            "tool_id": tool_id,
            "name": "dup",
            "provider": "api",
            "source_type": "mcp",
            "source_ref": "mcp/tools.json",
        }
        for tool_id in ("tool_v2:old", "tool_v2:new")
    ]
    report = ReadinessReport(
        run_id="run-1",
        project={},
        agent={},
        environment={},
        summary=ReportSummary(status="review_required"),
        tool_surface=ToolSurfaceSummary(total_tools=2, high_risk_tools=0),
        binding_surface_facts=AgentBindingGraphAssessment(
            root_agent_id="agent",
            status="partial",
            pass_eligible=False,
            unbound_tool_ids=["tool_v2:old", "tool_v2:new"],
        ),
        binding_surface_diff=BindingSurfaceDiff(
            enabled=True,
            base_comparison_requested=True,
            added_unbound_tool_ids=["tool_v2:new"],
        ),
        tool_catalog=duplicated,
    )
    report.release_decision = build_release_decision(
        report=report,
        tools=[],
        tool_catalog=[],
        ci_mode="advisory",
        fail_on=None,
        new_findings_only=False,
    )
    gaps = report.release_decision.evidence_coverage.evidence_gaps
    assert [gap.subject_id for gap in gaps] == ["tool_v2:new"]

    ledger = build_surface_exclusions(report)
    by_reason = {entry.reason: entry for entry in ledger.entries}
    assert by_reason["newly_unbound_tool"].accounting == "evidence_gap"
    assert by_reason["unbound_tool"].accounting != "evidence_gap"
    assert ledger.gap_backed == 1


def test_first_adoption_is_not_a_failed_base_comparison():
    """A base with no gate is not a comparison that failed (review 2).

    Marking every resolved base with no report as `base_comparison_unavailable`
    swept in `missing_manifest` — first adoption — and asked the adopter to
    regenerate a base report that cannot exist. Over a partially-wired catalog
    that made adoption unfinishable unless unrelated tools were falsely bound,
    against the #385 boundary.

    The distinction already existed one function over: `safe_recovery` excludes
    `missing_manifest` for exactly this reason, and this pins that the two
    agree.
    """

    from agents_shipgate.cli.verify.orchestrator import _BASE_COMPARISON_FAILURES

    assert "missing_manifest" not in _BASE_COMPARISON_FAILURES
    # The states where a base *was* asked for and genuinely could not be read.
    assert _BASE_COMPARISON_FAILURES == {"ref_missing", "archive_failed", "scan_failed"}
    # And the states that never asked stay out too.
    assert not _BASE_COMPARISON_FAILURES & {"not_requested", "skipped"}
    assert not _BASE_COMPARISON_FAILURES & {
        "succeeded",
        "cache_hit",
        "diff_from_provided",
    }


def test_a_malformed_diff_from_still_counts_as_a_requested_comparison(tmp_path):
    """Whether the bytes parsed is not whether the caller asked (review 2).

    Reading only the successfully-loaded reference meant a malformed
    `--diff-from` reported "no comparison requested" and then asserted that an
    unbound destructive tool predated the change.
    """

    config = _write_tree(
        tmp_path / "head",
        [_tool("safe"), _tool("delete_repository", destructive=True)],
        ["safe"],
    )
    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")

    report, _ = _scan(config, tmp_path / "head" / "reports", diff_from=broken)

    diff = report.binding_surface_diff
    assert diff.enabled is False
    assert diff.base_comparison_requested is True
    assert report.release_decision.evidence_coverage.binding_coverage.gap_count == 1
    (entry,) = report.surface_exclusions.entries
    assert entry.accounting == "unverified"


def test_an_unavailable_comparison_cannot_be_erased_by_rewriting_the_rows(tmp_path):
    """The converse invariant (review 2).

    Only `unverified => base gap` was checked, so rewriting the row to
    `not_claimed` and dropping `gated` to 0 passed while the base gap still
    stood — making the new fail-closed state erasable.
    """

    config = _write_tree(
        tmp_path / "head",
        [_tool("safe"), _tool("delete_repository", destructive=True)],
        ["safe"],
    )
    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")
    report, _ = _scan(config, tmp_path / "head" / "reports", diff_from=broken)

    # Rewrite the row the run emitted, keeping its subject, so this isolates
    # the accounting rather than tripping an earlier claim.
    (original,) = report.surface_exclusions.entries
    assert original.accounting == "unverified"
    report.surface_exclusions = SurfaceExclusionLedger.from_entries(
        [
            SurfaceExclusion(
                stage="binding",
                subject=original.subject,
                reason="unbound_tool",
                detail="rewritten to look pre-existing",
                accounting="not_claimed",
            )
        ]
    )
    with pytest.raises(SemanticConsistencyError, match="could not be performed"):
        validate_semantic_consistency(report, _rehydrated_tools(report))


def test_a_proven_adapter_omission_is_recorded(tmp_path):
    """An entry the adapter dropped must reach the ledger (review 2).

    Removing the fabricated warning-derived rows left the stage with no
    emitter, so a genuinely omitted entry — one the MCP adapter reads, refuses,
    and never puts in the catalog — went unaccounted for while the report
    claimed to enumerate every narrowed subject.
    """

    workspace = tmp_path / "w"
    workspace.mkdir()
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [_tool("ok"), 7]}), encoding="utf-8"
    )
    (workspace / "shipgate.yaml").write_text(
        _MANIFEST.format(tools="{tool: ok, source_id: server_mcp}"), encoding="utf-8"
    )

    report, _ = _scan(workspace / "shipgate.yaml", tmp_path / "reports")

    (entry,) = [
        row for row in report.surface_exclusions.entries if row.stage == "adapter_parse"
    ]
    assert entry.reason == "unreadable_entry"
    assert entry.subject == "/tools/1"
    assert entry.accounting == "evidence_gap"
    # Joined to the gap by an explicit pointer, not by hoping two renderings
    # of the same thing agree.
    assert entry.accounted_by == "Skipping non-object MCP tool entry"
    assert entry.accounted_by in {
        gap.subject
        for gap in report.release_decision.evidence_coverage.evidence_gaps
    }


# --- `subject` is a label, in every gap kind --------------------------------

#: The kinds `core.policy_evidence` emits. None is a kind the ledger joins,
#: which is exactly why the scoped rule never reached them.
_POLICY_GAP_KINDS = frozenset({
    "inferred_policy_applicability",
    "mixed_policy_evidence",
    "conflicting_policy_evidence",
    "unknown_policy_evidence",
})


def test_a_policy_gap_labels_a_tool_the_way_every_other_gap_does(tmp_path):
    """Scoping the label rule to the join set left the readable half broken.

    Review 2 moved the ledger's join onto `subject_id` and documented
    `subject` as a display label. The policy gaps were outside the rule's
    scope and kept a raw 64-hex canonical id in that label — and
    `evidence_gap_headline` prints it verbatim into the CLI's
    `Improve evidence:` line, `_decision_reason`, and the GitHub step summary,
    where it names nothing a reader can open. `support.search_kb` reached one
    gap list as both `[support_mcp_tools]` and `[tool_v2_445a25…]` at once; it
    carries a reviewed effect override in the fixture now and no longer raises a
    policy gap, so the cross-stage claim below is asserted on its sibling from
    the same source.

    Identity is not lost by relabelling: every row asserted here carries it in
    `subject_id`, which is the field that now does the joining.
    """

    report, _ = run_scan(
        config_path=Path("samples/support_refund_agent/shipgate.yaml"),
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    decision = report.release_decision
    assert decision is not None
    gaps = decision.evidence_coverage.evidence_gaps
    catalog_ids = {str(row["tool_id"]) for row in report.tool_catalog}

    policy_gaps = [gap for gap in gaps if gap.kind in _POLICY_GAP_KINDS]
    assert policy_gaps, "the fixture must still raise policy evidence gaps"
    assert not ({gap.subject for gap in policy_gaps} & catalog_ids)
    # The id moved to the field built for it rather than being dropped.
    assert all(gap.subject_id in catalog_ids for gap in policy_gaps)

    # One label across stages: the tool the binding stage and the policy stage
    # both name is one string, not two. Asserted on an MCP tool because the
    # divergence this pins was between `ActionFact.provider` and the catalog's
    # own rendering of the same source id.
    label = "gmail.send_customer_email [support_mcp_tools]"
    assert label in {gap.subject for gap in policy_gaps}
    assert label in {gap.subject for gap in gaps if gap.kind in BINDING_GAP_KINDS}

    # What the reader is actually shown. The headline is the surface the raw id
    # reached, so state the claim there and not only on the stored field.
    for gap in gaps:
        assert not re.search(r"[0-9a-f]{32}", evidence_gap_headline(gap)), gap.subject


def test_an_unresolvable_tool_id_never_becomes_the_label():
    """The display fallback must not put the digest back (PR #408 review).

    A check plugin is validated on its declared `check_id`, not on tool
    membership, so it can raise a finding carrying a stale or invented
    `tool_v2_<digest>`. The old fallback rendered exactly that id when no
    catalog row and no tool name could name it — reinstating the digest for the
    one case nothing proofreads. Identity is not lost: it still travels in
    `subject_id`.
    """

    from agents_shipgate.cli.scan.decision import _gap_subject
    from agents_shipgate.schemas.report import Finding

    finding = Finding(
        check_id="ORG-PLUGIN-RULE",
        title="a plugin rule",
        severity="high",
        category="org_policy",
        tool_id="tool_v2_" + "b" * 64,
        tool_name=None,
        recommendation="declare the tool",
    )

    assert _gap_subject(finding, {}) == "ORG-PLUGIN-RULE"
    # A resolvable id still labels by the catalog, and a name still wins over
    # the check id — the fallback narrows only the unnameable case.
    assert _gap_subject(finding, {finding.tool_id: "wipe_db [ops]"}) == "wipe_db [ops]"
    assert _gap_subject(finding.model_copy(update={"tool_name": "wipe_db"}), {}) == "wipe_db"


def test_a_raw_id_is_refused_for_a_gap_kind_the_ledger_never_joins():
    """The negative control: widening the rule has to be load-bearing.

    A guard scoped to a set of kinds passes vacuously for every kind outside
    it, and that is how the policy gaps kept their digests through #403. Feed
    the invariant a gap whose kind no ledger stage reads and whose label is a
    raw catalog id — the shape the scoped rule tolerated.
    """

    report = _report_with_one_possible_tool()
    gaps = report.release_decision.evidence_coverage.evidence_gaps
    gaps.append(
        gaps[0].model_copy(
            update={"kind": "inferred_policy_applicability", "subject": TOOL_ID}
        )
    )

    with pytest.raises(SemanticConsistencyError, match="canonical id"):
        _validate_exclusion_ledger(report)


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param(TOOL_ID, id="bare"),
        pytest.param(f"charge_card [{TOOL_ID}]", id="wrapped-in-a-label"),
        pytest.param("tool_v2_" + "a" * 64, id="not-in-this-catalog"),
    ],
)
def test_a_canonical_id_is_refused_wherever_it_sits_in_the_label(subject):
    """Match the shape, not membership in this run's catalog (PR #408 review).

    A guard comparing the whole subject against `tool_catalog` misses both
    spellings that actually shipped. `inputs/policy_packs.py` wrapped the id in
    a label — `create_refund [tool_v2_6dcebe…]` — and a check plugin is
    validated on its declared `check_id`, not on tool membership, so it can
    raise a finding carrying a stale or invented id that is in no catalog to
    compare against. Both read exactly as badly as the bare form.
    """

    report = _report_with_one_possible_tool()
    gaps = report.release_decision.evidence_coverage.evidence_gaps
    gaps.append(
        gaps[0].model_copy(
            update={"kind": "inferred_policy_applicability", "subject": subject}
        )
    )

    with pytest.raises(SemanticConsistencyError, match="canonical id"):
        _validate_exclusion_ledger(report)
