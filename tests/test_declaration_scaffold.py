"""The declaration scaffold assembles what the engine already generates.

It must never assert a value a human owns, and it must never be mistaken for
something that closes a gap on its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import yaml

from agents_shipgate.cli.scan.declarations import build_declaration_scaffold
from agents_shipgate.schemas.report import EvidenceGap, EvidenceGapAction


def _report(gaps: list[EvidenceGap]) -> list[EvidenceGap]:
    return gaps


def _gap(kind: str, path: str, template: dict | None) -> EvidenceGap:
    return EvidenceGap(
        kind=kind,  # type: ignore[arg-type]
        subject="lookup_order [openai_sdk_agent]",
        source_type="sdk_function",
        source_ref="risk_hint",
        why="test",
        next_action=EvidenceGapAction(
            kind="declare_action_effect",  # type: ignore[arg-type]
            path=path,
            why="test",
            expects="test",
            declaration_template=template,
        ),
    )


def test_scaffold_is_none_when_nothing_is_owed() -> None:
    assert build_declaration_scaffold(_report([])) is None
    without_template = _report([_gap("incomplete_surface", "shipgate.yaml", None)])
    assert build_declaration_scaffold(without_template) is None


def test_two_gaps_on_one_tool_merge_into_one_pasteable_row() -> None:
    """Two blocks for one tool would be invalid to paste into the manifest."""

    path = "shipgate.yaml#action_surface.actions[tool='lookup_order']"
    report = _report(
        [
            _gap("inferred_effect_only", path, {"tool": "lookup_order", "effect": "<REVIEW_REQUIRED>"}),
            _gap(
                "missing_authority_evidence",
                path,
                {"tool": "lookup_order", "authority": {"mode": "<REVIEW_REQUIRED>"}},
            ),
        ]
    )
    scaffold = build_declaration_scaffold(report)
    assert scaffold is not None
    assert scaffold.count("tool: lookup_order") == 1
    assert "closes: inferred_effect_only, missing_authority_evidence" in scaffold

    body = yaml.safe_load(scaffold)
    assert body == {
        "tool": "lookup_order",
        "effect": "<REVIEW_REQUIRED>",
        "authority": {"mode": "<REVIEW_REQUIRED>"},
    }


def test_scaffold_asserts_nothing_and_says_so() -> None:
    report = _report(
        [
            _gap(
                "inferred_effect_only",
                "shipgate.yaml#action_surface.actions[tool='lookup_order']",
                {"tool": "lookup_order", "effect": "<REVIEW_REQUIRED>"},
            )
        ]
    )
    scaffold = build_declaration_scaffold(report)
    assert scaffold is not None
    # The value a human owns is never guessed...
    assert "<REVIEW_REQUIRED>" in scaffold
    # ...and the file says a sentinel closes nothing, so a reader cannot
    # mistake pasting it verbatim for satisfying the gap.
    assert "closes nothing" in scaffold


def test_distinct_tools_stay_separate_blocks() -> None:
    report = _report(
        [
            _gap(
                "inferred_effect_only",
                "shipgate.yaml#action_surface.actions[tool='a']",
                {"tool": "a", "effect": "<REVIEW_REQUIRED>"},
            ),
            _gap(
                "inferred_effect_only",
                "shipgate.yaml#action_surface.actions[tool='b']",
                {"tool": "b", "effect": "<REVIEW_REQUIRED>"},
            ),
        ]
    )
    scaffold = build_declaration_scaffold(report)
    assert scaffold is not None
    assert "tool: a" in scaffold
    assert "tool: b" in scaffold
    assert scaffold.count("# merge into:") == 2


def test_gap_provenance_distinguishes_inherited_from_introduced(tmp_path) -> None:
    """An abstention a repository already owed must not read as an accusation
    about the current diff — and a genuinely new gap must still say so."""

    import json as _json

    from agents_shipgate.cli.verify.orchestrator import (
        _evidence_gap_identities,
        _gap_provenance_note,
    )
    from agents_shipgate.schemas.report import (
        EvidenceCoverageDecision,
        ReleaseDecision,
    )

    def _report_with(gaps: list[EvidenceGap]):
        class _R:
            release_decision = ReleaseDecision.model_construct(
                decision="insufficient_evidence",
                reason="test",
                evidence_coverage=EvidenceCoverageDecision.model_construct(
                    level="mixed", evidence_gaps=gaps
                ),
            )

        return _R()

    effect = _gap(
        "inferred_effect_only",
        "shipgate.yaml#action_surface.actions[tool='a']",
        {"tool": "a", "effect": "<REVIEW_REQUIRED>"},
    )
    authority = _gap(
        "missing_authority_evidence",
        "shipgate.yaml#action_surface.actions[tool='b']",
        {"tool": "b", "authority": {"mode": "<REVIEW_REQUIRED>"}},
    )

    def _base_file(gaps: list[EvidenceGap]) -> Path:
        path = tmp_path / f"base-{len(gaps)}.json"
        path.write_text(
            _json.dumps(
                {
                    "release_decision": {
                        "evidence_coverage": {
                            "evidence_gaps": [
                                {"kind": g.kind, "subject": g.subject} for g in gaps
                            ]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    # Same gap set on both sides: inherited.
    inherited = _gap_provenance_note(
        report=_report_with([effect]), base_report=_base_file([effect])
    )
    assert inherited is not None
    assert "no new evidence gap" in inherited
    assert "suggested-declarations.yaml" in inherited

    # A gap absent from the base is introduced by this diff.
    introduced = _gap_provenance_note(
        report=_report_with([effect, authority]), base_report=_base_file([effect])
    )
    assert introduced is not None
    assert "1 of 2 evidence gap(s) are new" in introduced

    # Without a readable base there is no basis to claim anything.
    assert (
        _gap_provenance_note(report=_report_with([effect]), base_report=None) is None
    )
    missing = tmp_path / "nope.json"
    assert (
        _gap_provenance_note(report=_report_with([effect]), base_report=missing) is None
    )
    unreadable = tmp_path / "bad.json"
    unreadable.write_text("not json{", encoding="utf-8")
    assert (
        _gap_provenance_note(report=_report_with([effect]), base_report=unreadable)
        is None
    )
    assert _evidence_gap_identities("nonsense") is None


def test_authority_template_is_fillable_against_the_manifest_schema() -> None:
    """A template a human cannot fill is worse than no template.

    The manifest requires `auth_type` for every authority mode except `none`,
    and non-empty `scopes` for `scoped`. A template offering `mode` alone
    produced a config error for the most common answer, which nobody noticed
    while the templates were only reachable by walking report.json.
    """

    from agents_shipgate.schemas.manifest.action_surface import ActionDeclarationConfig

    # Shape of the shipped template, with a reviewer's answers filled in.
    filled = {
        "tool": "process_order",
        "effect": "write",
        "scopes": ["orders:write"],
        "authority": {"mode": "scoped", "auth_type": "api_key"},
    }
    declaration = ActionDeclarationConfig.model_validate(filled)
    assert declaration.authority is not None
    assert declaration.authority.mode == "scoped"

    # `none` takes neither co-required field, which is why the scaffold tells
    # the reviewer to delete what their answer does not take.
    minimal = ActionDeclarationConfig.model_validate(
        {"tool": "process_order", "effect": "read", "authority": {"mode": "none"}}
    )
    assert minimal.authority is not None


def test_no_shipped_template_asserts_on_a_humans_behalf() -> None:
    """A template must ask, never answer.

    The binding template once shipped `complete: true`, `tools: []` and
    `handoffs: []` — a claim that the agent definitively reaches no tools —
    which a reviewer could paste while sentinels were still present. Every
    scalar a template offers must therefore be a sentinel, and every list must
    be empty or sentinel-filled, so a verbatim paste cannot state a fact.
    """

    from agents_shipgate.ci.release_decision import REVIEW_REQUIRED_SENTINEL

    def assert_no_assertion(node, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert_no_assertion(value, f"{path}.{key}")
            return
        if isinstance(node, list):
            for index, value in enumerate(node):
                assert_no_assertion(value, f"{path}[{index}]")
            return
        # Selector fields identify WHICH row the declaration is about, and
        # ``agent``/``handoffs`` name which agents. They are read off the
        # observed surface, not judged by a human, so they are not assertions
        # the scaffold is making on the reviewer's behalf. The index is
        # stripped so a list member (`handoffs[0]`) is judged as its field.
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if leaf in {
            "tool",
            "tool_id",
            "source_id",
            "source_type",
            "provider",
            "agent",
            "handoffs",
        }:
            return
        assert node == REVIEW_REQUIRED_SENTINEL, (
            f"{path} = {node!r} asserts a value the human owns"
        )

    for template in _shipped_templates():
        assert_no_assertion(template)


def _shipped_templates() -> list[dict]:
    """Every declaration_template the decision engine can emit."""

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool

    tool = Tool(
        id="t1",
        name="process_order",
        source_type="sdk_function",
        source_id="openai_sdk_agent",
    )
    # An ADK tool as well: ``incomplete_surface`` only scaffolds an inventory
    # for a source type that HAS a tool_inventories key, so a guard run only
    # over sdk_function would never see that template at all.
    adk_tool = Tool(
        id="t2",
        name="create_quote",
        source_type="google_adk",
        source_id="adk_agent",
    )
    # The binding templates are emitted from _binding_coverage, not
    # _semantic_gap, so enumerate them explicitly — a guard that misses the
    # template which actually carried an assertion is false confidence.
    templates: list[dict] = [
        dict(rd.AGENT_BINDINGS_ROOT_TEMPLATE),
        _binding_declarations_template(),
    ]
    for source in (tool, adk_tool):
        for kind in (
            "inferred_effect_only",
            "missing_authority_evidence",
            "partial_authority_evidence",
            "unresolved_tool_selector",
            "incomplete_surface",
        ):
            gap = rd._semantic_gap(source, kind=kind, why="test")
            template = gap.next_action.declaration_template
            if template:
                templates.append(template)
    assert templates, "expected at least one shipped template"
    return templates


def _binding_declarations_template() -> dict:
    """The closed-world declaration scaffolded for an unbound catalog (#361)."""

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool
    from agents_shipgate.schemas.bindings import (
        AgentBindingGraphAssessment,
        AgentBindingIssue,
        AgentBindingNode,
        AgentHandoffBindingEdge,
    )

    catalog = [
        Tool(id="t1", name="create_quote", source_type="google_adk", source_id="adk"),
        Tool(id="t2", name="send_email", source_type="google_adk", source_id="adk"),
    ]
    graph = AgentBindingGraphAssessment(
        root_agent_id="agent:root",
        status="partial",
        agents=[
            AgentBindingNode(agent_id="agent:root", name="Closer", source_id="adk"),
            AgentBindingNode(agent_id="agent:sub", name="Helper", source_id="adk"),
        ],
        handoff_edges=[
            AgentHandoffBindingEdge(
                source_agent_id="agent:root",
                target_agent_id="agent:sub",
                edge_type="subagent",
                confidence="high",
                provenance_kind="static_declaration",
                source="agent.py",
            )
        ],
        unbound_tool_ids=["t1", "t2"],
        issues=[
            AgentBindingIssue(
                kind="missing_binding_evidence",
                message="no static edge",
                agent_id="agent:root",
            )
        ],
    )
    template = rd._binding_declaration_template(graph, graph.issues[0], catalog)
    assert template is not None
    return template


def test_unfilled_sentinel_is_rejected_by_the_manifest() -> None:
    """The scaffold's promise, enforced rather than merely stated.

    A pasted-but-unfinished block used to LOAD: the manifest only checked that
    `authority.auth_type` was non-blank, so `<REVIEW_REQUIRED>` satisfied it and
    the declaration was assessed as reviewed evidence — moving a fixture from
    `insufficient_evidence` to `review_required` on placeholders alone.
    """

    import pytest

    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    base = {
        "version": "0.1",
        "project": {"name": "p"},
        "agent": {"name": "a", "declared_purpose": ["do a thing"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "s1", "type": "mcp", "path": "tools.json"}],
    }

    with pytest.raises(ValueError) as caught:
        AgentsShipgateManifest.model_validate(
            {
                **base,
                "action_surface": {
                    "actions": [
                        {
                            "tool": "lookup",
                            "effect": "read",
                            "scopes": ["orders:read"],
                            "authority": {
                                "mode": "scoped",
                                "auth_type": "<REVIEW_REQUIRED>",
                            },
                        }
                    ]
                },
            }
        )
    message = str(caught.value)
    assert "unfilled scaffold placeholder" in message
    assert "action_surface.actions[0].authority.auth_type" in message

    # A sentinel anywhere is rejected, including inside a list.
    with pytest.raises(ValueError):
        AgentsShipgateManifest.model_validate(
            {**base, "agent": {**base["agent"], "declared_purpose": ["<REVIEW_REQUIRED>"]}}
        )

    # The same manifest with reviewed values loads.
    AgentsShipgateManifest.model_validate(
        {
            **base,
            "action_surface": {
                "actions": [
                    {
                        "tool": "lookup",
                        "effect": "read",
                        "scopes": ["orders:read"],
                        "authority": {"mode": "scoped", "auth_type": "api_key"},
                    }
                ]
            },
        }
    )


def test_same_name_tools_render_distinguishable_selectors() -> None:
    """Two canonical tools sharing a display name must not collapse."""

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool

    gaps = [
        rd._semantic_gap(
            Tool(id=f"tool-{src}", name="lookup", source_type="sdk_function", source_id=src),
            kind="inferred_effect_only",
            why="test",
        )
        for src in ("alpha", "beta")
    ]
    scaffold = build_declaration_scaffold(gaps)
    assert scaffold is not None
    docs = [doc for doc in yaml.safe_load_all(scaffold) if doc]
    assert len(docs) == 2
    # Same display name, different rows — each resolves exactly one tool.
    assert {doc["tool"] for doc in docs} == {"lookup"}
    assert {doc["tool_id"] for doc in docs} == {"tool-alpha", "tool-beta"}


def test_binding_scaffold_only_offers_a_root_when_one_could_match() -> None:
    """A decorator-only repo has no agent object for a root selector to name."""

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.schemas.bindings import (
        AgentBindingGraphAssessment,
        AgentBindingIssue,
    )
    from agents_shipgate.schemas.report import ReadinessReport

    def _gaps(agents: list, kind: str):
        report = ReadinessReport.model_construct(
            binding_surface_facts=AgentBindingGraphAssessment(
                root_agent_id=None,
                status="partial",
                agents=agents,
                issues=[AgentBindingIssue(kind=kind, message="test")],
            )
        )
        _coverage, gaps = rd._binding_coverage(report)
        return gaps

    no_agents = _gaps([], "ambiguous_root_agent")
    assert no_agents
    assert no_agents[0].next_action.declaration_template is None
    assert "suggested-declarations" not in (no_agents[0].next_action.expects or "")


# --- #388: the scaffold says what a legal answer looks like ------------------


def _strip_comments(scaffold: str) -> list[str]:
    """The scaffold's YAML lines, with every comment line removed."""

    return [
        line
        for line in scaffold.splitlines()
        if not line.lstrip().startswith("#") and line not in {"", "---"}
    ]


def _blocks(scaffold: str) -> list[list[str]]:
    """The scaffold's documents, each as its raw lines (comments included)."""

    blocks: list[list[str]] = []
    for line in scaffold.splitlines():
        if line == "---":
            blocks.append([])
        elif blocks:
            blocks[-1].append(line)
    return blocks


def test_every_blank_is_preceded_by_a_comment_that_says_what_to_write() -> None:
    """The file a user is told to edit must say what a legal answer is (#388).

    It shipped `effect: <REVIEW_REQUIRED>` with the nine accepted values living
    only in report.json, and `authority.mode:` with its four — so the one file
    a reader was directed to was the one that did not name the vocabulary. A
    blank with nothing above it is the defect, whatever the field.
    """

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool

    tool = Tool(id="t1", name="process_order", source_type="sdk_function", source_id="s")
    gaps = [
        rd._semantic_gap(tool, kind=kind, why="test")
        for kind in ("missing_effect_evidence", "missing_authority_evidence")
    ]
    scaffold = build_declaration_scaffold(gaps)
    assert scaffold is not None

    lines = scaffold.splitlines()
    blanks = [i for i, line in enumerate(lines) if "<REVIEW_REQUIRED>" in line]
    assert blanks, "expected the fixture to produce blanks"
    for index in blanks:
        # A list's guidance sits above the key that opens it (`scopes:`), which
        # is where a reader looks; step over those bare container lines and
        # require a comment before the blank either way.
        cursor = index - 1
        while lines[cursor].rstrip().endswith(":"):
            cursor -= 1
        assert lines[cursor].lstrip().startswith("#"), (
            f"{lines[index]!r} has no guidance above it — the reader has to "
            "leave the file to find out what it accepts"
        )


def test_the_printed_vocabulary_is_the_gaps_own_accepted_values() -> None:
    """The scaffold and report.json cannot disagree about what is legal.

    Acceptance criterion of #388: the comment is rendered FROM the gap's
    ``accepted_values``, never from a second copy of the vocabulary, so adding
    an effect or an authority mode to the engine reaches the scaffold with no
    second edit.
    """

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.cli.scan.declarations import (
        _VOCABULARY_FIELD_BY_ACTION_KIND,
    )
    from agents_shipgate.core.domain import Tool
    from agents_shipgate.schemas.report import EvidenceGapAction

    # The routing table names real action kinds; a rename must break here
    # rather than silently stop annotating.
    valid = set(get_args(EvidenceGapAction.model_fields["kind"].annotation))
    assert set(_VOCABULARY_FIELD_BY_ACTION_KIND) <= valid

    tool = Tool(id="t1", name="process_order", source_type="sdk_function", source_id="s")
    for kind, field in (
        ("missing_effect_evidence", "effect"),
        ("missing_authority_evidence", "authority.mode"),
    ):
        gap = rd._semantic_gap(tool, kind=kind, why="test")
        scaffold = build_declaration_scaffold([gap])
        assert scaffold is not None
        rendered = " ".join(
            line.lstrip("# ").strip()
            for line in scaffold.splitlines()
            if line.lstrip().startswith("#")
        )
        accepted = gap.next_action.accepted_values
        assert accepted, f"{kind} publishes no vocabulary to print"
        printed = rendered.split("accepted:", 1)[1]
        for value in accepted:
            assert value in printed, f"{field} omits {value!r} from its comment"


def test_root_block_offers_the_agent_objects_the_scan_observed() -> None:
    """Instance 1 of #388: stop asking for a value already computed.

    `object` matches the agent's DECLARED name, not the Python variable it was
    assigned to — the issue's own worked example guessed the variable — so
    naming the observed candidates is also the fix for guessing wrong.
    """

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.schemas.bindings import (
        AgentBindingGraphAssessment,
        AgentBindingIssue,
        AgentBindingNode,
    )
    from agents_shipgate.schemas.report import ReadinessReport

    graph = AgentBindingGraphAssessment(
        root_agent_id=None,
        status="unknown",
        agents=[
            AgentBindingNode(
                agent_id="a2", name="BetaAgent", source_id="adk", source_ref="agent.py"
            ),
            AgentBindingNode(
                agent_id="a1", name="AlphaAgent", source_id="adk", source_ref="agent.py"
            ),
        ],
        issues=[AgentBindingIssue(kind="ambiguous_root_agent", message="ambiguous")],
    )
    report = ReadinessReport.model_construct(binding_surface_facts=graph)
    _coverage, gaps = rd._binding_coverage(report)
    scaffold = build_declaration_scaffold(gaps, agents=graph.agents)
    assert scaffold is not None

    assert "object: AlphaAgent, source_id: adk" in scaffold
    assert "object: BetaAgent, source_id: adk" in scaffold
    # Sorted by name, not by the graph's agent-id order, which reads arbitrary.
    assert scaffold.index("AlphaAgent") < scaffold.index("BetaAgent")
    # Offered, never filled in: the value stays the human's.
    body = yaml.safe_load(scaffold)
    assert body == {
        "agent_bindings": {
            "root": {
                "source_id": "<REVIEW_REQUIRED>",
                "object": "<REVIEW_REQUIRED>",
            }
        }
    }


def test_comments_are_the_only_difference_from_a_plain_yaml_dump() -> None:
    """The annotated renderer is not a second opinion about YAML style.

    It exists to interleave comments, which PyYAML cannot carry. Everything
    else — key order, indentation, quoting — must be byte-identical to
    ``safe_dump``, so a template's rendering cannot drift from what every other
    consumer of that dict sees.
    """

    for template in _shipped_templates():
        gap = _gap("inferred_effect_only", "shipgate.yaml", template)
        scaffold = build_declaration_scaffold([gap])
        assert scaffold is not None
        expected = yaml.safe_dump(
            template, sort_keys=False, default_flow_style=False
        ).rstrip("\n")
        assert _strip_comments(scaffold) == expected.splitlines()
        # And it still parses back to exactly the template it rendered.
        assert yaml.safe_load(scaffold) == template


# --- #361: scaffold the binding layer, and from the first scan ---------------


_COLD_START_TOOLS = (
    "create_quote",
    "escalate_case",
    "lookup_account",
    "send_quote_email",
    "summarize_case",
    "update_opportunity",
)


def _cold_start_project(root: Path) -> Path:
    """An ADK agent whose every ``tools=[...]`` entry is an imported symbol.

    The shape #361 was reported against (google/adk-samples#1917): static
    extraction resolves none of them, so the first scan has no tool rows at all
    and the reader is left to author both the inventory and the binding block
    from the docs.
    """

    project = root / "cold-start"
    project.mkdir()
    listed = ",\n        ".join(_COLD_START_TOOLS)
    (project / "agent.py").write_text(
        "from google.adk.agents import LlmAgent\n\n"
        f"from .tools import (\n    {',\n    '.join(_COLD_START_TOOLS)},\n)\n\n"
        "root_agent = LlmAgent(\n"
        '    name="SmartCloserAgent",\n'
        '    instruction="Close deals.",\n'
        f"    tools=[\n        {listed},\n    ],\n)\n",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        'version: "0.1"\n'
        "project:\n  name: smart-closer\n"
        "agent:\n  name: SmartCloserAgent\n  declared_purpose: [close opportunities]\n"
        "environment:\n  target: local\n"
        "tool_sources:\n  - id: adk_agent\n    type: google_adk\n    path: agent.py\n",
        encoding="utf-8",
    )
    return project


def _scan_cold_start(project: Path, reports: Path):
    from agents_shipgate.cli.scan import run_scan

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=reports,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    return report


def test_cold_start_walk_scaffolds_both_layers_in_two_iterations(tmp_path) -> None:
    """The whole of #361, walked: 5 hand-authored iterations become 2.

    Stage 1 (bare init) and stage 2 (inventory declared) both used to emit no
    scaffold at all — the binding gap carried ``declaration_template: null`` —
    so the reader hand-wrote a 98-line inventory and an ``agent_bindings``
    block at exactly the point they had the least context.
    """

    project = _cold_start_project(tmp_path)
    reports = tmp_path / "reports"

    # --- stage 1: nothing is extracted, and the repair is named -------------
    stage1 = _scan_cold_start(project, reports)
    assert stage1.release_decision is not None
    assert stage1.release_decision.decision == "insufficient_evidence"
    assert not stage1.tool_catalog

    skeleton = reports / "suggested-inventory.json"
    scaffold_path = reports / "suggested-declarations.yaml"
    assert skeleton.is_file(), "the six names the agent lists are still retyped"
    assert scaffold_path.is_file(), "no scaffold at the point the user is stuck"

    names = [entry["name"] for entry in json.loads(skeleton.read_text())["tools"]]
    assert names == sorted(_COLD_START_TOOLS)
    wiring = yaml.safe_load(scaffold_path.read_text())
    assert wiring == {
        "google_adk": {
            "tool_inventories": [
                {"path": "<REVIEW_REQUIRED>", "source_id": "adk_agent"}
            ]
        }
    }

    # --- stage 2: the inventory is declared; the binding block is scaffolded -
    (project / "inventories").mkdir()
    (project / "inventories" / "tools.json").write_text(
        skeleton.read_text(), encoding="utf-8"
    )
    manifest = project / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text()
        + "google_adk:\n  tool_inventories:\n"
        "    - path: inventories/tools.json\n      source_id: adk_agent\n",
        encoding="utf-8",
    )

    stage2 = _scan_cold_start(project, reports)
    assert stage2.release_decision is not None
    coverage = stage2.release_decision.evidence_coverage
    assert coverage.binding_coverage.gap_count == 1
    assert len(stage2.tool_catalog) == len(_COLD_START_TOOLS)
    assert scaffold_path.is_file()
    # The repair is made, so the stage-1 inventory instruction is withdrawn
    # rather than repeated at a reader who already followed it.
    assert not skeleton.exists()

    block = yaml.safe_load(scaffold_path.read_text())
    declaration = block["agent_bindings"]["declarations"][0]
    assert declaration["agent"] == "SmartCloserAgent"
    assert [row["tool"] for row in declaration["tools"]] == sorted(_COLD_START_TOOLS)
    assert all(row["tool_id"] for row in declaration["tools"])
    # The judgement is untouched: nothing claims the set is complete, and
    # nothing invents a reason for it.
    assert declaration["complete"] == "<REVIEW_REQUIRED>"
    assert declaration["reason"] == "<REVIEW_REQUIRED>"

    # --- merging it verbatim closes the binding layer in ONE iteration ------
    reviewed = yaml.safe_load(
        scaffold_path.read_text()
        .replace("complete: <REVIEW_REQUIRED>", "complete: true")
        .replace("reason: <REVIEW_REQUIRED>", "reason: reviewed against agent.py")
    )
    manifest.write_text(
        manifest.read_text() + yaml.safe_dump(reviewed, sort_keys=False),
        encoding="utf-8",
    )

    stage3 = _scan_cold_start(project, reports)
    assert stage3.release_decision is not None
    assert stage3.release_decision.evidence_coverage.binding_coverage.gap_count == 0
    assert len(stage3.binding_surface_facts.reachable_tool_ids) == len(
        _COLD_START_TOOLS
    )

    # --- and the route it advertises actually terminates ---------------------
    # The prescribed repair used to be unreachable: the unresolved-import
    # warnings stayed on the report after the inventory answered them, and
    # `evidence_below_ie_threshold` gates on their raw count, so a repository
    # that did exactly what it was told sat at `insufficient_evidence` forever
    # with no non-warning gap left to act on (PR #401 review).
    assert stage3.release_decision.evidence_coverage.source_warning_count == 0

    manifest.write_text(
        manifest.read_text()
        + yaml.safe_dump(
            {
                "action_surface": {
                    "actions": [
                        {
                            "tool": name,
                            "effect": "read",
                            "authority": {"mode": "none"},
                        }
                        for name in sorted(_COLD_START_TOOLS)
                    ]
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    final = _scan_cold_start(project, reports)
    assert final.release_decision is not None
    coverage = final.release_decision.evidence_coverage
    assert coverage.semantic_coverage.gap_count == 0
    assert coverage.binding_coverage.gap_count == 0
    assert coverage.source_warning_count == 0
    # Whatever the verdict now turns on, it is a judgement about the declared
    # surface — never the abstention the walk started in.
    remaining = {gap.kind for gap in coverage.evidence_gaps}
    assert "source_warning" not in remaining
    assert all(
        gap.next_action.path or gap.next_action.command
        for gap in coverage.evidence_gaps
    ), "a terminal verdict must leave nothing that cannot be acted on"


def test_binding_scaffold_is_withheld_rather_than_truncated(tmp_path) -> None:
    """A closed-world list cut at N would be false where nobody can see it.

    ``complete: true`` says the listed tools are ALL the agent reaches, so a
    silently truncated ``tools:`` is the one failure mode this template must
    not have. Past the ceiling it offers nothing and the prose repair stands.
    """

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool
    from agents_shipgate.schemas.bindings import (
        AgentBindingGraphAssessment,
        AgentBindingIssue,
        AgentBindingNode,
    )

    def _template(count: int):
        catalog = [
            Tool(id=f"t{i}", name=f"tool_{i}", source_type="mcp", source_id="s")
            for i in range(count)
        ]
        graph = AgentBindingGraphAssessment(
            root_agent_id="agent:root",
            status="partial",
            agents=[AgentBindingNode(agent_id="agent:root", name="Root")],
            unbound_tool_ids=[tool.id for tool in catalog],
            issues=[
                AgentBindingIssue(
                    kind="missing_binding_evidence",
                    message="no static edge",
                    agent_id="agent:root",
                )
            ],
        )
        return rd._binding_declaration_template(graph, graph.issues[0], catalog)

    at_ceiling = _template(rd._MAX_SCAFFOLDED_BINDING_TOOLS)
    assert at_ceiling is not None
    tools = at_ceiling["agent_bindings"]["declarations"][0]["tools"]
    assert len(tools) == rd._MAX_SCAFFOLDED_BINDING_TOOLS
    assert _template(rd._MAX_SCAFFOLDED_BINDING_TOOLS + 1) is None


def test_binding_scaffold_is_root_scoped_only(tmp_path) -> None:
    """A tool reachable only by another agent is repaired by wiring a handoff.

    ``_unbound_tool_gaps`` raises the same kind per tool for capabilities the
    root cannot reach. Scaffolding the root's closed-world tool set there would
    prescribe the wrong repair — and would invite declaring a tool as the
    root's when the repository says another agent owns it.
    """

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool
    from agents_shipgate.schemas.bindings import (
        AgentBindingGraphAssessment,
        AgentBindingIssue,
        AgentBindingNode,
    )

    catalog = [Tool(id="t1", name="pay", source_type="mcp", source_id="s")]
    graph = AgentBindingGraphAssessment(
        root_agent_id="agent:root",
        status="partial",
        agents=[AgentBindingNode(agent_id="agent:root", name="Root")],
        unbound_tool_ids=["t1"],
        issues=[
            AgentBindingIssue(
                kind="missing_binding_evidence",
                message="bound to an agent the root does not reach",
                agent_id="agent:other",
                tool_id="t1",
            )
        ],
    )
    assert (
        rd._binding_declaration_template(graph, graph.issues[0], catalog) is None
    )


def test_duplicate_named_root_falls_back_to_the_root_alias(tmp_path) -> None:
    """A declaration naming a name two agents share resolves to neither."""

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool
    from agents_shipgate.schemas.bindings import (
        AgentBindingGraphAssessment,
        AgentBindingIssue,
        AgentBindingNode,
    )

    catalog = [Tool(id="t1", name="pay", source_type="mcp", source_id="s")]
    graph = AgentBindingGraphAssessment(
        root_agent_id="agent:root",
        status="partial",
        agents=[
            AgentBindingNode(agent_id="agent:root", name="Twin", source_id="a"),
            AgentBindingNode(agent_id="agent:other", name="Twin", source_id="b"),
        ],
        unbound_tool_ids=["t1"],
        issues=[
            AgentBindingIssue(
                kind="missing_binding_evidence",
                message="no static edge",
                agent_id="agent:root",
            )
        ],
    )
    template = rd._binding_declaration_template(graph, graph.issues[0], catalog)
    assert template is not None
    assert template["agent_bindings"]["declarations"][0]["agent"] == "root"


def test_a_pasted_scaffold_says_it_is_unfinished_whatever_field_it_lands_in(
    tmp_path,
) -> None:
    """``complete`` accepts only ``true``, so its type used to answer first.

    "Input should be True" does not tell a reader they pasted an unfinished
    scaffold. The placeholder is rejected before the field's own type is
    consulted, so one wording covers every field.
    """

    import pytest

    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    base = {
        "version": "0.1",
        "project": {"name": "p"},
        "agent": {"name": "a", "declared_purpose": ["do a thing"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "s1", "type": "mcp", "path": "tools.json"}],
    }
    with pytest.raises(ValueError) as caught:
        AgentsShipgateManifest.model_validate(
            {
                **base,
                "agent_bindings": {
                    "declarations": [
                        {
                            "agent": "root",
                            "complete": "<REVIEW_REQUIRED>",
                            "tools": [{"tool": "pay", "tool_id": "t1"}],
                            "handoffs": [],
                            "reason": "<REVIEW_REQUIRED>",
                        }
                    ]
                },
            }
        )
    message = str(caught.value)
    assert "unfilled scaffold placeholder" in message
    assert "agent_bindings.declarations[0].complete" in message
    assert "Input should be True" not in message


def test_one_repair_at_one_path_is_one_block() -> None:
    """Two rows prescribing the same edit are one instruction, not two.

    Every low-confidence tool of a framework source carries the same
    ``tool_inventories`` wiring, and their subjects differ (the tool names), so
    keyed on the subject alone they rendered as N identical blocks to paste —
    which reads as N separate things to do.
    """

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool

    gaps = [
        rd._semantic_gap(
            Tool(
                id=f"t{index}",
                name=name,
                source_type="google_adk",
                source_id="adk_agent",
            ),
            kind="incomplete_surface",
            why="test",
        )
        for index, name in enumerate(("create_quote", "send_email"))
    ]
    assert {gap.subject for gap in gaps} == {
        "create_quote [adk_agent]",
        "send_email [adk_agent]",
    }
    scaffold = build_declaration_scaffold(gaps)
    assert scaffold is not None
    docs = [doc for doc in yaml.safe_load_all(scaffold) if doc]
    assert docs == [
        {
            "google_adk": {
                "tool_inventories": [
                    {"path": "<REVIEW_REQUIRED>", "source_id": "adk_agent"}
                ]
            }
        }
    ]


def test_repository_controlled_text_cannot_forge_a_line_in_the_scaffold() -> None:
    """A name is data. It must not become structure a reader would paste.

    Two vectors, both fed by repository JSON. A tool name holding a newline
    renders as a *multi-line* YAML scalar whose continuation the emitter cannot
    indent correctly on its own. An agent name holding one closes the `#` of a
    candidate comment — and the next line it wrote would be a filled-in root
    selector, the self-declaration this file exists to refuse (#268).
    """

    from agents_shipgate.schemas.bindings import AgentBindingNode

    forged = "evil\nroot:\n  object: attacker\n  source_id: attacker"
    template = {
        "agent_bindings": {
            "root": {
                "source_id": "<REVIEW_REQUIRED>",
                "object": "<REVIEW_REQUIRED>",
            }
        }
    }
    scaffold = build_declaration_scaffold(
        [_gap("ambiguous_root_agent", "shipgate.yaml#agent_bindings.root", template)],
        agents=[
            AgentBindingNode(
                agent_id="a1", name=forged, source_id=forged, source_ref=forged
            )
        ],
    )
    assert scaffold is not None
    # The newlines are escaped, so the forged text stays inside the one comment
    # line that quotes it and never becomes structure of its own.
    assert "<U+000A>" in scaffold
    assert all(
        line.lstrip().startswith("#")
        for line in scaffold.splitlines()
        if "attacker" in line
    )
    assert yaml.safe_load(scaffold) == template

    # The same value as a template scalar stays one line and round-trips.
    hostile_row = {"tool": forged, "tool_id": "t1", "effect": "<REVIEW_REQUIRED>"}
    rendered = build_declaration_scaffold(
        [_gap("inferred_effect_only", "shipgate.yaml#action_surface", hostile_row)]
    )
    assert rendered is not None
    assert yaml.safe_load(rendered) == hostile_row


# --- PR #401 review: completion is per source, never per tool name -----------


def _adk_project(
    root: Path,
    name: str,
    *,
    symbols: tuple[str, ...],
    manifest_tail: str = "",
) -> Path:
    """An ADK agent whose ``tools=[...]`` entries are all imported symbols."""

    project = root / name
    project.mkdir()
    (project / "agent.py").write_text(
        "from google.adk.agents import LlmAgent\n\n"
        "from .tools import (\n"
        + "".join(f"    {symbol},\n" for symbol in symbols)
        + ")\n\n"
        "root_agent = LlmAgent(\n"
        '    name="Closer",\n'
        '    instruction="Close deals.",\n'
        "    tools=[\n"
        + "".join(f"        {symbol},\n" for symbol in symbols)
        + "    ],\n)\n",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        'version: "0.1"\n'
        f"project:\n  name: {name}\n"
        "agent:\n  name: Closer\n  declared_purpose: [close opportunities]\n"
        "environment:\n  target: local\n"
        "tool_sources:\n  - id: adk_agent\n    type: google_adk\n    path: agent.py\n"
        + manifest_tail,
        encoding="utf-8",
    )
    return project


def _inventory(project: Path, names: tuple[str, ...]) -> None:
    (project / "inventories").mkdir(exist_ok=True)
    (project / "inventories" / "tools.json").write_text(
        json.dumps(
            {"tools": [{"name": name, "description": f"reviewed {name}"} for name in names]}
        ),
        encoding="utf-8",
    )


def _open_symbols(report) -> set[str]:
    from agents_shipgate.ci.release_decision import unresolved_symbol_names

    return set(unresolved_symbol_names(report))


def test_a_split_toolset_inventory_is_not_prescribed_forever(tmp_path) -> None:
    """Completion is the reviewed `source_id`, not a name that happens to match.

    The skeleton tells the reader to split a toolset symbol into the tools it
    exposes, so following the instruction guarantees the symbol name never
    appears in the inventory. Subtracting a catalog-wide name set therefore
    re-prescribed the same inventory on every later run, forever (PR #401
    review).
    """

    project = _adk_project(
        tmp_path,
        "split",
        symbols=("search_toolset",),
        manifest_tail=(
            "google_adk:\n  tool_inventories:\n"
            "  - path: inventories/tools.json\n    source_id: adk_agent\n"
        ),
    )
    _inventory(project, ("web_search", "document_search"))
    report = _scan_cold_start(project, tmp_path / "reports")

    assert {row["name"] for row in report.tool_catalog} == {
        "web_search",
        "document_search",
    }
    # The symbol is nowhere in the inventory, and the repair is still complete.
    assert _open_symbols(report) == set()
    assert report.release_decision is not None
    assert report.release_decision.evidence_coverage.source_warning_count == 0
    assert not (tmp_path / "reports" / "suggested-inventory.json").exists()


def test_a_same_named_tool_elsewhere_does_not_complete_this_source(tmp_path) -> None:
    """Another source exposing the name is a coincidence, not a repair.

    Under a catalog-wide name subtraction, an unrelated MCP server exposing
    `search` silently cleared the ADK source's unresolved `search` — a repair
    nobody had made (PR #401 review).
    """

    project = _adk_project(
        tmp_path,
        "crosssource",
        symbols=("search",),
        manifest_tail="  - id: other_mcp\n    type: mcp\n    path: mcp-tools.json\n",
    )
    (project / "mcp-tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "search",
                        "description": "an unrelated server's search",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = _scan_cold_start(project, tmp_path / "reports")

    assert "search" in {row["name"] for row in report.tool_catalog}
    # Still owed: nothing declared anything about the ADK source.
    assert _open_symbols(report) == {"search"}
    assert report.release_decision is not None
    assert report.release_decision.evidence_coverage.source_warning_count == 1


def test_withdrawal_needs_a_declared_surface_not_just_a_bound_file(tmp_path) -> None:
    """Silencing the warnings cannot be a shortcut to a verdict.

    Withdrawal is licensed by a reviewed inventory naming the source, so the
    cheapest abuse is an *empty* one. The enumerability check is what stops it
    reaching `passed`, and this pins that the two are independent.
    """

    project = _adk_project(
        tmp_path,
        "empty",
        symbols=("create_quote", "send_quote_email"),
        manifest_tail=(
            "google_adk:\n  tool_inventories:\n"
            "  - path: inventories/tools.json\n    source_id: adk_agent\n"
        ),
    )
    _inventory(project, ())
    report = _scan_cold_start(project, tmp_path / "reports")

    assert report.release_decision is not None
    assert report.release_decision.decision != "passed"
    assert "SHIP-INVENTORY-NOT-ENUMERABLE" in {
        finding.check_id for finding in report.findings
    }


def test_withdrawal_is_scoped_to_the_completed_source() -> None:
    """Two ADK sources, one declared: only that one's warnings are withdrawn."""

    from agents_shipgate.core.source_warnings import (
        adk_unresolved_tool_warning,
        withdraw_completed_adk_tool_warnings,
    )

    declared = adk_unresolved_tool_warning("Alpha", "pay")
    undeclared = adk_unresolved_tool_warning("Beta", "pay")
    unrelated = "some other loader said something"
    kept = withdraw_completed_adk_tool_warnings(
        [declared, undeclared, unrelated],
        agent_source_ids={"Alpha": "adk_a", "Beta": "adk_b"},
        completed_source_ids={"adk_a"},
    )
    assert kept == [undeclared, unrelated]

    # An agent name two sources both publish is dropped from the map rather
    # than guessed, so its warnings are never withdrawn against the wrong one.
    assert withdraw_completed_adk_tool_warnings(
        [declared],
        agent_source_ids={},
        completed_source_ids={"adk_a"},
    ) == [declared]


# --- PR #401 review: the remaining robustness findings -----------------------


def test_complete_asks_the_reviewer_to_check_handoffs_too() -> None:
    """`complete: true` closes the world over BOTH lists, so say both.

    The scaffold pre-fills observed handoffs and the hint spoke only about
    tools, so a reviewer could ratify the tool set while silently asserting a
    downstream agent surface they never looked at (PR #401 review).
    """

    from agents_shipgate.cli.scan.declarations import _FIELD_HINTS

    complete = _FIELD_HINTS["declarations.complete"]
    reason = _FIELD_HINTS["declarations.reason"]
    for hint in (complete, reason):
        assert "handoff" in hint.lower()
    assert "tool" in complete.lower()


def test_ambiguous_handoff_target_withholds_the_whole_template() -> None:
    """A handoff has no source qualifier, so an ambiguous name resolves nowhere.

    Emitting `handoffs: [Twin]` when two agents are named `Twin` produced a
    block that reports an unresolved binding instead of closing the gap it was
    offered for. Withheld entirely: dropping just the handoff would understate
    a closed world the reviewer is about to assert (PR #401 review).
    """

    import agents_shipgate.ci.release_decision as rd
    from agents_shipgate.core.domain import Tool
    from agents_shipgate.schemas.bindings import (
        AgentBindingGraphAssessment,
        AgentBindingIssue,
        AgentBindingNode,
        AgentHandoffBindingEdge,
    )

    def _graph(second_twin_name: str) -> AgentBindingGraphAssessment:
        return AgentBindingGraphAssessment(
            root_agent_id="agent:root",
            status="partial",
            agents=[
                AgentBindingNode(agent_id="agent:root", name="Root", source_id="a"),
                AgentBindingNode(agent_id="agent:twin", name="Twin", source_id="a"),
                AgentBindingNode(
                    agent_id="agent:other", name=second_twin_name, source_id="b"
                ),
            ],
            handoff_edges=[
                AgentHandoffBindingEdge(
                    source_agent_id="agent:root",
                    target_agent_id="agent:twin",
                    edge_type="subagent",
                    confidence="high",
                    provenance_kind="static_declaration",
                    source="agent.py",
                )
            ],
            unbound_tool_ids=["t1"],
            issues=[
                AgentBindingIssue(
                    kind="missing_binding_evidence",
                    message="no static edge",
                    agent_id="agent:root",
                )
            ],
        )

    catalog = [Tool(id="t1", name="pay", source_type="mcp", source_id="s")]

    ambiguous = _graph("Twin")
    assert (
        rd._binding_declaration_template(ambiguous, ambiguous.issues[0], catalog)
        is None
    )

    # The same graph with a distinguishable second agent still scaffolds.
    fine = _graph("Other")
    template = rd._binding_declaration_template(fine, fine.issues[0], catalog)
    assert template is not None
    assert template["agent_bindings"]["declarations"][0]["handoffs"] == ["Twin"]


def test_a_recursive_yaml_alias_still_gets_a_structured_error() -> None:
    """The before-validator walks RAW input, which can be cyclic.

    ``yaml.safe_load`` preserves recursive aliases, so `bogus: &loop {x: *loop}`
    is a syntactically valid manifest carrying a self-referential dict. An
    unguarded walk raised `RecursionError`, replacing the structured config
    error — and the agent-mode recovery payload with it — with a stack
    overflow (PR #401 review).
    """

    import pytest

    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    cyclic = yaml.safe_load("bogus: &loop {x: *loop}\n")
    assert cyclic["bogus"]["x"] is cyclic["bogus"]
    with pytest.raises(ValueError) as caught:
        AgentsShipgateManifest.model_validate(cyclic)
    assert not isinstance(caught.value, RecursionError)

    # Cycle-safety must not blind the check: a placeholder inside the cycle is
    # still found and still named.
    loop: dict = {"name": "a", "declared_purpose": ["<REVIEW_REQUIRED>"]}
    loop["self"] = loop
    with pytest.raises(ValueError) as found:
        AgentsShipgateManifest.model_validate(
            {
                "version": "0.1",
                "project": {"name": "p"},
                "environment": {"target": "local"},
                "agent": loop,
            }
        )
    assert "unfilled scaffold placeholder" in str(found.value)


def test_a_noncharacter_in_a_name_cannot_break_the_generated_scaffold() -> None:
    """PyYAML rejects U+FFFE outright, so a raw one made the file unloadable.

    `display_literal` escaped control and invisible code points but passed
    noncharacters through, and the scaffold quotes repository-controlled agent
    names in comments — so one such name meant `yaml.safe_load_all` could not
    read the document at all (PR #401 review).
    """

    from agents_shipgate.core.evidence_actions import (
        display_literal,
        undisplay_literal,
    )
    from agents_shipgate.schemas.bindings import AgentBindingNode

    hostile = "bad￾￿﷐"
    rendered = display_literal(hostile)
    assert "￾" not in rendered
    # Escaping stays injective, so the name is still recoverable.
    assert undisplay_literal(rendered) == hostile

    template = {
        "agent_bindings": {
            "root": {
                "source_id": "<REVIEW_REQUIRED>",
                "object": "<REVIEW_REQUIRED>",
            }
        }
    }
    scaffold = build_declaration_scaffold(
        [_gap("ambiguous_root_agent", "shipgate.yaml#agent_bindings.root", template)],
        agents=[AgentBindingNode(agent_id="a1", name=hostile, source_id=hostile)],
    )
    assert scaffold is not None
    assert yaml.safe_load(scaffold) == template
