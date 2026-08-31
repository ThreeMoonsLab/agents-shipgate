"""The published determinism boundary and the engine behaviour it claims (#473).

Three obligations, and they are different from each other:

1. **Fail-closed enumeration.** A route the page could omit is worse than no
   page, so an adapter without coverage, or a source type wired into a ceiling
   vocabulary without a route, must break generation. The negative controls
   here are the committed proof that it does.
2. **Committed equals generated.** CI runs
   ``scripts/generate_schemas.py --check``; this asserts the same thing from
   the suite, because a boundary page that drifted for one release is a
   published false claim for that release.
3. **The cells are true of the engine.** Every other property is about
   bookkeeping. This one runs the real adapters over real inputs and compares
   what they produce against what the page says they produce.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any, ClassVar, Literal

import pytest

from agents_shipgate.core.semantic_assessment import (
    extraction_is_complete,
    surface_is_complete,
)
from agents_shipgate.inputs import coverage as coverage_module
from agents_shipgate.inputs.conductor import load_conductor_artifacts
from agents_shipgate.inputs.coverage import (
    DECLARATION_SHAPE_ORDER,
    BoundaryCell,
    BoundaryCoverageError,
    ResolvedCell,
    SourceCoverage,
    build_boundary_matrix,
)
from agents_shipgate.inputs.crewai import load_crewai_artifacts
from agents_shipgate.inputs.google_adk import load_google_adk_artifacts
from agents_shipgate.inputs.langchain import load_langchain_artifacts
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.mcp_manifest import load_codex_config_mcp_sources
from agents_shipgate.inputs.n8n import load_n8n_artifacts
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.inputs.protocol import REGISTRY, AdapterRegistry
from agents_shipgate.schemas.manifest import ToolSourceConfig

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- helpers ----------------------------------------------------------------


def _cell(adapter: str, shape: str, variant: str | None = None) -> ResolvedCell:
    """The published cell for one route, or a failure naming what exists."""

    matrix = build_boundary_matrix()
    source = next(item for item in matrix.sources if item.adapter == adapter)
    for cell in source.cells:
        if cell.shape == shape and cell.variant == variant:
            return cell
    published = [(cell.shape, cell.variant) for cell in source.cells]
    raise AssertionError(f"{adapter} publishes no {(shape, variant)}; it has {published}")


def _assert_matches_published(tool: Any, cell: ResolvedCell) -> None:
    """The measured tool is exactly what the page promised for this route."""

    assert tool.source_type in cell.emits, (
        f"{tool.source_type!r} is not one of the published {cell.emits} for "
        f"{cell.shape}/{cell.variant}"
    )
    assert tool.extraction_confidence == cell.ceiling
    assert tool.extraction.get("surface") == cell.surface
    for flag in cell.surface_flags:
        assert tool.annotations.get(flag) is True, f"{flag} not set on the measured tool"
    # The published consequence is derived from these two predicates, so
    # asserting them against the real tool is what ties the page to behaviour
    # rather than to its own arithmetic.
    assert extraction_is_complete(tool) is cell.extraction_complete
    assert surface_is_complete(tool) is cell.surface_complete


class _Manifest:
    """The subset of the manifest a framework loader reads."""

    def __init__(self, **sections: Any) -> None:
        self.tool_sources: list[ToolSourceConfig] = sections.pop("tool_sources", [])
        for name in ("google_adk", "langchain", "crewai", "n8n", "codex_plugins"):
            setattr(self, name, sections.pop(name, None))
        assert not sections, sections


def _adk_tools(tmp_path: Path, filename: str, body: str) -> list[Any]:
    (tmp_path / filename).write_text(body, encoding="utf-8")
    manifest = _Manifest(
        tool_sources=[ToolSourceConfig(id="adk", type="google_adk", path=filename)]
    )
    loaded, _ = load_google_adk_artifacts(manifest, tmp_path)
    return [tool for source in loaded for tool in source.tools]


# --- fail-closed enumeration ------------------------------------------------


def test_every_builtin_adapter_publishes_coverage():
    """The whole registry, described — not a list kept beside it."""

    matrix = build_boundary_matrix()
    assert {source.adapter for source in matrix.sources} == {
        adapter.source_type for adapter in AdapterRegistry()
    }
    for source in matrix.sources:
        assert {cell.shape for cell in source.cells} == set(DECLARATION_SHAPE_ORDER)


def test_an_adapter_without_coverage_breaks_generation():
    """Negative control for "adding a source type without metadata fails the build"."""

    class UndocumentedAdapter:
        source_type: ClassVar[str] = "mcp"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):  # pragma: no cover - never called
            raise AssertionError("the boundary generator must not run an adapter")

    registry = AdapterRegistry(autopopulate=False)
    registry.register(UndocumentedAdapter())
    with pytest.raises(BoundaryCoverageError) as excinfo:
        build_boundary_matrix(registry)
    assert "declares no coverage" in str(excinfo.value)


def test_a_ceiling_vocabulary_token_without_a_route_breaks_generation(monkeypatch):
    """Negative control for the other direction of the enumeration.

    The registry proves every *adapter* is described. This is what stops a new
    source type from being wired into a ceiling — where the engine gates on it
    — while the page silently omits the route.
    """

    monkeypatch.setattr(
        coverage_module,
        "AST_ONLY_SOURCE_TYPES",
        frozenset({*coverage_module.AST_ONLY_SOURCE_TYPES, "brand_new_ast_tool"}),
    )
    with pytest.raises(BoundaryCoverageError) as excinfo:
        build_boundary_matrix()
    assert "brand_new_ast_tool" in str(excinfo.value)


def test_coverage_must_answer_every_declaration_shape():
    with pytest.raises(ValueError) as excinfo:
        SourceCoverage(
            # A `tool_sources[]`-configurable name, so the shape rule is the
            # one under test rather than the manifest-section rule.
            adapter="mcp",
            label="Partial",
            reads="Something.",
            cells=(
                BoundaryCell(shape="export_artifact", status="not_applicable", reads="x"),
            ),
        )
    assert "answers no declaration shape" in str(excinfo.value)


def test_two_routes_through_one_shape_must_name_themselves():
    with pytest.raises(ValueError) as excinfo:
        SourceCoverage(
            adapter="openapi",
            label="Ambiguous",
            reads="Something.",
            cells=tuple(
                BoundaryCell(shape=shape, status="not_applicable", reads="x")
                for shape in (*DECLARATION_SHAPE_ORDER, "factory")
            ),
        )
    assert "distinct `variant`" in str(excinfo.value)


def test_a_source_code_route_cannot_publish_high_without_proving_the_surface():
    """The engine caps AST-only source types, so the page may not promise otherwise."""

    with pytest.raises(ValueError) as excinfo:
        BoundaryCell(
            shape="literal_registration",
            status="extracted",
            reads="A decorated function.",
            emits=("langchain_function",),
            ceiling="high",
        )
    assert "caps it below high" in str(excinfo.value)


def test_a_route_cannot_claim_a_surface_flag_the_engine_ignores():
    """Negative control for the fail-open direction of `surface_flags`.

    A flag the completeness predicate does not read is inert on the probe, and
    inert reads as "surface complete" — so a typo would publish `proven` for
    exactly the routes that prove nothing.
    """

    with pytest.raises(ValueError) as excinfo:
        BoundaryCell(
            shape="dynamic_construction",
            status="extracted",
            reads="A server naming no tools.",
            emits=("codex_config_mcp",),
            ceiling="medium",
            surface_flags=("wildcard_tool",),
        )
    assert "not read by the engine's completeness predicate" in str(excinfo.value)


def test_a_manifest_section_must_be_a_real_manifest_key():
    """The section names differ from the adapter's own name for three inputs."""

    with pytest.raises(ValueError) as excinfo:
        SourceCoverage(
            adapter="mcp",
            label="MCP",
            reads="Something.",
            manifest_section="mcp_servers",
            cells=tuple(
                BoundaryCell(shape=shape, status="not_applicable", reads="x")
                for shape in DECLARATION_SHAPE_ORDER
            ),
        )
    assert "is not a field on AgentsShipgateManifest" in str(excinfo.value)


