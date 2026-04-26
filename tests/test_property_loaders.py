from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agents_shipgate.config.schema import ToolSourceConfig
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openapi import load_openapi_tools

TOOL_NAMES = st.from_regex(r"[A-Za-z][A-Za-z0-9._-]{0,24}", fullmatch=True)


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    tools=st.lists(
        st.fixed_dictionaries(
            {
                "name": TOOL_NAMES,
                "description": st.text(min_size=0, max_size=80),
                "annotations": st.dictionaries(
                    st.sampled_from(["readOnlyHint", "destructiveHint", "idempotentHint"]),
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

    loaded = load_mcp_tools(ToolSourceConfig(id="generated", type="mcp", path="tools.json"), tmp_path)

    assert len(loaded.tools) == len(tools)
    assert all(tool.id.startswith("tool:") for tool in loaded.tools)


HTTP_METHODS = st.sampled_from(["get", "post", "put", "patch", "delete"])


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    method=HTTP_METHODS,
    operation_id=TOOL_NAMES,
    property_name=TOOL_NAMES,
    property_type=st.sampled_from(["string", "number", "integer", "boolean"]),
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
