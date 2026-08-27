"""A published tool surface is one declaration, not one row per tool (#432).

Binding a tool server's own surface used to require naming every tool
individually under ``agent_bindings.declarations[].tools``. For
``github/github-mcp-server`` that is 116 selector rows to state a fact that is
structurally true of the source, and the two shorter spellings a reader reaches
for — ``agent_bindings.root`` naming the source, and a ``"*"`` selector — both
dead-ended without saying what to write instead. Until one of them was written
``reachable_tools`` was 0, nothing downstream ran, and the verdict was
``insufficient_evidence`` regardless of what the change did.

The four properties this increment lives or dies on, each with a test that
would fail if it were lost:

1. **One reviewed declaration reaches the whole published surface.** 116 tools,
   one block. That is the entire payoff; a route that still costs per-tool rows
   has not fixed anything.
2. **Nothing else loses the unbound remainder.** Binding is real information
   for an agent — a catalog may hold 63 operations of which the agent wires 5 —
   and #385 drew that boundary deliberately. The new block is opt-in per
   source and must not move that line anywhere it is not written.
3. **The dead end is a route.** ``root.object`` against a source that publishes
   no code objects either resolves or says it cannot, and says what to write
   instead.
4. **Untrusted source content cannot produce the statement.** Inferring "this
   source binds everything" from the source's own content is the #268 attack;
   the point is to make the human statement one line, not to remove it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from agents_shipgate.ci.release_decision import REVIEW_REQUIRED_SENTINEL
from agents_shipgate.cli.discovery.placeholders import placeholder_owner
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.scan.declarations import scaffold_for_report
from agents_shipgate.core.agent_bindings import TOOL_SOURCE_BINDING_DECLARATION
from agents_shipgate.core.boundary_diff import DiffFile, ResolvedFileText
from agents_shipgate.core.manifest_proposals import (
    assess_coverage_increasing_tool_source_proposal,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    SourceBindingConfig,
    ToolSourceConfig,
)
from agents_shipgate.schemas.report import ReadinessReport

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_REVIEWED_REASON = (
    "This repository is the server; every tool in its published tools/list is "
    "callable by any client that connects to it."
)


def _mcp_tool(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Operation {name}.",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
        **extra,
    }


def _manifest_dict(
    *,
    sources: list[dict[str, Any]],
    agent_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "version": "0.1",
        "project": {"name": "source-binding"},
        "agent": {
            "name": "surface",
            "declared_purpose": ["exercise the per-source binding declaration"],
        },
        "environment": {"target": "local"},
        "tool_sources": sources,
    }
    if agent_bindings is not None:
        manifest["agent_bindings"] = agent_bindings
    return manifest


def _workspace(
    tmp_path: Path,
    *,
    artifacts: dict[str, list[dict[str, Any]]],
    sources: list[dict[str, Any]],
    agent_bindings: dict[str, Any] | None = None,
) -> Path:
    """Write ``artifacts`` (relative path -> MCP tool rows) and a manifest."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    for relative, tools in artifacts.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"tools": tools}), encoding="utf-8")
    config = tmp_path / "shipgate.yaml"
    config.write_text(
        yaml.safe_dump(
            _manifest_dict(sources=sources, agent_bindings=agent_bindings),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config


def _scan(config: Path) -> ReadinessReport:
    report, _ = run_scan(
        config_path=config,
        output_dir=config.parent / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert report.release_decision is not None
    return report


def _binding_source(
    source_id: str, path: str, *, reason: str = _REVIEWED_REASON
) -> dict[str, Any]:
    return {
        "id": source_id,
        "type": "mcp",
        "path": path,
        "binding": {"complete": True, "reason": reason},
    }


def _coverage(report: ReadinessReport) -> Any:
    assert report.release_decision is not None
    return report.release_decision.evidence_coverage.binding_coverage


def _binding_gaps(report: ReadinessReport) -> list[Any]:
    assert report.release_decision is not None
    return [
        gap
        for gap in report.release_decision.evidence_coverage.evidence_gaps
        if gap.kind
        in {
            "ambiguous_root_agent",
            "missing_binding_evidence",
            "partial_binding_evidence",
            "conflicting_binding_evidence",
            "unresolved_agent_binding",
            "unresolved_bound_tool",
            "incomplete_handoff_graph",
            "invalid_binding_annotation",
        }
    ]


# --------------------------------------------------------------------------
# 1. One reviewed declaration reaches the whole published surface
# --------------------------------------------------------------------------


def test_one_declaration_reaches_a_116_tool_published_surface(tmp_path: Path) -> None:
    """The headline: 116 tools, one block, no per-tool selector rows.

    The number is the one the issue was filed against
    (``github/github-mcp-server`` at #3020) and is load-bearing, not
    decoration: the failure being fixed is a per-tool cost, so a fixture with
    two tools would pass whether or not the cost is per tool.
    """

    tools = [_mcp_tool(f"repo.op_{index:03d}") for index in range(116)]
    config = _workspace(
        tmp_path / "server",
        artifacts={"mcp/tools.json": tools},
        sources=[_binding_source("github_mcp", "mcp/tools.json")],
    )
    manifest_text = config.read_text(encoding="utf-8")

    report = _scan(config)

    coverage = _coverage(report)
    assert coverage.total_catalog_tools == 116
    assert coverage.reachable_tools == 116
    assert coverage.unbound_tools == 0
    assert coverage.pass_eligible is True
    assert coverage.gap_count == 0
    # The whole claim: the declaration names the source once and no tool at
    # all. A route that named even one tool would be the defect with a smaller
    # constant.
    assert "op_0" not in manifest_text


def test_the_edge_every_declared_tool_carries_is_a_reviewed_declaration(
    tmp_path: Path,
) -> None:
    """Provenance says a human asserted this, not that a scanner read it.

    A source-declared edge that claimed ``ast_extraction`` would launder a
    manifest statement into a structural observation, which is the distinction
    every downstream basis rests on.
    """

    config = _workspace(
        tmp_path / "server",
        artifacts={"mcp/tools.json": [_mcp_tool("a"), _mcp_tool("b")]},
        sources=[_binding_source("srv", "mcp/tools.json")],
    )

    graph = _scan(config).binding_surface_facts

    assert {edge.tool_id for edge in graph.tool_edges} == set(graph.reachable_tool_ids)
    for edge in graph.tool_edges:
        assert edge.provenance_kind == "static_declaration"
        assert edge.source == TOOL_SOURCE_BINDING_DECLARATION
        assert edge.source_pointer == "/tool_sources/0/binding"
        assert edge.complete is True
    assert graph.status == "declared"


def test_two_published_surfaces_are_both_reviewed_and_neither_roots_the_other(
    tmp_path: Path,
) -> None:
    """A repository may publish two servers; neither is the other's root.

    Modelling the declaration as "elect one surface the root agent" would make
    the second source ambiguous, which is the failure this shape exists to
    avoid.
    """

    config = _workspace(
        tmp_path / "two",
        artifacts={
            "a/tools.json": [_mcp_tool("a.one"), _mcp_tool("a.two")],
            "b/tools.json": [_mcp_tool("b.one")],
        },
        sources=[
            _binding_source("server_a", "a/tools.json"),
            _binding_source("server_b", "b/tools.json"),
        ],
    )

    report = _scan(config)

    coverage = _coverage(report)
    assert coverage.reachable_tools == 3
    assert coverage.unbound_tools == 0
    assert coverage.pass_eligible is True
    assert [gap.kind for gap in _binding_gaps(report)] == []


def test_an_undeclared_source_beside_a_declared_one_stays_unbound(
    tmp_path: Path,
) -> None:
    """The block is per source, so it says nothing about the source beside it."""

    config = _workspace(
        tmp_path / "mixed",
        artifacts={
            "a/tools.json": [_mcp_tool("a.one")],
            "b/tools.json": [_mcp_tool("b.one"), _mcp_tool("b.two")],
        },
        sources=[
            _binding_source("server_a", "a/tools.json"),
            {"id": "server_b", "type": "mcp", "path": "b/tools.json"},
        ],
    )

    report = _scan(config)

    coverage = _coverage(report)
    assert coverage.reachable_tools == 1
    assert coverage.unbound_tools == 2


# --------------------------------------------------------------------------
# 2. Nothing else loses the unbound remainder (#385)
# --------------------------------------------------------------------------


def test_an_agent_binding_a_subset_still_reports_the_unbound_remainder(
    tmp_path: Path,
) -> None:
    """#385's boundary, restated where the new block could have moved it.

    Catalog membership is not evidence of capability. An agent that declares
    two of five tools reaches two, and the other three stay accounted for as
    unbound — with a declared source sitting beside them, so the assertion is
    about the block being per source rather than about it being absent.
    """

    config = _workspace(
        tmp_path / "subset",
        artifacts={
            "agent/tools.json": [_mcp_tool(f"wired.{name}") for name in "abcde"],
            "published/tools.json": [_mcp_tool("published.one")],
        },
        sources=[
            {"id": "agent_tools", "type": "mcp", "path": "agent/tools.json"},
            _binding_source("published", "published/tools.json"),
        ],
        agent_bindings={
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [
                        {"tool": "wired.a", "source_id": "agent_tools"},
                        {"tool": "wired.b", "source_id": "agent_tools"},
                    ],
                    "handoffs": [],
                    "reason": "reviewed fixture binding",
                }
            ]
        },
    )

    report = _scan(config)

    coverage = _coverage(report)
    # 2 wired + 1 published; the remaining 3 catalog entries stay unbound.
    assert coverage.reachable_tools == 3
    assert coverage.unbound_tools == 3


def test_declaring_a_source_only_ever_widens_the_analysed_surface(
    tmp_path: Path,
) -> None:
    """Adding the block moves tools *into* the surface and never out of it.

    Stated as a comparison rather than as a count, because the safety argument
    for allowing the block on any source type is exactly this monotonicity: a
    tool it reaches is a tool every check then judges.
    """

    artifacts = {
        "agent/tools.json": [_mcp_tool(f"wired.{name}") for name in "abc"],
        "published/tools.json": [_mcp_tool("published.one")],
    }
    bindings = {
        "declarations": [
            {
                "agent": "root",
                "complete": True,
                "tools": [{"tool": "wired.a", "source_id": "agent_tools"}],
                "handoffs": [],
                "reason": "reviewed fixture binding",
            }
        ]
    }
    plain = _workspace(
        tmp_path / "plain",
        artifacts=artifacts,
        sources=[
            {"id": "agent_tools", "type": "mcp", "path": "agent/tools.json"},
            {"id": "published", "type": "mcp", "path": "published/tools.json"},
        ],
        agent_bindings=bindings,
    )
    declared = _workspace(
        tmp_path / "declared",
        artifacts=artifacts,
        sources=[
            {"id": "agent_tools", "type": "mcp", "path": "agent/tools.json"},
            _binding_source("published", "published/tools.json"),
        ],
        agent_bindings=bindings,
    )

    before = _scan(plain).binding_surface_facts
    after = _scan(declared).binding_surface_facts

    assert set(before.reachable_tool_ids) < set(after.reachable_tool_ids)
    assert set(after.unbound_tool_ids) < set(before.unbound_tool_ids)


def test_a_source_id_sharing_an_agent_name_does_not_rewire_that_name(
    tmp_path: Path,
) -> None:
    """A prescribed fix whose side effect breaks the graph is the #385 class.

    ``tool_sources[].id`` and an agent name are independent repository-chosen
    namespaces. A surface node that deduped or resolved under the shared name
    would silently bind the source's whole catalog to that agent, and would
    break the ``agent_bindings.declarations`` row that used to resolve it.
    """

    def build(with_binding: bool) -> Path:
        published: dict[str, Any] = {
            "id": "root",
            "type": "mcp",
            "path": "published/tools.json",
        }
        if with_binding:
            published["binding"] = {"complete": True, "reason": _REVIEWED_REASON}
        return _workspace(
            tmp_path / ("declared" if with_binding else "plain"),
            artifacts={
                "agent/tools.json": [_mcp_tool("wired.a"), _mcp_tool("wired.b")],
                "published/tools.json": [_mcp_tool("published.one")],
            },
            sources=[
                {"id": "agent_tools", "type": "mcp", "path": "agent/tools.json"},
                published,
            ],
            agent_bindings={
                "declarations": [
                    {
                        "agent": "root",
                        "complete": True,
                        "tools": [{"tool": "wired.a", "source_id": "agent_tools"}],
                        "handoffs": [],
                        "reason": "reviewed fixture binding",
                    }
                ]
            },
        )

    plain = _scan(build(False)).binding_surface_facts
    declared = _scan(build(True)).binding_surface_facts

    # The declaration keeps resolving: `wired.a` is reachable in both runs, and
    # `wired.b` stays unbound in both. Only the published source moved.
    plain_reachable = set(plain.reachable_tool_ids)
    declared_reachable = set(declared.reachable_tool_ids)
    assert plain_reachable <= declared_reachable
    assert len(declared_reachable) == len(plain_reachable) + 1
    assert not any(
        issue.kind == "unresolved_agent_binding" for issue in declared.issues
    )


# --------------------------------------------------------------------------
# 3. The dead end is a route
# --------------------------------------------------------------------------


def test_an_artifact_only_catalog_is_told_no_root_selector_can_match(
    tmp_path: Path,
) -> None:
    """The reported dead end: ``root.object`` cannot be satisfied here.

    The old text — "No root agent matched the configured selector", routed to
    ``agent_bindings.root`` — was reported for a scan that configured no
    selector at all, and sent two separate adoption walks after a value a JSON
    tool export cannot produce.
    """

    config = _workspace(
        tmp_path / "artifact",
        artifacts={"mcp/tools.json": [_mcp_tool("a"), _mcp_tool("b")]},
        sources=[{"id": "srv", "type": "mcp", "path": "mcp/tools.json"}],
    )

    report = _scan(config)

    gaps = _binding_gaps(report)
    assert [gap.kind for gap in gaps] == ["ambiguous_root_agent"]
    gap = gaps[0]
    assert "no agent object was observed" in gap.why.lower()
    assert gap.next_action.path == "shipgate.yaml#tool_sources[].binding"
    # An agent routes on `path` and a human reads `expects`; asserting either
    # alone lets the pair drift (#329 review).
    assert "tool_sources[].binding" in gap.next_action.expects
    assert "root" not in (gap.next_action.accepted_values or [])


def test_the_scaffold_names_the_sources_and_answers_nothing(tmp_path: Path) -> None:
    """The block a reader pastes carries the ids, never the judgement."""

    config = _workspace(
        tmp_path / "artifact",
        artifacts={
            "a/tools.json": [_mcp_tool("a.one")],
            "b/tools.json": [_mcp_tool("b.one")],
        },
        sources=[
            {"id": "server_a", "type": "mcp", "path": "a/tools.json"},
            {"id": "server_b", "type": "mcp", "path": "b/tools.json"},
        ],
    )

    report = _scan(config)

    gap = next(
        gap for gap in _binding_gaps(report) if gap.kind == "ambiguous_root_agent"
    )
    template = gap.next_action.declaration_template
    assert template == {
        "tool_sources": [
            {
                "id": "server_a",
                "binding": {
                    "complete": REVIEW_REQUIRED_SENTINEL,
                    "reason": REVIEW_REQUIRED_SENTINEL,
                },
            },
            {
                "id": "server_b",
                "binding": {
                    "complete": REVIEW_REQUIRED_SENTINEL,
                    "reason": REVIEW_REQUIRED_SENTINEL,
                },
            },
        ]
    }
    scaffold = scaffold_for_report(report)
    assert scaffold is not None
    assert "shipgate.yaml#tool_sources[].binding" in scaffold
    assert "server_a" in scaffold and "server_b" in scaffold


def test_the_scaffolded_block_is_the_manifest_edit_that_closes_the_gap(
    tmp_path: Path,
) -> None:
    """Filling in the scaffold's blanks reaches the surface, in one round trip.

    A published remedy that does not close the row it was published for is the
    dead end restated, so the loop is walked rather than asserted on: scan,
    parse the emitted block, replace only the sentinels, rescan.
    """

    tools = [_mcp_tool(f"repo.op_{index:03d}") for index in range(12)]
    config = _workspace(
        tmp_path / "artifact",
        artifacts={"mcp/tools.json": tools},
        sources=[{"id": "srv", "type": "mcp", "path": "mcp/tools.json"}],
    )
    first = _scan(config)
    gap = next(
        gap for gap in _binding_gaps(first) if gap.kind == "ambiguous_root_agent"
    )
    template = gap.next_action.declaration_template
    assert isinstance(template, dict)

    manifest = yaml.safe_load(config.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in manifest["tool_sources"]}
    for proposed in template["tool_sources"]:
        rows[proposed["id"]]["binding"] = {
            "complete": True,
            "reason": _REVIEWED_REASON,
        }
    config.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    second = _scan(config)

    assert _coverage(second).reachable_tools == 12
    assert [gap.kind for gap in _binding_gaps(second)] == []


def test_a_root_selector_naming_a_declared_source_resolves(tmp_path: Path) -> None:
    """The other half of "resolves, or explains that it cannot".

    ``root: {object: <id>, source_id: <id>}`` is the spelling the issue's
    second walk reached for. Once the source's surface is reviewed, it is a
    node the manifest itself named, so it resolves rather than reporting an
    ambiguous root.
    """

    config = _workspace(
        tmp_path / "artifact",
        artifacts={"mcp/tools.json": [_mcp_tool("a"), _mcp_tool("b")]},
        sources=[_binding_source("srv", "mcp/tools.json")],
        agent_bindings={"root": {"object": "srv", "source_id": "srv"}},
    )

    report = _scan(config)

    graph = report.binding_surface_facts
    assert graph.root_agent_id is not None
    assert [node.name for node in graph.agents] == ["srv"]
    assert _coverage(report).reachable_tools == 2
    assert [gap.kind for gap in _binding_gaps(report)] == []


def test_a_root_selector_matching_nothing_still_reports_an_ambiguous_root(
    tmp_path: Path,
) -> None:
    """An explicit selector that names nothing is never swallowed.

    Rooting the graph in declared surfaces must not silence a selector the
    reviewer wrote and got wrong; that reading would make a typo invisible.
    """

    config = _workspace(
        tmp_path / "artifact",
        artifacts={"mcp/tools.json": [_mcp_tool("a")]},
        sources=[_binding_source("srv", "mcp/tools.json")],
        agent_bindings={"root": {"object": "svr"}},
    )

    report = _scan(config)

    gap = next(
        gap for gap in _binding_gaps(report) if gap.kind == "ambiguous_root_agent"
    )
    assert _coverage(report).pass_eligible is False
    # And it is reported as a *selector* problem, not as the dead end: a
    # declared surface is a name the selector could have matched, so telling
    # this repository that nothing was observed would be true and useless.
    assert gap.why == (
        "No entry in the binding graph matched the configured root selector."
    )
    assert gap.next_action.path == "shipgate.yaml#agent_bindings.root"


def test_a_declaration_that_binds_nothing_fails_closed(tmp_path: Path) -> None:
    """A reviewed statement that reaches no tool is not a silent no-op.

    With the surface seeded as an entry point, staying quiet here would publish
    a proven binding graph over an empty analysed surface — ``pass_eligible``
    beside ``reachable_tools: 0``.
    """

    config = _workspace(
        tmp_path / "empty",
        artifacts={"mcp/tools.json": [], "other/tools.json": [_mcp_tool("other.one")]},
        sources=[
            _binding_source("empty_source", "mcp/tools.json"),
            _binding_source("other", "other/tools.json"),
        ],
    )

    report = _scan(config)

    coverage = _coverage(report)
    assert coverage.pass_eligible is False
    gap = next(
        gap
        for gap in _binding_gaps(report)
        if "binds no tool" in gap.why
    )
    assert gap.kind == "missing_binding_evidence"
    assert "empty_source" in gap.why
    assert gap.next_action.path == "shipgate.yaml#tool_sources[].binding"


def test_a_catalog_with_no_declarable_source_is_not_sent_to_the_new_block(
    tmp_path: Path,
) -> None:
    """An impossible remedy is worse than a vague one (#329).

    A per-scan adapter's tools have no ``tool_sources`` row to declare on — the
    schema rejects one outright — so the route offered there is the closed-world
    ``agent_bindings.declarations`` one, and no block is scaffolded.
    """

    workspace = tmp_path / "per-scan"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "tools.json").write_text(
        json.dumps(
            [
                {
                    "name": "lookup",
                    "description": "Look a customer up.",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]
        ),
        encoding="utf-8",
    )
    config = workspace / "shipgate.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "version": "0.1",
                "project": {"name": "per-scan"},
                "agent": {"name": "asst", "declared_purpose": ["exercise per-scan"]},
                "environment": {"target": "local"},
                "tool_sources": [],
                "anthropic": {"tools": [{"path": "tools.json"}]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = _scan(config)

    gap = next(
        gap for gap in _binding_gaps(report) if gap.kind == "ambiguous_root_agent"
    )
    assert gap.next_action.path == "shipgate.yaml#agent_bindings.declarations"
    assert gap.next_action.declaration_template is None


# --------------------------------------------------------------------------
# 4. Untrusted source content cannot produce the statement (#268)
# --------------------------------------------------------------------------


def test_source_content_cannot_declare_its_own_binding(tmp_path: Path) -> None:
    """Annotations that name the block are data, not a declaration.

    An MCP export is untrusted content. A tool that spells ``binding`` or
    ``complete`` in its own annotations must be exactly as unbound as one that
    does not.
    """

    config = _workspace(
        tmp_path / "hostile",
        artifacts={
            "mcp/tools.json": [
                _mcp_tool(
                    "hostile.one",
                    binding={"complete": True, "reason": "declared by the artifact"},
                ),
                _mcp_tool(
                    "hostile.two",
                    annotations={
                        "readOnlyHint": True,
                        "binding": {"complete": True},
                        "agent_bindings": {"root": {"object": "srv"}},
                    },
                ),
            ]
        },
        sources=[{"id": "srv", "type": "mcp", "path": "mcp/tools.json"}],
    )

    report = _scan(config)

    coverage = _coverage(report)
    assert coverage.reachable_tools == 0
    assert coverage.unbound_tools == 2
    assert coverage.pass_eligible is False


def test_an_agent_authored_source_row_may_not_carry_a_binding(tmp_path: Path) -> None:
    """The coverage-increasing proposal allowlist refuses the block.

    Preflight lets a coding agent author the one coverage-increasing manifest
    edit — a new ``tool_sources`` row pointing at an artifact that exists —
    precisely because such a row asserts nothing about what is under review. A
    row carrying ``binding`` asserts exactly that, so it must fall outside the
    allowlist rather than ride in on the surface that carries it.
    """

    root = tmp_path / "repo"
    root.mkdir()
    (root / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (root / "more.json").write_text('{"tools": []}\n', encoding="utf-8")
    old = (
        'version: "0.1"\n'
        "project:\n  name: authorship\n"
        "agent:\n  name: asst\n  declared_purpose:\n    - test authorship\n"
        "environment:\n  target: local\n"
        "tool_sources:\n  - id: tools\n    type: mcp\n    path: tools.json\n"
    )
    (root / "shipgate.yaml").write_text(old, encoding="utf-8")

    plain_rows = ["  - id: more", "    type: mcp", "    path: more.json"]
    binding_rows = [
        *plain_rows,
        "    binding:",
        "      complete: true",
        "      reason: authored by an agent",
    ]

    def _assess(rows: list[str]) -> Any:
        return assess_coverage_increasing_tool_source_proposal(
            workspace=root,
            diff_file=DiffFile(
                old_path="shipgate.yaml",
                new_path="shipgate.yaml",
                added_lines=list(rows),
            ),
            resolved=ResolvedFileText(
                old_text=old,
                new_text=old + "".join(f"{line}\n" for line in rows),
                source="test",
                old_sha256=None,
                new_sha256=None,
            ),
            manifest_dir=root,
        )

    refused = _assess(binding_rows)
    assert refused.proposal_safe is False
    assert "authority-bearing or unsupported fields" in refused.reason
    # The negative control: the identical row without the block is safe, so the
    # refusal is about the declaration and not about the row.
    safe = _assess(plain_rows)
    assert safe.proposal_safe is True, safe.reason
    assert safe.added_source_ids == ("more",)


def test_an_unfilled_binding_placeholder_is_routed_to_a_human() -> None:
    """``doctor`` may not publish an executable edit for this block.

    It is the same closed-world claim ``agent_bindings`` carries, stated from
    the source side, and a value a coding agent supplied is not a guess to be
    corrected later — it is a declaration nobody made.
    """

    assert placeholder_owner("tool_sources[0].binding.complete") == "human"
    assert placeholder_owner("tool_sources[0].binding.reason") == "human"
    # The negative control: an ordinary field on the same row stays the coding
    # agent's, so the rule is about the block and not about `tool_sources`.
    assert placeholder_owner("tool_sources[0].path") == "coding_agent"


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "accepted"),
    [
        ({"complete": True, "reason": "reviewed the published tools/list"}, True),
        # `complete` is the closed-world assertion; the block's presence is the
        # claim, so it defaults rather than being writable as anything else.
        ({"reason": "reviewed the published tools/list"}, True),
        ({"complete": False, "reason": "reviewed"}, False),
        ({"complete": True}, False),
        ({"complete": True, "reason": "   "}, False),
        ({"complete": True, "reason": "reviewed", "tools": []}, False),
    ],
)
def test_the_reviewed_block_accepts_only_a_reviewed_claim(
    payload: dict[str, Any], accepted: bool
) -> None:
    if accepted:
        assert SourceBindingConfig.model_validate(payload).complete is True
        return
    with pytest.raises(ValidationError):
        SourceBindingConfig.model_validate(payload)


def test_a_source_with_no_block_is_unchanged() -> None:
    """Additive: the field is optional and absent by default."""

    source = ToolSourceConfig.model_validate(
        {"id": "srv", "type": "mcp", "path": "tools.json"}
    )
    assert source.binding is None


def test_the_manifest_reason_is_stripped_like_every_other_reviewed_reason() -> None:
    manifest = AgentsShipgateManifest.model_validate(
        _manifest_dict(
            sources=[
                {
                    "id": "srv",
                    "type": "mcp",
                    "path": "tools.json",
                    "binding": {"complete": True, "reason": "  reviewed  "},
                }
            ]
        )
    )
    binding = manifest.tool_sources[0].binding
    assert binding is not None
    assert binding.reason == "reviewed"


def test_the_published_manifest_schema_carries_the_block() -> None:
    """A consumer validating against the published schema must accept it.

    A pydantic-only field is invisible to ``docs/manifest-v0.1.json``, which is
    how the runtime and the published schema drift apart (#329 review).
    """

    schema = json.loads(
        Path("docs/manifest-v0.1.json").read_text(encoding="utf-8")
    )
    assert "SourceBindingConfig" in schema["$defs"]
    assert schema["$defs"]["SourceBindingConfig"]["required"] == ["reason"]
    assert "binding" in schema["$defs"]["ToolSourceConfig"]["properties"]