def test_an_input_a_manifest_cannot_ask_for_is_refused():
    with pytest.raises(ValueError) as excinfo:
        SourceCoverage(
            adapter="validation",
            label="Validation traces",
            reads="Something.",
            cells=tuple(
                BoundaryCell(shape=shape, status="not_applicable", reads="x")
                for shape in DECLARATION_SHAPE_ORDER
            ),
        )
    assert "must name the manifest section it runs from" in str(excinfo.value)


def test_every_published_configuration_route_is_reachable():
    """Both halves, per input — the `scope` shortcut gets `conductor` wrong.

    `conductor` is a `per_scan` adapter with no manifest section of its own, so
    deriving the section route from `scope` would publish a `conductor:` key
    that `AgentsShipgateManifest` does not have.
    """

    from agents_shipgate.schemas.manifest import AgentsShipgateManifest
    from agents_shipgate.schemas.manifest.tool_sources import BUILTIN_TOOL_SOURCE_TYPES

    for source in build_boundary_matrix().sources:
        assert source.configured_as, source.adapter
        assert ("tool_sources" in source.configured_as) == (
            source.adapter in BUILTIN_TOOL_SOURCE_TYPES
        ), source.adapter
        assert ("manifest_section" in source.configured_as) == (
            source.manifest_section is not None
            and source.manifest_section_role == "activates"
        ), source.adapter
        if source.manifest_section is not None:
            assert source.manifest_section in AgentsShipgateManifest.model_fields


