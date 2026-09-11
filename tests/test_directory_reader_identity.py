"""Actual adapter discovery selects directories; lexical parents alone do not."""
from __future__ import annotations

import json

import pytest
import yaml
from test_codex_plugin import _write_skill_only_codex_plugin
from test_conductor import _task, _write_workflow
from test_current_control import repo as repo  # noqa: F401
from test_n8n import _write_workflow as write_n8n_workflow

from agents_shipgate.cli.scan import inspect_sources
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    reset_static_input_snapshot,
)


@pytest.mark.parametrize('adapter', ['openai_agents_sdk', 'langchain', 'conductor', 'codex_plugin', 'codex_config', 'n8n'])
def test_readers_bind_the_directories_they_actually_enumerate(repo, adapter):
    source = repo / 'sources'
    source.mkdir()
    manifest_path = repo / 'shipgate.yaml'
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest['tool_sources'] = [{'id': 'source', 'type': adapter, 'path': 'sources'}]
    expected = {'sources'}
    if adapter in {'openai_agents_sdk', 'langchain'}:
        decorator = 'function_tool' if adapter == 'openai_agents_sdk' else 'tool'
        module = 'agents' if adapter == 'openai_agents_sdk' else 'langchain_core.tools'
        (source / 'agent.py').write_text(
            f'from {module} import {decorator}\n@{decorator}\n'
            'def lookup(query: str) -> str:\n    """Read docs."""\n    return query\n'
        )
        # Their directory mode remains top-level, rather than scanning every
        # Python package merely because it is reachable below the directory.
        (source / 'unselected').mkdir()
        (source / 'unselected' / 'another.py').write_text('raise RuntimeError("never execute")')
    elif adapter == 'conductor':
        _write_workflow(source / 'agent.json', [_task('discover', 'LIST_MCP_TOOLS')])
        (source / 'nested').mkdir()
        expected.add('sources/nested')
    elif adapter == 'codex_plugin':
        _write_skill_only_codex_plugin(source)
        expected = {'sources/skills', 'sources/skills/review'}
    elif adapter == 'codex_config':
        (source / '.mcp.json').write_text(json.dumps({'mcpServers': {'docs': {'url': 'https://example.test/mcp'}}}))
        (source / 'nested').mkdir()
        expected.add('sources/nested')
    else:
        manifest['tool_sources'] = []
        manifest['n8n'] = {'workflows': [{'path': 'sources'}]}
        write_n8n_workflow(source / 'agent.json', tool_node_id='lookup', tool_name='Lookup')
        (source / 'nested').mkdir()
        expected.add('sources/nested')
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    snapshot = StaticInputSnapshot(repo)
    token = activate_static_input_snapshot(snapshot)
    try:
        inspect_sources(config_path=manifest_path)
        snapshot.finish()
    finally:
        reset_static_input_snapshot(token)
    rows = snapshot.input_directory_identity(source='worktree')['directories']
    assert {row['path'] for row in rows} == expected
    assert snapshot.input_directory_identity(source='worktree')['unconfirmable_paths'] == []
    assert 'sources/unselected' not in expected
