from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_lock import (
    build_capability_lock,
    diff_capability_locks,
    load_capability_lock,
    render_capability_lock_diff_json,
    render_capability_lock_json,
)
from agents_shipgate.core.domain import Agent, AuthInfo, Tool, ToolParameter, ToolRiskHint
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads(
    (REPO_ROOT / "docs/capability-lock-schema.v0.1.json").read_text(encoding="utf-8")
)


def _manifest(**updates: object) -> AgentsShipgateManifest:
    data: dict[str, object] = {
        "version": "0.1",
        "project": {"name": "capability-lock"},
        "agent": {
            "name": "support-agent",
            "declared_purpose": ["Support customer account workflows."],
        },
        "environment": {"target": "local"},
        "tool_sources": [
            {
                "id": "support_api",
                "type": "openapi",
                "path": "tools/support.openapi.yaml",
            }
        ],
    }
    data.update(updates)
    return AgentsShipgateManifest.model_validate(data)


def _tool(
    name: str,
    *,
    scopes: list[str] | None = None,
    hints: list[tuple[str, str]] | None = None,
    input_schema: dict[str, object] | None = None,
    parameters: list[ToolParameter] | None = None,
    source_path: str | None = "tools/support.openapi.yaml",
    source_start_line: int | None = 12,
    auth_type: str | None = "oauth",
    credential_mode: str | None = "delegated",
) -> Tool:
    return Tool(
        id=f"tool:{name}",
        name=name,
        description=f"{name} test tool",
        source_type="openapi",
        source_id="support_api",
        source_ref="tools/support.openapi.yaml",
        source_path=source_path,
        source_start_line=source_start_line,
        source_pointer=f"/paths/{name}",
        annotations={},
        input_schema=input_schema or {},
        auth=AuthInfo(
            type=auth_type,
            credential_mode=credential_mode,
            source="manifest",
            scopes=scopes or [],
        ),
        risk_hints=[
            ToolRiskHint(tag=tag, source="test", confidence=confidence)
            for tag, confidence in (hints or [])
        ],
        parameters=parameters or [],
        extraction_confidence="high",
    )


def _lock(
    tools: list[Tool],
    *,
    manifest: AgentsShipgateManifest | None = None,
    config_path: Path = Path("shipgate.yaml"),
    manifest_dir: Path = Path("."),
    toolkit_bound_count: int = 0,
):
    return build_capability_lock(
        manifest or _manifest(),
        agent=Agent(id="agent:one", name="support-agent"),
        tools=tools,
        config_path=config_path,
        manifest_dir=manifest_dir,
        cli_version="test-version",
        source_count=1,
        source_warning_count=0,
        toolkit_bound_count=toolkit_bound_count,
        plugins_enabled=True,
    )


def test_exported_lock_is_deterministic_and_has_no_timestamp() -> None:
    tool = _tool(
        "stripe.create_refund",
        scopes=["stripe:refunds:write"],
        hints=[("write", "high"), ("financial_action", "high")],
        parameters=[ToolParameter(name="amount", type="number", required=True)],
    )

    first = render_capability_lock_json(_lock([tool]))
    second = render_capability_lock_json(_lock([tool.model_copy(deep=True)]))

    assert first == second
    assert "timestamp" not in first
    assert "generated_at" not in first
    payload = json.loads(first)
    assert payload["capability_lock_schema_version"] == "0.1"
    assert payload["experimental"] is True
    assert payload["summary"]["capability_count"] == 1
    assert payload["hashes"]["semantic_capability_set_hash"]


def test_lock_preserves_capability_fact_hashes() -> None:
    tool = _tool("cases.search", scopes=["cases:read"])
    lock = _lock([tool])
    fact = lock.capabilities[0]

    assert fact.id.startswith("cap_")
    assert fact.hashes.identity_hash in fact.id
    assert lock.hashes.semantic_capability_set_hash
    assert lock.hashes.evidence_set_hash
    assert lock.hashes.source_set_hash


def test_lock_uses_manifest_relative_source_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    lock = _lock(
        [_tool("cases.search", scopes=["cases:read"])],
        config_path=project / "shipgate.yaml",
        manifest_dir=project,
    )

    assert lock.source.config_path == "shipgate.yaml"
    assert lock.source.manifest_dir == "."


def test_lock_discloses_dynamic_toolkit_bounds_are_out_of_enumerable_tools() -> None:
    lock = _lock(
        [_tool("cases.search", scopes=["cases:read"])],
        toolkit_bound_count=2,
    )

    assert lock.source.tool_count == 1
    assert lock.source.toolkit_bound_count == 2


def test_source_location_only_changes_land_in_evidence_changed() -> None:
    base = _lock(
        [
            _tool(
                "cases.search",
                scopes=["cases:read"],
                source_path="tools/one.openapi.yaml",
                source_start_line=10,
            )
        ]
    )
    head = _lock(
        [
            _tool(
                "cases.search",
                scopes=["cases:read"],
                source_path="tools/two.openapi.yaml",
                source_start_line=200,
            )
        ]
    )

    diff = diff_capability_locks(base, head)

    assert diff.summary.changed == 0
    assert diff.summary.evidence_changed == 1
    assert diff.evidence_changed[0].changed_hashes == ("evidence_hash",)