def test_the_published_threshold_is_the_one_that_binds():
    """One action below `high` withholds a verdict; the 50% ratio never binds first.

    `evidence_below_ie_threshold` is an OR whose first clause is
    `semantic_coverage.gap_count > 0`, and every below-`high` action raises an
    `incomplete_surface` semantic issue. Publishing the
    `_LOW_CONFIDENCE_TOOL_RATIO` clause instead told an adopter with one medium
    action in ten that they were under the bar.
    """

    from agents_shipgate.ci.release_decision import (
        _low_confidence_tool_threshold,
        evidence_below_ie_threshold,
    )
    from agents_shipgate.inputs.coverage import CELL_OUTCOME_VERDICTS
    from agents_shipgate.schemas.report import (
        EvidenceCoverageDecision,
        SemanticCoverageDecision,
    )

    # The mechanism, not the wording: one action below `high` raises one
    # `incomplete_surface` semantic issue, `_semantic_coverage` emits one gap
    # for it, and the predicate's first clause is satisfied — nine of ten
    # actions still proven, and the run is already below the threshold.
    one_gap_in_ten = EvidenceCoverageDecision(
        level="mixed",
        human_review_recommended=False,
        source_warning_count=0,
        semantic_coverage=SemanticCoverageDecision(
            total_actions=10, pass_eligible_actions=9, gap_count=1
        ),
        low_confidence_tool_count=1,
    )
    assert evidence_below_ie_threshold(one_gap_in_ten, tool_count=10) is True

    # And the ratio clause on its own would not have fired here, which is
    # exactly why publishing it was wrong.
    assert 1 < _low_confidence_tool_threshold(10)

    page = (REPO_ROOT / "docs" / "determinism-boundary.md").read_text(encoding="utf-8")
    for outcome in ("low_confidence", "set_unproven"):
        assert "half" not in CELL_OUTCOME_VERDICTS[outcome], outcome
    assert "zero-tolerance" in page
    assert "half the" not in page


def test_a_contract_route_cannot_claim_surface_evidence_nobody_writes():
    with pytest.raises(ValueError) as excinfo:
        BoundaryCell(
            shape="export_artifact",
            status="extracted",
            reads="An export.",
            emits=("mcp",),
            ceiling="high",
            surface="enumerated",
        )
    assert "no adapter writes extraction['surface']" in str(excinfo.value)


def test_generation_does_not_depend_on_installed_plugins():
    """A specification whose contents varied by machine is not a specification.

    ``REGISTRY`` is process-global and a scan run earlier in the same process
    may have added third-party adapters to it, so the default must be a fresh
    built-in registry rather than that one.
    """

    class ThirdPartyAdapter:
        source_type: ClassVar[str] = "acme_tools"
        scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
        artifact_class: ClassVar[type | None] = None

        def load(self, source, base_dir, manifest):  # pragma: no cover - never called
            raise AssertionError("the boundary generator must not run an adapter")

    REGISTRY.register(ThirdPartyAdapter())
    try:
        adapters = {source.adapter for source in build_boundary_matrix().sources}
    finally:
        REGISTRY._adapters.pop("acme_tools")
    assert "acme_tools" not in adapters


# --- committed equals generated ---------------------------------------------


