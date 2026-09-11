from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agents_shipgate.cli.discovery.signals import detect_workspace
from agents_shipgate.cli.discovery.template import render_auto_manifest
from agents_shipgate.cli.scan import inspect_sources, run_scan
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.conductor import load_conductor_artifacts


def test_conductor_static_mcp_call_and_human_checkpoint(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_workflow(
        project / "workflow.json",
        [
            _task(
                "review",
                "HUMAN",
                inputParameters={"approvalPayload": "human-secret-payload"},
            ),
            _task(
                "chat",
                "LLM_CHAT_COMPLETE",
                inputParameters={
                    "prompt": "private-system-prompt",
                    "messages": [{"content": "private-user-message"}],
                },
            ),
            _task(
                "lookup",
                "CALL_MCP_TOOL",
                description="Look up an order.",
                inputParameters={
                    "mcpServer": "https://mcp.example.test/rpc?token=secret",
                    "method": "lookup_order",
                    "headers": {"Authorization": "Bearer never-report-me"},
                    "arguments": {"order_id": "${workflow.input.order_id}"},
                },
            ),
        ],
    )
    _write_manifest(project, "workflow.json")

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json", "markdown", "sarif"],
        ci_mode="advisory",
    )

    assert report.report_schema_version == "0.43"
    surface = report.frameworks["conductor"]
    assert surface["workflow_count"] == 1
    assert surface["mcp_call_task_count"] == 1
    assert surface["human_checkpoint_count"] == 1
    assert surface["structurally_checkpointed_mcp_call_count"] == 1
    assert surface["dynamic_tool_surface_count"] == 0
    tool = next(item for item in report.tool_inventory if item["name"] == "lookup_order")
    assert tool["source_type"] == "conductor_mcp_call"
    assert tool["tool_id"] in report.binding_surface_facts.reachable_tool_ids
    manifest = load_manifest(project / "shipgate.yaml")
    _, artifacts = load_conductor_artifacts(manifest, project)
    assert artifacts is not None
    call = artifacts.mcp_call_tasks[0]
    assert call["preceding_checkpoint_refs"] == ["review"]
    assert call["checkpoint_relation"] == "same_sequence"
    assert call["semantic_approval"] == "unknown"
    rendered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "reports").iterdir())
        if path.is_file()
    )
    assert "never-report-me" not in rendered
    assert "token=secret" not in rendered
    assert "workflow.input.order_id" not in rendered
    assert "human-secret-payload" not in rendered
    assert "private-system-prompt" not in rendered
    assert "private-user-message" not in rendered


