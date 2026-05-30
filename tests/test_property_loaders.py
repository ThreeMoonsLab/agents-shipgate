"""Property-based tests for the static loaders.

Covers the three adapter shapes the round-3 architecture review
flagged as the most likely brittleness surfaces:

- ``inputs/mcp.py`` — JSON tool arrays from MCP exports.
- ``inputs/openapi.py`` — OpenAPI 3.x specifications.
- ``inputs/openai_sdk_static.py`` — Python AST extraction for
  ``@function_tool`` decorators.

Strategy: generate well-formed but adversarial inputs (random tool
names, mixed-case HTTP verbs, unicode-heavy descriptions, empty
schemas, optional fields toggled) and assert the loader either
extracts the expected normalized Tool objects or fails with a
typed ``InputParseError`` — never a stray ``KeyError`` /
``AttributeError`` / ``UnicodeError`` from the loader internals.

Hypothesis settings are tuned conservatively (``max_examples`` in
the 20–40 range, ``deadline=None``) so the suite stays fast on CI
while still exercising a meaningful fraction of the input space on
every run. Adding cases here is cheap and high-leverage; the
deterministic golden fixtures under ``samples/`` cover the happy
paths.
"""

from __future__ import annotations

import json
import keyword
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openai_sdk_static import load_openai_sdk_static_tools
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Tool names: alphanumeric + `._-`, leading alpha, capped length. Mirrors
# the safe-character set the adapters accept without emitting
# ``tool_name_warning``.
TOOL_NAMES = st.from_regex(r"[A-Za-z][A-Za-z0-9._-]{0,24}", fullmatch=True)

# Python identifiers (no dots; constrained for AST loader tests).
PY_IDENTIFIERS = st.from_regex(
    r"[A-Za-z_][A-Za-z0-9_]{0,20}",
    fullmatch=True,
).filter(lambda value: not keyword.iskeyword(value))

# Free-form text the loaders treat opaquely. Mix of ASCII + low Unicode
# to catch any naive bytes-vs-str assumption inside the loaders.
FREE_TEXT = st.text(min_size=0, max_size=120)

JSON_SCHEMA_PRIMITIVES = st.sampled_from(["string", "number", "integer", "boolean"])


# ---------------------------------------------------------------------------
# MCP loader
# ---------------------------------------------------------------------------


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    tools=st.lists(
        st.fixed_dictionaries(
            {
                "name": TOOL_NAMES,
                "description": FREE_TEXT,
                "annotations": st.dictionaries(
                    st.sampled_from(
                        ["readOnlyHint", "destructiveHint", "idempotentHint"]
                    ),
                    st.booleans(),
                    max_size=3,
                ),
            }
        ),
        max_size=8,
    )
)
def test_mcp_loader_accepts_generated_tool_arrays(tmp_path, tools):
    path = tmp_path / "tools.json"
    path.write_text(json.dumps({"tools": tools}), encoding="utf-8")

    loaded = load_mcp_tools(
        ToolSourceConfig(id="generated", type="mcp", path="tools.json"),
        tmp_path,
    )

    # The loader skips entries missing ``name``, so the result count is
    # the count of input entries with a non-empty name. Our strategy
    # always supplies a non-empty name, so the count should match.
    assert len(loaded.tools) == len(tools)
    assert all(tool.id.startswith("tool:") for tool in loaded.tools)
    # Stable tool IDs are deterministic — re-loading the same payload
    # should yield byte-identical IDs.
    second = load_mcp_tools(
        ToolSourceConfig(id="generated", type="mcp", path="tools.json"),
        tmp_path,
    )
    assert [tool.id for tool in second.tools] == [tool.id for tool in loaded.tools]


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    auth_type=st.sampled_from(["oauth2", "api_key", "bearer", "none"]),
    scopes=st.lists(
        st.from_regex(r"[a-z][a-z0-9_:.-]{0,18}", fullmatch=True),
        max_size=4,
        unique=True,
    ),
)
def test_mcp_loader_preserves_auth_scopes(tmp_path, auth_type, scopes):
    """Auth-scope round-trip — every scope a user declares survives
    extraction into ``Tool.auth.scopes`` in declaration order."""

    payload = {
        "tools": [
            {
                "name": "create_thing",
                "description": "Create a thing",
                "auth": {"type": auth_type, "scopes": scopes},
            }
        ]
    }
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_mcp_tools(
        ToolSourceConfig(id="auth", type="mcp", path="tools.json"),
        tmp_path,
    )

    assert len(loaded.tools) == 1
    assert loaded.tools[0].auth.type == auth_type
    assert loaded.tools[0].auth.scopes == scopes
    assert loaded.tools[0].auth.source == "mcp"


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    payload=st.one_of(
        st.integers(),
        st.text(min_size=1, max_size=10),
        st.lists(st.integers(), max_size=3),
        st.booleans(),
    )
)
def test_mcp_loader_rejects_non_object_or_array_payload(tmp_path, payload):
    """The loader must raise :class:`InputParseError` for any payload
    that isn't an object or an array of tools."""

    path = tmp_path / "tools.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        loaded = load_mcp_tools(
            ToolSourceConfig(id="bad", type="mcp", path="tools.json"),
            tmp_path,
        )
    except InputParseError:
        return
    # A top-level array is treated as a tools list. If we got here with a
    # list payload, the loader accepted it — that's the documented shape.
    if isinstance(payload, list):
        # The list must have contained at least one non-dict item; the
        # loader either skipped them with warnings (returning 0 tools) or
        # raised. Either is acceptable.
        assert loaded.warnings or loaded.tools == []
        return
    # Any other primitive payload must have raised.
    raise AssertionError(f"loader accepted invalid payload type: {type(payload).__name__}")


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    duplicate_count=st.integers(min_value=2, max_value=5),
)
def test_mcp_loader_warns_on_duplicate_names(tmp_path, duplicate_count):
    """Duplicate tool names within the same source emit a warning, not
    a hard error — adapter consumers rely on the warning to surface
    the inconsistency in ``report.source_warnings``."""

    tools = [
        {"name": "duplicate", "description": f"variant {i}"}
        for i in range(duplicate_count)
    ]
    path = tmp_path / "tools.json"
    path.write_text(json.dumps({"tools": tools}), encoding="utf-8")

    loaded = load_mcp_tools(
        ToolSourceConfig(id="dups", type="mcp", path="tools.json"),
        tmp_path,
    )

    # Loader keeps every entry but emits one warning per duplicate after
    # the first.
    assert len(loaded.tools) == duplicate_count
    duplicate_warnings = [w for w in loaded.warnings if "Duplicate" in w]
    assert len(duplicate_warnings) == duplicate_count - 1