def _load_generator():
    """Import the generator without putting ``scripts/`` on ``sys.path``.

    The same helper ``tests/test_schema_roundtrip.py`` uses, and for the same
    reason: an insert at position 0 outlives the test and shadows any module
    sharing a name with a file in ``scripts/`` for the rest of the worker.
    """

    spec = importlib.util.spec_from_file_location(
        "agents_shipgate_boundary_generator", REPO_ROOT / "scripts" / "generate_schemas.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["determinism-boundary.md", "determinism-boundary.json"])
def test_committed_boundary_matches_the_generator(name):
    generator = _load_generator()
    builder = (
        generator.build_determinism_boundary_page
        if name.endswith(".md")
        else generator.build_determinism_boundary_matrix
    )
    target, content = builder()
    assert target.name == name
    assert target.read_text(encoding="utf-8") == content, (
        f"docs/{name} is stale; run `python scripts/generate_schemas.py`"
    )


def test_the_page_names_every_input_and_every_shape():
    page = (REPO_ROOT / "docs" / "determinism-boundary.md").read_text(encoding="utf-8")
    matrix = build_boundary_matrix()
    for source in matrix.sources:
        assert f"### {source.label}" in page
    for shape in DECLARATION_SHAPE_ORDER:
        assert f"`{shape}`" in page


# --- the cells are true of the engine ---------------------------------------


def test_mcp_export_reaches_the_published_high_ceiling(tmp_path):
    (tmp_path / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "search_cases",
                        "description": "Search support cases.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = load_mcp_tools(
        ToolSourceConfig(id="support", type="mcp", path="tools.json"), tmp_path
    )
    cell = _cell("mcp", "export_artifact")
    assert cell.extraction_permits_pass is True
    _assert_matches_published(loaded.tools[0], cell)


def test_a_wildcard_mcp_export_reaches_high_and_still_proves_no_surface(tmp_path):
    """The published `set_unproven` outcome: a `high` ceiling that cannot pass.

    This is the cell that would be wrong if the page restated "high means
    pass-eligible" instead of asking the engine.
    """

    (tmp_path / "tools.json").write_text(json.dumps({"wildcard": True}), encoding="utf-8")
    loaded = load_mcp_tools(
        ToolSourceConfig(id="support", type="mcp", path="tools.json"), tmp_path
    )
    cell = _cell("mcp", "dynamic_construction")
    assert cell.outcome == "set_unproven"
    assert cell.extraction_permits_pass is False
    _assert_matches_published(loaded.tools[0], cell)


def test_openapi_operations_reach_the_published_high_ceiling(tmp_path):
    (tmp_path / "api.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Support", "version": "1.0.0"},
                "paths": {
                    "/cases/{case_id}": {
                        "get": {
                            "operationId": "get_case",
                            "summary": "Read one case.",
                            "parameters": [
                                {
                                    "name": "case_id",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "string"},
                                }
                            ],
                            "responses": {"200": {"description": "ok"}},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = load_openapi_tools(
        ToolSourceConfig(id="support", type="openapi", path="api.json"), tmp_path
    )
    _assert_matches_published(loaded.tools[0], _cell("openapi", "export_artifact"))


ADK_RESOLVED_MODULE = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def lookup_case(case_id: str) -> dict:
    """Look up support case metadata for a known case id."""
    return {"status": "ok"}


lookup_case_tool = FunctionTool(func=lookup_case)

root_agent = LlmAgent(
    name="support_agent",
    instruction="Look up cases.",
    tools=[lookup_case_tool],
)
'''

ADK_DYNAMIC_MODULE = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def lookup_case(case_id: str) -> dict:
    """Look up support case metadata for a known case id."""
    return {"status": "ok"}


lookup_case_tool = FunctionTool(func=lookup_case)

root_agent = LlmAgent(
    name="support_agent",
    instruction="Look up cases.",
    tools=[lookup_case_tool, *load_extra_tools()],
)
'''


def test_a_fully_resolved_adk_module_reaches_the_published_high_ceiling(tmp_path):
    """The one source-code route the page claims reaches `high`."""

    tools = _adk_tools(tmp_path, "agent.py", ADK_RESOLVED_MODULE)
    cell = _cell("google_adk", "literal_registration", "Python module")
    assert cell.extraction_permits_pass is True
    _assert_matches_published(tools[0], cell)


def test_an_unresolved_adk_expression_caps_the_module_at_the_published_medium(tmp_path):
    """#473 acceptance: the ADK `medium` ceiling, measured rather than asserted.

    The same function that reached `high` above is `medium` here, which is the
    published claim: completeness is a property of the module, not of the tool
    that happened to be read first.
    """

    tools = _adk_tools(tmp_path, "agent.py", ADK_DYNAMIC_MODULE)
    cell = _cell("google_adk", "dynamic_construction", "module function")
    assert cell.ceiling == "medium"
    assert cell.extraction_permits_pass is False
    _assert_matches_published(tools[0], cell)


def test_an_adk_config_tool_reference_reaches_the_published_low_ceiling(tmp_path):
    tools = _adk_tools(
        tmp_path,
        "agent.yaml",
        json.dumps({"name": "support_agent", "tools": [{"name": "do_thing"}]}),
    )
    _assert_matches_published(
        tools[0], _cell("google_adk", "literal_registration", "agent config")
    )


def test_a_langchain_tool_reaches_the_published_medium_ceiling(tmp_path):
    (tmp_path / "tools.py").write_text(
        '''
from langchain_core.tools import tool


@tool
def lookup_case(case_id: str) -> str:
    """Look up a support case."""
    return "ok"
''',
        encoding="utf-8",
    )
    manifest = _Manifest(
        tool_sources=[ToolSourceConfig(id="lc", type="langchain", path="tools.py")]
    )
    loaded, _ = load_langchain_artifacts(manifest, tmp_path)
    tools = [tool for source in loaded for tool in source.tools]
    cell = _cell("langchain", "literal_registration")
    assert cell.extraction_permits_pass is False
    _assert_matches_published(tools[0], cell)


def test_a_crewai_prebuilt_tool_reaches_the_published_low_ceiling(tmp_path):
    (tmp_path / "crew.py").write_text(
        """
from crewai import Agent
from crewai_tools import FileReadTool

researcher = Agent(
    role="researcher",
    goal="read files",
    backstory="reads",
    tools=[FileReadTool()],
)
""",
        encoding="utf-8",
    )
    manifest = _Manifest(
        tool_sources=[ToolSourceConfig(id="crew", type="crewai", path="crew.py")]
    )
    loaded, _ = load_crewai_artifacts(manifest, tmp_path)
    tools = [tool for source in loaded for tool in source.tools]
    _assert_matches_published(tools[0], _cell("crewai", "factory"))


def test_a_codex_config_server_splits_on_whether_it_names_its_tools(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "documented": {
                        "command": "documented-server",
                        "tools": {
                            "read_doc": {
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "opaque": {"command": "opaque-server"},
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = load_codex_config_mcp_sources(tmp_path, tmp_path)
    by_name = {
        tool.name: tool for source in loaded for tool in source.tools
    }

    documented = _cell("codex_config", "literal_registration", "tool with a schema")
    assert documented.extraction_permits_pass is True
    _assert_matches_published(by_name["read_doc"], documented)

    opaque = _cell("codex_config", "dynamic_construction")
    assert opaque.extraction_permits_pass is False
    _assert_matches_published(by_name["opaque.*"], opaque)


# --- #478 review: routes the first draft of the page got wrong --------------


def test_a_wildcard_inventory_is_reviewed_and_still_proves_nothing(tmp_path):
    """A reviewed file that names no tools loads at `high` and is not `proven`.

    The first draft published one `export_artifact` row per input at `proven`,
    which was false for exactly the inventory an adopter is most likely to
    write first.
    """

    from agents_shipgate.schemas.manifest.langchain import LangChainConfig

    (tmp_path / "inv.json").write_text(json.dumps({"wildcard": True}), encoding="utf-8")
    manifest = _Manifest(
        langchain=LangChainConfig.model_validate({"tool_inventories": ["inv.json"]})
    )
    loaded, _ = load_langchain_artifacts(manifest, tmp_path)
    tools = [tool for source in loaded for tool in source.tools]

    cell = _cell("langchain", "export_artifact", "wildcard inventory")
    assert cell.ceiling == "high"
    assert cell.outcome == "set_unproven"
    assert cell.extraction_permits_pass is False
    _assert_matches_published(tools[0], cell)

    # And the reviewed route beside it still reaches `proven`, so the split is
    # a real distinction rather than a blanket downgrade.
    assert _cell("langchain", "export_artifact", "reviewed inventory").outcome == "proven"


def test_every_inventory_input_publishes_the_wildcard_route():
    """The wrapper behaviour is shared, so no input may omit the row."""

    for adapter in ("langchain", "crewai", "google_adk", "n8n", "codex_plugin"):
        cell = _cell(adapter, "export_artifact", "wildcard inventory")
        assert cell.outcome == "set_unproven", adapter


def test_a_module_gap_lowers_the_actions_a_resolved_toolset_contributed(tmp_path):
    """The downgrade is module-wide, so the page must publish it per emitted type."""

    (tmp_path / "inv.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "search_cases",
                        "description": "Search.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    tools = _adk_tools(
        tmp_path,
        "agent.py",
        """
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset

support = McpToolset(inventory_path="inv.json")

root_agent = LlmAgent(name="a", instruction="x", tools=[support, *load_more()])
""",
    )
    contributed = next(tool for tool in tools if tool.source_type == "mcp")
    assert contributed.extraction_confidence == "medium"

    cell = _cell("google_adk", "dynamic_construction", "resolved toolset actions in the same module")
    assert cell.emits == ("mcp", "openapi")
    assert cell.ceiling == "medium"
    assert extraction_is_complete(contributed) is cell.extraction_complete


def test_an_n8n_expression_tool_name_still_enters_the_catalog(tmp_path):
    """The dynamic n8n route is a *catalog* path, not an omission."""

    from agents_shipgate.schemas.manifest.n8n import N8nConfig

    workflow = {
        "id": "wf1",
        "name": "Agent Flow",
        "nodes": [
            {
                "id": "agent",
                "name": "AI Agent",
                "type": "@n8n/n8n-nodes-langchain.agent",
                "parameters": {},
            },
            {
                "id": "t1",
                "name": "Dyn Tool",
                "type": "@n8n/n8n-nodes-langchain.toolWorkflow",
                "parameters": {"toolName": "={{ $json.tool_name }}", "workflowId": "sub-1"},
            },
        ],
        "connections": {
            "Dyn Tool": {"ai_tool": [[{"node": "AI Agent", "type": "ai_tool", "index": 0}]]}
        },
    }
    (tmp_path / "wf.json").write_text(json.dumps(workflow), encoding="utf-8")
    manifest = _Manifest(n8n=N8nConfig.model_validate({"workflows": ["wf.json"]}))
    loaded, artifacts = load_n8n_artifacts(manifest, tmp_path)
    tools = [tool for source in loaded for tool in source.tools]

    assert artifacts.dynamic_tool_surfaces, "the expression must be recorded"
    _assert_matches_published(
        tools[0], _cell("n8n", "dynamic_construction", "expression-backed tool name")
    )


def test_conductor_dynamic_method_and_dynamic_server_differ(tmp_path):
    """Only a non-literal `method` withholds the action; the server does not."""

    workflow = [
        {
            "name": "order_flow",
            "version": 1,
            "tasks": [
                {
                    "name": "a",
                    "taskReferenceName": "a",
                    "type": "CALL_MCP_TOOL",
                    "inputParameters": {
                        "method": "refund_order",
                        "mcpServer": "${wf.input.server}",
                    },
                },
                {
                    "name": "b",
                    "taskReferenceName": "b",
                    "type": "CALL_MCP_TOOL",
                    "inputParameters": {
                        "method": "${wf.input.m}",
                        "mcpServer": "https://x.invalid",
                    },
                },
            ],
        }
    ]
    (tmp_path / "wf.json").write_text(json.dumps(workflow), encoding="utf-8")
    manifest = _Manifest(
        tool_sources=[ToolSourceConfig(id="c", type="conductor", path="wf.json")]
    )
    loaded, _ = load_conductor_artifacts(manifest, tmp_path)
    tools = [tool for source in loaded for tool in source.tools]

    # The expression-backed *server* kept its action.
    assert [tool.name for tool in tools] == ["refund_order"]
    _assert_matches_published(
        tools[0], _cell("conductor", "dynamic_construction", "expression-backed server")
    )
    # The expression-backed *method* produced none.
    assert _cell(
        "conductor", "dynamic_construction", "expression-backed method"
    ).status == "not_extracted"


def test_a_not_extracted_route_may_still_raise_a_check():
    """`not_extracted` never meant "unseen", and the page no longer says so."""

    from agents_shipgate.checks.registry import check_catalog
    from agents_shipgate.inputs.coverage import CELL_OUTCOME_VERDICTS

    cell = _cell("conductor", "dynamic_construction", "expression-backed method")
    assert cell.raises == ("SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",)
    assert cell.raises[0] in {check.id for check in check_catalog(plugins_enabled=False)}
    assert "no check runs" not in CELL_OUTCOME_VERDICTS["not_extracted"]


def test_a_published_check_id_must_resolve(monkeypatch):
    """Negative control: a renamed check breaks generation, not the page."""

    import agents_shipgate.checks.registry as registry

    monkeypatch.setattr(registry, "check_catalog", lambda **_kwargs: [])
    with pytest.raises(BoundaryCoverageError) as excinfo:
        build_boundary_matrix()
    assert "no registered check owns" in str(excinfo.value)


def test_a_supplemental_section_is_not_a_configuration_route():
    """`codex_plugins:` cannot start the adapter, so it is not advertised as a route."""

    matrix = build_boundary_matrix()
    plugin = next(item for item in matrix.sources if item.adapter == "codex_plugin")
    assert plugin.manifest_section == "codex_plugins"
    assert plugin.manifest_section_role == "supplements"
    assert plugin.configured_as == ("tool_sources",)

    # The loader is why: no `tool_sources[]` row, no artifacts at all.
    from agents_shipgate.inputs.codex_plugin import load_codex_plugin_artifacts

    loaded, artifacts = load_codex_plugin_artifacts(_Manifest(), Path("."))
    assert loaded == [] and artifacts is None


def test_the_inventory_remedy_is_published_only_where_one_exists():
    """`inventory_manifest_key()` decides, not a blanket promise."""

    from agents_shipgate.ci.release_decision import inventory_manifest_key

    for source in build_boundary_matrix().sources:
        emitted = {value for cell in source.cells for value in cell.emits}
        keys = {
            key for value in emitted if (key := inventory_manifest_key(value)) is not None
        }
        # A *set*, not "the first match": an order-dependent answer would make
        # the published remedy move with an unrelated cell edit.
        assert len(keys) <= 1, source.adapter
        assert source.inventory_key == (next(iter(keys)) if keys else None), source.adapter

    # The inputs the engine has no inventory route for must not claim one.
    for adapter in ("openai_agents_sdk", "conductor", "codex_config", "mcp"):
        source = next(
            item for item in build_boundary_matrix().sources if item.adapter == adapter
        )
        assert source.inventory_key is None, adapter


def test_the_page_dates_itself_to_a_release():
    """An archived report's link must be checkable against the scanner that ran."""

    from agents_shipgate import __version__

    matrix = build_boundary_matrix()
    assert matrix.generated_for_version == __version__
    page = (REPO_ROOT / "docs" / "determinism-boundary.md").read_text(encoding="utf-8")
    assert f"agents-shipgate {__version__}" in page
    assert "blob/v<your-version>/docs/determinism-boundary.md" in page


def test_an_input_answering_two_inventory_keys_is_refused():
    """Negative control for the order-dependence the first draft had."""

    from agents_shipgate.inputs.coverage import _inventory_key

    cells = tuple(
        _cell(adapter, "export_artifact", "reviewed inventory")
        for adapter in ("langchain", "crewai")
    )
    with pytest.raises(BoundaryCoverageError) as excinfo:
        _inventory_key(cells)
    assert "differing inventory keys" in str(excinfo.value)


def test_a_section_role_without_a_section_is_refused():
    """The renderer would otherwise print `None:` as the manifest key."""

    with pytest.raises(ValueError) as excinfo:
        SourceCoverage(
            adapter="mcp",
            label="MCP",
            reads="Something.",
            manifest_section_role="supplements",
            cells=tuple(
                BoundaryCell(shape=shape, status="not_applicable", reads="x")
                for shape in DECLARATION_SHAPE_ORDER
            ),
        )
    assert "names none" in str(excinfo.value)


def test_every_dynamic_surface_route_names_the_check_it_feeds():
    """The `not_extracted` claim was false globally, so the fix must be global.

    LangChain, CrewAI, n8n, Google ADK, and Conductor all feed a
    `SHIP-*-DYNAMIC-*` check from the construct their dynamic/factory rows
    describe. Fixing only the Conductor instance the review named would have
    left the same wrong impression on four other inputs.
    """

    expected = {
        ("langchain", "factory"): "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
        ("langchain", "dynamic_construction"): (
            "SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
        ),
        ("crewai", "dynamic_construction"): "SHIP-CREWAI-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
        ("google_adk", "factory"): "SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE",
        ("conductor", "dynamic_construction"): (
            "SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
        ),
        # Not a `SHIP-*-DYNAMIC-*` id, and the same omission: the toolkit
        # constructor's scope bound is exactly what this check reads.
        ("openai_agents_sdk", "factory"): "SHIP-SCOPE-TOOLKIT-UNBOUNDED",
    }
    matrix = build_boundary_matrix()
    for (adapter, shape), check_id in expected.items():
        source = next(item for item in matrix.sources if item.adapter == adapter)
        raised = {
            value for cell in source.cells if cell.shape == shape for value in cell.raises
        }
        assert check_id in raised, (adapter, shape)


def test_the_remedy_never_denies_an_inventory_route_that_exists():
    """The inverse of the P2-1 finding, and just as wrong.

    `codex_plugin` reaches `proven` through a reviewed inventory declared at
    `codex_plugins.mcp_tool_inventories[]`, but `inventory_manifest_key()` — the
    engine's *gap-prescription* table — does not know that key. The remedy may
    say the engine prescribes no inventory remediation; it may not say the input
    has no inventory at all.
    """

    page = (REPO_ROOT / "docs" / "determinism-boundary.md").read_text(encoding="utf-8")
    assert "has no `tool_inventories[]` key" not in page
    assert "prescribes no `tool_inventories[]` remediation" in page

    plugin = next(
        item for item in build_boundary_matrix().sources if item.adapter == "codex_plugin"
    )
    assert plugin.inventory_key is None
    assert _cell("codex_plugin", "export_artifact", "reviewed inventory").outcome == "proven"


def test_every_wildcard_route_publishes_the_check_it_raises():
    """Derived from the flag, so no wildcard cell can forget it.

    `checks.inventory` raises `SHIP-INVENTORY-WILDCARD-TOOLS` on *any* tool
    annotated `wildcard_tools`. Declaring that per cell would be a second table
    for a relationship the engine already owns, and the cell that forgot it
    would be the one that mattered.
    """

    from agents_shipgate.inputs.coverage import WILDCARD_TOOLS_CHECK

    wildcard_cells = [
        (source.adapter, cell)
        for source in build_boundary_matrix().sources
        for cell in source.cells
        if "wildcard_tools" in cell.surface_flags
    ]
    assert len(wildcard_cells) >= 8, "the wildcard routes went missing"
    for adapter, cell in wildcard_cells:
        assert WILDCARD_TOOLS_CHECK in cell.raises, (adapter, cell.shape, cell.variant)
        # And it never claims a pass, which is the pairing that makes the row
        # actionable: a wildcard both fails to prove a surface and raises HIGH.
        assert cell.extraction_permits_pass is False, (adapter, cell.variant)


def test_the_derived_wildcard_check_is_a_real_check():
    from agents_shipgate.checks.registry import check_catalog
    from agents_shipgate.inputs.coverage import WILDCARD_TOOLS_CHECK

    assert WILDCARD_TOOLS_CHECK in {
        check.id for check in check_catalog(plugins_enabled=False)
    }


def test_every_at_a_glance_link_resolves_to_a_heading():
    """The summary table is the page's entry point, so its links must land.

    Approximating GitHub's anchor rule with a couple of `replace` calls emitted
    `#openai-agents-sdk-(python)`: the parentheses survived in the link and not
    in the heading, so the one row a Python adopter would click went nowhere.
    """

    page = (REPO_ROOT / "docs" / "determinism-boundary.md").read_text(encoding="utf-8")
    headings = {
        "".join(char for char in line[4:].strip().lower() if char.isalnum() or char in " -")
        .replace(" ", "-")
        for line in page.splitlines()
        if line.startswith("### ")
    }
    links = re.findall(r"\]\(#([a-z0-9-]+)\)", page)
    assert len(links) == len(build_boundary_matrix().sources), links
    for anchor in links:
        assert anchor in headings, f"{anchor} matches no heading; have {sorted(headings)}"


def test_the_summary_table_speaks_the_adopter_s_language():
    """No internal token in the table a reader meets first.

    The page opened with 78 lines of taxonomy — `set_unproven`, `sdk_function`,
    `incomplete_surface` — before anything about the reader's own repository.
    The tokens still exist, in the per-input detail and in the JSON, which is
    where precision is the point.
    """

    page = (REPO_ROOT / "docs" / "determinism-boundary.md").read_text(encoding="utf-8")
    summary = page[: page.index("## Your framework")]
    for token in ("set_unproven", "low_confidence", "not_extracted", "not_applicable"):
        assert token not in summary, token
    for source in build_boundary_matrix().sources:
        assert f"[{source.label}](#" in summary, source.label