def test_conductor_dynamic_surfaces_have_precise_sources(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_workflow(
        project / "workflow.json",
        [
            _task(
                "plan",
                "LLM_CHAT_COMPLETE",
                inputParameters={"tools": "${discover.output.tools}"},
            ),
            _task(
                "execute",
                "CALL_MCP_TOOL",
                inputParameters={
                    "mcpServer": "${workflow.input.server}",
                    "method": "${plan.output.result.method}",
                    "arguments": "${plan.output.result.arguments}",
                },
            ),
        ],
    )
    _write_manifest(project, "workflow.json")

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    findings = [
        item
        for item in report.findings
        if item.check_id == "SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
    ]
    assert len(findings) == 2
    assert {item.source.pointer for item in findings if item.source} == {
        "/tasks/0",
        "/tasks/1",
    }
    assert not any(item["source_type"] == "conductor_mcp_call" for item in report.tool_inventory)
    assert not report.binding_surface_facts.pass_eligible
    assert report.release_decision.decision != "passed"


def test_conductor_human_checkpoint_does_not_cross_sibling_branch(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    switch = _task(
        "route",
        "SWITCH",
        decisionCases={
            "review": [_task("review", "HUMAN")],
            "execute": [
                _task(
                    "call",
                    "CALL_MCP_TOOL",
                    inputParameters={
                        "mcpServer": "https://mcp.example.test/rpc",
                        "method": "lookup_order",
                        "arguments": {},
                    },
                )
            ],
        },
        defaultCase=[],
    )
    _write_workflow(project / "workflow.json", [switch])
    _write_manifest(project, "workflow.json")

    payload = inspect_sources(config_path=project / "shipgate.yaml")
    assert payload["frameworks"]["conductor"]["structurally_checkpointed_mcp_call_count"] == 0


def test_conductor_flat_arguments_compatibility_and_unsupported_capability(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_workflow(
        project / "workflow.json",
        [
            _task(
                "call",
                "CALL_MCP_TOOL",
                inputParameters={
                    "mcpServer": "https://mcp.example.test/rpc",
                    "method": "weather",
                    "location": "never-treat-as-argument",
                },
            ),
            _task("http", "HTTP", inputParameters={"uri": "https://example.test"}),
        ],
    )
    _write_manifest(project, "workflow.json")

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    surface = report.frameworks["conductor"]
    assert surface["unsupported_capability_count"] == 1
    assert any("unsupported flat input fields" in warning for warning in report.source_warnings)
    assert any("MCP-core v1" in warning for warning in report.source_warnings)
    assert report.release_decision.decision != "passed"


def test_conductor_detect_and_init_positive_and_negative_controls(tmp_path):
    positive = tmp_path / "positive"
    positive.mkdir()
    workflows = positive / "workflows"
    workflows.mkdir()
    _write_workflow(
        workflows / "agent.json",
        [_task("discover", "LIST_MCP_TOOLS", inputParameters={"mcpServer": "x"})],
    )
    detected = detect_workspace(positive)
    assert any(item.type == "conductor" for item in detected.frameworks)
    assert detected.suggested_sources == [{"type": "conductor", "path": "workflows/agent.json"}]
    rendered = render_auto_manifest(positive, detected).text
    assert "type: conductor" in rendered
    assert "path: workflows/agent.json" in rendered
    load_manifest(_write_text(positive / "shipgate.yaml", rendered))

    negative = tmp_path / "negative"
    negative.mkdir()
    _write_workflow(negative / "workflow.json", [_task("http", "HTTP")])
    not_detected = detect_workspace(negative)
    assert not any(item.type == "conductor" for item in not_detected.frameworks)
    assert not any(item["type"] == "conductor" for item in not_detected.suggested_sources)


def test_conductor_rejects_yaml_and_unsupported_schema(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _write_text(project / "workflow.yaml", "name: bad\ntasks: []\n")
    _write_manifest(project, "workflow.yaml")
    with pytest.raises(InputParseError, match=".json"):
        run_scan(
            config_path=project / "shipgate.yaml",
            output_dir=tmp_path / "bad-yaml",
            ci_mode="advisory",
        )

    _write_workflow(project / "workflow.json", [_task("call", "CALL_MCP_TOOL")], schema=3)
    _write_manifest(project, "workflow.json")
    with pytest.raises(InputParseError, match="schemaVersion"):
        run_scan(
            config_path=project / "shipgate.yaml",
            output_dir=tmp_path / "bad-schema",
            ci_mode="advisory",
        )


def test_conductor_multiple_sources_optional_failure_and_bulk_array(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    first = {
        "name": "first",
        "version": 1,
        "schemaVersion": 2,
        "tasks": [
            _task(
                "first_call",
                "CALL_MCP_TOOL",
                inputParameters={
                    "mcpServer": "https://mcp.example.test/rpc",
                    "method": "first_tool",
                    "arguments": {},
                },
            )
        ],
    }
    second = {
        "name": "second",
        "version": 1,
        "schemaVersion": 2,
        "tasks": [_task("wait", "WAIT")],
    }
    _write_text(project / "bulk.json", json.dumps([first, second]))
    _write_text(
        project / "shipgate.yaml",
        """version: "0.1"
project:
  name: conductor-multi
agent:
  name: conductor-agent
  declared_purpose:
    - Test aggregation.
environment:
  target: local
tool_sources:
  - id: missing_optional
    type: conductor
    path: missing.json
    optional: true
  - id: bulk
    type: conductor
    path: bulk.json
""",
    )

    payload = inspect_sources(config_path=project / "shipgate.yaml")
    surface = payload["frameworks"]["conductor"]
    assert surface["workflow_count"] == 2
    assert surface["task_count"] == 2
    assert payload["total_tools"] == 1
    assert any("Optional Conductor source" in item for item in payload["warnings"])


def test_report_schema_v032_pins_conductor_summary_fields():
    schema = json.loads(Path("docs/report-schema.v0.32.json").read_text(encoding="utf-8"))
    conductor = schema["properties"]["frameworks"]["properties"]["conductor"]
    assert set(conductor["required"]) == {
        "workflow_file_count",
        "workflow_count",
        "task_count",
        "llm_task_count",
        "mcp_discovery_task_count",
        "mcp_call_task_count",
        "human_checkpoint_count",
        "structurally_checkpointed_mcp_call_count",
        "sub_workflow_task_count",
        "dynamic_tool_surface_count",
        "unsupported_capability_count",
        "warnings",
    }


def test_conductor_directory_skips_documents_and_rejects_escaping_symlink(tmp_path):
    project = tmp_path / "project"
    workflows = project / "workflows"
    workflows.mkdir(parents=True)
    _write_text(workflows / "metadata.json", json.dumps({"kind": "metadata"}))
    _write_workflow(
        workflows / "agent.json",
        [_task("discover", "LIST_MCP_TOOLS")],
    )
    _write_manifest(project, "workflows")
    payload = inspect_sources(config_path=project / "shipgate.yaml")
    assert payload["frameworks"]["conductor"]["workflow_file_count"] == 1

    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, workflows / "escape")
    with pytest.raises(InputParseError, match="symlink resolves outside"):
        inspect_sources(config_path=project / "shipgate.yaml")


def test_conductor_local_sub_workflow_cycle_is_dynamic(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    first = {
        "name": "first",
        "version": 1,
        "schemaVersion": 2,
        "tasks": [
            _task(
                "call_second",
                "SUB_WORKFLOW",
                subWorkflowParam={"name": "second", "version": 1},
            )
        ],
    }
    second = {
        "name": "second",
        "version": 1,
        "schemaVersion": 2,
        "tasks": [
            _task(
                "call_first",
                "SUB_WORKFLOW",
                subWorkflowParam={"name": "first", "version": 1},
            )
        ],
    }
    _write_text(project / "workflows.json", json.dumps([first, second]))
    _write_manifest(project, "workflows.json")

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    findings = [
        item
        for item in report.findings
        if item.check_id == "SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE"
    ]
    assert len(findings) == 2
    assert all(item.evidence["surface"]["kind"] == "sub_workflow_cycle" for item in findings)


def _task(ref: str, task_type: str, **extra):
    return {
        "name": ref,
        "taskReferenceName": ref,
        "type": task_type,
        "inputParameters": {},
        **extra,
    }


def _write_workflow(path, tasks, *, schema=2):
    path.write_text(
        json.dumps(
            {
                "name": "conductor_agent",
                "version": 1,
                "schemaVersion": schema,
                "tasks": tasks,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_manifest(project, workflow_path):
    _write_text(
        project / "shipgate.yaml",
        f"""version: "0.1"
project:
  name: conductor-test
agent:
  name: conductor-agent
  declared_purpose:
    - Test Conductor static extraction.
environment:
  target: local
tool_sources:
  - id: conductor
    type: conductor
    path: {workflow_path}
""",
    )


def _write_text(path, text):
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("duplicate_target", [False, True])
def test_selected_conductor_json_alias_refuses_under_input_capture(tmp_path, duplicate_target):
    from agents_shipgate.core.static_inputs import (
        StaticInputSnapshot,
        activate_static_input_snapshot,
        reset_static_input_snapshot,
    )

    workflows = tmp_path / 'workflows'
    workflows.mkdir()
    target = 'a.json' if duplicate_target else 'original.txt'
    _write_workflow(workflows / target, [_task('discover', 'LIST_MCP_TOOLS')])
    (workflows / 'z.json').symlink_to(target)
    _write_manifest(tmp_path, 'workflows')
    # Existing standalone discovery accepts in-root aliases. Verification must
    # retain the lexical selected entry so changing its target cannot disappear
    # behind unchanged resolved file bytes and unchanged member kinds.
    assert inspect_sources(config_path=tmp_path / 'shipgate.yaml')['frameworks']['conductor']['workflow_file_count'] == 1
    snapshot = StaticInputSnapshot(tmp_path)
    token = activate_static_input_snapshot(snapshot)
    try:
        with pytest.raises(InputParseError, match='symlink'):
            inspect_sources(config_path=tmp_path / 'shipgate.yaml')
    finally:
        reset_static_input_snapshot(token)


def test_captured_conductor_directory_scans_grow_linearly(tmp_path, monkeypatch):
    from agents_shipgate.core import trust_roots
    from agents_shipgate.core.static_inputs import (
        StaticInputSnapshot,
        activate_static_input_snapshot,
        reset_static_input_snapshot,
    )
    from agents_shipgate.inputs.conductor import _load_source
    from agents_shipgate.schemas.manifest import ToolSourceConfig

    real_scandir = os.scandir
    entries = []

    class CountedScan:
        def __init__(self, path):
            self.scan = real_scandir(path)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.scan.close()
        def __iter__(self):
            return self
        def __next__(self):
            entry = next(self.scan)
            entries.append(1)
            return entry

    counts = []
    for size in (40, 80):
        root = tmp_path / str(size)
        workflows = root / 'workflows'
        workflows.mkdir(parents=True)
        for index in range(size):
            _write_workflow(workflows / f'{index:03}.json', [_task('discover', 'LIST_MCP_TOOLS')])
        snapshot = StaticInputSnapshot(root)
        token = activate_static_input_snapshot(snapshot)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(trust_roots.os, 'scandir', CountedScan)
                entries.clear()
                records, paths = _load_source(
                    ToolSourceConfig(id='conductor', type='conductor', path='workflows'), root,
                )
                snapshot.finish()
                counts.append(len(entries))
            assert len(records) == len(paths) == size
        finally:
            reset_static_input_snapshot(token)
    # Counts actual entries visited through scandir, including unbudgeted
    # lexical checks: per-file rescans could evade the snapshot's own budget.
    assert counts[1] <= 2 * counts[0] + 10, counts
