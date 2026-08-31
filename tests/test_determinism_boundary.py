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

import json
from pathlib import Path
from typing import Any, ClassVar, Literal

import pytest

from agents_shipgate.core.semantic_assessment import (
    extraction_is_complete,
    surface_is_complete,
)
from agents_shipgate.inputs import coverage as coverage_module
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
        for name in ("google_adk", "langchain", "crewai"):
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
            adapter="partial",
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
            adapter="ambiguous",
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


@pytest.mark.parametrize("name", ["determinism-boundary.md", "determinism-boundary.json"])
def test_committed_boundary_matches_the_generator(name):
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from generate_schemas import (  # noqa: PLC0415
        build_determinism_boundary_matrix,
        build_determinism_boundary_page,
    )

    builder = (
        build_determinism_boundary_page
        if name.endswith(".md")
        else build_determinism_boundary_matrix
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
        assert f"### {source.label} — `{source.adapter}`" in page
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
    cell = _cell("google_adk", "dynamic_construction")
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
