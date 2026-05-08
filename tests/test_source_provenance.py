"""Tests for v0.11 minimal source provenance on findings.

Covers:
- ``inputs/common.py`` helpers: ``json_pointer_escape/unescape``,
  ``PositionIndex``, ``iter_tool_items``, ``load_structured_file_with_positions``.
- Loaders propagating provenance: OpenAPI, MCP, OpenAI tool artifacts,
  Anthropic tool artifacts.
- ``checks/base.py:tool_finding`` forwards ``Tool.source_*`` into
  ``SourceReference``.
- SARIF emitter prefers structured fields and emits the JSON pointer.
- ``report_json_payload`` strips unset provenance keys (byte-equivalence
  for callers that do not populate provenance).
- ``_run_id`` does not change when only provenance fields change.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.cli.scan import _run_id
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.config.schema import ToolSourceConfig
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.models import (
    Agent,
    Finding,
    SourceReference,
    Tool,
)
from agents_shipgate.inputs.common import (
    iter_tool_items,
    json_pointer_escape,
    json_pointer_unescape,
    load_structured_file_with_positions,
    manifest_relative_path,
)
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.report.sarif import _location

# --- json_pointer_escape / unescape ----------------------------------------


def test_json_pointer_escape_orders_tilde_before_slash():
    """RFC 6901: ``~`` becomes ``~0`` first, then ``/`` becomes ``~1``.
    Reversing the order would produce ``~01`` for an input ``/``."""
    assert json_pointer_escape("a/b") == "a~1b"
    assert json_pointer_escape("a~b") == "a~0b"
    # Both: ~ first, then /. Input "a/~b" → "a~1~0b".
    assert json_pointer_escape("a/~b") == "a~1~0b"


def test_json_pointer_unescape_round_trips():
    cases = ["plain", "with/slash", "with~tilde", "/refunds", "~0~1"]
    for case in cases:
        assert json_pointer_unescape(json_pointer_escape(case)) == case


# --- PositionIndex ----------------------------------------------------------


def test_position_index_lookup_returns_parent_key_position(tmp_path):
    """For ``/paths/~1refunds/post`` the lookup must return the line of
    the ``post:`` key in the ``/refunds`` mapping, not the first child
    line inside the operation."""
    yaml_path = tmp_path / "spec.yaml"
    yaml_path.write_text(
        "paths:\n"
        "  /refunds:\n"
        "    post:\n"
        "      summary: Refund\n",
        encoding="utf-8",
    )

    _, positions = load_structured_file_with_positions(yaml_path)

    pos = positions.lookup("/paths/~1refunds/post")
    assert pos is not None
    line, column = pos
    assert line == 3  # "    post:" is on line 3
    assert column >= 1


def test_position_index_supports_arrays_and_escaped_chars(tmp_path):
    yaml_path = tmp_path / "tools.yaml"
    yaml_path.write_text(
        "tools:\n"
        "  - name: refund\n"
        "  - name: cancel\n"
        "weird/key:\n"
        "  value: 1\n",
        encoding="utf-8",
    )

    _, positions = load_structured_file_with_positions(yaml_path)

    first = positions.lookup("/tools/0")
    second = positions.lookup("/tools/1")
    weird = positions.lookup("/weird~1key")
    assert first is not None
    assert second is not None
    assert weird is not None
    assert first[0] == 2
    assert second[0] == 3
    assert weird[0] == 4


def test_position_index_returns_none_for_unknown_pointer(tmp_path):
    yaml_path = tmp_path / "small.yaml"
    yaml_path.write_text("a: 1\n", encoding="utf-8")
    _, positions = load_structured_file_with_positions(yaml_path)
    assert positions.lookup("/missing") is None


def test_json_input_returns_unsupported_index(tmp_path):
    """JSON inputs intentionally have ``supported=False`` in v0.11.
    Callers can still build pointers but no line numbers are available."""
    json_path = tmp_path / "tools.json"
    json_path.write_text(
        json.dumps({"tools": [{"name": "refund"}, {"name": "cancel"}]}),
        encoding="utf-8",
    )
    data, positions = load_structured_file_with_positions(json_path)
    assert data["tools"][0]["name"] == "refund"
    assert positions.supported is False
    assert positions.lookup("/tools/0") is None


def test_load_structured_file_with_positions_returns_plain_python(tmp_path):
    """The data returned to callers must be plain ``dict`` / ``list``.
    Downstream code compares against literals (e.g. ``data == {"a": 1}``)
    and should not see ruamel ``CommentedMap`` / ``CommentedSeq`` types."""
    yaml_path = tmp_path / "x.yaml"
    yaml_path.write_text("a:\n  b: [1, 2]\n", encoding="utf-8")
    data, _ = load_structured_file_with_positions(yaml_path)
    assert type(data) is dict
    assert type(data["a"]) is dict
    assert type(data["a"]["b"]) is list


# --- iter_tool_items --------------------------------------------------------


def test_iter_tool_items_preserves_index_when_filtering():
    """An item that the loader will later skip must NOT shift the indices
    of surviving items. v0.10's ``_tool_items``+``enumerate`` produced
    drift here — the new helper is the fix."""
    data = {
        "tools": [
            {"name": "first"},
            "not-a-dict",
            {"name": "third"},
        ]
    }
    items = list(iter_tool_items(data))
    assert [(i, p, item["name"]) for i, p, item in items] == [
        (0, "/tools/0", "first"),
        (2, "/tools/2", "third"),
    ]


def test_iter_tool_items_top_level_list_uses_root_pointer():
    items = list(iter_tool_items([{"name": "a"}, {"name": "b"}]))
    assert [p for _, p, _ in items] == ["/0", "/1"]


def test_iter_tool_items_singleton_object_yields_empty_pointer():
    items = list(iter_tool_items({"name": "only"}))
    assert items == [(0, "", {"name": "only"})]


def test_iter_tool_items_other_types_yield_nothing():
    assert list(iter_tool_items(42)) == []
    assert list(iter_tool_items("str")) == []
    assert list(iter_tool_items(None)) == []


# --- tool_finding forwards Tool.source_* into SourceReference ----------------


SAMPLE_MANIFEST = Path("samples/support_refund_agent/shipgate.yaml")


def _scan_context() -> ScanContext:
    manifest = load_manifest(SAMPLE_MANIFEST)
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
    )


def test_tool_finding_forwards_provenance_into_source_reference():
    tool = Tool(
        id="tool:t",
        name="t",
        source_type="openapi",
        source_ref="spec.yaml#/paths/~1pets/get",
        source_path="spec.yaml",
        source_start_line=42,
        source_end_line=58,
        source_start_column=5,
        source_pointer="/paths/~1pets/get",
    )
    finding = tool_finding(
        tool=tool,
        check_id="SHIP-TEST-X",
        title="x",
        severity="high",
        category="test",
        evidence={},
        confidence="high",
        recommendation="rec",
        context=_scan_context(),
    )
    src = finding.source
    assert src is not None
    assert src.path == "spec.yaml"
    assert src.start_line == 42
    assert src.end_line == 58
    assert src.start_column == 5
    assert src.pointer == "/paths/~1pets/get"
    # Legacy fields stay populated from the same Tool fields.
    assert src.type == "openapi"
    assert src.ref == "spec.yaml#/paths/~1pets/get"


# --- SARIF prefers structured fields, falls back gracefully ------------------


def _finding(source: SourceReference | None) -> Finding:
    return Finding(
        check_id="SHIP-TEST",
        title="t",
        severity="high",
        category="test",
        evidence={},
        confidence="high",
        recommendation="r",
        source=source,
    )


def test_sarif_location_prefers_structured_path_and_line():
    finding = _finding(
        SourceReference(
            type="openapi",
            ref="spec.yaml#/paths/~1pets/get",
            location=None,  # legacy left empty; structured wins
            path="spec.yaml",
            start_line=42,
            end_line=58,
            start_column=5,
            pointer="/paths/~1pets/get",
        )
    )
    location = _location(finding)
    assert location is not None
    physical = location["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "spec.yaml"
    region = physical["region"]
    assert region["startLine"] == 42
    assert region["endLine"] == 58
    assert region["startColumn"] == 5
    assert location["properties"] == {"shipgatePointer": "/paths/~1pets/get"}


def test_sarif_location_falls_back_to_legacy_line_when_path_set_but_line_missing():
    """Hybrid case: a plugin (or a v0.11 JSON input) populates structured
    ``source.path`` but the line number lives only on the legacy
    ``source.location = "path:line"`` string. SARIF must still emit the
    region — picking the structured branch and dropping the line would
    silently strip jump-to-line for code-scanning consumers."""
    finding = _finding(
        SourceReference(
            type="plugin",
            ref="custom.py:42",
            location="custom.py:42",
            path="custom.py",  # structured path set
            # start_line intentionally missing
        )
    )
    location = _location(finding)
    assert location is not None
    assert location["physicalLocation"]["artifactLocation"]["uri"] == "custom.py"
    # Line came from the legacy location string via _split_location.
    assert location["physicalLocation"]["region"] == {"startLine": 42}


def test_sarif_location_falls_back_to_legacy_split_location():
    """v0.10 AST loaders populate ``source.location = "path:line"`` and
    do not fill the structured fields. SARIF must still produce a region."""
    finding = _finding(
        SourceReference(
            type="openai_sdk",
            ref="agents.py:42",
            location="agents.py:42",
        )
    )
    location = _location(finding)
    assert location is not None
    assert location["physicalLocation"]["artifactLocation"]["uri"] == "agents.py"
    assert location["physicalLocation"]["region"] == {"startLine": 42}
    assert "properties" not in location  # no pointer when absent


def test_sarif_location_omits_region_when_no_position():
    finding = _finding(SourceReference(type="manifest", ref="shipgate.yaml"))
    location = _location(finding)
    assert location is not None
    # No region key at all when neither structured fields nor legacy line.
    assert "region" not in location["physicalLocation"]


# --- report_json_payload strips unset provenance keys ------------------------


def _minimal_report():
    """Build a minimal ReadinessReport-ish payload by dumping a Finding
    with no provenance and asserting the wire shape — we don't need a
    full ReadinessReport for the strip behavior."""
    from agents_shipgate.core.models import ReadinessReport, ReportSummary, ToolSurfaceSummary

    finding = _finding(
        SourceReference(type="manifest", ref="shipgate.yaml")
    )
    return ReadinessReport(
        run_id="test",
        project={"name": "p"},
        agent={"name": "a"},
        environment={"target": "staging"},
        summary=ReportSummary(status="passed"),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=[finding],
    )


def test_report_json_payload_strips_unset_provenance_keys():
    """v0.10 reports without provenance must remain byte-identical at the
    JSON level — no ``path: null``, ``start_line: null`` clutter."""
    report = _minimal_report()
    data = report_json_payload(report)
    assert data["findings"]
    src = data["findings"][0]["source"]
    for key in ("path", "start_line", "end_line", "start_column", "pointer"):
        assert key not in src, (
            f"provenance key {key!r} should be stripped when None to "
            "preserve v0.10 byte-equivalence"
        )
    # Legacy fields are preserved (type/ref) even when None-able.
    assert src["type"] == "manifest"


def test_report_json_payload_keeps_set_provenance_keys():
    report = _minimal_report()
    report.findings[0].source = SourceReference(
        type="openapi",
        ref="spec.yaml#/paths/~1pets/get",
        path="spec.yaml",
        start_line=42,
        pointer="/paths/~1pets/get",
    )
    data = report_json_payload(report)
    src = data["findings"][0]["source"]
    assert src["path"] == "spec.yaml"
    assert src["start_line"] == 42
    assert src["pointer"] == "/paths/~1pets/get"
    # Unset siblings still stripped.
    assert "end_line" not in src
    assert "start_column" not in src


# --- run_id stability under provenance churn --------------------------------


def _findings_with_source(source: SourceReference | None) -> list[Finding]:
    return [_finding(source)]


def _make_manifest_for_run_id():
    return load_manifest(SAMPLE_MANIFEST)


def test_run_id_unchanged_when_only_provenance_changes():
    """Adding a YAML blank line shouldn't change ``run_id``. Provenance
    is excluded from the hash exactly so reviewers see the same run_id
    when only line numbers shift."""
    manifest = _make_manifest_for_run_id()
    bare = SourceReference(type="openapi", ref="spec.yaml")
    with_provenance = SourceReference(
        type="openapi",
        ref="spec.yaml",
        path="spec.yaml",
        start_line=42,
        end_line=58,
        start_column=5,
        pointer="/paths/~1pets/get",
    )
    rid_bare = _run_id(
        manifest=manifest,
        tools=[],
        findings=_findings_with_source(bare),
    )
    rid_provenance = _run_id(
        manifest=manifest,
        tools=[],
        findings=_findings_with_source(with_provenance),
    )
    assert rid_bare == rid_provenance


# --- Loader-level provenance ------------------------------------------------


SUPPORT_REFUND_BASE = Path("samples/support_refund_agent")


def test_openapi_loader_populates_pointer_and_line():
    """The OpenAPI loader must thread ``/paths/{api_path}/{method}``
    pointers and 1-based YAML line numbers into ``Tool.source_*``."""
    from agents_shipgate.inputs.openapi import load_openapi_tools

    manifest = load_manifest(SUPPORT_REFUND_BASE / "shipgate.yaml")
    source = next(
        s for s in manifest.tool_sources if s.id == "support_openapi"
    )
    loaded = load_openapi_tools(source, SUPPORT_REFUND_BASE)

    refund = next(t for t in loaded.tools if t.name == "stripe.create_refund")
    assert refund.source_path == source.path
    # Pointer encodes the OpenAPI path + HTTP method as
    # /paths/{escaped_path}/{method}; the escape is RFC 6901, not URL.
    assert refund.source_pointer is not None
    assert refund.source_pointer.startswith("/paths/")
    assert refund.source_pointer.endswith("/post")
    # YAML positions are best-effort; assert "populated and sane".
    assert isinstance(refund.source_start_line, int)
    assert refund.source_start_line >= 1
    # The legacy `source_location` stays unset for OpenAPI to keep run_id
    # stable; callers consume the structured fields instead.
    assert refund.source_location is None


def test_mcp_loader_populates_pointer_for_dict_tools_form():
    from agents_shipgate.inputs.mcp import load_mcp_tools

    manifest = load_manifest(SUPPORT_REFUND_BASE / "shipgate.yaml")
    mcp_source = next(
        s for s in manifest.tool_sources if s.id == "support_mcp_tools"
    )
    loaded = load_mcp_tools(mcp_source, SUPPORT_REFUND_BASE)

    for tool in loaded.tools:
        assert tool.source_pointer is not None
        # dict.tools form yields /tools/{i} pointers; the index reflects
        # the original source position even when items are filtered.
        assert tool.source_pointer.startswith("/tools/")
        assert tool.source_path == mcp_source.path


def test_mcp_loader_pointer_for_array_root(tmp_path):
    """A top-level YAML/JSON list yields ``/0``, ``/1``, ... pointers
    rather than ``/tools/0``."""
    from agents_shipgate.inputs.mcp import load_mcp_tools

    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        "- name: a\n"
        "  description: first\n"
        "- name: b\n"
        "  description: second\n",
        encoding="utf-8",
    )
    source = ToolSourceConfig(id="src", type="mcp", path=tools_yaml.name)
    loaded = load_mcp_tools(source, tmp_path)
    pointers = [tool.source_pointer for tool in loaded.tools]
    assert pointers == ["/0", "/1"]


def test_openai_tools_loader_preserves_original_index_after_filter(tmp_path):
    """A non-dict entry must NOT shift the pointer of surviving items.
    v0.10 ``_tool_items``+``enumerate`` produced drift here."""
    from agents_shipgate.config.schema import (
        ArtifactPathConfig,
        OpenAIApiConfig,
    )
    from agents_shipgate.inputs.openai_api import load_openai_api_artifacts

    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        "tools:\n"
        "  - type: function\n"
        "    function:\n"
        "      name: first_tool\n"
        "      parameters:\n"
        "        type: object\n"
        "  - not-a-dict\n"
        "  - type: function\n"
        "    function:\n"
        "      name: third_tool\n"
        "      parameters:\n"
        "        type: object\n",
        encoding="utf-8",
    )
    config = OpenAIApiConfig(
        tools=[ArtifactPathConfig(path=tools_yaml.name)],
    )
    loaded, _ = load_openai_api_artifacts(config, tmp_path)
    assert loaded is not None
    pointers = {tool.name: tool.source_pointer for tool in loaded.tools}
    # The third tool keeps `/tools/2` even though `/tools/1` was skipped.
    assert pointers["first_tool"] == "/tools/0"
    assert pointers["third_tool"] == "/tools/2"


def test_anthropic_tools_loader_populates_pointer_and_line(tmp_path):
    from agents_shipgate.config.schema import (
        AnthropicConfig,
        ArtifactPathConfig,
    )
    from agents_shipgate.inputs.anthropic_api import load_anthropic_artifacts

    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        "- name: get_thing\n"
        "  description: read\n"
        "  input_schema:\n"
        "    type: object\n"
        "- name: write_thing\n"
        "  description: write\n"
        "  input_schema:\n"
        "    type: object\n",
        encoding="utf-8",
    )
    config = AnthropicConfig(tools=[ArtifactPathConfig(path=tools_yaml.name)])
    loaded, _ = load_anthropic_artifacts(config, tmp_path)
    assert loaded is not None
    pointers = {tool.name: tool.source_pointer for tool in loaded.tools}
    assert pointers["get_thing"] == "/0"
    assert pointers["write_thing"] == "/1"
    lines = {tool.name: tool.source_start_line for tool in loaded.tools}
    assert lines["get_thing"] == 1
    assert lines["write_thing"] == 5


def test_findings_carry_provenance_end_to_end(tmp_path):
    """End-to-end: scan the support_refund_agent fixture, write
    ``report.json``, and assert at least one high-severity finding has
    ``source.path``, ``source.start_line``, and ``source.pointer``
    populated. This is the user-visible contract; if it breaks,
    reviewers grep again."""
    from agents_shipgate.cli.scan import run_scan

    run_scan(
        config_path=SUPPORT_REFUND_BASE / "shipgate.yaml",
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    # Filter for findings whose source has the FULL provenance
    # (path + start_line). A pure-JSON source like the MCP wildcard
    # carries path + pointer but no line in v0.11 (documented gap), so
    # we look specifically for the line-carrying YAML cases.
    with_line = [
        f for f in payload["findings"]
        if f["severity"] in {"critical", "high"}
        and isinstance(f.get("source"), dict)
        and f["source"].get("path")
        and isinstance(f["source"].get("start_line"), int)
    ]
    assert with_line, "no high-severity finding has source.start_line populated"
    sample = with_line[0]["source"]
    assert sample.get("pointer") is not None


def test_run_id_changes_when_legacy_location_changes():
    """Sanity: the legacy ``source.location`` IS in the hash (v0.10
    behavior). The structured fields are the only ones excluded."""
    manifest = _make_manifest_for_run_id()
    rid_a = _run_id(
        manifest=manifest,
        tools=[],
        findings=_findings_with_source(
            SourceReference(type="openai_sdk", ref="agents.py", location="agents.py:1")
        ),
    )
    rid_b = _run_id(
        manifest=manifest,
        tools=[],
        findings=_findings_with_source(
            SourceReference(type="openai_sdk", ref="agents.py", location="agents.py:2")
        ),
    )
    assert rid_a != rid_b


# --- manifest-relative source.path -----------------------------------------


def test_manifest_relative_path_normalizes_relative_input(tmp_path):
    """Relative inputs come back as forward-slash POSIX paths."""
    assert manifest_relative_path("specs/foo.yaml", tmp_path) == "specs/foo.yaml"
    assert manifest_relative_path("./specs/foo.yaml", tmp_path) == "specs/foo.yaml"


def test_manifest_relative_path_relativizes_absolute_input(tmp_path):
    """Absolute paths inside the manifest dir come back as
    manifest-relative — SARIF / report consumers expect repo-relative
    URIs and absolute paths break portability across reviewers."""
    inside = tmp_path / "specs" / "foo.yaml"
    inside.parent.mkdir()
    inside.write_text("openapi: 3.0.0\n", encoding="utf-8")
    relative = manifest_relative_path(str(inside), tmp_path)
    assert relative == "specs/foo.yaml"


def test_mcp_loader_emits_relative_path_for_absolute_manifest_entry(tmp_path):
    """ToolSourceConfig accepts absolute paths that resolve inside the
    manifest dir; ``Tool.source_path`` must come back manifest-relative."""
    from agents_shipgate.inputs.mcp import load_mcp_tools

    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        "- name: only\n"
        "  description: x\n",
        encoding="utf-8",
    )
    source = ToolSourceConfig(id="src", type="mcp", path=str(tools_yaml))
    loaded = load_mcp_tools(source, tmp_path)
    assert loaded.tools
    # The path is relative even though the manifest declared it absolute.
    assert loaded.tools[0].source_path == "tools.yaml"


# --- Root pointer (singleton YAML tool object) -----------------------------


def test_position_index_exposes_root_pointer(tmp_path):
    """A YAML doc whose root is a single mapping/sequence must have a
    lookup entry at the empty pointer (RFC 6901 root-document)."""
    yaml_path = tmp_path / "single.yaml"
    yaml_path.write_text(
        "name: only\n"
        "description: x\n",
        encoding="utf-8",
    )
    _, positions = load_structured_file_with_positions(yaml_path)
    assert positions.lookup("") is not None
    line, _ = positions.lookup("")
    assert line == 1


def test_singleton_yaml_tool_keeps_line_provenance(tmp_path):
    """Anthropic / OpenAI singleton-tool YAML files (one object at the
    document root) must still emit ``source_start_line`` even though the
    natural pointer for the singleton is the empty (root) string."""
    from agents_shipgate.config.schema import (
        AnthropicConfig,
        ArtifactPathConfig,
    )
    from agents_shipgate.inputs.anthropic_api import load_anthropic_artifacts

    tools_yaml = tmp_path / "tool.yaml"
    tools_yaml.write_text(
        "name: just_one\n"
        "description: only tool\n"
        "input_schema:\n"
        "  type: object\n",
        encoding="utf-8",
    )
    config = AnthropicConfig(tools=[ArtifactPathConfig(path=tools_yaml.name)])
    loaded, _ = load_anthropic_artifacts(config, tmp_path)
    assert loaded is not None and len(loaded.tools) == 1
    tool = loaded.tools[0]
    assert tool.source_pointer == ""
    assert tool.source_start_line == 1


def test_sarif_emits_empty_pointer_for_root_document_singleton():
    """``""`` is a valid RFC 6901 pointer (root). SARIF must surface it
    under ``properties.shipgatePointer`` — dropping it loses the
    root-pointer signal for singleton-tool sources."""
    finding = _finding(
        SourceReference(
            type="anthropic_api",
            ref="tool.yaml#0",
            path="tool.yaml",
            start_line=1,
            pointer="",
        )
    )
    location = _location(finding)
    assert location is not None
    assert location["properties"] == {"shipgatePointer": ""}


# --- function_schemas now carries provenance --------------------------------


# --- YAML semantics preserved (positional loader is additive only) ---------


def test_positional_loader_preserves_yaml_1_1_booleans(tmp_path):
    """``on``, ``off``, ``yes``, ``no`` are booleans in PyYAML / YAML 1.1
    (which v0.10 callers see). ruamel rt parses YAML 1.2 where they're
    strings — switching to ruamel for the data path would silently break
    every consumer that branches on these values. The positional loader
    must keep the v0.10 booleans."""
    yaml_path = tmp_path / "switches.yaml"
    yaml_path.write_text("flag: on\nother: off\n", encoding="utf-8")
    data, positions = load_structured_file_with_positions(yaml_path)
    assert data == {"flag": True, "other": False}
    # Position index still works even though data went through PyYAML.
    assert positions.lookup("/flag") is not None


def test_positional_loader_accepts_duplicate_keys_like_safe_load(tmp_path):
    """PyYAML silently lets the last duplicate-key value win; ruamel rt
    raises ``DuplicateKeyError``. The positional loader must follow
    ``yaml.safe_load`` semantics so a v0.10 manifest that happened to
    pass yesterday cannot start failing today."""
    yaml_path = tmp_path / "dup.yaml"
    yaml_path.write_text("a: 1\na: 2\n", encoding="utf-8")
    data, positions = load_structured_file_with_positions(yaml_path)
    assert data == {"a": 2}
    # ruamel rejected the file, so positions are best-effort empty —
    # but the scan does NOT fail.
    assert positions.supported is False


# --- Wildcard MCP carries provenance ----------------------------------------


def test_mcp_wildcard_via_wildcard_key_records_pointer_and_line(tmp_path):
    """``wildcard: true`` is a high-severity tool-surface signal. The
    synthetic wildcard Tool must carry ``source_path`` and the line of
    the ``wildcard:`` key so reviewers jump straight to the toggle."""
    from agents_shipgate.inputs.mcp import load_mcp_tools

    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        "wildcard: true\n"
        "tools: []\n",
        encoding="utf-8",
    )
    source = ToolSourceConfig(id="mcp_w", type="mcp", path=tools_yaml.name)
    loaded = load_mcp_tools(source, tmp_path)
    assert len(loaded.tools) == 1
    tool = loaded.tools[0]
    assert tool.annotations["wildcard_tools"] is True
    assert tool.source_path == tools_yaml.name
    assert tool.source_pointer == "/wildcard"
    assert tool.source_start_line == 1


def test_mcp_wildcard_via_tools_star_records_tools_pointer(tmp_path):
    """``tools: '*'`` triggers the same wildcard branch but the pointer
    should land on the ``tools:`` key, not ``/wildcard`` — they're
    distinct lines and reviewers want the right one."""
    from agents_shipgate.inputs.mcp import load_mcp_tools

    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        "name: my-server\n"
        "tools: '*'\n",
        encoding="utf-8",
    )
    source = ToolSourceConfig(id="mcp_w", type="mcp", path=tools_yaml.name)
    loaded = load_mcp_tools(source, tmp_path)
    assert len(loaded.tools) == 1
    tool = loaded.tools[0]
    assert tool.source_pointer == "/tools"
    assert tool.source_start_line == 2


def test_openai_function_schemas_carries_root_pointer_and_line(tmp_path):
    """``openai_api.function_schemas`` files are a single function
    definition; the loader must emit ``source_path``, the root pointer,
    and a YAML line just like ``openai_api.tools``."""
    from agents_shipgate.config.schema import (
        NamedArtifactPathConfig,
        OpenAIApiConfig,
    )
    from agents_shipgate.inputs.openai_api import load_openai_api_artifacts

    schema_yaml = tmp_path / "fn.yaml"
    schema_yaml.write_text(
        "name: lookup\n"
        "parameters:\n"
        "  type: object\n",
        encoding="utf-8",
    )
    config = OpenAIApiConfig(
        function_schemas=[
            NamedArtifactPathConfig(path=schema_yaml.name, name="lookup"),
        ],
    )
    loaded, _ = load_openai_api_artifacts(config, tmp_path)
    assert loaded is not None and len(loaded.tools) == 1
    tool = loaded.tools[0]
    assert tool.source_path == schema_yaml.name
    assert tool.source_pointer == ""
    assert tool.source_start_line == 1
