
from agents_shipgate.config.schema import ToolSourceConfig
from agents_shipgate.inputs.openapi import load_openapi_tools


def test_openapi_loader_handles_small_generated_specs(tmp_path):
    for index in range(20):
        method = ["get", "post", "patch", "delete"][index % 4]
        schema_type = ["string", "number", "object", "array"][index % 4]
        spec_path = tmp_path / f"generated_{index}.openapi.yaml"
        spec_path.write_text(
            f"""
openapi: 3.1.0
info:
  title: Generated {index}
  version: "1.0"
paths:
  /items/{index}:
    {method}:
      operationId: generated_{index}
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                value:
                  type: {schema_type}
      responses:
        "200":
          description: ok
""",
            encoding="utf-8",
        )
        loaded = load_openapi_tools(
            ToolSourceConfig(
                id=f"generated_{index}",
                type="openapi",
                path=spec_path.name,
            ),
            tmp_path,
        )

        assert len(loaded.tools) == 1
        assert loaded.tools[0].name == f"generated_{index}"