# ---------------------------------------------------------------------------
# OpenAPI loader
# ---------------------------------------------------------------------------


HTTP_METHODS = st.sampled_from(["get", "post", "put", "patch", "delete"])


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    method=HTTP_METHODS,
    operation_id=TOOL_NAMES,
    property_name=TOOL_NAMES,
    property_type=JSON_SCHEMA_PRIMITIVES,
)
def test_openapi_loader_accepts_generated_simple_operations(
    tmp_path, method, operation_id, property_name, property_type
):
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Generated", "version": "1.0"},
        "paths": {
            "/generated": {
                method: {
                    "operationId": operation_id,
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        property_name: {"type": property_type}
                                    },
                                    "required": [property_name],
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    path = tmp_path / "generated.openapi.json"
    path.write_text(json.dumps(spec), encoding="utf-8")

    loaded = load_openapi_tools(
        ToolSourceConfig(id="generated", type="openapi", path="generated.openapi.json"),
        tmp_path,
    )

    assert [tool.name for tool in loaded.tools] == [operation_id]
    assert loaded.tools[0].input_schema.get("type") == "object"


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    methods=st.lists(HTTP_METHODS, min_size=2, max_size=5, unique=True),
)
def test_openapi_loader_emits_one_tool_per_method(tmp_path, methods):
    """A single path with multiple HTTP methods produces one Tool per
    method. Operation ordering is preserved in declared method order
    (dict iteration order of the YAML)."""

    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Multi", "version": "1.0"},
        "paths": {
            "/multi": {
                method: {
                    "operationId": f"op_{method}",
                    "responses": {"200": {"description": "ok"}},
                }
                for method in methods
            }
        },
    }
    path = tmp_path / "multi.openapi.json"
    path.write_text(json.dumps(spec), encoding="utf-8")

    loaded = load_openapi_tools(
        ToolSourceConfig(id="multi", type="openapi", path="multi.openapi.json"),
        tmp_path,
    )

    assert sorted(t.name for t in loaded.tools) == sorted(f"op_{m}" for m in methods)


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    bad_payload=st.one_of(
        # A non-object document.
        st.lists(st.integers(), max_size=3),
        # Missing the ``openapi`` version key.
        st.fixed_dictionaries({"info": st.fixed_dictionaries({"title": st.just("x")})}),
        # ``paths`` not a dict.
        st.fixed_dictionaries(
            {
                "openapi": st.just("3.1.0"),
                "info": st.fixed_dictionaries({"title": st.just("x")}),
                "paths": st.lists(st.integers(), max_size=2),
            }
        ),
    )
)
def test_openapi_loader_rejects_malformed_documents(tmp_path, bad_payload):
    """Every malformed top-level shape raises :class:`InputParseError`
    rather than crashing with an internal error."""

    path = tmp_path / "bad.openapi.json"
    path.write_text(json.dumps(bad_payload), encoding="utf-8")

    try:
        load_openapi_tools(
            ToolSourceConfig(id="bad", type="openapi", path="bad.openapi.json"),
            tmp_path,
        )
    except InputParseError:
        return
    raise AssertionError(
        f"OpenAPI loader did not raise on malformed payload: {bad_payload!r}"
    )


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    operation_id=TOOL_NAMES,
    description=FREE_TEXT,
)
def test_openapi_loader_preserves_description(
    tmp_path, operation_id, description
):
    """Operation ``description`` survives extraction onto
    ``Tool.description``.

    OpenAPI ``tags`` are not covered here — the current
    ``_extract_annotations`` only copies ``x-*`` extension metadata
    onto ``Tool.annotations``, not the standard ``tags`` array. If a
    future loader change starts surfacing tags, add an assertion for
    it then; do not imply coverage from the test name.
    """

    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Annotated", "version": "1.0"},
        "paths": {
            "/annotated": {
                "get": {
                    "operationId": operation_id,
                    "description": description,
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    path = tmp_path / "annotated.openapi.json"
    path.write_text(json.dumps(spec), encoding="utf-8")

    loaded = load_openapi_tools(
        ToolSourceConfig(id="anno", type="openapi", path="annotated.openapi.json"),
        tmp_path,
    )

    assert len(loaded.tools) == 1
    tool = loaded.tools[0]
    assert tool.name == operation_id
    # The loader concatenates summary + description; an empty source
    # description produces an empty/None tool description.
    if description.strip():
        assert tool.description is not None
        assert description in (tool.description or "")


# ---------------------------------------------------------------------------
# OpenAI Agents SDK static AST loader
# ---------------------------------------------------------------------------


def _empty_manifest() -> AgentsShipgateManifest:
    """Smallest valid manifest the SDK loader will accept.

    The SDK loader reads ``manifest.agent.sdk.entrypoint`` only as a
    fallback when ``source.path`` is unset. The property tests below
    always set ``source.path``, so the manifest doesn't drive routing
    — it just has to validate.
    """

    # The manifest needs at least one declared source to satisfy
    # cross-field validation. We declare an ``openai_agents_sdk``
    # source whose ``path`` is overridden per-test — the SDK loader
    # reads the per-call ``source.path``, not the manifest entry.
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "prop-test"},
            "agent": {"name": "prop", "declared_purpose": ["property-test fixture"]},
            "environment": {"target": "local"},
            "tool_sources": [
                {
                    "id": "sdk",
                    "type": "openai_agents_sdk",
                    "path": "placeholder.py",
                }
            ],
        }
    )


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    function_name=PY_IDENTIFIERS,
    param_name=PY_IDENTIFIERS,
    param_annotation=st.sampled_from(["str", "int", "float", "bool", "list[str]"]),
    has_default=st.booleans(),
)
def test_openai_sdk_loader_extracts_function_tool(
    tmp_path, function_name, param_name, param_annotation, has_default
):
    """Every ``@function_tool``-decorated function in the entrypoint
    produces exactly one Tool with the expected name, parameter
    presence, and required-flag derived from the default value.
    """

    if param_name in {"self", "ctx", "context"}:
        # These names are dropped by the extractor; the test would be
        # vacuous. Hypothesis will move on.
        return

    default_clause = " = None" if has_default else ""
    source = (
        "from agents import function_tool\n"
        "\n"
        "@function_tool\n"
        f"def {function_name}({param_name}: {param_annotation}{default_clause}) -> str:\n"
        f'    """Docstring for {function_name}."""\n'
        "    return ''\n"
    )
    entrypoint = tmp_path / "agent.py"
    entrypoint.write_text(source, encoding="utf-8")

    loaded = _load_sdk(tmp_path, entrypoint.name)

    assert len(loaded.tools) == 1
    tool = loaded.tools[0]
    assert tool.name == function_name
    assert tool.description is not None
    assert function_name in tool.description
    assert len(tool.parameters) == 1
    assert tool.parameters[0].name == param_name
    assert tool.parameters[0].required is (not has_default)


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    tool_names=st.lists(PY_IDENTIFIERS, min_size=1, max_size=5, unique=True),
)
def test_openai_sdk_loader_handles_multiple_decorated_functions(
    tmp_path, tool_names
):
    """The walk-order of ``ast.walk`` is deterministic; the loader
    should emit one Tool per decorated function and the set of
    extracted names should equal the set of declared names."""

    lines = ["from agents import function_tool", ""]
    for name in tool_names:
        lines.extend(
            [
                "@function_tool",
                f"def {name}() -> str:",
                "    return ''",
                "",
            ]
        )
    entrypoint = tmp_path / "agent.py"
    entrypoint.write_text("\n".join(lines), encoding="utf-8")

    loaded = _load_sdk(tmp_path, entrypoint.name)

    assert {tool.name for tool in loaded.tools} == set(tool_names)


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    real_name=PY_IDENTIFIERS,
    override_name=TOOL_NAMES,
    # Plain printable ASCII keeps the generated Python source free of
    # control characters (CR/LF/tabs) that would otherwise corrupt the
    # string literal we inject. The decorator-override behaviour does
    # not vary by character set; the goal is to exercise the kwarg
    # extraction path.
    override_description=st.from_regex(r"[A-Za-z0-9 .,'_-]{1,80}", fullmatch=True),
)
def test_openai_sdk_loader_respects_decorator_overrides(
    tmp_path, real_name, override_name, override_description
):
    """``@function_tool(name_override=..., description_override=...)``
    wins over the Python name and the docstring."""

    safe_description = override_description.replace('"', "'")
    source = (
        "from agents import function_tool\n"
        "\n"
        f'@function_tool(name_override="{override_name}", '
        f'description_override="{safe_description}")\n'
        f"def {real_name}() -> str:\n"
        '    """Original docstring — should be overridden."""\n'
        "    return ''\n"
    )
    entrypoint = tmp_path / "agent.py"
    entrypoint.write_text(source, encoding="utf-8")

    loaded = _load_sdk(tmp_path, entrypoint.name)

    assert len(loaded.tools) == 1
    assert loaded.tools[0].name == override_name
    assert loaded.tools[0].description == safe_description


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    decorator_import_alias=st.sampled_from(
        [
            "from agents import function_tool",
            "from openai_agents import function_tool",
            "from agents import function_tool as ft",
        ]
    )
)
def test_openai_sdk_loader_recognises_all_canonical_decorator_imports(
    tmp_path, decorator_import_alias
):
    """The loader recognises ``function_tool`` from either of the two
    canonical packages, including aliased imports."""

    decorator = (
        "ft" if "as ft" in decorator_import_alias else "function_tool"
    )
    source = (
        f"{decorator_import_alias}\n"
        "\n"
        f"@{decorator}\n"
        "def do_thing() -> str:\n"
        "    return ''\n"
    )
    entrypoint = tmp_path / "agent.py"
    entrypoint.write_text(source, encoding="utf-8")

    loaded = _load_sdk(tmp_path, entrypoint.name)

    assert len(loaded.tools) == 1
    assert loaded.tools[0].name == "do_thing"


def test_openai_sdk_loader_returns_empty_on_missing_entrypoint(tmp_path: Path):
    """Missing entrypoint files do not raise — the loader returns
    an empty :class:`LoadedToolSource` with a warning so the rest of
    the scan can continue and surface the issue through
    ``source_warnings``."""

    loaded = _load_sdk(tmp_path, "does-not-exist.py")

    assert loaded.tools == []
    assert any("not found" in w for w in loaded.warnings)


def test_openai_sdk_loader_raises_on_syntax_error(tmp_path: Path):
    """A Python file the loader cannot parse must raise
    :class:`InputParseError` — that is the contract every framework
    adapter shares so the dispatcher routes the failure consistently."""

    entrypoint = tmp_path / "broken.py"
    entrypoint.write_text("from agents import function_tool\ndef\n", encoding="utf-8")

    try:
        _load_sdk(tmp_path, entrypoint.name)
    except InputParseError:
        return
    raise AssertionError("SDK loader did not raise on syntax error")


def _load_sdk(base_dir: Path, entrypoint_name: str):
    return load_openai_sdk_static_tools(
        ToolSourceConfig(
            id="sdk",
            type="openai_agents_sdk",
            path=entrypoint_name,
        ),
        _empty_manifest(),
        base_dir,
    )
