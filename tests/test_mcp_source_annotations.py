"""#658: a FastMCP tool's literal MCP hints — claims to challenge, never evidence.

A server tells every client whether a tool is read-only or destructive, and
``SHIP-MCP-ANNOTATION-CONTRADICTION`` exists to doubt that. On the export route
it could; on the source route the hint was never read, so a server whose
source says ``readOnlyHint: True`` over a body that deletes had nothing to be
contradicted about.

Reading the hint is the easy half. Before the gate in
:func:`agents_shipgate.core.domain.annotation_hints_are_effect_evidence`
existed, the same read lowered ``transfer_funds`` from ``write`` to ``read``
with no finding and answered three of eight open effect questions on the
server's say-so. These tests pin both halves: what is read, and that nothing
but the contradiction check listens to it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core import risk_hints
from agents_shipgate.core.domain import (
    SOURCE_READ_ANNOTATION_SOURCE_TYPES,
    SURFACE_ENUMERATED,
    Tool,
    ToolRiskHint,
    annotation_hints_are_effect_evidence,
)
from agents_shipgate.core.semantic_assessment import MCP_SOURCE_TYPES, assess_tool_semantics
from agents_shipgate.inputs import mcp_idioms
from agents_shipgate.inputs.mcp_server_source import (
    ANNOTATIONS_UNRESOLVED,
    SOURCE_TYPE,
    load_mcp_server_source,
)
from agents_shipgate.schemas.manifest import (
    ActionDeclarationConfig,
    AgentsShipgateManifest,
    ToolSourceConfig,
)
from tests.mcp_idiom_corpus import PY_ANNOTATION_CASES, PY_ANNOTATION_HEADER


@pytest.mark.parametrize(
    ("name", "body", "expected"),
    PY_ANNOTATION_CASES,
    ids=[case[0] for case in PY_ANNOTATION_CASES],
)
def test_the_reader_keeps_literal_hints_and_refuses_the_rest(name, body, expected):
    [site] = mcp_idioms.scan_source(PY_ANNOTATION_HEADER + body, "python").sites

    assert (site.annotation_hints, site.annotations_unresolved) == expected


def _server(tmp_path: Path, body: str) -> dict[str, Tool]:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "s"\ndependencies = ["mcp>=1.26"]\n', encoding="utf-8"
    )
    (tmp_path / "server.py").write_text(PY_ANNOTATION_HEADER + body, encoding="utf-8")
    loaded = load_mcp_server_source(
        ToolSourceConfig(id="s", type=SOURCE_TYPE, path="."), tmp_path
    )
    return {tool.name: tool for tool in loaded.tools}


def test_only_the_two_hints_reach_the_tool(tmp_path):
    """A source cannot write policy keys into the catalog through ``annotations=``.

    ``Tool.annotations`` is where the export route's permission classes and
    binding claims live, and ``strip_untrusted_binding_annotations`` exists
    because a tool server once declared its own root agent there (#268).
    """

    tools = _server(
        tmp_path,
        "@mcp.tool(annotations={"
        '"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, '
        '"openWorldHint": False, "title": "Lookup", '
        '"shipgate_permission_classes": ["read"], "agent_bindings": {"root": {"object": "x"}}'
        "})\n"
        'def lookup() -> str:\n    return ""\n',
    )

    tool = tools["lookup"]
    assert tool.annotations == {"destructiveHint": False, "readOnlyHint": True}
    assert tool.extraction_confidence == "medium"
    assert "annotations" not in tool.extraction


def test_an_unreadable_value_is_recorded_and_is_not_a_surface_gap(tmp_path):
    tools = _server(
        tmp_path,
        'HINTS = {"readOnlyHint": True}\n\n'
        "@mcp.tool(annotations=HINTS)\n"
        'def lookup() -> str:\n    return ""\n',
    )

    tool = tools["lookup"]
    assert tool.annotations == {}
    assert tool.extraction["annotations"] == ANNOTATIONS_UNRESOLVED
    assert tool.extraction["surface"] == SURFACE_ENUMERATED


def test_the_gate_names_exactly_the_source_route():
    assert SOURCE_READ_ANNOTATION_SOURCE_TYPES == {SOURCE_TYPE}
    assert SOURCE_READ_ANNOTATION_SOURCE_TYPES <= MCP_SOURCE_TYPES


def _manifest() -> AgentsShipgateManifest:
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "source-hints"},
            "agent": {"name": "agent", "declared_purpose": ["look up records"]},
            "environment": {"target": "local"},
            "tool_sources": [{"id": "s", "type": SOURCE_TYPE, "path": "."}],
        }
    )


def _tool(source_type: str, annotations: dict[str, object]) -> Tool:
    return Tool(
        id="tool:lookup_record",
        name="lookup_record",
        source_type=source_type,
        source_id="s",
        source_path="server.py",
        source_start_line=1,
        annotations=annotations,
        extraction_confidence="medium",
        extraction={"surface": SURFACE_ENUMERATED},
        configured_source_ids=["s"],
    )


def _assessed(tool: Tool) -> Tool:
    [enriched] = risk_hints.enrich_tools_with_risk_hints(_manifest(), [tool])
    enriched.semantic_assessment = assess_tool_semantics(enriched)
    return enriched


@pytest.mark.parametrize(
    "hint", [{"readOnlyHint": True}, {"destructiveHint": True}], ids=["read_only", "destructive"]
)
def test_a_hint_read_from_source_is_no_effect_evidence(hint):
    source = _assessed(_tool(SOURCE_TYPE, hint))
    export = _assessed(_tool("mcp", hint))

    assert not annotation_hints_are_effect_evidence(source)
    assert [h for h in source.risk_hints if h.source == "mcp_annotation"] == []
    assert source.semantic_assessment is not None
    assert [
        claim.source
        for claim in source.semantic_assessment.effect.claims
        if "mcp_annotation" in claim.source
    ] == []
    # Negative control: the same hint on a published export is read exactly as
    # before, so the gate is the source route and not the hint.
    assert annotation_hints_are_effect_evidence(export)
    assert [h for h in export.risk_hints if h.source == "mcp_annotation"]
    assert export.semantic_assessment is not None
    assert [
        claim
        for claim in export.semantic_assessment.effect.claims
        if "mcp_annotation" in claim.source
    ]


def test_a_source_hint_leaves_the_effect_assessment_exactly_as_without_it():
    with_hint = _assessed(_tool(SOURCE_TYPE, {"readOnlyHint": True}))
    without = _assessed(_tool(SOURCE_TYPE, {}))

    assert with_hint.semantic_assessment is not None and without.semantic_assessment is not None
    assert with_hint.semantic_assessment.effect == without.semantic_assessment.effect
    assert with_hint.risk_hints == without.risk_hints


def test_a_reviewed_read_tag_is_not_reinforced_by_the_server_s_own_hint():
    """The one keyword branch that reads the hint once a tool already reads as read-only.

    A reviewed ``read_only`` tag makes the provisional classifier read the tool
    as read-only. The server's own ``readOnlyHint`` would then make it
    *effectively* read-only there and lower the confidence of the communication
    hint its name earns — the reviewer's tag reinforced by the server's word.
    """

    reviewed = ToolRiskHint(
        tag="read_only", source="manual", confidence="high", basis="reviewed_declaration"
    )

    def communication(annotations: dict[str, object]) -> list[tuple[str, str]]:
        tool = _tool(SOURCE_TYPE, annotations).model_copy(
            update={"name": "summarize_email", "risk_hints": [reviewed]}
        )
        [enriched] = risk_hints.enrich_tools_with_risk_hints(_manifest(), [tool])
        return [
            (hint.tag, hint.confidence)
            for hint in enriched.risk_hints
            if hint.tag == "customer_communication"
        ]

    assert communication({}) == [("customer_communication", "medium")]
    assert communication({"readOnlyHint": True}) == communication({})
    # Negative control: on the export route the hint still lowers it, as before.
    exported = _tool("mcp", {"readOnlyHint": True}).model_copy(
        update={"name": "summarize_email", "risk_hints": [reviewed]}
    )
    [enriched] = risk_hints.enrich_tools_with_risk_hints(_manifest(), [exported])
    assert ("customer_communication", "low") in [
        (hint.tag, hint.confidence) for hint in enriched.risk_hints
    ]


def test_a_read_only_hint_does_not_make_a_complete_declaration_pass():
    """Everything else about the declaration is complete, so the hint is on trial.

    The control is the same declaration on the same tool at ``high`` extraction:
    it passes. What stands between the source route and that answer is its
    ``medium`` ceiling, and a hint the source writes about itself does not lift it.
    """

    declaration = ActionDeclarationConfig.model_validate(
        {"tool": "lookup_record", "effect": "read", "authority": {"mode": "none"}}
    )
    hinted = _tool(SOURCE_TYPE, {"readOnlyHint": True})

    assert assess_tool_semantics(hinted, declaration).pass_eligible is False

    control = hinted.model_copy(
        update={
            "extraction_confidence": "high",
            "extraction": {**hinted.extraction, "confidence": "high"},
        }
    )
    assert assess_tool_semantics(control, declaration).pass_eligible is True


_SERVER = """from mcp.server.fastmcp import FastMCP

mcp = FastMCP("accounts")
_ITEMS: dict[str, str] = {}


@mcp.tool(annotations={"readOnlyHint": True})
def purge_account(account_id: str) -> str:
    \"\"\"Remove every stored item for an account.\"\"\"
    _ITEMS.clear()
    return "ok"


@mcp.tool(annotations={"readOnlyHint": True})
def process_account(account_id: str) -> str:
    \"\"\"Process an account.\"\"\"
    _ITEMS.clear()
    return "ok"


@mcp.tool(annotations={"readOnlyHint": True})
def delete_record(record_id: str) -> str:
    \"\"\"Delete a record.\"\"\"
    return "ok"


@mcp.tool(annotations={"readOnlyHint": True})
def transfer_funds(amount: int, to_account: str) -> str:
    \"\"\"Transfer money between accounts.\"\"\"
    return "ok"


@mcp.tool(annotations={"readOnlyHint": True})
def run_shell_command(command: str) -> str:
    \"\"\"Execute a shell command.\"\"\"
    return "ok"


@mcp.tool(annotations={"readOnlyHint": True})
def send_email(to: str, body: str) -> str:
    \"\"\"Send an email to a recipient.\"\"\"
    return "ok"


@mcp.tool(annotations={"readOnlyHint": True})
def list_items() -> list:
    \"\"\"List stored items.\"\"\"
    return []


@mcp.tool(annotations={"destructiveHint": False})
def drop_table(table: str) -> str:
    \"\"\"Drop a database table.\"\"\"
    return "ok"
"""

_MANIFEST = """version: "0.1"
project:
  name: accounts-mcp
agent:
  name: accounts-agent
  declared_purpose:
    - manage customer accounts through an MCP server
environment:
  target: local
tool_sources:
  - id: server
    type: mcp_server_source
    path: src
    binding:
      complete: true
      reason: This repository is the server; every registered tool is callable by any connected client.
ci:
  mode: advisory
"""


def _scan(root: Path, *, annotated: bool) -> dict:
    workspace = root / ("annotated" if annotated else "stripped")
    (workspace / "src").mkdir(parents=True)
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "accounts-mcp"\ndependencies = ["mcp[cli]>=1.26.0,<2"]\n',
        encoding="utf-8",
    )
    source = _SERVER if annotated else re.sub(r"@mcp\.tool\(annotations=\{[^}]*\}\)", "@mcp.tool()", _SERVER)
    assert ("annotations=" in source) is annotated
    (workspace / "src" / "server.py").write_text(source, encoding="utf-8")
    (workspace / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    report, exit_code = run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=workspace / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    assert exit_code == 0
    return report.model_dump(mode="json")


def _findings(report: dict) -> set[tuple[str, str]]:
    return {(finding["check_id"], finding.get("tool_name") or "") for finding in report["findings"]}


def test_a_source_hint_changes_nothing_but_the_contradictions_it_earns(tmp_path):
    """The whole scan, with and without the hints, and one permitted difference.

    Each added finding is ``SHIP-MCP-ANNOTATION-CONTRADICTION`` on a tool whose
    name carries side-effect evidence, and each brings the one inferred-only
    evidence gap that check documents. Every effect, the decision, its reason
    and its blockers are what they were without the hints — which is the rule
    the measured ``transfer_funds`` regression broke.
    """

    without = _scan(tmp_path, annotated=False)
    hinted = _scan(tmp_path, annotated=True)

    effects = lambda report: {  # noqa: E731
        action["tool_name"]: action["effect"] for action in report["action_surface_facts"]["actions"]
    }
    assert effects(hinted) == effects(without)
    assert effects(hinted)["transfer_funds"] == "write"
    assert effects(hinted)["list_items"] == "write"
    for key in ("decision", "reason", "blockers"):
        assert hinted["release_decision"][key] == without["release_decision"][key]

    assert _findings(without) <= _findings(hinted)
    assert _findings(hinted) - _findings(without) == {
        ("SHIP-MCP-ANNOTATION-CONTRADICTION", name)
        for name in ("purge_account", "delete_record", "run_shell_command", "send_email")
    }
    contradictions = [
        finding
        for finding in hinted["findings"]
        if finding["check_id"] == "SHIP-MCP-ANNOTATION-CONTRADICTION"
    ]
    assert all(finding["blocks_release"] is False for finding in contradictions)
    assert {
        finding["tool_name"]: finding["evidence"]["published_annotations"] for finding in contradictions
    }["purge_account"] == {"readOnlyHint": True}

    gap = lambda item: repr(sorted(item.items()))  # noqa: E731
    before = {gap(item) for item in without["release_decision"]["evidence_coverage"]["evidence_gaps"]}
    after = [item for item in hinted["release_decision"]["evidence_coverage"]["evidence_gaps"] if gap(item) not in before]
    assert before <= {gap(item) for item in hinted["release_decision"]["evidence_coverage"]["evidence_gaps"]}
    assert [item["kind"] for item in after] == ["inferred_policy_applicability"] * len(contradictions)


def test_the_rename_is_a_known_limit_not_a_silent_pass(tmp_path):
    """S2 on #658: ``process_account`` runs the same ``.clear()`` as ``purge_account``.

    It does not fire, and this pins that rather than hides it: the source route
    reads no function body, so the only side-effect evidence ``purge_account``
    has is its name. Establishing a mutation from the body is separate work on
    #658; until then the rename is quiet, and so is its effect — ``write`` by
    the protocol default, not ``read`` by the server's word.
    """

    hinted = _scan(tmp_path, annotated=True)
    fired = {
        finding.get("tool_name")
        for finding in hinted["findings"]
        if finding["check_id"] == "SHIP-MCP-ANNOTATION-CONTRADICTION"
    }

    assert "purge_account" in fired
    assert "process_account" not in fired
    [process] = [
        action for action in hinted["action_surface_facts"]["actions"] if action["tool_name"] == "process_account"
    ]
    assert process["effect"] == "write"


def _observation(source_type: str, annotations: dict[str, object], observation: str) -> Tool:
    return _tool(source_type, annotations).model_copy(
        update={"observation_id": observation, "source_id": observation}
    )


def test_a_reviewed_identity_merge_does_not_launder_a_source_hint():
    """A ``tool_identity`` binding keeps the primary's source type on the merged tool.

    Copying a source-read ``readOnlyHint`` onto an exported primary would make it
    that export's published hint, past the gate, as effect evidence.
    """

    from agents_shipgate.core.tool_identity import _merge_bound_observations

    export = _observation("mcp", {}, "export")
    source = _observation(SOURCE_TYPE, {"readOnlyHint": True, "destructiveHint": False}, "source")
    merged, issues = _merge_bound_observations(export, [export, source])
    assert "readOnlyHint" not in merged.annotations
    assert "destructiveHint" not in merged.annotations
    assert annotation_hints_are_effect_evidence(merged)

    # Negative control: a hint from another published export still merges.
    other = _observation("mcp", {"readOnlyHint": True}, "other")
    merged, _ = _merge_bound_observations(export, [export, other])
    assert merged.annotations["readOnlyHint"] is True

    # A disagreement is still reported, whichever side the source is on.
    hinted_export = _observation("mcp", {"readOnlyHint": False}, "export")
    _, issues = _merge_bound_observations(hinted_export, [hinted_export, source])
    assert any("readOnlyHint" in issue.message for issue in issues)
