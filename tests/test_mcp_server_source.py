"""The ``mcp_server_source`` input, its discovery route, and its trigger (#431).

Three first-party vendor MCP servers were measured before this input existed
and all three returned ``is_agent_project: false``: the MongoDB server (49
tools at the time, including ``drop-database``), ``mcp-grafana`` (Go), and —
had its committed snapshots not been unreachable from ``detect``'s filename
globs — ``github-mcp-server``. The fixtures below reproduce each server's
registration shape rather than its content, so the tests state the property
("this shape is readable") instead of pinning a vendor's tool list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.cli.discovery.mcp_source import discover_mcp_server_source
from agents_shipgate.cli.discovery.signals import detect_workspace
from agents_shipgate.core.domain import SURFACE_ENUMERATED, SURFACE_PARTIAL
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.semantic_assessment import (
    AST_ONLY_SOURCE_TYPES,
    MCP_SOURCE_TYPES,
)
from agents_shipgate.inputs.mcp_idioms import DIFF_TOKENS
from agents_shipgate.inputs.mcp_server_source import (
    MAX_SCANNED_FILES,
    SOURCE_TYPE,
    MCPServerSourceAdapter,
    load_mcp_server_source,
)
from agents_shipgate.inputs.protocol import REGISTRY
from agents_shipgate.schemas.manifest import BUILTIN_TOOL_SOURCE_TYPES, ToolSourceConfig

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- Fixtures modelled on the three measured vendor servers -----------------


def _mongodb_shaped(root: Path) -> Path:
    """A TypeScript server declaring each tool as a class with static fields."""

    workspace = root / "mongo"
    tools = workspace / "packages" / "tools-mongodb" / "src" / "tools"
    (tools / "delete").mkdir(parents=True)
    (tools / "read").mkdir(parents=True)
    (tools / "delete" / "dropDatabase.ts").write_text(
        'import { MongoDBToolBase } from "../../mongodbTool.js";\n'
        "\n"
        "export class DropDatabaseTool extends MongoDBToolBase {\n"
        '    static toolName = "drop-database";\n'
        '    public description = "Removes the specified database";\n'
        '    static operationType: OperationType = "delete";\n'
        "}\n",
        encoding="utf-8",
    )
    (tools / "delete" / "deleteMany.ts").write_text(
        "export class DeleteManyTool extends MongoDBToolBase {\n"
        '    static toolName = "delete-many";\n'
        '    static operationType: OperationType = "delete";\n'
        "}\n",
        encoding="utf-8",
    )
    (tools / "read" / "aggregate.ts").write_text(
        "export class AggregateTool extends MongoDBToolBase {\n"
        '    static toolName = "aggregate";\n'
        '    static operationType: OperationType = "read";\n'
        "}\n",
        encoding="utf-8",
    )
    (workspace / "package.json").write_text(
        json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1.0.0"}}),
        encoding="utf-8",
    )
    return workspace


def _grafana_shaped(root: Path) -> Path:
    """A Go server registering through a ``MustTool`` helper."""

    workspace = root / "grafana"
    (workspace / "tools").mkdir(parents=True)
    (workspace / "tools" / "incident.go").write_text(
        "package tools\n"
        "\n"
        "var UpdateIncident = mcpgrafana.MustTool(\n"
        '\t"update_incident",\n'
        '\t"Update an existing incident",\n'
        "\tupdateIncident,\n"
        ")\n",
        encoding="utf-8",
    )
    (workspace / "go.mod").write_text(
        "module github.com/grafana/mcp-grafana\n"
        "\n"
        "require github.com/mark3labs/mcp-go v0.58.0\n",
        encoding="utf-8",
    )
    return workspace


def _source(path: str, source_id: str = "server") -> ToolSourceConfig:
    return ToolSourceConfig(id=source_id, type=SOURCE_TYPE, path=path)


# --- Registration -----------------------------------------------------------


def test_the_input_is_registered_and_configurable():
    assert SOURCE_TYPE in BUILTIN_TOOL_SOURCE_TYPES
    assert REGISTRY.get(SOURCE_TYPE) is not None
    assert REGISTRY.get(SOURCE_TYPE).scope == "per_source"


def test_the_source_type_is_in_both_engine_vocabularies():
    """It reads source, and what it reads is an MCP server.

    ``AST_ONLY_SOURCE_TYPES`` is what makes completeness something this input
    has to *establish* rather than assume; ``MCP_SOURCE_TYPES`` is what gives a
    tool with no effect evidence the MCP protocol default instead of silence.
    Both vocabularies had a second copy of themselves at some point, which is
    why the check names them together.
    """

    assert SOURCE_TYPE in AST_ONLY_SOURCE_TYPES
    assert SOURCE_TYPE in MCP_SOURCE_TYPES

    from agents_shipgate.checks.mcp_permissions import (
        MCP_SOURCE_TYPES as CHECK_MCP_SOURCE_TYPES,
    )

    assert CHECK_MCP_SOURCE_TYPES is MCP_SOURCE_TYPES


def test_the_published_boundary_row_names_the_route():
    coverage = MCPServerSourceAdapter.coverage
    literal = next(
        cell for cell in coverage.cells if cell.shape == "literal_registration"
    )
    assert literal.status == "extracted"
    assert literal.emits == (SOURCE_TYPE,)
    assert literal.ceiling == "medium"
    assert literal.surface == SURFACE_ENUMERATED
    dynamic = next(
        cell for cell in coverage.cells if cell.shape == "dynamic_construction"
    )
    assert dynamic.status == "not_extracted"


# --- Reading a server -------------------------------------------------------


def test_a_typescript_server_yields_its_tools_at_medium_confidence(tmp_path):
    workspace = _mongodb_shaped(tmp_path)
    loaded = load_mcp_server_source(_source("packages"), workspace)

    by_name = {tool.name: tool for tool in loaded.tools}
    assert set(by_name) == {"drop-database", "delete-many", "aggregate"}
    drop = by_name["drop-database"]
    assert drop.source_type == SOURCE_TYPE
    assert drop.extraction_confidence == "medium"
    assert drop.extraction["surface"] == SURFACE_ENUMERATED
    assert drop.extraction["idiom"] == "ts_static_tool_name"
    assert drop.description == "Removes the specified database"
    # The evidence is the file that registers the tool, not the configured
    # directory: a finding pointing at `packages` sends a reviewer looking
    # through the whole tree.
    assert drop.source_path == "packages/tools-mongodb/src/tools/delete/dropDatabase.ts"
    assert drop.source_start_line == 4
    assert loaded.omissions == []


def test_a_declared_operation_class_challenges_but_never_proves(tmp_path):
    """``static operationType = "delete"`` is the vendor's own classification.

    It arrives as a low-confidence ``inferred_keyword`` hint, which can
    contradict a reviewer who declares ``drop-database`` read-only and can
    never make an action pass-eligible on its own. ``read`` is deliberately
    unmapped: a source asserting its own harmlessness is the one claim it has
    an incentive to make.
    """

    workspace = _mongodb_shaped(tmp_path)
    by_name = {
        tool.name: tool
        for tool in load_mcp_server_source(_source("packages"), workspace).tools
    }

    hint = by_name["drop-database"].risk_hints[0]
    assert hint.tag == "destructive"
    assert hint.source == "mcp_operation_type"
    assert hint.confidence == "low"
    assert hint.basis == "inferred_keyword"
    assert hint.provenance_kind == "keyword_heuristic"
    assert by_name["aggregate"].risk_hints == []


def test_a_go_server_yields_its_tools(tmp_path):
    workspace = _grafana_shaped(tmp_path)
    loaded = load_mcp_server_source(_source("tools"), workspace)

    assert [tool.name for tool in loaded.tools] == ["update_incident"]
    assert loaded.tools[0].extraction["idiom"] == "go_must_tool"


def test_a_single_file_path_is_accepted(tmp_path):
    workspace = _grafana_shaped(tmp_path)
    loaded = load_mcp_server_source(_source("tools/incident.go"), workspace)
    assert [tool.name for tool in loaded.tools] == ["update_incident"]


# --- What it refuses to guess ----------------------------------------------


def test_a_runtime_name_is_recorded_as_unenumerated_not_dropped(tmp_path):
    workspace = _mongodb_shaped(tmp_path)
    (workspace / "packages" / "tools-mongodb" / "src" / "tools" / "read" / "export.ts").write_text(
        "export class ExportTool extends MongoDBToolBase {\n"
        "    static toolName = EXPORT_TOOL_NAME;\n"
        "}\n",
        encoding="utf-8",
    )
    loaded = load_mcp_server_source(_source("packages"), workspace)

    assert [omission.reason for omission in loaded.omissions] == ["name_not_literal"]
    omission = loaded.omissions[0]
    assert omission.subject == (
        "packages/tools-mongodb/src/tools/read/export.ts:2"
    )
    # The warning text is the join key the exclusion ledger uses, so it has to
    # be the same string the omission carries.
    assert omission.warning in loaded.warnings
    assert "drop-database" in {tool.name for tool in loaded.tools}


def test_completeness_is_per_file_not_per_source(tmp_path):
    """#393's rule: one unresolved construct holds the file, not the tree.

    Holding the whole source would make ``insufficient_evidence`` the answer
    for any server with a single dynamically named tool, which is the constant
    verdict #393 exists to stop being.
    """

    workspace = _mongodb_shaped(tmp_path)
    tools = workspace / "packages" / "tools-mongodb" / "src" / "tools"
    (tools / "read" / "mixed.ts").write_text(
        'class A { static toolName = "find"; }\n'
        "class B { static toolName = DYNAMIC; }\n",
        encoding="utf-8",
    )
    by_name = {
        tool.name: tool
        for tool in load_mcp_server_source(_source("packages"), workspace).tools
    }

    assert by_name["find"].extraction["surface"] == SURFACE_PARTIAL
    assert by_name["find"].extraction["surface_gaps"] == ["name_not_literal"]
    assert by_name["drop-database"].extraction["surface"] == SURFACE_ENUMERATED


def test_a_duplicate_registration_is_one_tool_and_says_so(tmp_path):
    """Grafana registers ``alerting_manage_silences`` twice — one build is
    mounted. Two catalog rows sharing an id would be one action counted twice.
    """

    workspace = _grafana_shaped(tmp_path)
    (workspace / "tools" / "silences.go").write_text(
        "package tools\n"
        'var Read = mcpgrafana.MustTool("alerting_manage_silences", a, b)\n'
        'var Write = mcpgrafana.MustTool("alerting_manage_silences", c, d)\n',
        encoding="utf-8",
    )
    loaded = load_mcp_server_source(_source("tools"), workspace)

    names = [tool.name for tool in loaded.tools]
    assert names.count("alerting_manage_silences") == 1
    assert any("registered more than once" in warning for warning in loaded.warnings)


def test_test_files_do_not_contribute_tools(tmp_path):
    workspace = _grafana_shaped(tmp_path)
    (workspace / "tools" / "incident_test.go").write_text(
        "package tools\n" 'var T = mcpgrafana.MustTool("demo_tool", a, b)\n',
        encoding="utf-8",
    )
    loaded = load_mcp_server_source(_source("tools"), workspace)
    assert "demo_tool" not in {tool.name for tool in loaded.tools}


def test_a_path_with_no_readable_source_is_a_parse_error(tmp_path):
    workspace = tmp_path / "empty"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "README.md").write_text("# hi\n", encoding="utf-8")
    with pytest.raises(InputParseError, match="no TypeScript or Go files"):
        load_mcp_server_source(_source("docs"), workspace)


def test_a_single_file_in_another_language_is_a_parse_error(tmp_path):
    workspace = tmp_path / "py"
    workspace.mkdir()
    (workspace / "server.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(InputParseError, match="not a TypeScript or Go file"):
        load_mcp_server_source(_source("server.py"), workspace)


def test_a_missing_path_is_a_parse_error(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(InputParseError, match="Input path not found"):
        load_mcp_server_source(_source("nope"), workspace)


def test_the_file_cap_is_reported_and_holds_every_surface_partial(
    tmp_path, monkeypatch
):
    """A cap that truncated silently would be the fail-open this input closes."""

    monkeypatch.setattr(
        "agents_shipgate.inputs.mcp_server_source.MAX_SCANNED_FILES", 2
    )
    workspace = _mongodb_shaped(tmp_path)
    loaded = load_mcp_server_source(_source("packages"), workspace)

    assert any(omission.reason == "walk_capped" for omission in loaded.omissions)
    assert loaded.tools
    for tool in loaded.tools:
        assert tool.extraction["surface"] == SURFACE_PARTIAL
        assert "walk_capped" in tool.extraction["surface_gaps"]


def test_a_file_too_large_to_read_holds_the_whole_source_partial(
    tmp_path, monkeypatch
):
    """An unread file is a hole in the enumeration, not a smaller enumeration.

    Unlike one unresolved registration — which holds its own file — a file this
    reader never opened could register anything, so the question it leaves open
    is which tools exist at all.
    """

    monkeypatch.setattr(
        "agents_shipgate.inputs.mcp_server_source.MAX_SOURCE_FILE_BYTES", 1000
    )
    workspace = _mongodb_shaped(tmp_path)
    bundle = workspace / "packages" / "tools-mongodb" / "src" / "bundle.js"
    bundle.write_text("// tool\n" + ("x" * 2000), encoding="utf-8")

    loaded = load_mcp_server_source(_source("packages"), workspace)

    assert [
        omission.subject
        for omission in loaded.omissions
        if omission.reason == "file_too_large"
    ] == ["packages/tools-mongodb/src/bundle.js"]
    assert loaded.tools
    for tool in loaded.tools:
        assert tool.extraction["surface"] == SURFACE_PARTIAL
        assert "file_too_large" in tool.extraction["surface_gaps"]


def test_the_source_size_bound_sits_below_the_loader_s(tmp_path):
    """Above the loader's limit the omission would name the wrong problem.

    ``load_text_file`` refuses an oversized file first, and this input would
    then record ``unreadable_file`` — a decoding problem — for a file that was
    merely large.
    """

    from agents_shipgate.inputs.common import MAX_INPUT_FILE_BYTES
    from agents_shipgate.inputs.mcp_idioms import MAX_SOURCE_FILE_BYTES

    assert MAX_SOURCE_FILE_BYTES < MAX_INPUT_FILE_BYTES


def test_a_file_that_is_not_utf8_is_recorded_rather_than_fatal(tmp_path):
    """One undecodable file must not cost the repository its whole route."""

    workspace = _mongodb_shaped(tmp_path)
    broken = workspace / "packages" / "tools-mongodb" / "src" / "broken.ts"
    broken.write_bytes(b'// tool\nconst s = "\xff\xfe";\n')

    loaded = load_mcp_server_source(_source("packages"), workspace)

    assert [
        omission.reason
        for omission in loaded.omissions
        if omission.subject.endswith("broken.ts")
    ] == ["unreadable_file"]
    assert "drop-database" in {tool.name for tool in loaded.tools}


def test_the_cap_default_is_a_real_bound():
    assert MAX_SCANNED_FILES > 0


# --- Discovery --------------------------------------------------------------


def _inventory(workspace: Path) -> list[Path]:
    from agents_shipgate.cli.discovery.artifacts import _candidate_files

    return _candidate_files(workspace)


def test_discovery_needs_both_a_dependency_and_a_registration(tmp_path):
    workspace = _mongodb_shaped(tmp_path)
    found = discover_mcp_server_source(workspace, files=_inventory(workspace))
    assert found.detected
    assert found.path == "packages/tools-mongodb/src/tools"
    assert "drop-database" in found.tool_names

    # Remove the dependency evidence and the same registrations prove nothing:
    # a class of one's own that spells a field `toolName` is a coincidence of
    # spelling until the repository says it speaks MCP.
    (workspace / "package.json").write_text(
        json.dumps({"dependencies": {"express": "^4"}}), encoding="utf-8"
    )
    assert not discover_mcp_server_source(
        workspace, files=_inventory(workspace)
    ).detected


def test_a_dependency_without_a_registration_claims_nothing(tmp_path):
    workspace = tmp_path / "client"
    (workspace / "src").mkdir(parents=True)
    (workspace / "package.json").write_text(
        json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1"}}),
        encoding="utf-8",
    )
    (workspace / "src" / "client.ts").write_text(
        'const client = new Client();\nawait client.callTool("x");\n', encoding="utf-8"
    )
    assert not discover_mcp_server_source(
        workspace, files=_inventory(workspace)
    ).detected


def test_discovery_reads_no_more_files_than_it_says_it_did(tmp_path):
    """The cap and the flag that reports it must describe the same walk.

    The slice was dropped when the loop was rewritten, so `truncated` was
    published on a run that had read every file: the evidence line told a
    reader "this count is a lower bound" for a count that was exact, and
    `detect` — which runs on an unknown workspace with no manifest — walked an
    unbounded number of source files.
    """

    workspace = tmp_path / "many"
    (workspace / "src").mkdir(parents=True)
    (workspace / "package.json").write_text(
        json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1"}}),
        encoding="utf-8",
    )
    for index in range(6):
        (workspace / "src" / f"t{index}.ts").write_text(
            f'server.registerTool("tool_{index}", {{}}, handler);\n', encoding="utf-8"
        )

    capped = discover_mcp_server_source(
        workspace, files=_inventory(workspace), max_source_files=2
    )
    assert capped.truncated is True
    assert len(capped.tool_names) == 2
    assert any("lower bound" in line for line in capped.evidence)

    whole = discover_mcp_server_source(workspace, files=_inventory(workspace))
    assert whole.truncated is False
    assert len(whole.tool_names) == 6
    assert not any("lower bound" in line for line in whole.evidence)


def test_the_dependency_point_is_awarded_to_dependency_evidence(tmp_path):
    """Scored by value, not by position in the rendered evidence list.

    `evidence` is ordered for a human and gains conditional lines at the end,
    so awarding the point to `evidence[1]` made the published confidence
    depend on a list index rather than on the fact it stands for.
    """

    workspace = _grafana_shaped(tmp_path)
    found = discover_mcp_server_source(workspace, files=_inventory(workspace))

    assert found.framework_evidence
    # At least one dependency reason is rendered, which is what the scoring
    # matches on. Not containment: `_evidence_lines` caps the reasons it shows,
    # so a workspace with many declaring packages renders only some of them.
    assert set(found.framework_evidence) & set(found.evidence)
    detection = next(
        item
        for item in detect_workspace(workspace).frameworks
        if item.type == SOURCE_TYPE
    )
    # 2.0 registration + 1.0 declared dependency.
    assert detection.score == 3.0
    assert detection.confidence == "medium"


def test_a_committed_export_keeps_the_route_it_already_had(tmp_path):
    """Acceptance: the snapshot route stays preferred where both see one server.

    An export is the server's own contract, carries the input schemas this
    input does not read, and is ``high`` against this route's ``medium``. The
    withheld route is *named* in ``excluded_sources`` — a route that vanished
    without a reason would be indistinguishable from one nobody implemented.
    """

    workspace = _grafana_shaped(tmp_path)
    (workspace / "mcp-tools.json").write_text(
        json.dumps({"tools": [{"name": "update_incident", "inputSchema": {}}]}),
        encoding="utf-8",
    )
    result = detect_workspace(workspace)

    assert [source["type"] for source in result.suggested_sources] == ["mcp"]
    withheld = [
        entry for entry in result.excluded_sources if entry["type"] == SOURCE_TYPE
    ]
    assert len(withheld) == 1
    assert "mcp-tools.json" in withheld[0]["reason"]


def test_an_export_for_one_server_does_not_erase_another_s_registrations(tmp_path):
    """The export has to name *this* surface before it displaces it.

    Withholding on the mere existence of an export anywhere in the workspace
    meant that in a repository holding two servers, an export committed for one
    deleted every source-only registration of the other. The route that stood
    down was the only route those tools had.
    """

    workspace = tmp_path / "two"
    for name, tool in (("alpha", "alpha_read"), ("beta", "beta_write")):
        package = workspace / "servers" / name
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1"}}),
            encoding="utf-8",
        )
        (package / "index.ts").write_text(
            f'server.registerTool("{tool}", {{}}, handler);\n', encoding="utf-8"
        )
    # Only alpha commits an export.
    (workspace / "servers" / "alpha" / "mcp-tools.json").write_text(
        json.dumps({"tools": [{"name": "alpha_read", "inputSchema": {}}]}),
        encoding="utf-8",
    )

    found = discover_mcp_server_source(
        workspace,
        files=_inventory(workspace),
        exported_source_paths=["servers/alpha/mcp-tools.json"],
    )

    assert found.detected
    assert "beta_write" in found.tool_names
    assert found.excluded == ()
    assert any("does not name 1 of these registrations" in line for line in found.evidence)
    assert any("beta_write" in line for line in found.evidence)


def test_a_partial_export_does_not_erase_the_rest_of_one_server(tmp_path):
    """Same rule inside a single server: cover the surface or stand aside."""

    workspace = _grafana_shaped(tmp_path)
    (workspace / "tools" / "extra.go").write_text(
        "package tools\n"
        'var Delete = mcpgrafana.MustTool("delete_incident", d, h)\n',
        encoding="utf-8",
    )
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "update_incident", "inputSchema": {}}]}),
        encoding="utf-8",
    )

    found = discover_mcp_server_source(
        workspace, files=_inventory(workspace), exported_source_paths=["tools.json"]
    )

    assert found.detected
    assert "delete_incident" in found.tool_names
    assert found.excluded == ()


def test_a_complete_export_still_displaces_the_source_route(tmp_path):
    """The withheld case is unchanged where the export really does cover."""

    workspace = _grafana_shaped(tmp_path)
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "update_incident", "inputSchema": {}}]}),
        encoding="utf-8",
    )

    found = discover_mcp_server_source(
        workspace, files=_inventory(workspace), exported_source_paths=["tools.json"]
    )

    assert not found.detected
    assert len(found.excluded) == 1
    assert "names every one of these 1 registrations" in found.excluded[0]["reason"]


def test_a_wildcard_export_never_displaces_the_source_route(tmp_path):
    """A wildcard claims a surface without naming it, so it contains nothing."""

    workspace = _grafana_shaped(tmp_path)
    (workspace / "tools.json").write_text(
        json.dumps({"wildcard": True, "tools": []}), encoding="utf-8"
    )

    found = discover_mcp_server_source(
        workspace, files=_inventory(workspace), exported_source_paths=["tools.json"]
    )

    assert found.detected
    assert found.excluded == ()


def test_detect_and_scan_read_a_file_the_same_way(tmp_path):
    """One decoding contract, or `detect` names a route `scan` cannot fill.

    Discovery decoded with `errors="replace"`, so it could resolve a
    registration out of a file the adapter then refuses as `unreadable_file` —
    `detect` promising tools that `scan` does not enumerate.
    """

    workspace = _grafana_shaped(tmp_path)
    # The undecodable bytes sit in a comment and the registration beside them
    # is perfectly ordinary. That is what makes the two readers disagree: a
    # lenient decode replaces the bytes and goes on to resolve `broken_file`,
    # while the adapter refuses the file and enumerates nothing from it. A
    # test whose bad bytes are *inside the name* cannot see the difference,
    # because the replacement characters fail the tool-name shape either way.
    (workspace / "tools" / "broken.go").write_bytes(
        b"package tools\n"
        b"// \xff\xfe\n"
        b'var T = mcpgrafana.MustTool("broken_file", d, h)\n'
    )

    found = discover_mcp_server_source(workspace, files=_inventory(workspace))
    loaded = load_mcp_server_source(_source("tools"), workspace)

    assert set(found.tool_names) == {tool.name for tool in loaded.tools}
    assert "broken_file" not in found.tool_names
    assert "unreadable_file" in {omission.reason for omission in loaded.omissions}


# --- detect -----------------------------------------------------------------


@pytest.mark.parametrize("build", [_mongodb_shaped, _grafana_shaped])
def test_detect_reports_a_server_it_used_to_call_a_non_agent_project(
    tmp_path, build
):
    workspace = build(tmp_path)
    result = detect_workspace(workspace)

    assert result.is_agent_project is True
    detection = next(
        item for item in result.frameworks if item.type == SOURCE_TYPE
    )
    assert detection.evidence
    assert [source["type"] for source in result.suggested_sources] == [SOURCE_TYPE]
    assert "Stop:" not in result.next_action


def test_detect_and_scan_agree_on_the_tool_set(tmp_path):
    """A route ``detect`` promises and ``scan`` refuses is the worst outcome.

    They read the same files through the same predicate for exactly this
    reason; the first draft had a second, weaker pre-filter in discovery and
    the two disagreed by four tools on the MongoDB repository.
    """

    workspace = _mongodb_shaped(tmp_path)
    found = discover_mcp_server_source(workspace, files=_inventory(workspace))
    loaded = load_mcp_server_source(_source(found.path or "."), workspace)

    assert set(found.tool_names) == {tool.name for tool in loaded.tools}


def test_a_monorepo_server_resolves_to_one_manifest_scope(tmp_path):
    """One server is one scope, however many packages hold its tools.

    Contributing each registration file as scope evidence made
    ``mongodb-js/mongodb-mcp-server`` read as six separate projects, so
    ``detect`` published "run init in one of these" — and the package that
    actually holds the tools declares no MCP dependency of its own, so the
    published step returned ``is_agent_project: false``. A next step that
    cannot change the answer is the defect #399 named.
    """

    workspace = _mongodb_shaped(tmp_path)
    other = workspace / "packages" / "tools-atlas" / "src" / "tools"
    other.mkdir(parents=True)
    (other / "createCluster.ts").write_text(
        'class C { static toolName = "atlas-create-cluster"; }\n', encoding="utf-8"
    )
    for package in ("tools-mongodb", "tools-atlas"):
        (workspace / "packages" / package / "package.json").write_text(
            json.dumps({"name": package}), encoding="utf-8"
        )

    result = detect_workspace(workspace)

    assert result.agent_scope == "single"
    assert [candidate.path for candidate in result.agent_project_candidates] == ["."]
    detection = next(
        item for item in result.frameworks if item.type == SOURCE_TYPE
    )
    assert detection.candidate_files == ["packages"]
    assert result.suggested_sources == [{"type": SOURCE_TYPE, "path": "packages"}]
    # And the route the scope resolves to is the one that reads both packages.
    loaded = load_mcp_server_source(_source("packages"), workspace)
    assert "atlas-create-cluster" in {tool.name for tool in loaded.tools}


def test_the_detection_confidence_reflects_both_facts_behind_it(tmp_path):
    """A registration *and* a declared dependency is not the weakest answer.

    The label is what a reader of ``detect --json`` sees beside the route, and
    reporting ``low`` for a conjunction the gate deliberately requires would
    understate the only evidence there is.
    """

    workspace = _grafana_shaped(tmp_path)
    detection = next(
        item
        for item in detect_workspace(workspace).frameworks
        if item.type == SOURCE_TYPE
    )
    assert detection.confidence == "medium"


def test_init_writes_the_source_detect_suggested(tmp_path):
    from agents_shipgate.cli.discovery.template import render_auto_manifest

    workspace = _grafana_shaped(tmp_path)
    rendered = render_auto_manifest(workspace, detect_workspace(workspace))

    assert f"type: {SOURCE_TYPE}" in rendered.text
    assert "path: tools" in rendered.text
    assert rendered.tool_surface_origin == "detected"


# --- End to end -------------------------------------------------------------


def test_scan_publishes_the_tools_and_accounts_for_the_unenumerated_one(tmp_path):
    from agents_shipgate.cli.scan import run_scan

    workspace = _mongodb_shaped(tmp_path)
    (workspace / "packages" / "tools-mongodb" / "src" / "tools" / "read" / "export.ts").write_text(
        "export class ExportTool extends MongoDBToolBase {\n"
        "    static toolName = EXPORT_TOOL_NAME;\n"
        "}\n",
        encoding="utf-8",
    )
    (workspace / "shipgate.yaml").write_text(
        'version: "0.1"\n'
        "project:\n"
        "  name: mongodb-mcp-server\n"
        "agent:\n"
        "  name: mongodb-mcp-server\n"
        "  declared_purpose:\n"
        "    - expose MongoDB operations as MCP tools\n"
        "environment:\n"
        "  target: local\n"
        "tool_sources:\n"
        "  - id: mongodb_tools\n"
        f"    type: {SOURCE_TYPE}\n"
        "    path: packages\n",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    names = {row["name"] for row in report.tool_catalog}
    assert {"drop-database", "delete-many"} <= names
    assert "export" not in names

    ledger = report.surface_exclusions
    unenumerated = [
        entry for entry in ledger.entries if entry.reason == "name_not_literal"
    ]
    assert len(unenumerated) == 1
    assert unenumerated[0].stage == "adapter_parse"
    assert unenumerated[0].subject.endswith("export.ts:2")
    assert unenumerated[0].accounting == "evidence_gap"


def test_an_export_beside_the_source_route_keeps_its_high_confidence(tmp_path):
    """Both routes configured: neither degrades the other.

    Observations are never joined by name (#386), so the export's actions keep
    their own provider-scoped identity and their ``high`` extraction evidence,
    and the source route's sit beside them at ``medium``.
    """

    from agents_shipgate.cli.scan import run_scan

    workspace = _grafana_shaped(tmp_path)
    (workspace / "tools.json").write_text(
        json.dumps({"tools": [{"name": "update_incident", "inputSchema": {}}]}),
        encoding="utf-8",
    )
    (workspace / "shipgate.yaml").write_text(
        'version: "0.1"\n'
        "project:\n"
        "  name: mcp-grafana\n"
        "agent:\n"
        "  name: mcp-grafana\n"
        "  declared_purpose:\n"
        "    - expose Grafana operations as MCP tools\n"
        "environment:\n"
        "  target: local\n"
        "tool_sources:\n"
        "  - id: exported\n"
        "    type: mcp\n"
        "    path: tools.json\n"
        "  - id: registrations\n"
        f"    type: {SOURCE_TYPE}\n"
        "    path: tools\n",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=workspace / "shipgate.yaml",
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    by_provider = {row["provider"]: row for row in report.tool_catalog}
    assert by_provider["exported"]["confidence"] == "high"
    assert by_provider["registrations"]["confidence"] == "medium"


# --- The trigger catalog ----------------------------------------------------


def test_the_trigger_rule_routes_exactly_the_published_idiom_tokens():
    """One token list, or the router and the reader drift apart.

    A route table hand-maintained beside the function that owns the routes
    drifts in the direction nobody checks (#433), and the direction nobody
    checks here is a registration idiom that stops routing its own diffs.
    """

    catalog = json.loads((REPO_ROOT / "docs" / "triggers.json").read_text())
    rule = next(
        item
        for item in catalog["rules"]
        if item["id"] == "TRIGGER-MCP-TOOL-REGISTRATION-SOURCE"
    )
    tokens = sorted(clause["diff_contains"] for clause in rule["when"]["any_of"])
    assert tokens == sorted(DIFF_TOKENS)
    assert rule["action"] == "run_shipgate"
    assert rule["surface_class"] == "capability"


def test_the_trigger_rule_routes_the_diff_that_motivated_it():
    """A diff adding a write-stage confirmation to a MongoDB tool class.

    ``mongodb-js/mongodb-mcp-server#1417`` was the walk that produced this
    issue: ``verify --preview`` reported ``matched_rules: []`` on it, so the
    change that adds a human confirmation control to write-stage aggregations
    routed nowhere.
    """

    from agents_shipgate.triggers import evaluate

    verdict = evaluate(
        paths=["packages/tools-mongodb/src/tools/read/aggregate.ts"],
        diff_text=(
            "+++ b/packages/tools-mongodb/src/tools/read/aggregate.ts\n"
            '+    static toolName = "aggregate";\n'
            "+    protected async confirmWriteStages(): Promise<void> {}\n"
        ),
        manifest_present=False,
        user_requested=False,
    )

    assert "TRIGGER-MCP-TOOL-REGISTRATION-SOURCE" in [
        rule["id"] for rule in verdict["matched_rules"]
    ]
    assert verdict["should_run"] is True