def test_scope_change_reidentifies_capability_instead_of_unrelated_add_remove() -> None:
    base = _lock(
        [_tool("refunds.create", scopes=["payments:write"], hints=[("write", "high")])]
    )
    head = _lock(
        [
            _tool(
                "refunds.create",
                scopes=["payments:write", "payments:admin"],
                hints=[("write", "high")],
            )
        ]
    )

    diff = diff_capability_locks(base, head)

    assert diff.summary.added == 0
    assert diff.summary.removed == 0
    assert diff.summary.reidentified == 1
    row = diff.reidentified[0]
    assert row.before.id != row.after.id
    assert "identity_hash" in row.changed_hashes
    assert "authority_hash" in row.changed_hashes
    assert row.before.authority.scopes == ("payments:write",)
    assert row.after.authority.scopes == ("payments:admin", "payments:write")


def test_non_scope_authority_change_lands_in_changed() -> None:
    base = _lock([_tool("cases.search", scopes=["cases:read"], auth_type="oauth")])
    head = _lock([_tool("cases.search", scopes=["cases:read"], auth_type="api_key")])

    diff = diff_capability_locks(base, head)

    assert diff.summary.reidentified == 0
    assert diff.summary.changed == 1
    assert diff.changed[0].changed_hashes == ("authority_hash",)


def test_semantic_hash_changes_land_in_changed() -> None:
    base = _lock(
        [
            _tool(
                "cases.search",
                scopes=["cases:read"],
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            )
        ]
    )
    head = _lock(
        [
            _tool(
                "cases.search",
                scopes=["cases:read"],
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "observed": {"type": "string"},
                    },
                },
            )
        ]
    )

    diff = diff_capability_locks(base, head)

    assert diff.summary.changed == 1
    assert diff.summary.evidence_changed == 0
    assert "schema_hash" in diff.changed[0].changed_hashes


def test_added_and_removed_facts_sort_stably() -> None:
    base = _lock(
        [
            _tool("alpha.read", scopes=["alpha:read"]),
            _tool("beta.write", scopes=["beta:write"], hints=[("write", "high")]),
        ]
    )
    head = _lock(
        [
            _tool("beta.write", scopes=["beta:write"], hints=[("write", "high")]),
            _tool("gamma.read", scopes=["gamma:read"]),
        ]
    )

    diff = diff_capability_locks(base, head)

    assert [fact.identity.tool_name for fact in diff.added] == ["gamma.read"]
    assert [fact.identity.tool_name for fact in diff.removed] == ["alpha.read"]
    assert diff.summary.unchanged == 1


def test_malformed_lockfile_raises_input_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.lock.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(InputParseError, match="Invalid capability lock file"):
        load_capability_lock(path)


def test_capability_lock_and_diff_validate_against_schema() -> None:
    base = _lock([_tool("alpha.read", scopes=["alpha:read"])])
    head = _lock([_tool("beta.read", scopes=["beta:read"])])
    diff = diff_capability_locks(base, head)

    jsonschema.validate(json.loads(render_capability_lock_json(base)), SCHEMA)
    jsonschema.validate(json.loads(render_capability_lock_diff_json(diff)), SCHEMA)


def test_capability_export_writes_default_lock_and_report_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["capability", "export", "-c", "shipgate.yaml"])

    assert result.exit_code == 0, result.output
    committed = tmp_path / ".agents-shipgate" / "capabilities.lock.json"
    report_copy = tmp_path / "agents-shipgate-reports" / "capabilities.lock.json"
    assert committed.read_text(encoding="utf-8") == report_copy.read_text(
        encoding="utf-8"
    )
    payload = json.loads(committed.read_text(encoding="utf-8"))
    assert payload["summary"]["capability_count"] == 2
    assert "Wrote capability lock" in result.output


def test_capability_diff_cli_exits_zero_when_differences_exist(tmp_path: Path) -> None:
    base = _lock([_tool("alpha.read", scopes=["alpha:read"])])
    head = _lock(
        [
            _tool("alpha.read", scopes=["alpha:read"]),
            _tool("beta.write", scopes=["beta:write"], hints=[("write", "high")]),
        ]
    )
    base_path = tmp_path / "base.lock.json"
    head_path = tmp_path / "head.lock.json"
    base_path.write_text(render_capability_lock_json(base), encoding="utf-8")
    head_path.write_text(render_capability_lock_json(head), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "capability",
            "diff",
            "--base",
            str(base_path),
            "--head",
            str(head_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["added"] == 1
    assert payload["summary"]["changed"] == 0


def _write_project(path: Path) -> None:
    (path / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "cases.search",
                        "description": "Search cases.",
                        "annotations": {"readOnlyHint": True},
                        "auth": {"type": "oauth", "scopes": ["cases:read"]},
                    },
                    {
                        "name": "cases.update",
                        "description": "Update cases.",
                        "annotations": {},
                        "auth": {"type": "oauth", "scopes": ["cases:write"]},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (path / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: capability-lock-cli
agent:
  name: support-agent
  declared_purpose:
    - Support customer workflows.
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
""",
        encoding="utf-8",
    )
